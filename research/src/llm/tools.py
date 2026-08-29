"""
Project Atlas LLM Advisor — Tool Definitions
=============================================
Defines the Python functions that the LLM can call as "tools".

Each function here is:
1. Callable by the LLM when it decides it needs data
2. Backed by our real screener / SIP / allocation engine
3. Returns clean JSON-serialisable results for the LLM to interpret
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.fundamental_data import STOCK_FUNDAMENTALS
from features.dividend import evaluate_dividend_sustainability, analyze_dividend_history
from scoring.quality_score import calculate_atlas_dividend_score
from scoring.capital_allocator import allocate_capital, projected_returns
from scoring.sip_engine import simulate_sip

# Phase 4 imports
from data.commodity_data import get_latest_prices, get_commodity_returns, get_all_returns_summary
from data.bond_data import get_real_rate, get_yield_curve_signal, get_market_stress_level, get_macro_snapshot
from portfolio.dynamic_allocator import get_dynamic_allocation, apply_allocation_to_capital
from portfolio.risk_metrics import portfolio_scorecard, calculate_returns
from scoring.drip_engine import simulate_drip_portfolio, calculate_dividend_received
from analysis.mf_analyser import get_smart_money_signals, get_mf_confirmation_for_stock, get_fund_overlap

# Phase 5 — Penny Stocks
from screening.penny_screener import screen_penny_stocks, get_penny_stock_details, check_penny_exit, build_penny_portfolio


# ============================================================
# SHARED HELPER — build the scored basket (used by all tools)
# ============================================================

def _build_scored_basket() -> list:
    """Internal: build the full scored stock list from our fundamental database."""
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
            "div_yield": data.get("dividend_yield_pct", 0),
            "roe": data.get("roe_pct", 0),
            "debt_to_equity": data.get("debt_to_equity", 0),
            "payout_ratio": data.get("payout_ratio_pct", 0),
            "fcf_yield": data.get("fcf_yield_pct", 0),
            "f_score": data.get("piotroski_f_score", 0),
            "atlas_score": round(atlas_score, 1),
            "sustainability": sustainability,
            "profit_cagr_5y": data.get("profit_cagr_5y", 0),
            "dividend_cagr_5y": round(div_history.get("cagr_5y") or 0.0, 1),
            "pe_ratio": data.get("pe_ratio", 0),
            "category": category,
        })
    results.sort(key=lambda x: x["atlas_score"], reverse=True)
    return results


def _categorize(data, atlas_score, f_score):
    cats = []
    if (data.get("roe_pct", 0) >= 15 and data.get("debt_to_equity", 99) <= 1.0
            and f_score >= 6 and atlas_score >= 55):
        cats.append("Long Term Hold")
    if data.get("dividend_yield_pct", 0) >= 4.0 and data.get("payout_ratio_pct", 100) <= 80 and f_score >= 5:
        cats.append("Aggressive Dividend")
    if "Long Term Hold" in cats and data.get("dividend_yield_pct", 0) >= 2.5 and data.get("profit_cagr_5y", 0) >= 10:
        cats.append("Hybrid")
    if not cats:
        cats.append("Watchlist")
    return cats


# ============================================================
# TOOL 1: screen_stocks
# ============================================================

def screen_stocks(strategy: str = "hybrid") -> dict:
    """
    Screen and score all stocks in the Atlas database.

    Args:
        strategy: "long_term", "aggressive_dividend", or "hybrid"

    Returns:
        Dict with top picks for the strategy and their key metrics.
    """
    all_stocks = _build_scored_basket()

    if strategy == "long_term":
        picked = [s for s in all_stocks if "Long Term Hold" in s["category"]]
    elif strategy == "aggressive_dividend":
        picked = [s for s in all_stocks if "Aggressive Dividend" in s["category"]]
    else:
        seen, picked = set(), []
        for s in all_stocks:
            if any(c in s["category"] for c in ["Hybrid", "Long Term Hold", "Aggressive Dividend"]):
                if s["symbol"] not in seen:
                    seen.add(s["symbol"])
                    picked.append(s)

    return {
        "strategy": strategy,
        "total_stocks_screened": len(all_stocks),
        "stocks_selected": len(picked),
        "picks": [
            {
                "symbol": s["symbol"],
                "sector": s["sector"],
                "price": s["price"],
                "dividend_yield_pct": s["div_yield"],
                "roe_pct": s["roe"],
                "atlas_score": s["atlas_score"],
                "f_score": s["f_score"],
                "sustainability": s["sustainability"],
                "categories": s["category"],
            }
            for s in picked
        ],
    }


# ============================================================
# TOOL 2: allocate_capital
# ============================================================

def tool_allocate_capital(amount: float, strategy: str = "hybrid") -> dict:
    """
    Calculate how to split a lump-sum investment across screened stocks.

    Args:
        amount:   Total investment in INR (e.g., 50000)
        strategy: "long_term", "aggressive_dividend", or "hybrid"

    Returns:
        Allocation plan with shares, amounts, and projected dividend income.
    """
    all_stocks = _build_scored_basket()
    plan = allocate_capital(amount, all_stocks, strategy=strategy)
    proj = projected_returns(plan["allocations"], years=5, growth_rate_pct=12.0)

    buyable = [a for a in plan["allocations"] if a["shares"] > 0]
    skipped = [a for a in plan["allocations"] if a["shares"] == 0]

    return {
        "strategy": strategy,
        "total_capital": amount,
        "total_invested": plan.get("total_invested", 0),
        "leftover_cash": plan.get("leftover_cash", 0),
        "annual_dividend_income": sum(a["annual_dividend_income"] for a in plan["allocations"]),
        "monthly_dividend_income": sum(a["annual_dividend_income"] for a in plan["allocations"]) / 12,
        "allocations": [
            {
                "symbol": a["symbol"],
                "shares": a["shares"],
                "price": a["price"],
                "amount_invested": a["amount_invested"],
                "weight_pct": a["weight_pct"],
                "annual_dividend": a["annual_dividend_income"],
            }
            for a in buyable
        ],
        "skipped_stocks": [
            {"symbol": a["symbol"], "note": a.get("note", "")}
            for a in skipped
        ],
        "five_year_projection": {
            "projected_value": proj["future_portfolio_value"],
            "total_gain": proj["total_gain"],
            "return_multiple": proj["return_multiple"],
        }
    }


# ============================================================
# TOOL 3: plan_sip
# ============================================================

def tool_plan_sip(monthly_amount: float, months: int = 12, strategy: str = "hybrid") -> dict:
    """
    Simulate a monthly SIP plan for the given number of months.

    Args:
        monthly_amount: INR to invest every month.
        months:         Number of months to simulate (default 12).
        strategy:       "long_term", "aggressive_dividend", or "hybrid"

    Returns:
        SIP simulation results including final portfolio value and dividend income.
    """
    all_stocks = _build_scored_basket()

    if strategy == "long_term":
        basket = [s for s in all_stocks if "Long Term Hold" in s["category"]]
    elif strategy == "aggressive_dividend":
        basket = [s for s in all_stocks if "Aggressive Dividend" in s["category"]]
    else:
        seen, basket = set(), []
        for s in all_stocks:
            if any(c in s["category"] for c in ["Hybrid", "Long Term Hold", "Aggressive Dividend"]):
                if s["symbol"] not in seen:
                    seen.add(s["symbol"])
                    basket.append(s)

    result = simulate_sip(basket, monthly_amount, months=months, strategy=strategy)

    # Month-by-month summary (condensed)
    monthly_table = [
        {
            "month": h["month"],
            "total_invested": h["total_invested"],
            "portfolio_value": h["portfolio_value"],
        }
        for h in result.get("month_by_month", [])
    ]

    return {
        "strategy": strategy,
        "monthly_amount": monthly_amount,
        "months": months,
        "total_contributed": result["total_contributed"],
        "total_invested": result["total_invested"],
        "final_portfolio_value": result["final_portfolio_value"],
        "unrealised_gain": result["unrealised_gain"],
        "total_return_pct": result["total_return_pct"],
        "annual_dividend_income": result["annual_dividend_income"],
        "monthly_dividend_income": result["monthly_dividend_income"],
        "final_holdings": result["final_holdings"],
        "monthly_progress": monthly_table,
    }


# ============================================================
# TOOL 4: get_stock_details
# ============================================================

def get_stock_details(symbol: str) -> dict:
    """
    Get detailed fundamental data for a specific stock.

    Args:
        symbol: NSE stock symbol (e.g., "COALINDIA", "ITC")

    Returns:
        Full fundamental profile of the stock.
    """
    symbol = symbol.upper().strip()
    data = STOCK_FUNDAMENTALS.get(symbol)

    if not data:
        return {"error": f"Stock '{symbol}' not found in Atlas database. Available: {', '.join(STOCK_FUNDAMENTALS.keys())}"}

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

    return {
        "symbol": symbol,
        "sector": data.get("sector"),
        "current_price": data.get("current_price"),
        "market_cap_cr": data.get("market_cap_cr"),
        "valuation": {
            "pe_ratio": data.get("pe_ratio"),
            "pb_ratio": data.get("pb_ratio"),
            "dividend_yield_pct": data.get("dividend_yield_pct"),
        },
        "profitability": {
            "roe_pct": data.get("roe_pct"),
            "roce_pct": data.get("roce_pct"),
            "net_profit_margin_pct": data.get("net_profit_margin_pct"),
        },
        "financial_health": {
            "debt_to_equity": data.get("debt_to_equity"),
            "interest_coverage": data.get("interest_coverage"),
            "current_ratio": data.get("current_ratio"),
            "payout_ratio_pct": data.get("payout_ratio_pct"),
        },
        "growth": {
            "revenue_cagr_5y": data.get("revenue_cagr_5y"),
            "profit_cagr_5y": data.get("profit_cagr_5y"),
            "dividend_cagr_5y": round(div_history.get("cagr_5y") or 0.0, 1),
            "consecutive_div_years": data.get("years_consecutive_dividend"),
        },
        "scores": {
            "atlas_dividend_score": round(atlas_score, 1),
            "piotroski_f_score": data.get("piotroski_f_score"),
            "dividend_sustainability": sustainability,
        },
    }


# ============================================================
# TOOL 5: compare_stocks
# ============================================================

def compare_stocks(symbols: list) -> dict:
    """
    Side-by-side comparison of multiple stocks.

    Args:
        symbols: List of NSE symbols, e.g. ["COALINDIA", "ONGC", "ITC"]

    Returns:
        Comparative table of key metrics.
    """
    comparison = []
    for sym in symbols:
        detail = get_stock_details(sym)
        if "error" not in detail:
            comparison.append({
                "symbol": detail["symbol"],
                "price": detail["current_price"],
                "pe": detail["valuation"]["pe_ratio"],
                "div_yield_pct": detail["valuation"]["dividend_yield_pct"],
                "roe_pct": detail["profitability"]["roe_pct"],
                "debt_to_equity": detail["financial_health"]["debt_to_equity"],
                "atlas_score": detail["scores"]["atlas_dividend_score"],
                "f_score": detail["scores"]["piotroski_f_score"],
                "sustainability": detail["scores"]["dividend_sustainability"],
                "profit_cagr_5y": detail["growth"]["profit_cagr_5y"],
                "div_cagr_5y": detail["growth"]["dividend_cagr_5y"],
            })

    # Rank by atlas score
    comparison.sort(key=lambda x: x["atlas_score"], reverse=True)
    return {"compared": comparison, "winner": comparison[0]["symbol"] if comparison else None}


# ============================================================
# TOOL REGISTRY — maps tool names to functions
# ============================================================

TOOL_REGISTRY = {
    "screen_stocks": screen_stocks,
    "allocate_capital": tool_allocate_capital,
    "plan_sip": tool_plan_sip,
    "get_stock_details": get_stock_details,
    "compare_stocks": compare_stocks,
}

# Ollama-compatible tool schema definitions
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "screen_stocks",
            "description": "Screen and score Indian stocks from the Atlas database based on investment strategy. Use this when user asks which stocks to buy.",
            "parameters": {
                "type": "object",
                "properties": {
                    "strategy": {
                        "type": "string",
                        "enum": ["long_term", "aggressive_dividend", "hybrid"],
                        "description": "Investment strategy: long_term for quality compounders, aggressive_dividend for high yield, hybrid for balanced approach.",
                    }
                },
                "required": ["strategy"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "allocate_capital",
            "description": "Calculate how to split a one-time lump sum investment across screened stocks. Use when user has a fixed amount to invest now.",
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {
                        "type": "number",
                        "description": "Total investment amount in Indian Rupees (INR).",
                    },
                    "strategy": {
                        "type": "string",
                        "enum": ["long_term", "aggressive_dividend", "hybrid"],
                        "description": "Investment strategy to use.",
                    },
                },
                "required": ["amount", "strategy"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_sip",
            "description": "Simulate a monthly Systematic Investment Plan (SIP). Use when user wants to invest a fixed amount every month.",
            "parameters": {
                "type": "object",
                "properties": {
                    "monthly_amount": {
                        "type": "number",
                        "description": "Amount to invest each month in INR.",
                    },
                    "months": {
                        "type": "integer",
                        "description": "Number of months to simulate (default 12, max 60).",
                    },
                    "strategy": {
                        "type": "string",
                        "enum": ["long_term", "aggressive_dividend", "hybrid"],
                        "description": "Investment strategy to use.",
                    },
                },
                "required": ["monthly_amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_details",
            "description": "Get detailed fundamental analysis for a specific stock by its NSE symbol.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "NSE stock symbol, e.g. COALINDIA, ITC, TCS, INFY",
                    }
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_stocks",
            "description": "Compare multiple stocks side-by-side on key metrics.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbols": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of NSE symbols to compare, e.g. ['COALINDIA', 'ONGC', 'ITC']",
                    }
                },
                "required": ["symbols"],
            },
        },
    },
]


# ============================================================
# PHASE 4 TOOLS
# ============================================================

def market_health(nifty_price: float, nifty_sma200: float, india_vix: float) -> dict:
    """
    Get the current market health assessment including:
    - Recommended asset allocation (equities/gold/bonds/international)
    - Market regime (bull_calm, transition, bear_extreme etc.)
    - Macro indicators (real rates, yield curve, stress level)
    - Commodity prices and their hedge effectiveness

    Args:
        nifty_price:  Current NIFTY50 level.
        nifty_sma200: NIFTY50 200-day moving average.
        india_vix:    Current India VIX fear gauge value.
    """
    allocation = get_dynamic_allocation(
        nifty_price=nifty_price,
        nifty_sma200=nifty_sma200,
        india_vix=india_vix,
    )
    macro = get_macro_snapshot()
    commodities = get_all_returns_summary()
    stress = get_market_stress_level()
    yield_signal = get_yield_curve_signal()
    real_rate = get_real_rate()

    return {
        "market_regime": allocation["regime"],
        "regime_description": allocation["regime_description"],
        "recommended_allocation": allocation["allocation"],
        "signals": allocation["signals"],
        "macro": {
            "stress_level": stress,
            "yield_curve": yield_signal,
            "real_rate_pct": real_rate,
            "repo_rate_pct": macro.get("repo_rate"),
            "gsec_10y_yield_pct": macro.get("gsec_10y_yield"),
        },
        "commodities": commodities,
    }


def smart_money_signals(mode: str = "steady", min_funds: int = 3) -> dict:
    """
    Analyse what top Indian mutual funds are buying and selling.
    Reveals institutional conviction — 'smart money' patterns.

    Args:
        mode:      'steady' = 6-month averaged data (safe, confirmed positions).
                   'golden_pick' = latest month only (fresh, may be riskier).
        min_funds: Minimum number of funds that must hold a stock to qualify.
    """
    signals = get_smart_money_signals(min_funds=min_funds, mode=mode)
    return {
        "mode": mode,
        "description": "6-month confirmed positions" if mode == "steady" else "Latest month fresh picks",
        "consensus_stocks": signals,
        "accumulation_picks": [s for s in signals if s["accumulation"] == "ACCUMULATING"],
        "distribution_warnings": [s for s in signals if s["accumulation"] == "DISTRIBUTING"],
    }


def drip_simulation(monthly_amount: float, months: int = 36, strategy: str = "hybrid") -> dict:
    """
    Simulate a portfolio WITH dividend reinvestment (DRIP).
    Dividends are automatically reinvested into the highest Atlas-scored
    stock each quarter — compounding both capital and income over time.

    Args:
        monthly_amount: Monthly SIP amount in INR.
        months:         Number of months to simulate (default 36).
        strategy:       'long_term', 'aggressive_dividend', or 'hybrid'.
    """
    all_stocks = _build_scored_basket()

    if strategy == "long_term":
        basket = [s for s in all_stocks if "Long Term Hold" in s["category"]]
    elif strategy == "aggressive_dividend":
        basket = [s for s in all_stocks if "Aggressive Dividend" in s["category"]]
    else:
        seen, basket = set(), []
        for s in all_stocks:
            if any(c in s["category"] for c in ["Hybrid", "Long Term Hold", "Aggressive Dividend"]):
                if s["symbol"] not in seen:
                    seen.add(s["symbol"])
                    basket.append(s)

    # First run SIP to get initial holdings
    from scoring.sip_engine import simulate_sip
    sip_result = simulate_sip(basket, monthly_amount, months=6, strategy=strategy)
    initial_holdings = sip_result["final_holdings"]

    # Then simulate DRIP on top
    drip_result = simulate_drip_portfolio(
        basket, initial_holdings, months=months, drip_frequency="quarterly"
    )

    # Compare with non-DRIP
    no_drip_sip = simulate_sip(basket, monthly_amount, months=months, strategy=strategy)

    drip_advantage = drip_result["final_portfolio_value"] - no_drip_sip["final_portfolio_value"]

    return {
        "strategy": strategy,
        "months": months,
        "monthly_sip": monthly_amount,
        "with_drip": {
            "final_value": drip_result["final_portfolio_value"],
            "annual_dividend_income": drip_result["annual_dividend_income"],
            "monthly_dividend_income": drip_result["monthly_dividend_income"],
            "drip_transactions": drip_result["total_drip_transactions"],
        },
        "without_drip": {
            "final_value": no_drip_sip["final_portfolio_value"],
            "annual_dividend_income": no_drip_sip["annual_dividend_income"],
        },
        "drip_advantage_inr": round(drip_advantage, 2),
        "drip_advantage_pct": round(drip_advantage / max(no_drip_sip["final_portfolio_value"], 1) * 100, 1),
    }


def institutional_check(symbol: str) -> dict:
    """
    Check whether top mutual funds are backing a specific stock.
    Validates Atlas quant picks with institutional 'smart money' conviction.

    Args:
        symbol: NSE stock symbol (e.g. COALINDIA, ITC, TCS).
    """
    return get_mf_confirmation_for_stock(symbol.upper())


def portfolio_risk(portfolio_values: list, benchmark_values: list = None) -> dict:
    """
    Calculate risk metrics for a portfolio: Sharpe ratio, Sortino ratio,
    Max Drawdown, Alpha vs NIFTY, and Value at Risk.

    Args:
        portfolio_values:  Monthly portfolio values in INR (oldest to newest).
        benchmark_values:  Monthly NIFTY50 values (optional, defaults to 12% CAGR).
    """
    if not benchmark_values:
        # Default: assume 12% CAGR benchmark
        start = portfolio_values[0] if portfolio_values else 100000
        benchmark_values = [start * (1.12 ** (i / 12)) for i in range(len(portfolio_values))]

    return portfolio_scorecard(portfolio_values, benchmark_values)


# Extend the registries
TOOL_REGISTRY.update({
    "market_health":       market_health,
    "smart_money_signals": smart_money_signals,
    "drip_simulation":     drip_simulation,
    "institutional_check": institutional_check,
    "portfolio_risk":      portfolio_risk,
})

# Extend schemas
TOOL_SCHEMAS.extend([
    {
        "type": "function",
        "function": {
            "name": "market_health",
            "description": "Get current market health: regime, recommended asset allocation (equities/gold/bonds), macro signals, and commodity prices. Use when user asks about market conditions, hedging, or how to protect their portfolio.",
            "parameters": {
                "type": "object",
                "properties": {
                    "nifty_price":  {"type": "number", "description": "Current NIFTY50 level (e.g. 24500)"},
                    "nifty_sma200": {"type": "number", "description": "NIFTY50 200-day moving average (e.g. 23000)"},
                    "india_vix":    {"type": "number", "description": "India VIX fear gauge value (e.g. 15.2)"},
                },
                "required": ["nifty_price", "nifty_sma200", "india_vix"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "smart_money_signals",
            "description": "Show what top Indian mutual funds (Parag Parikh, Quant, Mirae, SBI Contra, Nippon) are buying and selling. Use for smart money confirmation or to find hidden institutional picks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["steady", "golden_pick"], "description": "steady=6-month confirmed, golden_pick=latest month only"},
                    "min_funds": {"type": "integer", "description": "Minimum funds holding a stock to show it (default 3)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "drip_simulation",
            "description": "Simulate a portfolio with dividend reinvestment (DRIP). Shows how reinvesting dividends into the best stock each quarter compounds wealth faster than taking cash. Compare with and without DRIP.",
            "parameters": {
                "type": "object",
                "properties": {
                    "monthly_amount": {"type": "number", "description": "Monthly SIP amount in INR"},
                    "months":         {"type": "integer", "description": "Months to simulate (default 36)"},
                    "strategy":       {"type": "string", "enum": ["long_term", "aggressive_dividend", "hybrid"]},
                },
                "required": ["monthly_amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "institutional_check",
            "description": "Check if top mutual funds are holding or accumulating a specific stock. Validates our quant picks with smart money conviction scores.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "NSE stock symbol, e.g. COALINDIA"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "portfolio_risk",
            "description": "Calculate portfolio risk metrics: Sharpe ratio, Sortino ratio, Max Drawdown, Alpha vs benchmark, and Value at Risk. Use when user asks how risky their portfolio is.",
            "parameters": {
                "type": "object",
                "properties": {
                    "portfolio_values": {"type": "array", "items": {"type": "number"}, "description": "Monthly portfolio values in INR, oldest first"},
                    "benchmark_values": {"type": "array", "items": {"type": "number"}, "description": "Monthly NIFTY50 levels for comparison (optional)"},
                },
                "required": ["portfolio_values"],
            },
        },
    },
])


# ============================================================
# PHASE 5 — PENNY STOCK TOOLS
# ============================================================

def tool_screen_penny_stocks(
    category: str = "all",
    strategy: str = "balanced",
    total_portfolio_value: float = 100000,
) -> dict:
    """
    Screen and rank Indian penny/small-cap stocks using the Atlas Penny Score.
    Returns top picks by category (growth_rocket, turnaround, hidden_gem).
    Automatically enforces 3% max per stock, 15% max total penny exposure.

    Args:
        category:              'all', 'growth_rocket', 'turnaround', or 'hidden_gem'
        strategy:              'aggressive' (rockets only), 'balanced' (mix), 'conservative' (gems only)
        total_portfolio_value: User's total portfolio size in INR for position sizing
    """
    result = screen_penny_stocks(category=category, total_portfolio_value=total_portfolio_value)
    portfolio = build_penny_portfolio(
        total_portfolio_value=total_portfolio_value,
        strategy=strategy,
        max_stocks=5,
    )
    return {
        "summary": {
            "screened": result["screened"],
            "passed": result["passed"],
            "rejected": result["rejected"],
        },
        "strong_buys": [
            {"symbol": s["symbol"], "score": s["penny_score"], "verdict": s["verdict"],
             "rev_cagr_2y": s["revenue_cagr_2y"], "known_for": s["known_for"],
             "price": s["price"], "promoter_trend": s["promoter_trend"]}
            for s in result["strong_buys"]
        ],
        "buys": [
            {"symbol": s["symbol"], "score": s["penny_score"], "verdict": s["verdict"],
             "rev_cagr_2y": s["revenue_cagr_2y"], "known_for": s["known_for"]}
            for s in result["buys"]
        ],
        "suggested_portfolio": portfolio,
        "risk_note": result.get("portfolio_limits", {}),
    }


def tool_penny_stock_details(symbol: str) -> dict:
    """
    Get full Atlas Penny Score analysis for a specific penny stock.
    Shows all 5 dimension scores, fundamentals, and verdict.

    Args:
        symbol: NSE stock symbol (e.g. CUPIDLTD, TRIDENT, MAITHANALL)
    """
    return get_penny_stock_details(symbol)


def tool_penny_exit_check(symbol: str, entry_price: float, current_price: float,
                          current_revenue_growth: float = None) -> dict:
    """
    Check if it's time to exit a penny stock position.
    Triggers: stop-loss (-30%), story break (revenue<15%), promoter selling, or profit booking (2x).

    Args:
        symbol:                 NSE symbol of the penny stock
        entry_price:            Price you bought at
        current_price:          Current market price
        current_revenue_growth: Latest quarterly revenue growth % (optional)
    """
    return check_penny_exit(symbol, entry_price, current_price, current_revenue_growth)


TOOL_REGISTRY.update({
    "screen_penny_stocks": tool_screen_penny_stocks,
    "penny_stock_details": tool_penny_stock_details,
    "penny_exit_check":    tool_penny_exit_check,
})

TOOL_SCHEMAS.extend([
    {
        "type": "function",
        "function": {
            "name": "screen_penny_stocks",
            "description": "Screen Indian penny/small-cap stocks for high-growth multi-bagger potential using the Atlas Penny Score. Finds growth rockets (2-3x revenue), turnaround stories, and hidden gems. Auto-enforces 3% position sizing rule.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category":              {"type": "string", "enum": ["all", "growth_rocket", "turnaround", "hidden_gem"]},
                    "strategy":              {"type": "string", "enum": ["aggressive", "balanced", "conservative"]},
                    "total_portfolio_value": {"type": "number", "description": "User's total portfolio value in INR for position sizing"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "penny_stock_details",
            "description": "Get full Atlas Penny Score breakdown for a specific penny stock — all 5 dimensions, fundamentals, and verdict.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "NSE symbol e.g. CUPIDLTD, TRIDENT, MAITHANALL"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "penny_exit_check",
            "description": "Check if it's time to exit a penny stock. Monitors stop-loss (-30%), revenue growth break, promoter selling, and profit booking at 2x.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol":                 {"type": "string"},
                    "entry_price":            {"type": "number", "description": "Your buy price"},
                    "current_price":          {"type": "number", "description": "Current market price"},
                    "current_revenue_growth": {"type": "number", "description": "Latest quarterly revenue growth % (optional)"},
                },
                "required": ["symbol", "entry_price", "current_price"],
            },
        },
    },
])


# ============================================================
# COMPREHENSIVE PLAN TOOL (fixes escalating SIP + DRIP + penny)
# ============================================================

def comprehensive_plan(
    yearly_sip_amounts: list,
    penny_total: float = 17000,
    strategy: str = "hybrid",
    inflation_rate_pct: float = 15.0,
) -> dict:
    """
    Generate a complete, multi-year investment plan with:
    - Step-up/escalating SIP (different amount each year)
    - DRIP (dividends reinvested quarterly into best stock)
    - Penny stock allocation (spread as installments)
    - Inflation comparison (does portfolio beat inflation?)
    - Quit-job analysis (how much passive income generated)

    Use this when the user asks for a comprehensive plan, step-up SIP,
    or wants to know if they can quit their job.

    Args:
        yearly_sip_amounts:  List of monthly SIP per year e.g. [10000, 15000, 22500]
        penny_total:         Total INR budgeted for penny stocks over entire period
        strategy:            'hybrid', 'long_term', or 'aggressive_dividend'
        inflation_rate_pct:  Inflation rate to beat (default 15%)
    """
    from scoring.sip_engine import simulate_escalating_sip
    from screening.penny_screener import build_penny_portfolio

    all_stocks = _build_scored_basket()

    result = simulate_escalating_sip(
        basket=all_stocks,
        yearly_amounts=yearly_sip_amounts,
        penny_total=penny_total,
        penny_installments=len(yearly_sip_amounts) * 2,  # every 6 months
        strategy=strategy,
        annual_price_growth_pct=12.0,
        inflation_rate_pct=inflation_rate_pct,
        drip_enabled=True,
    )

    # Add penny stock portfolio suggestion
    total_portfolio = result["final_portfolio"]["equity_value"]
    penny_port = build_penny_portfolio(
        total_portfolio_value=total_portfolio,
        penny_budget=penny_total,
        max_stocks=5,
        strategy="balanced",
    )

    result["penny_portfolio"] = {
        "total_budget": penny_total,
        "allocations": penny_port.get("allocations", []),
        "risk_warning": penny_port.get("risk_warning", ""),
    }

    return result


TOOL_REGISTRY["comprehensive_plan"] = comprehensive_plan

TOOL_SCHEMAS.append({
    "type": "function",
    "function": {
        "name": "comprehensive_plan",
        "description": (
            "Generate a COMPLETE multi-year investment plan with step-up/escalating SIP, "
            "DRIP dividend reinvestment, penny stock allocation, inflation comparison, and "
            "quit-job passive income analysis. Use this for any question about a full 3-year "
            "or multi-year plan, especially when monthly amounts increase each year."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "yearly_sip_amounts": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Monthly SIP amount for each year, e.g. [10000, 15000, 22500] means Year1=10k/mo, Year2=15k/mo, Year3=22.5k/mo",
                },
                "penny_total": {
                    "type": "number",
                    "description": "Total INR budgeted for penny stocks across the entire period (e.g. 17000)",
                },
                "strategy": {
                    "type": "string",
                    "enum": ["hybrid", "long_term", "aggressive_dividend"],
                    "description": "Investment strategy for quality stocks (default hybrid)",
                },
                "inflation_rate_pct": {
                    "type": "number",
                    "description": "Annual inflation rate to beat (default 15)",
                },
            },
            "required": ["yearly_sip_amounts"],
        },
    },
})

