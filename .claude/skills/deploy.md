---
name: deploy
description: Sync latest code to phone (Termux) and restart the trading daemon
---

# Deploy Atlas

Deploy the latest code and restart the trading daemon.

## Steps

1. Run all tests first (abort if any fail)
2. Stage and commit any uncommitted changes
3. Push to `origin atlas`
4. Remind user to pull on phone:
   ```bash
   cd ~/alog_trade && git pull origin atlas
   cd research/src && python -m trading.daemon
   ```
5. Show current open positions and P&L status

## Pre-flight Checks
- Verify `paper_positions.json` is not corrupted (valid JSON with open_positions + trade_history)
- Verify no debug/test code left in daemon.py
- Verify no hardcoded test values in risk.py or rl_optimizer.py
