"""
Project Atlas - Capital Allocation Engine
==========================================
Determines how to split investment capital across screened stocks
using score-weighted portfolio construction.

Key Principles:
    1. Higher Atlas Score = Higher allocation weight
    2. No single stock gets more than a configurable max weight (diversification)
    3. Allocations are adjusted to whole shares (can't buy fractional shares in India)
    4. Leftover cash (due to rounding) is reported separately
    5. Category splits can be configured (e.g., 60% Long Term, 40% Dividend)
"""

import math
from typing import Dict, List, Any


# ============================================================
# ALLOCATION CONFIG
# ============================================================

DEFAULT_CONFIG = {
    # Maximum % any single stock can receive (prevents over-concentration)
    "max_single_stock_pct": 30.0,

    # Minimum % for any stock that passes screening (ensures meaningful positions)
    "min_single_stock_pct": 5.0,

    # Category split for Hybrid strategy (if user picks "hybrid")
    # These must sum to 100
    "category_splits": {
        "Long Term Hold":       60.0,
        "Aggressive Dividend":  40.0,
    },
}


# ============================================================
# CORE ALLOCATION FUNCTIONS
# ============================================================

def score_weighted_weights(stocks: List[Dict], max_pct: float = 30.0, min_pct: float = 5.0) -> Dict[str, float]:
    """
    Calculate allocation weights based on Atlas Dividend Score.
    
    Higher score → higher weight, capped at max_pct per stock.
    After capping, weights are re-normalized so they sum to 100%.
    
    Args:
        stocks: List of scored stock dicts (must have 'symbol' and 'atlas_score').
        max_pct: Maximum weight for any single stock (default 30%).
        min_pct: Minimum weight for any included stock (default 5%).
    
    Returns:
        Dict mapping symbol -> weight percentage (sums to 100.0).
    """
    if not stocks:
        return {}

    # Raw weights from Atlas score
    total_score = sum(s["atlas_score"] for s in stocks)
    if total_score == 0:
        # Fallback: equal weight
        equal = 100.0 / len(stocks)
        return {s["symbol"]: equal for s in stocks}

    raw_weights = {s["symbol"]: (s["atlas_score"] / total_score) * 100.0 for s in stocks}

    # Apply max cap — redistribute excess proportionally
    for _ in range(10):  # Iterate until stable
        capped = {sym: min(w, max_pct) for sym, w in raw_weights.items()}
        total_capped = sum(capped.values())
        uncapped_symbols = [sym for sym, w in raw_weights.items() if w < max_pct]

        if abs(total_capped - 100.0) < 0.01:
            raw_weights = capped
            break

        # Redistribute excess to uncapped symbols proportionally
        excess = 100.0 - total_capped
        if not uncapped_symbols:
            break
        uncapped_total = sum(capped[s] for s in uncapped_symbols)
        for sym in uncapped_symbols:
            if uncapped_total > 0:
                capped[sym] += excess * (capped[sym] / uncapped_total)
        raw_weights = capped

    # Apply minimum weight (boost small allocations)
    final_weights = {}
    for sym, w in raw_weights.items():
        final_weights[sym] = max(w, min_pct)

    # Re-normalize to 100%
    total = sum(final_weights.values())
    final_weights = {sym: (w / total) * 100.0 for sym, w in final_weights.items()}

    return final_weights


def allocate_capital(
    total_capital: float,
    stocks: List[Dict],
    strategy: str = "hybrid",
    config: Dict = None
) -> Dict:
    """
    Main capital allocation function.
    
    Args:
        total_capital: Total money to invest in INR (e.g., 10000.0).
        stocks: List of scored stock dicts from the screener.
        strategy: "long_term", "aggressive_dividend", or "hybrid"
        config: Optional override for DEFAULT_CONFIG parameters.
    
    Returns:
        Dict with full allocation plan including shares, amounts, and summary.
    """
    if config is None:
        config = DEFAULT_CONFIG

    max_pct = config.get("max_single_stock_pct", 30.0)
    min_pct = config.get("min_single_stock_pct", 5.0)
    cat_splits = config.get("category_splits", {})

    # Filter stocks by strategy
    if strategy == "long_term":
        selected = [s for s in stocks if "Long Term Hold" in s["category"]]
        capital_pool = {"Long Term Hold": total_capital}

    elif strategy == "aggressive_dividend":
        selected = [s for s in stocks if "Aggressive Dividend" in s["category"]]
        capital_pool = {"Aggressive Dividend": total_capital}

    elif strategy == "hybrid":
        # Split capital by category first
        lt_stocks   = [s for s in stocks if "Hybrid" in s["category"] or "Long Term Hold" in s["category"]]
        div_stocks  = [s for s in stocks if "Aggressive Dividend" in s["category"] and "Hybrid" not in s["category"]]

        lt_capital  = total_capital * (cat_splits.get("Long Term Hold", 60.0) / 100.0)
        div_capital = total_capital * (cat_splits.get("Aggressive Dividend", 40.0) / 100.0)

        lt_alloc  = _allocate_pool(lt_capital, lt_stocks, max_pct, min_pct)
        div_alloc = _allocate_pool(div_capital, div_stocks, max_pct, min_pct)

        # Merge and return hybrid result
        all_allocs = lt_alloc["allocations"] + div_alloc["allocations"]
        total_invested = sum(a["amount_invested"] for a in all_allocs)
        leftover = total_capital - total_invested

        return {
            "strategy": "Hybrid",
            "total_capital": total_capital,
            "total_invested": round(total_invested, 2),
            "leftover_cash": round(leftover, 2),
            "category_breakdown": {
                "Long Term Hold (60%)": {
                    "allocated": round(lt_capital, 2),
                    "invested": round(lt_alloc["total_invested"], 2),
                },
                "Aggressive Dividend (40%)": {
                    "allocated": round(div_capital, 2),
                    "invested": round(div_alloc["total_invested"], 2),
                }
            },
            "allocations": all_allocs,
        }
    else:
        selected = stocks
        capital_pool = {"All": total_capital}

    result = _allocate_pool(total_capital, selected, max_pct, min_pct)
    result["strategy"] = strategy
    result["total_capital"] = total_capital
    return result


