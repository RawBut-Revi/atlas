"""
Project Atlas - Statistical Features Library
==============================================
Functions to calculate statistical properties of price data.
These features form the foundation for quantitative analysis:
log returns, rolling volatility, z-scores, and more.
"""

import pandas as pd
import numpy as np


def calculate_log_returns(df: pd.DataFrame, column: str = 'close') -> pd.DataFrame:
    """
    Log Returns (Continuously Compounded Returns).
    
    Log returns are preferred in quantitative finance because:
        1. They are time-additive (multi-period return = sum of single-period returns).
        2. They are approximately normally distributed for short intervals.
        3. They prevent negative prices in models.
    
    Formula: ln(P_t / P_{t-1})
    
    Args:
        df: DataFrame with price data.
        column: Column to calculate returns on.
    
    Returns:
        DataFrame with 'log_return' column appended.
    """
    df['log_return'] = np.log(df[column] / df[column].shift(1))
    return df


def calculate_pct_returns(df: pd.DataFrame, column: str = 'close', 
                          periods: list[int] = [1, 5, 10, 20]) -> pd.DataFrame:
    """
    Percentage Returns over multiple periods.
    
    Useful for analyzing momentum across different timeframes:
        - 1-day: very short-term momentum
        - 5-day: weekly momentum
        - 10-day: bi-weekly momentum
        - 20-day: monthly momentum
    
    Args:
        df: DataFrame with price data.
        column: Column to calculate returns on.
        periods: List of lookback periods for return calculation.
    
    Returns:
        DataFrame with 'pct_return_{period}' columns appended.
    """
    for period in periods:
        df[f'pct_return_{period}'] = df[column].pct_change(periods=period)
    return df


def calculate_rolling_volatility(df: pd.DataFrame, column: str = 'close',
                                  periods: list[int] = [10, 20, 60]) -> pd.DataFrame:
    """
    Rolling Volatility (Annualized Standard Deviation of Log Returns).
    
    Provides volatility estimates over different time horizons.
    Comparing short-term vs long-term volatility can signal regime changes.
    
    Args:
        df: DataFrame with price data.
        column: Column to calculate on.
        periods: List of rolling window sizes.
    
    Returns:
        DataFrame with 'rolling_vol_{period}' columns appended (annualized).
    """
    log_returns = np.log(df[column] / df[column].shift(1))
    
    for period in periods:
        df[f'rolling_vol_{period}'] = log_returns.rolling(window=period).std() * np.sqrt(252)
    return df


def calculate_z_score(df: pd.DataFrame, column: str = 'close', period: int = 20) -> pd.DataFrame:
    """
    Z-Score (Rolling Standardization).
    
    Measures how many standard deviations the current price is from its
    rolling mean. Extremely useful for mean-reversion strategies:
        - Z > +2: Price is extended above the mean (potential short)
        - Z < -2: Price is extended below the mean (potential long)
    
    Formula: Z = (X - μ) / σ
    
    Args:
        df: DataFrame with price data.
        column: Column to calculate Z-score on.
        period: Rolling window for mean and std calculation.
    
    Returns:
        DataFrame with 'z_score_{period}' column appended.
    """
    rolling_mean = df[column].rolling(window=period).mean()
    rolling_std = df[column].rolling(window=period).std()
    
    df[f'z_score_{period}'] = (df[column] - rolling_mean) / rolling_std
    return df


def calculate_volume_features(df: pd.DataFrame, periods: list[int] = [10, 20]) -> pd.DataFrame:
    """
    Volume-Based Features.
    
    Volume confirms price action. A breakout on high volume is more
    significant than one on low volume.
    
    Features:
        - Volume SMA: average volume over period
        - Volume Ratio: current volume / average volume (>1 = above average)
        - OBV (On-Balance Volume): cumulative volume direction indicator
    
    Args:
        df: DataFrame with 'volume' and 'close' columns.
        periods: List of periods for volume averaging.
    
    Returns:
        DataFrame with volume feature columns appended.
    """
    for period in periods:
        vol_sma = df['volume'].rolling(window=period).mean()
        df[f'vol_sma_{period}'] = vol_sma
        df[f'vol_ratio_{period}'] = df['volume'] / vol_sma
    
    # On-Balance Volume (OBV)
    # If close > prev close, add volume. If close < prev close, subtract volume.
    obv_direction = np.where(df['close'] > df['close'].shift(1), df['volume'],
                    np.where(df['close'] < df['close'].shift(1), -df['volume'], 0))
    df['obv'] = np.cumsum(obv_direction)
    
    return df
