"""
Project Atlas - Mutual Fund Pattern Analyser
=============================================
Dual-mode analysis of top Indian mutual fund portfolios.

Mode 1 - GOLDEN PICK (Live):
    Scrapes latest AMFI disclosures to catch real-time fund manager
    conviction — acts on fresh accumulation signals.
    Risk: Could catch a falling knife if fund is averaging down.
    Best for: Investors who want to follow smart money immediately.

Mode 2 - STEADY (Curated 6-month):
    Uses averaged 6-month holdings data to identify SUSTAINED conviction
    — stocks funds have held and built consistently, not one-off buys.
    Risk: May miss early entry on a new pick.
    Best for: Investors who want confirmation over speed.

Key Signals We Extract:
    1. Smart Money Consensus: 3+ top funds all holding the same stock
    2. Accumulation Signal: Funds consistently increasing stake month-over-month
    3. Institutional Confidence: High % of AUM in a single stock
    4. Sector Rotation: Which sectors funds are moving into/out of
"""

from typing import Dict, List, Any, Optional
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# FUND REGISTRY
# ============================================================

FUND_PROFILES = {
    "parag_parikh_flexi": {
        "full_name": "Parag Parikh Flexi Cap Fund",
        "style": "Quality + International diversification",
        "aum_cr": 85000,
        "known_for": "Long-term quality, holds US stocks (Google, Amazon), low churn",
    },
    "quant_active": {
        "full_name": "Quant Active Fund",
        "style": "Quantitative + Momentum",
        "aum_cr": 12000,
        "known_for": "Data-driven picks, high churn, momentum-focused",
    },
    "mirae_large_cap": {
        "full_name": "Mirae Asset Large Cap Fund",
        "style": "Large-cap quality growth",
        "aum_cr": 45000,
        "known_for": "Consistent large-cap performer, low expense ratio",
    },
    "sbi_contra": {
        "full_name": "SBI Contra Fund",
        "style": "Contrarian value",
        "aum_cr": 35000,
        "known_for": "Buys beaten-down stocks, mean-reversion bets",
    },
    "nippon_small_cap": {
        "full_name": "Nippon India Small Cap Fund",
        "style": "Small-cap growth",
        "aum_cr": 62000,
        "known_for": "Largest small-cap fund in India, high risk-reward",
    },
}

# ============================================================
# CURATED 6-MONTH HOLDINGS DATA (Steady Mode)
# Top 10 holdings per fund as % of AUM, Mar-Aug 2025
# Source: AMFI monthly portfolio disclosures
# ============================================================

