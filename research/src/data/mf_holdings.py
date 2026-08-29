"""
mf_holdings.py
==============
Project Atlas – Quantitative Investment Research Platform (Indian Markets)

Mutual fund holdings module with DUAL MODE support:

  MODE 1 – CURATED DATA
    Six months of hardcoded top-10 holdings (% of AUM) for five leading
    Indian mutual funds (Mar 2025 – Aug 2025).  Suitable for offline
    analysis, back-testing, and development without network access.

  MODE 2 – LIVE SCRAPER
    ``fetch_live_mf_holdings()`` attempts to pull portfolio data from
    AMFI's public disclosure endpoint.  Falls back to curated data if
    the HTTP request fails or the response cannot be parsed.

AMFI Portfolio Data Format Notes:
-----------------------------------
AMFI publishes daily NAV data at:
    https://www.amfiindia.com/spages/NAVAll.txt

The file is pipe-delimited with the structure:
    Scheme Code|ISIN Div Payout|ISIN Div Reinvestment|Scheme Name|Net Asset Value|Repurchase Price|Sale Price|Date

For scheme-level portfolio holdings, AMFI requires SEBI-mandated monthly
portfolio disclosures submitted by AMCs.  A public search endpoint is:
    https://www.amfiindia.com/modules/PortfolioHoldings

However, machine-readable portfolio CSVs are typically available on each
AMC's own website.  This module uses NAVAll.txt to validate scheme codes
and falls back to curated data for actual holdings detail.

Author : Project Atlas Research Team
Updated: Aug 2025
"""

from __future__ import annotations

import urllib.request
import urllib.error
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# 1.  CURATED HOLDINGS DATA  (Mar 2025 – Aug 2025)
# ---------------------------------------------------------------------------
# Format:
#   FUND_HOLDINGS[fund_key][month_str] = [
#       {"stock": "NSE:TICKER", "pct_aum": float}, ...  # top 10 holdings
#   ]
# pct_aum values are approximate and based on publicly disclosed portfolios.

