"""
Project Atlas - Total Return Score (dividend yield + capital appreciation)
==========================================================================
Ranks stocks for dividend reinvestment by EXPECTED ANNUAL TOTAL RETURN, quality-adjusted:

    expected_return = forward dividend yield + expected price growth + valuation + trend
    efficiency      = expected_return x quality multiplier

Every constant lives in ScoreConfig. These are transparent heuristic priors, NOT a
backtested model: growth haircuts and valuation sensitivity are assumptions to tune.

`f` is a profile (data/stock_profiles.py) or a curated STOCK_FUNDAMENTALS entry. Any field may
be missing/None: the score then uses what exists, flags the gap, and lowers confidence.

Live inputs passed alongside:
    price     - live last traded price
    ttm_dps   - trailing-12-month dividend per share from real ex-dividend events; yield is
                ttm_dps / live price, so a price fall raises yield honestly.
    trend_pct - live price vs its 200-day average (%). Rewards capital appreciation and stops a
                collapsing stock ranking high purely because its yield spiked (yield trap).
"""
from dataclasses import dataclass
from typing import Dict, List, Optional

from features.dividend import evaluate_dividend_sustainability


@dataclass(frozen=True)
class ScoreConfig:
    hist_growth_haircut: float = 0.5        # profit CAGR rarely repeats in full
    max_hist_growth: float = 12.0
    max_sustainable_growth: float = 15.0    # ROE x retention, capped
    div_growth_credit: float = 0.5          # share of dividend CAGR credited to forward yield
    max_div_growth: float = 15.0
    fair_pe_base: float = 8.5               # Graham-style: fair PE = base + per_growth x growth
    fair_pe_per_growth: float = 2.0
    valuation_sensitivity: float = 3.0      # return points per unit of (1 - PE/fair PE)
    max_valuation_drag: float = 4.0
    max_valuation_bonus: float = 2.0
    trend_weight: float = 0.15              # return points per 1% above/below the 200-day average
    max_trend_drag: float = 4.0             # yield-trap guard
    max_trend_bonus: float = 2.0
    price_hist_weight: float = 0.10         # return points per 1% of 3-year price CAGR
    max_price_hist: float = 2.0
    stale_band: tuple = (0.6, 1.6)          # live/table price outside this: table stale or split-hit
    stale_confidence: float = 0.85
    sparse_threshold: float = 0.6           # fraction of key fundamentals present


SUSTAINABILITY_FACTOR = {
    "High Safety": 1.00, "Moderate Safety": 0.95, "Unknown": 0.90,
    "At Risk": 0.85, "Dangerous": 0.70,
}


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _num(f: Dict, k: str) -> Optional[float]:
    v = f.get(k)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _proxy_piotroski(f: Dict) -> Optional[float]:
    """Quality on the 0-9 scale from whatever checks the data supports (None if under 3 checks)."""
    checks: List[bool] = []
    roe, margin, de = _num(f, "roe_pct"), _num(f, "net_profit_margin_pct"), _num(f, "debt_to_equity")
    fcf, pg = _num(f, "fcf_yield_pct"), _num(f, "profit_cagr_5y")
    if roe is not None:
        checks.append(roe >= 12.0)
    if margin is not None:
        checks.append(margin >= 8.0)
    if de is not None and f.get("sector") != "Financial Services":      # leverage is the business model of a bank
        checks.append(de < 1.0)
    if fcf is not None:
        checks.append(fcf > 0)
    if pg is not None:
        checks.append(pg > 0)
    if f.get("years_consecutive_dividend") is not None:
        checks.append(f["years_consecutive_dividend"] >= 5)
    if _num(f, "max_drawdown_3y_pct") is not None:
        checks.append(f["max_drawdown_3y_pct"] > -45.0)
    return 9.0 * sum(checks) / len(checks) if len(checks) >= 3 else None


