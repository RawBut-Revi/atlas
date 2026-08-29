"""
Project Atlas - Atlas Penny Score
===================================
A dedicated scoring model for penny/small-cap growth stocks.
Completely separate from the Atlas Dividend Score.

Why a separate model?
    Dividend Score measures: income stability, payout safety, ROE
    Penny Score measures:    revenue momentum, turnaround velocity,
                             promoter conviction, sector tailwind strength

Weights:
    Revenue Momentum   30%  — The #1 signal. 2x revenue = real business growth
    Profitability Turn 20%  — Loss→profit is the highest-conviction turnaround signal
    Promoter Conviction 20% — Promoter buying their own stock = they know something
    Financial Safety   15%  — Enough runway to survive before story plays out
    Sector Tailwind    15%  — Govt/macro wind at the back
"""

from typing import Dict, Optional


# ============================================================
# HARD REJECT FILTERS
# ============================================================

def passes_hard_filters(data: Dict) -> tuple:
    """
    Check if a stock passes all hard reject filters.
    Failing ANY filter = auto-disqualification.

    Returns:
        (passed: bool, reason: str)
    """
    checks = [
        (data.get("promoter_holding", 0) >= 25,
         f"Promoter holding {data.get('promoter_holding', 0)}% < 25% minimum"),

        (data.get("pledged_pct", 100) <= 40,
         f"Pledged shares {data.get('pledged_pct', 100)}% > 40% — promoter in distress"),

        (data.get("interest_coverage", 0) >= 1.0 or data.get("debt_to_equity", 0) == 0,
         f"Interest coverage {data.get('interest_coverage', 0):.1f}x < 1.0 — can't service debt"),

        (data.get("revenue_cagr_2y", -999) > -15,
         f"Revenue declining {data.get('revenue_cagr_2y', 0):.1f}% — story is over"),

        (data.get("promoter_trend", "") != "decreasing",
         "Promoter actively selling — loss of conviction"),
    ]

    for passed, reason in checks:
        if not passed:
            return False, reason

    return True, "OK"


# ============================================================
# DIMENSION SCORERS
# ============================================================

def score_revenue_momentum(revenue_cagr_2y: float, revenue_cagr_3y: float,
                            latest_revenue_cr: float, prev_revenue_cr: float) -> float:
    """
    Score: 0-30 pts
    The #1 signal for penny stock selection.
    2-3x revenue in 2 years = near-certainty of price re-rating.
    """
    score = 0.0

    # 2-year CAGR component (max 20 pts)
    if revenue_cagr_2y >= 100:    score += 20   # 2x revenue = full marks
    elif revenue_cagr_2y >= 60:   score += 16
    elif revenue_cagr_2y >= 40:   score += 13
    elif revenue_cagr_2y >= 25:   score += 10
    elif revenue_cagr_2y >= 15:   score += 6
    elif revenue_cagr_2y >= 5:    score += 3
    else:                          score += 0    # Below 5% = weak

    # 3-year CAGR consistency bonus (max 5 pts)
    if revenue_cagr_3y >= 50:     score += 5
    elif revenue_cagr_3y >= 30:   score += 3
    elif revenue_cagr_3y >= 15:   score += 2

    # YoY acceleration bonus (max 5 pts)
    # If latest year growth > 3-year average = accelerating
    if prev_revenue_cr > 0:
        yoy_growth = (latest_revenue_cr - prev_revenue_cr) / prev_revenue_cr * 100
        if yoy_growth > revenue_cagr_3y * 1.5:  score += 5   # Accelerating!
        elif yoy_growth > revenue_cagr_3y:       score += 3   # Holding pace

    return min(30.0, score)


def score_profitability_turn(profit_cagr_2y: Optional[float], was_loss_making: bool,
                              roe: float) -> float:
    """
    Score: 0-20 pts
    Turnaround = highest conviction signal.
    Loss→profit is the entry point most people miss.
    """
    score = 0.0

    if was_loss_making and profit_cagr_2y is not None:
        # Turnaround premium — this is the jackpot scenario
        score += 15
        if profit_cagr_2y >= 100:  score += 5  # Explosive profit growth post-turnaround
        elif profit_cagr_2y >= 50: score += 3
    elif profit_cagr_2y is not None:
        # Already profitable, growing profits
        if profit_cagr_2y >= 80:   score += 18
        elif profit_cagr_2y >= 50: score += 15
        elif profit_cagr_2y >= 30: score += 11
        elif profit_cagr_2y >= 15: score += 7
        elif profit_cagr_2y >= 0:  score += 3

    # ROE quality bonus
    if roe >= 25:      score = min(score + 2, 20)
    elif roe >= 18:    score = min(score + 1, 20)

    return min(20.0, score)


