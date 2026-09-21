"""
Project Atlas - Momentum-Led Investment Scanner
===============================================
Ranks the NSE universe for medium-term investing (hold weeks to months, rebalance monthly).

Score (all cross-sectional percentile ranks among liquid stocks, weights fixed up front and NOT
fitted to the backtest):

    0.35 * rank(12-1 month momentum)   price 12 months ago -> 1 month ago (skips the reversal month)
  + 0.25 * rank(6 month momentum)
  + 0.20 * rank(price vs 200-day average)
  + 0.20 * rank(closeness to the 52-week high)
  - 0.15 * rank(volatility)            calmer stocks preferred at equal momentum

Gates: at least 253 daily bars, turnover >= 10 Cr/day, price above its 200-day average.
Portfolio: top N by score, inverse-volatility weights, single-stock cap. Optional market risk-off
switch (off by default, see ScannerConfig.regime_filter): if fewer than 40% of liquid stocks are
above their own 200-day average, hold cash.

BACKTEST REALITY CHECK (2015-2026, monthly, 0.25%/side): about 22% CAGR with the switch off, 17% with
it on, versus 21.5% for simply holding the whole (survivorship-flattered) universe equal-weighted.
No demonstrated edge over the universe, and the 25% target was met in only 5 of 12 calendar years.

Everything in the score comes from prices, so `backtest()` can replay it point-in-time with
costs. The quality filter (ROE / debt / profit growth) uses TODAY'S fundamentals and Yahoo has no
point-in-time fundamentals, so it is applied to live picks only and is untested.

Known biases in the backtest (all flatter it): survivorship (the universe is today's stocks) and
cash earning 0% is the only conservative offset. Read `backtest()`'s output as an upper-ish bound.
"""
import bisect
import math
import random
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List, Optional, Tuple

DAY = 86400
IST = timezone(timedelta(hours=5, minutes=30))
MIN_BARS = 253                      # 12-1 momentum needs close[i-252]
TARGET_ANNUAL_RETURN = 0.25


@dataclass(frozen=True)
class ScannerConfig:
    w_mom_12_1: float = 0.35
    w_mom_6: float = 0.25
    w_trend: float = 0.20
    w_high: float = 0.20
    vol_penalty: float = 0.15
    min_turnover_cr: float = 10.0   # units of 1e7 LOCAL currency per day (Rs 10 Cr in India)
    turnover_scale: float = 1.0     # price*volume -> local currency (LSE quotes pence: 0.01)
    top_n: int = 12
    max_weight: float = 0.15
    rebalance_days: int = 30
    cost_per_side: float = 0.0025   # STT 0.1% + stamp/exchange/GST ~0.05% + 0.1% slippage, per side
    # OFF by default. It was defined up front, but the 2015-2026 backtest showed it cost CAGR
    # (16.9% vs 22.3%) and did not reduce drawdown (-28% vs -24%). Turning it off was decided AFTER
    # seeing that result, so treat the default as tuned; both variants are reported.
    regime_filter: bool = False
    weighting: str = "inverse_vol"  # or "equal" (used by the walk-forward study's simpler variants)
    min_breadth: float = 0.40       # risk-on needs this share of liquid stocks above their 200dma
    stale_days: int = 6             # no print this close to the date = untradeable
    # live quality overlay (untestable: uses today's fundamentals)
    min_roe: float = 10.0
    max_debt_to_equity: float = 2.5
    max_pe: float = 90.0


# ── features ────────────────────────────────────────────────────────────────

def features_at(close: List[float], volume: List[float], i: int, turnover_scale: float = 1.0) -> Optional[Dict]:
    """Momentum/trend/vol features using only bars 0..i. None if history is too short or bad."""
    if i < MIN_BARS - 1 or i >= len(close):
        return None
    p = close[i]
    if not p or p <= 0:
        return None
    b12, b1, b6 = close[i - 252], close[i - 21], close[i - 126]
    if not (b12 and b1 and b6) or min(b12, b1, b6) <= 0:
        return None
    win = close[i - 199:i + 1]
    hi = max(close[i - 251:i + 1])
    rets = []
    for k in range(i - 125, i + 1):
        a, b = close[k - 1], close[k]
        if a and b and a > 0 and b > 0:
            rets.append(math.log(b / a))
    if len(rets) < 60:
        return None
    m = sum(rets) / len(rets)
    vol = math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1)) * math.sqrt(252)
    turn = sum(close[k] * (volume[k] or 0) for k in range(i - 19, i + 1)) / 20.0 * turnover_scale / 1e7
    return {
        "price": p,
        "mom_12_1": b1 / b12 - 1.0,
        "mom_6": p / b6 - 1.0,
        "trend_pct": (p / (sum(win) / len(win)) - 1.0) * 100.0,
        "high_prox": p / hi,
        "vol": vol,
        "turnover_cr": turn,
    }


