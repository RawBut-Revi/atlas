"""
bond_data.py
============
Project Atlas – Quantitative Investment Research Platform (Indian Markets)

Bond and macro data module covering Indian fixed-income markets.
Provides:
  - Monthly 10-Year G-Sec yield history (Jan 2022 – Aug 2025)
  - Monthly RBI Repo Rate history (Jan 2022 – Aug 2025)
  - Real interest rate computation
  - Yield-curve shape signal
  - Market stress level indicator
  - Indian bond instrument profiles

Data sources / references (curated):
  - RBI Monetary Policy Committee press releases
  - CCIL / FBIL benchmark yield data
  - NSE / BSE bond market statistics

Values are approximate end-of-month readings for research purposes.
Refresh with live RBI / CCIL data before production use.

Author : Project Atlas Research Team
Updated: Aug 2025
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# 1.  MACRO CONSTANTS
# ---------------------------------------------------------------------------

# Assumed current CPI inflation for real-rate computation (%)
# Update periodically from MoSPI data releases.
ASSUMED_CPI_INFLATION_PCT: float = 5.5

# Hardcoded 2-Year G-Sec yield for yield-curve spread calculation (%)
# Source: FBIL / CCIL benchmark, approximate Aug 2025
GSEC_2Y_YIELD_PCT: float = 6.75

# ---------------------------------------------------------------------------
# 2.  HISTORICAL 10-YEAR G-SEC YIELDS  (% per annum, end-of-month)
# ---------------------------------------------------------------------------

GSEC_10Y_YIELDS: Dict[str, float] = {
    # ---- 2022 ----
    "2022-01": 6.56,
    "2022-02": 6.69,
    "2022-03": 6.84,
    "2022-04": 7.12,
    "2022-05": 7.40,
    "2022-06": 7.45,
    "2022-07": 7.35,
    "2022-08": 7.22,
    "2022-09": 7.39,
    "2022-10": 7.47,
    "2022-11": 7.30,
    "2022-12": 7.33,
    # ---- 2023 ----
    "2023-01": 7.31,
    "2023-02": 7.42,
    "2023-03": 7.31,
    "2023-04": 7.18,
    "2023-05": 7.02,
    "2023-06": 7.11,
    "2023-07": 7.17,
    "2023-08": 7.18,
    "2023-09": 7.22,
    "2023-10": 7.38,
    "2023-11": 7.28,
    "2023-12": 7.18,
    # ---- 2024 ----
    "2024-01": 7.18,
    "2024-02": 7.06,
    "2024-03": 7.05,
    "2024-04": 7.17,
    "2024-05": 7.01,
    "2024-06": 6.99,
    "2024-07": 6.96,
    "2024-08": 6.86,
    "2024-09": 6.74,
    "2024-10": 6.84,
    "2024-11": 6.77,
    "2024-12": 6.76,
    # ---- 2025 ----
    "2025-01": 6.69,
    "2025-02": 6.68,
    "2025-03": 6.58,
    "2025-04": 6.42,
    "2025-05": 6.32,
    "2025-06": 6.38,
    "2025-07": 6.44,
    "2025-08": 6.50,
}

# ---------------------------------------------------------------------------
# 3.  RBI REPO RATE HISTORY  (% per annum, decision effective from date shown)
# ---------------------------------------------------------------------------
# Historical MPC decisions:
#   Jan 2022: 4.00% (accommodative stance, COVID-era low)
#   May 2022: 4.40% (+40 bps emergency hike, May 4 off-cycle)
#   Jun 2022: 4.90% (+50 bps)
#   Aug 2022: 5.40% (+50 bps)
#   Sep 2022: 5.90% (+50 bps)
#   Dec 2022: 6.25% (+35 bps)
#   Feb 2023: 6.50% (+25 bps) ← terminal rate
#   Apr 2023 – Dec 2024: 6.50% (on hold)
#   Feb 2025: 6.25% (-25 bps, first cut)
#   Apr 2025: 6.00% (-25 bps)
#   Jun 2025: 5.75% (-25 bps)
#   Aug 2025: 5.75% (on hold)

RBI_REPO_RATE: Dict[str, float] = {
    # ---- 2022 ----
    "2022-01": 4.00,
    "2022-02": 4.00,
    "2022-03": 4.00,
    "2022-04": 4.00,
    "2022-05": 4.40,   # off-cycle emergency hike
    "2022-06": 4.90,
    "2022-07": 4.90,
    "2022-08": 5.40,
    "2022-09": 5.90,
    "2022-10": 5.90,
    "2022-11": 5.90,
    "2022-12": 6.25,
    # ---- 2023 ----
    "2023-01": 6.25,
    "2023-02": 6.50,
    "2023-03": 6.50,
    "2023-04": 6.50,
    "2023-05": 6.50,
    "2023-06": 6.50,
    "2023-07": 6.50,
    "2023-08": 6.50,
    "2023-09": 6.50,
    "2023-10": 6.50,
    "2023-11": 6.50,
    "2023-12": 6.50,
    # ---- 2024 ----
    "2024-01": 6.50,
    "2024-02": 6.50,
    "2024-03": 6.50,
    "2024-04": 6.50,
    "2024-05": 6.50,
    "2024-06": 6.50,
    "2024-07": 6.50,
    "2024-08": 6.50,
    "2024-09": 6.50,
    "2024-10": 6.50,
    "2024-11": 6.50,
    "2024-12": 6.50,
    # ---- 2025 ----
    "2025-01": 6.50,
    "2025-02": 6.25,   # first rate cut cycle
    "2025-03": 6.25,
    "2025-04": 6.00,
    "2025-05": 6.00,
    "2025-06": 5.75,
    "2025-07": 5.75,
    "2025-08": 5.75,
}

# ---------------------------------------------------------------------------
# 4.  BOND INSTRUMENT PROFILES
# ---------------------------------------------------------------------------

BOND_INSTRUMENTS: Dict[str, Dict] = {
    "sgb": {
        "display_name": "Sovereign Gold Bonds (SGBs)",
        "issuer": "Government of India (via RBI)",
        "description": (
            "SGBs are government securities denominated in grams of gold. They offer "
            "a fixed interest rate of 2.5% p.a. on the issue price, paid semi-annually, "
            "plus capital appreciation linked to gold price. Capital gains on redemption "
            "at maturity (8 years) are tax-exempt for individuals."
        ),
        "typical_yield_range_pct": (2.5, 4.0),   # interest + implied yield
        "tenure_years": 8,
        "liquidity": "low-moderate (listed on exchanges; early exit from year 5)",
        "risk_level": "low",
        "tax_exempt_on_maturity": True,
        "min_investment_units": 1,   # 1 unit = 1 gram of gold
        "ideal_for": ["long-term gold allocation", "tax-efficient gold exposure"],
    },
    "rbi_floating_rate_bonds": {
        "display_name": "RBI Floating Rate Savings Bonds (7.35% 2031)",
        "issuer": "Government of India (via RBI)",
        "description": (
            "Sovereign-backed savings bonds with a floating coupon reset every 6 months, "
            "linked to the NSC rate + 35 bps spread. Ideal for risk-averse investors "
            "seeking better-than-FD returns with sovereign guarantee. Not tradeable on "
            "secondary markets."
        ),
        "typical_yield_range_pct": (7.0, 7.5),
        "tenure_years": 7,
        "liquidity": "low (no secondary market; premature withdrawal restricted)",
        "risk_level": "very low",
        "tax_exempt_on_maturity": False,
        "coupon_reset_frequency": "semi-annual",
        "ideal_for": ["senior citizens", "risk-averse fixed-income investors"],
    },
    "liquid_funds": {
        "display_name": "Liquid Mutual Funds",
        "issuer": "Various AMCs (SEBI regulated)",
        "description": (
            "Debt mutual funds investing in money-market instruments with maturity ≤ 91 days. "
            "Highly liquid (T+1 redemption up to ₹50,000 via instant redemption). Returns "
            "track prevailing repo rate with a slight lag. Suitable as cash-management vehicle "
            "and short-term parking of surplus funds."
        ),
        "typical_yield_range_pct": (5.5, 7.5),   # varies with repo cycle
        "tenure_years": None,   # open-ended
        "liquidity": "very high (same/next day redemption)",
        "risk_level": "very low",
        "tax_exempt_on_maturity": False,
        "tax_treatment": "LTCG (>2 years) with indexation; else slab rate",
        "ideal_for": ["emergency fund parking", "short-term surplus", "STP source"],
    },
    "gsec_etf": {
        "display_name": "G-Sec ETFs / Index Funds",
        "issuer": "Various AMCs (Bharat Bond, Nippon, ICICI Pru, etc.)",
        "description": (
            "Passively managed funds tracking indices of Government Securities. "
            "Bharat Bond ETFs target specific maturity profiles (3Y, 5Y, 10Y). "
            "Provide gilt exposure with exchange liquidity and lower expense ratios "
            "than actively managed debt funds."
        ),
        "typical_yield_range_pct": (6.3, 7.2),
        "tenure_years": "variable (3, 5, 10 year target maturity variants)",
        "liquidity": "high (exchange traded)",
        "risk_level": "low-moderate (duration risk)",
        "tax_exempt_on_maturity": False,
        "tax_treatment": "LTCG (>2 years) with indexation; else slab rate",
        "ideal_for": ["long-term fixed-income allocation", "liability-matching"],
    },
    "corporate_bond_funds": {
        "display_name": "Corporate Bond Funds (AAA-rated)",
        "issuer": "Various AMCs (SEBI regulated)",
        "description": (
            "Debt mutual funds investing ≥80% in highest-rated corporate bonds (AA+ and above). "
            "Offer a yield pickup of 30–80 bps over equivalent G-Secs with marginally higher "
            "credit risk. Suitable for 3+ year investment horizons for tax efficiency."
        ),
        "typical_yield_range_pct": (7.0, 8.0),
        "tenure_years": None,   # open-ended
        "liquidity": "high (T+1/T+2 redemption)",
        "risk_level": "low-moderate",
        "tax_exempt_on_maturity": False,
        "tax_treatment": "LTCG (>2 years) with indexation; else slab rate",
        "ideal_for": ["medium-term allocation", "yield enhancement vs gilts"],
    },
}

# ---------------------------------------------------------------------------
# 5.  PUBLIC API FUNCTIONS
# ---------------------------------------------------------------------------


def _sorted_series(series: Dict[str, float]) -> List[Tuple[str, float]]:
    """Return chronologically sorted list of (period, value) tuples."""
    return sorted(series.items())


def get_current_gsec_yield() -> Dict[str, object]:
    """
    Return the most recent 10-Year G-Sec yield reading.

    Returns
    -------
    dict
        - ``period``    : Latest month (YYYY-MM)
        - ``yield_pct`` : Yield in % per annum
        - ``label``     : Instrument description
    """
    sorted_data = _sorted_series(GSEC_10Y_YIELDS)
    period, yield_val = sorted_data[-1]
    return {
        "period": period,
        "yield_pct": yield_val,
        "label": "10-Year Government of India Security (benchmark)",
    }


def get_current_repo_rate() -> Dict[str, object]:
    """
    Return the current RBI Repo Rate.

    Returns
    -------
    dict
        - ``period``    : Latest month of record (YYYY-MM)
        - ``rate_pct``  : Repo rate in % per annum
        - ``label``     : Instrument description
    """
    sorted_data = _sorted_series(RBI_REPO_RATE)
    period, rate = sorted_data[-1]
    return {
        "period": period,
        "rate_pct": rate,
        "label": "RBI Repo Rate (MPC decision)",
    }


def get_real_rate(cpi_inflation_pct: Optional[float] = None) -> Dict[str, object]:
    """
    Compute the current real interest rate (G-Sec 10Y yield minus CPI inflation).

    Uses the Fisher approximation:  real_rate ≈ nominal_yield − CPI

    Parameters
    ----------
    cpi_inflation_pct : float, optional
        CPI inflation to use. Defaults to the module constant
        ``ASSUMED_CPI_INFLATION_PCT`` (currently 5.5%).

    Returns
    -------
    dict
        - ``gsec_10y_yield_pct`` : Current 10Y G-Sec yield (%)
        - ``cpi_inflation_pct``  : CPI used (%)
        - ``real_rate_pct``      : Computed real rate (%)
        - ``interpretation``     : Plain-English signal
        - ``period``             : Data period (YYYY-MM)

    Examples
    --------
    >>> info = get_real_rate()
    >>> info["real_rate_pct"]
    1.0   # approximate
    """
    inflation = cpi_inflation_pct if cpi_inflation_pct is not None else ASSUMED_CPI_INFLATION_PCT

    gsec_data = get_current_gsec_yield()
    nominal = gsec_data["yield_pct"]
    real_rate = round(nominal - inflation, 2)

    if real_rate < 0:
        interpretation = "Negative real rate – financial repression regime; equities and real assets favoured"
    elif real_rate < 0.5:
        interpretation = "Near-zero real rate – mildly stimulative; neutral for fixed income"
    elif real_rate < 1.5:
        interpretation = "Moderate positive real rate – balanced macro environment"
    else:
        interpretation = "High real rate – restrictive monetary conditions; bonds may outperform equities"

    return {
        "period": gsec_data["period"],
        "gsec_10y_yield_pct": nominal,
        "cpi_inflation_pct": inflation,
        "real_rate_pct": real_rate,
        "interpretation": interpretation,
    }


def get_yield_curve_signal(two_year_yield_pct: Optional[float] = None) -> Dict[str, object]:
    """
    Determine the yield-curve shape (10Y vs 2Y spread).

    A positive spread → normal (upward-sloping) curve.
    Near-zero spread → flat curve (economic slowdown signal).
    Negative spread → inverted curve (recession warning).

    Parameters
    ----------
    two_year_yield_pct : float, optional
        2-Year G-Sec yield to use. Defaults to module constant
        ``GSEC_2Y_YIELD_PCT`` (currently 6.75%).

    Returns
    -------
    dict
        - ``ten_year_yield_pct`` : Current 10Y yield (%)
        - ``two_year_yield_pct`` : 2Y yield used (%)
        - ``spread_bps``         : 10Y – 2Y spread in basis points
        - ``curve_shape``        : 'normal', 'flat', or 'inverted'
        - ``signal``             : Investment implication

    Examples
    --------
    >>> sig = get_yield_curve_signal()
    >>> sig["curve_shape"]
    'inverted'   # when 10Y < 2Y
    """
    y2 = two_year_yield_pct if two_year_yield_pct is not None else GSEC_2Y_YIELD_PCT
    gsec_data = get_current_gsec_yield()
    y10 = gsec_data["yield_pct"]
    spread_bps = round((y10 - y2) * 100, 1)

    if spread_bps < -10:
        curve_shape = "inverted"
        signal = (
            "Inverted curve: short rates above long rates – historically a recession/slowdown precursor. "
            "Consider reducing duration; overweight short-end bonds and liquid instruments."
        )
    elif spread_bps < 20:
        curve_shape = "flat"
        signal = (
            "Flat curve: minimal term premium. Markets pricing rate cuts ahead. "
            "Neutral duration stance; watch MPC guidance closely."
        )
    else:
        curve_shape = "normal"
        signal = (
            "Normal upward-sloping curve: healthy term premium. "
            "Long-duration bonds offer compensation for holding period risk."
        )

    return {
        "period": gsec_data["period"],
        "ten_year_yield_pct": y10,
        "two_year_yield_pct": y2,
        "spread_bps": spread_bps,
        "curve_shape": curve_shape,
        "signal": signal,
    }


def get_market_stress_level() -> Dict[str, object]:
    """
    Assess current fixed-income market stress using repo rate trend and G-Sec yield level.

    Logic:
    - Rate-hike cycles with high yields → 'high' or 'extreme' stress
    - Stable/declining rate with normalising yields → 'low' or 'moderate'

    Returns
    -------
    dict
        - ``stress_level``         : 'low', 'moderate', 'high', or 'extreme'
        - ``gsec_10y_yield_pct``   : Current 10Y yield
        - ``repo_rate_pct``        : Current repo rate
        - ``repo_trend``           : '↑ hiking', '→ on hold', or '↓ easing'
        - ``interpretation``       : Narrative summary

    Examples
    --------
    >>> stress = get_market_stress_level()
    >>> stress["stress_level"]
    'low'
    """
    sorted_repo = _sorted_series(RBI_REPO_RATE)
    current_repo = sorted_repo[-1][1]
    prev_repo_3m = sorted_repo[-4][1] if len(sorted_repo) >= 4 else sorted_repo[0][1]

    gsec_data = get_current_gsec_yield()
    current_yield = gsec_data["yield_pct"]

    # Repo trend
    if current_repo > prev_repo_3m + 0.10:
        repo_trend = "↑ hiking"
        trend_score = 2
    elif current_repo < prev_repo_3m - 0.10:
        repo_trend = "↓ easing"
        trend_score = -1
    else:
        repo_trend = "→ on hold"
        trend_score = 0

    # Yield stress score
    if current_yield > 7.5:
        yield_score = 3
    elif current_yield > 7.0:
        yield_score = 2
    elif current_yield > 6.5:
        yield_score = 1
    else:
        yield_score = 0

    total_score = trend_score + yield_score

    if total_score >= 4:
        stress_level = "extreme"
        interpretation = (
            "Extreme stress: aggressive rate hikes with very high yields. "
            "Significant mark-to-market losses in bond portfolios likely. "
            "Shorten duration; avoid long-duration bonds."
        )
    elif total_score >= 2:
        stress_level = "high"
        interpretation = (
            "High stress: policy rate or market yields elevated. "
            "Duration risk is significant. Prefer floating-rate instruments "
            "and short-maturity bonds."
        )
    elif total_score >= 1:
        stress_level = "moderate"
        interpretation = (
            "Moderate stress: rates stable but still at cyclical highs. "
            "Balanced duration stance. Liquid funds and short-duration bonds attractive."
        )
    else:
        stress_level = "low"
        interpretation = (
            "Low stress: easing monetary cycle with declining yields. "
            "Positive environment for long-duration bonds. "
            "Consider increasing duration in fixed-income allocation."
        )

    return {
        "period": gsec_data["period"],
        "stress_level": stress_level,
        "gsec_10y_yield_pct": current_yield,
        "repo_rate_pct": current_repo,
        "repo_trend": repo_trend,
        "interpretation": interpretation,
    }


def get_rate_history(
    instrument: str = "gsec_10y",
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> List[Dict[str, object]]:
    """
    Return historical rate/yield series, optionally filtered by date range.

    Parameters
    ----------
    instrument : str
        'gsec_10y' for 10-Year G-Sec yield, 'repo' for RBI Repo Rate.
    start : str, optional
        Start month inclusive (YYYY-MM).
    end : str, optional
        End month inclusive (YYYY-MM).

    Returns
    -------
    list of dict
        Each entry: ``period`` (YYYY-MM) and ``value`` (%).

    Raises
    ------
    ValueError
        If *instrument* is unrecognised.

    Examples
    --------
    >>> history = get_rate_history("repo", start="2022-05", end="2023-02")
    >>> [r["value"] for r in history]
    [4.4, 4.9, 4.9, 5.4, 5.9, 5.9, 5.9, 6.25, 6.5]
    """
    if instrument == "gsec_10y":
        series = GSEC_10Y_YIELDS
        label = "10Y G-Sec Yield (%)"
    elif instrument == "repo":
        series = RBI_REPO_RATE
        label = "RBI Repo Rate (%)"
    else:
        raise ValueError(f"Unknown instrument '{instrument}'. Use 'gsec_10y' or 'repo'.")

    result = []
    for period, value in _sorted_series(series):
        if start and period < start:
            continue
        if end and period > end:
            continue
        result.append({"period": period, "value": value, "label": label})

    return result


def get_macro_snapshot() -> Dict[str, object]:
    """
    Return a consolidated macro snapshot combining all key fixed-income signals.

    Returns
    -------
    dict
        Combines output from get_real_rate(), get_yield_curve_signal(),
        get_market_stress_level(), and current repo rate.

    Examples
    --------
    >>> snap = get_macro_snapshot()
    >>> snap["stress_level"]
    'low'
    """
    real_rate = get_real_rate()
    curve = get_yield_curve_signal()
    stress = get_market_stress_level()
    repo = get_current_repo_rate()

    return {
        "data_period": real_rate["period"],
        "repo_rate_pct": repo["rate_pct"],
        "gsec_10y_yield_pct": real_rate["gsec_10y_yield_pct"],
        "gsec_2y_yield_pct": curve["two_year_yield_pct"],
        "spread_10y_2y_bps": curve["spread_bps"],
        "cpi_inflation_pct": real_rate["cpi_inflation_pct"],
        "real_rate_pct": real_rate["real_rate_pct"],
        "curve_shape": curve["curve_shape"],
        "stress_level": stress["stress_level"],
        "repo_trend": stress["repo_trend"],
        "signals": {
            "real_rate": real_rate["interpretation"],
            "yield_curve": curve["signal"],
            "market_stress": stress["interpretation"],
        },
    }


# ---------------------------------------------------------------------------
# 6.  MODULE SELF-TEST
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    print("=" * 60)
    print("Project Atlas – Bond Data Module Self-Test")
    print("=" * 60)

    print("\n[1] Current G-Sec 10Y Yield")
    print(json.dumps(get_current_gsec_yield(), indent=2))

    print("\n[2] Current Repo Rate")
    print(json.dumps(get_current_repo_rate(), indent=2))

    print("\n[3] Real Rate")
    print(json.dumps(get_real_rate(), indent=2))

    print("\n[4] Yield Curve Signal")
    print(json.dumps(get_yield_curve_signal(), indent=2))

    print("\n[5] Market Stress Level")
    print(json.dumps(get_market_stress_level(), indent=2))

    print("\n[6] Full Macro Snapshot")
    print(json.dumps(get_macro_snapshot(), indent=2))

    print("\n[7] Repo Rate History (Jan 2022 – Mar 2023)")
    history = get_rate_history("repo", start="2022-01", end="2023-03")
    for h in history:
        print(f"  {h['period']}: {h['value']}%")

    print("\n[8] Bond Instruments Catalogue")
    for k, v in BOND_INSTRUMENTS.items():
        print(f"  {k}: {v['display_name']} | Yield: {v['typical_yield_range_pct']} | Risk: {v['risk_level']}")
