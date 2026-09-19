"""
Order-flow features for Garvit's 5-concept system, computed POINT-IN-TIME on 1-minute OHLCV.
Every value at bar t uses only bars <= t (and the PRIOR day's complete session), so the backtest
cannot see the future. A test rebuilds features on truncated data and demands identical rows.

READ THIS FIRST - proxies, not order flow.
The data (Upstox 1-minute candles) has no aggressor side, bid/ask or per-price buy/sell volume, so:
  * buy/sell volume is a CANDLE-LOCATION PROXY:  buy = volume x (close-low)/(high-low), sell = the rest.
    A candle that closes on its high counts as all buying; on its low, all selling.
  * "Absorption" is measured on that proxy over a short window of bars, not per price level.
  * Delta = buy - sell of the proxy. Divergences are therefore proxy divergences.
What is tested is a proxy of each concept. It cannot confirm or refute true footprint/tick-level order flow.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from impulse_bt.volume_profile import volume_profile


@dataclass(frozen=True)
class FeatureConfig:
    n_bins: int = 50
    hvn_pct: float = 70.0            # Concept 1: HVN = price bins above the 70th percentile of bin volume
    touch_bars: int = 3              # "price pulls back to HVN": extreme of the last 3 bars inside the zone
    tol_pct: float = 0.0003          # tolerance around a zone: 0.03% of price (or 0.25 ATR if larger)
    absorb_window: int = 5           # Concept 2: window of bars over which sell vs buy proxy is compared
    absorb_ratio: float = 5.0        #   sell > 5x buy (long) / buy > 5x sell (short)
    absorb_lookback: int = 15        #   "making lower lows": window low below the prior 15 bars' low
    pivot_k: int = 3                 # Concept 3: a pivot needs k lower/higher bars each side (confirmed k bars later)
    div_gap_max: int = 60            #   pivots at most 60 bars apart
    div_valid_bars: int = 15         #   a divergence stays actionable this many bars
    atr_len: int = 14
    drop_filler_bars: bool = True    # feed artefact: flat zero-volume bars (15:15-15:27 from Aug 2026)


def drop_filler(df: pd.DataFrame) -> pd.DataFrame:
    stale = (df["Volume"] == 0) & (df["Open"] == df["High"]) & (df["High"] == df["Low"]) & (df["Low"] == df["Close"])
    return df[~stale]


# ─── Concept 1 & 4: prior-day volume profile -> HVN zones and value area ──────────────────────────
def session_profile(h, l, v, cfg: FeatureConfig) -> Optional[Dict]:
    """HVN zones, POC and the spec's value area (POC +/- 1 volume-weighted standard deviation of price)."""
    prof = volume_profile(h, l, v, n_bins=cfg.n_bins)
    if prof is None or len(prof["volumes"]) < 3:
        return None
    vols, edges = prof["volumes"], prof["edges"]
    centres = (edges[:-1] + edges[1:]) / 2.0
    thr = np.percentile(vols, cfg.hvn_pct)
    hot = vols > thr                                       # strictly above the 70th percentile
    zones: List[tuple] = []
    i = 0
    while i < len(hot):
        if hot[i]:
            j = i
            while j + 1 < len(hot) and hot[j + 1]:
                j += 1
            peak = float(centres[i + int(np.argmax(vols[i:j + 1]))])
            zones.append((float(edges[i]), float(edges[j + 1]), peak))      # contiguous run = one node
            i = j + 1
        else:
            i += 1
    total = vols.sum()
    mean = float((centres * vols).sum() / total)
    sigma = float(np.sqrt((vols * (centres - mean) ** 2).sum() / total))
    return {"zones": zones, "poc": prof["poc"], "sigma": sigma,
            "va_lo": prof["poc"] - sigma, "va_hi": prof["poc"] + sigma}


# ─── proxies ──────────────────────────────────────────────────────────────────────────────────────
def proxy_flow(o, h, l, c, v):
    """Buy/sell volume proxy from candle location. A flat candle splits 50/50."""
    rng = h - l
    frac = np.where(rng > 0, (c - l) / np.where(rng > 0, rng, 1.0), 0.5)
    buy = v * frac
    return buy, v - buy, buy - (v - buy)


