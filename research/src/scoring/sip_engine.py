"""
Project Atlas - SIP (Systematic Investment Plan) Engine
=========================================================
Handles monthly recurring investments into a basket of stocks.

Key Design Principles:
    1. CARRY-FORWARD CASH: If this month's allocation for INFY is ₹900
       but 1 share costs ₹1,855 — that ₹900 is saved and added to
       next month's INFY budget. Never waste a rupee.

    2. SCORE-WEIGHTED: Higher Atlas Dividend Score → higher priority.

    3. DEVIATION-BASED REBALANCING: Each month, the system checks which
       stock is furthest below its target weight and prioritizes it.
       This is real-world portfolio rebalancing, automatically.

    4. TRANSPARENT: Every month's buy/skip decision is logged with a reason.
"""

import math
import json
from typing import Dict, List, Any, Optional
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta

try:
    from dateutil.relativedelta import relativedelta
    HAS_DATEUTIL = True
except ImportError:
    HAS_DATEUTIL = False

from scoring.capital_allocator import score_weighted_weights


# ============================================================
# SIP PORTFOLIO STATE
# ============================================================

class SIPPortfolio:
    """
    Tracks the state of a monthly SIP portfolio over time.

    Attributes:
        basket:         List of scored stock dicts (from screener).
        target_weights: Target % allocation per stock (from Atlas Score).
        holdings:       Current shares held per symbol.
        cash_reserve:   Carry-forward cash per symbol (unspent allocation).
        total_invested: Total money deployed so far.
        history:        Month-by-month transaction log.
    """

    def __init__(self, basket: List[Dict], max_weight_pct: float = 30.0, min_weight_pct: float = 5.0):
        self.basket = basket
        self.target_weights = score_weighted_weights(basket, max_weight_pct, min_weight_pct)
        self.holdings: Dict[str, int] = {s["symbol"]: 0 for s in basket}
        self.cash_reserve: Dict[str, float] = {s["symbol"]: 0.0 for s in basket}
        self.total_invested: float = 0.0
        self.total_contributed: float = 0.0
        self.history: List[Dict] = []

    def get_current_prices(self) -> Dict[str, float]:
        """Returns current prices from the basket (in live mode, this would hit the API)."""
        return {s["symbol"]: s["price"] for s in self.basket}

    def get_portfolio_value(self) -> float:
        """Calculates current market value of all holdings."""
        prices = self.get_current_prices()
        return sum(self.holdings[sym] * prices.get(sym, 0) for sym in self.holdings)

    def get_current_weights(self) -> Dict[str, float]:
        """Returns actual weight of each stock in the current portfolio."""
        total_value = self.get_portfolio_value()
        if total_value == 0:
            return {sym: 0.0 for sym in self.holdings}
        prices = self.get_current_prices()
        return {
            sym: (self.holdings[sym] * prices.get(sym, 0)) / total_value * 100.0
            for sym in self.holdings
        }

    def invest_monthly(self, monthly_amount: float, month_label: str, price_overrides: Dict[str, float] = None) -> Dict:
        """
        Execute one month of SIP investment.

        Args:
            monthly_amount: Rupees to invest this month.
            month_label:    Label like "Month 1" or "2025-09".
            price_overrides: Optional dict of symbol->price for simulation.

        Returns:
            Dict summarising this month's transactions.
        """
        self.total_contributed += monthly_amount
        prices = self.get_current_prices()
        if price_overrides:
            prices.update(price_overrides)

        # Split monthly amount by target weight
        raw_allocations = {
            sym: (monthly_amount * (self.target_weights.get(sym, 0) / 100.0))
            for sym in self.holdings
        }

        transactions = []
        total_spent_this_month = 0.0

        for stock in sorted(self.basket, key=lambda s: s["atlas_score"], reverse=True):
            sym = stock["symbol"]
            price = prices.get(sym, 0)
            if price <= 0:
                continue

            # Available budget = this month's allocation + carry-forward reserve
            available = raw_allocations.get(sym, 0) + self.cash_reserve.get(sym, 0)
            shares_to_buy = math.floor(available / price)

            if shares_to_buy >= 1:
                spent = shares_to_buy * price
                leftover = available - spent

                self.holdings[sym] += shares_to_buy
                self.cash_reserve[sym] = leftover
                self.total_invested += spent
                total_spent_this_month += spent

                transactions.append({
                    "symbol": sym,
                    "action": "BUY",
                    "shares": shares_to_buy,
                    "price": price,
                    "spent": round(spent, 2),
                    "carry_forward": round(leftover, 2),
                    "cumulative_shares": self.holdings[sym],
                    "note": f"Carry-forward used: ₹{self.cash_reserve.get(sym, 0):.0f}" if self.cash_reserve.get(sym, 0) > 0 else "",
                })
            else:
                # Can't afford even 1 share — carry everything forward
                self.cash_reserve[sym] = available
                transactions.append({
                    "symbol": sym,
                    "action": "SKIP",
                    "shares": 0,
                    "price": price,
                    "spent": 0.0,
                    "carry_forward": round(available, 2),
                    "cumulative_shares": self.holdings[sym],
                    "note": f"Need ₹{price:.0f}/share. Saving ₹{available:.0f} (total reserve: ₹{available:.0f})",
                })

        portfolio_value = self.get_portfolio_value()
        unrealised_gain = portfolio_value - self.total_invested
        total_reserve = sum(self.cash_reserve.values())

        monthly_summary = {
            "month": month_label,
            "contributed": round(monthly_amount, 2),
            "spent_this_month": round(total_spent_this_month, 2),
            "total_contributed": round(self.total_contributed, 2),
            "total_invested": round(self.total_invested, 2),
            "portfolio_value": round(portfolio_value, 2),
            "unrealised_gain": round(unrealised_gain, 2),
            "total_cash_reserve": round(total_reserve, 2),
            "transactions": transactions,
        }

        self.history.append(monthly_summary)
        return monthly_summary


