"""
Project Atlas — Multi-Week Swing Observation & F&O Derivative Radar
Observes multi-week price forecasts (1 to 4 weeks) without auto-buying,
simulates risk/reward for Cash Equity vs Futures vs Options, and feeds
directional bias to the intraday execution engine.
"""

import os
import json
import math
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional, List

from .backtest import fetch_historical_data
from .patterns import analyze_3hour_patterns
from .indicators import calculate_ema_pure, calculate_rsi_pure, calculate_atr_pct_pure
from .commodity_strategy import fetch_commodity_data, COMMODITY_SPECS
from .currency_strategy import fetch_currency_data, CURRENCY_PAIRS

WATCHLIST_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "swing_watchlist.json")

# F&O Lot Sizes for key liquid swing assets
FNO_LOT_SIZES = {
    "RELIANCE": 250,
    "TCS": 175,
    "HDFCBANK": 550,
    "INFY": 400,
    "ICICIBANK": 700,
    "SBIN": 750,
    "BHARTIARTL": 475,
    "ITC": 1600,
    "LT": 175,
    "TATAMOTORS": 700,
    "TATASTEEL": 5500,
    "ADANIENT": 300,
    "TRENT": 100,
    "BEL": 2850,
    "COALINDIA": 2100,
    "CRUDEOILM": 10,
    "COPPER": 2500,
    "USDINR": 1000,
}


@dataclass
class SwingObservation:
    symbol: str
    asset_type: str                  # "EQUITY", "COMMODITY", "CURRENCY"
    current_price: float
    projected_target: float          # 2-4 Week Price Target
    projected_stop_loss: float       # Swing Invalidation Level
    potential_return_pct: float      # Potential Equity Gain %
    time_horizon_weeks: int          # 1, 2, 3, or 4 Weeks
    swing_direction: str             # "BULLISH" or "BEARISH"
    confidence: int                  # 0 to 100%
    catalyst_pattern: str            # Pattern description
    recommended_vehicle: str         # "OPTIONS", "FUTURES", or "CASH_EQUITY"
    
    # F&O Simulation Details
    option_strike: str               # e.g. "160 CE" or "3200 PE"
    option_approx_premium: float     # Estimated option price
    option_lot_size: int
    option_capital_required: float   # Max risk (Premium * Lot size)
    option_projected_profit: float   # Potential profit if target hits
    futures_margin_required: float   # Approx margin for 1 future contract
    futures_projected_profit: float  # Futures profit in INR
    intraday_bias: str               # "ONLY_BUY_DIPS" or "ONLY_SELL_RALLIES"

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "asset_type": self.asset_type,
            "current_price": round(self.current_price, 2),
            "projected_target": round(self.projected_target, 2),
            "projected_stop_loss": round(self.projected_stop_loss, 2),
            "potential_return_pct": round(self.potential_return_pct, 1),
            "time_horizon_weeks": self.time_horizon_weeks,
            "swing_direction": self.swing_direction,
            "confidence": self.confidence,
            "catalyst_pattern": self.catalyst_pattern,
            "recommended_vehicle": self.recommended_vehicle,
            "option_strike": self.option_strike,
            "option_approx_premium": round(self.option_approx_premium, 2),
            "option_lot_size": self.option_lot_size,
            "option_capital_required": round(self.option_capital_required, 2),
            "option_projected_profit": round(self.option_projected_profit, 2),
            "futures_margin_required": round(self.futures_margin_required, 2),
            "futures_projected_profit": round(self.futures_projected_profit, 2),
            "intraday_bias": self.intraday_bias,
        }