def wilder_smooth(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        out[n - 1] = x[:n].mean()
        for t in range(n, len(x)):
            out[t] = (out[t - 1] * (n - 1) + x[t]) / n
    return out


def build_features(df: pd.DataFrame, cfg: FeatureConfig = FeatureConfig(), gex: Optional[pd.Series] = None) -> pd.DataFrame:
    """Adds order-flow columns to an OHLCV frame (Open/High/Low/Close/Volume, naive IST index)."""
    if cfg.drop_filler_bars:
        df = drop_filler(df)
    df = df.copy()
    n = len(df)
    o, h, l, c, v = (df[k].to_numpy(dtype=float) for k in ("Open", "High", "Low", "Close", "Volume"))
    dates = df.index.normalize()
    codes = pd.factorize(dates)[0]
    starts = np.flatnonzero(np.r_[True, codes[1:] != codes[:-1]])
    ends = np.r_[starts[1:], n]

    tr = h - l
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    tr[starts] = (h - l)[starts]                           # no overnight gap in the true range of a session's first bar
    atr = wilder_smooth(tr, cfg.atr_len)

    buy, sell, delta = proxy_flow(o, h, l, c, v)
    cumdelta = np.zeros(n)
    minute = np.zeros(n)
    for s, e in zip(starts, ends):
        cumdelta[s:e] = np.cumsum(delta[s:e])
        minute[s:e] = (df.index[s:e] - df.index[s]).total_seconds() / 60.0

    col = {k: np.full(n, np.nan) for k in ("hvn_lvl", "next_hvn_up", "next_hvn_dn", "abs_lvl_long", "abs_lvl_short",
                                          "poc", "va_lo", "va_hi", "day_open")}
    flag = {k: np.zeros(n) for k in ("hvn_long", "hvn_short", "abs_long", "abs_short", "div_bull", "div_bear")}
    imb = np.zeros(n)
    w, lb, k = cfg.absorb_window, cfg.absorb_lookback, cfg.pivot_k

    prev = None
    for s, e in zip(starts, ends):
        P = prev
        prev = session_profile(h[s:e], l[s:e], v[s:e], cfg)         # becomes "yesterday" for the NEXT session
        if P is None:
            continue
        col["poc"][s:e], col["va_lo"][s:e], col["va_hi"][s:e], col["day_open"][s:e] = P["poc"], P["va_lo"], P["va_hi"], o[s]
        imb[s:e] = 1.0 if o[s] > P["va_hi"] else (-1.0 if o[s] < P["va_lo"] else 0.0)     # Concept 4
        zones = P["zones"]
        piv_lo: List[tuple] = []
        piv_hi: List[tuple] = []
        bull_until = bear_until = -1
        for t in range(s, e):
            a = max(s, t - cfg.touch_bars + 1)
            lo_, hi_ = l[a:t + 1].min(), h[a:t + 1].max()
            tol = max(0.25 * atr[t], cfg.tol_pct * c[t]) if not np.isnan(atr[t]) else cfg.tol_pct * c[t]
            # Concept 1: pullback into an HVN zone (long from above, short from below)
            zl = [z for z in zones if z[0] - tol <= lo_ <= z[1] + tol]
            zs = [z for z in zones if z[0] - tol <= hi_ <= z[1] + tol]
            if zl:
                z = min(zl, key=lambda q: abs(q[2] - lo_))
                flag["hvn_long"][t], col["hvn_lvl"][t] = 1.0, z[2]
                ups = [q[2] for q in zones if q[2] > max(c[t], z[1])]
                col["next_hvn_up"][t] = min(ups) if ups else np.nan
            if zs:
                z = min(zs, key=lambda q: abs(q[2] - hi_))
                flag["hvn_short"][t] = 1.0
                if not zl:
                    col["hvn_lvl"][t] = z[2]
                dns = [q[2] for q in zones if q[2] < min(c[t], z[0])]
                col["next_hvn_dn"][t] = max(dns) if dns else np.nan
            if not zl:                                       # targets are still needed for non-HVN variants
                ups = [q[2] for q in zones if q[2] > c[t]]
                col["next_hvn_up"][t] = min(ups) if ups else np.nan
            if not zs:
                dns = [q[2] for q in zones if q[2] < c[t]]
                col["next_hvn_dn"][t] = max(dns) if dns else np.nan

            # Concept 2 (proxy): absorption over the last `w` bars
            if t - w + 1 >= s and t - w >= s:
                wa = t - w + 1
                pa = max(s, wa - lb)
                wl, wh = l[wa:t + 1].min(), h[wa:t + 1].max()
                k_lo, k_hi = wa + int(np.argmin(l[wa:t + 1])), wa + int(np.argmax(h[wa:t + 1]))
                sb, ss = buy[wa:t + 1].sum(), sell[wa:t + 1].sum()
                if wl < l[pa:wa].min() and k_lo < t and c[t] > wl and ss >= cfg.absorb_ratio * max(sb, 1e-9):
                    flag["abs_long"][t], col["abs_lvl_long"][t] = 1.0, wl          # sellers absorbed, low defended
                if wh > h[pa:wa].max() and k_hi < t and c[t] < wh and sb >= cfg.absorb_ratio * max(ss, 1e-9):
                    flag["abs_short"][t], col["abs_lvl_short"][t] = 1.0, wh

            # Concept 3: delta divergence, confirmed only k bars after the pivot
            p = t - k
            if p - k >= s:
                if l[p] <= l[p - k:p + k + 1].min():
                    piv_lo.append((p, l[p], cumdelta[p]))
                    if len(piv_lo) >= 2 and piv_lo[-1][0] - piv_lo[-2][0] <= cfg.div_gap_max \
                            and piv_lo[-1][1] < piv_lo[-2][1] and piv_lo[-1][2] > piv_lo[-2][2]:
                        bull_until = t + cfg.div_valid_bars                        # lower low in price, higher low in delta
                if h[p] >= h[p - k:p + k + 1].max():
                    piv_hi.append((p, h[p], cumdelta[p]))
                    if len(piv_hi) >= 2 and piv_hi[-1][0] - piv_hi[-2][0] <= cfg.div_gap_max \
                            and piv_hi[-1][1] > piv_hi[-2][1] and piv_hi[-1][2] < piv_hi[-2][2]:
                        bear_until = t + cfg.div_valid_bars                        # higher high in price, lower high in delta
            flag["div_bull"][t] = 1.0 if t <= bull_until else 0.0
            flag["div_bear"][t] = 1.0 if t <= bear_until else 0.0

    out = df
    out["atr"], out["delta"], out["cumdelta"], out["minute"], out["imb"] = atr, delta, cumdelta, minute, imb
    for name, arr in {**col, **flag}.items():
        out[name] = arr
    if gex is not None:                                     # Concept 5: one reading per day, known at the open
        g = gex.copy()
        g.index = pd.to_datetime(g.index).normalize()
        out["gex"] = dates.map(g).to_numpy(dtype=float)
    else:
        out["gex"] = np.nan
    return out
