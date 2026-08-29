"""
Project Atlas - Volatility Indicators Library
===============================================
Volatility estimators ranging from simple (ATR, Bollinger Bands) to
advanced OHLC-based estimators (Garman-Klass, Yang-Zhang) that are
statistically more efficient than close-to-close volatility.
"""

import pandas as pd
import numpy as np


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    Average True Range (ATR).
    
    Measures market volatility by decomposing the entire range of an asset
    price for that period. The True Range accounts for gaps between sessions.
    
    True Range = max(High - Low, |High - Prev Close|, |Low - Prev Close|)
    ATR = Wilder's Smoothed Average of True Range
    
    Args:
        df: DataFrame with 'high', 'low', 'close' columns.
        period: Lookback period (standard is 14).
    
    Returns:
        DataFrame with 'atr_{period}' column appended.
    """
    high = df['high']
    low = df['low']
    prev_close = df['close'].shift(1)
    
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    # Wilder's smoothing
    df[f'atr_{period}'] = true_range.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    
    return df


def calculate_bollinger_bands(df: pd.DataFrame, column: str = 'close', 
                               period: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    """
    Bollinger Bands.
    
    A volatility band placed above and below a moving average. Volatility
    is based on standard deviation, which changes as volatility increases
    or decreases. The bands automatically widen when volatility increases
    and narrow when volatility decreases.
    
    Key signals:
        - Price near upper band = potentially overbought
        - Price near lower band = potentially oversold
        - Band squeeze (narrow bands) = low volatility, breakout expected
    
    Args:
        df: DataFrame with OHLCV data.
        column: Column to calculate bands on.
        period: Lookback period for the moving average (default 20).
        num_std: Number of standard deviations for the bands (default 2.0).
    
    Returns:
        DataFrame with 'bb_upper', 'bb_middle', 'bb_lower', 'bb_width', 'bb_pct' appended.
    """
    middle = df[column].rolling(window=period).mean()
    std = df[column].rolling(window=period).std()
    
    df['bb_upper'] = middle + (std * num_std)
    df['bb_middle'] = middle
    df['bb_lower'] = middle - (std * num_std)
    
    # Bandwidth: how wide the bands are relative to the middle band
    # Useful for detecting "squeeze" (low volatility regimes)
    df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_middle']
    
    # %B: where the price is relative to the bands (0 = lower, 1 = upper)
    df['bb_pct'] = (df[column] - df['bb_lower']) / (df['bb_upper'] - df['bb_lower'])
    
    return df


def calculate_historical_volatility(df: pd.DataFrame, column: str = 'close', 
                                     period: int = 20) -> pd.DataFrame:
    """
    Historical (Realized) Volatility.
    
    Annualized standard deviation of log returns. This is the simplest
    volatility estimator using only closing prices.
    
    Args:
        df: DataFrame with OHLCV data.
        column: Column to calculate on.
        period: Rolling window size (default 20 trading days ~1 month).
    
    Returns:
        DataFrame with 'hist_vol_{period}' column appended (annualized).
    """
    log_returns = np.log(df[column] / df[column].shift(1))
    # Annualize: multiply by sqrt(252 trading days)
    df[f'hist_vol_{period}'] = log_returns.rolling(window=period).std() * np.sqrt(252)
    
    return df


def calculate_garman_klass_volatility(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """
    Garman-Klass Volatility Estimator.
    
    Uses OHLC data to estimate volatility more efficiently than close-to-close.
    Approximately 8x more efficient (lower variance) than the classical
    close-to-close estimator.
    
    Formula per bar:
        0.5 * ln(H/L)^2 - (2*ln(2) - 1) * ln(C/O)^2
    
    Reference: Garman & Klass (1980)
    
    Args:
        df: DataFrame with 'open', 'high', 'low', 'close' columns.
        period: Rolling window for averaging (default 20).
    
    Returns:
        DataFrame with 'gk_vol_{period}' column appended (annualized).
    """
    log_hl = np.log(df['high'] / df['low'])
    log_co = np.log(df['close'] / df['open'])
    
    gk = 0.5 * log_hl**2 - (2 * np.log(2) - 1) * log_co**2
    
    # Rolling mean, then annualize
    df[f'gk_vol_{period}'] = np.sqrt(gk.rolling(window=period).mean() * 252)
    
    return df


def calculate_yang_zhang_volatility(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """
    Yang-Zhang Volatility Estimator.
    
    The BEST single OHLC-based volatility estimator. It combines:
        1. Overnight volatility (close-to-open gaps)
        2. Open-to-close volatility (intraday range)
        3. Rogers-Satchell volatility (uses full OHLC)
    
    This is particularly important for Indian equities because of the
    frequent overnight gap-ups and gap-downs due to global cues.
    
    Reference: Yang & Zhang (2000)
    
    Args:
        df: DataFrame with 'open', 'high', 'low', 'close' columns.
        period: Rolling window for averaging (default 20).
    
    Returns:
        DataFrame with 'yz_vol_{period}' column appended (annualized).
    """
    log_ho = np.log(df['high'] / df['open'])
    log_lo = np.log(df['low'] / df['open'])
    log_co = np.log(df['close'] / df['open'])
    
    # Overnight returns (close-to-open)
    log_oc = np.log(df['open'] / df['close'].shift(1))
    
    # Rogers-Satchell component
    rs = log_ho * (log_ho - log_co) + log_lo * (log_lo - log_co)
    
    # Overnight variance
    close_vol = log_oc.rolling(window=period).var()
    
    # Open-to-close variance
    open_vol = log_co.rolling(window=period).var()
    
    # Rogers-Satchell variance
    rs_vol = rs.rolling(window=period).mean()
    
    # Yang-Zhang weighting constant
    k = 0.34 / (1.34 + (period + 1) / (period - 1))
    
    # Combined Yang-Zhang variance
    yz_var = close_vol + k * open_vol + (1 - k) * rs_vol
    
    # Annualize
    df[f'yz_vol_{period}'] = np.sqrt(yz_var * 252)
    
    return df
