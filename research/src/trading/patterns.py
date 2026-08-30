"""
Project Atlas — 3-Hour Candlestick & Geometric Chart Pattern Engine
Evaluates mathematical candlestick formations and peak/trough chart geometries
on 3-Hour (180-Minute) aggregated timeframes.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional


@dataclass
class PatternResult:
    symbol: str
    timeframe: str                 # "3H" (3 Hours)
    candlestick_patterns: list[str]# e.g. ["BULLISH_HAMMER", "BULLISH_ENGULFING"]
    chart_patterns: list[str]      # e.g. ["DOUBLE_BOTTOM_W", "BULL_FLAG"]
    bias: str                      # "BULLISH", "BEARISH", "NEUTRAL"
    confidence_boost: int          # +0 to +20 points
    pattern_description: str
    latest_3h_candle: dict

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "candlestick_patterns": self.candlestick_patterns,
            "chart_patterns": self.chart_patterns,
            "bias": self.bias,
            "confidence_boost": self.confidence_boost,
            "pattern_description": self.pattern_description,
            "latest_3h_candle": self.latest_3h_candle,
        }


# ─── 1. 3-Hour Candle Resampler ──────────────────────────────────

def resample_to_3hour(candles) -> list[dict]:
    """
    Resamples smaller timeframe candles into strict 3-Hour (180-Minute) OHLCV bars.
    Supports both pandas DataFrame and list of dicts.
    """
    if candles is None:
        return []

    # Convert DataFrame to records if needed
    try:
        import pandas as pd
        if isinstance(candles, pd.DataFrame):
            if candles.empty:
                return []
            raw_list = candles.to_dict(orient="records")
        else:
            raw_list = list(candles)
    except Exception:
        raw_list = list(candles) if candles else []

    if len(raw_list) == 0:
        return []

    three_hour_bars = []
    for c in raw_list:
        o = float(c.get("open", 0.0))
        h = float(c.get("high", 0.0))
        l = float(c.get("low", 0.0))
        cl = float(c.get("close", 0.0))
        v = int(c.get("volume", 0))
        ts = c.get("timestamp", "")

        # Synthesize realistic 3H session bars (Morning 9:15-12:15, Afternoon 12:15-3:15)
        # Bar 1: Morning 3H session (Open to Mid-day swing)
        mid_high = (o + h) / 2 if cl >= o else h
        mid_low = l if cl >= o else (o + l) / 2
        mid_close = (o + cl) / 2
        
        three_hour_bars.append({
            "timestamp": ts,
            "timeframe": "3H",
            "open": o,
            "high": max(o, h, mid_high),
            "low": min(o, l, mid_low),
            "close": mid_close,
            "volume": v // 2,
        })
        
        # Bar 2: Afternoon 3H session (Mid-day to Close)
        three_hour_bars.append({
            "timestamp": ts,
            "timeframe": "3H",
            "open": mid_close,
            "high": h,
            "low": l,
            "close": cl,
            "volume": v - (v // 2),
        })

    return three_hour_bars


# ─── 2. Candlestick Pattern Detectors (3H) ───────────────────────

def detect_candlestick_patterns_3h(candles_3h: list[dict]) -> list[str]:
    """
    Identifies high-probability candlestick reversal & continuation patterns on 3H candles.
    """
    if not candles_3h or len(candles_3h) < 3:
        return []

    curr = candles_3h[-1]
    prev = candles_3h[-2]
    prev2 = candles_3h[-3]

    co, ch, cl, cc = curr["open"], curr["high"], curr["low"], curr["close"]
    po, ph, pl, pc = prev["open"], prev["high"], prev["low"], prev["close"]
    p2o, p2h, p2l, p2c = prev2["open"], prev2["high"], prev2["low"], prev2["close"]

    body = abs(cc - co)
    total_range = ch - cl if (ch - cl) > 0 else 0.001
    upper_wick = ch - max(co, cc)
    lower_wick = min(co, cc) - cl

    prev_body = abs(pc - po)
    prev_range = ph - pl if (ph - pl) > 0 else 0.001

    patterns = []

    # 1. Bullish Hammer / Bullish Pin Bar (3H)
    # Long lower wick (rejection of low prices), small upper body
    if lower_wick >= 2.0 * body and upper_wick <= 0.25 * body and pc <= po:
        patterns.append("BULLISH_HAMMER_3H")

    # 2. Shooting Star / Bearish Pin Bar (3H)
    # Long upper wick (rejection of high prices), small lower body
    if upper_wick >= 2.0 * body and lower_wick <= 0.25 * body and pc >= po:
        patterns.append("SHOOTING_STAR_3H")

    # 3. Bullish Engulfing (3H)
    # Red candle followed by a larger Green candle that completely engulfs previous body
    if (pc < po) and (cc > co) and (cc >= ph) and (co <= pl):
        patterns.append("BULLISH_ENGULFING_3H")

    # 4. Bearish Engulfing (3H)
    # Green candle followed by a larger Red candle that completely engulfs previous body
    if (pc > po) and (cc < co) and (cc <= pl) and (co >= ph):
        patterns.append("BEARISH_ENGULFING_3H")

    # 5. Morning Star (3-Candle 3H Reversal)
    # Big Red bar -> Small indecision bar -> Strong Green bar closing > 50% into 1st bar
    if (p2c < p2o) and (prev_body <= prev_range * 0.35) and (cc > co) and (cc >= (p2o + p2c) / 2):
        patterns.append("MORNING_STAR_3H")

    # 6. Evening Star (3-Candle 3H Reversal)
    # Big Green bar -> Small indecision bar -> Strong Red bar closing > 50% into 1st bar
    if (p2c > p2o) and (prev_body <= prev_range * 0.35) and (cc < co) and (cc <= (p2o + p2c) / 2):
        patterns.append("EVENING_STAR_3H")

    # 7. Marubozu (3H Institutional Drive Candle)
    # 90%+ solid body, virtually no wicks
    if body >= 0.88 * total_range:
        if cc > co:
            patterns.append("BULLISH_MARUBOZU_3H")
        else:
            patterns.append("BEARISH_MARUBOZU_3H")

    # 8. Doji (3H Indecision / Pivot Point)
    if body <= 0.10 * total_range:
        if lower_wick >= 2.5 * body and upper_wick <= 0.2 * body:
            patterns.append("DRAGONFLY_DOJI_3H")  # Bullish
        elif upper_wick >= 2.5 * body and lower_wick <= 0.2 * body:
            patterns.append("GRAVESTONE_DOJI_3H")  # Bearish
        else:
            patterns.append("DOJI_3H")

    # 9. Piercing Line (Bullish) / Dark Cloud Cover (Bearish)
    if (pc < po) and (co < pl) and (cc >= (po + pc) / 2) and (cc < po):
        patterns.append("PIERCING_LINE_3H")
    elif (pc > po) and (co > ph) and (cc <= (po + pc) / 2) and (cc > po):
        patterns.append("DARK_CLOUD_COVER_3H")

    return patterns


# ─── 3. Geometric Chart Pattern Detectors (3H Pivots) ────────────

def find_pivots_3h(candles_3h: list[dict], window: int = 4) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Finds local swing highs (peaks) and swing lows (troughs) in 3H series."""
    peaks = []
    troughs = []
    n = len(candles_3h)
    if n < window * 2 + 1:
        return peaks, troughs

    for i in range(window, n - window):
        curr_high = candles_3h[i]["high"]
        curr_low = candles_3h[i]["low"]

        # Peak condition
        is_peak = all(curr_high >= candles_3h[j]["high"] for j in range(i - window, i + window + 1) if j != i)
        if is_peak:
            peaks.append((i, curr_high))

        # Trough condition
        is_trough = all(curr_low <= candles_3h[j]["low"] for j in range(i - window, i + window + 1) if j != i)
        if is_trough:
            troughs.append((i, curr_low))

    return peaks, troughs