def _pct_ranks(values: List[float]) -> List[float]:
    """Percentile rank in [0, 1], ties share the average rank."""
    n = len(values)
    if n == 1:
        return [0.5]
    order = sorted(range(n), key=lambda k: values[k])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = ((i + j) / 2.0) / (n - 1)
        i = j + 1
    return ranks


def score_universe(feats: Dict[str, Dict], cfg: ScannerConfig = ScannerConfig()) -> List[Dict]:
    """Ranked candidates (best first). Ranks are taken among liquid stocks; the trend gate then
    removes anything below its 200-day average."""
    liquid = {s: f for s, f in feats.items() if f["turnover_cr"] >= cfg.min_turnover_cr}
    if not liquid:
        return []
    syms = list(liquid)
    r12 = _pct_ranks([liquid[s]["mom_12_1"] for s in syms])
    r6 = _pct_ranks([liquid[s]["mom_6"] for s in syms])
    rt = _pct_ranks([liquid[s]["trend_pct"] for s in syms])
    rh = _pct_ranks([liquid[s]["high_prox"] for s in syms])
    rv = _pct_ranks([liquid[s]["vol"] for s in syms])

    out = []
    for k, s in enumerate(syms):
        f = liquid[s]
        if f["trend_pct"] <= 0:
            continue
        score = (cfg.w_mom_12_1 * r12[k] + cfg.w_mom_6 * r6[k] + cfg.w_trend * rt[k]
                 + cfg.w_high * rh[k] - cfg.vol_penalty * rv[k])
        out.append({"symbol": s, "score": round(score, 4), **f})
    out.sort(key=lambda r: -r["score"])
    return out


def breadth(feats: Dict[str, Dict], cfg: ScannerConfig = ScannerConfig()) -> Optional[float]:
    liquid = [f for f in feats.values() if f["turnover_cr"] >= cfg.min_turnover_cr]
    if len(liquid) < 10:
        return None
    return sum(1 for f in liquid if f["trend_pct"] > 0) / len(liquid)


def risk_on(feats: Dict[str, Dict], cfg: ScannerConfig = ScannerConfig()) -> bool:
    if not cfg.regime_filter:
        return True
    b = breadth(feats, cfg)
    return b is None or b >= cfg.min_breadth


def inverse_vol_weights(picks: List[Dict], max_weight: float) -> Dict[str, float]:
    """Inverse-volatility weights summing to <= 1, each capped at max_weight (any excess is cash)."""
    if not picks:
        return {}
    inv = {p["symbol"]: 1.0 / max(p["vol"], 0.05) for p in picks}
    total = sum(inv.values())
    return {s: min(max_weight, v / total) for s, v in inv.items()}


def portfolio_weights(picks: List[Dict], cfg: ScannerConfig) -> Dict[str, float]:
    """Weights for the chosen stocks: inverse-volatility (default) or equal, both capped at cfg.max_weight."""
    if cfg.weighting == "equal":
        return {p["symbol"]: min(cfg.max_weight, 1.0 / len(picks)) for p in picks} if picks else {}
    return inverse_vol_weights(picks, cfg.max_weight)


# ── live quality overlay ────────────────────────────────────────────────────

