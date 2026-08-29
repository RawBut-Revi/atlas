"""
Project Atlas - Fundamental Analysis Library
===============================================
Core financial ratios and metrics for evaluating company quality.
All functions work with a company's financial data dict and return
calculated metrics.

Categories:
    - Profitability: ROE, ROCE, Margins
    - Valuation: PE, PB, Dividend Yield
    - Financial Health: Debt ratios, Coverage
    - Growth: Revenue, Profit, Dividend CAGRs
"""

import numpy as np
from typing import Optional


# ============================================================
# PROFITABILITY METRICS
# ============================================================

def calculate_roe(net_income: float, shareholders_equity: float) -> Optional[float]:
    """
    Return on Equity (ROE).
    
    Measures how efficiently a company uses shareholder capital to generate profits.
    
    Great companies: ROE > 15% consistently
    Red flag: ROE < 10% or highly volatile
    
    Formula: Net Income / Shareholders' Equity * 100
    """
    if shareholders_equity <= 0:
        return None
    return (net_income / shareholders_equity) * 100


def calculate_roce(ebit: float, capital_employed: float) -> Optional[float]:
    """
    Return on Capital Employed (ROCE).
    
    More comprehensive than ROE — includes debt capital.
    Shows how efficiently ALL capital (equity + debt) generates returns.
    
    Great companies: ROCE > 20%
    Capital employed = Total Assets - Current Liabilities
    
    Formula: EBIT / Capital Employed * 100
    """
    if capital_employed <= 0:
        return None
    return (ebit / capital_employed) * 100


def calculate_net_profit_margin(net_income: float, revenue: float) -> Optional[float]:
    """
    Net Profit Margin.
    
    What percentage of revenue turns into actual profit.
    
    Great companies: > 15% (varies by sector)
    IT sector: 20%+ is common
    FMCG: 15%+ is good
    
    Formula: Net Income / Revenue * 100
    """
    if revenue <= 0:
        return None
    return (net_income / revenue) * 100


def calculate_operating_margin(operating_income: float, revenue: float) -> Optional[float]:
    """
    Operating Profit Margin (EBIT Margin).
    
    Profitability from core operations, excluding interest and taxes.
    More stable than net margin as it excludes one-time items.
    
    Formula: Operating Income / Revenue * 100
    """
    if revenue <= 0:
        return None
    return (operating_income / revenue) * 100


def calculate_fcf_yield(free_cash_flow: float, market_cap: float) -> Optional[float]:
    """
    Free Cash Flow Yield.
    
    How much free cash flow the company generates relative to its market price.
    Higher is better — means you're getting more cash flow per rupee invested.
    
    FCF Yield > Dividend Yield = Dividend is sustainable
    FCF Yield > 5% = Potentially undervalued
    
    Formula: Free Cash Flow / Market Cap * 100
    """
    if market_cap <= 0:
        return None
    return (free_cash_flow / market_cap) * 100


# ============================================================
# VALUATION METRICS
# ============================================================

def calculate_pe_ratio(market_price: float, earnings_per_share: float) -> Optional[float]:
    """
    Price-to-Earnings Ratio (PE).
    
    How much investors pay per rupee of earnings.
    
    PE < 15: Potentially undervalued (for large caps)
    PE 15-25: Fair value
    PE > 25: Potentially overvalued (unless high growth)
    
    Formula: Market Price / EPS
    """
    if earnings_per_share <= 0:
        return None
    return market_price / earnings_per_share


def calculate_pb_ratio(market_price: float, book_value_per_share: float) -> Optional[float]:
    """
    Price-to-Book Ratio (PB).
    
    Compares market price to the company's net asset value.
    
    PB < 1: Trading below book value (potentially undervalued or distressed)
    PB 1-3: Reasonable for quality companies
    PB > 3: Premium valuation
    
    Formula: Market Price / Book Value Per Share
    """
    if book_value_per_share <= 0:
        return None
    return market_price / book_value_per_share


def calculate_peg_ratio(pe_ratio: float, earnings_growth_rate: float) -> Optional[float]:
    """
    PEG Ratio (PE relative to Growth).
    
    Adjusts PE for growth — a high PE is justified if growth is also high.
    
    PEG < 1: Undervalued relative to growth (sweet spot!)
    PEG = 1: Fairly valued
    PEG > 2: Overvalued even considering growth
    
    Formula: PE Ratio / Earnings Growth Rate (%)
    """
    if earnings_growth_rate <= 0:
        return None
    return pe_ratio / earnings_growth_rate


def calculate_ev_ebitda(enterprise_value: float, ebitda: float) -> Optional[float]:
    """
    Enterprise Value / EBITDA.
    
    Better than PE for comparing companies with different debt levels.
    
    EV/EBITDA < 10: Potentially undervalued
    EV/EBITDA 10-15: Fair value
    EV/EBITDA > 15: Premium
    
    Formula: Enterprise Value / EBITDA
    """
    if ebitda <= 0:
        return None
    return enterprise_value / ebitda