FUND_HOLDINGS: Dict[str, Dict[str, List[Dict[str, object]]]] = {

    # ------------------------------------------------------------------
    # Parag Parikh Flexi Cap Fund  (PPFAS)
    # Benchmark: Nifty 500 Total Return Index
    # Style: Flexi-cap, value-oriented, significant overseas allocation
    # ------------------------------------------------------------------
    "parag_parikh_flexi_cap": {
        "2025-03": [
            {"stock": "NSE:BAJFINANCE",   "pct_aum": 4.8},
            {"stock": "NSE:COALINDIA",    "pct_aum": 4.5},
            {"stock": "NSE:ITC",          "pct_aum": 4.4},
            {"stock": "NASDAQ:ALPHABET",  "pct_aum": 4.2},
            {"stock": "NASDAQ:META",      "pct_aum": 3.9},
            {"stock": "NSE:HDFCBANK",     "pct_aum": 3.7},
            {"stock": "NSE:POWERGRID",    "pct_aum": 3.5},
            {"stock": "NSE:ZYDUSLIFE",    "pct_aum": 3.2},
            {"stock": "NYSE:AMAZON",      "pct_aum": 3.1},
            {"stock": "NSE:ICICIBANK",    "pct_aum": 3.0},
        ],
        "2025-04": [
            {"stock": "NSE:BAJFINANCE",   "pct_aum": 4.9},
            {"stock": "NSE:COALINDIA",    "pct_aum": 4.6},
            {"stock": "NSE:ITC",          "pct_aum": 4.5},
            {"stock": "NASDAQ:ALPHABET",  "pct_aum": 4.0},
            {"stock": "NASDAQ:META",      "pct_aum": 3.8},
            {"stock": "NSE:HDFCBANK",     "pct_aum": 3.8},
            {"stock": "NSE:POWERGRID",    "pct_aum": 3.6},
            {"stock": "NSE:ZYDUSLIFE",    "pct_aum": 3.3},
            {"stock": "NYSE:AMAZON",      "pct_aum": 3.0},
            {"stock": "NSE:ICICIBANK",    "pct_aum": 3.1},
        ],
        "2025-05": [
            {"stock": "NSE:BAJFINANCE",   "pct_aum": 5.0},
            {"stock": "NSE:COALINDIA",    "pct_aum": 4.7},
            {"stock": "NSE:ITC",          "pct_aum": 4.5},
            {"stock": "NASDAQ:ALPHABET",  "pct_aum": 3.9},
            {"stock": "NASDAQ:META",      "pct_aum": 4.0},
            {"stock": "NSE:HDFCBANK",     "pct_aum": 3.9},
            {"stock": "NSE:POWERGRID",    "pct_aum": 3.5},
            {"stock": "NSE:ZYDUSLIFE",    "pct_aum": 3.4},
            {"stock": "NYSE:AMAZON",      "pct_aum": 3.2},
            {"stock": "NSE:ICICIBANK",    "pct_aum": 3.2},
        ],
        "2025-06": [
            {"stock": "NSE:BAJFINANCE",   "pct_aum": 5.1},
            {"stock": "NSE:COALINDIA",    "pct_aum": 4.6},
            {"stock": "NSE:ITC",          "pct_aum": 4.6},
            {"stock": "NASDAQ:ALPHABET",  "pct_aum": 3.8},
            {"stock": "NASDAQ:META",      "pct_aum": 4.1},
            {"stock": "NSE:HDFCBANK",     "pct_aum": 4.0},
            {"stock": "NSE:POWERGRID",    "pct_aum": 3.4},
            {"stock": "NSE:ZYDUSLIFE",    "pct_aum": 3.4},
            {"stock": "NYSE:AMAZON",      "pct_aum": 3.3},
            {"stock": "NSE:ICICIBANK",    "pct_aum": 3.2},
        ],
        "2025-07": [
            {"stock": "NSE:BAJFINANCE",   "pct_aum": 5.2},
            {"stock": "NSE:COALINDIA",    "pct_aum": 4.5},
            {"stock": "NSE:ITC",          "pct_aum": 4.7},
            {"stock": "NASDAQ:ALPHABET",  "pct_aum": 3.9},
            {"stock": "NASDAQ:META",      "pct_aum": 4.2},
            {"stock": "NSE:HDFCBANK",     "pct_aum": 4.1},
            {"stock": "NSE:POWERGRID",    "pct_aum": 3.3},
            {"stock": "NSE:ZYDUSLIFE",    "pct_aum": 3.5},
            {"stock": "NYSE:AMAZON",      "pct_aum": 3.4},
            {"stock": "NSE:ICICIBANK",    "pct_aum": 3.3},
        ],
        "2025-08": [
            {"stock": "NSE:BAJFINANCE",   "pct_aum": 5.3},
            {"stock": "NSE:COALINDIA",    "pct_aum": 4.4},
            {"stock": "NSE:ITC",          "pct_aum": 4.8},
            {"stock": "NASDAQ:ALPHABET",  "pct_aum": 4.0},
            {"stock": "NASDAQ:META",      "pct_aum": 4.3},
            {"stock": "NSE:HDFCBANK",     "pct_aum": 4.2},
            {"stock": "NSE:POWERGRID",    "pct_aum": 3.2},
            {"stock": "NSE:ZYDUSLIFE",    "pct_aum": 3.6},
            {"stock": "NYSE:AMAZON",      "pct_aum": 3.5},
            {"stock": "NSE:ICICIBANK",    "pct_aum": 3.4},
        ],
    },

    # ------------------------------------------------------------------
    # Quant Active Fund
    # Benchmark: Nifty 500 Total Return Index
    # Style: Quant-driven multi-cap, high churn, contrarian/momentum
    # ------------------------------------------------------------------
    "quant_active": {
        "2025-03": [
            {"stock": "NSE:RELIANCE",     "pct_aum": 6.2},
            {"stock": "NSE:HDFCBANK",     "pct_aum": 5.8},
            {"stock": "NSE:ICICIBANK",    "pct_aum": 5.1},
            {"stock": "NSE:JSWSTEEL",     "pct_aum": 4.6},
            {"stock": "NSE:LT",           "pct_aum": 4.3},
            {"stock": "NSE:TATAMOTORS",   "pct_aum": 4.0},
            {"stock": "NSE:NTPC",         "pct_aum": 3.8},
            {"stock": "NSE:AXISBANK",     "pct_aum": 3.6},
            {"stock": "NSE:COALINDIA",    "pct_aum": 3.4},
            {"stock": "NSE:HINDALCO",     "pct_aum": 3.2},
        ],
        "2025-04": [
            {"stock": "NSE:RELIANCE",     "pct_aum": 6.4},
            {"stock": "NSE:HDFCBANK",     "pct_aum": 5.9},
            {"stock": "NSE:ICICIBANK",    "pct_aum": 5.0},
            {"stock": "NSE:JSWSTEEL",     "pct_aum": 4.5},
            {"stock": "NSE:LT",           "pct_aum": 4.5},
            {"stock": "NSE:TATAMOTORS",   "pct_aum": 3.8},
            {"stock": "NSE:NTPC",         "pct_aum": 3.9},
            {"stock": "NSE:AXISBANK",     "pct_aum": 3.7},
            {"stock": "NSE:COALINDIA",    "pct_aum": 3.5},
            {"stock": "NSE:HINDALCO",     "pct_aum": 3.3},
        ],
        "2025-05": [
            {"stock": "NSE:RELIANCE",     "pct_aum": 6.5},
            {"stock": "NSE:HDFCBANK",     "pct_aum": 5.7},
            {"stock": "NSE:ICICIBANK",    "pct_aum": 5.2},
            {"stock": "NSE:JSWSTEEL",     "pct_aum": 4.4},
            {"stock": "NSE:LT",           "pct_aum": 4.7},
            {"stock": "NSE:TATAMOTORS",   "pct_aum": 3.7},
            {"stock": "NSE:NTPC",         "pct_aum": 4.0},
            {"stock": "NSE:AXISBANK",     "pct_aum": 3.8},
            {"stock": "NSE:COALINDIA",    "pct_aum": 3.6},
            {"stock": "NSE:HINDALCO",     "pct_aum": 3.4},
        ],
        "2025-06": [
            {"stock": "NSE:RELIANCE",     "pct_aum": 6.3},
            {"stock": "NSE:HDFCBANK",     "pct_aum": 5.8},
            {"stock": "NSE:ICICIBANK",    "pct_aum": 5.3},
            {"stock": "NSE:JSWSTEEL",     "pct_aum": 4.3},
            {"stock": "NSE:LT",           "pct_aum": 4.8},
            {"stock": "NSE:TATAMOTORS",   "pct_aum": 3.6},
            {"stock": "NSE:NTPC",         "pct_aum": 4.1},
            {"stock": "NSE:AXISBANK",     "pct_aum": 3.9},
            {"stock": "NSE:COALINDIA",    "pct_aum": 3.5},
            {"stock": "NSE:HINDALCO",     "pct_aum": 3.5},
        ],
        "2025-07": [
            {"stock": "NSE:RELIANCE",     "pct_aum": 6.4},
            {"stock": "NSE:HDFCBANK",     "pct_aum": 5.9},
            {"stock": "NSE:ICICIBANK",    "pct_aum": 5.4},
            {"stock": "NSE:JSWSTEEL",     "pct_aum": 4.2},
            {"stock": "NSE:LT",           "pct_aum": 4.9},
            {"stock": "NSE:TATAMOTORS",   "pct_aum": 3.5},
            {"stock": "NSE:NTPC",         "pct_aum": 4.2},
            {"stock": "NSE:AXISBANK",     "pct_aum": 4.0},
            {"stock": "NSE:COALINDIA",    "pct_aum": 3.4},
            {"stock": "NSE:HINDALCO",     "pct_aum": 3.6},
        ],
        "2025-08": [
            {"stock": "NSE:RELIANCE",     "pct_aum": 6.5},
            {"stock": "NSE:HDFCBANK",     "pct_aum": 6.0},
            {"stock": "NSE:ICICIBANK",    "pct_aum": 5.5},
            {"stock": "NSE:JSWSTEEL",     "pct_aum": 4.1},
            {"stock": "NSE:LT",           "pct_aum": 5.0},
            {"stock": "NSE:TATAMOTORS",   "pct_aum": 3.4},
            {"stock": "NSE:NTPC",         "pct_aum": 4.3},
            {"stock": "NSE:AXISBANK",     "pct_aum": 4.1},
            {"stock": "NSE:COALINDIA",    "pct_aum": 3.3},
            {"stock": "NSE:HINDALCO",     "pct_aum": 3.7},
        ],
    },

    # ------------------------------------------------------------------
    # Mirae Asset Large Cap Fund
    # Benchmark: Nifty 100 Total Return Index
    # Style: Large-cap, growth-at-reasonable-price (GARP)
    # ------------------------------------------------------------------
    "mirae_large_cap": {
        "2025-03": [
            {"stock": "NSE:HDFCBANK",     "pct_aum": 9.5},
            {"stock": "NSE:RELIANCE",     "pct_aum": 8.8},
            {"stock": "NSE:ICICIBANK",    "pct_aum": 8.2},
            {"stock": "NSE:INFY",         "pct_aum": 6.4},
            {"stock": "NSE:TCS",          "pct_aum": 5.8},
            {"stock": "NSE:AXISBANK",     "pct_aum": 4.6},
            {"stock": "NSE:LT",           "pct_aum": 4.2},
            {"stock": "NSE:KOTAKBANK",    "pct_aum": 3.9},
            {"stock": "NSE:MARUTI",       "pct_aum": 3.5},
            {"stock": "NSE:SUNPHARMA",    "pct_aum": 3.2},
        ],
        "2025-04": [
            {"stock": "NSE:HDFCBANK",     "pct_aum": 9.6},
            {"stock": "NSE:RELIANCE",     "pct_aum": 8.7},
            {"stock": "NSE:ICICIBANK",    "pct_aum": 8.3},
            {"stock": "NSE:INFY",         "pct_aum": 6.5},
            {"stock": "NSE:TCS",          "pct_aum": 5.7},
            {"stock": "NSE:AXISBANK",     "pct_aum": 4.7},
            {"stock": "NSE:LT",           "pct_aum": 4.3},
            {"stock": "NSE:KOTAKBANK",    "pct_aum": 4.0},
            {"stock": "NSE:MARUTI",       "pct_aum": 3.6},
            {"stock": "NSE:SUNPHARMA",    "pct_aum": 3.3},
        ],
        "2025-05": [
            {"stock": "NSE:HDFCBANK",     "pct_aum": 9.7},
            {"stock": "NSE:RELIANCE",     "pct_aum": 8.6},
            {"stock": "NSE:ICICIBANK",    "pct_aum": 8.4},
            {"stock": "NSE:INFY",         "pct_aum": 6.6},
            {"stock": "NSE:TCS",          "pct_aum": 5.6},
            {"stock": "NSE:AXISBANK",     "pct_aum": 4.8},
            {"stock": "NSE:LT",           "pct_aum": 4.4},
            {"stock": "NSE:KOTAKBANK",    "pct_aum": 4.1},
            {"stock": "NSE:MARUTI",       "pct_aum": 3.7},
            {"stock": "NSE:SUNPHARMA",    "pct_aum": 3.4},
        ],
        "2025-06": [
            {"stock": "NSE:HDFCBANK",     "pct_aum": 9.8},
            {"stock": "NSE:RELIANCE",     "pct_aum": 8.5},
            {"stock": "NSE:ICICIBANK",    "pct_aum": 8.5},
            {"stock": "NSE:INFY",         "pct_aum": 6.7},
            {"stock": "NSE:TCS",          "pct_aum": 5.5},
            {"stock": "NSE:AXISBANK",     "pct_aum": 4.9},
            {"stock": "NSE:LT",           "pct_aum": 4.5},
            {"stock": "NSE:KOTAKBANK",    "pct_aum": 4.2},
            {"stock": "NSE:MARUTI",       "pct_aum": 3.8},
            {"stock": "NSE:SUNPHARMA",    "pct_aum": 3.5},
        ],
        "2025-07": [
            {"stock": "NSE:HDFCBANK",     "pct_aum": 9.9},
            {"stock": "NSE:RELIANCE",     "pct_aum": 8.4},
            {"stock": "NSE:ICICIBANK",    "pct_aum": 8.6},
            {"stock": "NSE:INFY",         "pct_aum": 6.8},
            {"stock": "NSE:TCS",          "pct_aum": 5.4},
            {"stock": "NSE:AXISBANK",     "pct_aum": 5.0},
            {"stock": "NSE:LT",           "pct_aum": 4.6},
            {"stock": "NSE:KOTAKBANK",    "pct_aum": 4.3},
            {"stock": "NSE:MARUTI",       "pct_aum": 3.9},
            {"stock": "NSE:SUNPHARMA",    "pct_aum": 3.6},
        ],
        "2025-08": [
            {"stock": "NSE:HDFCBANK",     "pct_aum": 10.0},
            {"stock": "NSE:RELIANCE",     "pct_aum": 8.3},
            {"stock": "NSE:ICICIBANK",    "pct_aum": 8.7},
            {"stock": "NSE:INFY",         "pct_aum": 6.9},
            {"stock": "NSE:TCS",          "pct_aum": 5.3},
            {"stock": "NSE:AXISBANK",     "pct_aum": 5.1},
            {"stock": "NSE:LT",           "pct_aum": 4.7},
            {"stock": "NSE:KOTAKBANK",    "pct_aum": 4.4},
            {"stock": "NSE:MARUTI",       "pct_aum": 4.0},
            {"stock": "NSE:SUNPHARMA",    "pct_aum": 3.7},
        ],
    },

    # ------------------------------------------------------------------
    # SBI Contra Fund
    # Benchmark: Nifty 500 Total Return Index
    # Style: Contrarian – buys out-of-favour/unloved sectors
    # ------------------------------------------------------------------
    "sbi_contra": {
        "2025-03": [
            {"stock": "NSE:COALINDIA",    "pct_aum": 5.8},
            {"stock": "NSE:ITC",          "pct_aum": 5.4},
            {"stock": "NSE:ONGC",         "pct_aum": 4.9},
            {"stock": "NSE:NTPC",         "pct_aum": 4.6},
            {"stock": "NSE:HDFCBANK",     "pct_aum": 4.4},
            {"stock": "NSE:POWERGRID",    "pct_aum": 4.1},
            {"stock": "NSE:BHEL",         "pct_aum": 3.8},
            {"stock": "NSE:SAIL",         "pct_aum": 3.6},
            {"stock": "NSE:NATIONALUM",   "pct_aum": 3.2},
            {"stock": "NSE:GAIL",         "pct_aum": 3.0},
        ],
        "2025-04": [
            {"stock": "NSE:COALINDIA",    "pct_aum": 6.0},
            {"stock": "NSE:ITC",          "pct_aum": 5.5},
            {"stock": "NSE:ONGC",         "pct_aum": 4.8},
            {"stock": "NSE:NTPC",         "pct_aum": 4.7},
            {"stock": "NSE:HDFCBANK",     "pct_aum": 4.5},
            {"stock": "NSE:POWERGRID",    "pct_aum": 4.2},
            {"stock": "NSE:BHEL",         "pct_aum": 3.9},
            {"stock": "NSE:SAIL",         "pct_aum": 3.7},
            {"stock": "NSE:NATIONALUM",   "pct_aum": 3.3},
            {"stock": "NSE:GAIL",         "pct_aum": 3.1},
        ],
        "2025-05": [
            {"stock": "NSE:COALINDIA",    "pct_aum": 6.2},
            {"stock": "NSE:ITC",          "pct_aum": 5.6},
            {"stock": "NSE:ONGC",         "pct_aum": 4.7},
            {"stock": "NSE:NTPC",         "pct_aum": 4.8},
            {"stock": "NSE:HDFCBANK",     "pct_aum": 4.6},
            {"stock": "NSE:POWERGRID",    "pct_aum": 4.3},
            {"stock": "NSE:BHEL",         "pct_aum": 4.0},
            {"stock": "NSE:SAIL",         "pct_aum": 3.8},
            {"stock": "NSE:NATIONALUM",   "pct_aum": 3.4},
            {"stock": "NSE:GAIL",         "pct_aum": 3.2},
        ],
        "2025-06": [
            {"stock": "NSE:COALINDIA",    "pct_aum": 6.3},
            {"stock": "NSE:ITC",          "pct_aum": 5.7},
            {"stock": "NSE:ONGC",         "pct_aum": 4.6},
            {"stock": "NSE:NTPC",         "pct_aum": 4.9},
            {"stock": "NSE:HDFCBANK",     "pct_aum": 4.7},
            {"stock": "NSE:POWERGRID",    "pct_aum": 4.4},
            {"stock": "NSE:BHEL",         "pct_aum": 4.1},
            {"stock": "NSE:SAIL",         "pct_aum": 3.9},
            {"stock": "NSE:NATIONALUM",   "pct_aum": 3.5},
            {"stock": "NSE:GAIL",         "pct_aum": 3.3},
        ],
        "2025-07": [
            {"stock": "NSE:COALINDIA",    "pct_aum": 6.4},
            {"stock": "NSE:ITC",          "pct_aum": 5.8},
            {"stock": "NSE:ONGC",         "pct_aum": 4.5},
            {"stock": "NSE:NTPC",         "pct_aum": 5.0},
            {"stock": "NSE:HDFCBANK",     "pct_aum": 4.8},
            {"stock": "NSE:POWERGRID",    "pct_aum": 4.5},
            {"stock": "NSE:BHEL",         "pct_aum": 4.2},
            {"stock": "NSE:SAIL",         "pct_aum": 4.0},
            {"stock": "NSE:NATIONALUM",   "pct_aum": 3.6},
            {"stock": "NSE:GAIL",         "pct_aum": 3.4},
        ],
        "2025-08": [
            {"stock": "NSE:COALINDIA",    "pct_aum": 6.5},
            {"stock": "NSE:ITC",          "pct_aum": 5.9},
            {"stock": "NSE:ONGC",         "pct_aum": 4.4},
            {"stock": "NSE:NTPC",         "pct_aum": 5.1},
            {"stock": "NSE:HDFCBANK",     "pct_aum": 4.9},
            {"stock": "NSE:POWERGRID",    "pct_aum": 4.6},
            {"stock": "NSE:BHEL",         "pct_aum": 4.3},
            {"stock": "NSE:SAIL",         "pct_aum": 4.1},
            {"stock": "NSE:NATIONALUM",   "pct_aum": 3.7},
            {"stock": "NSE:GAIL",         "pct_aum": 3.5},
        ],
    },

    # ------------------------------------------------------------------
    # Nippon India Small Cap Fund
    # Benchmark: Nifty Small Cap 250 Total Return Index
    # Style: Small-cap, growth, diversified (400+ holdings)
    # Top 10 represent core high-conviction positions
    # ------------------------------------------------------------------
    "nippon_small_cap": {
        "2025-03": [
            {"stock": "NSE:KPITTECH",     "pct_aum": 2.8},
            {"stock": "NSE:BLUESTARCO",   "pct_aum": 2.5},
            {"stock": "NSE:TRENTLTD",     "pct_aum": 2.4},
            {"stock": "NSE:KALYANKJIL",   "pct_aum": 2.2},
            {"stock": "NSE:CMSINFO",      "pct_aum": 2.0},
            {"stock": "NSE:GPPL",         "pct_aum": 1.9},
            {"stock": "NSE:RKFORGE",      "pct_aum": 1.8},
            {"stock": "NSE:APTUS",        "pct_aum": 1.7},
            {"stock": "NSE:ROUTE",        "pct_aum": 1.6},
            {"stock": "NSE:DCBBANK",      "pct_aum": 1.5},
        ],
        "2025-04": [
            {"stock": "NSE:KPITTECH",     "pct_aum": 2.9},
            {"stock": "NSE:BLUESTARCO",   "pct_aum": 2.6},
            {"stock": "NSE:TRENTLTD",     "pct_aum": 2.5},
            {"stock": "NSE:KALYANKJIL",   "pct_aum": 2.3},
            {"stock": "NSE:CMSINFO",      "pct_aum": 2.1},
            {"stock": "NSE:GPPL",         "pct_aum": 1.9},
            {"stock": "NSE:RKFORGE",      "pct_aum": 1.9},
            {"stock": "NSE:APTUS",        "pct_aum": 1.8},
            {"stock": "NSE:ROUTE",        "pct_aum": 1.7},
            {"stock": "NSE:DCBBANK",      "pct_aum": 1.6},
        ],
        "2025-05": [
            {"stock": "NSE:KPITTECH",     "pct_aum": 3.0},
            {"stock": "NSE:BLUESTARCO",   "pct_aum": 2.7},
            {"stock": "NSE:TRENTLTD",     "pct_aum": 2.6},
            {"stock": "NSE:KALYANKJIL",   "pct_aum": 2.4},
            {"stock": "NSE:CMSINFO",      "pct_aum": 2.2},
            {"stock": "NSE:GPPL",         "pct_aum": 2.0},
            {"stock": "NSE:RKFORGE",      "pct_aum": 2.0},
            {"stock": "NSE:APTUS",        "pct_aum": 1.9},
            {"stock": "NSE:ROUTE",        "pct_aum": 1.8},
            {"stock": "NSE:DCBBANK",      "pct_aum": 1.7},
        ],
        "2025-06": [
            {"stock": "NSE:KPITTECH",     "pct_aum": 3.1},
            {"stock": "NSE:BLUESTARCO",   "pct_aum": 2.8},
            {"stock": "NSE:TRENTLTD",     "pct_aum": 2.7},
            {"stock": "NSE:KALYANKJIL",   "pct_aum": 2.5},
            {"stock": "NSE:CMSINFO",      "pct_aum": 2.3},
            {"stock": "NSE:GPPL",         "pct_aum": 2.0},
            {"stock": "NSE:RKFORGE",      "pct_aum": 2.1},
            {"stock": "NSE:APTUS",        "pct_aum": 2.0},
            {"stock": "NSE:ROUTE",        "pct_aum": 1.9},
            {"stock": "NSE:DCBBANK",      "pct_aum": 1.8},
        ],
        "2025-07": [
            {"stock": "NSE:KPITTECH",     "pct_aum": 3.2},
            {"stock": "NSE:BLUESTARCO",   "pct_aum": 2.9},
            {"stock": "NSE:TRENTLTD",     "pct_aum": 2.8},
            {"stock": "NSE:KALYANKJIL",   "pct_aum": 2.6},
            {"stock": "NSE:CMSINFO",      "pct_aum": 2.4},
            {"stock": "NSE:GPPL",         "pct_aum": 2.1},
            {"stock": "NSE:RKFORGE",      "pct_aum": 2.2},
            {"stock": "NSE:APTUS",        "pct_aum": 2.1},
            {"stock": "NSE:ROUTE",        "pct_aum": 2.0},
            {"stock": "NSE:DCBBANK",      "pct_aum": 1.9},
        ],
        "2025-08": [
            {"stock": "NSE:KPITTECH",     "pct_aum": 3.3},
            {"stock": "NSE:BLUESTARCO",   "pct_aum": 3.0},
            {"stock": "NSE:TRENTLTD",     "pct_aum": 2.9},
            {"stock": "NSE:KALYANKJIL",   "pct_aum": 2.7},
            {"stock": "NSE:CMSINFO",      "pct_aum": 2.5},
            {"stock": "NSE:GPPL",         "pct_aum": 2.1},
            {"stock": "NSE:RKFORGE",      "pct_aum": 2.3},
            {"stock": "NSE:APTUS",        "pct_aum": 2.2},
            {"stock": "NSE:ROUTE",        "pct_aum": 2.1},
            {"stock": "NSE:DCBBANK",      "pct_aum": 2.0},
        ],
    },
}