def quality_check(profile: Optional[Dict], cfg: ScannerConfig = ScannerConfig()) -> Tuple[bool, List[str], List[str]]:
    """(passes, fail_reasons, unverified_fields) from today's profile. Missing data never rejects."""
    if not profile:
        return True, [], ["no fundamentals"]
    fails, missing = [], []

    def num(k):
        v = profile.get(k)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    roe, de, pg, pe = num("roe_pct"), num("debt_to_equity"), num("profit_cagr_5y"), num("pe_ratio")
    financial = profile.get("sector") == "Financial Services"
    if roe is None:
        missing.append("ROE")
    elif roe < cfg.min_roe:
        fails.append(f"ROE {roe:.0f}% < {cfg.min_roe:.0f}%")
    if de is None:
        missing.append("debt")
    elif not financial and de > cfg.max_debt_to_equity:
        fails.append(f"D/E {de:.1f} > {cfg.max_debt_to_equity:g}")
    if pg is not None and pg < 0:
        fails.append(f"profit CAGR {pg:.0f}% < 0")
    if pe is not None and pe > cfg.max_pe:
        fails.append(f"PE {pe:.0f} > {cfg.max_pe:.0f}")
    return not fails, fails, missing


# ── live scan ───────────────────────────────────────────────────────────────

def scan(prices: Dict[str, Dict], profiles: Optional[Dict[str, Dict]] = None,
         cfg: ScannerConfig = ScannerConfig()) -> Dict:
    """
    prices:   {symbol: {"ts": [...], "close": [...], "volume": [...]}} daily bars, oldest first.
    profiles: {symbol: profile} (data/stock_profiles.py) for the quality overlay and sector names.
    Uses each stock's latest bar. Returns picks, rejects and the risk-on/off state.
    """
    profiles = profiles or {}
    feats, latest = {}, 0
    for sym, h in prices.items():
        i = len(h["close"]) - 1
        f = features_at(h["close"], h["volume"], i, cfg.turnover_scale)
        if f is not None:
            f["last_bar"] = h["ts"][i]
            feats[sym] = f
            latest = max(latest, h["ts"][i])

    # a stock whose last print is old relative to the freshest one is not tradeable today
    feats = {s: f for s, f in feats.items() if latest - f["last_bar"] <= cfg.stale_days * DAY}
    ranked = score_universe(feats, cfg)
    b = breadth(feats, cfg)
    on = risk_on(feats, cfg)

    picks, rejected = [], []
    for r in ranked:
        ok, fails, missing = quality_check(profiles.get(r["symbol"]), cfg)
        r = {**r, "sector": (profiles.get(r["symbol"]) or {}).get("sector"), "unverified": missing}
        if ok:
            picks.append(r)
        else:
            rejected.append({"symbol": r["symbol"], "score": r["score"], "reasons": fails})
        if len(picks) >= cfg.top_n:
            break

    weights = portfolio_weights(picks, cfg) if on else {}
    for p in picks:
        p["weight"] = round(weights.get(p["symbol"], 0.0), 4)
    return {
        "asof": datetime.fromtimestamp(latest, tz=IST).date().isoformat() if latest else None,
        "risk_on": on, "breadth": None if b is None else round(b, 3),
        "universe_scanned": len(prices), "eligible": len(ranked),
        "picks": picks, "rejected": rejected[:10],
        "invested_pct": round(sum(weights.values()) * 100.0, 1),
    }


# ── point-in-time backtest ──────────────────────────────────────────────────

def _d(ts: int) -> date:
    return datetime.fromtimestamp(ts, tz=IST).date()


def _idx_at(ts: List[int], when: int, stale_days: int) -> Optional[int]:
    i = bisect.bisect_right(ts, when) - 1
    return i if i >= 0 and when - ts[i] <= stale_days * DAY else None


def _period_return(h: Dict, i: int, j: int) -> float:
    """Total return from bar i to bar j including dividends with ex-date in (i, j]."""
    lo, hi = _d(h["ts"][i]).isoformat(), _d(h["ts"][j]).isoformat()
    a, b = bisect.bisect_right(h["ev_dates"], lo), bisect.bisect_right(h["ev_dates"], hi)
    divs = sum(e["dps"] for e in h["events"][a:b])
    return (h["close"][j] + divs) / h["close"][i] - 1.0


def _prep_one(h: Dict) -> Dict:
    ev = sorted(h.get("events", []), key=lambda e: e["ex_date"])
    return {**h, "events": ev, "ev_dates": [e["ex_date"] for e in ev]}