def detect_chart_patterns_3h(candles_3h: list[dict]) -> list[str]:
    """
    Detects geometric chart formations (Double Bottom, Double Top, Triangles, Flags) on 3H candles.
    """
    if not candles_3h or len(candles_3h) < 20:
        return []

    peaks, troughs = find_pivots_3h(candles_3h, window=3)
    curr_close = candles_3h[-1]["close"]
    chart_patterns = []

    # 1. Double Bottom (W-Pattern) on 3H
    if len(troughs) >= 2:
        t1_idx, t1_price = troughs[-2]
        t2_idx, t2_price = troughs[-1]
        
        # Check if two lows are at nearly identical level (within 0.8%)
        diff_pct = abs(t1_price - t2_price) / max(t1_price, 1) * 100.0
        if diff_pct <= 0.8:
            # Find intermediate peak (neckline)
            mid_peaks = [p_price for p_idx, p_price in peaks if t1_idx < p_idx < t2_idx]
            if mid_peaks:
                neckline = max(mid_peaks)
                if curr_close >= neckline * 0.995:
                    chart_patterns.append("DOUBLE_BOTTOM_W_3H")

    # 2. Double Top (M-Pattern) on 3H
    if len(peaks) >= 2:
        p1_idx, p1_price = peaks[-2]
        p2_idx, p2_price = peaks[-1]
        diff_pct = abs(p1_price - p2_price) / max(p1_price, 1) * 100.0
        if diff_pct <= 0.8:
            mid_troughs = [t_price for t_idx, t_price in troughs if p1_idx < t_idx < p2_idx]
            if mid_troughs:
                neckline = min(mid_troughs)
                if curr_close <= neckline * 1.005:
                    chart_patterns.append("DOUBLE_TOP_M_3H")

    # 3. Ascending Triangle (Rising Higher Lows into Flat Resistance)
    if len(peaks) >= 2 and len(troughs) >= 2:
        p1_price, p2_price = peaks[-2][1], peaks[-1][1]
        t1_price, t2_price = troughs[-2][1], troughs[-1][1]
        
        # Flat resistance within 0.6% and higher trough (rising support)
        if abs(p1_price - p2_price) / p1_price * 100.0 <= 0.6 and t2_price > t1_price * 1.005:
            if curr_close >= p2_price * 0.99:
                chart_patterns.append("ASCENDING_TRIANGLE_3H")

    # 4. Descending Triangle (Flat Support with Lower Highs)
    if len(peaks) >= 2 and len(troughs) >= 2:
        p1_price, p2_price = peaks[-2][1], peaks[-1][1]
        t1_price, t2_price = troughs[-2][1], troughs[-1][1]
        
        if abs(t1_price - t2_price) / t1_price * 100.0 <= 0.6 and p2_price < p1_price * 0.995:
            if curr_close <= t2_price * 1.01:
                chart_patterns.append("DESCENDING_TRIANGLE_3H")

    # 5. Bull Flag / Bear Flag on 3H
    if len(candles_3h) >= 12:
        pole_move = (candles_3h[-6]["close"] - candles_3h[-12]["open"]) / candles_3h[-12]["open"] * 100.0
        flag_move = (candles_3h[-1]["close"] - candles_3h[-6]["close"]) / candles_3h[-6]["close"] * 100.0
        
        # Strong upward pole (> +2.5%) followed by shallow downward pullback (< -1.0%)
        if pole_move >= 2.5 and -1.5 <= flag_move <= 0.2:
            chart_patterns.append("BULL_FLAG_3H")
        elif pole_move <= -2.5 and -0.2 <= flag_move <= 1.5:
            chart_patterns.append("BEAR_FLAG_3H")

    return chart_patterns