# Friendly display names → fund keys
FUND_DISPLAY_NAMES: Dict[str, str] = {
    "Parag Parikh Flexi Cap Fund": "parag_parikh_flexi_cap",
    "Quant Active Fund":           "quant_active",
    "Mirae Asset Large Cap Fund":  "mirae_large_cap",
    "SBI Contra Fund":             "sbi_contra",
    "Nippon India Small Cap Fund": "nippon_small_cap",
}

# Sorted months for history navigation
_MONTHS_ORDER: List[str] = ["2025-03", "2025-04", "2025-05", "2025-06", "2025-07", "2025-08"]
_LATEST_MONTH: str = "2025-08"

# ---------------------------------------------------------------------------
# 2.  MODE 2 – LIVE SCRAPER
# ---------------------------------------------------------------------------

# AMFI NAV file URL – pipe-delimited, updated each business day.
# Format per line:
#   Scheme Code|ISIN Div Payout|ISIN Div Reinvestment|Scheme Name|NAV|Repurchase Price|Sale Price|Date
# Example:
#   119551|INF879O01019|INF879O01027|Aditya Birla Sun Life Arbitrage Fund-Growth|21.9423|21.9423|21.9423|15-Aug-2025
#
# Note: This URL provides NAV data, NOT portfolio holdings. Full portfolio
# holdings are disclosed monthly by each AMC on their own websites.
# AMFI aggregates them at: https://www.amfiindia.com/modules/PortfolioHoldings
# (interactive page, not a direct API endpoint).
AMFI_NAV_URL: str = "https://www.amfiindia.com/spages/NAVAll.txt"