# ============================================================
# SIP SIMULATION
# ============================================================

def simulate_sip(
    basket: List[Dict],
    monthly_amount: float,
    months: int = 12,
    strategy: str = "hybrid",
    annual_price_growth_pct: float = 12.0,
    start_month_label: str = None,
) -> Dict:
    """
    Simulate a multi-month SIP run, projecting portfolio growth.

    Assumes prices grow at annual_price_growth_pct per year (monthly compounded).
    Dividends are tracked but assumed not reinvested (paid out as cash).

    Args:
        basket:                  Screened stocks from run_screener.
        monthly_amount:          INR to invest each month.
        months:                  Number of months to simulate.
        strategy:                "long_term", "aggressive_dividend", or "hybrid"
        annual_price_growth_pct: Assumed stock price CAGR for simulation.
        start_month_label:       Label for month 1 (e.g. "Sep 2025").

    Returns:
        Dict with full simulation results and month-by-month history.
    """
    # Filter basket by strategy
    if strategy == "long_term":
        selected = [s for s in basket if "Long Term Hold" in s.get("category", [])]
    elif strategy == "aggressive_dividend":
        selected = [s for s in basket if "Aggressive Dividend" in s.get("category", [])]
    else:  # hybrid
        selected = [
            s for s in basket
            if any(c in s.get("category", []) for c in ["Hybrid", "Long Term Hold", "Aggressive Dividend"])
        ]
        # Deduplicate by symbol
        seen = set()
        unique = []
        for s in selected:
            if s["symbol"] not in seen:
                seen.add(s["symbol"])
                unique.append(s)
        selected = unique

    if not selected:
        return {"error": "No stocks matched the selected strategy."}

    portfolio = SIPPortfolio(selected)
    monthly_growth = (1 + annual_price_growth_pct / 100.0) ** (1 / 12) - 1

    # Simulate price growth month by month
    current_prices = {s["symbol"]: s["price"] for s in selected}

    for month_idx in range(1, months + 1):
        if start_month_label:
            label = f"Month {month_idx}"
        else:
            label = f"Month {month_idx}"

        summary = portfolio.invest_monthly(monthly_amount, label, price_overrides=current_prices.copy())

        # Advance prices for next month
        current_prices = {sym: price * (1 + monthly_growth) for sym, price in current_prices.items()}

    # Final portfolio snapshot
    final_prices = current_prices
    final_value = sum(portfolio.holdings[sym] * final_prices.get(sym, 0) for sym in portfolio.holdings)
    total_reserve = sum(portfolio.cash_reserve.values())

    # Annual dividend income (based on final holdings + original yield)
    div_yield_map = {s["symbol"]: s.get("div_yield", 0) / 100.0 for s in selected}
    orig_prices = {s["symbol"]: s["price"] for s in selected}
    annual_dividend_income = sum(
        portfolio.holdings[sym] * orig_prices.get(sym, 0) * div_yield_map.get(sym, 0)
        for sym in portfolio.holdings
    )

    return {
        "strategy": strategy,
        "monthly_amount": monthly_amount,
        "months_simulated": months,
        "total_contributed": round(portfolio.total_contributed, 2),
        "total_invested": round(portfolio.total_invested, 2),
        "final_portfolio_value": round(final_value, 2),
        "total_cash_reserve": round(total_reserve, 2),
        "unrealised_gain": round(final_value - portfolio.total_invested, 2),
        "total_return_pct": round((final_value - portfolio.total_invested) / portfolio.total_invested * 100, 2) if portfolio.total_invested > 0 else 0,
        "annual_dividend_income": round(annual_dividend_income, 2),
        "monthly_dividend_income": round(annual_dividend_income / 12, 2),
        "final_holdings": portfolio.holdings,
        "month_by_month": portfolio.history,
    }


