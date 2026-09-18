---
name: train-rl
description: Retrain the PPO RL optimizer on latest trade data from paper_positions.json
---

# Train RL

Retrain the PPO Reinforcement Learning execution optimizer on the latest trade history.

## Steps

1. Read `research/src/paper_positions.json` to get latest trade count
2. Run the RL trainer:
   ```bash
   cd research/src
   python -c "
   import sys
   sys.stdout.reconfigure(encoding='utf-8')
   from trading.rl_trainer import RLPolicyTrainer
   trainer = RLPolicyTrainer()
   trainer.train(epochs=50, lr=0.001)
   "
   ```
3. Verify the new weights at `research/src/trading/rl_weights.json`:
   - Check keys: w1, b1, w2, b2, w3, b3, log_std
   - Verify dimensions: w1=22×64, w2=64×32, w3=32×3
4. Test the new policy with differentiation check:
   - Feed favorable state (Bull, high confidence) → expect higher risk_scaling
   - Feed unfavorable state (Bear, losing streak) → expect lower risk_scaling
   - Report the range of outputs
5. Commit updated weights: `feat(rl): retrain PPO on N trades`

## Important
- The trainer reads from paper_positions.json automatically
- Training uses numerical gradient ascent (not proper PPO yet — that's Phase 4 Task 6)
- Expect baseline reward around -0.07 with 183 trades
- Weight file is ~77KB
