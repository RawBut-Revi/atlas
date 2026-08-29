"""
Project Atlas — Quantitative Trading Strategy Engine

Combines:
  1. Connors RSI(2) Mean Reversion on Pullbacks (High Precision)
  2. EMA(50) & EMA(20) Macro Trend Filter
  3. Volume Surge Confirmation (Institutions)
  4. ATR-based Dynamic Stop-Loss and Target (1:1.5 Risk-to-Reward)
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Optional

from .indicators import (
    calculate_rsi,
    calculate_ema,
    calculate_vwap,
    calculate_macd,
    calculate_bollinger_bands,
    calculate_volume_ratio,
    calculate_atr,
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


def generate_signal(symbol: str, df: pd.DataFrame) -> TradeSignal:
    """
    Generate quantitative trade signal for a given stock based on historical candles.
    """
    if df is None or len(df) < 50:
        return _empty_signal(symbol)

    rsi2 = calculate_rsi(df, period=2)
    rsi14 = calculate_rsi(df, period=14)
    ema20 = calculate_ema(df["close"], 20)
    ema50 = calculate_ema(df["close"], 50)
    macd_line, signal_line, _ = calculate_macd(df)
    vol_ratio = calculate_volume_ratio(df)
    atr = calculate_atr(df)
    bb_upper, bb_mid, bb_lower = calculate_bollinger_bands(df)

    curr = df.iloc[-1]
    prev = df.iloc[-2]
    price = curr["close"]

    r2 = rsi2.iloc[-1]
    r14 = rsi14.iloc[-1]
    e20 = ema20.iloc[-1]
    e50 = ema50.iloc[-1]
    v_rat = vol_ratio.iloc[-1] if not pd.isna(vol_ratio.iloc[-1]) else 1.0
    curr_atr = atr.iloc[-1] if not pd.isna(atr.iloc[-1]) else price * 0.015

    if any(pd.isna(x) for x in [r2, r14, e20, e50, price]):
        return _empty_signal(symbol)

    # Trend Determination
    if price > e50 and e20 > e50:
        trend = "BULLISH"
    elif price < e50 and e20 < e50:
        trend = "BEARISH"
    else:
        trend = "SIDEWAYS"

    # Strategy 1: High-Probability Pullback in Trend
    # BUY: In Bullish Trend, extreme short-term pullback (RSI2 <= 15 or price near BB Lower)
    is_pullback_buy = (trend == "BULLISH" or price > e50) and (r2 <= 20 or price <= bb_lower.iloc[-1] * 1.01)
    
    # SELL: In Bearish Trend, extreme short-term rally (RSI2 >= 80 or price near BB Upper)
    is_pullback_sell = (trend == "BEARISH" or price < e50) and (r2 >= 80 or price >= bb_upper.iloc[-1] * 0.99)

    # Strategy 2: Momentum Volume Breakout
    is_breakout_buy = (price > prev["high"]) and (v_rat >= 1.2) and (r14 >= 55) and (trend == "BULLISH")
    is_breakout_sell = (price < prev["low"]) and (v_rat >= 1.2) and (r14 <= 45) and (trend == "BEARISH")

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