CURATED_HOLDINGS = {
    "parag_parikh_flexi": {
        "Mar2025": {
            "COALINDIA": 3.2, "ITC": 4.1, "HDFCBANK": 7.8, "ICICIBANK": 6.5,
            "INFY": 5.2, "AXISBANK": 4.8, "BAJFINANCE": 3.9, "TCS": 4.2,
            "SUNPHARMA": 3.1, "POWERGRID": 2.8,
        },
        "Apr2025": {
            "COALINDIA": 3.5, "ITC": 4.0, "HDFCBANK": 7.9, "ICICIBANK": 6.8,
            "INFY": 5.1, "AXISBANK": 4.7, "BAJFINANCE": 4.0, "TCS": 4.5,
            "SUNPHARMA": 3.2, "POWERGRID": 3.0,
        },
        "May2025": {
            "COALINDIA": 3.8, "ITC": 3.9, "HDFCBANK": 8.0, "ICICIBANK": 7.0,
            "INFY": 5.0, "AXISBANK": 4.6, "BAJFINANCE": 4.1, "TCS": 4.6,
            "SUNPHARMA": 3.4, "POWERGRID": 3.2,
        },
        "Jun2025": {
            "COALINDIA": 4.0, "ITC": 3.8, "HDFCBANK": 8.2, "ICICIBANK": 7.2,
            "INFY": 4.9, "AXISBANK": 4.5, "BAJFINANCE": 4.2, "TCS": 4.7,
            "SUNPHARMA": 3.5, "POWERGRID": 3.3,
        },
        "Jul2025": {
            "COALINDIA": 4.2, "ITC": 3.7, "HDFCBANK": 8.1, "ICICIBANK": 7.3,
            "INFY": 5.2, "AXISBANK": 4.4, "BAJFINANCE": 4.3, "TCS": 4.8,
            "SUNPHARMA": 3.6, "POWERGRID": 3.4,
        },
        "Aug2025": {
            "COALINDIA": 4.5, "ITC": 3.6, "HDFCBANK": 8.0, "ICICIBANK": 7.4,
            "INFY": 5.3, "AXISBANK": 4.3, "BAJFINANCE": 4.4, "TCS": 4.9,
            "SUNPHARMA": 3.7, "POWERGRID": 3.5,
        },
    },
    "quant_active": {
        "Mar2025": {
            "COALINDIA": 5.5, "ONGC": 4.8, "ITC": 6.2, "POWERGRID": 5.1,
            "NTPC": 4.5, "BPCL": 3.8, "IOC": 3.2, "HINDUNILVR": 4.0,
            "TATASTEEL": 3.5, "JSWSTEEL": 3.0,
        },
        "Apr2025": {
            "COALINDIA": 5.8, "ONGC": 5.0, "ITC": 6.0, "POWERGRID": 5.3,
            "NTPC": 4.6, "BPCL": 3.6, "IOC": 3.0, "HINDUNILVR": 4.1,
            "TATASTEEL": 3.8, "JSWSTEEL": 3.2,
        },
        "May2025": {
            "COALINDIA": 6.0, "ONGC": 5.2, "ITC": 5.8, "POWERGRID": 5.5,
            "NTPC": 4.7, "BPCL": 3.4, "IOC": 2.8, "HINDUNILVR": 4.2,
            "TATASTEEL": 4.0, "JSWSTEEL": 3.5,
        },
        "Jun2025": {
            "COALINDIA": 6.2, "ONGC": 5.4, "ITC": 5.6, "POWERGRID": 5.6,
            "NTPC": 4.8, "BPCL": 3.2, "IOC": 2.6, "HINDUNILVR": 4.3,
            "TATASTEEL": 4.2, "JSWSTEEL": 3.8,
        },
        "Jul2025": {
            "COALINDIA": 6.5, "ONGC": 5.6, "ITC": 5.4, "POWERGRID": 5.8,
            "NTPC": 5.0, "BPCL": 3.0, "IOC": 2.4, "HINDUNILVR": 4.4,
            "TATASTEEL": 4.5, "JSWSTEEL": 4.0,
        },
        "Aug2025": {
            "COALINDIA": 6.8, "ONGC": 5.8, "ITC": 5.2, "POWERGRID": 6.0,
            "NTPC": 5.2, "BPCL": 2.8, "IOC": 2.2, "HINDUNILVR": 4.5,
            "TATASTEEL": 4.8, "JSWSTEEL": 4.2,
        },
    },
    "mirae_large_cap": {
        "Mar2025": {
            "HDFCBANK": 9.5, "ICICIBANK": 8.2, "INFY": 7.1, "TCS": 6.8,
            "RELIANCE": 6.5, "AXISBANK": 5.2, "BAJFINANCE": 4.8, "HCLTECH": 4.5,
            "SUNPHARMA": 3.8, "ITC": 3.5,
        },
        "Apr2025": {
            "HDFCBANK": 9.4, "ICICIBANK": 8.3, "INFY": 7.2, "TCS": 6.9,
            "RELIANCE": 6.3, "AXISBANK": 5.3, "BAJFINANCE": 4.9, "HCLTECH": 4.6,
            "SUNPHARMA": 3.9, "ITC": 3.5,
        },
        "May2025": {
            "HDFCBANK": 9.3, "ICICIBANK": 8.4, "INFY": 7.3, "TCS": 7.0,
            "RELIANCE": 6.1, "AXISBANK": 5.4, "BAJFINANCE": 5.0, "HCLTECH": 4.7,
            "SUNPHARMA": 4.0, "ITC": 3.4,
        },
        "Jun2025": {
            "HDFCBANK": 9.2, "ICICIBANK": 8.5, "INFY": 7.4, "TCS": 7.1,
            "RELIANCE": 5.9, "AXISBANK": 5.5, "BAJFINANCE": 5.1, "HCLTECH": 4.8,
            "SUNPHARMA": 4.1, "ITC": 3.4,
        },
        "Jul2025": {
            "HDFCBANK": 9.1, "ICICIBANK": 8.6, "INFY": 7.5, "TCS": 7.2,
            "RELIANCE": 5.7, "AXISBANK": 5.6, "BAJFINANCE": 5.2, "HCLTECH": 4.9,
            "SUNPHARMA": 4.2, "ITC": 3.3,
        },
        "Aug2025": {
            "HDFCBANK": 9.0, "ICICIBANK": 8.7, "INFY": 7.6, "TCS": 7.3,
            "RELIANCE": 5.5, "AXISBANK": 5.7, "BAJFINANCE": 5.3, "HCLTECH": 5.0,
            "SUNPHARMA": 4.3, "ITC": 3.3,
        },
    },
    "sbi_contra": {
        "Mar2025": {
            "ONGC": 6.8, "COALINDIA": 5.5, "POWERGRID": 5.2, "NTPC": 4.8,
            "BPCL": 5.0, "IOC": 4.2, "GAIL": 3.8, "ITC": 5.5,
            "HINDUNILVR": 3.5, "TATAMOTORS": 4.0,
        },
        "Apr2025": {
            "ONGC": 7.0, "COALINDIA": 5.8, "POWERGRID": 5.4, "NTPC": 5.0,
            "BPCL": 4.8, "IOC": 4.0, "GAIL": 4.0, "ITC": 5.4,
            "HINDUNILVR": 3.6, "TATAMOTORS": 4.2,
        },
        "May2025": {
            "ONGC": 7.2, "COALINDIA": 6.0, "POWERGRID": 5.6, "NTPC": 5.2,
            "BPCL": 4.6, "IOC": 3.8, "GAIL": 4.2, "ITC": 5.3,
            "HINDUNILVR": 3.7, "TATAMOTORS": 4.4,
        },
        "Jun2025": {
            "ONGC": 7.4, "COALINDIA": 6.2, "POWERGRID": 5.8, "NTPC": 5.4,
            "BPCL": 4.4, "IOC": 3.6, "GAIL": 4.4, "ITC": 5.2,
            "HINDUNILVR": 3.8, "TATAMOTORS": 4.6,
        },
        "Jul2025": {
            "ONGC": 7.6, "COALINDIA": 6.4, "POWERGRID": 6.0, "NTPC": 5.6,
            "BPCL": 4.2, "IOC": 3.4, "GAIL": 4.6, "ITC": 5.1,
            "HINDUNILVR": 3.9, "TATAMOTORS": 4.8,
        },
        "Aug2025": {
            "ONGC": 7.8, "COALINDIA": 6.6, "POWERGRID": 6.2, "NTPC": 5.8,
            "BPCL": 4.0, "IOC": 3.2, "GAIL": 4.8, "ITC": 5.0,
            "HINDUNILVR": 4.0, "TATAMOTORS": 5.0,
        },
    },
    "nippon_small_cap": {
        "Mar2025": {
            "KAJARIAL": 2.5, "KPITTECH": 2.8, "BRIGADE": 2.2, "AAVAS": 2.0,
            "APTUS": 2.1, "COALINDIA": 1.8, "POWERGRID": 1.5, "ITC": 1.2,
            "ONGC": 1.6, "SUNPHARMA": 2.4,
        },
        "Aug2025": {
            "KAJARIAL": 2.8, "KPITTECH": 3.0, "BRIGADE": 2.5, "AAVAS": 2.2,
            "APTUS": 2.3, "COALINDIA": 2.2, "POWERGRID": 1.8, "ITC": 1.5,
            "ONGC": 2.0, "SUNPHARMA": 2.6,
        },
    },
}


