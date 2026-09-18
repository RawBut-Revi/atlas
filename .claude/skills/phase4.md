---
name: phase4
description: Execute the next Phase 4 task from the implementation blueprint in docs/HANDOVER.md
---

# Phase 4 Execution

Execute the next pending Phase 4 task from the implementation blueprint.

## Context
Read `docs/HANDOVER.md` Section 6 "Phase 4 Blueprint" for the full task list. The 6 tasks in priority order are:

1. **Charges-Aware Trade Filter** — Add minimum expected profit filter after Neural Markov gate
2. **Disable Dead Strategies** — Block INTRADAY, US_SESSION_MOMENTUM, GAP_FADE
3. **Global Daily Trade Cap** — Max 8 new trades per day (total, not per symbol)
4. **Time-Based Entry Restrictions** — Block historically unprofitable hours
5. **Charges-Aware MLP Retraining** — Train MLP on net P&L not just target hit
6. **Proper PPO Implementation** — Clipped surrogate objective, value baseline, GAE

## Workflow
1. Read `docs/HANDOVER.md` to identify which task to work on (user may specify via $ARGUMENTS)
2. Read the relevant source files listed in the task description
3. Implement the changes
4. Write tests for the changes
5. Run the tests and verify they pass
6. Commit with message format: `feat(phase4): Task N - description`

## Rules
- NO PyTorch/TensorFlow/NumPy — pure Python math only
- Always use `with self.state_lock:` for state access
- Test scripts need `sys.stdout.reconfigure(encoding="utf-8")`
- Mock `save_state` in tests to protect `paper_positions.json`
- Commit to `atlas` branch