def score_stock(symbol: str, f: Dict, price: float, ttm_dps: Optional[float] = None,
                trend_pct: Optional[float] = None, cfg: ScoreConfig = ScoreConfig()) -> Dict:
    """Score one stock. Raises ValueError on a bad price."""
    if not price or price <= 0:
        raise ValueError(f"{symbol}: invalid price {price!r}")
    flags: List[str] = []
    static_price = _num(f, "current_price") or price
    ratio = price / static_price

    # Yield on the LIVE price. Without real dividend events, hold the profile's DPS constant.
    if ttm_dps is not None:
        yield_pct = ttm_dps / price * 100.0
    else:
        yield_pct = (_num(f, "dividend_yield_pct") or 0.0) * static_price / price
    div_cagr = _clamp(_num(f, "dividend_cagr_5y") or 0.0, 0.0, cfg.max_div_growth)
    fwd_yield = yield_pct * (1.0 + div_cagr / 100.0 * cfg.div_growth_credit)

    # Expected price growth: mean of the available estimates.
    payout, roe, pg = _num(f, "payout_ratio_pct"), _num(f, "roe_pct"), _num(f, "profit_cagr_5y")
    estimates = []
    if roe is not None and payout is not None:
        estimates.append(_clamp(roe * (1.0 - payout / 100.0), 0.0, cfg.max_sustainable_growth))
    if pg is not None:
        estimates.append(_clamp(pg * cfg.hist_growth_haircut, 0.0, cfg.max_hist_growth))
    if estimates:
        growth = sum(estimates) / len(estimates)
    else:
        growth = 0.0
        flags.append("no growth data")

    # Valuation. Live EPS gives a live PE; otherwise rescale a stored PE by the price ratio,
    # which is invalid after a split/bonus or with an old table: skip it, flag it, cut confidence.
    eps, pe_stored = _num(f, "eps_ttm"), _num(f, "pe_ratio")
    stale = False
    pe_live = None
    if eps and eps > 0:
        pe_live = price / eps
    elif pe_stored:
        if cfg.stale_band[0] <= ratio <= cfg.stale_band[1]:
            pe_live = pe_stored * ratio
        else:
            stale = True
            flags.append(f"fundamentals stale: live Rs{price:.0f} vs table Rs{static_price:.0f}")
    fair_pe = cfg.fair_pe_base + cfg.fair_pe_per_growth * growth
    val_adj = 0.0
    if pe_live and pe_live > 0:
        val_adj = _clamp((1.0 - pe_live / fair_pe) * cfg.valuation_sensitivity,
                         -cfg.max_valuation_drag, cfg.max_valuation_bonus)

    trend_adj = 0.0
    if trend_pct is not None:
        trend_adj = _clamp(trend_pct * cfg.trend_weight, -cfg.max_trend_drag, cfg.max_trend_bonus)
        if trend_pct < -25.0:
            flags.append(f"downtrend {trend_pct:.0f}% vs 200-day average: yield-trap risk")
    p3 = _num(f, "price_cagr_3y")
    price_hist_adj = _clamp(p3 * cfg.price_hist_weight, -cfg.max_price_hist, cfg.max_price_hist) if p3 is not None else 0.0

    expected_return = fwd_yield + growth + val_adj + trend_adj + price_hist_adj

    # Quality: real Piotroski if present, else a proxy from the checks the data supports.
    if payout is not None:
        sustainability = evaluate_dividend_sustainability(
            payout, _num(f, "fcf_yield_pct") if _num(f, "fcf_yield_pct") is not None else 4.0,
            _num(f, "debt_to_equity") if _num(f, "debt_to_equity") is not None else 0.5)
    else:
        sustainability = "Unknown"
    fscore = _num(f, "piotroski_f_score")
    if fscore is None:
        fscore = _proxy_piotroski(f)
        if fscore is None:
            fscore = 4.5
            flags.append("no quality data")
    quality_mult = (0.85 + 0.15 * _clamp(fscore, 0, 9) / 9.0) * SUSTAINABILITY_FACTOR.get(sustainability, 0.9)
    if stale:
        quality_mult *= cfg.stale_confidence

    key_fields = [pe_live is not None or pe_stored is not None, roe is not None, payout is not None,
                  pg is not None, _num(f, "debt_to_equity") is not None]
    completeness = sum(key_fields) / len(key_fields)
    if completeness < cfg.sparse_threshold:
        flags.append(f"sparse fundamentals ({sum(key_fields)}/{len(key_fields)} key fields)")
    quality_mult *= 0.85 + 0.15 * completeness

    efficiency = expected_return * quality_mult if expected_return > 0 else expected_return

    return {
        "symbol": symbol, "sector": f.get("sector") or "UNKNOWN", "price": round(price, 2),
        "dividend_yield_pct": round(yield_pct, 2), "fwd_yield_pct": round(fwd_yield, 2),
        "growth_pct": round(growth, 2), "valuation_adj_pct": round(val_adj, 2),
        "trend_pct": trend_pct, "trend_adj_pct": round(trend_adj, 2),
        "price_hist_adj_pct": round(price_hist_adj, 2), "flags": flags,
        "expected_return_pct": round(expected_return, 2), "sustainability": sustainability,
        "quality_mult": round(quality_mult, 3), "data_completeness": round(completeness, 2),
        "efficiency": round(efficiency, 2),
    }


def score_universe(fundamentals: Dict[str, Dict], prices: Dict[str, float],
                   ttm_dps: Optional[Dict[str, float]] = None,
                   trends: Optional[Dict[str, float]] = None,
                   cfg: ScoreConfig = ScoreConfig()) -> Dict[str, Dict]:
    """Scores every stock that has a usable live price; others are omitted, not guessed."""
    out = {}
    for sym, f in fundamentals.items():
        price = prices.get(sym)
        if not price or price <= 0:
            continue
        out[sym] = score_stock(sym, f, price, (ttm_dps or {}).get(sym), (trends or {}).get(sym), cfg)
    return out