def _allocate_pool(capital: float, stocks: List[Dict], max_pct: float, min_pct: float) -> Dict:
    """
    Allocate a capital pool across a list of stocks.
    Returns allocations with share counts.
    """
    if not stocks:
        return {"allocations": [], "total_invested": 0.0}

    weights = score_weighted_weights(stocks, max_pct, min_pct)
    allocations = []

    for stock in stocks:
        sym = stock["symbol"]
        weight_pct = weights.get(sym, 0)
        target_amount = capital * (weight_pct / 100.0)
        price = stock["price"]

        if price <= 0:
            continue

        # Calculate whole shares (India doesn't allow fractional shares)
        shares = math.floor(target_amount / price)

        if shares < 1:
            # Can't afford even 1 share — note this
            allocations.append({
                "symbol": sym,
                "sector": stock.get("sector", ""),
                "category": stock.get("category", []),
                "price": price,
                "weight_pct": round(weight_pct, 1),
                "target_amount": round(target_amount, 2),
                "shares": 0,
                "amount_invested": 0.0,
                "leftover_from_stock": round(target_amount, 2),
                "atlas_score": stock.get("atlas_score", 0),
                "div_yield": stock.get("div_yield", 0),
                "annual_dividend_income": 0.0,
                "note": f"⚠️  Need at least ₹{price:.0f} for 1 share",
            })
            continue

        amount_invested = shares * price
        leftover_from_stock = target_amount - amount_invested

        # Projected annual dividend income from this position
        div_yield_pct = stock.get("div_yield", 0)
        annual_dividend_income = amount_invested * (div_yield_pct / 100.0)

        allocations.append({
            "symbol": sym,
            "sector": stock.get("sector", ""),
            "category": stock.get("category", []),
            "price": price,
            "weight_pct": round(weight_pct, 1),
            "target_amount": round(target_amount, 2),
            "shares": shares,
            "amount_invested": round(amount_invested, 2),
            "leftover_from_stock": round(leftover_from_stock, 2),
            "atlas_score": stock.get("atlas_score", 0),
            "div_yield": div_yield_pct,
            "annual_dividend_income": round(annual_dividend_income, 2),
            "note": "",
        })

    # Sort by weight descending
    allocations.sort(key=lambda x: x["weight_pct"], reverse=True)
    total_invested = sum(a["amount_invested"] for a in allocations)

    return {
        "allocations": allocations,
        "total_invested": round(total_invested, 2),
    }


def projected_returns(allocations: List[Dict], years: int = 5, growth_rate_pct: float = 12.0) -> Dict:
    """
    Project the portfolio's future value assuming dividend reinvestment (DRIP)
    and capital appreciation.
    
    Args:
        allocations: List of allocation dicts from allocate_capital().
        years: Investment horizon in years.
        growth_rate_pct: Assumed annual capital appreciation % (default 12%).
    
    Returns:
        Dict with projected portfolio value and cumulative dividend income.
    """
    total_invested = sum(a["amount_invested"] for a in allocations)
    total_annual_div = sum(a["annual_dividend_income"] for a in allocations)
    blended_div_yield = (total_annual_div / total_invested * 100) if total_invested > 0 else 0

    # Total return = capital appreciation + dividend yield
    annual_total_return = (growth_rate_pct + blended_div_yield) / 100.0

    # Future value with compound growth
    future_value = total_invested * ((1 + annual_total_return) ** years)
    total_gain = future_value - total_invested
    cumulative_dividends = total_annual_div * years  # Simple estimate (non-compounded)

    return {
        "initial_investment": round(total_invested, 2),
        "blended_div_yield_pct": round(blended_div_yield, 2),
        "assumed_capital_growth_pct": growth_rate_pct,
        "total_annual_return_pct": round((growth_rate_pct + blended_div_yield), 2),
        "projected_years": years,
        "future_portfolio_value": round(future_value, 2),
        "total_gain": round(total_gain, 2),
        "cumulative_dividends_simple": round(cumulative_dividends, 2),
        "return_multiple": round(future_value / total_invested, 2),
    }