def calculate_dividend_yield(annual_dividend_per_share: float, market_price: float) -> Optional[float]:
    """
    Dividend Yield.
    
    Annual dividend income as a percentage of current price.
    
    For our aggressive strategy: Target 6%+
    India inflation ~6-7%, so yield must beat this.
    
    Formula: Annual Dividend Per Share / Market Price * 100
    """
    if market_price <= 0:
        return None
    return (annual_dividend_per_share / market_price) * 100


# ============================================================
# FINANCIAL HEALTH METRICS
# ============================================================

def calculate_debt_to_equity(total_debt: float, shareholders_equity: float) -> Optional[float]:
    """
    Debt-to-Equity Ratio.
    
    How much debt the company uses relative to equity.
    
    D/E < 0.5: Conservative (ideal for dividend stocks)
    D/E 0.5-1.0: Moderate
    D/E > 1.0: Leveraged (risky for dividend sustainability)
    D/E = 0: Debt-free (dream stock!)
    
    Formula: Total Debt / Shareholders' Equity
    """
    if shareholders_equity <= 0:
        return None
    return total_debt / shareholders_equity


def calculate_interest_coverage(ebit: float, interest_expense: float) -> Optional[float]:
    """
    Interest Coverage Ratio.
    
    Can the company comfortably pay its interest obligations?
    
    > 5: Very comfortable
    3-5: Adequate
    < 3: Risky — interest eating into profits
    < 1: Company can't cover interest from operations!
    
    Formula: EBIT / Interest Expense
    """
    if interest_expense <= 0:
        return float('inf')  # No debt = infinite coverage (great!)
    return ebit / interest_expense


def calculate_current_ratio(current_assets: float, current_liabilities: float) -> Optional[float]:
    """
    Current Ratio.
    
    Can the company pay its short-term obligations?
    
    > 2: Very healthy
    1.5-2: Good
    < 1: May struggle to pay short-term debts
    
    Formula: Current Assets / Current Liabilities
    """
    if current_liabilities <= 0:
        return None
    return current_assets / current_liabilities


def calculate_payout_ratio(dividends_paid: float, net_income: float) -> Optional[float]:
    """
    Dividend Payout Ratio.
    
    What percentage of earnings is paid as dividends.
    
    30-60%: Sweet spot (enough retained for growth + generous dividend)
    > 80%: Potentially unsustainable
    > 100%: Paying more than they earn (RED FLAG!)
    
    Formula: Dividends Paid / Net Income * 100
    """
    if net_income <= 0:
        return None
    return (dividends_paid / net_income) * 100


# ============================================================
# GROWTH METRICS
# ============================================================

def calculate_cagr(beginning_value: float, ending_value: float, years: int) -> Optional[float]:
    """
    Compound Annual Growth Rate (CAGR).
    
    Smoothed annualized growth rate over a period.
    
    For beating inflation in India: CAGR > 7%
    Great growth: CAGR > 15%
    
    Formula: (Ending / Beginning)^(1/years) - 1
    """
    if beginning_value <= 0 or ending_value <= 0 or years <= 0:
        return None
    return ((ending_value / beginning_value) ** (1 / years) - 1) * 100


def calculate_revenue_cagr(revenues: list[float]) -> Optional[float]:
    """Calculate revenue CAGR from a list of annual revenues (oldest to newest)."""
    if len(revenues) < 2:
        return None
    return calculate_cagr(revenues[0], revenues[-1], len(revenues) - 1)


def calculate_profit_cagr(profits: list[float]) -> Optional[float]:
    """Calculate net profit CAGR from a list of annual profits (oldest to newest)."""
    if len(profits) < 2 or any(p <= 0 for p in [profits[0], profits[-1]]):
        return None
    return calculate_cagr(profits[0], profits[-1], len(profits) - 1)


def calculate_dividend_cagr(dividends: list[float]) -> Optional[float]:
    """Calculate dividend per share CAGR (oldest to newest)."""
    if len(dividends) < 2 or any(d <= 0 for d in [dividends[0], dividends[-1]]):
        return None
    return calculate_cagr(dividends[0], dividends[-1], len(dividends) - 1)


# ============================================================
# INFLATION-BEATING CHECK
# ============================================================

def beats_inflation(total_return_cagr: float, inflation_rate: float = 6.5) -> bool:
    """
    Check if the total return (dividend yield + capital appreciation CAGR)
    beats the Indian inflation rate.
    
    Default inflation rate: 6.5% (RBI's typical upper band)
    
    For real wealth building: total return should be at least 2x inflation.
    """
    return total_return_cagr > inflation_rate


def real_return(nominal_return: float, inflation_rate: float = 6.5) -> float:
    """
    Calculate the real (inflation-adjusted) return.
    
    Formula: ((1 + nominal/100) / (1 + inflation/100) - 1) * 100
    """
    return ((1 + nominal_return / 100) / (1 + inflation_rate / 100) - 1) * 100