def score_promoter_conviction(holding: float, trend: str, pledged: float) -> float:
    """
    Score: 0-20 pts
    Promoter = insider. They know the business best.
    High holding + increasing + zero pledge = maximum conviction signal.
    """
    score = 0.0

    # Holding % (max 8 pts)
    if holding >= 70:     score += 8
    elif holding >= 55:   score += 6
    elif holding >= 45:   score += 4
    elif holding >= 35:   score += 2

    # Trend (max 8 pts) — most important component
    if trend == "increasing":   score += 8   # Promoter buying = very bullish
    elif trend == "stable":     score += 4
    elif trend == "decreasing": score += 0   # Would have failed hard filter

    # Pledge penalty (max 4 pts)
    if pledged == 0:      score += 4
    elif pledged <= 10:   score += 3
    elif pledged <= 20:   score += 2
    elif pledged <= 30:   score += 1

    return min(20.0, score)


def score_financial_safety(debt_to_equity: float, interest_coverage: float,
                            market_cap_cr: float) -> float:
    """
    Score: 0-15 pts
    Penny stocks need enough financial runway to survive before the
    market recognizes the story. Debt kills turnarounds.
    """
    score = 0.0

    # Debt to equity (max 7 pts)
    if debt_to_equity == 0:       score += 7   # Debt-free = safest
    elif debt_to_equity <= 0.2:   score += 6
    elif debt_to_equity <= 0.5:   score += 5
    elif debt_to_equity <= 0.8:   score += 3
    elif debt_to_equity <= 1.2:   score += 1

    # Interest coverage (max 5 pts)
    if interest_coverage >= 15:   score += 5
    elif interest_coverage >= 8:  score += 4
    elif interest_coverage >= 5:  score += 3
    elif interest_coverage >= 2:  score += 2
    elif interest_coverage >= 1:  score += 1

    # Market cap — small enough to still be undiscovered? (max 3 pts)
    if market_cap_cr < 500:        score += 3   # True micro-cap, max upside
    elif market_cap_cr < 1500:     score += 2   # Small-cap, still room to run
    elif market_cap_cr < 5000:     score += 1   # Mid-small, smaller upside

    return min(15.0, score)


def score_sector_tailwind(pli_beneficiary: bool, sector_tailwind: str,
                           sector: str) -> float:
    """
    Score: 0-15 pts
    Govt policy and macro tailwinds are the wind that turns small boats into rockets.
    PLI schemes have a proven track record of creating multi-baggers.
    """
    score = 0.0

    # PLI beneficiary (max 8 pts)
    if pli_beneficiary:
        score += 8  # Direct govt money flowing into the sector

    # Sector classification (max 7 pts)
    high_tailwind_sectors = [
        "renewable energy", "solar", "semiconductor", "defence", "railway",
        "electronics", "ev", "electric vehicle", "pharma", "medical devices",
        "precision engineering", "aerospace"
    ]
    moderate_tailwind_sectors = [
        "auto ancillary", "infrastructure", "construction", "steel",
        "chemicals", "textile", "it", "technology"
    ]

    sector_lower = sector.lower()
    if any(s in sector_lower for s in high_tailwind_sectors):
        score += 7
    elif any(s in sector_lower for s in moderate_tailwind_sectors):
        score += 4
    else:
        score += 2

    return min(15.0, score)


# ============================================================
# MASTER PENNY SCORE
# ============================================================

def calculate_penny_score(data: Dict) -> Dict:
    """
    Calculate the Atlas Penny Score for a stock.

    Args:
        data: Stock dict from penny_stock_data.py

    Returns:
        Dict with total score, dimension scores, verdict, and hard filter result.
    """
    passed, reject_reason = passes_hard_filters(data)

    if not passed:
        return {
            "total_score": 0,
            "passed_filters": False,
            "reject_reason": reject_reason,
            "verdict": "REJECTED",
        }

    rev = score_revenue_momentum(
        data.get("revenue_cagr_2y", 0),
        data.get("revenue_cagr_3y", 0),
        data.get("latest_revenue_cr", 0),
        data.get("prev_revenue_cr", 0),
    )
    prof = score_profitability_turn(
        data.get("profit_cagr_2y"),
        data.get("was_loss_making", False),
        data.get("roe", 0),
    )
    prom = score_promoter_conviction(
        data.get("promoter_holding", 0),
        data.get("promoter_trend", "stable"),
        data.get("pledged_pct", 0),
    )
    safety = score_financial_safety(
        data.get("debt_to_equity", 99),
        data.get("interest_coverage", 0),
        data.get("market_cap_cr", 0),
    )
    tailwind = score_sector_tailwind(
        data.get("pli_beneficiary", False),
        data.get("sector_tailwind", ""),
        data.get("sector", ""),
    )

    total = rev + prof + prom + safety + tailwind

    # Verdict
    if total >= 78:    verdict = "STRONG BUY — Top-tier penny pick"
    elif total >= 62:  verdict = "BUY — Good risk/reward"
    elif total >= 48:  verdict = "WATCHLIST — Monitor closely"
    elif total >= 35:  verdict = "SPECULATIVE — High risk, proceed with caution"
    else:              verdict = "AVOID — Risk outweighs reward"

    return {
        "total_score": round(total, 1),
        "passed_filters": True,
        "reject_reason": None,
        "verdict": verdict,
        "dimension_scores": {
            "revenue_momentum":    round(rev, 1),
            "profitability_turn":  round(prof, 1),
            "promoter_conviction": round(prom, 1),
            "financial_safety":    round(safety, 1),
            "sector_tailwind":     round(tailwind, 1),
        },
        "max_possible": {"revenue_momentum": 30, "profitability_turn": 20,
                         "promoter_conviction": 20, "financial_safety": 15,
                         "sector_tailwind": 15},
    }


