"""
Project Atlas - Dividend Analysis Engine
==========================================
Dedicated analysis for evaluating dividend sustainability, history,
and upcoming ex-dividend opportunities.
"""

from typing import List, Dict, Any
from .fundamental import calculate_dividend_yield, calculate_dividend_cagr

def evaluate_dividend_sustainability(
    payout_ratio: float, 
    fcf_yield: float, 
    debt_to_equity: float
) -> str:
    """
    Evaluates how safe the current dividend is.
    
    Returns a string rating: "High Safety", "Moderate Safety", "At Risk", "Dangerous"
    """
    if payout_ratio < 0 or fcf_yield < 0:
        return "Unknown"
        
    if payout_ratio > 100:
        return "Dangerous" # Paying more than they earn
        
    if payout_ratio > 80 and fcf_yield < 3.0:
        return "At Risk" # High payout, low cash flow
        
    if debt_to_equity > 1.5 and payout_ratio > 60:
        return "At Risk" # High debt might force dividend cuts
        
    if payout_ratio <= 60 and fcf_yield > 5.0 and debt_to_equity < 1.0:
        return "High Safety" # Plenty of room to keep paying
        
    return "Moderate Safety"


def analyze_dividend_history(history: List[float]) -> Dict[str, Any]:
    """
    Analyzes a list of historical annual dividend payouts (oldest to newest).
    
    Returns metrics like consecutive years of payment, consecutive years of growth,
    and 3Y/5Y CAGRs.
    """
    if not history:
        return {}
        
    years_paid = 0
    years_grown = 0
    
    # Calculate consecutive years paid (working backwards)
    for div in reversed(history):
        if div > 0:
            years_paid += 1
        else:
            break
            
    # Calculate consecutive years grown (working backwards)
    for i in range(len(history) - 1, 0, -1):
        if history[i] > history[i-1]:
            years_grown += 1
        else:
            break
            
    cagr_3y = calculate_dividend_cagr(history[-4:]) if len(history) >= 4 else None
    cagr_5y = calculate_dividend_cagr(history[-6:]) if len(history) >= 6 else None
    
    return {
        "consecutive_years_paid": years_paid,
        "consecutive_years_grown": years_grown,
        "cagr_3y": cagr_3y,
        "cagr_5y": cagr_5y,
        "latest_payout": history[-1] if history else 0
    }

def is_attractive_dividend_play(
    current_yield: float,
    historical_avg_yield: float,
    sustainability_rating: str,
    target_yield: float = 4.0
) -> bool:
    """
    Determines if a stock is currently an attractive dividend play based on
    our aggressive criteria.
    """
    # Needs to meet our target yield
    if current_yield < target_yield:
        return False
        
    # Is the yield higher than its own historical average? (implies undervaluation)
    if current_yield < historical_avg_yield * 0.9: # Give a 10% buffer
        return False
        
    # Must be reasonably safe
    if sustainability_rating in ["At Risk", "Dangerous"]:
        return False
        
    return True
