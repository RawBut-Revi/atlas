"""
Project Atlas - Full Fundamental Screening Run
================================================
Scores and screens all stocks in our fundamental database,
prints ranked recommendations by category, and then runs
a capital allocation plan based on user input.

Usage:
    python src/run_screener.py
    (You will be prompted to enter your investment capital and strategy.)
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data.fundamental_data import STOCK_FUNDAMENTALS
from scoring.capital_allocator import allocate_capital, projected_returns
from features.fundamental import (
    calculate_dividend_yield, calculate_peg_ratio,
    beats_inflation, real_return
)
from features.dividend import (
    evaluate_dividend_sustainability,
    analyze_dividend_history,
    is_attractive_dividend_play
)
from scoring.quality_score import (
    calculate_atlas_dividend_score
)

INFLATION_RATE = 6.5  # Indian avg inflation %


def score_stock(symbol: str, data: dict) -> dict:
    """Score a single stock across all our metrics."""

    div_history = analyze_dividend_history(data.get("dividend_history", []))

    atlas_score = calculate_atlas_dividend_score(
        years_consecutive_payout=data.get("years_consecutive_dividend", 0),
        dividend_cagr_5y=div_history.get("cagr_5y") or 0.0,
        payout_ratio=data.get("payout_ratio_pct", 100),
        fcf_yield=data.get("fcf_yield_pct", 0),
        roe=data.get("roe_pct", 0),
    )

    sustainability = evaluate_dividend_sustainability(
        payout_ratio=data.get("payout_ratio_pct", 100),
        fcf_yield=data.get("fcf_yield_pct", 0),
        debt_to_equity=data.get("debt_to_equity", 99),
    )

    f_score = data.get("piotroski_f_score", 0)
    div_yield = data.get("dividend_yield_pct", 0)
    profit_cagr = data.get("profit_cagr_5y", 0)
    div_cagr = div_history.get("cagr_5y") or 0.0

    # Total return estimate: dividend yield + expected price appreciation (proxy: profit CAGR)
    total_return_estimate = div_yield + profit_cagr
    real_ret = real_return(total_return_estimate, INFLATION_RATE)
    inflation_beaten = beats_inflation(total_return_estimate, INFLATION_RATE)

    # Categorize
    category = categorize_stock(data, atlas_score, f_score)

    return {
        "symbol": symbol,
        "sector": data.get("sector", "N/A"),
        "price": data.get("current_price", 0),
        "pe": data.get("pe_ratio", 0),
        "div_yield": div_yield,
        "div_cagr_5y": div_cagr,
        "roe": data.get("roe_pct", 0),
        "debt_to_equity": data.get("debt_to_equity", 0),
        "payout_ratio": data.get("payout_ratio_pct", 0),
        "fcf_yield": data.get("fcf_yield_pct", 0),
        "f_score": f_score,
        "atlas_score": round(atlas_score, 1),
        "sustainability": sustainability,
        "profit_cagr_5y": profit_cagr,
        "total_return_est": round(total_return_estimate, 1),
        "real_return": round(real_ret, 1),
        "beats_inflation": inflation_beaten,
        "category": category,
    }


def categorize_stock(data: dict, atlas_score: float, f_score: int) -> list:
    """Assign a stock to one or more investment categories."""
    categories = []

    roe = data.get("roe_pct", 0)
    de = data.get("debt_to_equity", 99)
    div_yield = data.get("dividend_yield_pct", 0)
    profit_cagr = data.get("profit_cagr_5y", 0)
    payout = data.get("payout_ratio_pct", 100)

    # --- Long Term Hold: Quality + Sustainability ---
    if roe >= 15 and de <= 1.0 and f_score >= 6 and atlas_score >= 55:
        categories.append("Long Term Hold")

    # --- Aggressive Dividend: High yield, safe payout ---
    if div_yield >= 4.0 and payout <= 80 and f_score >= 5:
        categories.append("Aggressive Dividend")

    # --- Hybrid: Quality + High Yield ---
    if ("Long Term Hold" in categories and div_yield >= 2.5 and profit_cagr >= 10):
        categories.append("Hybrid")

    if not categories:
        categories.append("Watchlist")

    return categories


def run_screener():
    print("=" * 70)
    print("  PROJECT ATLAS — Fundamental Stock Screener")
    print(f"  Inflation Hurdle Rate: {INFLATION_RATE}%")
    print("=" * 70)

    results = []
    for symbol, data in STOCK_FUNDAMENTALS.items():
        scored = score_stock(symbol, data)
        results.append(scored)

    # Sort by Atlas Dividend Score
    results.sort(key=lambda x: x["atlas_score"], reverse=True)

    # --- LONG TERM HOLDS ---
    print("\n🏆 LONG TERM HOLD (Quality Compounders)")
    print("-" * 70)
    lt_holds = [r for r in results if "Long Term Hold" in r["category"]]
    _print_table(lt_holds)

    # --- AGGRESSIVE DIVIDEND ---
    print("\n💰 AGGRESSIVE DIVIDEND (High Yield Focus)")
    print("-" * 70)
    agg_div = [r for r in results if "Aggressive Dividend" in r["category"]]
    _print_table(agg_div)

    # --- HYBRID ---
    print("\n🎯 HYBRID (Quality + Yield — Best of Both)")
    print("-" * 70)
    hybrid = [r for r in results if "Hybrid" in r["category"]]
    _print_table(hybrid)

    # --- INFLATION BEATERS SUMMARY ---
    print("\n📈 INFLATION BEATING CHECK (6.5% hurdle)")
    print("-" * 70)
    print(f"  {'Symbol':<14} {'Div Yield':>9} {'Profit CAGR':>12} {'Total Est.':>11} {'Real Return':>12} {'Beats?':>7}")
    print(f"  {'-'*14} {'-'*9} {'-'*12} {'-'*11} {'-'*12} {'-'*7}")
    for r in results:
        beat = "✅" if r["beats_inflation"] else "❌"
        print(f"  {r['symbol']:<14} {r['div_yield']:>8.1f}% {r['profit_cagr_5y']:>11.1f}% {r['total_return_est']:>10.1f}% {r['real_return']:>11.1f}% {beat:>7}")

    print("\n" + "=" * 70)
    print("  Screening complete!")
    print("=" * 70)

    # ========================================================
    # CAPITAL ALLOCATION SECTION
    # ========================================================
    print("\n" + "=" * 70)
    print("  CAPITAL ALLOCATION PLANNER")
    print("=" * 70)

    try:
        capital_input = input("\n  How much do you want to invest? (e.g. 10000): Rs ")
        total_capital = float(capital_input.replace(",", "").strip())
    except (ValueError, EOFError):
        print("  Invalid input. Skipping allocation.")
        return

    print("\n  Strategy Options:")
    print("    1. Long Term Hold   — Quality compounders, hold for years")
    print("    2. Aggressive Dividend — High yield focus (6%+ target)")
    print("    3. Hybrid           — 60% quality + 40% high yield (Recommended)")

    try:
        strat_input = input("\n  Choose strategy (1/2/3) [default: 3]: ").strip() or "3"
        strat_map = {"1": "long_term", "2": "aggressive_dividend", "3": "hybrid"}
        strategy = strat_map.get(strat_input, "hybrid")
    except EOFError:
        strategy = "hybrid"

    print(f"\n  Running allocation for Rs {total_capital:,.0f} with '{strategy}' strategy...\n")

    plan = allocate_capital(total_capital, results, strategy=strategy)
    _print_allocation(plan, total_capital)

    # Projected returns
    proj = projected_returns(plan["allocations"], years=5, growth_rate_pct=12.0)
    _print_projections(proj)


def _print_allocation(plan: dict, total_capital: float):
    """Pretty-print the allocation plan."""
    print("=" * 70)
    print(f"  PORTFOLIO ALLOCATION  |  Strategy: {plan.get('strategy','').upper()}")
    print("=" * 70)

    # Category breakdown (for hybrid)
    if "category_breakdown" in plan:
        print("\n  Category Split:")
        for cat, info in plan["category_breakdown"].items():
            print(f"    {cat:<35} Allocated: Rs {info['allocated']:>8,.0f}  |  Invested: Rs {info['invested']:>8,.0f}")

    print(f"\n  {'Symbol':<12} {'Wt%':>5} {'Price':>8} {'Shares':>7} {'Invested':>10} {'Div Yield':>10} {'Annual Div':>11} {'Note'}")
    print(f"  {'-'*12} {'-'*5} {'-'*8} {'-'*7} {'-'*10} {'-'*10} {'-'*11} {'-'*20}")

    for a in plan["allocations"]:
        note = a.get("note", "")
        print(
            f"  {a['symbol']:<12} {a['weight_pct']:>4.1f}%"
            f" {a['price']:>8,.0f}"
            f" {a['shares']:>7}"
            f"  Rs {a['amount_invested']:>8,.0f}"
            f"  {a['div_yield']:>7.1f}%"
            f"  Rs {a['annual_dividend_income']:>7,.0f}/yr"
            f"  {note}"
        )

    total_invested  = plan.get("total_invested", 0)
    leftover        = plan.get("leftover_cash", total_capital - total_invested)
    total_annual_div = sum(a["annual_dividend_income"] for a in plan["allocations"])

    print(f"\n  {'-'*70}")
    print(f"  Total Capital:    Rs {total_capital:>10,.0f}")
    print(f"  Total Invested:   Rs {total_invested:>10,.0f}")
    print(f"  Leftover Cash:    Rs {leftover:>10,.0f}  (not enough for 1 share of remaining)")
    print(f"  Annual Dividends: Rs {total_annual_div:>10,.0f}/yr")
    print(f"  Monthly Income:   Rs {total_annual_div/12:>10,.0f}/mo  (approx)")


def _print_projections(proj: dict):
    """Pretty-print the 5-year projection."""
    print(f"\n{'=' * 70}")
    print(f"  5-YEAR PORTFOLIO PROJECTION")
    print(f"{'=' * 70}")
    print(f"  Initial Investment:       Rs {proj['initial_investment']:>10,.0f}")
    print(f"  Blended Dividend Yield:       {proj['blended_div_yield_pct']:>7.2f}%")
    print(f"  Assumed Capital Growth:       {proj['assumed_capital_growth_pct']:>7.1f}%  (historical avg)")
    print(f"  Total Annual Return Est.:     {proj['total_annual_return_pct']:>7.1f}%")
    print(f"  --")
    print(f"  Projected Value (5Y):     Rs {proj['future_portfolio_value']:>10,.0f}")
    print(f"  Total Gain:               Rs {proj['total_gain']:>10,.0f}")
    print(f"  Return Multiple:              {proj['return_multiple']:>7.1f}x  your money")
    print(f"  Simple Dividend Income (5Y):  Rs {proj['cumulative_dividends_simple']:>8,.0f}")
    print(f"{'=' * 70}\n")


def _print_table(stocks: list):
    if not stocks:
        print("  No stocks matched this category.")
        return
    header = f"  {'Symbol':<13} {'Sector':<22} {'Yield':>6} {'ROE':>6} {'F':>3} {'Atlas':>6} {'Safety':<16} {'Inflation?':>10}"
    print(header)
    print(f"  {'-'*13} {'-'*22} {'-'*6} {'-'*6} {'-'*3} {'-'*6} {'-'*16} {'-'*10}")
    for s in stocks:
        beat = "✅ Beats" if s["beats_inflation"] else "❌ Below"
        print(
            f"  {s['symbol']:<13} {s['sector']:<22} {s['div_yield']:>5.1f}%"
            f" {s['roe']:>5.1f}% {s['f_score']:>3} {s['atlas_score']:>6.1f}"
            f" {s['sustainability']:<16} {beat:>10}"
        )


if __name__ == "__main__":
    run_screener()
