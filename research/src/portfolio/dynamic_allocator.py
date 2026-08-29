"""
Project Atlas - Dynamic Hedge Allocator
=========================================
Adjusts portfolio allocation between equities, gold, bonds,
and international assets based on live market conditions.

Philosophy:
    Markets go through four seasons:
    1. Growth   (rising economy, rising inflation)   → Equities win
    2. Inflation (high inflation, slowing growth)    → Gold/Commodities win
    3. Recession (falling economy, falling inflation)→ Bonds win
    4. Deflation (falling economy, falling inflation)→ Gold + Long bonds win

    A dynamic allocator reads market signals and tilts the portfolio
    toward the assets best suited for the CURRENT season.

Signals we use (India-specific):
    - NIFTY50 trend  : above/below 200-DMA → bull or bear regime
    - India VIX      : fear gauge → low (<15) calm, high (>25) stressed
    - G-Sec 10Y yield: rising → economy healthy; falling → risk-off
    - Repo rate trend: RBI hiking → tighten equity; cutting → buy equity
    - Gold trend     : Gold above 200-DMA → inflation/risk-off regime
"""

from typing import Dict, Any


# ============================================================
# ALLOCATION TEMPLATES
# ============================================================

# Base allocations for each market regime
# fmt: off
REGIME_ALLOCATIONS = {
    "bull_calm": {
        # Low VIX, NIFTY above 200-DMA, rising economy
        "equities":       60.0,
        "gold":           10.0,
        "bonds":          20.0,
        "international":  10.0,
        "description":    "Bull market, low volatility. Max equity exposure.",
    },
    "bull_volatile": {
        # Moderate VIX, NIFTY above 200-DMA but choppy
        "equities":       55.0,
        "gold":           12.0,
        "bonds":          23.0,
        "international":  10.0,
        "description":    "Bull market turning volatile. Slight defensive tilt.",
    },
    "transition": {
        # VIX elevated, NIFTY near 200-DMA, mixed signals
        "equities":       48.0,
        "gold":           17.0,
        "bonds":          25.0,
        "international":  10.0,
        "description":    "Transition zone. Balanced hedge.",
    },
    "bear_moderate": {
        # High VIX, NIFTY below 200-DMA, weakening economy
        "equities":       38.0,
        "gold":           22.0,
        "bonds":          30.0,
        "international":  10.0,
        "description":    "Bear market. Rotate to defensive assets.",
    },
    "bear_extreme": {
        # VIX > 30, crash/crisis conditions
        "equities":       28.0,
        "gold":           27.0,
        "bonds":          35.0,
        "international":  10.0,
        "description":    "Extreme stress / crash. Maximum protection mode.",
    },
}
# fmt: on


# ============================================================
# MARKET SIGNAL READER
# ============================================================

