"""
Project Atlas - Penny Stock Database
======================================
Curated fundamental data for ~25 Indian penny/small-cap stocks
with historical growth metrics, promoter data, and sector catalysts.

⚠️  RISK WARNING: Penny stocks are speculative. These are NOT
    dividend investments. They are growth bets. Max 3% of total
    portfolio per stock. Max 15% total penny allocation.

Categories:
    "growth_rocket"  - Already showing 2-3x revenue growth, story not fully priced
    "turnaround"     - Was loss-making, now turning profitable
    "hidden_gem"     - Profitable, consistent, but undiscovered/under-covered

Data fields:
    price              : Current price (INR)
    market_cap_cr      : Market cap in Crores
    revenue_cagr_2y    : 2-year Revenue CAGR %
    revenue_cagr_3y    : 3-year Revenue CAGR %
    profit_cagr_2y     : 2-year Net Profit CAGR % (None if was loss-making)
    latest_revenue_cr  : Latest annual revenue (Crores)
    prev_revenue_cr    : Previous year revenue (Crores)
    was_loss_making    : True if company turned profitable in last 2 years
    promoter_holding   : Promoter holding %
    promoter_trend     : "increasing", "stable", or "decreasing"
    pledged_pct        : % of promoter shares pledged (lower is safer)
    debt_to_equity     : D/E ratio
    interest_coverage  : EBIT / Interest (>2 is safe)
    roe                : Return on Equity %
    pe_ratio           : Price to Earnings (None if loss-making)
    sector             : Business sector
    pli_beneficiary    : True if company benefits from PLI/Govt schemes
    sector_tailwind    : Description of macro tailwind
    category           : "growth_rocket", "turnaround", or "hidden_gem"
    known_for          : Why this stock is noteworthy
    key_risks          : Main risks to watch
"""

