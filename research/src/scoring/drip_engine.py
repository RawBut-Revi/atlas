"""
Project Atlas - DRIP Engine (Dividend Reinvestment Plan)
=========================================================
Strategy C: Reinvest dividends into the HIGHEST Atlas-scored
stock at the moment of reinvestment — always putting money
where the current data says it belongs most.

Why Strategy C is optimal:
    - Markets change. The best stock today may not be the best
      stock when your dividend arrives 6 months from now.
    - Atlas scores update with prices, fundamentals, and technicals.
    - This means your dividend always flows to the strongest
      opportunity available, not a historically good one.
"""

from typing import Dict, List, Any
import math


def get_highest_scored_stock(basket: List[Dict], exclude_symbol: str = None) -> Dict:
    """
    Returns the stock with the highest current Atlas Dividend Score.

    Args:
        basket:         Full list of scored stock dicts.
        exclude_symbol: Optionally exclude a symbol (e.g. if it's suspended).

    Returns:
        The stock dict with the highest atlas_score.
    """
    candidates = [s for s in basket if s.get("atlas_score", 0) > 0]
    if exclude_symbol:
        candidates = [s for s in candidates if s["symbol"] != exclude_symbol]
    if not candidates:
        return None
    return max(candidates, key=lambda s: s["atlas_score"])


def calculate_dividend_received(holdings: Dict[str, int], basket: List[Dict]) -> Dict[str, float]:
    """
    Calculate the actual dividend cash received for the current holdings.

    Args:
        holdings: Dict of symbol -> shares held.
        basket:   Scored stock list with div_yield and price.

    Returns:
        Dict of symbol -> dividend cash received (INR).
    """
    price_map = {s["symbol"]: s["price"] for s in basket}
    yield_map = {s["symbol"]: s.get("div_yield", 0) / 100.0 for s in basket}
    dividends = {}

    for symbol, shares in holdings.items():
        price = price_map.get(symbol, 0)
        div_yield = yield_map.get(symbol, 0)
        # Annual dividend; divide by 4 for quarterly distribution
        annual_div = shares * price * div_yield
        quarterly_div = annual_div / 4
        dividends[symbol] = round(quarterly_div, 2)

    return dividends


def execute_drip(
    holdings: Dict[str, int],
    basket: List[Dict],
    dividend_cash: float,
    label: str = "DRIP",
) -> Dict[str, Any]:
    """
    Execute a DRIP reinvestment: take cash and buy shares of the
    highest Atlas-scored stock right now.

    Args:
        holdings:      Current holdings (symbol -> shares).
        basket:        Current scored basket (with updated prices/scores).
        dividend_cash: Total dividend cash to reinvest (INR).
        label:         Label for logging (e.g. "Q1 2026 DRIP").

    Returns:
        Dict with transaction details and updated holdings.
    """
    if dividend_cash <= 0:
        return {"action": "SKIP", "reason": "No dividend cash to reinvest", "label": label}

    # Find the best stock to buy right now
    target = get_highest_scored_stock(basket)
    if not target:
        return {"action": "SKIP", "reason": "No valid stocks in basket", "label": label}

    symbol = target["symbol"]
    price = target["price"]
    atlas_score = target["atlas_score"]

    shares_to_buy = math.floor(dividend_cash / price)

    if shares_to_buy < 1:
        return {
            "action": "ACCUMULATE",
            "symbol": symbol,
            "atlas_score": atlas_score,
            "dividend_cash": round(dividend_cash, 2),
            "price": price,
            "shares_to_buy": 0,
            "amount_spent": 0.0,
            "leftover": round(dividend_cash, 2),
            "label": label,
            "note": f"Saving ₹{dividend_cash:.0f} — need ₹{price:.0f} for 1 share of {symbol}",
        }

    amount_spent = shares_to_buy * price
    leftover = dividend_cash - amount_spent

    # Update holdings
    holdings[symbol] = holdings.get(symbol, 0) + shares_to_buy

    return {
        "action": "BUY",
        "symbol": symbol,
        "sector": target.get("sector", ""),
        "atlas_score": atlas_score,
        "div_yield": target.get("div_yield", 0),
        "dividend_cash": round(dividend_cash, 2),
        "price": price,
        "shares_to_buy": shares_to_buy,
        "amount_spent": round(amount_spent, 2),
        "leftover": round(leftover, 2),
        "label": label,
        "note": f"Best pick by Atlas Score ({atlas_score}) at ₹{price}/share",
    }