def _stats(rets: List[float], dates: List[str], period_days: int) -> Dict:
    if not rets:
        return {}
    curve, peak, mdd = 1.0, 1.0, 0.0
    for r in rets:
        curve *= 1.0 + r
        peak = max(peak, curve)
        mdd = min(mdd, curve / peak - 1.0)
    years = len(rets) * period_days / 365.0
    m = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1)) if len(rets) > 1 else 0.0
    ann_vol = sd * math.sqrt(365.0 / period_days)
    cagr = curve ** (1.0 / years) - 1.0 if years > 0 and curve > 0 else -1.0
    by_year: Dict[str, float] = {}
    for r, d in zip(rets, dates):
        y = d[:4]
        by_year[y] = (1.0 + by_year.get(y, 0.0)) * (1.0 + r) - 1.0
    return {
        "cagr": cagr, "total_return": curve - 1.0, "ann_vol": ann_vol,
        "sharpe_0rf": (m * 365.0 / period_days) / ann_vol if ann_vol > 0 else None,
        "max_drawdown": mdd, "periods": len(rets), "years": round(years, 2),
        "positive_periods_pct": 100.0 * sum(1 for r in rets if r > 0) / len(rets),
        "yearly": {y: round(v, 4) for y, v in by_year.items()},
    }


def active_stats(strat: List[float], bench: List[float], period_days: int) -> Dict:
    """Period-by-period excess return over the benchmark: is the edge distinguishable from luck?"""
    d = [a - b for a, b in zip(strat, bench)]
    n = len(d)
    if n < 3:
        return {}
    m = sum(d) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in d) / (n - 1))
    ppy = 365.0 / period_days
    return {
        "mean_excess_per_year": m * ppy, "tracking_error": sd * math.sqrt(ppy),
        "t_stat": m / (sd / math.sqrt(n)) if sd > 0 else None,
        "information_ratio": (m * ppy) / (sd * math.sqrt(ppy)) if sd > 0 else None,
        "hit_rate_pct": 100.0 * sum(1 for x in d if x > 0) / n, "periods": n,
    }


def make_dates(ref_ts: List[int], cfg: ScannerConfig = ScannerConfig()) -> Tuple[List[int], int]:
    """Monthly rebalance dates starting once 12-1 momentum has enough history; returns (dates, end_ts)."""
    start = ref_ts[0] + 380 * DAY
    step = cfg.rebalance_days * DAY
    return list(range(start, ref_ts[-1] - step + 1, step)), ref_ts[-1]


def prepare_periods(stocks: Iterable[Tuple[str, Dict]], dates: List[int], end_ts: int,
                    cfg: ScannerConfig = ScannerConfig()) -> Dict:
    """
    Phase 1 (the slow part): for every stock and rebalance date, the point-in-time features and the
    forward period return. Streams stock by stock, so a 2,300-stock market never sits in memory as raw
    history. Only liquid stocks are kept (they are the only ones the scanner and benchmark use).
    """
    nxt = dates[1:] + [end_ts]
    table: List[Dict] = [dict() for _ in dates]
    for sym, raw in stocks:
        h = _prep_one(raw)
        ts = h["ts"]
        for k, (when, nx) in enumerate(zip(dates, nxt)):
            i = _idx_at(ts, when, cfg.stale_days)
            if i is None:
                continue
            f = features_at(h["close"], h["volume"], i, cfg.turnover_scale)
            if f is None or f["turnover_cr"] < cfg.min_turnover_cr:
                continue
            j = _idx_at(ts, nx, cfg.stale_days)
            table[k][sym] = (f, _period_return(h, i, j) if j is not None and j > i else 0.0)
    return {"dates": dates, "end": end_ts, "table": table}


def slice_periods(prepared: Dict, first_date: Optional[str] = None, last_date: Optional[str] = None) -> Dict:
    """
    Sub-window of a prepared table by rebalance-date (YYYY-MM-DD, inclusive). Features were computed
    point-in-time on the full history, so a window that starts in 2022 still ranks with 2021 data,
    which is correct: nothing after each date is used. Portfolio state (holdings, costs) restarts
    empty at the window start, so every candidate pays the same initial buy.
    """
    keep = [k for k, ts in enumerate(prepared["dates"])
            if (first_date is None or _d(ts).isoformat() >= first_date)
            and (last_date is None or _d(ts).isoformat() <= last_date)]
    if not keep:
        return {"dates": [], "end": prepared["end"], "table": []}
    a, b = keep[0], keep[-1] + 1
    return {"dates": prepared["dates"][a:b], "table": prepared["table"][a:b],
            "end": prepared["dates"][b] if b < len(prepared["dates"]) else prepared["end"]}