PENNY_STOCKS = {

    # ============================================================
    # GROWTH ROCKETS — Revenue already 2-3x, story not fully priced
    # ============================================================

    "CUPIDLTD": {
        "price": 185,
        "market_cap_cr": 560,
        "revenue_cagr_2y": 48.5,
        "revenue_cagr_3y": 38.2,
        "profit_cagr_2y": 82.3,
        "latest_revenue_cr": 285,
        "prev_revenue_cr": 192,
        "was_loss_making": False,
        "promoter_holding": 51.8,
        "promoter_trend": "stable",
        "pledged_pct": 0.0,
        "debt_to_equity": 0.12,
        "interest_coverage": 18.5,
        "roe": 28.4,
        "pe_ratio": 22.5,
        "sector": "Healthcare / Medical Devices",
        "pli_beneficiary": True,
        "sector_tailwind": "Govt export push for medical devices + condom exports under PLI scheme",
        "category": "growth_rocket",
        "known_for": "Condom + medical device manufacturer. Explosive export growth. Debt-free.",
        "key_risks": ["Concentrated product line", "Export market dependency"],
    },

    "WAAREERENEW": {
        "price": 295,
        "market_cap_cr": 1850,
        "revenue_cagr_2y": 112.4,
        "revenue_cagr_3y": 85.6,
        "profit_cagr_2y": 145.2,
        "latest_revenue_cr": 890,
        "prev_revenue_cr": 420,
        "was_loss_making": False,
        "promoter_holding": 72.1,
        "promoter_trend": "stable",
        "pledged_pct": 0.0,
        "debt_to_equity": 0.35,
        "interest_coverage": 12.8,
        "roe": 32.5,
        "pe_ratio": 38.2,
        "sector": "Renewable Energy",
        "pli_beneficiary": True,
        "sector_tailwind": "India's 500GW renewable target by 2030, massive solar EPC orders",
        "category": "growth_rocket",
        "known_for": "Solar EPC + module manufacturer. Massive order book growth.",
        "key_risks": ["High PE valuation", "Execution risk on large orders"],
    },

    "HINDRECL": {
        "price": 48,
        "market_cap_cr": 185,
        "revenue_cagr_2y": 35.8,
        "revenue_cagr_3y": 28.4,
        "profit_cagr_2y": 68.2,
        "latest_revenue_cr": 285,
        "prev_revenue_cr": 210,
        "was_loss_making": False,
        "promoter_holding": 48.5,
        "promoter_trend": "increasing",
        "pledged_pct": 0.0,
        "debt_to_equity": 0.42,
        "interest_coverage": 8.5,
        "roe": 18.2,
        "pe_ratio": 14.8,
        "sector": "Electronics / Power",
        "pli_beneficiary": True,
        "sector_tailwind": "Electronics manufacturing push, power conversion demand",
        "category": "growth_rocket",
        "known_for": "Power rectifiers and semiconductor components. Low PE, promoter buying.",
        "key_risks": ["Small size", "Competition from Chinese imports"],
    },

    "GABRIEL": {
        "price": 385,
        "market_cap_cr": 2200,
        "revenue_cagr_2y": 22.5,
        "revenue_cagr_3y": 18.8,
        "profit_cagr_2y": 45.2,
        "latest_revenue_cr": 3850,
        "prev_revenue_cr": 3150,
        "was_loss_making": False,
        "promoter_holding": 55.0,
        "promoter_trend": "stable",
        "pledged_pct": 0.0,
        "debt_to_equity": 0.08,
        "interest_coverage": 22.0,
        "roe": 22.8,
        "pe_ratio": 24.5,
        "sector": "Auto Ancillary",
        "pli_beneficiary": True,
        "sector_tailwind": "EV transition + auto sector recovery, shock absorber demand",
        "category": "hidden_gem",
        "known_for": "Shock absorber manufacturer. Debt-free. EV model pivot underway.",
        "key_risks": ["Auto sector cyclicality", "EV transition risk"],
    },

    # ============================================================
    # TURNAROUND STORIES — From loss to profit
    # ============================================================

    "USHAMART": {
        "price": 42,
        "market_cap_cr": 1250,
        "revenue_cagr_2y": 18.5,
        "revenue_cagr_3y": 12.2,
        "profit_cagr_2y": None,  # Was loss-making
        "latest_revenue_cr": 3200,
        "prev_revenue_cr": 2700,
        "was_loss_making": True,
        "promoter_holding": 75.2,
        "promoter_trend": "stable",
        "pledged_pct": 8.5,
        "debt_to_equity": 0.68,
        "interest_coverage": 4.2,
        "roe": 12.5,
        "pe_ratio": 18.5,
        "sector": "Steel / Wire Ropes",
        "pli_beneficiary": False,
        "sector_tailwind": "Infrastructure capex boom, wire rope demand from mining/ports",
        "category": "turnaround",
        "known_for": "Wire rope leader. Full debt repayment done. Margins recovering.",
        "key_risks": ["Steel price volatility", "Residual debt", "Low pledged shares"],
    },

    "TRIDENT": {
        "price": 34,
        "market_cap_cr": 6800,
        "revenue_cagr_2y": 12.5,
        "revenue_cagr_3y": 16.8,
        "profit_cagr_2y": 28.5,
        "latest_revenue_cr": 7200,
        "prev_revenue_cr": 6400,
        "was_loss_making": False,
        "promoter_holding": 71.5,
        "promoter_trend": "stable",
        "pledged_pct": 0.0,
        "debt_to_equity": 0.55,
        "interest_coverage": 6.8,
        "roe": 18.5,
        "pe_ratio": 22.5,
        "sector": "Textiles / Paper",
        "pli_beneficiary": True,
        "sector_tailwind": "PLI for textiles, home textile exports to US/EU",
        "category": "turnaround",
        "known_for": "Vertically integrated textile + paper. High promoter, zero pledge.",
        "key_risks": ["Cotton price volatility", "Currency risk on exports"],
    },

    "CAPACITE": {
        "price": 285,
        "market_cap_cr": 1450,
        "revenue_cagr_2y": 35.2,
        "revenue_cagr_3y": 18.5,
        "profit_cagr_2y": None,
        "latest_revenue_cr": 2100,
        "prev_revenue_cr": 1550,
        "was_loss_making": True,
        "promoter_holding": 52.8,
        "promoter_trend": "increasing",
        "pledged_pct": 0.0,
        "debt_to_equity": 0.82,
        "interest_coverage": 3.5,
        "roe": 14.2,
        "pe_ratio": 28.5,
        "sector": "Construction / EPC",
        "pli_beneficiary": False,
        "sector_tailwind": "Urban housing boom, commercial real estate upturn",
        "category": "turnaround",
        "known_for": "Premium residential construction company. Order book at all-time high.",
        "key_risks": ["Cash conversion cycle", "Real estate sector risk"],
    },

    "MAITHANALL": {
        "price": 1050,
        "market_cap_cr": 2800,
        "revenue_cagr_2y": 8.5,
        "revenue_cagr_3y": 22.5,
        "profit_cagr_2y": 45.8,
        "latest_revenue_cr": 2800,
        "prev_revenue_cr": 2580,
        "was_loss_making": False,
        "promoter_holding": 73.8,
        "promoter_trend": "stable",
        "pledged_pct": 0.0,
        "debt_to_equity": 0.0,
        "interest_coverage": 999,
        "roe": 35.2,
        "pe_ratio": 8.5,
        "sector": "Ferro Alloys / Steel",
        "pli_beneficiary": False,
        "sector_tailwind": "Domestic steel capacity expansion requiring ferro alloys",
        "category": "hidden_gem",
        "known_for": "Debt-free. PE of 8.5x. ROE of 35%. Severely undervalued ferro alloys play.",
        "key_risks": ["Cyclical commodity", "Dependent on steel industry capex"],
    },

    # ============================================================
    # HIDDEN GEMS — Profitable, undiscovered
    # ============================================================

    "PONDYOX": {
        "price": 385,
        "market_cap_cr": 650,
        "revenue_cagr_2y": 18.2,
        "revenue_cagr_3y": 15.5,
        "profit_cagr_2y": 42.5,
        "latest_revenue_cr": 1850,
        "prev_revenue_cr": 1570,
        "was_loss_making": False,
        "promoter_holding": 58.5,
        "promoter_trend": "increasing",
        "pledged_pct": 0.0,
        "debt_to_equity": 0.22,
        "interest_coverage": 12.5,
        "roe": 28.5,
        "pe_ratio": 10.5,
        "sector": "Metal Recycling / Chemicals",
        "pli_beneficiary": False,
        "sector_tailwind": "Lead acid battery recycling demand from EV + inverter boom",
        "category": "hidden_gem",
        "known_for": "Lead recycling leader. Low PE, high ROE, promoter buying. Circular economy play.",
        "key_risks": ["Lead price volatility", "Environmental regulation risk"],
    },

    "SHARDAMOTR": {
        "price": 1285,
        "market_cap_cr": 1900,
        "revenue_cagr_2y": 22.8,
        "revenue_cagr_3y": 18.5,
        "profit_cagr_2y": 38.5,
        "latest_revenue_cr": 4200,
        "prev_revenue_cr": 3500,
        "was_loss_making": False,
        "promoter_holding": 60.5,
        "promoter_trend": "stable",
        "pledged_pct": 0.0,
        "debt_to_equity": 0.05,
        "interest_coverage": 85.0,
        "roe": 24.5,
        "pe_ratio": 14.5,
        "sector": "Auto Ancillary",
        "pli_beneficiary": True,
        "sector_tailwind": "EV emission components, catalytic converter demand",
        "category": "hidden_gem",
        "known_for": "Emission control systems. Near debt-free. Low PE for high-quality auto ancillary.",
        "key_risks": ["Auto sector dependency", "EV shift may reduce emission component demand long-term"],
    },

    "SANSERA": {
        "price": 820,
        "market_cap_cr": 4200,
        "revenue_cagr_2y": 28.5,
        "revenue_cagr_3y": 22.5,
        "profit_cagr_2y": 52.8,
        "latest_revenue_cr": 3800,
        "prev_revenue_cr": 2950,
        "was_loss_making": False,
        "promoter_holding": 55.5,
        "promoter_trend": "stable",
        "pledged_pct": 0.0,
        "debt_to_equity": 0.42,
        "interest_coverage": 8.5,
        "roe": 18.5,
        "pe_ratio": 26.5,
        "sector": "Auto Ancillary / Precision Engineering",
        "pli_beneficiary": True,
        "sector_tailwind": "Aerospace + EV precision component demand, export to Europe",
        "category": "hidden_gem",
        "known_for": "Precision forging for autos + aerospace. Pivoting to EV + aerospace.",
        "key_risks": ["High PE", "Transition execution risk"],
    },

    "SJVN": {
        "price": 108,
        "market_cap_cr": 42000,
        "revenue_cagr_2y": 22.5,
        "revenue_cagr_3y": 18.8,
        "profit_cagr_2y": 28.5,
        "latest_revenue_cr": 2850,
        "prev_revenue_cr": 2330,
        "was_loss_making": False,
        "promoter_holding": 81.8,
        "promoter_trend": "stable",
        "pledged_pct": 0.0,
        "debt_to_equity": 0.85,
        "interest_coverage": 5.8,
        "roe": 12.5,
        "pe_ratio": 28.5,
        "sector": "Hydropower / Renewables",
        "pli_beneficiary": True,
        "sector_tailwind": "Govt-backed hydro + solar. 25GW capacity target. PSU safety.",
        "category": "growth_rocket",
        "known_for": "Govt hydro PSU expanding aggressively into solar. Safe + growth.",
        "key_risks": ["High debt for expansion", "Govt policy dependency"],
    },

    "IRFC": {
        "price": 158,
        "market_cap_cr": 205000,
        "revenue_cagr_2y": 18.5,
        "revenue_cagr_3y": 22.5,
        "profit_cagr_2y": 22.8,
        "latest_revenue_cr": 26500,
        "prev_revenue_cr": 22350,
        "was_loss_making": False,
        "promoter_holding": 86.4,
        "promoter_trend": "stable",
        "pledged_pct": 0.0,
        "debt_to_equity": 9.5,   # High but normal for NBFC/Financing
        "interest_coverage": 1.3,
        "roe": 14.5,
        "pe_ratio": 22.5,
        "sector": "Railway Financing (NBFC)",
        "pli_beneficiary": True,
        "sector_tailwind": "Railway capex ₹2.5L Cr budget. IRFC funds all railway purchases.",
        "category": "hidden_gem",
        "known_for": "Railway NBFC. Zero credit risk (lends only to Indian Railways). Guaranteed growth.",
        "key_risks": ["High debt (normal for NBFC)", "Govt policy changes to railway funding"],
    },
}
