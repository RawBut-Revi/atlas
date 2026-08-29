"""
Project Atlas — Backtesting Engine

Runs the multi-confluence strategy on historical data to validate
performance before paper/live trading.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
import requests
import time
from urllib.parse import quote

from .strategy import generate_signal, TradeSignal


UPSTOX_WATCHLIST = {
    "RELIANCE": "NSE_EQ|INE002A01018",
    "TCS": "NSE_EQ|INE467B01029",
    "HDFCBANK": "NSE_EQ|INE040A01034",
    "INFY": "NSE_EQ|INE009A01021",
    "ITC": "NSE_EQ|INE154A01025",
    "ICICIBANK": "NSE_EQ|INE090A01021",
    "SBIN": "NSE_EQ|INE062A01020",
    "KOTAKBANK": "NSE_EQ|INE237A01036",
    "HINDUNILVR": "NSE_EQ|INE030A01027",
    "BHARTIARTL": "NSE_EQ|INE397D01024",
}


@dataclass
class BacktestTrade:
    entry_date: str
    direction: str
    entry_price: float
    stop_loss: float
    target: float
    exit_price: float
    pnl: float
    result: str  # "WIN", "LOSS"
    confidence: int


@dataclass
class BacktestResult:
    symbol: str
    period: str
    total_signals: int
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    max_drawdown: float
    trades: list

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "period": self.period,
            "total_signals": self.total_signals,
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.win_rate, 1),
            "total_pnl": round(self.total_pnl, 2),
            "avg_win": round(self.avg_win, 2),
            "avg_loss": round(self.avg_loss, 2),
            "profit_factor": round(self.profit_factor, 2),
            "max_drawdown": round(self.max_drawdown, 2),
            "trades": [
                {
                    "date": t.entry_date,
                    "dir": t.direction,
                    "entry": round(t.entry_price, 2),
                    "sl": round(t.stop_loss, 2),
                    "target": round(t.target, 2),
                    "exit": round(t.exit_price, 2),
                    "pnl": round(t.pnl, 2),
                    "result": t.result,
                    "conf": t.confidence,
                }
                for t in self.trades
            ],
        }


from .universe import NSE_UNIVERSE, get_instrument_key

def fetch_historical_data(
    symbol: str, from_date: str, to_date: str
) -> Optional[pd.DataFrame]:
    """Fetch historical daily candles from Upstox (no auth needed)."""
    inst_key = get_instrument_key(symbol) or UPSTOX_WATCHLIST.get(symbol)
    if not inst_key:
        return None

    encoded = quote(inst_key, safe="")
    url = f"https://api.upstox.com/v2/historical-candle/{encoded}/day/{to_date}/{from_date}"

    try:
        resp = requests.get(url, headers={"Accept": "application/json"}, timeout=10)
        data = resp.json()

        if data.get("status") != "success":
            return None

        candles = data["data"]["candles"]
        if not candles:
            return None

        rows = []
        for c in candles:
            rows.append({
                "timestamp": c[0],
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": int(c[5]),
            })

        df = pd.DataFrame(rows)
        df = df.sort_values("timestamp").reset_index(drop=True)
        return df

    except Exception as e:
        print(f"  [Backtest] Error fetching {symbol}: {e}")
        return None


def run_backtest(
    symbol: str,
    months: int = 6,
    capital: float = 10000.0,
    risk_pct: float = 2.0,
) -> Optional[BacktestResult]:
    """
    Run the multi-confluence strategy on historical data.

    For each day, we:
    1. Use a rolling window of the last 30 days as context
    2. Generate a signal on that day
    3. If signal fires, simulate a trade using next day's open as entry
    4. Check if SL or Target is hit using next day's high/low
    """
    today = datetime.now()
    from_date = (today - timedelta(days=months * 30 + 60)).strftime("%Y-%m-%d")
    to_date = today.strftime("%Y-%m-%d")

    print(f"  [Backtest] Fetching {symbol} data from {from_date} to {to_date}...")
    df = fetch_historical_data(symbol, from_date, to_date)

    if df is None or len(df) < 60:
        print(f"  [Backtest] Insufficient data for {symbol}")
        return None

    trades = []
    total_signals = 0
    window_size = 30

    for i in range(window_size, len(df) - 1):
        # Rolling window for signal generation
        window = df.iloc[i - window_size : i + 1].copy().reset_index(drop=True)
        signal = generate_signal(symbol, window)

        if signal.direction == "NONE":
            continue

        total_signals += 1

        # Simulate trade on next day
        next_day = df.iloc[i + 1]
        entry_price = next_day["open"]

        # Recalculate SL/target from actual entry
        sl_distance = abs(signal.entry_price - signal.stop_loss)
        sl_pct = sl_distance / signal.entry_price if signal.entry_price > 0 else 0.005

        if signal.direction == "BUY":
            stop_loss = entry_price * (1 - sl_pct)
            target = entry_price * (1 + sl_pct)
            # Check if SL or Target hit
            if next_day["low"] <= stop_loss:
                exit_price = stop_loss
                result = "LOSS"
            elif next_day["high"] >= target:
                exit_price = target
                result = "WIN"
            else:
                exit_price = next_day["close"]
                result = "WIN" if exit_price > entry_price else "LOSS"
            pnl = exit_price - entry_price

        else:  # SELL
            stop_loss = entry_price * (1 + sl_pct)
            target = entry_price * (1 - sl_pct)
            if next_day["high"] >= stop_loss:
                exit_price = stop_loss
                result = "LOSS"
            elif next_day["low"] <= target:
                exit_price = target
                result = "WIN"
            else:
                exit_price = next_day["close"]
                result = "WIN" if exit_price < entry_price else "LOSS"
            pnl = entry_price - exit_price

        # Calculate qty based on risk
        risk_amount = capital * risk_pct / 100
        qty = max(1, int(risk_amount / sl_distance)) if sl_distance > 0 else 1
        pnl_amount = pnl * qty

        trades.append(BacktestTrade(
            entry_date=str(next_day["timestamp"])[:10],
            direction=signal.direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            target=target,
            exit_price=exit_price,
            pnl=pnl_amount,
            result=result,
            confidence=signal.confidence,
        ))

    # Calculate stats
    wins = [t for t in trades if t.result == "WIN"]
    losses = [t for t in trades if t.result == "LOSS"]
    win_count = len(wins)
    loss_count = len(losses)
    total = len(trades)

    win_rate = (win_count / total * 100) if total > 0 else 0
    avg_win = (sum(t.pnl for t in wins) / win_count) if win_count > 0 else 0
    avg_loss = (sum(abs(t.pnl) for t in losses) / loss_count) if loss_count > 0 else 0

    gross_profit = sum(t.pnl for t in wins)
    gross_loss = sum(abs(t.pnl) for t in losses)
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    # Max drawdown
    cumulative = 0
    peak = 0
    max_dd = 0
    for t in trades:
        cumulative += t.pnl
        peak = max(peak, cumulative)
        dd = peak - cumulative
        max_dd = max(max_dd, dd)

    period = f"{from_date} to {to_date}"

    return BacktestResult(
        symbol=symbol,
        period=period,
        total_signals=total_signals,
        total_trades=total,
        wins=win_count,
        losses=loss_count,
        win_rate=win_rate,
        total_pnl=sum(t.pnl for t in trades),
        avg_win=avg_win,
        avg_loss=avg_loss,
        profit_factor=profit_factor,
        max_drawdown=max_dd,
        trades=trades,
    )


def run_full_backtest(
    symbols: list[str] = None,
    months: int = 6,
    capital: float = 10000.0,
) -> list[dict]:
    """Run backtest on all symbols and return combined results."""
    if symbols is None:
        symbols = list(UPSTOX_WATCHLIST.keys())

    results = []
    for sym in symbols:
        print(f"\n  [Backtest] Testing {sym}...")
        result = run_backtest(sym, months=months, capital=capital)
        if result:
            results.append(result.to_dict())
        time.sleep(0.5)  # Rate limit

    return results
