"""
Project Atlas - DRIP Score Backtest (point-in-time, cross-sectional)
====================================================================
Question answered: on each past date, did the stocks the score ranked highest actually deliver
better FORWARD TOTAL RETURN (price + dividends) than the rest of the universe?

Method: monthly, rank every eligible stock using ONLY data available on that date, then compare
the top quintile's forward return with the bottom quintile's and the universe average, and
measure the rank correlation (Spearman IC) between score and forward return.

What this can and cannot test:
  - TESTED: the price/dividend half of the score: dividend yield, dividend consistency and
    growth, 200-day trend, 3-year price momentum. All are rebuilt exactly as of each past date.
  - NOT TESTED: the fundamental half (profit growth, PE valuation, ROE, quality). Yahoo has no
    point-in-time fundamentals, so using today's numbers on past dates would leak the future.
Known biases (all flatter the results): survivorship (the universe is today's stocks), no
transaction costs, and overlapping windows (t-stats use non-overlapping dates to compensate).
"""
import bisect
import math
import random
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

from data.stock_profiles import history_stats
from scoring.total_return_score import score_stock

IST = timezone(timedelta(hours=5, minutes=30))
DAY = 86400
HORIZONS_DAYS = {"3m": 91, "6m": 182, "12m": 365}
MIN_HISTORY_YEARS = 3.2          # price_cagr_3y needs 3 years behind the date
STALE_DAYS = 6                   # a stock with no print this close to the date is treated as untradeable


def _d(ts: int) -> date:
    return datetime.fromtimestamp(ts, tz=IST).date()


def _idx_at(ts: List[int], when: int) -> Optional[int]:
    """Index of the last bar at or before `when`, if it is not stale."""
    i = bisect.bisect_right(ts, when) - 1
    return i if i >= 0 and when - ts[i] <= STALE_DAYS * DAY else None