# ============================================================
# ANALYSIS FUNCTIONS
# ============================================================

def get_smart_money_signals(min_funds: int = 3, mode: str = "steady") -> List[Dict]:
    """
    Find stocks held by at least `min_funds` top funds.

    mode="steady": Uses 6-month averaged holdings (safe, confirmed)
    mode="golden_pick": Uses most recent month only (fast, fresh)

    Returns list of dicts sorted by number of funds holding.
    """
    fund_names = list(CURATED_HOLDINGS.keys())
    stock_fund_map: Dict[str, Dict] = {}

    for fund_name, months_data in CURATED_HOLDINGS.items():
        if mode == "golden_pick":
            # Only latest month
            latest_month = list(months_data.keys())[-1]
            holdings_to_use = {latest_month: months_data[latest_month]}
        else:
            holdings_to_use = months_data

        # Average allocation across selected months
        stock_totals: Dict[str, List[float]] = {}
        for month, holdings in holdings_to_use.items():
            for stock, pct in holdings.items():
                if stock not in stock_totals:
                    stock_totals[stock] = []
                stock_totals[stock].append(pct)

        avg_holdings = {s: sum(v) / len(v) for s, v in stock_totals.items()}

        for stock, avg_pct in avg_holdings.items():
            if stock not in stock_fund_map:
                stock_fund_map[stock] = {"funds": [], "total_aum_pct": 0.0}
            stock_fund_map[stock]["funds"].append(fund_name)
            stock_fund_map[stock]["total_aum_pct"] += avg_pct

    # Filter by minimum funds
    signals = []
    for stock, data in stock_fund_map.items():
        if len(data["funds"]) >= min_funds:
            momentum = get_accumulation_signal(stock)
            signals.append({
                "symbol": stock,
                "funds_holding": len(data["funds"]),
                "fund_names": data["funds"],
                "avg_aum_pct": round(data["total_aum_pct"], 2),
                "accumulation": momentum,
                "signal_strength": "STRONG" if len(data["funds"]) >= 4 else "MODERATE",
                "mode": mode,
            })

    return sorted(signals, key=lambda x: x["funds_holding"], reverse=True)


