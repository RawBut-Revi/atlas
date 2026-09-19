"""
Project Atlas - Total Return Score (dividend yield + capital appreciation)
==========================================================================
Ranks stocks for dividend reinvestment by EXPECTED ANNUAL TOTAL RETURN, quality-adjusted:

    expected_return = forward dividend yield + expected price growth + valuation adjustment
    efficiency      = expected_return x quality multiplier

Every constant lives in ScoreConfig. These are transparent heuristic priors, NOT a
backtested model: the growth haircuts and valuation sensitivity are assumptions to tune.

Inputs the fundamentals dict cannot supply live are passed in:
    price    - live last traded price (the static table is months old)
    ttm_dps  - trailing-12-month dividend per share from real ex-dividend events; when given,
               yield is ttm_dps / live price, so a price fall raises yield honestly.
    trend_pct - live price vs its 200-day average (%). Rewards capital appreciation and stops a
               collapsing stock ranking high purely because its yield spiked (yield trap).
"""
from dataclasses import dataclass
from typing import Dict, Optional

from features.dividend import evaluate_dividend_sustainability


@dataclass(frozen=True)
class ScoreConfig:
    hist_growth_haircut: float = 0.5        # 5y profit CAGR rarely repeats in full
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
    max_trend_drag: float = 4.0             # yield-trap guard: a collapsing price cannot rank on yield
    max_trend_bonus: float = 2.0
    stale_band: tuple = (0.6, 1.6)          # live/static price outside this: table is stale or split-hit
    stale_confidence: float = 0.85


SUSTAINABILITY_FACTOR = {
    "High Safety": 1.00, "Moderate Safety": 0.95, "Unknown": 0.90,
    "At Risk": 0.85, "Dangerous": 0.70,
}


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def score_stock(symbol: str, f: Dict, price: float, ttm_dps: Optional[float] = None,
                trend_pct: Optional[float] = None, cfg: ScoreConfig = ScoreConfig()) -> Dict:
    """Score one stock. `f` is an entry of STOCK_FUNDAMENTALS. Raises ValueError on a bad price."""
    if not price or price <= 0:
        raise ValueError(f"{symbol}: invalid price {price!r}")

    static_price = f.get("current_price") or price

    # Yield on the LIVE price. Without real dividend events, hold the static DPS constant.
    if ttm_dps is not None:
        yield_pct = ttm_dps / price * 100.0
    else:
        yield_pct = f.get("dividend_yield_pct", 0.0) * static_price / price

    div_cagr = _clamp(f.get("dividend_cagr_5y", 0.0), 0.0, cfg.max_div_growth)
    fwd_yield = yield_pct * (1.0 + div_cagr / 100.0 * cfg.div_growth_credit)

    # Expected price growth: blend of retained-earnings growth and haircut history.
    payout = f.get("payout_ratio_pct", 0.0)
    sustainable_g = _clamp(f.get("roe_pct", 0.0) * (1.0 - payout / 100.0), 0.0, cfg.max_sustainable_growth)
    hist_g = _clamp(f.get("profit_cagr_5y", 0.0) * cfg.hist_growth_haircut, 0.0, cfg.max_hist_growth)
    growth = 0.5 * sustainable_g + 0.5 * hist_g

    # Valuation: rescale the static PE to the live price (EPS held constant). If live and static
    # prices diverge sharply the table is stale or hit by a split/bonus, so a price-ratio rescale
    # would be wrong: skip the valuation term, flag it, and cut confidence.
    flags = []
    ratio = price / static_price
    stale = not (cfg.stale_band[0] <= ratio <= cfg.stale_band[1])
    pe_live = f.get("pe_ratio", 0.0) * ratio
    fair_pe = cfg.fair_pe_base + cfg.fair_pe_per_growth * growth
    val_adj = 0.0
    if stale:
        flags.append(f"fundamentals stale: live Rs{price:.0f} vs table Rs{static_price:.0f}")
    elif pe_live > 0 and fair_pe > 0:
        val_adj = _clamp((1.0 - pe_live / fair_pe) * cfg.valuation_sensitivity,
                         -cfg.max_valuation_drag, cfg.max_valuation_bonus)

    trend_adj = 0.0
    if trend_pct is not None:
        trend_adj = _clamp(trend_pct * cfg.trend_weight, -cfg.max_trend_drag, cfg.max_trend_bonus)
        if trend_pct < -25.0:
            flags.append(f"downtrend {trend_pct:.0f}% vs 200-day average: yield-trap risk")

    expected_return = fwd_yield + growth + val_adj + trend_adj

    sustainability = evaluate_dividend_sustainability(
        payout, f.get("fcf_yield_pct", 0.0), f.get("debt_to_equity", 0.0))
    piotroski = _clamp(f.get("piotroski_f_score", 5), 0, 9)
    quality_mult = (0.85 + 0.15 * piotroski / 9.0) * SUSTAINABILITY_FACTOR.get(sustainability, 0.9)
    if stale:
        quality_mult *= cfg.stale_confidence

    efficiency = expected_return * quality_mult if expected_return > 0 else expected_return

    return {
        "symbol": symbol, "sector": f.get("sector", "UNKNOWN"), "price": round(price, 2),
        "dividend_yield_pct": round(yield_pct, 2), "fwd_yield_pct": round(fwd_yield, 2),
        "growth_pct": round(growth, 2), "valuation_adj_pct": round(val_adj, 2),
        "trend_pct": trend_pct, "trend_adj_pct": round(trend_adj, 2), "flags": flags,
        "expected_return_pct": round(expected_return, 2), "sustainability": sustainability,
        "quality_mult": round(quality_mult, 3), "efficiency": round(efficiency, 2),
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
