# CLAUDE.md — Project Atlas

## Identity

**Project Atlas** is an autonomous multi-asset Indian market trading bot built for zero-human-intervention intraday + swing trading. It runs 24/7 on Android Termux and Windows desktop, monitoring NSE Equities, NSE Currency Futures, and MCX Commodities.

**Repo:** `https://github.com/RawBut-Revi/atlas.git` | **Branch:** `atlas`
**Owner:** Revan (single developer, trades from phone via Telegram)

---

## Project Structure

```
alog_trade/
├── research/                    # Python trading bot (PRIMARY CODEBASE)
│   ├── src/
│   │   ├── trading/             # Core trading engine
│   │   │   ├── daemon.py        # Master orchestrator (1265 lines, v5)
│   │   │   ├── asset_threads.py # Equity/Currency/Commodity worker threads
│   │   │   ├── neural_markov.py # HMM regime filter + MLP conviction engine
│   │   │   ├── rl_optimizer.py  # PPO RL execution optimizer (22→64→32→3)
│   │   │   ├── rl_trainer.py    # Offline PPO trainer
│   │   │   ├── rl_weights.json  # Trained PPO weights
│   │   │   ├── risk.py          # Position sizing + risk management
│   │   │   ├── strategy.py      # Equity signal generation
│   │   │   ├── currency_strategy.py
│   │   │   ├── commodity_strategy.py
│   │   │   ├── gap_strategy.py  # Gap-up/down plays
│   │   │   ├── charges.py       # Transaction cost modeling (STT/GST/brokerage)
│   │   │   ├── indicators.py    # RSI, EMA, ATR, VWAP, Volume
│   │   │   ├── patterns.py      # 3-hour candlestick pattern recognition
│   │   │   ├── swing_radar.py   # Multi-day swing trade scanner
│   │   │   ├── telegram_bot.py  # Telegram command interface
│   │   │   ├── universe.py      # 181-stock NSE universe
│   │   │   ├── backtest.py      # Historical data fetcher
│   │   │   └── news_radar.py    # DECOMMISSIONED (caused 53-trade JPYINR loop)
│   │   ├── screening/           # Penny stock screener
│   │   ├── paper_positions.json # Live state: open positions + 183 trade history
│   │   └── swing_watchlist.json # Active swing trade setups
│   └── .venv/                   # Python 3.12 virtual environment
├── engine/                      # Go CLI for Upstox broker API
│   ├── cmd/                     # CLI commands
│   ├── internal/                # Upstox API client, token management
│   └── atlas.exe                # Compiled binary
├── terminal/                    # Wails v2 desktop app (Bloomberg-style UI)
│   ├── frontend/                # React + Tailwind dashboard
│   ├── app.go, auth.go, services.go, main.go
│   └── terminal.exe
└── CLAUDE.md                    # THIS FILE
```

---

## Architecture (Phase 3 — Current)

```
TradingDaemon (Master Orchestrator v5)
├── TelegramServer    — 0.8s poll, instant command handling
├── EquityThread      — 180s cycle, 09:15-15:15 IST, own HMM
├── CurrencyThread    — 90s cycle, 09:00-16:45 IST, own HMM
└── CommodityThread   — 120s cycle, 09:00-23:15 IST, own HMM

Trade Pipeline:
  Market Data → HMM Regime Filter → MLP Conviction (P(Win)≥78%) → RL PPO Sizing → Execute
                (Layer 1)           (Layer 2)                     (Layer 3)
```

Each asset thread has its own `GaussianMarketHMM()` instance — zero cross-thread state bleeding.

---

## Hard Constraints

- **NO PyTorch / TensorFlow / NumPy** — Must run on Android Termux (ARM). All ML is pure Python `math` module.
- **Python 3.12** — Venv at `research/.venv/`
- **Broker: Upstox** — Paper trading mode currently. Go engine handles auth/tokens.
- **Capital: ₹1,50,000** — Paper trading with real market data.
- **Telegram Bot** — Primary control interface. Commands: `/status`, `/positions`, `/pnl`, `/regime`, `/ai`, `/rl`, `/help`, etc.
- **Windows encoding** — `sys.stdout.reconfigure(encoding="utf-8")` required in test scripts. daemon.py uses UTF-8 emoji throughout.
- **State file** — `paper_positions.json` is the single source of truth. Protected by `threading.Lock()` (`state_lock`).

---

## Trading Rules

| Parameter | Value |
|-----------|-------|
| Risk per trade | 1.5% of capital (₹2,250) |
| Max daily loss | 4% of capital (₹6,000) |
| Max positions | 8 simultaneous |
| MIS leverage | 5x (Upstox) |
| Max daily trades per symbol | 3 (prevents loop disasters) |
| Neural Markov threshold | P(Win) ≥ 78% |

### Markets Traded

| Market | Instruments | Hours (IST) |
|--------|------------|-------------|
| NSE Equity | 40 fast-scan from 181 universe | 09:15 – 15:15 |
| NSE Currency | USDINR, GBPINR, EURINR, JPYINR | 09:00 – 16:45 |
| MCX Commodity | CrudeOilMini, GoldMini, SilverMicro, CopperMini, NatGasMini | 09:00 – 23:15 |

---

## AI Systems

### Layer 1: Gaussian HMM Regime Filter (`neural_markov.py`)
- 3-state Hidden Markov Model: BULL_MOMENTUM, BEAR_EXPANSION, CHOP_CONSOLIDATION
- Blocks ALL trades when P(Chop) ≥ 65%
- 0.045ms inference latency

