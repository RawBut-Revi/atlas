---
name: test-atlas
description: Run all Atlas trading bot test suites and report results
---

# Test Atlas

Run all test suites for the trading bot and report pass/fail status.

## Steps

1. Set working directory to `research/src`
2. Run each test module using `research/.venv/Scripts/python.exe -m pytest` or direct execution
3. Test areas to cover:
   - Module imports: All 9 core modules resolve (daemon, asset_threads, neural_markov, rl_optimizer, rl_trainer, risk, strategy, currency_strategy, commodity_strategy)
   - TradingDaemon instantiation: creates with risk_manager, rl_optimizer, state_lock, is_running
   - Asset thread wiring: 3 threads with isolated HMM instances
   - RiskManager: calculate_position_size with and without max_risk_budget
   - Neural Markov: evaluate_trade_conviction returns regime + approved status
   - RL Optimizer: get_optimal_action returns bounded RLAction
   - State lock: concurrent read/write safety
   - RL weights: correct dimensions 22→64→32→3

## Important
- Always add `sys.stdout.reconfigure(encoding="utf-8")` at top of test scripts
- Mock `save_state` to avoid overwriting real `paper_positions.json`
- Python: `research/.venv/Scripts/python.exe`
- sys.path must include `research/src` for trading module imports
