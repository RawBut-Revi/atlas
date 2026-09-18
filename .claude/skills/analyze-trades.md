---
name: analyze-trades
description: Run deep trade performance analysis on paper_positions.json — P&L breakdown, win rate by asset/strategy/hour, failure diagnosis
---

# Analyze Trades

Run a comprehensive analysis on the trading bot's performance.

## Steps

1. Read `research/src/paper_positions.json`
2. Parse all closed trades from `trade_history`
3. Compute and display:
   - Total trades, wins, losses, win rate
   - Gross P&L, charges, net P&L
   - Avg win, avg loss, profit factor, expectancy
   - P&L standard deviation, max drawdown
   - Breakdown by asset class (EQUITY, CURRENCY, COMMODITY)
   - Breakdown by strategy (TREND_MOMENTUM, INTRADAY, etc.)
   - Breakdown by hour of day
   - Breakdown by date
   - Top 10 worst symbols, top 5 best symbols
   - RL vs non-RL trade comparison (trades with `rl_risk_scaling` field)
   - Exit analysis: SQUARE_OFF vs TARGET_HIT vs SL_HIT counts
   - Consecutive losing streak
4. Flag any strategies with 0% win rate as "DISABLE CANDIDATE"
5. Flag any symbols with >3 trades and 0% win rate as "REMOVE FROM UNIVERSE"

## Important
- Use `sys.stdout.reconfigure(encoding="utf-8")` for Windows
- Python: `research/.venv/Scripts/python.exe`
- Charges are in `charges_breakdown.total_charges` or top-level `charges` field
- `result` field is "WIN" or "LOSS"
- RL trades have `rl_risk_scaling`, `rl_exit_preference`, `rl_trail_tightness` fields