def read_market_signals(
    nifty_price: float,
    nifty_sma200: float,
    india_vix: float,
    gsec_10y_yield: float,
    repo_rate: float,
    gold_price: float,
    gold_sma200: float,
) -> Dict[str, Any]:
    """
    Read current market signals and return a signal summary.

    Args:
        nifty_price:    Current NIFTY50 index level.
        nifty_sma200:   NIFTY50 200-day moving average.
        india_vix:      India VIX value (fear gauge, typically 10-40).
        gsec_10y_yield: Current 10Y Government Securities yield (%).
        repo_rate:      RBI repo rate (%).
        gold_price:     MCX Gold price (INR/10g).
        gold_sma200:    Gold 200-day moving average.

    Returns:
        Dict with individual signal readings and combined regime.
    """
    signals = {}

    # --- Signal 1: NIFTY Trend ---
    nifty_pct_vs_sma = ((nifty_price - nifty_sma200) / nifty_sma200) * 100
    if nifty_price > nifty_sma200 * 1.05:
        signals["nifty_trend"] = ("bullish", f"NIFTY {nifty_pct_vs_sma:+.1f}% above 200-DMA")
    elif nifty_price > nifty_sma200:
        signals["nifty_trend"] = ("mildly_bullish", f"NIFTY {nifty_pct_vs_sma:+.1f}% above 200-DMA")
    elif nifty_price > nifty_sma200 * 0.95:
        signals["nifty_trend"] = ("neutral", f"NIFTY {nifty_pct_vs_sma:+.1f}% near 200-DMA")
    else:
        signals["nifty_trend"] = ("bearish", f"NIFTY {nifty_pct_vs_sma:+.1f}% below 200-DMA ⚠️")

    # --- Signal 2: India VIX (Fear Gauge) ---
    if india_vix < 13:
        signals["vix"] = ("very_low", f"VIX {india_vix:.1f} — Extreme complacency, be cautious")
    elif india_vix < 17:
        signals["vix"] = ("low", f"VIX {india_vix:.1f} — Calm markets")
    elif india_vix < 22:
        signals["vix"] = ("moderate", f"VIX {india_vix:.1f} — Moderate fear")
    elif india_vix < 28:
        signals["vix"] = ("high", f"VIX {india_vix:.1f} — High fear, defensive mode")
    else:
        signals["vix"] = ("extreme", f"VIX {india_vix:.1f} — Panic/crash conditions ❗")

    # --- Signal 3: Real Rate (G-Sec yield minus inflation) ---
    assumed_cpi = 5.5
    real_rate = gsec_10y_yield - assumed_cpi
    if real_rate > 1.5:
        signals["real_rate"] = ("tight", f"Real rate {real_rate:.2f}% — Bonds attractive")
    elif real_rate > 0:
        signals["real_rate"] = ("neutral", f"Real rate {real_rate:.2f}% — Neutral")
    else:
        signals["real_rate"] = ("loose", f"Real rate {real_rate:.2f}% — Negative real rates, Gold attractive")

    # --- Signal 4: Gold Trend ---
    gold_pct_vs_sma = ((gold_price - gold_sma200) / gold_sma200) * 100
    if gold_price > gold_sma200 * 1.03:
        signals["gold_trend"] = ("bullish", f"Gold {gold_pct_vs_sma:+.1f}% above 200-DMA — Inflation/risk-off regime")
    elif gold_price > gold_sma200:
        signals["gold_trend"] = ("neutral", f"Gold slightly above 200-DMA")
    else:
        signals["gold_trend"] = ("bearish", f"Gold {gold_pct_vs_sma:+.1f}% below 200-DMA — Risk-on regime")

    # --- Combine into regime ---
    regime = _determine_regime(signals, india_vix, nifty_price, nifty_sma200)
    signals["regime"] = regime

    return signals


def _determine_regime(signals: Dict, vix: float, nifty: float, sma200: float) -> str:
    """Combine individual signals into a single market regime label."""
    nifty_above = nifty > sma200
    vix_level = signals["vix"][0]

    if nifty_above and vix_level in ("very_low", "low"):
        return "bull_calm"
    elif nifty_above and vix_level == "moderate":
        return "bull_volatile"
    elif vix_level == "moderate" or (nifty > sma200 * 0.95):
        return "transition"
    elif vix_level == "high":
        return "bear_moderate"
    else:
        return "bear_extreme"


# ============================================================
# DYNAMIC ALLOCATOR
# ============================================================