def rank_periods(prepared: Dict, cfg: ScannerConfig = ScannerConfig()) -> Tuple[List[Optional[List[Dict]]], List[bool]]:
    """Per period: the ranked candidate list (None if the universe is too thin) and the risk-on flag."""
    ranked, on = [], []
    for tbl in prepared["table"]:
        feats = {s: fr[0] for s, fr in tbl.items()}
        ranked.append(score_universe(feats, cfg) if len(feats) >= 15 else None)
        on.append(risk_on(feats, cfg))
    return ranked, on


def run_periods(prepared: Dict, ranked: List, on: List[bool], cfg: ScannerConfig = ScannerConfig(),
                chooser: Optional[Callable[[List[Dict], int], List[Dict]]] = None) -> Dict:
    """
    Phase 2 (cheap): pick, weight, pay costs, compound. `chooser(ranked_list, period_index)` returns the
    picks; the default is the top-N by score. Reusing phase 1 lets the random baseline run hundreds of times.
    """
    strat, bench, dstr, held, prev_rets, cash_periods, traded = [], [], [], {}, {}, 0, []
    for k, tbl in enumerate(prepared["table"]):
        if ranked[k] is None:
            continue
        rets = {s: fr[1] for s, fr in tbl.items()}
        if on[k]:
            picks = chooser(ranked[k], k) if chooser else ranked[k][:cfg.top_n]
        else:
            picks, cash_periods = [], cash_periods + 1
        w_new = portfolio_weights(picks, cfg)

        # costs on traded value: last period's weights drift with LAST period's returns to today,
        # then trade to the new weights
        drift_total = sum(w * (1.0 + prev_rets.get(s, 0.0)) for s, w in held.items()) + max(0.0, 1.0 - sum(held.values()))
        drifted = {s: w * (1.0 + prev_rets.get(s, 0.0)) / drift_total for s, w in held.items()} if drift_total > 0 else {}
        turnover = sum(abs(w_new.get(s, 0.0) - drifted.get(s, 0.0)) for s in set(w_new) | set(drifted))
        cost = cfg.cost_per_side * turnover
        traded.append(turnover / 2.0)                       # one-way: the share of the portfolio bought

        gross = sum(w * rets[s] for s, w in w_new.items())
        strat.append((1.0 - cost) * (1.0 + gross) - 1.0)
        bench.append(sum(rets.values()) / len(rets) if rets else 0.0)
        end = prepared["dates"][k + 1] if k + 1 < len(prepared["dates"]) else prepared["end"]
        dstr.append(_d(end).isoformat())
        held, prev_rets = w_new, rets
    return {"strat": strat, "bench": bench, "dates": dstr, "cash_periods": cash_periods, "traded": traded}


def random_baseline(prepared: Dict, ranked: List, on: List[bool], cfg: ScannerConfig = ScannerConfig(),
                    runs: int = 200, seed: int = 0) -> Dict:
    """
    Null test: the same gates, weights, costs and rebalance schedule, but the N stocks are chosen AT RANDOM
    from the eligible (liquid, above-200dma) pool. If the scanner's ranking has real skill its CAGR should sit
    far above this distribution; if it lands in the middle, the ranking adds nothing beyond the gates.
    """
    rng = random.Random(seed)

    def pick(cands, k):
        return rng.sample(cands, min(cfg.top_n, len(cands)))

    cagrs = []
    for _ in range(runs):
        r = run_periods(prepared, ranked, on, cfg, chooser=pick)
        cagrs.append(_stats(r["strat"], r["dates"], cfg.rebalance_days).get("cagr", 0.0))
    cagrs.sort()

    def q(p):
        return cagrs[min(len(cagrs) - 1, int(p * len(cagrs)))]

    return {"runs": runs, "mean_cagr": sum(cagrs) / len(cagrs), "median_cagr": q(0.5),
            "p05_cagr": q(0.05), "p95_cagr": q(0.95), "cagrs": cagrs}