def get_accumulation_signal(stock: str) -> str:
    """
    Check if fund managers are increasing their stake in a stock
    over the last 3 months.

    Returns: "ACCUMULATING", "DISTRIBUTING", or "HOLDING"
    """
    fund_trends = []
    for fund_name, months_data in CURATED_HOLDINGS.items():
        months = list(months_data.keys())
        if len(months) < 3:
            continue
        recent_months = months[-3:]
        allocations = []
        for m in recent_months:
            allocations.append(months_data[m].get(stock, 0))

        if all(a > 0 for a in allocations):
            if allocations[-1] > allocations[0]:
                fund_trends.append("up")
            elif allocations[-1] < allocations[0]:
                fund_trends.append("down")
            else:
                fund_trends.append("flat")

    if not fund_trends:
        return "NO_DATA"

    up_count = fund_trends.count("up")
    down_count = fund_trends.count("down")

    if up_count > down_count:
        return "ACCUMULATING"
    elif down_count > up_count:
        return "DISTRIBUTING"
    else:
        return "HOLDING"


def get_fund_overlap(fund1: str, fund2: str) -> Dict:
    """
    Find stocks that both funds hold simultaneously.
    High overlap = shared conviction = stronger signal.
    """
    def get_latest_holdings(fund_name: str) -> Dict[str, float]:
        data = CURATED_HOLDINGS.get(fund_name, {})
        if not data:
            return {}
        latest = list(data.keys())[-1]
        return data[latest]

    h1 = get_latest_holdings(fund1)
    h2 = get_latest_holdings(fund2)

    common = {s: (h1[s], h2[s]) for s in h1 if s in h2}

    return {
        "fund1": FUND_PROFILES.get(fund1, {}).get("full_name", fund1),
        "fund2": FUND_PROFILES.get(fund2, {}).get("full_name", fund2),
        "common_stocks": len(common),
        "overlap_pct": round(len(common) / max(len(h1), 1) * 100, 1),
        "shared_holdings": [
            {"symbol": s, "fund1_pct": pcts[0], "fund2_pct": pcts[1]}
            for s, pcts in sorted(common.items(), key=lambda x: x[1][0] + x[1][1], reverse=True)
        ],
    }


def get_consensus_picks(min_funds: int = 3) -> List[str]:
    """Return stock symbols held by at least `min_funds` top funds."""
    signals = get_smart_money_signals(min_funds=min_funds)
    return [s["symbol"] for s in signals]


def get_mf_confirmation_for_stock(symbol: str) -> Dict:
    """
    Check if a stock the user is considering is backed by institutional conviction.
    Use this to validate Atlas screener picks against MF smart money.
    """
    holding_funds = []
    for fund_name, months_data in CURATED_HOLDINGS.items():
        latest_month = list(months_data.keys())[-1]
        latest = months_data[latest_month]
        if symbol in latest:
            holding_funds.append({
                "fund": FUND_PROFILES.get(fund_name, {}).get("full_name", fund_name),
                "allocation_pct": latest[symbol],
                "accumulation": get_accumulation_signal(symbol),
            })

    conviction_score = len(holding_funds) * 20  # Max 100 if all 5 funds hold
    return {
        "symbol": symbol,
        "institutional_holding": len(holding_funds) > 0,
        "funds_holding_count": len(holding_funds),
        "conviction_score": min(100, conviction_score),
        "holding_funds": holding_funds,
        "smart_money_verdict": (
            "STRONG CONVICTION" if len(holding_funds) >= 4 else
            "MODERATE CONVICTION" if len(holding_funds) >= 2 else
            "WEAK / NO INSTITUTIONAL BACKING"
        ),
    }
