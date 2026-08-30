"""
Project Atlas — Quantitative Trading Strategy Engine
Dual-mode: Runs with pandas if available, OR pure Python lists (zero dependencies for Termux/mobile).
"""

from dataclasses import dataclass
from typing import Optional

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

from .indicators import (
    calculate_rsi_pure,
    calculate_ema_pure,
    calculate_atr_pure,
    calculate_volume_ratio_pure,
    calculate_rsi,
    calculate_ema,
    calculate_atr,
    calculate_volume_ratio,
    calculate_bollinger_bands,
)


@dataclass
class TradeSignal:
    symbol: str
    direction: str  # "BUY", "SELL", or "NONE"
    confidence: int  # 0 to 100
    entry_price: float
    stop_loss: float
    target_price: float
    risk_reward: str
    rationale: str
    rsi2_value: float
    rsi14_value: float
    trend: str  # "BULLISH", "BEARISH", "SIDEWAYS"
    atr_value: float

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "confidence": self.confidence,
            "entry_price": round(self.entry_price, 2),
            "stop_loss": round(self.stop_loss, 2),
            "target_price": round(self.target_price, 2),
            "risk_reward": self.risk_reward,
            "rationale": self.rationale,
            "rsi2": round(self.rsi2_value, 1),
            "rsi14": round(self.rsi14_value, 1),
            "trend": self.trend,
            "atr": round(self.atr_value, 2),
        }


def generate_signal(symbol: str, df) -> TradeSignal:
    """
    Generate quantitative trade signal for a given stock based on historical candles.
    Supports both pandas DataFrame and standard list-of-dicts.
    """
    if df is None or len(df) < 50:
        return _empty_signal(symbol)

    if HAS_PANDAS and isinstance(df, pd.DataFrame):
        closes = list(df["close"])
        highs = list(df["high"])
        lows = list(df["low"])
        volumes = list(df["volume"])
    elif isinstance(df, list):
        closes = [r["close"] for r in df]
        highs = [r["high"] for r in df]
        lows = [r["low"] for r in df]
        volumes = [r["volume"] for r in df]
    else:
        return _empty_signal(symbol)

    price = closes[-1]
    prev_high = highs[-2]
    prev_low = lows[-2]

    # Calculate indicators (pure Python / vectorized)
    rsi2_list = calculate_rsi_pure(closes, period=2)
    rsi14_list = calculate_rsi_pure(closes, period=14)
    ema20_list = calculate_ema_pure(closes, 20)
    ema50_list = calculate_ema_pure(closes, 50)
    curr_atr = calculate_atr_pure(highs, lows, closes, 14)
    v_rat = calculate_volume_ratio_pure(volumes, 20)

    r2 = rsi2_list[-1]
    r14 = rsi14_list[-1]
    e20 = ema20_list[-1]
    e50 = ema50_list[-1]
    if curr_atr <= 0:
        curr_atr = price * 0.015

    # Trend Determination
    if price > e50 and e20 > e50:
        trend = "BULLISH"
    elif price < e50 and e20 < e50:
        trend = "BEARISH"
    else:
        trend = "SIDEWAYS"

    # Strategy 1: High-Probability Pullback in Trend (Connors RSI)
    is_pullback_buy = (trend == "BULLISH" or price > e50) and (r2 <= 20)
    is_pullback_sell = (trend == "BEARISH" or price < e50) and (r2 >= 80)

    # Strategy 2: Momentum Volume Breakout
    is_breakout_buy = (price > prev_high) and (v_rat >= 1.2) and (r14 >= 55) and (trend == "BULLISH")
    is_breakout_sell = (price < prev_low) and (v_rat >= 1.2) and (r14 <= 45) and (trend == "BEARISH")

    direction = "NONE"
    confidence = 0
    rationale = "No high-probability setup currently"

    if is_pullback_buy:
        direction = "BUY"
        confidence = 88 if r2 <= 10 else 78
        rationale = f"Oversold pullback in uptrend (RSI2: {r2:.1f}, above EMA50)"
    elif is_breakout_buy:
        direction = "BUY"
        confidence = 82
        rationale = f"Volume breakout above previous high (Vol: {v_rat:.1f}x avg)"
    elif is_pullback_sell:
        direction = "SELL"
        confidence = 88 if r2 >= 90 else 78
        rationale = f"Overbought rally in downtrend (RSI2: {r2:.1f}, below EMA50)"
    elif is_breakout_sell:
        direction = "SELL"
        confidence = 82
        rationale = f"Volume breakdown below previous low (Vol: {v_rat:.1f}x avg)"

    if direction == "NONE":
        return TradeSignal(
            symbol=symbol,
            direction="NONE",
            confidence=confidence,
            entry_price=price,
            stop_loss=0.0,
            target_price=0.0,
            risk_reward="1:1.5",
            rationale=rationale,
            rsi2_value=r2,
            rsi14_value=r14,
            trend=trend,
            atr_value=curr_atr,
        )

    # Risk-Reward 1:1.5 with ATR
    sl_distance = curr_atr * 0.75
    target_distance = sl_distance * 1.5

    if direction == "BUY":
        stop_loss = max(price - sl_distance, price * 0.95)
        target_price = price + target_distance
    else:
        stop_loss = min(price + sl_distance, price * 1.05)
        target_price = price - target_distance

    return TradeSignal(
        symbol=symbol,
        direction=direction,
        confidence=confidence,
        entry_price=price,
        stop_loss=stop_loss,
        target_price=target_price,
        risk_reward="1:1.5",
        rationale=rationale,
        rsi2_value=r2,
        rsi14_value=r14,
        trend=trend,
        atr_value=curr_atr,
    )


def _empty_signal(symbol: str) -> TradeSignal:
    return TradeSignal(
        symbol=symbol,
        direction="NONE",
        confidence=0,
        entry_price=0.0,
        stop_loss=0.0,
        target_price=0.0,
        risk_reward="1:1.5",
        rationale="Insufficient historical data",
        rsi2_value=50.0,
        rsi14_value=50.0,
        trend="UNKNOWN",
        atr_value=0.0,
    )
