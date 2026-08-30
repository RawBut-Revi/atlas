"""
Project Atlas — Autonomous Quantitative Trading Daemon
Runs continuously in background (on Android Termux, Laptop, or Cloud).
Scans 170+ NSE stocks every 3-5 minutes, auto-executes paper trades, manages risk, and sends Telegram alerts.
"""

import os
import sys
import time
import json
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading.universe import NSE_UNIVERSE, get_universe_symbols
from trading.strategy import generate_signal
from trading.backtest import fetch_historical_data
from trading.risk import RiskManager, MARKET_OPEN, MARKET_CLOSE, SQUARE_OFF
from trading.telegram_bot import TelegramNotifier
from trading.gap_strategy import scan_for_gaps
from trading.currency_strategy import (
    scan_all_currency_pairs, CURRENCY_PAIRS,
    CURRENCY_OPEN, CURRENCY_CLOSE, CURRENCY_SQUARE_OFF,
)

IST = pytz.timezone("Asia/Kolkata")
JOURNAL_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "paper_positions.json")


class TradingDaemon:
    def __init__(self, scan_interval_seconds: int = 180, mode: str = "PAPER"):
        self.scan_interval = scan_interval_seconds
        self.mode = mode
        self.risk_manager = RiskManager(capital=10000.0)
        self.notifier = TelegramNotifier()
        self.is_running = True
        self.last_scan_time = None
        self.daily_report_sent = False
        self.gap_scanned_today = False
        self.currency_square_off_done = False

    def load_state(self) -> dict:
        if os.path.exists(JOURNAL_FILE):
            try:
                with open(JOURNAL_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"open_positions": [], "trade_history": [], "capital": 10000.0, "total_pnl": 0.0}

    def save_state(self, state: dict):
        try:
            with open(JOURNAL_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            print(f"[Daemon] Error saving state: {e}")

    def handle_telegram_command(self, cmd: str) -> str:
        """Process incoming commands from mobile Telegram."""
        state = self.load_state()
        cmd = cmd.strip().lower()

        if cmd == "/status":
            now_ist = datetime.now(IST).strftime("%H:%M:%S IST")
            return (
                f"🤖 <b>ATLAS BOT STATUS</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"⏱️ <b>Time:</b> {now_ist}\n"
                f"🟢 <b>Status:</b> RUNNING ({self.mode} MODE)\n"
                f"📊 <b>Equities:</b> {len(NSE_UNIVERSE)} NSE Stocks\n"
                f"💱 <b>Currency:</b> {len(CURRENCY_PAIRS)} FX Pairs (USD/EUR/GBP/JPY)\n"
                f"⚡ <b>Gap Scanner:</b> {'Done' if self.gap_scanned_today else 'Pending (runs at 9:15 AM)'}\n"
                f"💼 <b>Capital:</b> INR {state.get('capital', 10000):,.2f}\n"
                f"⚡ <b>Open Positions:</b> {len(state.get('open_positions', []))}\n"
                f"💰 <b>Today P&L:</b> INR {state.get('total_pnl', 0.0):.2f}"
            )

        elif cmd == "/positions":
            open_pos = state.get("open_positions", [])
            if not open_pos:
                return "No open positions currently."
            
            lines = ["⚡ <b>ACTIVE POSITIONS</b>\n━━━━━━━━━━━━━━━━━━━"]
            for p in open_pos:
                asset_type = p.get("asset_type", "EQUITY")
                tag = "💱" if asset_type == "CURRENCY" else "📊"
                qty_label = f"Lots: {p.get('lots', p.get('qty'))}" if asset_type == "CURRENCY" else f"Qty: {p['qty']}"
                lines.append(
                    f"{tag} <b>{p['symbol']}</b> ({p['direction']}) | {qty_label}\n"
                    f"  Entry: {p['entry_price']} | Target: {p['target_price']} | SL: {p['stop_loss']}"
                )
            return "\n".join(lines)

        elif cmd == "/pnl":
            history = state.get("trade_history", [])
            closed_today = [t for t in history if t.get("exit_time", "").startswith(datetime.now().strftime("%Y-%m-%d"))]
            wins = len([t for t in closed_today if t.get("pnl", 0) > 0])
            losses = len([t for t in closed_today if t.get("pnl", 0) <= 0])
            total_trades = len(closed_today)
            wr = round(wins / max(total_trades, 1) * 100, 1)
            pnl = state.get("total_pnl", 0.0)

            return (
                f"📈 <b>P&L PERFORMANCE</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"🔢 <b>Trades Today:</b> {total_trades}\n"
                f"✅ Wins: {wins} | ❌ Losses: {losses}\n"
                f"🎯 Win Rate: {wr}%\n"
                f"💰 Total Realized P&L: <b>INR {pnl:+.2f}</b>"
            )

        elif cmd == "/scan":
            self.run_scan_cycle()
            return "🔍 Scan completed! Check signals/positions."

        elif cmd == "/gaps":
            gaps = scan_for_gaps()
            if not gaps:
                return "No significant gap openings detected today."
            lines = ["⚡ <b>GAP OPENINGS DETECTED</b>\n━━━━━━━━━━━━━━━━━━━"]
            for g in gaps[:8]:
                icon = "🟢" if g["direction"] == "BUY" else "🔴"
                lines.append(
                    f"{icon} <b>{g['symbol']}</b> {g['gap_type']} {g['gap_pct']:+.1f}%\n"
                    f"  {g['strategy']} | {g['direction']} | Conf: {g['confidence']}%"
                )
            return "\n".join(lines)

        elif cmd == "/currency":
            from trading.currency_strategy import get_all_currency_telemetry
            telemetry = get_all_currency_telemetry()
            if not telemetry:
                return "Unable to fetch currency data. Check internet connection."

            lines = ["💱 <b>CURRENCY FUTURES (NSE CDS)</b>\n━━━━━━━━━━━━━━━━━━━"]
            for s in telemetry:
                if s["direction"] == "BUY":
                    badge = f"🟢 <b>BUY ({s['strategy']})</b>"
                elif s["direction"] == "SELL":
                    badge = f"🔴 <b>SELL ({s['strategy']})</b>"
                else:
                    badge = "⚪ <b>NEUTRAL (Consolidating)</b>"

                lines.append(
                    f"📈 <b>{s['symbol']}</b>: ₹{s['entry_price']:.4f} ({s['trend']})\n"
                    f"  {badge}\n"
                    f"  🎯 Target: ₹{s['target_price']:.4f} | 🛑 SL: ₹{s['stop_loss']:.4f}\n"
                    f"  📦 Lots: {s['lots']} | 🛡️ Risk: ₹{s['risk_inr']:.0f} | 🎯 Conf: {s['confidence']}%\n"
                    f"  💡 <i>{s['rationale']}</i>\n"
                )
            return "\n".join(lines)

        return "Commands: /status, /positions, /pnl, /scan, /gaps, /currency"

    def scan_universe(self) -> list[dict]:
        """Scans all stocks in universe in parallel and finds high-confidence setups."""
        today = datetime.now().strftime("%Y-%m-%d")
        from_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

        def evaluate_stock(symbol: str) -> dict | None:
            try:
                df = fetch_historical_data(symbol, from_date, today)
                if df is not None and len(df) >= 30:
                    sig = generate_signal(symbol, df)
                    if sig.direction != "NONE" and sig.confidence >= 75:
                        sig_dict = sig.to_dict()
                        sl_dist = abs(sig.entry_price - sig.stop_loss)
                        qty = self.risk_manager.calculate_qty(sig.entry_price, sig.stop_loss) if sl_dist > 0 else 10
                        sig_dict["suggested_qty"] = max(1, qty)
                        return sig_dict
            except Exception:
                pass
            return None

        signals = []
        symbols = get_universe_symbols()
        with ThreadPoolExecutor(max_workers=16) as executor:
            future_to_sym = {executor.submit(evaluate_stock, sym): sym for sym in symbols}
            for future in as_completed(future_to_sym):
                res = future.result()
                if res:
                    signals.append(res)

        return signals

    def manage_open_positions(self, state: dict):
        """Monitors open positions against current market prices for SL and TP."""
        open_pos = state.get("open_positions", [])
        if not open_pos:
            return

        today = datetime.now().strftime("%Y-%m-%d")
        from_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        remaining = []

        for p in open_pos:
            try:
                df = fetch_historical_data(p["symbol"], from_date, today)
                if df is None or len(df) == 0:
                    remaining.append(p)
                    continue

                curr_bar = df.iloc[-1]
                high = curr_bar["high"]
                low = curr_bar["low"]
                close = curr_bar["close"]

                hit_tp = False
                hit_sl = False
                exit_price = close

                if p["direction"] == "BUY":
                    if high >= p["target_price"]:
                        hit_tp = True
                        exit_price = p["target_price"]
                    elif low <= p["stop_loss"]:
                        hit_sl = True
                        exit_price = p["stop_loss"]
                else:  # SELL
                    if low <= p["target_price"]:
                        hit_tp = True
                        exit_price = p["target_price"]
                    elif high >= p["stop_loss"]:
                        hit_sl = True
                        exit_price = p["stop_loss"]

                if hit_tp or hit_sl:
                    if p["direction"] == "BUY":
                        realized = (exit_price - p["entry_price"]) * p["qty"]
                    else:
                        realized = (p["entry_price"] - exit_price) * p["qty"]

                    closed = {
                        **p,
                        "exit_price": round(exit_price, 2),
                        "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "pnl": round(realized, 2),
                        "result": "WIN" if realized > 0 else "LOSS",
                        "status": "CLOSED",
                    }

                    state["trade_history"].insert(0, closed)
                    state["total_pnl"] = round(state.get("total_pnl", 0.0) + realized, 2)
                    self.notifier.notify_trade_closed(closed)
                    print(f"[Daemon] Position Closed: {p['symbol']} P&L: ₹{realized:.2f}")
                else:
                    remaining.append(p)

            except Exception as e:
                remaining.append(p)

        state["open_positions"] = remaining
        self.save_state(state)

    def square_off_all(self, state: dict):
        """Force-closes all remaining open positions at 3:15 PM IST."""
        open_pos = state.get("open_positions", [])
        if not open_pos:
            return

        print("[Daemon] 3:15 PM IST: Squaring off all open intraday positions...")
        today = datetime.now().strftime("%Y-%m-%d")
        from_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        for p in open_pos:
            exit_price = p["entry_price"]
            try:
                df = fetch_historical_data(p["symbol"], from_date, today)
                if df is not None and len(df) > 0:
                    exit_price = df.iloc[-1]["close"]
            except Exception:
                pass

            if p["direction"] == "BUY":
                realized = (exit_price - p["entry_price"]) * p["qty"]
            else:
                realized = (p["entry_price"] - exit_price) * p["qty"]

            closed = {
                **p,
                "exit_price": round(exit_price, 2),
                "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "pnl": round(realized, 2),
                "result": "WIN" if realized > 0 else "LOSS",
                "status": "SQUARE_OFF",
            }
            state["trade_history"].insert(0, closed)
            state["total_pnl"] = round(state.get("total_pnl", 0.0) + realized, 2)
            self.notifier.notify_trade_closed(closed)

        state["open_positions"] = []
        self.save_state(state)

    def run_scan_cycle(self):
        """Runs one full scan and execution cycle."""
        state = self.load_state()
        print(f"\n[Daemon] [{datetime.now().strftime('%H:%M:%S')}] Scanning universe ({len(NSE_UNIVERSE)} stocks)...")

        # 1. Manage existing positions first
        self.manage_open_positions(state)

        # 2. Check risk limits
        can_trade, reason = self.risk_manager.can_trade()
        if not can_trade:
            print(f"[Daemon] Trade entry paused: {reason}")
            return

        # 3. Check max simultaneous positions
        if len(state.get("open_positions", [])) >= 3:
            print("[Daemon] Max simultaneous positions (3) reached. Monitoring existing.")
            return

        # 4. Scan for new setups
        signals = self.scan_universe()
        print(f"[Daemon] Found {len(signals)} high-confidence setups.")

        # 5. Take top 1-2 setups
        for sig in signals[:2]:
            # Don't duplicate open symbols
            if any(p["symbol"] == sig["symbol"] for p in state.get("open_positions", [])):
                continue

            self.notifier.notify_signal_found(sig)

            # Auto-execute paper trade
            pos_id = f"pos_{int(time.time()*1000)}"
            new_pos = {
                "id": pos_id,
                "symbol": sig["symbol"],
                "direction": sig["direction"],
                "qty": sig.get("suggested_qty", 10),
                "entry_price": sig["entry_price"],
                "stop_loss": sig["stop_loss"],
                "target_price": sig["target_price"],
                "mode": self.mode,
                "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "status": "OPEN",
            }

            state.setdefault("open_positions", []).append(new_pos)
            self.save_state(state)
            self.notifier.notify_trade_executed(new_pos)
            print(f"[Daemon] Executed {self.mode} {sig['direction']} {new_pos['qty']}x {sig['symbol']} @ ₹{sig['entry_price']}")

            if len(state.get("open_positions", [])) >= 3:
                break

    def run_gap_scan(self):
        """Runs gap opening scan at 9:15 AM — once per day."""
        if self.gap_scanned_today:
            return

        print(f"\n[Daemon] [{datetime.now().strftime('%H:%M:%S')}] Running Early Market GAP Scanner...")
        state = self.load_state()

        gap_signals = scan_for_gaps()
        if gap_signals:
            print(f"[Daemon] Found {len(gap_signals)} gap openings!")
            for g in gap_signals[:3]:  # Take top 3 gaps
                if any(p["symbol"] == g["symbol"] for p in state.get("open_positions", [])):
                    continue
                if len(state.get("open_positions", [])) >= 5:
                    break

                # Notify on Telegram
                self.notifier.notify_signal_found(g)

                # Auto-execute paper trade
                pos_id = f"gap_{int(time.time()*1000)}"
                new_pos = {
                    "id": pos_id,
                    "symbol": g["symbol"],
                    "direction": g["direction"],
                    "qty": 10,  # Default qty for gaps
                    "entry_price": g["entry_price"],
                    "stop_loss": g["stop_loss"],
                    "target_price": g["target_price"],
                    "mode": self.mode,
                    "asset_type": "EQUITY",
                    "strategy": g["strategy"],
                    "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "OPEN",
                }
                state.setdefault("open_positions", []).append(new_pos)
                self.save_state(state)
                self.notifier.notify_trade_executed(new_pos)
                print(f"[Daemon] GAP {g['strategy']}: {g['direction']} {g['symbol']} | Gap: {g['gap_pct']:+.1f}%")
        else:
            print("[Daemon] No significant gap openings detected today.")

        self.gap_scanned_today = True

    def run_currency_scan(self):
        """Scans currency futures for trading setups."""
        state = self.load_state()
        print(f"\n[Daemon] [{datetime.now().strftime('%H:%M:%S')}] Scanning Currency Futures (4 FX pairs)...")

        # Check max positions
        currency_positions = [p for p in state.get("open_positions", []) if p.get("asset_type") == "CURRENCY"]
        if len(currency_positions) >= 2:
            print("[Daemon] Max currency positions (2) reached. Monitoring.")
            return

        currency_signals = scan_all_currency_pairs()
        if currency_signals:
            print(f"[Daemon] Found {len(currency_signals)} currency setups!")
            for sig in currency_signals[:1]:  # Take top 1 currency signal
                if any(p["symbol"] == sig["symbol"] for p in state.get("open_positions", [])):
                    continue

                self.notifier.notify_signal_found(sig)

                pos_id = f"fx_{int(time.time()*1000)}"
                new_pos = {
                    "id": pos_id,
                    "symbol": sig["symbol"],
                    "direction": sig["direction"],
                    "lots": sig["lots"],
                    "qty": sig["lots"],
                    "entry_price": sig["entry_price"],
                    "stop_loss": sig["stop_loss"],
                    "target_price": sig["target_price"],
                    "mode": self.mode,
                    "asset_type": "CURRENCY",
                    "strategy": sig["strategy"],
                    "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "OPEN",
                }
                state.setdefault("open_positions", []).append(new_pos)
                self.save_state(state)
                self.notifier.notify_trade_executed(new_pos)
                print(f"[Daemon] CURRENCY: {sig['direction']} {sig['lots']} lot(s) {sig['symbol']} @ {sig['entry_price']:.4f}")
        else:
            print("[Daemon] No actionable currency setups.")

    def run(self):
        """Main daemon loop — Extended hours: 9:00 AM to 5:00 PM IST."""
        print("==================================================")
        print("   PROJECT ATLAS — AUTONOMOUS TRADING DAEMON v2   ")
        print(f"   Equities: {len(NSE_UNIVERSE)} Stocks | FX: {len(CURRENCY_PAIRS)} Pairs")
        print(f"   Mode: {self.mode} | Scan Every: {self.scan_interval}s")
        print(f"   Telegram: {'ACTIVE' if self.notifier.is_enabled else 'NOT CONFIGURED'}")
        print("   Schedule:")
        print("     09:00-09:15  Currency Pre-Market Scan")
        print("     09:15-09:30  Gap Opening Detection")
        print("     09:15-15:15  Equity Intraday Scans")
        print("     09:00-16:45  Currency Trading Window")
        print("     15:25        Equity Auto Square-Off")
        print("     16:45        Currency Auto Square-Off")
        print("==================================================")

        from datetime import time as dt_time
        WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        heartbeat_counter = 0

        while self.is_running:
            try:
                # Always poll Telegram commands
                self.notifier.check_incoming_commands(self.handle_telegram_command)

                now_ist = datetime.now(IST)
                now_time = now_ist.time()
                weekday = now_ist.weekday()
                is_weekday = weekday < 5
                time_str = now_ist.strftime("%H:%M:%S")
                heartbeat_counter += 1

                if not is_weekday:
                    # Weekend
                    if heartbeat_counter % 6 == 1:  # Print every ~60s
                        days_to_monday = (7 - weekday) % 7
                        if days_to_monday == 0:
                            days_to_monday = 7
                        print(f"[{time_str}] Market closed (Weekend - {WEEKDAYS[weekday]}). Next session: Monday 09:00 AM IST. Listening for Telegram commands...")
                    time.sleep(10)
                    continue

                # ─── Before Market: 00:00 – 9:00 AM ──────────────────
                if now_time < CURRENCY_OPEN:
                    if heartbeat_counter % 6 == 1:
                        print(f"[{time_str}] Waiting for market open at 09:00 AM IST. Listening for Telegram commands...")
                    time.sleep(10)
                    continue

                # ─── 9:00 AM – 9:15 AM: Currency Pre-Market Scan ─────
                elif CURRENCY_OPEN <= now_time < MARKET_OPEN:
                    self.daily_report_sent = False
                    self.gap_scanned_today = False
                    self.currency_square_off_done = False
                    print(f"\n[{time_str}] >> CURRENCY PRE-MARKET WINDOW (09:00-09:15)")
                    self.run_currency_scan()
                    next_phase = "Gap Opening Detection at 09:15"
                    print(f"[{time_str}] Next: {next_phase}. Sleeping {self.scan_interval}s...")
                    time.sleep(self.scan_interval)

                # ─── 9:15 AM – 9:30 AM: Gap Opening + First Equity Scan ──
                elif MARKET_OPEN <= now_time < dt_time(9, 30):
                    print(f"\n[{time_str}] >> GAP OPENING + EQUITY SCAN WINDOW (09:15-09:30)")
                    self.run_gap_scan()
                    self.run_scan_cycle()
                    print(f"[{time_str}] Next scan in {self.scan_interval}s...")
                    time.sleep(self.scan_interval)

                # ─── 9:30 AM – 3:15 PM: Regular Equity + Currency Scans ──
                elif dt_time(9, 30) <= now_time <= MARKET_CLOSE:
                    state = self.load_state()
                    open_count = len(state.get("open_positions", []))
                    print(f"\n[{time_str}] >> INTRADAY SCAN (Equities + Currency) | Open Positions: {open_count}")
                    self.run_scan_cycle()
                    if CURRENCY_OPEN <= now_time <= CURRENCY_CLOSE:
                        self.run_currency_scan()
                    print(f"[{time_str}] Next scan in {self.scan_interval}s...")
                    time.sleep(self.scan_interval)

                # ─── 3:15 PM – 3:25 PM: Equity Square-Off Window ─────
                elif MARKET_CLOSE < now_time <= SQUARE_OFF:
                    print(f"\n[{time_str}] >> EQUITY SQUARE-OFF WINDOW (15:15-15:25)")
                    state = self.load_state()
                    equity_positions = [p for p in state.get("open_positions", []) if p.get("asset_type", "EQUITY") == "EQUITY"]
                    if equity_positions:
                        print(f"[{time_str}] Squaring off {len(equity_positions)} EQUITY position(s)...")
                        remaining = [p for p in state.get("open_positions", []) if p.get("asset_type") == "CURRENCY"]
                        for p in equity_positions:
                            self._close_position(state, p, "SQUARE_OFF")
                        state["open_positions"] = remaining
                        self.save_state(state)
                    else:
                        print(f"[{time_str}] No equity positions to square off.")
                    print(f"[{time_str}] Currency trading continues until 16:45...")
                    time.sleep(60)

                # ─── 3:25 PM – 4:45 PM: Currency-Only Trading Window ─
                elif SQUARE_OFF < now_time <= CURRENCY_SQUARE_OFF:
                    if not self.currency_square_off_done:
                        print(f"\n[{time_str}] >> CURRENCY-ONLY WINDOW (15:25-16:45)")
                        self.run_currency_scan()
                        state = self.load_state()
                        self.manage_open_positions(state)
                        print(f"[{time_str}] Next currency scan in {self.scan_interval}s...")
                    time.sleep(self.scan_interval)

                # ─── 4:45 PM – 5:00 PM: Currency Square-Off & Daily Report ──
                elif CURRENCY_SQUARE_OFF < now_time <= CURRENCY_CLOSE and not self.daily_report_sent:
                    print(f"\n[{time_str}] >> END OF DAY — FINAL SQUARE-OFF & REPORT")
                    state = self.load_state()
                    if state.get("open_positions"):
                        print(f"[{time_str}] Squaring off {len(state['open_positions'])} remaining position(s)...")
                        self.square_off_all(state)
                    self.currency_square_off_done = True

                    summary = self.risk_manager.get_daily_summary()
                    summary["total_pnl"] = state.get("total_pnl", 0.0)
                    self.notifier.notify_daily_summary(summary)
                    self.daily_report_sent = True
                    print(f"[{time_str}] Daily session concluded. P&L summary sent to Telegram.")
                    print(f"[{time_str}] Bot will resume tomorrow at 09:00 AM IST.")
                    time.sleep(60)

                else:
                    # After market close (5:00 PM+)
                    if heartbeat_counter % 6 == 1:
                        print(f"[{time_str}] Market closed for today. Next session: Tomorrow 09:00 AM IST. Listening for Telegram commands...")
                    time.sleep(10)

            except KeyboardInterrupt:
                print("\n[Daemon] Stopping trading daemon...")
                self.is_running = False
                break
            except Exception as e:
                print(f"[Daemon] Loop exception: {e}")
                time.sleep(10)

    def _close_position(self, state: dict, p: dict, status: str = "CLOSED"):
        """Closes a single position and records P&L."""
        exit_price = p["entry_price"]  # Default fallback
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            from_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            if p.get("asset_type") == "CURRENCY":
                from trading.currency_strategy import fetch_currency_data
                candles = fetch_currency_data(p["symbol"], days=7)
                if candles:
                    exit_price = candles[-1]["close"]
            else:
                df = fetch_historical_data(p["symbol"], from_date, today)
                if df is not None and len(df) > 0:
                    if isinstance(df, list):
                        exit_price = df[-1]["close"]
                    else:
                        exit_price = df.iloc[-1]["close"]
        except Exception:
            pass

        qty = p.get("lots", p.get("qty", 1))
        if p["direction"] == "BUY":
            realized = (exit_price - p["entry_price"]) * qty
        else:
            realized = (p["entry_price"] - exit_price) * qty

        closed = {
            **p,
            "exit_price": round(exit_price, 4),
            "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "pnl": round(realized, 2),
            "result": "WIN" if realized > 0 else "LOSS",
            "status": status,
        }
        state.setdefault("trade_history", []).insert(0, closed)
        state["total_pnl"] = round(state.get("total_pnl", 0.0) + realized, 2)
        self.notifier.notify_trade_closed(closed)


if __name__ == "__main__":
    daemon = TradingDaemon(scan_interval_seconds=180, mode="PAPER")
    daemon.run()
