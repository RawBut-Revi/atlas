"""
Project Atlas — Currency Futures Trading Engine
Trades USDINR, EURINR, GBPINR, JPYINR on NSE Currency Derivatives (NCD_FO).

Strategy Stack:
  1. EMA Ribbon Trend (10/20/50) + Pullback to Support / Resistance
  2. Bollinger Band (20, 2.0) Squeeze & Breakout
  3. Mean Reversion at Extremes (RSI Divergence)
  4. Dynamic ATR Risk Sizing (1:1.5 R:R)

Market Hours: 9:00 AM – 5:00 PM IST (extended hours vs equities).
Zero STT, Low Margin (~₹1,900/lot for USDINR).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, time
from typing import Optional
import requests

from .indicators import (
    calculate_rsi_pure,
    calculate_ema_pure,
    calculate_atr_pure,
    calculate_volume_ratio_pure,
)


# ─── Currency Pair Specifications ────────────────────────────────
@dataclass
class CurrencyPairSpec:
    symbol: str            # e.g. "USDINR"
    yfinance_ticker: str   # e.g. "USDINR=X"
    lot_size: int          # Contract lot size
    tick_size: float       # Minimum price movement
    tick_value: float      # Value per tick per lot in INR
    approx_margin: float   # Approx margin per lot in INR
    pip_size: float        # 1 pip in price terms


CURRENCY_PAIRS = {
    "USDINR": CurrencyPairSpec(
        symbol="USDINR", yfinance_ticker="USDINR=X",
        lot_size=1000, tick_size=0.0025, tick_value=2.5,
        approx_margin=1900.0, pip_size=0.01,
    ),
    "EURINR": CurrencyPairSpec(
        symbol="EURINR", yfinance_ticker="EURINR=X",
        lot_size=1000, tick_size=0.0025, tick_value=2.5,
        approx_margin=2400.0, pip_size=0.01,
    ),
    "GBPINR": CurrencyPairSpec(
        symbol="GBPINR", yfinance_ticker="GBPINR=X",
        lot_size=1000, tick_size=0.0025, tick_value=2.5,
        approx_margin=2800.0, pip_size=0.01,
    ),
    "JPYINR": CurrencyPairSpec(
        symbol="JPYINR", yfinance_ticker="JPYINR=X",
        lot_size=100000, tick_size=0.0025, tick_value=2.5,
        approx_margin=2200.0, pip_size=0.01,
    ),
}

# Currency market hours
CURRENCY_OPEN = time(9, 0)
CURRENCY_CLOSE = time(17, 0)
CURRENCY_SQUARE_OFF = time(16, 45)


@dataclass
class CurrencySignal:
    symbol: str
    direction: str         # "BUY", "SELL", or "NONE"
    confidence: int        # 0-100
    strategy: str          # "TREND_FOLLOW", "PULLBACK_DIP", "BOLLINGER_SQUEEZE", "MEAN_REVERSION", "NEUTRAL"
    entry_price: float
    stop_loss: float
    target_price: float
    lots: int
    risk_inr: float        # Risk in INR per trade
    rationale: str
    trend: str             # "BULLISH", "BEARISH", "SIDEWAYS"
    rsi14: float = 50.0
    ema20: float = 0.0
    ema50: float = 0.0
    atr: float = 0.0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "confidence": self.confidence,
            "strategy": self.strategy,
            "entry_price": round(self.entry_price, 4),
            "stop_loss": round(self.stop_loss, 4),
            "target_price": round(self.target_price, 4),
            "lots": self.lots,
            "risk_inr": round(self.risk_inr, 2),
            "rationale": self.rationale,
            "trend": self.trend,
            "rsi14": round(self.rsi14, 1),
            "ema20": round(self.ema20, 4),
            "ema50": round(self.ema50, 4),
            "atr": round(self.atr, 4),
            "signal_type": "CURRENCY",
        }


def fetch_currency_data(pair_symbol: str, days: int = 90) -> list[dict]:
    """
    Fetches historical daily currency data.
    Uses Yahoo Finance public API (no auth needed, works on Termux/mobile).
    """
    spec = CURRENCY_PAIRS.get(pair_symbol)
    if not spec:
        return []

    ticker = spec.yfinance_ticker
    end_ts = int(datetime.now().timestamp())
    start_ts = int((datetime.now() - timedelta(days=days)).timestamp())

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {
        "period1": start_ts,
        "period2": end_ts,
        "interval": "1d",
    }
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        data = resp.json()

        result = data.get("chart", {}).get("result", [])
        if not result:
            return []

        quotes = result[0]
        timestamps = quotes.get("timestamp", [])
        ohlcv = quotes.get("indicators", {}).get("quote", [{}])[0]

        candles = []
        for i in range(len(timestamps)):
            o = ohlcv.get("open", [None])[i]
            h = ohlcv.get("high", [None])[i]
            l = ohlcv.get("low", [None])[i]
            c = ohlcv.get("close", [None])[i]
            v = ohlcv.get("volume", [0])[i] or 0

            if all(x is not None for x in [o, h, l, c]):
                candles.append({
                    "timestamp": timestamps[i],
                    "open": float(o),
                    "high": float(h),
                    "low": float(l),
                    "close": float(c),
                    "volume": int(v),
                })

        return candles

    except Exception as e:
        print(f"[Currency] Error fetching {pair_symbol}: {e}")
        return []


def generate_currency_signal(
    pair_symbol: str,
    candles: list[dict],
    capital: float = 10000.0,
    risk_pct: float = 1.5,
) -> CurrencySignal:
    """
    Generates a quantitative trading signal for a currency pair.
    """
    spec = CURRENCY_PAIRS.get(pair_symbol)
    if not spec or not candles or len(candles) < 55:
        return _empty_currency_signal(pair_symbol)

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]

    price = closes[-1]

    # ─── Indicators ───────────────────────────────────────────────
    ema10 = calculate_ema_pure(closes, 10)
    ema20 = calculate_ema_pure(closes, 20)
    ema50 = calculate_ema_pure(closes, 50)
    rsi14 = calculate_rsi_pure(closes, 14)
    atr = calculate_atr_pure(highs, lows, closes, 14)

    e10 = ema10[-1]
    e20 = ema20[-1]
    e50 = ema50[-1]
    r14 = rsi14[-1]

    if atr <= 0:
        atr = price * 0.003  # ~0.3% default

    # ─── Bollinger Bands (20, 2.0) ────────────────────────────────
    bb_period = 20
    recent_closes = closes[-bb_period:]
    bb_mid = sum(recent_closes) / bb_period
    variance = sum((c - bb_mid) ** 2 for c in recent_closes) / bb_period
    bb_std = variance ** 0.5
    bb_upper = bb_mid + 2.0 * bb_std
    bb_lower = bb_mid - 2.0 * bb_std
    bb_width = (bb_upper - bb_lower) / bb_mid if bb_mid > 0 else 0

    # Squeeze detection
    prev_closes = closes[-(bb_period + 10):-10]
    if len(prev_closes) >= bb_period:
        prev_mid = sum(prev_closes[-bb_period:]) / bb_period
        prev_var = sum((c - prev_mid) ** 2 for c in prev_closes[-bb_period:]) / bb_period
        prev_std = prev_var ** 0.5
        prev_width = (2 * prev_std * 2) / prev_mid if prev_mid > 0 else 0
        is_squeeze = bb_width < prev_width * 0.75
    else:
        is_squeeze = False

    # ─── Macro Trend ──────────────────────────────────────────────
    if e20 > e50:
        trend = "BULLISH"
    elif e20 < e50:
        trend = "BEARISH"
    else:
        trend = "SIDEWAYS"

    direction = "NONE"
    confidence = 0
    strategy = "NONE"
    rationale = f"Consolidating at EMA Ribbon ({e20:.4f} / {e50:.4f}), RSI {r14:.0f}"

    # ─── 1. Trend Momentum Breakout ───────────────────────────────
    if trend == "BULLISH" and price > e10 > e20 and 52 <= r14 <= 70:
        direction = "BUY"
        confidence = 82
        strategy = "TREND_MOMENTUM"
        rationale = f"Bullish momentum expansion (Price > EMA10 > EMA20), RSI {r14:.0f}"

    elif trend == "BEARISH" and price < e10 < e20 and 30 <= r14 <= 48:
        direction = "SELL"
        confidence = 82
        strategy = "TREND_MOMENTUM"
        rationale = f"Bearish momentum breakdown (Price < EMA10 < EMA20), RSI {r14:.0f}"

    # ─── 2. Trend Pullback / Dip Buying (High Win Rate) ───────────
    elif trend == "BULLISH" and (price <= e20 or price <= e10) and r14 >= 44:
        direction = "BUY"
        confidence = 78
        strategy = "PULLBACK_DIP"
        rationale = f"Dip to EMA Support in macro uptrend (Price near EMA20 {e20:.4f}), RSI {r14:.0f}"

    elif trend == "BEARISH" and (price >= e20 or price >= e10) and r14 <= 56:
        direction = "SELL"
        confidence = 78
        strategy = "PULLBACK_RALLY"
        rationale = f"Rally to EMA Resistance in macro downtrend (Price near EMA20 {e20:.4f}), RSI {r14:.0f}"

    # ─── 3. Bollinger Squeeze Breakout ────────────────────────────
    elif is_squeeze and price > bb_upper:
        direction = "BUY"
        confidence = 80
        strategy = "BOLLINGER_SQUEEZE"
        rationale = f"Bollinger squeeze breakout above {bb_upper:.4f}. Volatility expansion expected."

    elif is_squeeze and price < bb_lower:
        direction = "SELL"
        confidence = 80
        strategy = "BOLLINGER_SQUEEZE"
        rationale = f"Bollinger squeeze breakdown below {bb_lower:.4f}. Volatility expansion expected."

    # ─── 4. Mean Reversion at Extremes ────────────────────────────
    elif r14 <= 30 and price <= bb_lower * 1.002:
        direction = "BUY"
        confidence = 76
        strategy = "MEAN_REVERSION"
        rationale = f"Oversold bounce setup (RSI {r14:.0f}) at Bollinger lower band ({bb_lower:.4f})"

    elif r14 >= 70 and price >= bb_upper * 0.998:
        direction = "SELL"
        confidence = 76
        strategy = "MEAN_REVERSION"
        rationale = f"Overbought reversal setup (RSI {r14:.0f}) at Bollinger upper band ({bb_upper:.4f})"

    # ─── Risk Calculation ─────────────────────────────────────────
    sl_distance = atr * 0.75
    tp_distance = sl_distance * 1.5  # 1:1.5 R:R

    if direction == "BUY":
        stop_loss = price - sl_distance
        target = price + tp_distance
    elif direction == "SELL":
        stop_loss = price + sl_distance
        target = price - tp_distance
    else:
        stop_loss = price - sl_distance
        target = price + tp_distance

    risk_amount = capital * (risk_pct / 100.0)
    pnl_per_lot_per_pip = spec.lot_size * spec.pip_size
    sl_pips = sl_distance / spec.pip_size
    risk_per_lot = max(sl_pips * pnl_per_lot_per_pip, 1.0)

    lots = max(1, int(risk_amount / risk_per_lot))
    max_lots_by_margin = max(1, int(capital / spec.approx_margin))
    lots = min(lots, max_lots_by_margin)
    actual_risk = lots * risk_per_lot

    return CurrencySignal(
        symbol=pair_symbol, direction=direction, confidence=confidence,
        strategy=strategy, entry_price=price, stop_loss=stop_loss,
        target_price=target, lots=lots, risk_inr=actual_risk,
        rationale=rationale, trend=trend, rsi14=r14, ema20=e20, ema50=e50, atr=atr,
    )


def scan_all_currency_pairs(capital: float = 10000.0) -> list[dict]:
    """Scans all 4 major INR currency pairs for actionable trading signals."""
    signals = []
    for pair_symbol in CURRENCY_PAIRS:
        try:
            candles = fetch_currency_data(pair_symbol, days=90)
            if candles and len(candles) >= 55:
                sig = generate_currency_signal(pair_symbol, candles, capital=capital)
                if sig.direction != "NONE":
                    signals.append(sig.to_dict())
        except Exception as e:
            print(f"[Currency] Error scanning {pair_symbol}: {e}")
            continue

    signals.sort(key=lambda s: s["confidence"], reverse=True)
    return signals


def get_all_currency_telemetry(capital: float = 10000.0) -> list[dict]:
    """Returns complete technical breakdown for all 4 pairs (even when neutral)."""
    telemetry = []
    for pair_symbol in CURRENCY_PAIRS:
        try:
            candles = fetch_currency_data(pair_symbol, days=90)
            if candles and len(candles) >= 55:
                sig = generate_currency_signal(pair_symbol, candles, capital=capital)
                telemetry.append(sig.to_dict())
        except Exception as e:
            continue
    return telemetry


def _empty_currency_signal(pair_symbol: str) -> CurrencySignal:
    return CurrencySignal(
        symbol=pair_symbol, direction="NONE", confidence=0,
        strategy="NONE", entry_price=0.0, stop_loss=0.0,
        target_price=0.0, lots=0, risk_inr=0.0,
        rationale="No actionable setup", trend="UNKNOWN",
    )