def simulate_drip_portfolio(
    basket: List[Dict],
    initial_holdings: Dict[str, int],
    months: int = 36,
    drip_frequency: str = "quarterly",  # "monthly" or "quarterly"
    annual_price_growth_pct: float = 12.0,
) -> Dict:
    """
    Simulate a full DRIP portfolio over time.

    Each quarter (or month), dividends are collected and reinvested
    into the highest Atlas-scored stock. Tracks compounding over time.

    Args:
        basket:               Scored stock basket.
        initial_holdings:     Starting share counts per symbol.
        months:               Simulation duration.
        drip_frequency:       How often dividends are reinvested.
        annual_price_growth_pct: Assumed annual price CAGR.

    Returns:
        Full simulation with transaction log and final portfolio value.
    """
    holdings = dict(initial_holdings)
    monthly_growth = (1 + annual_price_growth_pct / 100.0) ** (1 / 12) - 1
    current_prices = {s["symbol"]: s["price"] for s in basket}
    drip_cash_reserve = 0.0  # Accumulated unspent DRIP cash

    drip_interval = 3 if drip_frequency == "quarterly" else 1
    transaction_log = []
    portfolio_snapshots = []

    for month in range(1, months + 1):
        # Update basket prices for this month
        updated_basket = []
        for s in basket:
            updated = dict(s)
            updated["price"] = current_prices.get(s["symbol"], s["price"])
            updated_basket.append(updated)

        # Every drip_interval months, collect and reinvest dividends
        if month % drip_interval == 0:
            period_dividends = calculate_dividend_received(holdings, updated_basket)
            total_div = sum(period_dividends.values()) + drip_cash_reserve

            result = execute_drip(
                holdings,
                updated_basket,
                total_div,
                label=f"Month {month} DRIP",
            )
            transaction_log.append(result)

            # Carry forward unspent DRIP cash
            drip_cash_reserve = result.get("leftover", 0)

        # Portfolio value snapshot
        port_value = sum(
            holdings.get(s["symbol"], 0) * current_prices.get(s["symbol"], 0)
            for s in basket
        )
        portfolio_snapshots.append({
            "month": month,
            "portfolio_value": round(port_value, 2),
            "total_shares": sum(holdings.values()),
            "drip_cash_reserve": round(drip_cash_reserve, 2),
        })

        # Advance prices
        current_prices = {sym: p * (1 + monthly_growth) for sym, p in current_prices.items()}

    # Final calculations
    final_prices = current_prices
    final_value = sum(
        holdings.get(s["symbol"], 0) * final_prices.get(s["symbol"], 0)
        for s in basket
    )

    # Annual dividend income from final holdings
    annual_div = sum(
        holdings.get(s["symbol"], 0) * final_prices.get(s["symbol"], 0) * (s.get("div_yield", 0) / 100.0)
        for s in basket
    )

    return {
        "months_simulated": months,
        "drip_frequency": drip_frequency,
        "final_holdings": holdings,
        "final_portfolio_value": round(final_value, 2),
        "annual_dividend_income": round(annual_div, 2),
        "monthly_dividend_income": round(annual_div / 12, 2),
        "drip_cash_reserve": round(drip_cash_reserve, 2),
        "total_drip_transactions": len([t for t in transaction_log if t["action"] == "BUY"]),
        "transaction_log": transaction_log,
        "portfolio_snapshots": portfolio_snapshots,
    }