def summarize(prepared: Dict, cfg: ScannerConfig = ScannerConfig(), random_runs: int = 0, seed: int = 0) -> Dict:
    ranked, on = rank_periods(prepared, cfg)
    r = run_periods(prepared, ranked, on, cfg)
    strat, bench, dstr = r["strat"], r["bench"], r["dates"]
    half = len(strat) // 2
    s_all, b_all = _stats(strat, dstr, cfg.rebalance_days), _stats(bench, dstr, cfg.rebalance_days)
    out = {
        "config": {"top_n": cfg.top_n, "rebalance_days": cfg.rebalance_days, "cost_per_side": cfg.cost_per_side,
                   "regime_filter": cfg.regime_filter, "max_weight": cfg.max_weight},
        "first_date": _d(prepared["dates"][0]).isoformat() if prepared["dates"] else None,
        "last_date": dstr[-1] if dstr else None,
        "strategy": s_all, "benchmark": b_all,
        "active": active_stats(strat, bench, cfg.rebalance_days),
        "first_half": _stats(strat[:half], dstr[:half], cfg.rebalance_days),
        "second_half": _stats(strat[half:], dstr[half:], cfg.rebalance_days),
        "benchmark_first_half": _stats(bench[:half], dstr[:half], cfg.rebalance_days),
        "benchmark_second_half": _stats(bench[half:], dstr[half:], cfg.rebalance_days),
        "cash_periods_pct": round(100.0 * r["cash_periods"] / max(1, len(strat)), 1),
        "avg_eligible": round(sum(len(x) for x in ranked if x) / max(1, sum(1 for x in ranked if x)), 1),
        "target_annual_return": TARGET_ANNUAL_RETURN,
        "years_meeting_target": sum(1 for v in s_all.get("yearly", {}).values() if v >= TARGET_ANNUAL_RETURN),
        "years_total": len(s_all.get("yearly", {})),
        "caveats": ["survivorship bias (universe = today's stocks)", "price-only score; quality filter untested",
                    "cash earns 0%", "benchmark has no costs"],
    }
    # Turnover and the cost it causes. Momentum picks persist, so this is far below a random picker's.
    ppy = 365.0 / cfg.rebalance_days
    one_way = sum(r["traded"]) / len(r["traded"]) if r["traded"] else 0.0
    free = run_periods(prepared, ranked, on, replace(cfg, cost_per_side=0.0))
    s_free = _stats(free["strat"], free["dates"], cfg.rebalance_days)
    out["turnover"] = {"avg_one_way_monthly_pct": round(one_way * 100.0, 1),
                       "cagr_before_costs": s_free.get("cagr"),
                       "cost_drag_per_year": (s_free.get("cagr", 0.0) - s_all["cagr"]) if s_all else None,
                       "note": f"~{one_way * ppy * 100:.0f}% of the portfolio is bought each year"}

    if random_runs and s_all:
        # FAIR null test: no costs on either side. A random picker reshuffles ~87% of the portfolio a month
        # versus ~42% for momentum, so comparing after costs would credit the scanner for merely trading less.
        rb = random_baseline(prepared, ranked, on, replace(cfg, cost_per_side=0.0), runs=random_runs, seed=seed)
        below = sum(1 for c in rb["cagrs"] if c < s_free["cagr"])
        out["random"] = {**{k: v for k, v in rb.items() if k != "cagrs"},
                         "scanner_cagr_before_costs": s_free["cagr"],
                         "scanner_percentile": round(100.0 * below / len(rb["cagrs"]), 1),
                         "note": "selection skill only: scanner vs random picks from the same eligible pool, both before costs"}
    return out


def backtest(histories: Dict[str, Dict], cfg: ScannerConfig = ScannerConfig(), random_runs: int = 0) -> Dict:
    """
    Monthly point-in-time replay of the price-only scanner with transaction costs.
    Compares against the equal-weight liquid universe (no costs: flatters the benchmark slightly).
    """
    if not histories:
        return {}
    ref = max(histories.values(), key=lambda h: len(h["ts"]))["ts"]
    dates, end = make_dates(ref, cfg)
    return summarize(prepare_periods(histories.items(), dates, end, cfg), cfg, random_runs)
