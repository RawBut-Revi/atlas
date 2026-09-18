---
name: status
description: Quick status check — open positions, P&L, git status, last commit
---

# Atlas Status

Quick status overview of the trading bot.

## Steps

1. Read `research/src/paper_positions.json`:
   - Number of open positions (with symbols and unrealized P&L if possible)
   - Number of historical trades
   - Total realized P&L
   - Capital
2. Show git status:
   - Current branch
   - Last 3 commits
   - Any uncommitted changes
3. Show RL optimizer status:
   - When weights were last trained (file modification date of `rl_weights.json`)
   - Number of trades the model was trained on
4. Show swing watchlist summary from `research/src/swing_watchlist.json`

Format everything as a clean, concise summary table.
