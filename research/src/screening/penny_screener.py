"""
Project Atlas - Penny Stock Screener
======================================
Unified screener that runs all penny stocks through the Atlas Penny Score
and returns categorized, ranked results with position sizing.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.penny_stock_data import PENNY_STOCKS
from scoring.penny_score import calculate_penny_score, calculate_penny_allocation, check_exit_signals
from typing import Dict, List, Optional


def screen_penny_stocks(
    category: str = "all",
    min_score: float = 48.0,
    total_portfolio_value: float = 100000,
) -> Dict:
    """
    Screen all penny stocks and return ranked results.

    Args:
        category:              "all", "growth_rocket", "turnaround", "hidden_gem"
        min_score:             Minimum Atlas Penny Score to include (default 48)
        total_portfolio_value: For position sizing calculations

    Returns:
        Ranked list of penny stock picks with scores, sizing, and key metrics.
    """
    results = []
    rejected = []

    for symbol, data in PENNY_STOCKS.items():
        # Filter by category
        if category != "all" and data.get("category") != category:
            continue

        data_with_symbol = {**data, "symbol": symbol}
        score_result = calculate_penny_score(data_with_symbol)

        if not score_result["passed_filters"]:
            rejected.append({
                "symbol": symbol,
                "reason": score_result["reject_reason"],
            })
            continue

        if score_result["total_score"] < min_score:
            continue

        allocation = calculate_penny_allocation(
            score_result["total_score"],
            total_portfolio_value,
        )

        results.append({
            "symbol": symbol,
            "category": data["category"],
            "price": data["price"],
            "market_cap_cr": data["market_cap_cr"],
            "sector": data["sector"],
            "penny_score": score_result["total_score"],
            "verdict": score_result["verdict"],
            "dimension_scores": score_result["dimension_scores"],
            # Key metrics
            "revenue_cagr_2y": data["revenue_cagr_2y"],
            "revenue_cagr_3y": data["revenue_cagr_3y"],
            "was_turnaround": data["was_loss_making"],
            "promoter_holding": data["promoter_holding"],
            "promoter_trend": data["promoter_trend"],
            "pledged_pct": data["pledged_pct"],
            "debt_to_equity": data["debt_to_equity"],
            "roe": data["roe"],
            "pe_ratio": data["pe_ratio"],
            "pli_beneficiary": data["pli_beneficiary"],
            "sector_tailwind": data["sector_tailwind"],
            "known_for": data["known_for"],
            "key_risks": data["key_risks"],
            # Position sizing (auto-enforced)
            "suggested_allocation_pct": allocation["suggested_allocation_pct"],
            "suggested_allocation_inr": allocation["suggested_allocation_inr"],
        })

    # Sort by penny score (highest first)
    results.sort(key=lambda x: x["penny_score"], reverse=True)

    # Summary
    strong_buys = [r for r in results if r["penny_score"] >= 78]
    buys = [r for r in results if 62 <= r["penny_score"] < 78]
    watchlist = [r for r in results if 48 <= r["penny_score"] < 62]

    total_penny_inr = sum(r["suggested_allocation_inr"] for r in results[:5])  # top 5
    max_penny_allowed = total_portfolio_value * 0.15

    return {
        "category_filter": category,
        "min_score_filter": min_score,
        "portfolio_value": total_portfolio_value,
        "screened": len(PENNY_STOCKS),
        "passed": len(results),
        "rejected": len(rejected),
        "strong_buys": strong_buys,
        "buys": buys,
        "watchlist": watchlist,
        "all_picks": results,
        "rejected_stocks": rejected,
        "portfolio_limits": {
            "max_penny_allocation_inr": round(max_penny_allowed, 2),
            "max_per_stock_inr": round(total_portfolio_value * 0.03, 2),
            "top5_suggested_total": round(total_penny_inr, 2),
            "within_limit": total_penny_inr <= max_penny_allowed,
        },
    }


def get_penny_stock_details(symbol: str) -> Dict:
    """
    Full analysis of a single penny stock.
    """
    symbol = symbol.upper()
    data = PENNY_STOCKS.get(symbol)

    if not data:
        return {
            "error": f"'{symbol}' not in Atlas penny database. Available: {', '.join(PENNY_STOCKS.keys())}"
        }

    data_with_symbol = {**data, "symbol": symbol}
    score_result = calculate_penny_score(data_with_symbol)

    return {
        "symbol": symbol,
        "category": data["category"],
        "known_for": data["known_for"],
        "sector": data["sector"],
        "sector_tailwind": data["sector_tailwind"],
        "key_risks": data["key_risks"],
        "price": data["price"],
        "market_cap_cr": data["market_cap_cr"],
        "penny_score": score_result["total_score"],
        "verdict": score_result["verdict"],
        "dimension_scores": score_result.get("dimension_scores", {}),
        "passed_filters": score_result["passed_filters"],
        "reject_reason": score_result.get("reject_reason"),
        "fundamentals": {
            "revenue_cagr_2y_pct":  data["revenue_cagr_2y"],
            "revenue_cagr_3y_pct":  data["revenue_cagr_3y"],
            "profit_cagr_2y_pct":   data.get("profit_cagr_2y"),
            "was_loss_making":      data["was_loss_making"],
            "roe_pct":              data["roe"],
            "pe_ratio":             data["pe_ratio"],
            "debt_to_equity":       data["debt_to_equity"],
            "interest_coverage":    data["interest_coverage"],
            "promoter_holding_pct": data["promoter_holding"],
            "promoter_trend":       data["promoter_trend"],
            "pledged_pct":          data["pledged_pct"],
            "pli_beneficiary":      data["pli_beneficiary"],
        },
    }


def check_penny_exit(
    symbol: str,
    entry_price: float,
    current_price: float,
    current_revenue_growth: Optional[float] = None,
) -> Dict:
    """
    Check if it's time to exit a penny stock position.

    Triggers:
    - Stop-loss at -30% from entry
    - Revenue growth drops below 15%
    - Promoter starts selling
    - Double your money → book 50%
    """
    symbol = symbol.upper()
    data = PENNY_STOCKS.get(symbol, {})
    data_with_symbol = {**data, "symbol": symbol}

    return check_exit_signals(
        data_with_symbol, entry_price, current_price, current_revenue_growth
    )


def build_penny_portfolio(
    total_portfolio_value: float,
    penny_budget: float = None,
    max_stocks: int = 5,
    strategy: str = "balanced",  # "aggressive", "balanced", "conservative"
) -> Dict:
    """
    Build a complete penny stock sub-portfolio.

    Args:
        total_portfolio_value: Full portfolio size in INR
        penny_budget:          Budget for penny stocks (default: 15% of portfolio)
        max_stocks:            Maximum number of stocks (default 5)
        strategy:              "aggressive" = growth_rockets only,
                               "balanced"   = mix of all categories,
                               "conservative" = hidden_gems only

    Returns:
        Complete penny portfolio with position sizes and risk breakdown.
    """
    if penny_budget is None:
        penny_budget = total_portfolio_value * 0.15  # 15% default

    # Select category filter based on strategy
    if strategy == "aggressive":
        category = "growth_rocket"
    elif strategy == "conservative":
        category = "hidden_gem"
    else:
        category = "all"

    screen = screen_penny_stocks(category=category, min_score=48, total_portfolio_value=total_portfolio_value)
    picks = screen["all_picks"][:max_stocks]

    if not picks:
        return {"error": "No stocks passed the screening criteria."}

    # Distribute budget proportionally by score
    total_score = sum(p["penny_score"] for p in picks)
    per_stock_hard_cap = total_portfolio_value * 0.03

    allocations = []
    total_allocated = 0.0

    for p in picks:
        weight = p["penny_score"] / total_score
        raw_alloc = penny_budget * weight
        alloc = min(raw_alloc, per_stock_hard_cap)
        shares = int(alloc / p["price"])
        actual_spend = shares * p["price"]
        total_allocated += actual_spend

        allocations.append({
            "symbol": p["symbol"],
            "category": p["category"],
            "penny_score": p["penny_score"],
            "verdict": p["verdict"],
            "price": p["price"],
            "shares": shares,
            "amount_inr": round(actual_spend, 2),
            "portfolio_pct": round(actual_spend / total_portfolio_value * 100, 2),
            "sector": p["sector"],
            "known_for": p["known_for"],
            "revenue_cagr_2y": p["revenue_cagr_2y"],
            "promoter_trend": p["promoter_trend"],
        })

    leftover = round(penny_budget - total_allocated, 2)

    return {
        "strategy": strategy,
        "total_portfolio_value": total_portfolio_value,
        "penny_budget": round(penny_budget, 2),
        "penny_budget_pct": round(penny_budget / total_portfolio_value * 100, 1),
        "total_allocated": round(total_allocated, 2),
        "leftover_cash": leftover,
        "stocks_selected": len(allocations),
        "allocations": allocations,
        "risk_warning": (
            "⚠️  Penny stocks are HIGH RISK. Max 3% per stock. "
            "Stop-loss at -30%. Book 50% profit when 2x. "
            "Exit immediately if promoter starts selling."
        ),
    }
