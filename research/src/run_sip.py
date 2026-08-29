"""
Project Atlas - SIP Planner CLI
=================================
Interactive SIP planner that:
    1. Runs the fundamental screener
    2. Asks for monthly amount and strategy
    3. Shows this month's exact buy orders
    4. Simulates 12-month portfolio growth
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data.fundamental_data import STOCK_FUNDAMENTALS
from features.fundamental import beats_inflation, real_return
from features.dividend import evaluate_dividend_sustainability, analyze_dividend_history
from scoring.quality_score import calculate_atlas_dividend_score
from scoring.sip_engine import SIPPortfolio, simulate_sip

INFLATION_RATE = 6.5


def build_scored_basket() -> list:
    """Build the full scored stock list (same logic as run_screener.py)."""
    results = []
    for symbol, data in STOCK_FUNDAMENTALS.items():
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
        category = _categorize(data, atlas_score, data.get("piotroski_f_score", 0))

        results.append({
            "symbol": symbol,
            "sector": data.get("sector"),
            "price": data.get("current_price", 0),
            "pe": data.get("pe_ratio", 0),
            "div_yield": data.get("dividend_yield_pct", 0),
            "roe": data.get("roe_pct", 0),
            "debt_to_equity": data.get("debt_to_equity", 0),
            "payout_ratio": data.get("payout_ratio_pct", 0),
            "fcf_yield": data.get("fcf_yield_pct", 0),
            "f_score": data.get("piotroski_f_score", 0),
            "atlas_score": round(atlas_score, 1),
            "sustainability": sustainability,
            "profit_cagr_5y": data.get("profit_cagr_5y", 0),
            "category": category,
        })

    results.sort(key=lambda x: x["atlas_score"], reverse=True)
    return results


def _categorize(data, atlas_score, f_score):
    cats = []
    roe = data.get("roe_pct", 0)
    de = data.get("debt_to_equity", 99)
    div_yield = data.get("dividend_yield_pct", 0)
    profit_cagr = data.get("profit_cagr_5y", 0)
    payout = data.get("payout_ratio_pct", 100)

    if roe >= 15 and de <= 1.0 and f_score >= 6 and atlas_score >= 55:
        cats.append("Long Term Hold")
    if div_yield >= 4.0 and payout <= 80 and f_score >= 5:
        cats.append("Aggressive Dividend")
    if "Long Term Hold" in cats and div_yield >= 2.5 and profit_cagr >= 10:
        cats.append("Hybrid")
    if not cats:
        cats.append("Watchlist")
    return cats


def print_header():
    print("=" * 70)
    print("  PROJECT ATLAS — SIP (Systematic Investment Plan) Planner")
    print("=" * 70)


def get_strategy_basket(all_stocks, strategy):
    if strategy == "long_term":
        return [s for s in all_stocks if "Long Term Hold" in s["category"]]
    elif strategy == "aggressive_dividend":
        return [s for s in all_stocks if "Aggressive Dividend" in s["category"]]
    else:  # hybrid
        seen, basket = set(), []
        for s in all_stocks:
            if any(c in s["category"] for c in ["Hybrid", "Long Term Hold", "Aggressive Dividend"]):
                if s["symbol"] not in seen:
                    seen.add(s["symbol"])
                    basket.append(s)
        return basket


def print_basket(basket):
    print(f"\n  Your Stock Basket ({len(basket)} stocks):")
    print(f"  {'#':<3} {'Symbol':<13} {'Sector':<22} {'Price':>7} {'Yield':>7} {'Atlas':>7} {'Category'}")
    print(f"  {'-'*3} {'-'*13} {'-'*22} {'-'*7} {'-'*7} {'-'*7} {'-'*25}")
    for i, s in enumerate(basket, 1):
        cats = ", ".join(s["category"])
        print(f"  {i:<3} {s['symbol']:<13} {s['sector']:<22} {s['price']:>6,.0f} {s['div_yield']:>6.1f}%"
              f" {s['atlas_score']:>6.1f}  {cats}")


def print_monthly_plan(summary, basket):
    print(f"\n{'=' * 70}")
    print(f"  THIS MONTH'S BUY ORDERS  |  Budget: Rs {summary['contributed']:,.0f}")
    print(f"{'=' * 70}")
    print(f"  {'Symbol':<13} {'Action':<6} {'Shares':>7} {'Price':>8} {'Spent':>10} {'Reserve':>10} {'Note'}")
    print(f"  {'-'*13} {'-'*6} {'-'*7} {'-'*8} {'-'*10} {'-'*10} {'-'*25}")

    for t in summary["transactions"]:
        action_display = "BUY" if t["action"] == "BUY" else "SKIP"
        spent_display = f"Rs {t['spent']:>7,.0f}" if t["spent"] > 0 else "      -    "
        print(
            f"  {t['symbol']:<13} {action_display:<6} {t['shares']:>7}  "
            f"{t['price']:>7,.0f} {spent_display} "
            f"Rs {t['carry_forward']:>7,.0f}  {t['note'][:35]}"
        )

    print(f"\n  Spent this month:   Rs {summary['spent_this_month']:>9,.0f}")
    print(f"  Unspent (reserves): Rs {summary['total_cash_reserve']:>9,.0f}  <- Rolls into next month")
    print(f"  Portfolio Value:    Rs {summary['portfolio_value']:>9,.0f}")


def print_simulation(result, months):
    print(f"\n{'=' * 70}")
    print(f"  {months}-MONTH SIP SIMULATION  |  Rs {result['monthly_amount']:,.0f}/month")
    print(f"{'=' * 70}")

    # Month by month growth table (condensed)
    print(f"\n  {'Month':<10} {'Invested':>12} {'Port Value':>12} {'Gain/Loss':>12} {'Div/Yr':>10}")
    print(f"  {'-'*10} {'-'*12} {'-'*12} {'-'*12} {'-'*10}")

    div_yield_map = {}
    for hist in result["month_by_month"]:
        gain = hist["portfolio_value"] - hist["total_invested"]
        gain_str = f"+Rs {gain:,.0f}" if gain >= 0 else f"-Rs {abs(gain):,.0f}"
        annual_div = hist["portfolio_value"] * (result["annual_dividend_income"] / result["final_portfolio_value"]) if result["final_portfolio_value"] > 0 else 0
        print(f"  {hist['month']:<10} Rs {hist['total_invested']:>9,.0f} "
              f"Rs {hist['portfolio_value']:>9,.0f} {gain_str:>12}  Rs {annual_div:>6,.0f}/yr")

    print(f"\n{'=' * 70}")
    print(f"  FINAL SUMMARY AFTER {months} MONTHS")
    print(f"{'=' * 70}")
    print(f"  Total Contributed:       Rs {result['total_contributed']:>10,.0f}")
    print(f"  Total Actually Invested: Rs {result['total_invested']:>10,.0f}")
    print(f"  Cash Reserve (carry-fwd):Rs {result['total_cash_reserve']:>10,.0f}")
    print(f"  Final Portfolio Value:   Rs {result['final_portfolio_value']:>10,.0f}")
    print(f"  Unrealised Gain:         Rs {result['unrealised_gain']:>10,.0f}  ({result['total_return_pct']:.1f}%)")
    print(f"  --")
    print(f"  Annual Dividend Income:  Rs {result['annual_dividend_income']:>10,.0f}/yr")
    print(f"  Monthly Dividend Income: Rs {result['monthly_dividend_income']:>10,.0f}/mo")

    print(f"\n  Final Holdings:")
    print(f"  {'Symbol':<13} {'Shares':>8}")
    print(f"  {'-'*13} {'-'*8}")
    for sym, shares in result["final_holdings"].items():
        if shares > 0:
            print(f"  {sym:<13} {shares:>8}")

    total_ret = result["total_return_pct"]
    real_ret = real_return(total_ret / (result["months_simulated"] / 12), INFLATION_RATE)
    print(f"\n  Inflation check (6.5% hurdle): {'✅ Beating inflation!' if real_ret > 0 else '❌ Below inflation'}")
    print(f"{'=' * 70}\n")


def run_sip_planner():
    print_header()

    all_stocks = build_scored_basket()

    # Strategy selection
    print("\n  Strategy Options:")
    print("    1. Long Term Hold       — Quality compounders")
    print("    2. Aggressive Dividend  — High yield (6%+ target)")
    print("    3. Hybrid               — 60% quality + 40% yield (Recommended)")

    try:
        strat_input = input("\n  Choose strategy (1/2/3) [default: 3]: ").strip() or "3"
        strat_map = {"1": "long_term", "2": "aggressive_dividend", "3": "hybrid"}
        strategy = strat_map.get(strat_input, "hybrid")
    except EOFError:
        strategy = "hybrid"

    basket = get_strategy_basket(all_stocks, strategy)
    print_basket(basket)

    # Monthly amount
    try:
        amt_input = input(f"\n  Monthly SIP amount (Rs 8000-10000): Rs ").strip()
        monthly_amount = float(amt_input.replace(",", ""))
    except (ValueError, EOFError):
        monthly_amount = 9000.0
        print(f"  Using default: Rs {monthly_amount:,.0f}")

    # Months to simulate
    try:
        months_input = input(f"  Simulate for how many months? [default: 12]: ").strip() or "12"
        months = int(months_input)
    except (ValueError, EOFError):
        months = 12

    # Show Month 1 plan
    print(f"\n  Running SIP simulation for {months} months at Rs {monthly_amount:,.0f}/month...")
    result = simulate_sip(basket, monthly_amount, months=months, strategy=strategy)

    if "error" in result:
        print(f"  Error: {result['error']}")
        return

    # Print Month 1 in detail
    print_monthly_plan(result["month_by_month"][0], basket)

    # Print full simulation
    print_simulation(result, months)


if __name__ == "__main__":
    run_sip_planner()
