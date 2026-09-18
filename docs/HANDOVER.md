# Project Atlas — Complete Handover Document

**From:** Antigravity (Claude Opus via Google Antigravity)
**To:** Claude Code (Claude Pro)
**Date:** September 19, 2026
**Context:** Revan has purchased Claude Pro. This document transfers full project context so Claude Code can continue development independently.

---

## 1. What Is Project Atlas?

Project Atlas is an **autonomous multi-asset Indian stock market trading bot** that:
- Runs 24/7 on **Android Termux** (phone) and **Windows desktop**
- Trades **NSE Equities** (181 stocks), **NSE Currency Futures** (4 pairs), **MCX Commodities** (5 instruments)
- Uses a 3-layer AI pipeline: **HMM Regime → MLP Conviction → PPO RL Optimizer**
- Controlled remotely via **Telegram bot** commands
- Currently in **PAPER trading** mode with ₹1,50,000 capital
- Broker: **Upstox** (Go engine handles API auth)
- **Zero PyTorch/TensorFlow** — all ML is pure Python `math` module (Termux ARM compatibility)

**Repo:** `https://github.com/RawBut-Revi/atlas.git` | **Branch:** `atlas`

---

## 2. Development History

### Phase 1 (Pre-Sep 3, 2026)
- Basic trading daemon with equity, currency, commodity scanning
- Simple signal generation (RSI, EMA, ATR, VWAP, volume)
- 3-hour candlestick pattern recognition
- Gap trading strategies
- Telegram bot interface
- Transaction cost modeling (STT, GST, brokerage, exchange fees)
- **Result:** Functional but undisciplined — COPPER standard lot wiped ₹19,543 in one trade

### Phase 2 (Sep 3, 2026) — commit `9319c9d`
- **Hybrid Neural-Markov Architecture** added:
  - Layer 1: 3-state Gaussian HMM (Bull/Bear/Chop regime detection)
  - Layer 2: 18→32→16→1 MLP (P(Win) estimation, 78% threshold)
- **News Radar decommissioned** (caused 53-trade JPYINR loop burning ₹10,454 in brokerage on Sep 4)
- **Daily trade cap**: Max 3 trades per symbol per day
- **Result:** Currency trading became profitable (+₹14,803), equity/commodity still losing

### Phase 3 (Sep 5, 2026) — commits `5fbc138` through `32511ec`
5 tasks completed:
1. **`asset_threads.py`** — Dedicated Equity/Currency/Commodity worker threads with isolated HMMs
2. **`neural_markov.py` singleton removal** — Thread-safe HMM/NN instances (no global state)
3. **`daemon.py` Master Orchestrator** — 4 concurrent daemon threads replace monolithic scheduler
4. **`rl_optimizer.py`** — PPO policy network (22→64→32→3), reward function, weight serialization
5. **`rl_trainer.py`** + daemon integration — Offline PPO training on 175 trades, RL wired into all scan methods

**Result:** Multi-threaded architecture working. RL trained but outputs near-flat policy (~0.97x scaling). All tests passing.

---

## 3. Current State (as of Sep 19, 2026)

### Portfolio
- **183 closed trades** in `paper_positions.json`
- **2 open positions**: NATGASMINI (BUY) and SILVERMIC (BUY)
- **Capital:** ₹1,50,000
- **Total P&L:** ₹-13,350.76

### Performance Analysis (Deep Dive Done)
| Metric | Value |
|--------|-------|
| Gross P&L | **₹-950** (nearly breakeven on market moves!) |
| Total Charges | **₹12,401** (93% of total loss) |
| Net P&L | ₹-13,351 |
| Win Rate | 38.8% (71W / 112L) |
| Profit Factor | 0.69 |
| Max Drawdown | ₹23,870 |
| Sharpe | -5.17 |
| Max Losing Streak | 21 trades |

### Asset Class Performance
| Asset | Trades | Win Rate | Net P&L |
|-------|--------|----------|---------|
| **CURRENCY** | 112 | **59.8%** | **+₹14,803** ✅ |
| EQUITY | 48 | 4.2% | -₹10,032 ❌ |
| COMMODITY | 23 | 8.7% | -₹18,122 ❌ |