def spearman(a: List[float], b: List[float]) -> Optional[float]:
    if len(a) < 3 or len(a) != len(b):
        return None

    def ranks(x):
        order = sorted(range(len(x)), key=lambda i: x[i])
        r, i = [0.0] * len(x), 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and x[order[j + 1]] == x[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2.0 + 1.0
            i = j + 1
        return r
    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va, vb = sum((x - ma) ** 2 for x in ra), sum((y - mb) ** 2 for y in rb)
    return cov / math.sqrt(va * vb) if va > 0 and vb > 0 else None


def features_at(hist: Dict, i: int) -> Optional[Tuple[Dict, float, float, Optional[float]]]:
    """(profile, price, ttm_dps, trend_pct) using only bars 0..i. None if history is too short."""
    ts = hist["ts"]
    if (ts[i] - ts[0]) / (365 * DAY) < MIN_HISTORY_YEARS:
        return None
    asof = _d(ts[i])
    cutoff = asof.isoformat()
    cut = bisect.bisect_right(hist["ev_dates"], cutoff)
    sl = {"ts": ts[:i + 1], "close": hist["close"][:i + 1], "volume": hist["volume"][:i + 1],
          "events": hist["events"][:cut]}
    st = history_stats(sl, asof)
    price = sl["close"][-1]
    ttm = st["dividend_history"][-1] if st["dividend_history"] else 0.0
    win = sl["close"][-200:]
    trend = (price / (sum(win) / len(win)) - 1.0) * 100.0 if len(win) >= 100 else None
    profile = {**st, "current_price": price, "dividend_yield_pct": ttm / price * 100.0, "sector": "UNKNOWN"}
    return profile, price, ttm, trend


def forward_return(hist: Dict, i: int, horizon_days: int) -> Optional[float]:
    """Total return from bar i to the horizon: price change plus dividends with ex-date in (t, t+h]."""
    ts = hist["ts"]
    target = ts[i] + horizon_days * DAY
    if target > ts[-1]:
        return None
    j = _idx_at(ts, target)
    if j is None or j <= i:
        return None
    lo, hi = _d(ts[i]).isoformat(), _d(ts[j]).isoformat()
    a, b = bisect.bisect_right(hist["ev_dates"], lo), bisect.bisect_right(hist["ev_dates"], hi)
    divs = sum(e["dps"] for e in hist["events"][a:b])
    return (hist["close"][j] + divs) / hist["close"][i] - 1.0


def prepare(histories: Dict[str, Dict]) -> Dict[str, Dict]:
    """Adds the sorted ex-date index used for point-in-time dividend slicing."""
    out = {}
    for s, h in histories.items():
        ev = sorted(h["events"], key=lambda e: e["ex_date"])
        out[s] = {**h, "events": ev, "ev_dates": [e["ex_date"] for e in ev]}
    return out


def make_signals(seed: int = 0) -> Dict[str, Callable]:
    rng = random.Random(seed)
    return {
        "atlas_history": lambda sym, p, price, ttm, tr: score_stock(sym, p, price, ttm, tr)["efficiency"],
        "yield_only": lambda sym, p, price, ttm, tr: ttm / price,
        "trend_only": lambda sym, p, price, ttm, tr: tr if tr is not None else 0.0,
        "momentum_3y": lambda sym, p, price, ttm, tr: p["price_cagr_3y"] if p.get("price_cagr_3y") is not None else 0.0,
        "random": lambda sym, p, price, ttm, tr: rng.random(),
    }


def _mean(x):
    return sum(x) / len(x) if x else None


def _tstat(x):
    if len(x) < 3:
        return None
    m = sum(x) / len(x)
    sd = math.sqrt(sum((v - m) ** 2 for v in x) / (len(x) - 1))
    return m / (sd / math.sqrt(len(x))) if sd > 0 else None


def run_backtest(histories: Dict[str, Dict], step_days: int = 30, min_universe: int = 20,
                 min_years: int = 3, min_turnover_cr: float = 10.0, seed: int = 0,
                 signals: Optional[Dict[str, Callable]] = None) -> Dict:
    H = prepare(histories)
    signals = signals or make_signals(seed)
    ref = max(H.values(), key=lambda h: len(h["ts"]))["ts"]
    start = ref[0] + int(MIN_HISTORY_YEARS * 365 * DAY) + 10 * DAY
    dates = list(range(start, ref[-1] - min(HORIZONS_DAYS.values()) * DAY, step_days * DAY))

    # rows[signal][horizon] -> list of per-date dicts
    rows: Dict[str, Dict[str, List[Dict]]] = {s: {h: [] for h in HORIZONS_DAYS} for s in signals}
    for when in dates:
        cross = []
        for sym, h in H.items():
            i = _idx_at(h["ts"], when)
            if i is None:
                continue
            f = features_at(h, i)
            if f is None:
                continue
            p, price, ttm, tr = f
            if p["years_consecutive_dividend"] < min_years or p["avg_turnover_cr"] < min_turnover_cr:
                continue
            fwd = {k: forward_return(h, i, d) for k, d in HORIZONS_DAYS.items()}
            cross.append((sym, p, price, ttm, tr, fwd))
        for name, fn in signals.items():
            scored = [(fn(sym, p, price, ttm, tr), fwd) for sym, p, price, ttm, tr, fwd in cross]
            for hname in HORIZONS_DAYS:
                pts = [(sc, fw[hname]) for sc, fw in scored if fw[hname] is not None]
                if len(pts) < min_universe:
                    continue
                pts.sort(key=lambda t: -t[0])
                q = max(1, len(pts) // 5)
                top, bot = [r for _, r in pts[:q]], [r for _, r in pts[-q:]]
                rows[name][hname].append({
                    "when": _d(when).isoformat(), "n": len(pts),
                    "ic": spearman([s for s, _ in pts], [r for _, r in pts]),
                    "top": _mean(top), "bottom": _mean(bot), "universe": _mean([r for _, r in pts])})

    summary: Dict[str, Dict[str, Dict]] = {}
    for name in signals:
        summary[name] = {}
        for hname, hd in HORIZONS_DAYS.items():
            r = rows[name][hname]
            k = max(1, math.ceil(hd / step_days))            # non-overlapping subsample for significance
            sub = r[::k]
            ics = [x["ic"] for x in r if x["ic"] is not None]
            half = len(r) // 2
            summary[name][hname] = {
                "dates": len(r), "independent_dates": len(sub),
                "mean_ic": _mean(ics), "ic_t": _tstat([x["ic"] for x in sub if x["ic"] is not None]),
                "ic_positive_pct": 100.0 * sum(1 for v in ics if v > 0) / len(ics) if ics else None,
                "ic_first_half": _mean([x["ic"] for x in r[:half] if x["ic"] is not None]),
                "ic_second_half": _mean([x["ic"] for x in r[half:] if x["ic"] is not None]),
                "top_ret": _mean([x["top"] for x in r]), "bottom_ret": _mean([x["bottom"] for x in r]),
                "universe_ret": _mean([x["universe"] for x in r]),
                "top_minus_universe": _mean([x["top"] - x["universe"] for x in r]),
                "excess_t": _tstat([x["top"] - x["universe"] for x in sub]),
                "avg_universe_size": _mean([x["n"] for x in r]),
            }
    return {"summary": summary, "first_date": _d(dates[0]).isoformat() if dates else None,
            "last_date": _d(dates[-1]).isoformat() if dates else None, "rows": rows}