### Layer 2: MLP Target-Hit Probability (`neural_markov.py`)
- 18-feature → 32 → 16 → 1 sigmoid network (pure Python)
- Outputs P(Win) — trades rejected below 78%
- **KNOWN ISSUE**: Miscalibrated for equity INTRADAY (approves 0% WR trades)

### Layer 3: PPO RL Execution Optimizer (`rl_optimizer.py`)
- 22-dim state → 64 → 32 → 3 continuous action MLP
- Outputs: risk_scaling [0.5, 1.5], exit_preference [0, 1], trail_tightness [0.3, 0.7]
- Trained on 175 trades. Currently ~flat output (~0.97x scaling on everything)
- 0.49ms inference latency

---

## Performance (183 trades, Aug 31 – Sep 7, 2026)

| Metric | Value |
|--------|-------|
| Gross P&L | ₹-950 (nearly breakeven!) |
| Charges | ₹12,401 (93% of total loss) |
| **Net P&L** | **₹-13,351** |
| Win Rate | 38.8% |
| Max Drawdown | ₹23,870 |

### Asset Class P&L
- **Currency: +₹14,803** (59.8% WR) ✅ — USDINR alone = +₹12,587
- Equity: -₹10,032 (4.2% WR) ❌
- Commodity: -₹18,122 (8.7% WR) ❌

### Strategy P&L
- **TREND_MOMENTUM: +₹12,233** (54.7% WR) ✅ — Only winning strategy
- INTRADAY: -₹2,452 (0% WR) ❌ — DISABLE
- US_SESSION_MOMENTUM: -₹1,044 (0% WR) ❌ — DISABLE
- GAP_FADE: -₹8,564 (14% WR) ❌ — DISABLE
- 3H_PATTERN_BREAKOUT: -₹14,671 (80% WR but COPPER killed it)

---

## Phase 4 — What Needs To Be Built Next

### Priority 1: Charges-Aware Trade Filter (CRITICAL)
Neither HMM, MLP, nor RL considers transaction costs. A trade with P(Win)=99% but expected_profit < charges is a guaranteed net loser. Add a filter AFTER Neural Markov gate:
```python
expected_profit = (target - entry) * qty * multiplier
estimated_charges = calculate_estimated_charges(symbol, asset_type, entry, target, qty)
if expected_profit < 2 * estimated_charges:
    REJECT  # "Insufficient edge after costs"
```

### Priority 2: Disable Dead Strategies
- Set `DISABLED_STRATEGIES = ['INTRADAY', 'US_SESSION_MOMENTUM', 'GAP_FADE']`
- In each scan method, skip if strategy is in disabled list
- This alone saves ~₹12,000/week in charge burn

### Priority 3: Daily Total Trade Cap
- Current cap is 3 per symbol per day. Need GLOBAL cap: max 8 trades total per day.
- After 8 trades, only manage existing positions (no new entries)

### Priority 4: Charges-Aware MLP Retraining
- Train MLP on "will net profit be positive?" not just "will price hit target?"
- Add charges estimate as 19th input feature to MLP

### Priority 5: Proper PPO Implementation
- Replace numerical gradient with clipped surrogate objective
- Add value function baseline + GAE advantage estimation
- Needs 500+ diverse trades for meaningful policy development

### Priority 6: Time-Based Entry Restrictions
- No equity entries before 10:00 (morning volatility traps)
- No equity entries after 15:30 (only exits)
- No commodity entries at MCX open (12:00)
- Post-market hours (17:00-20:00) need higher conviction threshold

---

## Code Conventions

- **Commit messages**: `feat(phaseN): description` or `fix(component): description`
- **No global mutable state** — Each thread gets its own HMM/NN instances
- **State access**: Always use `with self.state_lock:` around load_state/save_state
- **Telegram messages**: Use HTML formatting (`<b>`, `<code>`, emoji)
- **Position dict keys**: `id`, `symbol`, `direction`, `qty`, `lots`, `entry_price`, `stop_loss`, `target_price`, `target_1`, `target_2`, `asset_type`, `strategy`, `entry_time`, `status`, `rl_risk_scaling`, `rl_exit_preference`, `rl_trail_tightness`
- **Trade result keys**: All position keys + `exit_price`, `exit_time`, `gross_pnl`, `charges`, `net_pnl`, `pnl`, `charges_breakdown`, `result` (WIN/LOSS)

## Testing

- Test scripts go in the scratch directory or use unittest
- Always add `sys.stdout.reconfigure(encoding="utf-8")` for Windows
- Mock `save_state` in tests to avoid overwriting `paper_positions.json`
- Python executable: `research/.venv/Scripts/python.exe`

## Key Gotchas

1. **COPPER Standard lots** — Multiplier is 2500kg (₹19,543 loss from ONE trade). Always use Mini/Micro lots. The multiplier cap in `risk.py` should block these.
2. **JPYINR lot multiplier** — 100,000 per lot. Even tiny price moves = huge P&L.
3. **Sep 4 disaster** — NEWS_PANIC_EXIT fired 91 times causing a 53-trade JPYINR loop burning ₹10,454 in brokerage. News radar is decommissioned.
4. **`paper_positions.json` path** — It's at `research/src/paper_positions.json`, NOT `research/src/trading/paper_positions.json`.
5. **Non-atomic trade execution** — The scan→evaluate→save cycle is NOT fully atomic. Two threads can race on state. Low risk because threads scan different assets, but `execute_trade_atomically()` wrapper is still needed.