### Strategy Performance
| Strategy | Trades | Win Rate | Net P&L | Action |
|----------|--------|----------|---------|--------|
| **TREND_MOMENTUM** | 95 | **54.7%** | **+₹12,233** | ✅ Keep |
| 3H_PATTERN_BREAKOUT | 20 | 80% | -₹14,671 | ⚠️ COPPER killed it |
| INTRADAY | 38 | **0%** | -₹2,452 | ❌ DISABLE |
| US_SESSION_MOMENTUM | 18 | **0%** | -₹1,044 | ❌ DISABLE |
| GAP_FADE | 7 | 14% | -₹8,564 | ❌ DISABLE |
| GAP_AND_GO | 3 | 33% | +₹984 | 🟡 Monitor |

---

## 4. AI Systems — Detailed Assessment

### HMM Regime Filter — ✅ WORKING
- Correctly identifies Bull (P=98%), Bear (P=88%), Chop (P=94%) from price data
- Each asset thread has isolated HMM (Phase 3)
- Blocks trades in CHOP_CONSOLIDATION when P(Chop) ≥ 65%

### MLP Conviction Engine — ⚠️ SPLIT PERSONALITY
- **Great for FX:** USDINR 97.6% WR, GBPINR 93.3% WR
- **Terrible for equity:** INTRADAY trades have 0% WR but MLP says P(Win) > 78%
- **Root cause:** MLP doesn't consider transaction costs. Equity trades with ₹0-5 gross move pay ₹37-42 in charges
- The 36 INTRADAY losses are all ₹-37 to ₹-42 (exactly equal to charges — zero price movement)

### PPO RL Optimizer — 🟡 EARLY STAGE
- 8 live trades so far (Sep 7, all with RL)
- Policy shows some directional sensitivity:
  - Favorable → risk 0.96x, exit pref 0.43
  - Unfavorable → risk 0.86x, exit pref 0.32, trail 0.57
- But range is narrow (0.86–0.96 vs theoretical 0.5–1.5)
- Trained with numerical gradient (not proper PPO clipped objective)
- Needs 500+ diverse trades for meaningful policy

### 🔴 Critical Blind Spot
**No AI layer considers transaction costs.** The pipeline approves trades where expected_profit < charges. This is the #1 cause of losses.

---

## 5. Key Files Reference

