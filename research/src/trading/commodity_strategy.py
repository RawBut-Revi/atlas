"""
Project Atlas — Commodity Futures Trading Engine (MCX)
Trades Crude Oil, Natural Gas, Silver, Gold, and Copper on MCX.

Trading Sessions:
  1. Day Session (09:00 AM – 05:00 PM IST): Range Trading & Mean Reversion
  2. US Peak Session (05:00 PM – 11:30 PM IST): US Market Open Breakout (ORB) & Trend Momentum

Market Hours: 09:00 AM – 11:30 PM IST.
Auto Square-Off: 11:15 PM IST.
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
from .patterns import analyze_3hour_patterns


@dataclass
class CommoditySpec:
    symbol: str            # e.g. "CRUDEOILM"
    name: str              # "Crude Oil Mini"
    yfinance_ticker: str   # "CL=F"
    lot_size: int          # Contract lot size
    unit: str              # "Barrels", "mmBtu", "kg", "grams"
    tick_size: float       # Minimum price movement in INR
    tick_value: float      # Profit/loss per tick per lot in INR
    approx_margin: float   # Approx margin per lot in INR
    inr_price_factor: float # Multiplier to convert global USD benchmark to approximate MCX INR quote


COMMODITY_SPECS = {
    "CRUDEOILM": CommoditySpec(
        symbol="CRUDEOILM", name="Crude Oil Mini", yfinance_ticker="CL=F",
        lot_size=10, unit="Barrels", tick_size=1.0, tick_value=10.0,
        approx_margin=16000.0, inr_price_factor=85.0,  # e.g. $83 * 85 = ~₹7,055/bbl
    ),
    "NATGASMINI": CommoditySpec(
        symbol="NATGASMINI", name="Natural Gas Mini", yfinance_ticker="NG=F",
        lot_size=250, unit="mmBtu", tick_size=0.1, tick_value=25.0,
        approx_margin=14000.0, inr_price_factor=85.0,  # e.g. $2.88 * 85 = ~₹245/mmBtu
    ),
    "SILVERMIC": CommoditySpec(
        symbol="SILVERMIC", name="Silver Micro", yfinance_ticker="SI=F",
        lot_size=1, unit="kg", tick_size=1.0, tick_value=1.0,
        approx_margin=7500.0, inr_price_factor=1350.0,  # e.g. $67 * 1350 = ~₹90,450/kg
    ),
    "GOLDM": CommoditySpec(
        symbol="GOLDM", name="Gold Mini", yfinance_ticker="GC=F",
        lot_size=100, unit="Grams", tick_size=1.0, tick_value=10.0,
        approx_margin=22000.0, inr_price_factor=18.5,  # e.g. $4530 * 18.5 = ~₹83,800/10g
    ),
    "COPPERM": CommoditySpec(
        symbol="COPPERM", name="Copper Mini", yfinance_ticker="HG=F",
        lot_size=250, unit="kg", tick_size=0.05, tick_value=12.5,
        approx_margin=28000.0, inr_price_factor=130.0,  # e.g. $6.65 * 130 = ~₹864/kg
    ),
    "COPPER": CommoditySpec(
        symbol="COPPER", name="Copper Mini", yfinance_ticker="HG=F",
        lot_size=250, unit="kg", tick_size=0.05, tick_value=12.5,
        approx_margin=28000.0, inr_price_factor=130.0,
    ),
}

# MCX Market Hours
MCX_OPEN = time(9, 0)
MCX_US_SESSION_OPEN = time(17, 0)  # 5:00 PM IST (US pre-market & European close)
MCX_CLOSE = time(23, 30)
MCX_SQUARE_OFF = time(23, 15)


@dataclass
class CommoditySignal:
    symbol: str
    name: str
    direction: str         # "BUY", "SELL", or "NONE"
    confidence: int        # 0-100
    strategy: str          # "US_SESSION_BREAKOUT", "EMA_TREND_MOMENTUM", "RANGE_MEAN_REVERSION", "NEUTRAL"
    entry_price: float     # In INR
    stop_loss: float
    target_price: float
    lots: int
    risk_inr: float
    session: str           # "US_PEAK_SESSION" or "DAY_SESSION"
    rationale: str
    trend: str             # "BULLISH", "BEARISH", "SIDEWAYS"
    rsi14: float = 50.0
    atr: float = 0.0
    pattern_3h: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "direction": self.direction,
            "confidence": self.confidence,
            "strategy": self.strategy,
            "entry_price": round(self.entry_price, 2),
            "stop_loss": round(self.stop_loss, 2),
            "target_price": round(self.target_price, 2),
            "lots": self.lots,
            "risk_inr": round(self.risk_inr, 2),
            "session": self.session,
            "rationale": self.rationale,
            "trend": self.trend,
            "rsi14": round(self.rsi14, 1),
            "atr": round(self.atr, 2),
            "pattern_3h": self.pattern_3h,
            "signal_type": "COMMODITY",
        }


def fetch_commodity_data(symbol: str, days: int = 90) -> list[dict]:
    """
    Fetches historical daily commodity candles and converts to INR equivalent.
    """
    spec = COMMODITY_SPECS.get(symbol)
    if not spec:
        return []

    ticker = spec.yfinance_ticker
    end_ts = int(datetime.now().timestamp())
    start_ts = int((datetime.now() - timedelta(days=days)).timestamp())

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {"period1": start_ts, "period2": end_ts, "interval": "1d"}
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

        factor = spec.inr_price_factor
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
                    "open": float(o) * factor,
                    "high": float(h) * factor,
                    "low": float(l) * factor,
                    "close": float(c) * factor,
                    "volume": int(v),
                })
        return candles

    except Exception as e:
        print(f"[Commodity] Error fetching {symbol}: {e}")
        return []


def generate_commodity_signal(
    symbol: str,
    candles: list[dict],
    capital: float = 10000.0,
    risk_pct: float = 2.0,
) -> CommoditySignal:
    """
    Generates high-precision signals for MCX Commodities.
    """
    spec = COMMODITY_SPECS.get(symbol)
    if not spec or not candles or len(candles) < 50:
        return _empty_commodity_signal(symbol)

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    volumes = [c["volume"] for c in candles]

    price = closes[-1]

    # Indicators
    ema10 = calculate_ema_pure(closes, 10)
    ema20 = calculate_ema_pure(closes, 20)
    ema50 = calculate_ema_pure(closes, 50)
    rsi14 = calculate_rsi_pure(closes, 14)
    atr = calculate_atr_pure(highs, lows, closes, 14)
    vol_ratio = calculate_volume_ratio_pure(volumes, 20)

    e10 = ema10[-1]
    e20 = ema20[-1]
    e50 = ema50[-1]
    r14 = rsi14[-1]

    if atr <= 0:
        atr = price * 0.02

    # Bollinger Bands
    bb_period = 20
    recent_closes = closes[-bb_period:]
    bb_mid = sum(recent_closes) / bb_period
    bb_std = (sum((c - bb_mid) ** 2 for c in recent_closes) / bb_period) ** 0.5
    bb_upper = bb_mid + 2.0 * bb_std
    bb_lower = bb_mid - 2.0 * bb_std

    # Session detection
    now_hour = datetime.now().hour
    is_us_session = now_hour >= 17 or now_hour < 1  # 5:00 PM to 1:00 AM
    session_tag = "US_PEAK_SESSION" if is_us_session else "DAY_SESSION"

    # Macro Trend
    if e20 > e50 and price > e50:
        trend = "BULLISH"
    elif e20 < e50 and price < e50:
        trend = "BEARISH"
    else:
        trend = "SIDEWAYS"

    direction = "NONE"
    confidence = 0
    strategy = "NONE"
    rationale = f"Consolidating at ₹{price:.1f} (RSI {r14:.0f})"

    # ─── Strategy 1: US Evening Session Breakout (High Momentum) ─
    if is_us_session:
        if trend == "BULLISH" and price > e10 > e20 and r14 >= 54:
            direction = "BUY"
            confidence = 85 if vol_ratio >= 1.5 else 80
            strategy = "US_SESSION_MOMENTUM"
            rationale = f"US Session bullish trend breakout (Price > EMA10 > EMA20), RSI {r14:.0f}"

        elif trend == "BEARISH" and price < e10 < e20 and r14 <= 46:
            direction = "SELL"
            confidence = 85 if vol_ratio >= 1.5 else 80
            strategy = "US_SESSION_MOMENTUM"
            rationale = f"US Session bearish breakdown (Price < EMA10 < EMA20), RSI {r14:.0f}"

    # ─── Strategy 2: Day Session EMA Pullback / Dip ───────────────
    if direction == "NONE":
        if trend == "BULLISH" and price <= e20 and r14 >= 42:
            direction = "BUY"
            confidence = 78
            strategy = "PULLBACK_DIP"
            rationale = f"Support test at EMA20 (₹{e20:.1f}) in macro uptrend, RSI {r14:.0f}"

        elif trend == "BEARISH" and price >= e20 and r14 <= 58:
            direction = "SELL"
            confidence = 78
            strategy = "PULLBACK_RALLY"
            rationale = f"Resistance test at EMA20 (₹{e20:.1f}) in macro downtrend, RSI {r14:.0f}"

    # ─── Strategy 3: Mean Reversion on Bollinger Extremes ────────
    if direction == "NONE":
        if r14 <= 28 and price <= bb_lower * 1.005:
            direction = "BUY"
            confidence = 75
            strategy = "MEAN_REVERSION"
            rationale = f"Oversold bounce setup (RSI {r14:.0f}) at Bollinger lower band (₹{bb_lower:.1f})"

        elif r14 >= 72 and price >= bb_upper * 0.995:
            direction = "SELL"
            confidence = 75
            strategy = "MEAN_REVERSION"
            rationale = f"Overbought reversal setup (RSI {r14:.0f}) at Bollinger upper band (₹{bb_upper:.1f})"

    # ─── 4. 3-Hour Candlestick & Chart Pattern Analysis ──────────
    pat_res = analyze_3hour_patterns(symbol, candles)
    pattern_desc = pat_res.pattern_description if pat_res.candlestick_patterns or pat_res.chart_patterns else ""

    if direction == "BUY" and pat_res.bias == "BULLISH":
        confidence = min(95, confidence + pat_res.confidence_boost)
        if pattern_desc:
            rationale += f" | 3H: {pattern_desc}"
    elif direction == "SELL" and pat_res.bias == "BEARISH":
        confidence = min(95, confidence + pat_res.confidence_boost)
        if pattern_desc:
            rationale += f" | 3H: {pattern_desc}"
    elif direction == "NONE" and pat_res.bias != "NEUTRAL":
        if pat_res.bias == "BULLISH" and r14 >= 42:
            direction = "BUY"
            confidence = 80 + pat_res.confidence_boost
            strategy = "3H_PATTERN_BREAKOUT"
            rationale = f"3H Pattern setup: {pattern_desc}"
        elif pat_res.bias == "BEARISH" and r14 <= 58:
            direction = "SELL"
            confidence = 80 + pat_res.confidence_boost
            strategy = "3H_PATTERN_BREAKOUT"
            rationale = f"3H Pattern setup: {pattern_desc}"

    # ─── Sizing & Risk ────────────────────────────────────────────
    sl_distance = atr * 0.75
    tp_distance = sl_distance * 1.5

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
    risk_per_lot = (sl_distance / spec.tick_size) * spec.tick_value
    lots = max(1, int(risk_amount / max(risk_per_lot, 1.0)))
    actual_risk = lots * max(risk_per_lot, 1.0)

    return CommoditySignal(
        symbol=symbol, name=spec.name, direction=direction, confidence=confidence,
        strategy=strategy, entry_price=price, stop_loss=stop_loss,
        target_price=target, lots=lots, risk_inr=actual_risk,
        session=session_tag, rationale=rationale, trend=trend,
        rsi14=r14, atr=atr,
        pattern_3h=pattern_desc,
    )


def scan_all_commodities(capital: float = 10000.0) -> list[dict]:
    """Scans all 5 MCX commodities for actionable trading setups."""
    signals = []
    for symbol in COMMODITY_SPECS:
        try:
            candles = fetch_commodity_data(symbol, days=90)
            if candles and len(candles) >= 50:
                sig = generate_commodity_signal(symbol, candles, capital=capital)
                if sig.direction != "NONE":
                    signals.append(sig.to_dict())
        except Exception as e:
            print(f"[Commodity] Error scanning {symbol}: {e}")
            continue

    signals.sort(key=lambda s: s["confidence"], reverse=True)
    return signals


def get_all_commodity_telemetry(capital: float = 10000.0) -> list[dict]:
    """Returns complete telemetry for all 5 MCX commodities."""
    telemetry = []
    for symbol in COMMODITY_SPECS:
        try:
            candles = fetch_commodity_data(symbol, days=90)
            if candles and len(candles) >= 50:
                sig = generate_commodity_signal(symbol, candles, capital=capital)
                telemetry.append(sig.to_dict())
        except Exception as e:
            continue
    return telemetry


def _empty_commodity_signal(symbol: str) -> CommoditySignal:
    spec = COMMODITY_SPECS.get(symbol)
    name = spec.name if spec else symbol
    return CommoditySignal(
        symbol=symbol, name=name, direction="NONE", confidence=0,
        strategy="NONE", entry_price=0.0, stop_loss=0.0,
        target_price=0.0, lots=0, risk_inr=0.0,
        session="OFF_HOURS", rationale="No actionable setup", trend="UNKNOWN",
    )


if __name__ == "__main__":
    print("=== MCX Commodity Futures Telemetry ===")
    for t in get_all_commodity_telemetry():
        print(f"{t['symbol']} ({t['name']}): Price=INR {t['entry_price']} | Dir={t['direction']} | Strategy={t['strategy']} | Trend={t['trend']} | Conf={t['confidence']}%")
