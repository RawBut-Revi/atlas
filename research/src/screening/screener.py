"""
Project Atlas - Unified Screener
==================================
Combines Fundamental (What to buy) and Technical (When to buy) analysis
to identify high-probability dividend investments.
"""

from typing import Dict, Any, List

def screen_long_term_hold(
    fundamentals: Dict[str, float], 
    technicals: Dict[str, float],
    f_score: int,
    atlas_score: float
) -> Dict[str, Any]:
    """
    Screener for "Long Term Holdables" (Quality + Sustainability).
    
    Target: Buy and hold quality dividend stocks, only sell if fundamentals deteriorate.
    """
    passed_fundamental = True
    reasons_fundamental = []
    
    # 1. Fundamental Checks (What to buy)
    if fundamentals.get('roe', 0) < 15.0:
        passed_fundamental = False
        reasons_fundamental.append("ROE < 15%")
        
    if fundamentals.get('debt_to_equity', 99) > 1.0:
        passed_fundamental = False
        reasons_fundamental.append("Debt/Equity > 1.0")
        
    if f_score < 6:
        passed_fundamental = False
        reasons_fundamental.append(f"Piotroski F-Score ({f_score}) < 6")
        
    if atlas_score < 60:
        passed_fundamental = False
        reasons_fundamental.append(f"Atlas Dividend Score ({atlas_score}) < 60")
        
    passed_technical = True
    reasons_technical = []
    
    # 2. Technical Checks (When to buy) - Looking for value entries
    if technicals.get('rsi_14', 100) > 60:
        passed_technical = False
        reasons_technical.append("RSI > 60 (Not oversold/cheap enough)")
        
    if technicals.get('close', 0) > technicals.get('sma_200', 0) * 1.10:
        passed_technical = False
        reasons_technical.append("Price > 10% above SMA-200 (Extended)")
        
    return {
        "category": "Long Term Hold",
        "passed": passed_fundamental and passed_technical,
        "fundamental_pass": passed_fundamental,
        "technical_pass": passed_technical,
        "reasons": reasons_fundamental + reasons_technical
    }

def screen_tactical_dividend(
    fundamentals: Dict[str, float], 
    technicals: Dict[str, float],
    days_to_ex_dividend: int
) -> Dict[str, Any]:
    """
    Screener for "Tactical Dividend" (Short-term yield capture).
    
    Target: Enter before ex-dividend dates when technicals align, capture dividend + appreciation.
    """
    passed = True
    reasons = []
    
    # 1. Event Check
    if days_to_ex_dividend < 0 or days_to_ex_dividend > 45:
        passed = False
        reasons.append(f"Not in ex-dividend window ({days_to_ex_dividend} days)")
        
    # 2. Yield Check
    if fundamentals.get('dividend_yield', 0) < 3.0:
        passed = False
        reasons.append("Dividend yield < 3.0%")
        
    # 3. Technical Momentum Check (Needs upward momentum into the ex-date)
    if technicals.get('close', 0) < technicals.get('sma_50', 99999):
        passed = False
        reasons.append("Price below SMA-50 (Lack of momentum)")
        
    if technicals.get('macd', -1) < technicals.get('macd_signal', 0):
        passed = False
        reasons.append("MACD Bearish")
        
    return {
        "category": "Tactical Dividend",
        "passed": passed,
        "reasons": reasons
    }

def screen_hybrid(
    fundamentals: Dict[str, float], 
    technicals: Dict[str, float],
    f_score: int,
    atlas_score: float,
    days_to_ex_dividend: int
) -> Dict[str, Any]:
    """
    Screener for "Hybrid" (Core Quality + Short-term Catalyst).
    
    Target: Quality companies that are ALSO approaching a dividend payout
    AND have favorable technicals. The ultimate sweet spot.
    """
    long_term_res = screen_long_term_hold(fundamentals, technicals, f_score, atlas_score)
    tactical_res = screen_tactical_dividend(fundamentals, technicals, days_to_ex_dividend)
    
    # Both must pass their fundamental/catalyst checks
    passed = long_term_res["fundamental_pass"] and tactical_res["passed"]
    
    reasons = []
    if not long_term_res["fundamental_pass"]:
        reasons.extend(long_term_res["reasons"]) # Add fundamental failure reasons
    if not tactical_res["passed"]:
        reasons.extend(tactical_res["reasons"])
        
    return {
        "category": "Hybrid",
        "passed": passed,
        "reasons": reasons
    }