| File | Lines | Purpose |
|------|-------|---------|
| [daemon.py](file:///c:/Users/revan/OneDrive/Documents/alog_trade/research/src/trading/daemon.py) | 1265 | Master orchestrator, scan methods, Telegram handler |
| [neural_markov.py](file:///c:/Users/revan/OneDrive/Documents/alog_trade/research/src/trading/neural_markov.py) | 370 | HMM + MLP (evaluate_trade_conviction, get_current_regime_status) |
| [rl_optimizer.py](file:///c:/Users/revan/OneDrive/Documents/alog_trade/research/src/trading/rl_optimizer.py) | ~400 | PPO policy network, RLState, RLAction, RLExecutionOptimizer |
| [rl_trainer.py](file:///c:/Users/revan/OneDrive/Documents/alog_trade/research/src/trading/rl_trainer.py) | ~300 | Offline PPO trainer, RLPolicyTrainer |
| [risk.py](file:///c:/Users/revan/OneDrive/Documents/alog_trade/research/src/trading/risk.py) | 169 | RiskManager, calculate_position_size (with max_risk_budget) |
| [charges.py](file:///c:/Users/revan/OneDrive/Documents/alog_trade/research/src/trading/charges.py) | 202 | TradeCharges, calculate_trade_charges |
| [asset_threads.py](file:///c:/Users/revan/OneDrive/Documents/alog_trade/research/src/trading/asset_threads.py) | ~350 | EquityThread, CurrencyThread, CommodityThread |
| [strategy.py](file:///c:/Users/revan/OneDrive/Documents/alog_trade/research/src/trading/strategy.py) | ~280 | generate_signal for equities |
| [currency_strategy.py](file:///c:/Users/revan/OneDrive/Documents/alog_trade/research/src/trading/currency_strategy.py) | ~400 | scan_all_currency_pairs, generate_currency_signal |
| [commodity_strategy.py](file:///c:/Users/revan/OneDrive/Documents/alog_trade/research/src/trading/commodity_strategy.py) | ~400 | scan_all_commodities, generate_commodity_signal |
| [paper_positions.json](file:///c:/Users/revan/OneDrive/Documents/alog_trade/research/src/paper_positions.json) | 6875 | Live state file (open_positions + trade_history) |

### Key Function Signatures
```python
# Neural Markov gate
evaluate_trade_conviction(signal: dict, df, hmm_instance=None, nn_instance=None) -> dict
# Returns: {approved: bool, regime: str, win_probability: float, regime_probs: dict, decision: str}

# Position sizing (supports RL budget override)
RiskManager.calculate_position_size(entry_price, stop_loss, asset_type='EQUITY', lot_multiplier=1, max_risk_budget=None) -> (qty, risk_inr, status_msg)

# Charges calculation
calculate_trade_charges(symbol, asset_type, direction, entry_price, exit_price, qty_or_lots=1) -> TradeCharges

# RL action
RLExecutionOptimizer.get_optimal_action(state: RLState) -> RLAction
# RLAction: risk_scaling [0.5,1.5], exit_preference [0,1], trail_tightness [0.3,0.7]
```

---

## 6. Phase 4 Blueprint — What to Build Next

### Task 1: Charges-Aware Trade Filter (CRITICAL — Do First)

**Goal:** Add a minimum expected profit filter after the Neural Markov gate. Reject trades where expected profit cannot cover transaction costs.

**Where:** In `daemon.py`, in each of the 4 scan methods:
- `run_gap_scan()` — after line ~906 (position sizing)
- `run_scan_cycle()` — after line ~1003 (position sizing)
- `run_currency_scan()` — after line ~1078 (position sizing)
- `run_commodity_scan()` — after line ~1157 (position sizing)

**Implementation:**
```python
# Add this helper method to TradingDaemon class:
def passes_charges_filter(self, symbol, asset_type, direction, entry_price, target_price, qty, lot_multiplier=1):
    """Reject trades where expected profit < 2x estimated charges."""
    from trading.charges import calculate_trade_charges
    estimated = calculate_trade_charges(symbol, asset_type, direction, entry_price, target_price, qty)
    expected_profit = abs(estimated.gross_pnl)
    estimated_charges = estimated.total_charges
    if expected_profit < 2 * estimated_charges:
        print(f"[Charges Filter] REJECTED {symbol}: Expected ₹{expected_profit:.0f} < 2×Charges ₹{2*estimated_charges:.0f}")
        return False
    return True
```

Then in each scan method, AFTER `calculate_position_size` but BEFORE creating the position:
```python
if not self.passes_charges_filter(symbol, asset_type, direction, entry_price, target_price, qty):
    continue
```

**Test:** Create trades with tiny targets (₹5 move on equity) and verify they get rejected. Create trades with large targets (₹500 move) and verify they pass.

---

### Task 2: Disable Dead Strategies

**Goal:** Prevent INTRADAY, US_SESSION_MOMENTUM, and GAP_FADE from executing trades.

**Where:** `daemon.py`, top of file near constants.

**Implementation:**
```python
# Add near line 48 (after TOP_INTRADAY_UNIVERSE)
DISABLED_STRATEGIES = {'INTRADAY', 'US_SESSION_MOMENTUM', 'GAP_FADE'}
```

In `run_scan_cycle()` (around line ~1003-1035), where the signal's strategy is checked:
```python
if sig_dict.get('strategy') in DISABLED_STRATEGIES:
    continue
```

Same check in `run_gap_scan()`, `run_currency_scan()`, `run_commodity_scan()`.

Also update the `/ai` Telegram command to show which strategies are disabled.

**Test:** Generate signals with disabled strategy names and verify they're skipped.

---

### Task 3: Global Daily Trade Cap

**Goal:** Max 8 new trades per day (total, not per symbol). After 8, only manage existing positions.

**Where:** `daemon.py`, in TradingDaemon class.

**Implementation:**
```python
# In __init__, add:
self.trades_opened_today = 0
self.last_trade_date = None

# Helper method:
def can_open_new_trade(self) -> bool:
    today = datetime.now(IST).strftime('%Y-%m-%d')
    if today != self.last_trade_date:
        self.trades_opened_today = 0
        self.last_trade_date = today
    return self.trades_opened_today < 8

def record_new_trade(self):
    today = datetime.now(IST).strftime('%Y-%m-%d')
    if today != self.last_trade_date:
        self.trades_opened_today = 0
        self.last_trade_date = today
    self.trades_opened_today += 1
```

Add `can_open_new_trade()` check at the top of each scan method's trade execution block. Call `record_new_trade()` after successful position creation.

**Test:** Open 8 trades in a test, verify 9th is blocked. Verify counter resets on new day.

---

### Task 4: Time-Based Entry Restrictions

**Goal:** Block trades during historically unprofitable time windows.

**Where:** `daemon.py`, in each scan method or as a helper.

**Implementation:**
```python
def is_entry_allowed(self, asset_type: str) -> bool:
    now = datetime.now(IST).time()
    if asset_type == 'EQUITY':
        # No entries before 10:00 (morning traps) or after 15:30 (exit only)
        if now < time(10, 0) or now > time(15, 30):
            return False
    elif asset_type == 'COMMODITY':
        # No entries at MCX open (avoid 12:00 volatility spike)
        if time(11, 55) <= now <= time(12, 10):
            return False
    return True
```

**Test:** Mock different times and verify correct blocking.

---

### Task 5: Charges-Aware MLP Retraining

**Goal:** Retrain the MLP conviction engine to predict "will net P&L be positive?" instead of just "will price hit target?"

**Where:** `neural_markov.py` — `TargetHitNeuralNet` class.

**Implementation:**
1. Add `estimated_charges_ratio` as 19th input feature (charges / expected_profit)
2. Retrain on 183 historical trades using their actual net P&L as the target label
3. Update `evaluate_trade_conviction()` to pass the charges ratio
4. Keep the 78% threshold but now it means "78% chance of NET positive P&L"

This is complex — requires updating the MLP input dimensions, retraining weights, and updating all callers. Do this after Tasks 1-4 are proven.

---

### Task 6: Proper PPO Implementation

**Goal:** Replace numerical gradient with real PPO algorithm for better policy learning.

**Where:** `rl_trainer.py` and `rl_optimizer.py`

**Implementation:**
1. Implement PPO clipped surrogate objective: $L^{CLIP} = \min(r_t A_t, \text{clip}(r_t, 1-\epsilon, 1+\epsilon) A_t)$
2. Add value function head to PPOPolicyNetwork (separate 22→64→32→1 network)
3. Implement Generalized Advantage Estimation (GAE-λ)
4. Use proper trajectory rollouts (not single-step)
5. Train with proper mini-batch SGD

**Constraints:** Still pure Python, no PyTorch. Use the existing matrix multiplication infrastructure in rl_optimizer.py.

This is the most complex task — defer until 500+ trades are available in paper_positions.json.

---

## 7. Known Bugs & Gotchas

| Issue | Severity | Detail |
|-------|----------|--------|
| Non-atomic state access | MEDIUM | Two threads can race on paper_positions.json. state_lock protects individual calls but not read-modify-write cycles |
| COPPER standard lots | FIXED (Phase 2) | Multiplier cap blocks standard lots. Verify it still works |
| JPYINR lot multiplier | LOW | 100,000x per lot. Tiny moves = huge P&L. 3-trade cap helps |
| Windows cp1252 encoding | LOW | Emoji in print() crashes PowerShell. Test scripts need UTF-8 reconfigure |
| News Radar zombie | LOW | `news_radar.py` still exists but is decommissioned. Don't re-enable |
| HMM cold start | LOW | Fresh HMM defaults to CHOP. Needs 10-20 price bars to converge |

---

## 8. Running the Bot

```bash
# Start the trading daemon
cd research/src
python -m trading.daemon

# Run tests
python -m pytest tests/  # if formal tests exist
# Or run individual test scripts from scratch/

# Go engine (broker API)
cd engine
./atlas.exe auth   # Upstox OAuth
./atlas.exe token  # Refresh access token
```

---

## 9. Revan's Preferences

- Calls the bot "Project Atlas"
- Wants autonomous operation — minimal human intervention
- Prefers aggressive development pace — "complete this week"
- Values token efficiency — use Opus for architecture/design, Flash for mechanical coding
- Tests everything on paper first before going live
- Monitors from phone via Telegram
- Git branch: always `atlas`
- Commit style: `feat(phaseN): description`

---

## 10. Summary for Claude Code

**You are continuing Project Atlas from Phase 3 (complete) into Phase 4.**

The bot was nearly gross-breakeven (₹-950) but ₹12,401 in transaction costs turned it into a ₹-13,351 loss. The fix is straightforward:

1. **Add charges filter** — reject trades that can't cover their own costs
2. **Kill dead strategies** — INTRADAY (0% WR), US_SESSION, GAP_FADE
3. **Cap daily trades** — max 8 total per day
4. **Time restrictions** — avoid historically bad hours
5. **Retrain MLP** — predict net P&L, not just target hit
6. **Proper PPO** — once enough data exists

**The CLAUDE.md file is at the repo root.** Read it first every session.

Good luck. The currency TREND_MOMENTUM edge is real — protect it and cut the noise.

— Opus out. 🎯