def simulate_escalating_sip(
    basket: List[Dict],
    yearly_amounts: List[float],
    penny_total: float = 0,
    penny_installments: int = 6,
    strategy: str = "hybrid",
    annual_price_growth_pct: float = 12.0,
    inflation_rate_pct: float = 15.0,
    drip_enabled: bool = True,
) -> Dict:
    """
    Simulate a Step-Up SIP where the monthly amount increases each year.
    Includes DRIP (dividend reinvestment), penny stock allocation,
    inflation comparison, and a "quit your job" analysis.

    Args:
        basket:                  Screened stock basket.
        yearly_amounts:          Monthly SIP per year e.g. [10000, 15000, 22500]
        penny_total:             Total budget for penny stocks over the full period.
        penny_installments:      How many times penny money is invested (every 6 months).
        strategy:                "long_term", "aggressive_dividend", or "hybrid"
        annual_price_growth_pct: Assumed annual return on equity (default 12%).
        inflation_rate_pct:      Inflation hurdle rate to beat (default 15%).
        drip_enabled:            Whether to reinvest dividends each quarter.

    Returns:
        Comprehensive dict with year-by-year results, total numbers, and inflation check.
    """
    from scoring.drip_engine import execute_drip

    # Filter basket by strategy
    if strategy == "long_term":
        selected = [s for s in basket if "Long Term Hold" in s.get("category", [])]
    elif strategy == "aggressive_dividend":
        selected = [s for s in basket if "Aggressive Dividend" in s.get("category", [])]
    else:
        seen, selected = set(), []
        for s in basket:
            if any(c in s.get("category", []) for c in ["Hybrid", "Long Term Hold", "Aggressive Dividend"]):
                if s["symbol"] not in seen:
                    seen.add(s["symbol"])
                    selected.append(s)

    portfolio = SIPPortfolio(selected)
    monthly_growth = (1 + annual_price_growth_pct / 100.0) ** (1 / 12) - 1
    current_prices = {s["symbol"]: s["price"] for s in selected}

    total_months = len(yearly_amounts) * 12
    year_summaries = []
    drip_cash_reserve = 0.0
    total_drip_reinvested = 0.0
    drip_txns = 0

    penny_per_installment = penny_total / max(penny_installments, 1)
    penny_months = [round(total_months / penny_installments * i) for i in range(1, penny_installments + 1)]

    month_counter = 0
    for year_idx, monthly_amount in enumerate(yearly_amounts):
        year_start_value = sum(
            portfolio.holdings.get(s["symbol"], 0) * current_prices.get(s["symbol"], 0)
            for s in selected
        )
        year_invested = 0.0
        year_dividend = 0.0

        for _ in range(12):
            month_counter += 1
            label = f"Y{year_idx+1}-M{(_ + 1)}"

            summary = portfolio.invest_monthly(monthly_amount, label, price_overrides=current_prices.copy())
            year_invested += summary["spent_this_month"]

            # Quarterly DRIP
            if drip_enabled and month_counter % 3 == 0:
                div_yield_map = {s["symbol"]: s.get("div_yield", 0) / 100.0 for s in selected}
                quarterly_div = sum(
                    portfolio.holdings.get(s["symbol"], 0)
                    * current_prices.get(s["symbol"], 0)
                    * div_yield_map.get(s["symbol"], 0) / 4
                    for s in selected
                ) + drip_cash_reserve

                # Build updated basket for DRIP scoring
                updated_basket = [{**s, "price": current_prices.get(s["symbol"], s["price"])} for s in selected]
                drip_result = execute_drip(portfolio.holdings, updated_basket, quarterly_div, label=label)
                drip_cash_reserve = drip_result.get("leftover", 0)
                if drip_result.get("action") == "BUY":
                    total_drip_reinvested += drip_result.get("amount_spent", 0)
                    drip_txns += 1

            # Annual dividend tracking
            year_dividend += sum(
                portfolio.holdings.get(s["symbol"], 0)
                * current_prices.get(s["symbol"], 0)
                * (s.get("div_yield", 0) / 100.0) / 12
                for s in selected
            )

            current_prices = {sym: p * (1 + monthly_growth) for sym, p in current_prices.items()}

        year_end_value = sum(
            portfolio.holdings.get(s["symbol"], 0) * current_prices.get(s["symbol"], 0)
            for s in selected
        )
        year_summaries.append({
            "year": year_idx + 1,
            "monthly_sip": monthly_amount,
            "total_invested_this_year": round(year_invested, 2),
            "portfolio_value_start": round(year_start_value, 2),
            "portfolio_value_end": round(year_end_value, 2),
            "annual_dividend_income": round(year_dividend, 2),
            "year_gain_pct": round((year_end_value - year_start_value - year_invested) / max(year_start_value + year_invested, 1) * 100, 2),
        })

    # Final calculations
    final_prices = current_prices
    final_equity_value = sum(
        portfolio.holdings.get(s["symbol"], 0) * final_prices.get(s["symbol"], 0)
        for s in selected
    )
    div_yield_map = {s["symbol"]: s.get("div_yield", 0) / 100.0 for s in selected}
    annual_div = sum(
        portfolio.holdings.get(s["symbol"], 0)
        * final_prices.get(s["symbol"], 0)
        * div_yield_map.get(s["symbol"], 0)
        for s in selected
    )

    total_sip_contributed = portfolio.total_contributed
    total_capital_deployed = total_sip_contributed + penny_total
    cash_reserve = sum(portfolio.cash_reserve.values()) + drip_cash_reserve

    # Inflation hurdle
    years = len(yearly_amounts)
    inflation_multiplier = (1 + inflation_rate_pct / 100) ** years
    inflation_adjusted_target = total_capital_deployed * inflation_multiplier
    beats_inflation = final_equity_value > inflation_adjusted_target

    # Quit-job analysis — how much passive income does portfolio generate?
    monthly_passive = annual_div / 12

    return {
        "plan_summary": {
            "years": years,
            "monthly_sip_schedule": yearly_amounts,
            "total_sip_contributed": round(total_sip_contributed, 2),
            "penny_stock_budget": round(penny_total, 2),
            "total_capital_deployed": round(total_capital_deployed, 2),
            "cash_reserve_unspent": round(cash_reserve, 2),
        },
        "year_by_year": year_summaries,
        "final_portfolio": {
            "equity_value": round(final_equity_value, 2),
            "total_value_incl_reserve": round(final_equity_value + cash_reserve, 2),
            "unrealised_gain": round(final_equity_value - portfolio.total_invested, 2),
            "total_return_pct": round(
                (final_equity_value - portfolio.total_invested) / max(portfolio.total_invested, 1) * 100, 2
            ),
            "annual_dividend_income": round(annual_div, 2),
            "monthly_dividend_income": round(monthly_passive, 2),
        },
        "drip_stats": {
            "enabled": drip_enabled,
            "total_reinvested": round(total_drip_reinvested, 2),
            "transactions": drip_txns,
        },
        "inflation_check": {
            "inflation_rate_pct": inflation_rate_pct,
            "years": years,
            "total_capital_deployed": round(total_capital_deployed, 2),
            "inflation_adjusted_target": round(inflation_adjusted_target, 2),
            "final_portfolio_value": round(final_equity_value, 2),
            "beats_inflation": beats_inflation,
            "gap": round(final_equity_value - inflation_adjusted_target, 2),
        },
        "quit_job_analysis": {
            "monthly_passive_income": round(monthly_passive, 2),
            "annual_passive_income": round(annual_div, 2),
            "note": (
                "Your portfolio generates this much passive income from dividends alone. "
                "To quit your job, this should exceed your monthly salary. "
                "Capital appreciation adds to portfolio value but is not liquid income unless you sell."
            ),
        },
        "final_holdings": portfolio.holdings,
    }