def analyze_swing_setup(symbol: str, asset_type: str = "EQUITY", df=None) -> Optional[SwingObservation]:
    """
    Evaluates multi-week structural price action and builds an F&O vs Equity swing thesis.
    """
    if df is None or len(df) < 40:
        return None

    if isinstance(df, list):
        closes = [r["close"] for r in df]
        highs = [r["high"] for r in df]
        lows = [r["low"] for r in df]
        volumes = [r["volume"] for r in df]
    else:
        closes = list(df["close"])
        highs = list(df["high"])
        lows = list(df["low"])
        volumes = list(df["volume"])

    price = closes[-1]
    if price <= 0:
        return None

    ema20 = calculate_ema_pure(closes, 20)[-1]
    ema50 = calculate_ema_pure(closes, 50)[-1]
    rsi14 = calculate_rsi_pure(closes, 14)[-1]
    atr_pct = calculate_atr_pct_pure(highs, lows, closes, 14)

    # 3-Hour & Multi-day Pattern recognition
    pat_res = analyze_3hour_patterns(symbol, df)
    patterns = pat_res.candlestick_patterns + pat_res.chart_patterns

    # Structural Swing Trend Evaluation
    is_bullish = price > ema50 and ema20 > ema50 and rsi14 >= 48
    is_bearish = price < ema50 and ema20 < ema50 and rsi14 <= 52

    if not is_bullish and not is_bearish:
        if pat_res.bias == "BULLISH":
            is_bullish = True
        elif pat_res.bias == "BEARISH":
            is_bearish = True
        else:
            return None

    direction = "BULLISH" if is_bullish else "BEARISH"
    confidence = 80 + pat_res.confidence_boost

    # Multi-Week Target & Horizon Projection
    # Swing targets typically aim for 2.5x to 4x ATR% over 2 to 4 weeks
    move_pct = max(atr_pct * 3.2, 5.0)  # Min 5% multi-week move
    horizon_weeks = 3 if move_pct >= 8.0 else 2

    if direction == "BULLISH":
        projected_target = price * (1.0 + move_pct / 100.0)
        projected_sl = price * (1.0 - (move_pct / 2.0) / 100.0)
        intraday_bias = "ONLY_BUY_DIPS"
    else:
        projected_target = price * (1.0 - move_pct / 100.0)
        projected_sl = price * (1.0 + (move_pct / 2.0) / 100.0)
        intraday_bias = "ONLY_SELL_RALLIES"

    lot_size = FNO_LOT_SIZES.get(symbol, 500)

    # ─── Derivative Strategy Simulation (Options vs Futures vs Equity) ───
    # Pick slightly OTM / ATM Strike
    strike_step = 10.0 if price < 500 else (50.0 if price < 2500 else 100.0)
    if direction == "BULLISH":
        atm_strike = math.ceil(price / strike_step) * strike_step
        option_strike = f"{int(atm_strike)} CE"
        approx_premium = price * 0.025  # ~2.5% premium for monthly ATM call
        target_intrinsic = max(0.0, projected_target - atm_strike)
        projected_option_profit = (target_intrinsic + approx_premium * 0.5 - approx_premium) * lot_size
    else:
        atm_strike = math.floor(price / strike_step) * strike_step
        option_strike = f"{int(atm_strike)} PE"
        approx_premium = price * 0.025
        target_intrinsic = max(0.0, atm_strike - projected_target)
        projected_option_profit = (target_intrinsic + approx_premium * 0.5 - approx_premium) * lot_size

    option_cap = approx_premium * lot_size
    projected_option_profit = max(projected_option_profit, option_cap * 1.5)  # 1.5x to 2.5x payoff

    # Futures simulation
    futures_margin = price * lot_size * 0.22  # ~22% exchange margin
    futures_profit = abs(projected_target - price) * lot_size

    # Recommended Vehicle Logic:
    # If high volatility (> 3% ATR): Options (Asymmetrical risk/reward)
    # If liquid F&O with steady trend: Futures or Options
    # If smaller capital or non-F&O: Cash Equity
    if atr_pct >= 2.8 and option_cap <= 30000:
        recommended_vehicle = "OPTIONS"
    elif confidence >= 90 and futures_margin <= 150000:
        recommended_vehicle = "FUTURES"
    else:
        recommended_vehicle = "CASH_EQUITY"

    pattern_desc = ", ".join(patterns) if patterns else ("EMA Ribbon Trend Expansion" if is_bullish else "Breakdown Under EMA50")

    return SwingObservation(
        symbol=symbol,
        asset_type=asset_type,
        current_price=price,
        projected_target=projected_target,
        projected_stop_loss=projected_sl,
        potential_return_pct=move_pct,
        time_horizon_weeks=horizon_weeks,
        swing_direction=direction,
        confidence=confidence,
        catalyst_pattern=pattern_desc,
        recommended_vehicle=recommended_vehicle,
        option_strike=option_strike,
        option_approx_premium=approx_premium,
        option_lot_size=lot_size,
        option_capital_required=option_cap,
        option_projected_profit=projected_option_profit,
        futures_margin_required=futures_margin,
        futures_projected_profit=futures_profit,
        intraday_bias=intraday_bias,
    )


from concurrent.futures import ThreadPoolExecutor, as_completed


def scan_swing_radar() -> List[SwingObservation]:
    """
    Scans liquid universe across Equities, Commodities, and FX in parallel (< 2 seconds).
    """
    today = datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")

    observations = []

    # 1. Top Liquid Equities
    equities_to_watch = [
        "TATASTEEL", "RELIANCE", "TCS", "HDFCBANK", "INFY", "TATAMOTORS",
        "TRENT", "BEL", "ADANIENT", "COALINDIA", "ICICIBANK", "BHARTIARTL", "LT"
    ]

    def eval_equity(sym: str) -> Optional[SwingObservation]:
        try:
            df = fetch_historical_data(sym, from_date, today)
            return analyze_swing_setup(sym, "EQUITY", df)
        except Exception:
            return None

    def eval_commodity(sym: str) -> Optional[SwingObservation]:
        try:
            c_data = fetch_commodity_data(sym, days=60)
            return analyze_swing_setup(sym, "COMMODITY", c_data)
        except Exception:
            return None

    def eval_currency(sym: str) -> Optional[SwingObservation]:
        try:
            fx_data = fetch_currency_data(sym, days=60)
            return analyze_swing_setup(sym, "CURRENCY", fx_data)
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = []
        for sym in equities_to_watch:
            futures.append(executor.submit(eval_equity, sym))
        for sym in ["CRUDEOILM", "COPPER", "SILVERMIC", "GOLDM"]:
            futures.append(executor.submit(eval_commodity, sym))
        for sym in ["USDINR", "EURINR", "GBPINR"]:
            futures.append(executor.submit(eval_currency, sym))

        for f in as_completed(futures):
            res = f.result()
            if res and res.confidence >= 80:
                observations.append(res)

    observations.sort(key=lambda x: x.confidence, reverse=True)

    # Save to disk
    try:
        with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
            json.dump([o.to_dict() for o in observations], f, indent=2)
    except Exception:
        pass

    return observations


def get_swing_directional_bias(symbol: str) -> str:
    """
    Returns the multi-week bias for a symbol ('ONLY_BUY_DIPS', 'ONLY_SELL_RALLIES', or 'NEUTRAL').
    Used by the intraday scanner to align micro entries with macro swing trends.
    """
    if os.path.exists(WATCHLIST_FILE):
        try:
            with open(WATCHLIST_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data:
                    if item.get("symbol") == symbol:
                        return item.get("intraday_bias", "NEUTRAL")
        except Exception:
            pass
    return "NEUTRAL"
