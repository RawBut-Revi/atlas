"""
Project Atlas — Technical Indicators for Intraday Trading
All indicators are computed on pandas DataFrames with columns:
  timestamp, open, high, low, close, volume
"""

import numpy as np
import pandas as pd


def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Relative Strength Index (RSI)."""
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    """Volume-Weighted Average Price (intraday, resets daily)."""
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    cum_tp_vol = (typical_price * df["volume"]).cumsum()
    cum_vol = df["volume"].cumsum()
    return cum_tp_vol / cum_vol.replace(0, np.nan)


def calculate_macd(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD line, Signal line, Histogram."""
    ema_fast = calculate_ema(df["close"], fast)
    ema_slow = calculate_ema(df["close"], slow)
    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, signal_period)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calculate_bollinger_bands(
    df: pd.DataFrame, period: int = 20, std_dev: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Upper Band, Middle Band (SMA), Lower Band."""
    middle = df["close"].rolling(window=period).mean()
    std = df["close"].rolling(window=period).std()
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return upper, middle, lower


def calculate_volume_ratio(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Current volume divided by rolling average volume."""
    avg_vol = df["volume"].rolling(window=period).mean()
    return df["volume"] / avg_vol.replace(0, np.nan)


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range — used for stop-loss calculation."""
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift(1)).abs()
    low_close = (df["low"] - df["close"].shift(1)).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return true_range.rolling(window=period).mean()


def calculate_support_resistance(
    df: pd.DataFrame, lookback: int = 20
) -> tuple[float, float]:
    """Simple support/resistance from recent highs and lows."""
    recent = df.tail(lookback)
    support = recent["low"].min()
    resistance = recent["high"].max()
    return support, resistance
