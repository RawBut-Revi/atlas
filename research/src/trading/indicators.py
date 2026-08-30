"""
Project Atlas — Technical Indicators for Intraday Trading
Dual-engine: Runs with pandas/numpy IF available, OR in pure lightweight Python
with zero dependencies (perfect for mobile Termux and embedded devices).
"""

import math

try:
    import numpy as np
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False


# ─── Pure Python Implementations (Zero Dependencies) ─────────────

def calculate_ema_pure(values: list[float], period: int) -> list[float]:
    """Calculates EMA for a list of floats in pure Python."""
    if not values or len(values) < 1:
        return []
    alpha = 2.0 / (period + 1.0)
    ema = [values[0]]
    for v in values[1:]:
        ema.append(alpha * v + (1.0 - alpha) * ema[-1])
    return ema


def calculate_rsi_pure(closes: list[float], period: int = 14) -> list[float]:
    """Calculates RSI for a list of closes in pure Python."""
    if len(closes) < period + 1:
        return [50.0] * len(closes)

    gains = [0.0]
    losses = [0.0]
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(diff if diff > 0 else 0.0)
        losses.append(-diff if diff < 0 else 0.0)

    # Initial average
    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period

    rsi_values = [50.0] * period
    if avg_loss == 0:
        rsi_values.append(100.0)
    else:
        rs = avg_gain / avg_loss
        rsi_values.append(100.0 - (100.0 / (1.0 + rs)))

    # Exponential smoothing
    for i in range(period + 1, len(closes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi_values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(100.0 - (100.0 / (1.0 + rs)))

    return rsi_values


def calculate_atr_pure(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    """Calculates latest ATR in pure Python."""
    if len(closes) < 2:
        return (highs[-1] - lows[-1]) if highs and lows else 0.0

    tr_list = []
    for i in range(1, len(closes)):
        h_l = highs[i] - lows[i]
        h_pc = abs(highs[i] - closes[i - 1])
        l_pc = abs(lows[i] - closes[i - 1])
        tr_list.append(max(h_l, h_pc, l_pc))

    recent_tr = tr_list[-period:] if len(tr_list) >= period else tr_list
    return sum(recent_tr) / max(len(recent_tr), 1)


def calculate_volume_ratio_pure(volumes: list[int | float], period: int = 20) -> float:
    """Calculates latest volume divided by average volume."""
    if not volumes:
        return 1.0
    recent = volumes[-period:] if len(volumes) >= period else volumes
    avg_vol = sum(recent) / max(len(recent), 1)
    return (volumes[-1] / avg_vol) if avg_vol > 0 else 1.0


# ─── Pandas Implementations (Fast when pandas is available) ───────

def calculate_rsi(df, period: int = 14):
    if not HAS_PANDAS or not isinstance(df, pd.DataFrame):
        closes = [row["close"] for row in df] if isinstance(df, list) else list(df)
        return calculate_rsi_pure(closes, period)
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calculate_ema(series, period: int):
    if not HAS_PANDAS or not isinstance(series, (pd.Series, pd.DataFrame)):
        vals = list(series)
        return calculate_ema_pure(vals, period)
    return series.ewm(span=period, adjust=False).mean()


def calculate_vwap(df):
    if not HAS_PANDAS or not isinstance(df, pd.DataFrame):
        return [row["close"] for row in df]
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    cum_tp_vol = (typical_price * df["volume"]).cumsum()
    cum_vol = df["volume"].cumsum()
    return cum_tp_vol / cum_vol.replace(0, np.nan)


def calculate_macd(df, fast: int = 12, slow: int = 26, signal_period: int = 9):
    if not HAS_PANDAS or not isinstance(df, pd.DataFrame):
        closes = [row["close"] for row in df] if isinstance(df, list) else list(df)
        ema_f = calculate_ema_pure(closes, fast)
        ema_s = calculate_ema_pure(closes, slow)
        macd_l = [f - s for f, s in zip(ema_f, ema_s)]
        sig_l = calculate_ema_pure(macd_l, signal_period)
        return macd_l, sig_l, [m - s for m, s in zip(macd_l, sig_l)]
    ema_fast = calculate_ema(df["close"], fast)
    ema_slow = calculate_ema(df["close"], slow)
    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, signal_period)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calculate_bollinger_bands(df, period: int = 20, std_dev: float = 2.0):
    if not HAS_PANDAS or not isinstance(df, pd.DataFrame):
        closes = [row["close"] for row in df] if isinstance(df, list) else list(df)
        mid = sum(closes[-period:]) / min(len(closes), period)
        return [mid * 1.02] * len(closes), [mid] * len(closes), [mid * 0.98] * len(closes)
    middle = df["close"].rolling(window=period).mean()
    std = df["close"].rolling(window=period).std()
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return upper, middle, lower


def calculate_volume_ratio(df, period: int = 20):
    if not HAS_PANDAS or not isinstance(df, pd.DataFrame):
        vols = [row["volume"] for row in df] if isinstance(df, list) else list(df)
        return calculate_volume_ratio_pure(vols, period)
    avg_vol = df["volume"].rolling(window=period).mean()
    return df["volume"] / avg_vol.replace(0, np.nan)


def calculate_atr(df, period: int = 14):
    if not HAS_PANDAS or not isinstance(df, pd.DataFrame):
        highs = [r["high"] for r in df]
        lows = [r["low"] for r in df]
        closes = [r["close"] for r in df]
        return calculate_atr_pure(highs, lows, closes, period)
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift(1)).abs()
    low_close = (df["low"] - df["close"].shift(1)).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return true_range.rolling(window=period).mean()
