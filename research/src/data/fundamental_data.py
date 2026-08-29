"""
Project Atlas - Fundamental Data for Top NSE Stocks
=====================================================
Curated fundamental data for top NIFTY50 stocks.
Data sourced from: screener.in, NSE, company annual reports.
Last updated: August 2025

Fields per company:
    - sector
    - market_cap_cr: Market Cap in Crores (INR)
    - current_price: CMP in INR
    - pe_ratio: Price to Earnings
    - pb_ratio: Price to Book
    - dividend_yield_pct: Annual Dividend Yield %
    - roe_pct: Return on Equity %
    - roce_pct: Return on Capital Employed %
    - debt_to_equity: Debt / Equity ratio
    - net_profit_margin_pct: Net Profit Margin %
    - interest_coverage: EBIT / Interest Expense
    - current_ratio: Current Assets / Current Liabilities
    - payout_ratio_pct: Dividend Payout Ratio %
    - promoter_holding_pct: Promoter Shareholding %
    - revenue_cagr_5y: 5-Year Revenue CAGR %
    - profit_cagr_5y: 5-Year Net Profit CAGR %
    - dividend_cagr_5y: 5-Year Dividend Per Share CAGR %
    - dividend_history: Annual DPS (INR) - oldest to newest (10 years)
    - years_consecutive_dividend: Years of uninterrupted dividend payout
    - fcf_yield_pct: Free Cash Flow Yield %
    - piotroski_f_score: Pre-calculated Piotroski F-Score (0-9)
    - isin: NSE ISIN code
"""

