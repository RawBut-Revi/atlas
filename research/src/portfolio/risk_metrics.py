"""
Project Atlas - Portfolio Risk Metrics
=======================================
Core risk measurement: Sharpe, Sortino, Max Drawdown, Beta,
Correlation Matrix, and Value at Risk (VaR).

These tell you not just HOW MUCH you made but HOW MUCH RISK
you took to make it — the quality of your returns matters.
"""

import math
from typing import List, Dict, Optional


# ============================================================
# RETURN CALCULATIONS
# ============================================================

def calculate_returns(prices: List[float]) -> List[float]:
    """Convert a list of prices to period-over-period returns."""
    if len(prices) < 2:
        return []
    return [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, len(prices))]


def annualized_return(returns: List[float], periods_per_year: int = 12) -> float:
    """Calculate CAGR from periodic returns."""
    if not returns:
        return 0.0
    cumulative = 1.0
    for r in returns:
        cumulative *= (1 + r)
    years = len(returns) / periods_per_year
    return (cumulative ** (1 / years) - 1) * 100 if years > 0 else 0.0


def annualized_volatility(returns: List[float], periods_per_year: int = 12) -> float:
    """Calculate annualized standard deviation of returns."""
    if len(returns) < 2:
        return 0.0
    n = len(returns)
    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std_dev = math.sqrt(variance)
    return std_dev * math.sqrt(periods_per_year) * 100  # annualized %


# ============================================================
# SHARPE RATIO
# ============================================================

def sharpe_ratio(
    returns: List[float],
    risk_free_rate_annual_pct: float = 6.5,
    periods_per_year: int = 12,
) -> float:
    """
    Sharpe Ratio: risk-adjusted return.

    Sharpe > 1.0 : Good
    Sharpe > 1.5 : Very good
    Sharpe > 2.0 : Excellent
    Sharpe < 0   : Worse than risk-free (FD/bonds) — not worth the risk

    Formula: (Portfolio Return - Risk-Free Rate) / Portfolio Std Dev

    Args:
        returns:                 List of periodic portfolio returns.
        risk_free_rate_annual_pct: RBI repo rate or FD rate as benchmark.
        periods_per_year:        12 for monthly data, 252 for daily.
    """
    if len(returns) < 3:
        return 0.0

    port_return = annualized_return(returns, periods_per_year)
    port_vol = annualized_volatility(returns, periods_per_year)

    if port_vol == 0:
        return 0.0

    return (port_return - risk_free_rate_annual_pct) / port_vol


# ============================================================
# SORTINO RATIO
# ============================================================

def sortino_ratio(
    returns: List[float],
    risk_free_rate_annual_pct: float = 6.5,
    periods_per_year: int = 12,
) -> float:
    """
    Sortino Ratio: like Sharpe but only penalizes DOWNSIDE volatility.

    More relevant for dividend investors who care about
    protecting capital, not just total volatility.

    Sortino > 1.5: Good
    Sortino > 2.0: Very good

    Formula: (Portfolio Return - Risk-Free Rate) / Downside Std Dev
    """
    if len(returns) < 3:
        return 0.0

    port_return = annualized_return(returns, periods_per_year)

    # Only negative returns count as risk
    downside = [r for r in returns if r < 0]
    if not downside:
        return float('inf')  # No downside = infinite Sortino

    downside_variance = sum(r ** 2 for r in downside) / len(downside)
    downside_std = math.sqrt(downside_variance) * math.sqrt(periods_per_year) * 100

    if downside_std == 0:
        return 0.0

    return (port_return - risk_free_rate_annual_pct) / downside_std


# ============================================================
# MAXIMUM DRAWDOWN
# ============================================================

def max_drawdown(prices: List[float]) -> Dict:
    """
    Maximum Drawdown: the worst peak-to-trough decline.

    This is the single most important risk metric for
    understanding how bad things can get.

    DD < 10%: Low risk portfolio
    DD 10-20%: Moderate (typical for balanced portfolios)
    DD 20-40%: High (equity-heavy, normal for long-term investors)
    DD > 40%: Severe (consider reducing equity exposure)

    Returns:
        Dict with max_drawdown_pct, peak_value, trough_value.
    """
    if len(prices) < 2:
        return {"max_drawdown_pct": 0.0, "peak": 0, "trough": 0}

    peak = prices[0]
    max_dd = 0.0
    peak_val = prices[0]
    trough_val = prices[0]

    for price in prices:
        if price > peak:
            peak = price
        dd = (peak - price) / peak
        if dd > max_dd:
            max_dd = dd
            peak_val = peak
            trough_val = price

    return {
        "max_drawdown_pct": round(max_dd * 100, 2),
        "peak_value": round(peak_val, 2),
        "trough_value": round(trough_val, 2),
    }


# ============================================================
# CORRELATION MATRIX
# ============================================================

