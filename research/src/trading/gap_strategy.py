"""
Project Atlas — Early Market Opening Gap Detection Strategy
Scans 181 equities at 9:15 AM IST for gap-up/gap-down opportunities.

Two High-Probability Setups:
  1. Gap Fade (Mean Reversion) — Trade against the gap when RSI is extreme.
  2. Gap & Go (Momentum)       — Trade with the gap on high-volume breakouts.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

from .indicators import calculate_rsi_pure, calculate_ema_pure, calculate_atr_pure, calculate_volume_ratio_pure
from .universe import get_universe_symbols
from .backtest import fetch_historical_data


@dataclass
class GapSignal:
    symbol: str
    gap_type: str          # "GAP_UP" or "GAP_DOWN"
    gap_pct: float         # Gap percentage
    strategy: str          # "GAP_FADE" or "GAP_AND_GO"
    direction: str         # "BUY" or "SELL"
    confidence: int        # 0-100
    open_price: float      # Today's opening price
    prev_close: float      # Previous day's close
    entry_price: float
    stop_loss: float
    target_price: float
    rationale: str

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "gap_type": self.gap_type,
            "gap_pct": round(self.gap_pct, 2),
            "strategy": self.strategy,
            "direction": self.direction,
            "confidence": self.confidence,
            "open_price": round(self.open_price, 2),
            "prev_close": round(self.prev_close, 2),
            "entry_price": round(self.entry_price, 2),
            "stop_loss": round(self.stop_loss, 2),
            "target_price": round(self.target_price, 2),
            "rationale": self.rationale,
            "signal_type": "GAP",
        }


def detect_gap(symbol: str, candles) -> Optional[GapSignal]:
    """
    Detects gap opening and returns a GapSignal if actionable.
    Expects candles as a list of dicts or pandas DataFrame with at least 50 rows.
    """
    if candles is None or len(candles) < 50:
        return None

    # Extract data
    if HAS_PANDAS and isinstance(candles, pd.DataFrame):
        closes = list(candles["close"])
        highs = list(candles["high"])
        lows = list(candles["low"])
        opens = list(candles["open"])
        volumes = list(candles["volume"])
    elif isinstance(candles, list):
        closes = [r["close"] for r in candles]
        highs = [r["high"] for r in candles]
        lows = [r["low"] for r in candles]
        opens = [r["open"] for r in candles]
        volumes = [r["volume"] for r in candles]
    else:
        return None

    prev_close = closes[-2]
    today_open = opens[-1]
    today_close = closes[-1]
    today_high = highs[-1]
    today_low = lows[-1]

    if prev_close <= 0:
        return None

    # Calculate gap percentage
    gap_pct = ((today_open - prev_close) / prev_close) * 100.0

    # Filter: Only significant gaps (>= 0.8%)
    if abs(gap_pct) < 0.8:
        return None

    gap_type = "GAP_UP" if gap_pct > 0 else "GAP_DOWN"

    # Calculate indicators for context
    rsi14 = calculate_rsi_pure(closes, period=14)[-1]
    rsi2 = calculate_rsi_pure(closes, period=2)[-1]
    ema50 = calculate_ema_pure(closes, 50)[-1]
    atr = calculate_atr_pure(highs, lows, closes, 14)
    vol_ratio = calculate_volume_ratio_pure(volumes, 20)

    if atr <= 0:
        atr = today_close * 0.015

    # ─── Strategy 1: Gap Fade (Mean Reversion) ────────────────────
    # Gap Up + RSI Overbought → SHORT (Sell the gap, target prev close)
    # Gap Down + RSI Oversold → LONG  (Buy the dip, target prev close)
    is_gap_fade = False
    fade_direction = "NONE"
    fade_confidence = 0
    fade_rationale = ""

    if gap_type == "GAP_UP" and (rsi14 >= 65 or rsi2 >= 80):
        # Overbought gap up — mean reversion likely
        is_gap_fade = True
        fade_direction = "SELL"
        fade_confidence = 85 if rsi14 >= 75 else 78
        fade_rationale = f"Gap Up {gap_pct:+.1f}% into overbought territory (RSI14: {rsi14:.0f}). Fade towards prev close."

    elif gap_type == "GAP_DOWN" and (rsi14 <= 35 or rsi2 <= 20):
        # Oversold gap down — mean reversion likely
        is_gap_fade = True
        fade_direction = "BUY"
        fade_confidence = 85 if rsi14 <= 25 else 78
        fade_rationale = f"Gap Down {gap_pct:+.1f}% into oversold territory (RSI14: {rsi14:.0f}). Fade towards prev close."

    # ─── Strategy 2: Gap & Go (Momentum Continuation) ─────────────
    # Gap Up + Strong Volume + Bullish trend → BUY continuation
    # Gap Down + Strong Volume + Bearish trend → SELL continuation
    is_gap_go = False
    go_direction = "NONE"
    go_confidence = 0
    go_rationale = ""

    if gap_type == "GAP_UP" and vol_ratio >= 1.8 and today_close > ema50:
        # High-volume gap up in bullish trend — momentum continuation
        is_gap_go = True
        go_direction = "BUY"
        go_confidence = 82 if vol_ratio >= 2.5 else 76
        go_rationale = f"Gap Up {gap_pct:+.1f}% with {vol_ratio:.1f}x volume surge. Institutional momentum breakout."

    elif gap_type == "GAP_DOWN" and vol_ratio >= 1.8 and today_close < ema50:
        # High-volume gap down in bearish trend — momentum continuation
        is_gap_go = True
        go_direction = "SELL"
        go_confidence = 82 if vol_ratio >= 2.5 else 76
        go_rationale = f"Gap Down {gap_pct:+.1f}% with {vol_ratio:.1f}x volume surge. Institutional breakdown."

    # Prioritize: Gap Fade has statistically higher win rate on large-cap blue chips
    if is_gap_fade:
        if fade_direction == "SELL":
            entry = today_open
            stop_loss = today_open + atr * 0.6   # SL above gap high
            target = prev_close                   # 100% gap fill
        else:  # BUY fade
            entry = today_open
            stop_loss = today_open - atr * 0.6   # SL below gap low
            target = prev_close                   # 100% gap fill

        return GapSignal(
            symbol=symbol, gap_type=gap_type, gap_pct=gap_pct,
            strategy="GAP_FADE", direction=fade_direction,
            confidence=fade_confidence, open_price=today_open,
            prev_close=prev_close, entry_price=entry,
            stop_loss=stop_loss, target_price=target,
            rationale=fade_rationale,
        )

    elif is_gap_go:
        if go_direction == "BUY":
            entry = today_open
            stop_loss = today_open - atr * 0.75
            target = today_open + atr * 1.5
        else:  # SELL
            entry = today_open
            stop_loss = today_open + atr * 0.75
            target = today_open - atr * 1.5

        return GapSignal(
            symbol=symbol, gap_type=gap_type, gap_pct=gap_pct,
            strategy="GAP_AND_GO", direction=go_direction,
            confidence=go_confidence, open_price=today_open,
            prev_close=prev_close, entry_price=entry,
            stop_loss=stop_loss, target_price=target,
            rationale=go_rationale,
        )

    return None


def scan_for_gaps(symbols: list[str] = None) -> list[dict]:
    """
    Scans the entire stock universe for gap openings.
    Call this at 9:15 AM IST for the early morning scan.
    """
    if symbols is None:
        symbols = get_universe_symbols()

    today = datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

    gap_signals = []
    for symbol in symbols:
        try:
            candles = fetch_historical_data(symbol, from_date, today)
            if candles is not None and len(candles) >= 50:
                sig = detect_gap(symbol, candles)
                if sig is not None:
                    gap_signals.append(sig.to_dict())
        except Exception:
            continue

    # Sort by absolute gap percentage (biggest gaps first)
    gap_signals.sort(key=lambda s: abs(s["gap_pct"]), reverse=True)
    return gap_signals


if __name__ == "__main__":
    print("=== Gap Opening Scanner (Backtest Mode) ===")
    results = scan_for_gaps()
    print(f"Found {len(results)} actionable gap setups:")
    for r in results[:10]:
        print(f"  {r['gap_type']:10s} {r['symbol']:12s} {r['gap_pct']:+5.1f}% | {r['strategy']:12s} | {r['direction']:4s} | Conf: {r['confidence']}%")