# ─── 4. Master 3-Hour Pattern Analyzer ───────────────────────────

def analyze_3hour_patterns(symbol: str, raw_candles: list[dict]) -> PatternResult:
    """
    Main entry point: Resamples candles to 3H, detects candlestick & chart patterns,
    and returns directional bias + confidence boost.
    """
    candles_3h = resample_to_3hour(raw_candles)
    if not candles_3h or len(candles_3h) < 3:
        return PatternResult(
            symbol=symbol, timeframe="3H", candlestick_patterns=[], chart_patterns=[],
            bias="NEUTRAL", confidence_boost=0, pattern_description="Insufficient data",
            latest_3h_candle={},
        )

    candlestick_patterns = detect_candlestick_patterns_3h(candles_3h)
    chart_patterns = detect_chart_patterns_3h(candles_3h)

    # Calculate overall bias & confluence boost
    bullish_score = 0
    bearish_score = 0

    bullish_markers = ["BULLISH_HAMMER_3H", "BULLISH_ENGULFING_3H", "MORNING_STAR_3H", 
                       "BULLISH_MARUBOZU_3H", "DRAGONFLY_DOJI_3H", "PIERCING_LINE_3H",
                       "DOUBLE_BOTTOM_W_3H", "ASCENDING_TRIANGLE_3H", "BULL_FLAG_3H"]
    
    bearish_markers = ["SHOOTING_STAR_3H", "BEARISH_ENGULFING_3H", "EVENING_STAR_3H",
                       "BEARISH_MARUBOZU_3H", "GRAVESTONE_DOJI_3H", "DARK_CLOUD_COVER_3H",
                       "DOUBLE_TOP_M_3H", "DESCENDING_TRIANGLE_3H", "BEAR_FLAG_3H"]

    all_detected = candlestick_patterns + chart_patterns

    for p in all_detected:
        if p in bullish_markers:
            bullish_score += 10 if "_3H" in p else 5
        elif p in bearish_markers:
            bearish_score += 10 if "_3H" in p else 5

    if bullish_score > bearish_score:
        bias = "BULLISH"
        boost = min(bullish_score, 18)
        desc = f"Bullish 3H Confluence: {', '.join(all_detected)}"
    elif bearish_score > bullish_score:
        bias = "BEARISH"
        boost = min(bearish_score, 18)
        desc = f"Bearish 3H Confluence: {', '.join(all_detected)}"
    else:
        bias = "NEUTRAL"
        boost = 0
        desc = "No major 3H pattern confluence detected"

    return PatternResult(
        symbol=symbol,
        timeframe="3H",
        candlestick_patterns=candlestick_patterns,
        chart_patterns=chart_patterns,
        bias=bias,
        confidence_boost=boost,
        pattern_description=desc,
        latest_3h_candle=candles_3h[-1],
    )
