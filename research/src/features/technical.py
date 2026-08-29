"""
Project Atlas - Technical Indicators Library
=============================================
Standard technical indicators for trend, momentum, and signal analysis.
All functions accept a pandas DataFrame with OHLCV columns and return
the DataFrame with new feature columns appended.
"""

import pandas as pd
import numpy as np


def calculate_sma(df: pd.DataFrame, column: str = 'close', periods: list[int] = [20, 50, 200]) -> pd.DataFrame:
    """
    Simple Moving Average (SMA).
    
    Smooths out price data by creating a constantly updated average price.
    Useful for identifying trend direction and support/resistance levels.
    
    Args:
        df: DataFrame with OHLCV data.
        column: Column to calculate SMA on.
        periods: List of lookback periods.
    
    Returns:
        DataFrame with SMA columns appended (e.g., sma_20, sma_50, sma_200).
    """
    for period in periods:
        df[f'sma_{period}'] = df[column].rolling(window=period).mean()
    return df


def calculate_ema(df: pd.DataFrame, column: str = 'close', periods: list[int] = [12, 26, 50]) -> pd.DataFrame:
    """
    Exponential Moving Average (EMA).
    
    Gives more weight to recent prices, making it more responsive to new
    information than SMA. Widely used in MACD calculation.
    
    Args:
        df: DataFrame with OHLCV data.
        column: Column to calculate EMA on.
        periods: List of lookback periods.
    
    Returns:
        DataFrame with EMA columns appended (e.g., ema_12, ema_26, ema_50).
    """
    for period in periods:
        df[f'ema_{period}'] = df[column].ewm(span=period, adjust=False).mean()
    return df


def calculate_rsi(df: pd.DataFrame, column: str = 'close', period: int = 14) -> pd.DataFrame:
    """
    Relative Strength Index (RSI).
    
    Momentum oscillator that measures the speed and magnitude of recent price
    changes to evaluate overbought (>70) or oversold (<30) conditions.
    
    Uses the Wilder smoothing method (exponential moving average) for accuracy.
    
    Args:
        df: DataFrame with OHLCV data.
        column: Column to calculate RSI on.
        period: Lookback period (standard is 14).
    
    Returns:
        DataFrame with 'rsi_{period}' column appended.
    """
    delta = df[column].diff()
    
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    
    # Wilder's smoothing (equivalent to EMA with alpha = 1/period)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    
    df[f'rsi_{period}'] = rsi
    return df


def calculate_macd(df: pd.DataFrame, column: str = 'close', 
                   fast_period: int = 12, slow_period: int = 26, 
                   signal_period: int = 9) -> pd.DataFrame:
    """
    Moving Average Convergence Divergence (MACD).
    
    Trend-following momentum indicator that shows the relationship between
    two EMAs. The MACD histogram is particularly useful for detecting
    momentum shifts.
    
    Components:
        - MACD Line = EMA(fast) - EMA(slow)
        - Signal Line = EMA(MACD Line, signal_period)
        - Histogram = MACD Line - Signal Line
    
    Args:
        df: DataFrame with OHLCV data.
        column: Column to calculate MACD on.
        fast_period: Fast EMA period (default 12).
        slow_period: Slow EMA period (default 26).
        signal_period: Signal line EMA period (default 9).
    
    Returns:
        DataFrame with 'macd', 'macd_signal', 'macd_hist' columns appended.
    """
    ema_fast = df[column].ewm(span=fast_period, adjust=False).mean()
    ema_slow = df[column].ewm(span=slow_period, adjust=False).mean()
    
    df['macd'] = ema_fast - ema_slow
    df['macd_signal'] = df['macd'].ewm(span=signal_period, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_signal']
    
    return df
