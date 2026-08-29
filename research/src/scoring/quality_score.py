"""
Project Atlas - Quality Scoring Models
======================================
Objective scoring models to rank stocks based on their fundamental strength,
dividend sustainability, and growth potential.
"""

from typing import Dict, Any, List

def calculate_piotroski_f_score(financials_current_year: Dict[str, float], financials_prev_year: Dict[str, float]) -> int:
    """
    Calculates the Piotroski F-Score (0-9).
    
    A 9-point scoring system to determine the fundamental strength of a firm.
    Scores 7-9 are considered good quality.
    
    Expected keys in financials dictionaries:
    - net_income
    - operating_cash_flow
    - total_assets
    - long_term_debt
    - current_assets
    - current_liabilities
    - shares_outstanding
    - gross_margin
    - asset_turnover
    """
    score = 0
    
    # --- Profitability ---
    
    # 1. Positive Net Income
    if financials_current_year.get('net_income', 0) > 0:
        score += 1
        
    # 2. Positive Operating Cash Flow
    if financials_current_year.get('operating_cash_flow', 0) > 0:
        score += 1
        
    # 3. Higher ROA in the current period compared to the previous period
    roa_current = financials_current_year.get('net_income', 0) / financials_current_year.get('total_assets', 1)
    roa_prev = financials_prev_year.get('net_income', 0) / financials_prev_year.get('total_assets', 1)
    if roa_current > roa_prev:
        score += 1
        
    # 4. Cash flow from operations is greater than net income (quality of earnings)
    if financials_current_year.get('operating_cash_flow', 0) > financials_current_year.get('net_income', 0):
        score += 1
        
    # --- Leverage, Liquidity, and Source of Funds ---
    
    # 5. Lower ratio of long-term debt to total assets in the current period compared to previous
    debt_ratio_curr = financials_current_year.get('long_term_debt', 0) / financials_current_year.get('total_assets', 1)
    debt_ratio_prev = financials_prev_year.get('long_term_debt', 0) / financials_prev_year.get('total_assets', 1)
    if debt_ratio_curr < debt_ratio_prev:
        score += 1
        
    # 6. Higher current ratio this year compared to previous year
    current_ratio_curr = financials_current_year.get('current_assets', 0) / financials_current_year.get('current_liabilities', 1)
    current_ratio_prev = financials_prev_year.get('current_assets', 0) / financials_prev_year.get('current_liabilities', 1)
    if current_ratio_curr > current_ratio_prev:
        score += 1
        
    # 7. No new shares issued (no dilution)
    if financials_current_year.get('shares_outstanding', 0) <= financials_prev_year.get('shares_outstanding', 0):
        score += 1
        
    # --- Operating Efficiency ---
    
    # 8. Higher gross margin compared to previous year
    if financials_current_year.get('gross_margin', 0) > financials_prev_year.get('gross_margin', 0):
        score += 1
        
    # 9. Higher asset turnover ratio compared to previous year
    if financials_current_year.get('asset_turnover', 0) > financials_prev_year.get('asset_turnover', 0):
        score += 1
        
    return score


def calculate_atlas_dividend_score(
    years_consecutive_payout: int,
    dividend_cagr_5y: float,
    payout_ratio: float,
    fcf_yield: float,
    roe: float
) -> float:
    """
    Atlas Dividend Score (0-100).
    
    A composite score tailored for our aggressive dividend strategy that also
    demands quality and sustainability.
    
    Weightings:
    - Consistency (20%): Years of consecutive payouts.
    - Growth (25%): 5-year Dividend CAGR.
    - Sustainability (25%): Payout Ratio (penalty for > 70%).
    - Cash Coverage (15%): Free Cash Flow Yield.
    - Quality (15%): ROE.
    """
    score = 0.0
    
    # 1. Consistency (Max 20 pts)
    # 10+ years gets full points
    consistency_pts = min(20.0, (years_consecutive_payout / 10.0) * 20.0)
    score += consistency_pts
    
    # 2. Growth (Max 25 pts)
    # Target 15%+ CAGR for full points
    if dividend_cagr_5y >= 15.0:
        growth_pts = 25.0
    elif dividend_cagr_5y > 0:
        growth_pts = (dividend_cagr_5y / 15.0) * 25.0
    else:
        growth_pts = 0.0
    score += growth_pts
    
    # 3. Sustainability (Payout Ratio) (Max 25 pts)
    # Sweet spot is 30-60%.
    if 30.0 <= payout_ratio <= 60.0:
        sus_pts = 25.0
    elif payout_ratio < 30.0:
        # A bit low, but safe
        sus_pts = 15.0
    elif 60.0 < payout_ratio <= 80.0:
        # Getting high, scale down
        sus_pts = 25.0 - ((payout_ratio - 60.0) / 20.0) * 15.0
    else:
        # > 80% is red flag
        sus_pts = 0.0
    score += sus_pts
    
    # 4. Cash Coverage (Max 15 pts)
    # FCF Yield > 5% gets full points
    if fcf_yield >= 5.0:
        cash_pts = 15.0
    elif fcf_yield > 0:
        cash_pts = (fcf_yield / 5.0) * 15.0
    else:
        cash_pts = 0.0
    score += cash_pts
    
    # 5. Quality (ROE) (Max 15 pts)
    # ROE > 15% gets full points
    if roe >= 15.0:
        roe_pts = 15.0
    elif roe > 0:
        roe_pts = (roe / 15.0) * 15.0
    else:
        roe_pts = 0.0
    score += roe_pts
    
    return min(100.0, max(0.0, score))