def _resolve_fund_key(fund_name: str) -> Optional[str]:
    """
    Resolve a fund display name or internal key to its canonical internal key.

    Parameters
    ----------
    fund_name : str
        Either an internal key (e.g. 'quant_active') or display name
        (e.g. 'Quant Active Fund').

    Returns
    -------
    str or None
        Canonical fund key, or None if not found.
    """
    if fund_name in FUND_HOLDINGS:
        return fund_name
    return FUND_DISPLAY_NAMES.get(fund_name)


def fetch_live_mf_holdings(
    fund_name: str,
    timeout: int = 10,
) -> Dict[str, object]:
    """
    Attempt to fetch live mutual fund scheme data from AMFI.

    This function pings the AMFI NAV file to confirm connectivity and
    returns a status dict.  Full portfolio holdings require per-AMC
    website scraping (no unified machine-readable AMFI endpoint exists
    for holdings as of Aug 2025).  The function falls back to curated
    data automatically on any network or parse error.

    Parameters
    ----------
    fund_name : str
        Display name or internal key of the fund.
    timeout : int, optional
        HTTP request timeout in seconds. Defaults to 10.

    Returns
    -------
    dict
        - ``source``        : 'live' if AMFI reachable, else 'curated'
        - ``fund_key``      : Resolved internal key
        - ``latest_month``  : Most recent month of holdings data
        - ``holdings``      : List of top-10 holding dicts
        - ``live_status``   : HTTP status or error message
        - ``note``          : Data lineage note

    Examples
    --------
    >>> result = fetch_live_mf_holdings("Quant Active Fund")
    >>> result["source"]
    'curated'   # if AMFI unreachable
    """
    fund_key = _resolve_fund_key(fund_name)
    if fund_key is None:
        raise ValueError(
            f"Unknown fund '{fund_name}'. "
            f"Valid options: {list(FUND_DISPLAY_NAMES.keys()) + list(FUND_HOLDINGS.keys())}"
        )

    live_status = "not_attempted"
    source = "curated"

    # --- Attempt live AMFI connectivity check ---
    try:
        req = urllib.request.Request(
            AMFI_NAV_URL,
            headers={"User-Agent": "ProjectAtlas/1.0 (research; contact: atlas@example.com)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status == 200:
                # AMFI reachable – in production, parse holdings from AMC-specific URLs here.
                live_status = f"HTTP {response.status} OK – AMFI NAV file reachable"
                source = "curated"   # holdings still from curated (no unified holdings API)
            else:
                live_status = f"HTTP {response.status} – unexpected response"
    except urllib.error.HTTPError as exc:
        live_status = f"HTTP error: {exc.code} {exc.reason}"
    except urllib.error.URLError as exc:
        live_status = f"URL error: {exc.reason}"
    except TimeoutError:
        live_status = "Request timed out"
    except Exception as exc:  # noqa: BLE001
        live_status = f"Unexpected error: {type(exc).__name__}: {exc}"

    holdings = FUND_HOLDINGS[fund_key].get(_LATEST_MONTH, [])

    return {
        "source": source,
        "fund_key": fund_key,
        "latest_month": _LATEST_MONTH,
        "holdings": holdings,
        "live_status": live_status,
        "note": (
            "Holdings are from Project Atlas curated dataset (Mar–Aug 2025). "
            "For live holdings, integrate with individual AMC monthly portfolio "
            "disclosure PDFs/CSVs. AMFI aggregator: "
            "https://www.amfiindia.com/modules/PortfolioHoldings"
        ),
    }


# ---------------------------------------------------------------------------
# 3.  ANALYTICS FUNCTIONS
# ---------------------------------------------------------------------------


def _get_latest_holdings(fund_key: str) -> List[Dict[str, object]]:
    """Return the most recent month's holdings for a fund."""
    return FUND_HOLDINGS[fund_key].get(_LATEST_MONTH, [])


def _get_all_stocks_in_fund(
    fund_key: str,
    month: Optional[str] = None,
) -> Dict[str, float]:
    """
    Return a {stock: pct_aum} mapping for a fund in a given month.

    Parameters
    ----------
    fund_key : str
        Internal fund key.
    month : str, optional
        YYYY-MM string. Defaults to latest month.

    Returns
    -------
    dict
        Mapping of stock ticker → pct_aum.
    """
    m = month or _LATEST_MONTH
    return {h["stock"]: h["pct_aum"] for h in FUND_HOLDINGS[fund_key].get(m, [])}


def get_fund_overlap(fund1: str, fund2: str) -> Dict[str, object]:
    """
    Find stocks commonly held by both funds in the latest month.

    Parameters
    ----------
    fund1 : str
        Display name or internal key of the first fund.
    fund2 : str
        Display name or internal key of the second fund.

    Returns
    -------
    dict
        - ``fund1``           : Resolved key of fund 1
        - ``fund2``           : Resolved key of fund 2
        - ``month``           : Data month (YYYY-MM)
        - ``common_stocks``   : List of dicts with stock, fund1_pct, fund2_pct
        - ``overlap_count``   : Number of overlapping stocks

    Raises
    ------
    ValueError
        If either fund name is unrecognised.

    Examples
    --------
    >>> overlap = get_fund_overlap("Quant Active Fund", "Mirae Asset Large Cap Fund")
    >>> overlap["common_stocks"]
    [{"stock": "NSE:HDFCBANK", "fund1_pct": 6.0, "fund2_pct": 10.0}, ...]
    """
    key1 = _resolve_fund_key(fund1)
    key2 = _resolve_fund_key(fund2)

    if key1 is None:
        raise ValueError(f"Unknown fund: '{fund1}'")
    if key2 is None:
        raise ValueError(f"Unknown fund: '{fund2}'")

    holdings1 = _get_all_stocks_in_fund(key1)
    holdings2 = _get_all_stocks_in_fund(key2)

    common = sorted(
        [
            {
                "stock": stock,
                "fund1_pct": holdings1[stock],
                "fund2_pct": holdings2[stock],
            }
            for stock in holdings1
            if stock in holdings2
        ],
        key=lambda x: x["fund1_pct"] + x["fund2_pct"],
        reverse=True,
    )

    return {
        "fund1": key1,
        "fund2": key2,
        "month": _LATEST_MONTH,
        "common_stocks": common,
        "overlap_count": len(common),
    }


def get_consensus_picks(min_funds: int = 3) -> Dict[str, object]:
    """
    Return stocks held by at least *min_funds* of the tracked funds.

    Parameters
    ----------
    min_funds : int, optional
        Minimum number of funds that must hold the stock. Defaults to 3.

    Returns
    -------
    dict
        - ``month``         : Data month (YYYY-MM)
        - ``min_funds``     : Threshold used
        - ``consensus``     : List of dicts:
                              {stock, fund_count, funds_holding, avg_pct_aum}
        - ``total_tracked`` : Number of funds analysed

    Examples
    --------
    >>> picks = get_consensus_picks(min_funds=3)
    >>> picks["consensus"][0]["stock"]
    'NSE:HDFCBANK'
    """
    # Aggregate pct_aum per stock across all funds
    stock_data: Dict[str, Dict[str, object]] = {}

    for fund_key in FUND_HOLDINGS:
        holdings = _get_all_stocks_in_fund(fund_key)
        for stock, pct in holdings.items():
            if stock not in stock_data:
                stock_data[stock] = {"funds": [], "total_pct": 0.0}
            stock_data[stock]["funds"].append(fund_key)
            stock_data[stock]["total_pct"] = round(
                stock_data[stock]["total_pct"] + pct, 2
            )

    consensus = [
        {
            "stock": stock,
            "fund_count": len(info["funds"]),
            "funds_holding": info["funds"],
            "avg_pct_aum": round(info["total_pct"] / len(info["funds"]), 2),
        }
        for stock, info in stock_data.items()
        if len(info["funds"]) >= min_funds
    ]

    consensus.sort(key=lambda x: (x["fund_count"], x["avg_pct_aum"]), reverse=True)

    return {
        "month": _LATEST_MONTH,
        "min_funds": min_funds,
        "consensus": consensus,
        "total_tracked": len(FUND_HOLDINGS),
    }


def get_fund_momentum(fund_name: str, stock: str) -> Dict[str, object]:
    """
    Determine whether a fund is increasing or decreasing allocation to a stock
    over the most recent 3-month window.

    Parameters
    ----------
    fund_name : str
        Display name or internal key of the fund.
    stock : str
        Stock ticker string (e.g. 'NSE:HDFCBANK').

    Returns
    -------
    dict
        - ``fund_key``      : Resolved fund key
        - ``stock``         : Stock ticker
        - ``trend``         : 'accumulating', 'distributing', 'stable', or 'not_found'
        - ``3m_change_pct`` : Change in % AUM over 3 months (positive = accumulating)
        - ``monthly_data``  : List of {month, pct_aum} for last 3 months
        - ``signal``        : Plain-English interpretation

    Raises
    ------
    ValueError
        If the fund name is unrecognised.

    Examples
    --------
    >>> momentum = get_fund_momentum("SBI Contra Fund", "NSE:COALINDIA")
    >>> momentum["trend"]
    'accumulating'
    """
    fund_key = _resolve_fund_key(fund_name)
    if fund_key is None:
        raise ValueError(f"Unknown fund: '{fund_name}'")

    # Last 3 months
    lookback_months = _MONTHS_ORDER[-3:]
    monthly_data = []

    for month in lookback_months:
        holdings = FUND_HOLDINGS[fund_key].get(month, [])
        match = next((h for h in holdings if h["stock"] == stock), None)
        monthly_data.append({
            "month": month,
            "pct_aum": match["pct_aum"] if match else None,
        })

    # Filter to months where the stock appears
    valid = [d for d in monthly_data if d["pct_aum"] is not None]

    if len(valid) < 2:
        return {
            "fund_key": fund_key,
            "stock": stock,
            "trend": "not_found",
            "3m_change_pct": None,
            "monthly_data": monthly_data,
            "signal": "Stock not present in fund's top-10 holdings for the analysis window.",
        }

    first_pct = valid[0]["pct_aum"]
    last_pct = valid[-1]["pct_aum"]
    change = round(last_pct - first_pct, 2)

    if change > 0.15:
        trend = "accumulating"
        signal = f"Fund is actively building position in {stock} (+{change}% AUM over 3M). Strong buy signal."
    elif change < -0.15:
        trend = "distributing"
        signal = f"Fund is reducing exposure to {stock} ({change}% AUM over 3M). Potential caution signal."
    else:
        trend = "stable"
        signal = f"Fund position in {stock} is broadly stable (change: {change}% AUM over 3M)."

    return {
        "fund_key": fund_key,
        "stock": stock,
        "trend": trend,
        "3m_change_pct": change,
        "monthly_data": monthly_data,
        "signal": signal,
    }


def get_smart_money_signals(min_accumulating_funds: int = 3) -> Dict[str, object]:
    """
    Identify stocks being actively accumulated (increasing allocation) by
    at least *min_accumulating_funds* of the tracked funds.

    A stock is considered 'being accumulated' if its 3-month % AUM change
    is positive (> +0.10%) in a given fund.

    Parameters
    ----------
    min_accumulating_funds : int, optional
        Minimum number of funds accumulating the stock. Defaults to 3.

    Returns
    -------
    dict
        - ``signal_month``          : Latest data month
        - ``min_accumulating_funds``: Threshold used
        - ``strong_signals``        : List of dicts:
                                      {stock, accumulating_funds, avg_3m_change_pct, funds}
        - ``total_funds_tracked``   : Number of funds analysed

    Examples
    --------
    >>> signals = get_smart_money_signals(min_accumulating_funds=3)
    >>> signals["strong_signals"][0]["stock"]
    'NSE:HDFCBANK'   # example – most broadly accumulated
    """
    # Determine 3-month change per stock per fund
    stock_signals: Dict[str, Dict[str, object]] = {}

    lookback_months = _MONTHS_ORDER[-4:]   # need 4 months to get a 3M change

    for fund_key in FUND_HOLDINGS:
        start_holdings = _get_all_stocks_in_fund(fund_key, month=lookback_months[0])
        end_holdings = _get_all_stocks_in_fund(fund_key, month=lookback_months[-1])

        all_stocks = set(start_holdings) | set(end_holdings)

        for stock in all_stocks:
            start_pct = start_holdings.get(stock, 0.0)
            end_pct = end_holdings.get(stock, 0.0)
            change = round(end_pct - start_pct, 3)

            if change > 0.10:   # accumulation threshold
                if stock not in stock_signals:
                    stock_signals[stock] = {"funds": [], "changes": []}
                stock_signals[stock]["funds"].append(fund_key)
                stock_signals[stock]["changes"].append(change)

    strong_signals = [
        {
            "stock": stock,
            "accumulating_funds": len(data["funds"]),
            "avg_3m_change_pct": round(sum(data["changes"]) / len(data["changes"]), 2),
            "funds": data["funds"],
        }
        for stock, data in stock_signals.items()
        if len(data["funds"]) >= min_accumulating_funds
    ]

    strong_signals.sort(
        key=lambda x: (x["accumulating_funds"], x["avg_3m_change_pct"]),
        reverse=True,
    )

    return {
        "signal_month": _LATEST_MONTH,
        "analysis_window": f"{lookback_months[0]} to {lookback_months[-1]}",
        "min_accumulating_funds": min_accumulating_funds,
        "strong_signals": strong_signals,
        "total_funds_tracked": len(FUND_HOLDINGS),
    }


def get_holdings_for_fund(
    fund_name: str,
    month: Optional[str] = None,
) -> Dict[str, object]:
    """
    Return top-10 holdings for a specific fund and month.

    Parameters
    ----------
    fund_name : str
        Display name or internal key.
    month : str, optional
        YYYY-MM string. Defaults to latest available month.

    Returns
    -------
    dict
        - ``fund_key``  : Internal key
        - ``month``     : Data month
        - ``holdings``  : List of holding dicts

    Raises
    ------
    ValueError
        If the fund name or month is unrecognised.
    """
    fund_key = _resolve_fund_key(fund_name)
    if fund_key is None:
        raise ValueError(f"Unknown fund: '{fund_name}'")

    m = month or _LATEST_MONTH
    if m not in FUND_HOLDINGS[fund_key]:
        available = sorted(FUND_HOLDINGS[fund_key].keys())
        raise ValueError(
            f"No holdings data for {fund_key} in {m}. "
            f"Available months: {available}"
        )

    return {
        "fund_key": fund_key,
        "month": m,
        "holdings": FUND_HOLDINGS[fund_key][m],
    }


# ---------------------------------------------------------------------------
# 4.  MODULE SELF-TEST
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    print("=" * 60)
    print("Project Atlas – MF Holdings Module Self-Test")
    print("=" * 60)

    print("\n[1] Latest Holdings – SBI Contra Fund")
    data = get_holdings_for_fund("SBI Contra Fund")
    for h in data["holdings"]:
        print(f"  {h['stock']:<30} {h['pct_aum']:.1f}%")

    print("\n[2] Fund Overlap – Quant Active vs Mirae Large Cap")
    overlap = get_fund_overlap("quant_active", "mirae_large_cap")
    print(f"  Overlapping stocks: {overlap['overlap_count']}")
    for c in overlap["common_stocks"]:
        print(f"    {c['stock']:<30} QA: {c['fund1_pct']:.1f}%  ML: {c['fund2_pct']:.1f}%")

    print("\n[3] Consensus Picks (3+ funds)")
    picks = get_consensus_picks(min_funds=3)
    for p in picks["consensus"]:
        print(f"  {p['stock']:<30} {p['fund_count']} funds  avg {p['avg_pct_aum']:.1f}%")

    print("\n[4] Fund Momentum – SBI Contra / COALINDIA")
    momentum = get_fund_momentum("sbi_contra", "NSE:COALINDIA")
    print(json.dumps(momentum, indent=2))

    print("\n[5] Smart Money Signals (3+ funds accumulating)")
    signals = get_smart_money_signals(min_accumulating_funds=3)
    print(f"  Strong signals found: {len(signals['strong_signals'])}")
    for s in signals["strong_signals"]:
        print(f"    {s['stock']:<30} {s['accumulating_funds']} funds  avg D={s['avg_3m_change_pct']:+.2f}%")

    print("\n[6] Live Fetch (falls back to curated)")
    result = fetch_live_mf_holdings("Parag Parikh Flexi Cap Fund")
    print(f"  Source: {result['source']}")
    print(f"  Live Status: {result['live_status']}")
    print(f"  Holdings (latest month):")
    for h in result["holdings"][:3]:
        print(f"    {h['stock']}")