# ============================================================
# POSITION SIZING (3% RULE — AUTO-ENFORCED)
# ============================================================

def calculate_penny_allocation(
    penny_score: float,
    total_portfolio_value: float,
    max_penny_portfolio_pct: float = 15.0,
    max_single_stock_pct: float = 3.0,
) -> Dict:
    """
    Auto-enforce position sizing rules for penny stocks.

    Rules:
    - Total penny allocation NEVER exceeds max_penny_portfolio_pct (15%)
    - Single stock NEVER exceeds max_single_stock_pct (3%)
    - Higher-scored stocks get slightly more within the 3% cap

    Args:
        penny_score:            Atlas Penny Score (0-100)
        total_portfolio_value:  Total portfolio value in INR
        max_penny_portfolio_pct: Maximum % for all penny stocks combined
        max_single_stock_pct:   Hard cap per stock

    Returns:
        Position sizing recommendation in INR and %
    """
    max_penny_inr = total_portfolio_value * max_penny_portfolio_pct / 100
    max_single_inr = total_portfolio_value * max_single_stock_pct / 100

    # Scale within 1-3% based on score
    if penny_score >= 78:      suggested_pct = 3.0
    elif penny_score >= 62:    suggested_pct = 2.0
    elif penny_score >= 48:    suggested_pct = 1.5
    else:                      suggested_pct = 1.0

    suggested_inr = min(total_portfolio_value * suggested_pct / 100, max_single_inr)

    return {
        "suggested_allocation_pct": suggested_pct,
        "suggested_allocation_inr": round(suggested_inr, 2),
        "hard_cap_per_stock_inr": round(max_single_inr, 2),
        "max_penny_portfolio_inr": round(max_penny_inr, 2),
        "rule": f"Max {max_single_stock_pct}% per stock, {max_penny_portfolio_pct}% total penny exposure",
    }


# ============================================================
# EXIT SIGNAL ENGINE
# ============================================================

def check_exit_signals(
    data: Dict,
    entry_price: float,
    current_price: float,
    current_revenue_growth: Optional[float] = None,
) -> Dict:
    """
    Check automatic exit signals for a penny stock position.

    Three triggers:
    1. Stop-loss: Price drops -30% from entry
    2. Story break: Revenue growth drops below 15% for 2 quarters
    3. Promoter exit: Promoter starts reducing stake

    Args:
        data:                   Current stock data dict
        entry_price:            Your purchase price
        current_price:          Current market price
        current_revenue_growth: Latest quarterly revenue growth % (optional)

    Returns:
        Dict with exit recommendation and triggered signals
    """
    signals = []
    exit_urgency = "HOLD"

    # --- Signal 1: Stop-loss (-30% from entry) ---
    pnl_pct = (current_price - entry_price) / entry_price * 100
    if pnl_pct <= -30:
        signals.append({
            "trigger": "STOP_LOSS",
            "message": f"Down {pnl_pct:.1f}% from entry ₹{entry_price}. Capital protection rule triggered.",
            "action": "SELL IMMEDIATELY — full position",
        })
        exit_urgency = "EXIT NOW"

    # --- Signal 2: Revenue story breaking ---
    if current_revenue_growth is not None and current_revenue_growth < 15:
        signals.append({
            "trigger": "STORY_BREAK",
            "message": f"Revenue growth slowed to {current_revenue_growth:.1f}% (threshold: 15%). If next quarter also below 15%, exit.",
            "action": "REDUCE 50% — Watch next quarter",
        })
        if exit_urgency == "HOLD":
            exit_urgency = "REDUCE"

    # --- Signal 3: Promoter selling ---
    if data.get("promoter_trend") == "decreasing":
        signals.append({
            "trigger": "PROMOTER_EXIT",
            "message": "Promoter reducing stake — insider losing conviction. Major red flag.",
            "action": "SELL IMMEDIATELY — full position",
        })
        exit_urgency = "EXIT NOW"

    # --- Profit booking (partial) ---
    if pnl_pct >= 100:
        signals.append({
            "trigger": "PROFIT_BOOKING",
            "message": f"Up {pnl_pct:.1f}% — doubled your money! Book 50% to recover capital, let rest run.",
            "action": "SELL 50% — let remaining position run free",
        })
        if exit_urgency == "HOLD":
            exit_urgency = "PARTIAL SELL"

    return {
        "symbol": data.get("symbol", ""),
        "entry_price": entry_price,
        "current_price": current_price,
        "pnl_pct": round(pnl_pct, 1),
        "exit_urgency": exit_urgency,
        "signals": signals,
        "hold_ok": exit_urgency == "HOLD",
    }