def get_dynamic_allocation(
    nifty_price: float,
    nifty_sma200: float,
    india_vix: float,
    gsec_10y_yield: float = 6.85,
    repo_rate: float = 6.5,
    gold_price: float = 85000,
    gold_sma200: float = 80000,
) -> Dict[str, Any]:
    """
    Calculate the recommended asset allocation for the current market.

    Returns a full allocation plan with percentages, reasoning,
    and comparison vs base allocation.

    Args:
        nifty_price:    Current NIFTY50 level (e.g., 24500).
        nifty_sma200:   NIFTY50 200-DMA (e.g., 23000).
        india_vix:      Current VIX (e.g., 15.2).
        gsec_10y_yield: 10-Year G-Sec yield % (default 6.85).
        repo_rate:      RBI repo rate % (default 6.5).
        gold_price:     MCX Gold (default 85000 INR/10g).
        gold_sma200:    Gold 200-DMA (default 80000).

    Returns:
        Dict with allocation percentages, signals, and narrative.
    """
    signals = read_market_signals(
        nifty_price, nifty_sma200, india_vix,
        gsec_10y_yield, repo_rate,
        gold_price, gold_sma200,
    )
    regime = signals["regime"]
    allocation = REGIME_ALLOCATIONS[regime]

    # Build signal summary for display
    signal_summary = []
    for key in ["nifty_trend", "vix", "real_rate", "gold_trend"]:
        if key in signals:
            signal_summary.append(f"  • {key.replace('_', ' ').title()}: {signals[key][1]}")

    return {
        "regime": regime,
        "regime_description": allocation["description"],
        "allocation": {
            "equities_pct":      allocation["equities"],
            "gold_pct":          allocation["gold"],
            "bonds_pct":         allocation["bonds"],
            "international_pct": allocation["international"],
        },
        "signals": signal_summary,
        "vs_base": {
            "equities_delta":      allocation["equities"] - 55.0,   # vs neutral 55%
            "gold_delta":          allocation["gold"] - 15.0,
            "bonds_delta":         allocation["bonds"] - 20.0,
            "international_delta": allocation["international"] - 10.0,
        },
    }


def apply_allocation_to_capital(total_capital: float, allocation: Dict) -> Dict[str, float]:
    """
    Convert percentage allocations into rupee amounts.

    Args:
        total_capital: Total portfolio value in INR.
        allocation:    Output from get_dynamic_allocation()["allocation"].

    Returns:
        Dict with rupee amounts for each asset class.
    """
    alloc = allocation["allocation"]
    return {
        "equities_inr":      round(total_capital * alloc["equities_pct"] / 100, 2),
        "gold_inr":          round(total_capital * alloc["gold_pct"] / 100, 2),
        "bonds_inr":         round(total_capital * alloc["bonds_pct"] / 100, 2),
        "international_inr": round(total_capital * alloc["international_pct"] / 100, 2),
    }


# ============================================================
# REBALANCING TRIGGER
# ============================================================

def check_rebalance_needed(
    current_weights: Dict[str, float],
    target_allocation: Dict,
    threshold_pct: float = 5.0,
) -> Dict[str, Any]:
    """
    Check if the portfolio needs rebalancing based on drift from targets.

    Rebalancing is triggered when any asset class drifts more than
    threshold_pct from its target weight.

    Args:
        current_weights:    Actual weights dict (equities_pct, gold_pct, etc.).
        target_allocation:  Output from get_dynamic_allocation().
        threshold_pct:      Drift threshold to trigger rebalance (default 5%).

    Returns:
        Dict with rebalance needed flag and specific actions required.
    """
    target = target_allocation["allocation"]
    actions = []
    max_drift = 0.0

    mapping = {
        "equities_pct": "equities_pct",
        "gold_pct": "gold_pct",
        "bonds_pct": "bonds_pct",
        "international_pct": "international_pct",
    }

    for key, target_key in mapping.items():
        current = current_weights.get(key, 0)
        target_val = target.get(target_key, 0)
        drift = current - target_val
        max_drift = max(max_drift, abs(drift))

        if abs(drift) >= threshold_pct:
            action = "REDUCE" if drift > 0 else "INCREASE"
            asset = key.replace("_pct", "").replace("_", " ").title()
            actions.append({
                "asset": asset,
                "action": action,
                "current_pct": round(current, 1),
                "target_pct": round(target_val, 1),
                "drift_pct": round(drift, 1),
            })

    return {
        "rebalance_needed": len(actions) > 0,
        "max_drift_pct": round(max_drift, 1),
        "threshold_pct": threshold_pct,
        "actions": actions,
        "regime": target_allocation.get("regime", "unknown"),
    }
