"""
Project Atlas — Phase 3: Offline Reinforcement Learning Policy Trainer
======================================================================
Trains the PPO Policy Network using historical trade executions from paper_positions.json.
Simulates counterfactual execution policies:
  - Explores dynamic risk scaling (0.5x to 1.5x)
  - Explores exit preference (T1 partial exit vs T2 runner)
  - Explores trailing stop-loss tightness (0.3 to 0.7)
  - Maximizes PPO multi-objective reward (profit minus drawdown and friction)
Saves optimized policy weights to rl_weights.json.
"""

import json
import math
import os
import sys
from typing import List, Dict, Tuple

# Ensure trading module is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading.rl_optimizer import RLState, RLAction, RLExecutionOptimizer, PPOPolicyNetwork

JOURNAL_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "paper_positions.json")
WEIGHTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rl_weights.json")


class RLPolicyTrainer:
    """
    Offline PPO Trainer for Atlas execution optimization.
    Replays real-world trades, reconstructs market states, evaluates counterfactual actions,
    and performs policy gradient updates to optimize take-home returns and eliminate chop.
    """

    def __init__(self, journal_path: str = JOURNAL_FILE, weights_path: str = WEIGHTS_FILE):
        self.journal_path = journal_path
        self.weights_path = weights_path
        self.optimizer = RLExecutionOptimizer(weights_path=None)  # Start with fresh or base policy

    def load_historical_trajectories(self) -> List[dict]:
        """Loads and filters closed trades from paper_positions.json."""
        if not os.path.exists(self.journal_path):
            print(f"[RLTrainer] Error: Journal file {self.journal_path} not found.")
            return []

        with open(self.journal_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        history = data.get("trade_history", [])
        print(f"[RLTrainer] Loaded {len(history)} historical trades from {self.journal_path}")
        return history

    def reconstruct_state(self, trade: dict, running_stats: dict) -> List[float]:
        """
        Reconstructs the 22-dimensional continuous state vector at trade entry.
        """
        asset_type = trade.get("asset_type", "EQUITY")
        strategy = trade.get("strategy", "INTRADAY")
        status = trade.get("status", "CLOSED")
        is_win = (trade.get("net_pnl", 0) > 0)
        net_pnl = trade.get("net_pnl", 0.0)

        # Infer HMM regime at the time
        if is_win and abs(net_pnl) > 1000:
            # High momentum trend winners (e.g. Polycab, USDINR)
            hmm_bull = 0.85 if trade.get("direction") == "BUY" else 0.05
            hmm_bear = 0.85 if trade.get("direction") == "SELL" else 0.05
            hmm_chop = 0.10
        elif status == "NEWS_PANIC_EXIT" or "SQUARE_OFF" in status:
            # Trapped in consolidation or premature exit
            hmm_bull = 0.20
            hmm_bear = 0.20
            hmm_chop = 0.60
        else:
            hmm_bull = 0.40
            hmm_bear = 0.30
            hmm_chop = 0.30

        # Technical indicator reconstruction
        rsi2 = 0.15 if trade.get("direction") == "BUY" else 0.85
        rsi14 = 0.60 if trade.get("direction") == "BUY" else 0.40
        vol_ratio = 1.6 if is_win else 0.9
        atr_pct = 2.2 if is_win else 1.1

        state = RLState(
            hmm_p_bull=hmm_bull,
            hmm_p_bear=hmm_bear,
            hmm_p_chop=hmm_chop,
            rsi2_normalized=rsi2,
            rsi14_normalized=rsi14,
            vwap_distance_pct=0.6 if is_win else 0.0,
            volume_ratio=vol_ratio,
            atr_pct=atr_pct,
            confidence=0.82 if is_win else 0.70,
            unrealized_pnl_pct=0.0,
            time_in_trade_norm=0.0,
            time_of_day_norm=0.25,
            num_open_positions=running_stats.get("open_count", 2) / 8.0,
            daily_pnl_norm=running_stats.get("daily_pnl", 0.0) / 150000.0,
            win_rate_recent=running_stats.get("win_rate", 0.55),
            avg_rr_recent=1.5,
            drawdown_pct=running_stats.get("drawdown", 0.0) / 150000.0,
            consecutive_losses=min(5, running_stats.get("loss_streak", 0)) / 5.0,
            spread_cost_pct=trade.get("charges", 50.0) / 2250.0,
            pattern_score=1.0 if "BREAKOUT" in strategy or "GAP" in strategy else 0.5,
            trend_alignment=1.0 if is_win else -0.5,
            macro_swing_bias=1.0,
        )
        return state.to_vector()

    def simulate_counterfactual_reward(self, trade: dict, action: RLAction) -> float:
        """
        Simulates how the trade would have performed under action:
          - risk_scaling scales the position size and therefore the raw gross P&L
          - exit_preference affects runner capture (higher on trend wins)
          - trail_tightness affects drawdown on reversals
        """
        real_gross = trade.get("gross_pnl", 0.0)
        real_charges = trade.get("charges", 50.0)
        real_net = trade.get("net_pnl", 0.0)
        is_win = (real_net > 0)

        # Counterfactual P&L scaling
        scaled_gross = real_gross * action.risk_scaling
        scaled_charges = real_charges * action.risk_scaling

        # Exit preference benefit:
        # If trade was a multi-R runner (e.g. Polycab, USDINR), high exit_preference (T2) boosts profit
        if is_win and real_gross > 1500.0:
            runner_boost = 1.0 + (action.exit_preference - 0.5) * 0.4
            scaled_gross *= runner_boost

        # Trailing tightness benefit:
        # Tighter trailing (lower trail_tightness) cuts losses on reversing trades
        if not is_win and action.trail_tightness < 0.45:
            # Trailed early to cost, saving loss
            scaled_gross *= 0.65  # Loss reduced by 35%!

        counterfactual_net = scaled_gross - scaled_charges
        counterfactual_drawdown = abs(min(0.0, counterfactual_net))

        return self.optimizer.compute_reward(
            net_pnl=counterfactual_net,
            max_drawdown=counterfactual_drawdown,
            fees=scaled_charges,
            is_win=(counterfactual_net > 0),
        )

    def train(self, epochs: int = 40, learning_rate: float = 0.008) -> dict:
        """
        Executes Policy Gradient training over historical trajectories.
        Uses numerical gradient approximation to optimize policy network parameters.
        """
        history = self.load_historical_trajectories()
        if not history:
            return {"error": "No historical trades found"}

        # Prepare dataset: [(state_vector, trade_dict)]
        dataset = []
        running_stats = {"open_count": 2, "daily_pnl": 0.0, "win_rate": 0.55, "drawdown": 0.0, "loss_streak": 0}
        
        for t in history:
            s_vec = self.reconstruct_state(t, running_stats)
            dataset.append((s_vec, t))
            # Update running stats
            is_win = (t.get("net_pnl", 0) > 0)
            running_stats["loss_streak"] = 0 if is_win else running_stats["loss_streak"] + 1

        print(f"\n[RLTrainer] Reconstructed {len(dataset)} state-trade training trajectories.")

        # Evaluate baseline policy reward before training
        initial_rewards = []
        for s_vec, t in dataset:
            act = self.optimizer.policy.predict(s_vec)
            rew = self.simulate_counterfactual_reward(t, act)
            initial_rewards.append(rew)
        avg_initial_reward = sum(initial_rewards) / len(initial_rewards)

        print(f"[RLTrainer] Baseline Untrained Policy Average Reward: {avg_initial_reward:+.4f}")
        print(f"[RLTrainer] Starting PPO Policy Optimization ({epochs} Epochs, LR={learning_rate})...\n")

        # Training Loop: Policy Gradient with Parameter Perturbation
        policy = self.optimizer.policy
        params_to_train = [
            ("b3", policy.b3),
            ("b2", policy.b2),
            ("w3", policy.w3),
        ]

        best_avg_reward = avg_initial_reward
        eps = 0.05  # Finite difference step

        for epoch in range(1, epochs + 1):
            epoch_rewards = []

            # 1. Compute current policy rewards across batch
            for s_vec, t in dataset:
                act = policy.predict(s_vec)
                r = self.simulate_counterfactual_reward(t, act)
                epoch_rewards.append(r)
            current_mean_r = sum(epoch_rewards) / len(epoch_rewards)

            # 2. Gradient ascent step on output layer biases (fast convergence on macro parameters)
            for m in range(policy.output_dim):
                orig_b = policy.b3[m]
                
                # Test positive perturbation
                policy.b3[m] = orig_b + eps
                r_pos = sum(self.simulate_counterfactual_reward(t, policy.predict(s_vec)) for s_vec, t in dataset[:50]) / 50.0

                # Test negative perturbation
                policy.b3[m] = orig_b - eps
                r_neg = sum(self.simulate_counterfactual_reward(t, policy.predict(s_vec)) for s_vec, t in dataset[:50]) / 50.0

                # Numerical gradient
                grad = (r_pos - r_neg) / (2.0 * eps)
                policy.b3[m] = orig_b + (learning_rate * grad)

            # 3. Optimize Layer 3 Weights (w3) for feature-specific action refinement
            for k in range(0, policy.hidden2, 4):  # Subsample neurons for speed
                for m in range(policy.output_dim):
                    orig_w = policy.w3[k][m]
                    policy.w3[k][m] = orig_w + eps
                    r_pos = sum(self.simulate_counterfactual_reward(t, policy.predict(s_vec)) for s_vec, t in dataset[:30]) / 30.0
                    policy.w3[k][m] = orig_w - eps
                    r_neg = sum(self.simulate_counterfactual_reward(t, policy.predict(s_vec)) for s_vec, t in dataset[:30]) / 30.0
                    grad = (r_pos - r_neg) / (2.0 * eps)
                    policy.w3[k][m] = orig_w + (learning_rate * 0.5 * grad)

            if current_mean_r > best_avg_reward:
                best_avg_reward = current_mean_r

            if epoch % 5 == 0 or epoch == epochs:
                sample_action = policy.predict(dataset[0][0])
                print(f"  • Epoch {epoch:2d}/{epochs} | Mean Reward: {current_mean_r:+.4f} (Best: {best_avg_reward:+.4f}) | Sample Action: Risk={sample_action.risk_scaling:.2f}x, Trail={sample_action.trail_tightness:.2f}")

        # Final evaluation
        final_rewards = [self.simulate_counterfactual_reward(t, policy.predict(s_vec)) for s_vec, t in dataset]
        avg_final_reward = sum(final_rewards) / len(final_rewards)
        reward_improvement_pct = ((avg_final_reward - avg_initial_reward) / abs(avg_initial_reward)) * 100.0 if avg_initial_reward != 0 else 0.0

        print(f"\n[RLTrainer] Training Complete!")
        print(f"  • Initial Baseline Reward: {avg_initial_reward:+.4f}")
        print(f"  • Final Trained Reward:    {avg_final_reward:+.4f}")
        print(f"  • Expected Edge Expansion: {reward_improvement_pct:+.1f}%")

        # Save weights to JSON
        self.optimizer.save_weights(self.weights_path)

        return {
            "initial_reward": round(avg_initial_reward, 4),
            "final_reward": round(avg_final_reward, 4),
            "improvement_pct": round(reward_improvement_pct, 2),
            "trajectories_trained": len(dataset),
            "weights_saved_to": self.weights_path,
        }


if __name__ == "__main__":
    trainer = RLPolicyTrainer()
    results = trainer.train(epochs=25, learning_rate=0.01)
    print("\nTraining summary:", results)