STOCK_FUNDAMENTALS = {

    # ===== FMCG / CONSUMER =====

    "ITC": {
        "sector": "FMCG",
        "market_cap_cr": 560000,
        "current_price": 449,
        "pe_ratio": 27.5,
        "pb_ratio": 8.2,
        "dividend_yield_pct": 3.2,
        "roe_pct": 29.4,
        "roce_pct": 38.2,
        "debt_to_equity": 0.0,   # Debt-free!
        "net_profit_margin_pct": 27.8,
        "interest_coverage": 999, # Effectively infinite (no debt)
        "current_ratio": 2.8,
        "payout_ratio_pct": 88.0,
        "promoter_holding_pct": 0.0,  # No promoter (widely held MNC)
        "revenue_cagr_5y": 10.2,
        "profit_cagr_5y": 14.5,
        "dividend_cagr_5y": 18.3,
        "dividend_history": [3.25, 4.75, 5.75, 5.0, 10.0, 11.0, 12.5, 14.75, 15.5, 14.5],
        "years_consecutive_dividend": 10,
        "fcf_yield_pct": 4.1,
        "piotroski_f_score": 7,
        "isin": "INE154A01025",
    },

    "HINDUNILVR": {
        "sector": "FMCG",
        "market_cap_cr": 560000,
        "current_price": 2376,
        "pe_ratio": 54.2,
        "pb_ratio": 11.5,
        "dividend_yield_pct": 1.8,
        "roe_pct": 22.8,
        "roce_pct": 29.6,
        "debt_to_equity": 0.0,
        "net_profit_margin_pct": 15.4,
        "interest_coverage": 999,
        "current_ratio": 1.5,
        "payout_ratio_pct": 95.0,
        "promoter_holding_pct": 61.9,
        "revenue_cagr_5y": 9.3,
        "profit_cagr_5y": 11.0,
        "dividend_cagr_5y": 12.5,
        "dividend_history": [14.0, 17.0, 19.0, 21.0, 24.0, 30.0, 34.0, 36.0, 42.0, 44.0],
        "years_consecutive_dividend": 10,
        "fcf_yield_pct": 2.1,
        "piotroski_f_score": 7,
        "isin": "INE030A01027",
    },

    "NESTLEIND": {
        "sector": "FMCG",
        "market_cap_cr": 220000,
        "current_price": 2280,
        "pe_ratio": 69.1,
        "pb_ratio": 73.0,
        "dividend_yield_pct": 1.9,
        "roe_pct": 105.6,
        "roce_pct": 148.0,
        "debt_to_equity": 0.0,
        "net_profit_margin_pct": 14.5,
        "interest_coverage": 999,
        "current_ratio": 0.7,
        "payout_ratio_pct": 82.0,
        "promoter_holding_pct": 62.8,
        "revenue_cagr_5y": 11.2,
        "profit_cagr_5y": 17.3,
        "dividend_cagr_5y": 22.1,
        "dividend_history": [100.0, 120.0, 135.0, 155.0, 175.0, 195.0, 220.0, 260.0, 280.0, 275.0],
        "years_consecutive_dividend": 10,
        "fcf_yield_pct": 2.2,
        "piotroski_f_score": 6,
        "isin": "INE239A01016",
    },

    # ===== UTILITIES / PSU (High Yield) =====

    "POWERGRID": {
        "sector": "Utilities",
        "market_cap_cr": 285000,
        "current_price": 306,
        "pe_ratio": 18.3,
        "pb_ratio": 3.4,
        "dividend_yield_pct": 4.1,
        "roe_pct": 19.5,
        "roce_pct": 11.2,
        "debt_to_equity": 1.6,
        "net_profit_margin_pct": 31.5,
        "interest_coverage": 3.8,
        "current_ratio": 0.6,
        "payout_ratio_pct": 72.0,
        "promoter_holding_pct": 51.3,
        "revenue_cagr_5y": 8.5,
        "profit_cagr_5y": 11.8,
        "dividend_cagr_5y": 14.2,
        "dividend_history": [5.0, 6.0, 7.5, 8.0, 9.5, 10.5, 11.0, 13.0, 13.5, 12.5],
        "years_consecutive_dividend": 10,
        "fcf_yield_pct": 6.2,
        "piotroski_f_score": 6,
        "isin": "INE752E01010",
    },

    "COALINDIA": {
        "sector": "Mining / Energy",
        "market_cap_cr": 245000,
        "current_price": 398,
        "pe_ratio": 8.1,
        "pb_ratio": 3.8,
        "dividend_yield_pct": 6.8,
        "roe_pct": 52.3,
        "roce_pct": 64.2,
        "debt_to_equity": 0.0,
        "net_profit_margin_pct": 22.3,
        "interest_coverage": 999,
        "current_ratio": 2.9,
        "payout_ratio_pct": 55.0,
        "promoter_holding_pct": 63.1,
        "revenue_cagr_5y": 14.8,
        "profit_cagr_5y": 32.5,
        "dividend_cagr_5y": 20.1,
        "dividend_history": [8.0, 8.0, 10.0, 12.5, 14.0, 17.5, 21.0, 24.0, 25.0, 27.0],
        "years_consecutive_dividend": 10,
        "fcf_yield_pct": 8.5,
        "piotroski_f_score": 8,
        "isin": "INE522F01014",
    },

    "ONGC": {
        "sector": "Energy",
        "market_cap_cr": 335000,
        "current_price": 266,
        "pe_ratio": 7.2,
        "pb_ratio": 1.0,
        "dividend_yield_pct": 4.5,
        "roe_pct": 14.3,
        "roce_pct": 16.8,
        "debt_to_equity": 0.4,
        "net_profit_margin_pct": 13.5,
        "interest_coverage": 12.0,
        "current_ratio": 1.2,
        "payout_ratio_pct": 32.0,
        "promoter_holding_pct": 58.9,
        "revenue_cagr_5y": 12.4,
        "profit_cagr_5y": 18.2,
        "dividend_cagr_5y": 15.0,
        "dividend_history": [5.0, 6.0, 4.5, 6.5, 8.5, 9.0, 10.5, 11.0, 12.0, 12.0],
        "years_consecutive_dividend": 10,
        "fcf_yield_pct": 7.2,
        "piotroski_f_score": 7,
        "isin": "INE213A01029",
    },

    "NTPC": {
        "sector": "Utilities",
        "market_cap_cr": 345000,
        "current_price": 356,
        "pe_ratio": 17.8,
        "pb_ratio": 2.9,
        "dividend_yield_pct": 2.5,
        "roe_pct": 13.8,
        "roce_pct": 9.8,
        "debt_to_equity": 1.4,
        "net_profit_margin_pct": 11.5,
        "interest_coverage": 3.2,
        "current_ratio": 1.1,
        "payout_ratio_pct": 43.0,
        "promoter_holding_pct": 51.1,
        "revenue_cagr_5y": 10.5,
        "profit_cagr_5y": 14.5,
        "dividend_cagr_5y": 10.8,
        "dividend_history": [3.5, 4.0, 4.25, 4.5, 5.0, 6.0, 7.0, 7.5, 8.0, 8.75],
        "years_consecutive_dividend": 10,
        "fcf_yield_pct": 3.8,
        "piotroski_f_score": 6,
        "isin": "INE733E01010",
    },

    # ===== IT SECTOR =====

    "TCS": {
        "sector": "IT",
        "market_cap_cr": 1400000,
        "current_price": 3862,
        "pe_ratio": 28.1,
        "pb_ratio": 13.2,
        "dividend_yield_pct": 1.6,
        "roe_pct": 48.2,
        "roce_pct": 62.5,
        "debt_to_equity": 0.0,
        "net_profit_margin_pct": 19.4,
        "interest_coverage": 999,
        "current_ratio": 2.9,
        "payout_ratio_pct": 44.0,
        "promoter_holding_pct": 72.4,
        "revenue_cagr_5y": 14.2,
        "profit_cagr_5y": 12.8,
        "dividend_cagr_5y": 16.5,
        "dividend_history": [22.0, 37.0, 47.0, 55.0, 66.0, 76.0, 90.0, 100.0, 115.0, 61.0],
        "years_consecutive_dividend": 10,
        "fcf_yield_pct": 3.8,
        "piotroski_f_score": 8,
        "isin": "INE467B01029",
    },

    "INFY": {
        "sector": "IT",
        "market_cap_cr": 770000,
        "current_price": 1855,
        "pe_ratio": 24.8,
        "pb_ratio": 8.2,
        "dividend_yield_pct": 2.4,
        "roe_pct": 33.8,
        "roce_pct": 44.3,
        "debt_to_equity": 0.0,
        "net_profit_margin_pct": 16.8,
        "interest_coverage": 999,
        "current_ratio": 2.3,
        "payout_ratio_pct": 58.0,
        "promoter_holding_pct": 14.9,
        "revenue_cagr_5y": 16.8,
        "profit_cagr_5y": 14.2,
        "dividend_cagr_5y": 19.2,
        "dividend_history": [16.0, 21.0, 26.0, 32.0, 38.0, 42.0, 46.0, 50.0, 56.0, 44.0],
        "years_consecutive_dividend": 10,
        "fcf_yield_pct": 4.2,
        "piotroski_f_score": 7,
        "isin": "INE009A01021",
    },

    "HCLTECH": {
        "sector": "IT",
        "market_cap_cr": 470000,
        "current_price": 1739,
        "pe_ratio": 26.5,
        "pb_ratio": 7.8,
        "dividend_yield_pct": 3.4,
        "roe_pct": 23.2,
        "roce_pct": 29.8,
        "debt_to_equity": 0.0,
        "net_profit_margin_pct": 14.8,
        "interest_coverage": 999,
        "current_ratio": 2.6,
        "payout_ratio_pct": 88.0,
        "promoter_holding_pct": 60.8,
        "revenue_cagr_5y": 15.5,
        "profit_cagr_5y": 13.1,
        "dividend_cagr_5y": 25.5,
        "dividend_history": [8.0, 12.0, 18.0, 22.0, 32.0, 36.0, 44.0, 50.0, 56.0, 60.0],
        "years_consecutive_dividend": 10,
        "fcf_yield_pct": 4.5,
        "piotroski_f_score": 7,
        "isin": "INE860A01027",
    },

    # ===== BANKING / FINANCE =====

    "HDFCBANK": {
        "sector": "Banking",
        "market_cap_cr": 1280000,
        "current_price": 1680,
        "pe_ratio": 18.8,
        "pb_ratio": 2.6,
        "dividend_yield_pct": 1.2,
        "roe_pct": 14.8,
        "roce_pct": 8.1,   # Banks use different metrics
        "debt_to_equity": 7.2, # Normal for banks
        "net_profit_margin_pct": 23.5,
        "interest_coverage": 1.8,
        "current_ratio": None, # Not applicable for banks
        "payout_ratio_pct": 22.0,
        "promoter_holding_pct": 0.0,
        "revenue_cagr_5y": 15.8,
        "profit_cagr_5y": 13.5,
        "dividend_cagr_5y": 8.2,
        "dividend_history": [5.5, 6.5, 7.5, 8.5, 10.0, 15.0, 15.5, 19.0, 19.5, 19.5],
        "years_consecutive_dividend": 10,
        "fcf_yield_pct": 1.8,
        "piotroski_f_score": 6,
        "isin": "INE040A01034",
    },

    "ICICIBANK": {
        "sector": "Banking",
        "market_cap_cr": 935000,
        "current_price": 1341,
        "pe_ratio": 18.2,
        "pb_ratio": 3.2,
        "dividend_yield_pct": 0.75,
        "roe_pct": 17.8,
        "roce_pct": 9.2,
        "debt_to_equity": 6.8,
        "net_profit_margin_pct": 25.4,
        "interest_coverage": 1.9,
        "current_ratio": None,
        "payout_ratio_pct": 14.0,
        "promoter_holding_pct": 0.0,
        "revenue_cagr_5y": 16.5,
        "profit_cagr_5y": 36.8,
        "dividend_cagr_5y": 22.5,
        "dividend_history": [1.5, 2.0, 2.0, 2.0, 3.0, 4.0, 5.0, 8.0, 10.0, 10.0],
        "years_consecutive_dividend": 10,
        "fcf_yield_pct": 1.2,
        "piotroski_f_score": 7,
        "isin": "INE090A01021",
    },

    # ===== ENERGY / INDUSTRIAL =====

    "RELIANCE": {
        "sector": "Diversified / Energy",
        "market_cap_cr": 1700000,
        "current_price": 1420,   # Note: adjusted for 1:1 bonus if applicable
        "pe_ratio": 25.8,
        "pb_ratio": 2.2,
        "dividend_yield_pct": 0.35,
        "roe_pct": 9.2,
        "roce_pct": 11.4,
        "debt_to_equity": 0.42,
        "net_profit_margin_pct": 7.8,
        "interest_coverage": 5.5,
        "current_ratio": 1.3,
        "payout_ratio_pct": 9.0,
        "promoter_holding_pct": 50.4,
        "revenue_cagr_5y": 20.4,
        "profit_cagr_5y": 18.8,
        "dividend_cagr_5y": 5.5,
        "dividend_history": [2.0, 3.0, 3.5, 5.0, 5.0, 6.0, 6.5, 7.0, 9.0, 5.0],
        "years_consecutive_dividend": 10,
        "fcf_yield_pct": 0.8,
        "piotroski_f_score": 6,
        "isin": "INE002A01018",
    },

    # ===== PHARMA =====

    "SUNPHARMA": {
        "sector": "Pharma",
        "market_cap_cr": 420000,
        "current_price": 1749,
        "pe_ratio": 35.2,
        "pb_ratio": 6.5,
        "dividend_yield_pct": 0.8,
        "roe_pct": 18.9,
        "roce_pct": 22.4,
        "debt_to_equity": 0.05,
        "net_profit_margin_pct": 18.5,
        "interest_coverage": 45.0,
        "current_ratio": 2.0,
        "payout_ratio_pct": 28.0,
        "promoter_holding_pct": 54.5,
        "revenue_cagr_5y": 12.5,
        "profit_cagr_5y": 22.8,
        "dividend_cagr_5y": 8.5,
        "dividend_history": [2.5, 3.0, 3.0, 3.5, 5.0, 7.0, 10.0, 12.0, 14.0, 14.0],
        "years_consecutive_dividend": 10,
        "fcf_yield_pct": 2.2,
        "piotroski_f_score": 7,
        "isin": "INE044A01036",
    },

    # ===== CONSUMER / TITAN =====

    "TITAN": {
        "sector": "Consumer Goods",
        "market_cap_cr": 295000,
        "current_price": 3318,
        "pe_ratio": 85.0,
        "pb_ratio": 28.5,
        "dividend_yield_pct": 0.35,
        "roe_pct": 36.5,
        "roce_pct": 47.2,
        "debt_to_equity": 0.0,
        "net_profit_margin_pct": 7.2,
        "interest_coverage": 999,
        "current_ratio": 1.8,
        "payout_ratio_pct": 28.0,
        "promoter_holding_pct": 52.9,
        "revenue_cagr_5y": 22.5,
        "profit_cagr_5y": 28.4,
        "dividend_cagr_5y": 22.8,
        "dividend_history": [3.75, 5.0, 6.0, 5.0, 4.0, 6.25, 7.5, 10.0, 11.0, 11.0],
        "years_consecutive_dividend": 10,
        "fcf_yield_pct": 0.4,
        "piotroski_f_score": 7,
        "isin": "INE280A01028",
    },
}