def pearson_correlation(series_a: List[float], series_b: List[float]) -> float:
    """
    Calculate Pearson correlation between two return series.

    +1.0 : Perfect positive correlation (move together)
     0.0 : No correlation (independent)
    -1.0 : Perfect negative correlation (perfect hedge)

    For hedging, we want assets with correlation close to -1 or 0.
    """
    n = min(len(series_a), len(series_b))
    if n < 3:
        return 0.0

    a = series_a[:n]
    b = series_b[:n]

    mean_a = sum(a) / n
    mean_b = sum(b) / n

    cov = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n))
    std_a = math.sqrt(sum((x - mean_a) ** 2 for x in a))
    std_b = math.sqrt(sum((x - mean_b) ** 2 for x in b))

    if std_a == 0 or std_b == 0:
        return 0.0

    return cov / (std_a * std_b)


def correlation_matrix(assets: Dict[str, List[float]]) -> Dict[str, Dict[str, float]]:
    """
    Build a full correlation matrix for a set of assets.

    Args:
        assets: Dict of asset_name -> list of returns.

    Returns:
        Nested dict: matrix[asset_a][asset_b] = correlation.
    """
    names = list(assets.keys())
    matrix = {}

    for a in names:
        matrix[a] = {}
        for b in names:
            if a == b:
                matrix[a][b] = 1.0
            else:
                # Convert prices to returns if needed
                returns_a = assets[a] if not all(r < 1 for r in assets[a]) else calculate_returns(assets[a])
                returns_b = assets[b] if not all(r < 1 for r in assets[b]) else calculate_returns(assets[b])
                matrix[a][b] = round(pearson_correlation(returns_a, returns_b), 3)

    return matrix


# ============================================================
# VALUE AT RISK (VaR)
# ============================================================

def value_at_risk(
    portfolio_value: float,
    returns: List[float],
    confidence: float = 0.95,
) -> Dict:
    """
    Historical Value at Risk (VaR).

    "With 95% confidence, the portfolio will NOT lose more
    than X rupees in a single period."

    Args:
        portfolio_value: Current portfolio value in INR.
        returns:         Historical periodic returns.
        confidence:      Confidence level (0.95 = 95%).

    Returns:
        Dict with var_inr and var_pct.
    """
    if not returns:
        return {"var_pct": 0.0, "var_inr": 0.0}

    sorted_returns = sorted(returns)
    index = int((1 - confidence) * len(sorted_returns))
    var_return = sorted_returns[max(0, index)]

    var_pct = abs(var_return) * 100
    var_inr = portfolio_value * abs(var_return)

    return {
        "confidence_pct": confidence * 100,
        "var_pct": round(var_pct, 2),
        "var_inr": round(var_inr, 2),
        "interpretation": (
            f"With {confidence*100:.0f}% confidence, max single-period loss "
            f"= ₹{var_inr:,.0f} ({var_pct:.2f}%)"
        ),
    }


# ============================================================
# FULL PORTFOLIO SCORECARD
# ============================================================

def portfolio_scorecard(
    portfolio_values: List[float],
    benchmark_values: List[float],
    risk_free_rate: float = 6.5,
) -> Dict:
    """
    Generate a complete risk scorecard for the portfolio.

    Args:
        portfolio_values:  Monthly portfolio values (INR).
        benchmark_values:  Monthly NIFTY50 values for comparison.
        risk_free_rate:    Annual risk-free rate % (RBI repo rate).

    Returns:
        Comprehensive risk metrics dict.
    """
    port_returns = calculate_returns(portfolio_values)
    bench_returns = calculate_returns(benchmark_values)

    port_cagr = annualized_return(port_returns)
    bench_cagr = annualized_return(bench_returns)
    port_vol = annualized_volatility(port_returns)
    sharpe = sharpe_ratio(port_returns, risk_free_rate)
    sortino = sortino_ratio(port_returns, risk_free_rate)
    dd = max_drawdown(portfolio_values)
    corr = pearson_correlation(port_returns, bench_returns)
    var = value_at_risk(portfolio_values[-1] if portfolio_values else 0, port_returns)

    alpha = port_cagr - bench_cagr  # Excess return vs benchmark

    return {
        "returns": {
            "portfolio_cagr_pct": round(port_cagr, 2),
            "benchmark_cagr_pct": round(bench_cagr, 2),
            "alpha_pct": round(alpha, 2),
        },
        "risk": {
            "annualized_volatility_pct": round(port_vol, 2),
            "max_drawdown_pct": dd["max_drawdown_pct"],
            "var_95_pct": var["var_pct"],
        },
        "risk_adjusted": {
            "sharpe_ratio": round(sharpe, 2),
            "sortino_ratio": round(sortino, 2),
            "benchmark_correlation": round(corr, 3),
        },
        "ratings": {
            "sharpe": "Excellent" if sharpe > 2 else "Good" if sharpe > 1 else "Average" if sharpe > 0 else "Poor",
            "drawdown": "Low" if dd["max_drawdown_pct"] < 10 else "Moderate" if dd["max_drawdown_pct"] < 20 else "High",
            "alpha": "Outperforming" if alpha > 0 else "Underperforming",
        },
    }
