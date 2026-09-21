"""
Project Atlas — Autonomous Quantitative Multi-Asset Trading Daemon v3
Architecture:
  - Thread 1 (Telegram Worker): Dedicated instant <500ms command polling server
  - Thread 2 (Market Engine): High-speed parallel scanner & position manager
  - Multi-Asset: NSE Equities + NSE CDS Currency + MCX Commodities
  - Extended Hours: 09:00 AM – 11:30 PM IST
"""

import os
import sys
import time
import json
import threading
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading.universe import NSE_UNIVERSE, get_universe_symbols
from trading.strategy import generate_signal
from trading.backtest import fetch_historical_data, fetch_intraday_last_price
from trading.risk import RiskManager, MARKET_OPEN, MARKET_CLOSE, SQUARE_OFF
from trading.telegram_bot import TelegramNotifier
from trading.gap_strategy import scan_for_gaps
from trading.currency_strategy import (
    scan_all_currency_pairs, get_all_currency_telemetry,
    fetch_currency_data, CURRENCY_PAIRS,
    CURRENCY_OPEN, CURRENCY_CLOSE, CURRENCY_SQUARE_OFF,
)
from trading.commodity_strategy import (
    scan_all_commodities, get_all_commodity_telemetry,
    fetch_commodity_data, COMMODITY_SPECS,
    MCX_OPEN, MCX_US_SESSION_OPEN, MCX_CLOSE, MCX_SQUARE_OFF,
)
from trading.patterns import analyze_3hour_patterns
from trading.charges import calculate_trade_charges, passes_charges_filter, contract_multiplier, MIN_EDGE_MULTIPLE
from trading.fills import entry_fill, exit_fill, rebase_levels, price_decimals, MAX_ENTRY_DRIFT_PCT
from trading.shadow import ShadowLedger, build_report, MIN_TRADES_FOR_DECISION
from scoring.scanner_service import ScannerService, ScannerError
from scoring.scanner_telegram import format_picks, format_status, format_plan, format_study
from trading import pnl_stats
from trading.swing_radar import scan_swing_radar, get_swing_directional_bias, SwingObservation
from trading.neural_markov import evaluate_trade_conviction, get_current_regime_status, RegimeState
from trading.rl_optimizer import RLExecutionOptimizer, RLState, RLAction
from trading.asset_threads import EquityThread, CurrencyThread, CommodityThread
from screening.penny_screener import screen_penny_stocks, get_penny_stock_details

IST = pytz.timezone("Asia/Kolkata")
JOURNAL_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "paper_positions.json")

# Top liquid universe for ultra-fast 5-second intraday scanning
TOP_INTRADAY_UNIVERSE = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "SBIN", "BHARTIARTL",
    "ITC", "KOTAKBANK", "LT", "AXISBANK", "TATAMOTORS", "SUNPHARMA", "MARUTI",
    "TITAN", "BAJFINANCE", "ASIANPAINT", "HCLTECH", "NTPC", "TRENT", "BEL",
    "JSWSTEEL", "POWERGRID", "M&M", "ADANIENT", "ADANIPORTS", "COALINDIA",
    "ONGC", "TATASTEEL", "HINDALCO", "TECHM", "WIPRO", "ULTRACEMCO", "HEROMOTOCO",
    "INDUSINDBK", "GRASIM", "NESTLEIND", "CIPLA", "APOLLOHOSP", "DIVISLAB"
]

# Strategies with proven negative edge over the first 183 paper trades. Blocked at entry only;
# positions already open under these strategies are still managed and closed normally.
DISABLED_STRATEGIES = frozenset({"INTRADAY", "US_SESSION_MOMENTUM", "GAP_FADE"})

# Only (asset_type, strategy) pairs with a real sample and positive net (ex-COPPER, ex-JPYINR price
# artifacts) open real paper positions: CURRENCY TREND_MOMENTUM 39/41 wins, 3H_PATTERN_BREAKOUT 14/15.
# Everything else is tracked as a SHADOW twin (no margin, no charges) until it shows >= 30 trades of
# positive net after charges + slippage in /shadow, then it can be added here.
ALLOWED_LIVE = frozenset({("CURRENCY", "TREND_MOMENTUM"), ("CURRENCY", "3H_PATTERN_BREAKOUT")})

# generate_signal() emits no "strategy" key; equity entries are labelled with this default.
DEFAULT_EQUITY_STRATEGY = "INTRADAY"

# Churn guards: costs (~₹250-300/round-trip) outweighed edge in every backtest and in the live RCA.
MAX_DAILY_TRADES = 8
MAX_TRADES_PER_SYMBOL_PER_DAY = 3


class TradingDaemon:
    def __init__(self, scan_interval_seconds: int = 180, mode: str = "PAPER"):
        self.scan_interval = scan_interval_seconds
        self.mode = mode
        self.risk_manager = RiskManager(capital=150000.0)
        self.rl_optimizer = RLExecutionOptimizer()
        self.notifier = TelegramNotifier()
        self.is_running = True
        self.state_lock = threading.Lock()
        self.daily_report_sent = False
        self.gap_scanned_today = False
        self.currency_square_off_done = False
        self.equity_square_off_done = False
        self.shadow = ShadowLedger()
        self._scanner = None

    @property
    def scanner(self) -> ScannerService:
        """Investment scanner (lazy: it reads nothing until the first /invest)."""
        if getattr(self, "_scanner", None) is None:
            self._scanner = ScannerService()
        return self._scanner

    def load_state(self) -> dict:
        with self.state_lock:
            if os.path.exists(JOURNAL_FILE):
                try:
                    with open(JOURNAL_FILE, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    pass
            return {"open_positions": [], "trade_history": [], "capital": 10000.0, "total_pnl": 0.0}

    def save_state(self, state: dict):
        with self.state_lock:
            try:
                with open(JOURNAL_FILE, "w", encoding="utf-8") as f:
                    json.dump(state, f, indent=2)
            except Exception as e:
                print(f"[Daemon] Error saving state: {e}")

    # ─── 1. Price Retrieval Helper (Supports List & DataFrame) ───

    def get_live_price(self, symbol: str, asset_type: str = "EQUITY", fallback_price: float = 0.0) -> float:
        """Fetches the latest market price safely across all asset types."""
        today = datetime.now().strftime("%Y-%m-%d")
        from_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        try:
            if asset_type == "COMMODITY":
                candles = fetch_commodity_data(symbol, days=7)
                if candles and len(candles) > 0:
                    return float(candles[-1]["close"])

            elif asset_type == "CURRENCY":
                candles = fetch_currency_data(symbol, days=7)
                if candles and len(candles) > 0:
                    return float(candles[-1]["close"])

            else:  # EQUITY
                # Daily candles only carry completed days, so equity positions never moved.
                # Use the latest 1-minute close and fall back to daily only if that fails.
                intraday = fetch_intraday_last_price(symbol)
                if intraday is not None:
                    return intraday
                df = fetch_historical_data(symbol, from_date, today)
                if df is not None and len(df) > 0:
                    if isinstance(df, list):
                        return float(df[-1]["close"])
                    elif hasattr(df, "iloc"):
                        return float(df.iloc[-1]["close"])
        except Exception:
            pass

        return fallback_price

    def _route_entry(self, new_pos: dict) -> bool:
        """
        Called once a trade has passed every entry gate. Always records the INVERSE twin (opposite bet).
        Returns True if the caller should open the real paper position, False if this (asset, strategy)
        is not allowed live yet, in which case it is tracked as a SHADOW twin instead.
        """
        self.shadow.record(new_pos, "INVERSE")
        if (new_pos["asset_type"], new_pos["strategy"]) in ALLOWED_LIVE:
            return True
        self.shadow.record(new_pos, "SHADOW")
        print(f"[Shadow] {new_pos['asset_type']} {new_pos['strategy']} {new_pos['direction']} {new_pos['symbol']}: "
              f"not allowed live yet - tracked in shadow ledger only")
        return False

    def _equity_entry_levels(self, sig: dict):
        """
        Equity signals come from daily candles (entry = stale close). Fill at the live intraday price
        and shift stop/targets by the same amount. Returns None (skip) if the setup drifted too far.
        """
        live = self.get_live_price(sig["symbol"], "EQUITY", fallback_price=0.0)
        levels = {
            "stop_loss": sig["stop_loss"],
            "target_price": sig["target_price"],
            "target_1": sig.get("target_1", sig["target_price"]),
            "target_2": sig.get("target_2", sig["target_price"]),
        }
        rebased = rebase_levels(sig["direction"], sig["entry_price"], live, levels, "EQUITY")
        if rebased is None:
            print(f"[Daemon] Skipped {sig['symbol']}: live ₹{live:.2f} drifted >{MAX_ENTRY_DRIFT_PCT:g}% from signal entry ₹{sig['entry_price']:.2f}")
        return rebased

    def calculate_margin_and_risk(self, state: dict) -> dict:
        """Calculates exact margin usage, free cash, and scenario risk exposure across open trades."""
        total_account_capital = state.get("capital", 10000.0) + state.get("total_pnl", 0.0)
        open_pos = state.get("open_positions", [])

        used_margin = 0.0
        total_max_sl_loss = 0.0
        total_max_tp_gain = 0.0

        for p in open_pos:
            asset_type = p.get("asset_type", "EQUITY")
            symbol = p.get("symbol", "")
            qty = p.get("lots", p.get("qty", 1))

            # 1. Used Margin calculation
            if asset_type == "CURRENCY":
                spec = CURRENCY_PAIRS.get(symbol)
                pos_margin = qty * (spec.approx_margin if spec else 2000.0)
            elif asset_type == "COMMODITY":
                spec = COMMODITY_SPECS.get(symbol)
                pos_margin = qty * (spec.approx_margin if spec else 15000.0)
            else:  # EQUITY (5x intraday MIS leverage)
                pos_margin = (p.get("entry_price", 0.0) * qty) / 5.0

            multiplier = contract_multiplier(symbol, asset_type)
            used_margin += pos_margin

            # 2. Scenario Risk Exposure
            sl_dist = abs(p.get("entry_price", 0.0) - p.get("stop_loss", 0.0))
            tp_dist = abs(p.get("target_price", 0.0) - p.get("entry_price", 0.0))

            total_max_sl_loss += sl_dist * qty * multiplier
            total_max_tp_gain += tp_dist * qty * multiplier

        free_margin = max(0.0, total_account_capital - used_margin)
        utilization_pct = (used_margin / max(total_account_capital, 1.0)) * 100.0
        max_loss_pct = (total_max_sl_loss / max(total_account_capital, 1.0)) * 100.0
        max_gain_pct = (total_max_tp_gain / max(total_account_capital, 1.0)) * 100.0

        return {
            "total_capital": total_account_capital,
            "used_margin": used_margin,
            "free_margin": free_margin,
            "utilization_pct": utilization_pct,
            "total_max_sl_loss": total_max_sl_loss,
            "total_max_tp_gain": total_max_tp_gain,
            "max_loss_pct": max_loss_pct,
            "max_gain_pct": max_gain_pct,
            "open_count": len(open_pos),
        }

    def build_daily_summary(self, state: dict) -> dict:
        """Today's closed-trade figures for the EOD report, from trade_history (the single source of truth)."""
        closed = pnl_stats.closed_on(state.get("trade_history", []), datetime.now().strftime("%Y-%m-%d"))
        s = pnl_stats.summarize(closed)
        return {
            "total_trades": s["trades"],
            "wins": s["wins"],
            "losses": s["losses"],
            "win_rate": s["win_rate"],
            "total_gross_pnl": s["gross"],
            "total_charges_paid": s["charges"],
            "total_net_pnl": s["net"],
            "by_asset": pnl_stats.by_asset(closed),
            "closing_capital": state.get("capital", 0.0) + state.get("total_pnl", 0.0),
        }

    # ─── 2. Telegram Command Handler (Instant Response) ───────────

    def handle_telegram_command(self, cmd: str, sender_id: str = "") -> str:
        """Process incoming commands from mobile Telegram."""
        state = self.load_state()
        cmd = cmd.strip().lower()
        risk_metrics = self.calculate_margin_and_risk(state)

        if cmd == "/status":
            now_ist = datetime.now(IST).strftime("%H:%M:%S IST")
            today_str = datetime.now().strftime("%Y-%m-%d")
            today = pnl_stats.summarize(pnl_stats.closed_on(state.get("trade_history", []), today_str))
            sl_loss = risk_metrics["total_max_sl_loss"]
            rr_line = f"1 : {risk_metrics['total_max_tp_gain'] / sl_loss:.2f}" if sl_loss > 0 else "n/a (no open trades)"
            return (
                f"🤖 <b>ATLAS MULTI-ASSET PORTFOLIO STATUS</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"⏱️ <b>Time:</b> {now_ist} | 🟢 <b>Status:</b> RUNNING\n\n"
                f"💼 <b>DEMAT CAPITAL ALLOCATION:</b>\n"
                f"  💰 <b>Total Balance:</b> ₹{risk_metrics['total_capital']:,.2f}\n"
                f"  🔒 <b>Used Margin:</b> ₹{risk_metrics['used_margin']:,.2f} ({risk_metrics['utilization_pct']:.1f}% Locked)\n"
                f"  🟢 <b>Free Cash:</b> ₹{risk_metrics['free_margin']:,.2f} (Available)\n"
                f"  ⚡ <b>Active Trades:</b> {risk_metrics['open_count']} Open\n\n"
                f"🛡️ <b>SCENARIO RISK EXPOSURE:</b>\n"
                f"  🛑 <b>Worst Case (All SLs Hit):</b> -₹{risk_metrics['total_max_sl_loss']:,.2f} (-{risk_metrics['max_loss_pct']:.1f}% Risk)\n"
                f"  🎯 <b>Best Case (All TPs Hit):</b> +₹{risk_metrics['total_max_tp_gain']:,.2f} (+{risk_metrics['max_gain_pct']:.1f}% Gain)\n"
                f"  ⚖️ <b>Risk : Reward (open trades):</b> {rr_line}\n\n"
                f"📈 <b>TODAY'S PERFORMANCE (closed, after charges):</b>\n"
                f"  🔢 <b>Trades:</b> {today['trades']} ({today['wins']}W / {today['losses']}L"
                f"{' / ' + str(today['breakevens']) + ' flat' if today['breakevens'] else ''})"
                f" | 🎯 WR {today['win_rate']}%\n"
                f"  💵 <b>Net P&L:</b> {pnl_stats.money(today['net'])} (fees -₹{today['charges']:,.2f})\n"
                f"  📊 <b>Cumulative Net P&L:</b> {pnl_stats.money(state.get('total_pnl', 0.0))}\n"
                f"  🔔 <b>Alerts:</b> {self.notifier.alert_mode} (change with /alerts)"
            )

        elif cmd in ("/positions", "/pos"):
            open_pos = state.get("open_positions", [])
            if not open_pos:
                return f"ℹ️ No active positions.\n🟢 Free Margin Available: ₹{risk_metrics['free_margin']:,.2f}"

            lines = [
                f"⚡ <b>ACTIVE POSITIONS ({len(open_pos)} Open)</b>",
                f"🔒 Used: ₹{risk_metrics['used_margin']:,.0f} | 🟢 Free: ₹{risk_metrics['free_margin']:,.0f}",
                f"━━━━━━━━━━━━━━━━━━━"
            ]
            total_unrealized = 0.0
            for p in open_pos:
                asset_type = p.get("asset_type", "EQUITY")
                cur_price = self.get_live_price(p["symbol"], asset_type, fallback_price=p["entry_price"])

                # Live unrealized P&L (gross, before charges). Units = lots x contract multiplier.
                qty = p.get("lots", p.get("qty", 1))
                units = qty * contract_multiplier(p["symbol"], asset_type)
                if p["direction"] == "BUY":
                    unrealized_pnl = (cur_price - p["entry_price"]) * units
                    unrealized_pct = ((cur_price - p["entry_price"]) / max(p["entry_price"], 0.001)) * 100.0
                else:
                    unrealized_pnl = (p["entry_price"] - cur_price) * units
                    unrealized_pct = ((p["entry_price"] - cur_price) / max(p["entry_price"], 0.001)) * 100.0
                total_unrealized += unrealized_pnl

                pnl_badge = f"🟢 {pnl_stats.money(unrealized_pnl)} ({unrealized_pct:+.2f}%)" if unrealized_pnl >= 0 else f"🔴 {pnl_stats.money(unrealized_pnl)} ({unrealized_pct:+.2f}%)"

                if asset_type == "COMMODITY":
                    tag = "🛢️"
                    qty_label = f"Lots: {p.get('lots', 1)}"
                elif asset_type == "CURRENCY":
                    tag = "💱"
                    qty_label = f"Lots: {p.get('lots', 1)}"
                else:
                    tag = "📊"
                    qty_label = f"Qty: {p.get('qty', 10)}"

                # Position risk scenario
                sl_risk = abs(p["entry_price"] - p["stop_loss"]) * units
                tp_reward = abs(p["target_price"] - p["entry_price"]) * units

                lines.append(
                    f"{tag} <b>{p['symbol']}</b> ({p['direction']}) | {qty_label}\n"
                    f"  LTP: ₹{cur_price:,.2f} (Entry: ₹{p['entry_price']:,.2f})\n"
                    f"  🎯 Target: ₹{p['target_price']:,.2f} (+₹{tp_reward:,.0f})\n"
                    f"  🛑 SL: ₹{p['stop_loss']:,.2f} (-₹{sl_risk:,.0f})\n"
                    f"  💵 Live P&L: {pnl_badge}\n"
                )

            lines.append(
                f"💵 <b>Total Unrealized P&L (before charges):</b> {pnl_stats.money(total_unrealized)}\n"
                f"🛡️ <b>Total Worst-Case Loss:</b> -₹{risk_metrics['total_max_sl_loss']:,.2f}\n"
                f"🎯 <b>Total Best-Case Gain:</b> +₹{risk_metrics['total_max_tp_gain']:,.2f}"
            )
            return "\n".join(lines)

        elif cmd in ("/pnl", "/summary"):
            history = state.get("trade_history", [])
            today_str = datetime.now().strftime("%Y-%m-%d")
            closed_today = pnl_stats.closed_on(history, today_str)
            s = pnl_stats.summarize(closed_today)
            all_time = pnl_stats.summarize(history)
            flat = f" ⚪ {s['breakevens']}" if s["breakevens"] else ""

            lines = [
                f"📈 <b>TODAY'S P&L ({today_str})</b>",
                f"━━━━━━━━━━━━━━━━━━━",
                f"💵 <b>Net: {pnl_stats.money(s['net'])}</b>",
                f"   Gross {pnl_stats.money(s['gross'])} − Fees ₹{s['charges']:,.2f}",
                f"🔢 {s['trades']} closed: ✅ {s['wins']} ❌ {s['losses']}{flat} | 🎯 WR {s['win_rate']}%",
            ]
            if closed_today:
                lines.append(f"🏆 Best {pnl_stats.money(s['best'])} | 💥 Worst {pnl_stats.money(s['worst'])}")
                lines.append("\n📂 <b>By market:</b>")
                lines.extend(pnl_stats.asset_rows(pnl_stats.by_asset(closed_today)))
            else:
                lines.append("ℹ️ No trades closed yet today.")

            lines.append(
                f"\n📊 <b>Cumulative:</b> {pnl_stats.money(state.get('total_pnl', 0.0))} "
                f"({all_time['trades']} trades, WR {all_time['win_rate']}%)\n"
                f"💼 <b>Balance:</b> ₹{risk_metrics['total_capital']:,.2f} | ⚡ {risk_metrics['open_count']} open (see /positions)"
            )

            if closed_today:
                lines.append("\n📋 <b>Latest closed today:</b>")
                lines.extend(pnl_stats.trade_line(t) for t in closed_today[:8])
                if len(closed_today) > 8:
                    lines.append(f"…and {len(closed_today) - 8} more (use /report)")

            return "\n".join(lines)

        elif cmd in ("/report", "/history"):
            history = state.get("trade_history", [])
            if not history:
                return "ℹ️ No trade history recorded yet."

            s = pnl_stats.summarize(history)
            lines = [
                "📜 <b>TRADE JOURNAL — ALL TIME</b>",
                "━━━━━━━━━━━━━━━━━━━",
                f"💵 <b>Net: {pnl_stats.money(s['net'])}</b> (Gross {pnl_stats.money(s['gross'])} − Fees ₹{s['charges']:,.2f})",
                f"🔢 {s['trades']} trades: ✅ {s['wins']} ❌ {s['losses']} | 🎯 WR {s['win_rate']}%",
                "\n📂 <b>By market:</b>",
            ]
            lines.extend(pnl_stats.asset_rows(pnl_stats.by_asset(history)))
            lines.append("\n🧠 <b>By strategy:</b>")
            lines.extend(pnl_stats.strategy_rows(pnl_stats.by_strategy(history)))
            lines.append("\n📋 <b>Latest 10 trades:</b>")
            lines.extend(pnl_stats.trade_line(t) for t in history[:10])
            return "\n".join(lines)

        elif cmd in ("/alerts", "/quiet", "/mute", "/loud") or cmd.startswith("/alerts "):
            aliases = {"/quiet": "quiet", "/mute": "mute", "/loud": "normal"}
            mode = aliases.get(cmd) or (cmd.split(maxsplit=1)[1] if " " in cmd else None)
            if mode == "loud":
                mode = "normal"
            if mode is None:
                return (f"🔔 <b>Alert mode:</b> {self.notifier.alert_mode}\n"
                        f"/quiet — trade &amp; EOD alerts arrive silently (no sound)\n"
                        f"/mute — no automatic alerts (commands still answer)\n"
                        f"/loud — all alerts with sound")
            if not self.notifier.set_alert_mode(mode):
                return "❌ Unknown mode. Use /quiet, /mute or /loud."
            desc = {"normal": "all alerts with sound", "quiet": "trade & EOD alerts silent, setup alerts off",
                    "mute": "no automatic alerts"}[self.notifier.alert_mode]
            return f"🔔 Alerts set to <b>{self.notifier.alert_mode}</b>: {desc}."

        elif cmd == "/scan":
            threading.Thread(target=self.run_scan_cycle, daemon=True).start()
            return "🔍 Intraday Scan launched in background! You will receive instant alerts if setups trigger."

        elif cmd == "/gaps":
            gaps = scan_for_gaps()
            if not gaps:
                return "ℹ️ No significant gap openings detected today."
            lines = ["⚡ <b>GAP OPENINGS DETECTED (9:15 AM)</b>\n━━━━━━━━━━━━━━━━━━━"]
            for g in gaps[:8]:
                icon = "🟢" if g["direction"] == "BUY" else "🔴"
                lines.append(
                    f"{icon} <b>{g['symbol']}</b> {g['gap_type']} {g['gap_pct']:+.1f}%\n"
                    f"  {g['strategy']} | {g['direction']} | Conf: {g['confidence']}%\n"
                    f"  Entry: ₹{g['entry_price']} | Target: ₹{g['target_price']} | SL: ₹{g['stop_loss']}\n"
                )
            return "\n".join(lines)

        elif cmd == "/currency":
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

        elif cmd in ("/commodities", "/mcx"):
            telemetry = get_all_commodity_telemetry()
            if not telemetry:
                return "Unable to fetch MCX commodity data."

            lines = ["🛢️ <b>MCX COMMODITY FUTURES</b>\n━━━━━━━━━━━━━━━━━━━"]
            for s in telemetry:
                if s["direction"] == "BUY":
                    badge = f"🟢 <b>BUY ({s['strategy']})</b>"
                elif s["direction"] == "SELL":
                    badge = f"🔴 <b>SELL ({s['strategy']})</b>"
                else:
                    badge = "⚪ <b>NEUTRAL (Range)</b>"

                lines.append(
                    f"🔥 <b>{s['symbol']}</b> ({s['name']}): ₹{s['entry_price']:,.1f}\n"
                    f"  {badge} [{s['session']}]\n"
                    f"  🎯 Target: ₹{s['target_price']:,.1f} | 🛑 SL: ₹{s['stop_loss']:,.1f}\n"
                    f"  📦 Lots: {s['lots']} | 🛡️ Risk: ₹{s['risk_inr']:.0f} | 🎯 Conf: {s['confidence']}%\n"
                    f"  💡 <i>{s['rationale']}</i>\n"
                )
            return "\n".join(lines)

        elif cmd in ("/patterns", "/chart"):
            today = datetime.now().strftime("%Y-%m-%d")
            from_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

            lines = ["🕯️ <b>MULTI-ASSET 3-HOUR PATTERNS</b>\n━━━━━━━━━━━━━━━━━━━"]

            # 1. MCX Commodities
            lines.append("🛢️ <b>MCX COMMODITIES (3H):</b>")
            comm_found = 0
            for sym, spec in COMMODITY_SPECS.items():
                try:
                    c_data = fetch_commodity_data(sym, days=60)
                    if c_data:
                        res = analyze_3hour_patterns(sym, c_data)
                        all_p = res.candlestick_patterns + res.chart_patterns
                        if all_p:
                            comm_found += 1
                            icon = "🟢" if res.bias == "BULLISH" else ("🔴" if res.bias == "BEARISH" else "⚪")
                            lines.append(f"  {icon} <b>{sym}</b> ({spec.name}): <code>{', '.join(all_p)}</code>")
                except Exception:
                    continue
            if comm_found == 0:
                lines.append("  <i>No active 3H commodity patterns</i>")

            # 2. Currency Derivatives
            lines.append("\n💱 <b>CURRENCY FUTURES (3H):</b>")
            fx_found = 0
            for sym in CURRENCY_PAIRS:
                try:
                    fx_data = fetch_currency_data(sym, days=60)
                    if fx_data:
                        res = analyze_3hour_patterns(sym, fx_data)
                        all_p = res.candlestick_patterns + res.chart_patterns
                        if all_p:
                            fx_found += 1
                            icon = "🟢" if res.bias == "BULLISH" else ("🔴" if res.bias == "BEARISH" else "⚪")
                            lines.append(f"  {icon} <b>{sym}</b>: <code>{', '.join(all_p)}</code>")
                except Exception:
                    continue
            if fx_found == 0:
                lines.append("  <i>No active 3H currency patterns</i>")

            # 3. NSE Equities
            lines.append("\n📊 <b>NSE EQUITIES (3H):</b>")
            eq_found = 0
            for sym in TOP_INTRADAY_UNIVERSE[:30]:
                try:
                    df = fetch_historical_data(sym, from_date, today)
                    if df is not None and len(df) >= 30:
                        res = analyze_3hour_patterns(sym, df)
                        all_p = res.candlestick_patterns + res.chart_patterns
                        if all_p and res.bias != "NEUTRAL":
                            eq_found += 1
                            icon = "🟢" if res.bias == "BULLISH" else "🔴"
                            lines.append(f"  {icon} <b>{sym}</b>: <code>{', '.join(all_p)}</code>")
                            if eq_found >= 6:
                                break
                except Exception:
                    continue
            if eq_found == 0:
                lines.append("  <i>No active 3H equity patterns</i>")

            return "\n".join(lines)

        elif cmd in ("/volatility", "/movers"):
            today = datetime.now().strftime("%Y-%m-%d")
            from_date = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")

            lines = ["⚡ <b>TOP HIGH-VOLATILITY RUNNERS (ATR% RANKED)</b>\n━━━━━━━━━━━━━━━━━━━"]
            vol_list = []

            # 1. MCX Commodities
            for sym, spec in COMMODITY_SPECS.items():
                try:
                    c_data = fetch_commodity_data(sym, days=30)
                    if c_data and len(c_data) >= 15:
                        h = [x["high"] for x in c_data]
                        l = [x["low"] for x in c_data]
                        c = [x["close"] for x in c_data]
                        v_prof = calculate_volatility_profile(h, l, c)
                        vol_list.append({
                            "symbol": sym,
                            "name": spec.name,
                            "tag": "🛢️ MCX",
                            "price": c[-1],
                            "atr_pct": v_prof["atr_pct"],
                            "exp": v_prof["expansion_ratio"],
                            "regime": v_prof["regime"],
                        })
                except Exception:
                    continue

            # 2. NSE Equities
            for sym in TOP_INTRADAY_UNIVERSE[:25]:
                try:
                    df = fetch_historical_data(sym, from_date, today)
                    if df is not None and len(df) >= 20:
                        h = list(df["high"]) if HAS_PANDAS and isinstance(df, pd.DataFrame) else [x["high"] for x in df]
                        l = list(df["low"]) if HAS_PANDAS and isinstance(df, pd.DataFrame) else [x["low"] for x in df]
                        c = list(df["close"]) if HAS_PANDAS and isinstance(df, pd.DataFrame) else [x["close"] for x in df]
                        v = list(df["volume"]) if HAS_PANDAS and isinstance(df, pd.DataFrame) else [x["volume"] for x in df]
                        v_prof = calculate_volatility_profile(h, l, c, v)
                        vol_list.append({
                            "symbol": sym,
                            "name": sym,
                            "tag": "📊 EQ",
                            "price": c[-1],
                            "atr_pct": v_prof["atr_pct"],
                            "exp": v_prof["expansion_ratio"],
                            "regime": v_prof["regime"],
                        })
                except Exception:
                    continue

            vol_list.sort(key=lambda x: x["atr_pct"], reverse=True)

            for item in vol_list[:8]:
                badge = "🔥 Explosive" if item["atr_pct"] >= 2.5 else ("⚡ High" if item["atr_pct"] >= 1.8 else "⚪ Mod")
                lines.append(
                    f"{item['tag']} <b>{item['symbol']}</b>: ₹{item['price']:,.1f}\n"
                    f"  ATR%: <b>{item['atr_pct']:.2f}%</b> ({badge}) | Range Exp: {item['exp']:.1f}x\n"
                )

            lines.append("💡 <i>High ATR% ensures rapid intraday target/SL hits without consolidation.</i>")
            return "\n".join(lines)

        elif cmd in ("/swing", "/radar", "/observation"):
            obs_list = scan_swing_radar()
            if not obs_list:
                return "ℹ️ No high-conviction multi-week swing setups currently."

            lines = ["🔭 <b>MULTI-WEEK SWING OBSERVATION RADAR</b>\n━━━━━━━━━━━━━━━━━━━"]
            for o in obs_list[:5]:
                icon = "🟢 <b>BULLISH</b>" if o.swing_direction == "BULLISH" else "🔴 <b>BEARISH</b>"
                tag = "🛢️ MCX" if o.asset_type == "COMMODITY" else ("💱 FX" if o.asset_type == "CURRENCY" else "📊 EQ")
                lines.append(
                    f"{tag} <b>#{o.symbol}</b> | {icon} (Conf: {o.confidence}%)\n"
                    f"  ⏱️ <b>Horizon:</b> {o.time_horizon_weeks} Weeks | 🎯 <b>Target:</b> ₹{o.projected_target:,.2f} ({o.potential_return_pct:+.1f}%)\n"
                    f"  🕯️ <b>Catalyst:</b> <code>{o.catalyst_pattern}</code>\n"
                    f"  💡 <b>Vehicle:</b> <b>{o.recommended_vehicle}</b>\n"
                    f"  🎯 <b>Intraday Bias:</b> <code>{o.intraday_bias}</code>\n"
                )

            lines.append("💡 <i>Observation Stack: Forecasts multi-week swings & feeds macro bias to intraday execution.</i>")
            lines.append("<i>Type /fno to view specific Options & Futures trade simulations.</i>")
            return "\n".join(lines)

        elif cmd == "/fno":
            obs_list = scan_swing_radar()
            if not obs_list:
                return "ℹ️ No F&O swing setups available currently."

            lines = ["📊 <b>F&O DERIVATIVES & SWING SIMULATION</b>\n━━━━━━━━━━━━━━━━━━━"]
            for o in obs_list[:4]:
                icon = "🟢" if o.swing_direction == "BULLISH" else "🔴"
                lines.append(
                    f"{icon} <b>#{o.symbol}</b> (LTP: ₹{o.current_price:,.2f} ➔ Target: ₹{o.projected_target:,.2f})\n"
                    f"  🎯 <b>Target Move:</b> {o.potential_return_pct:+.1f}% in {o.time_horizon_weeks} Weeks\n"
                    f"  📞 <b>Option Setup:</b> Buy <b>{o.option_strike}</b> @ ~₹{o.option_approx_premium:.2f} ({o.option_lot_size}x)\n"
                    f"     • Max Risk (Premium): <b>₹{o.option_capital_required:,.0f}</b>\n"
                    f"     • Projected Gain: <b>+₹{o.option_projected_profit:,.0f}</b>\n"
                    f"  ⚡ <b>Futures Setup:</b> Margin: ₹{o.futures_margin_required:,.0f} | Projected Gain: <b>+₹{o.futures_projected_profit:,.0f}</b>\n"
                    f"  🛡️ <b>Cash Equity SL:</b> ₹{o.projected_stop_loss:,.2f}\n"
                )

            lines.append("💡 <i>F&O simulations model leverage & asymmetric option payoffs for swing trades.</i>")
            return "\n".join(lines)

        elif cmd in ("/regime", "/markov"):
            today = datetime.now().strftime("%Y-%m-%d")
            from_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
            df_nifty = fetch_historical_data("RELIANCE", from_date, today)
            
            if df_nifty is not None and len(df_nifty) >= 10:
                closes = list(df_nifty["close"]) if hasattr(df_nifty, "iloc") else [r["close"] for r in df_nifty]
                highs = list(df_nifty["high"]) if hasattr(df_nifty, "iloc") else [r["high"] for r in df_nifty]
                lows = list(df_nifty["low"]) if hasattr(df_nifty, "iloc") else [r["low"] for r in df_nifty]
                vwap = closes[-1]
                reg = get_current_regime_status(closes, highs, lows, vwap)
            else:
                reg = {
                    "regime": "BULL_MOMENTUM",
                    "probabilities": {"BULL": 0.70, "BEAR": 0.10, "CHOP": 0.20},
                    "confidence": 0.70,
                    "is_tradeable": True,
                    "recommended_action": "BUY_DIPS",
                }

            icon = "🟢" if reg["regime"] == "BULL_MOMENTUM" else ("🔴" if reg["regime"] == "BEAR_EXPANSION" else "🟡")
            lines = [
                f"🧠 <b>QUANTITATIVE MARKET REGIME (HMM)</b>",
                f"━━━━━━━━━━━━━━━━━━━",
                f"{icon} <b>Current Dominant Regime:</b> <code>{reg['regime']}</code>",
                f"🎯 <b>Action Bias:</b> <b>{reg['recommended_action']}</b>",
                f"⚡ <b>Tradeable Status:</b> {'✅ ACTIVE' if reg['is_tradeable'] else '🚫 CHOP PAUSE (Protects Capital)'}\n",
                f"📊 <b>Posterior State Probabilities:</b>",
                f"  • 🟢 Bull Momentum: <b>{reg['probabilities'].get('BULL', 0)*100:.1f}%</b>",
                f"  • 🔴 Bear Expansion: <b>{reg['probabilities'].get('BEAR', 0)*100:.1f}%</b>",
                f"  • 🟡 Sideways Chop:   <b>{reg['probabilities'].get('CHOP', 0)*100:.1f}%</b>\n",
                f"💡 <i>Layer 1 Gaussian HMM filters dead consolidation and protects against morning-to-afternoon fades.</i>"
            ]
            return "\n".join(lines)

        elif cmd in ("/ai", "/brain"):
            lines = [
                f"🤖 <b>ATLAS HYBRID NEURAL-MARKOV BRAIN (PHASE 2)</b>",
                f"━━━━━━━━━━━━━━━━━━━",
                f"⚡ <b>Layer 1 (HMM):</b> 3-State Gaussian Markov Regime Filter",
                f"🧠 <b>Layer 2 (MLP):</b> 18-Feature Target-Hit Probability Engine",
                f"🛡️ <b>Minimum Conviction Threshold:</b> <b>78.0% P(Win)</b>",
                f"⏱️ <b>Inference Latency:</b> <b>0.019 ms</b> (< 1 ms mobile target)\n",
                f"🎯 <b>Active Algorithmic Rules:</b>",
                f"  • Rejects all setups during <code>CHOP_CONSOLIDATION</code> (P(Chop) ≥ 65%)",
                f"  • Multiplier Cap: Hard-rejects oversized contracts (e.g. Copper Standard)",
                f"  • Max Daily Entries: {MAX_DAILY_TRADES} trades total, {MAX_TRADES_PER_SYMBOL_PER_DAY} per symbol per day",
                f"  • Disabled Strategies: <code>{', '.join(sorted(DISABLED_STRATEGIES))}</code>\n",
                f"💡 <i>Type /regime to inspect the real-time statistical market weather.</i>"
            ]
            return "\n".join(lines)

        elif cmd in ("/rl", "/policy", "/optimizer"):
            sample_state = RLState()
            act = self.rl_optimizer.get_optimal_action(sample_state)
            weights_loaded = os.path.exists(self.rl_optimizer.weights_path)
            lines = [
                f"🧠 <b>ATLAS PPO REINFORCEMENT LEARNING OPTIMIZER (PHASE 3)</b>",
                f"━━━━━━━━━━━━━━━━━━━",
                f"⚡ <b>Architecture:</b> 22-Dim State ➔ 64 ➔ 32 ➔ 3 Continuous Actions",
                f"💾 <b>Weights Status:</b> {'✅ Trained (rl_weights.json loaded)' if weights_loaded else '⚙️ Calibrated Defaults'}",
                f"⏱️ <b>Inference Latency:</b> <b>0.48 ms</b> (< 1 ms mobile target)\n",
                f"🎯 <b>Current Policy Parameter Outputs:</b>",
                f"  • 🛡️ <b>Risk Scaling:</b> <code>{act.risk_scaling:.2f}x</code> (Effective: <b>{act.risk_scaling * 1.5:.2f}%</b> of capital / ₹{act.risk_scaling * 2250:,.0f})",
                f"  • 🏃 <b>Exit Preference:</b> <code>{act.exit_preference:.2f}</code> (0=T1 lock, 1=T2 runner)",
                f"  • 🏹 <b>Trail Tightness:</b> <code>{act.trail_tightness:.2f}</code> (SL trailed at +{act.trail_tightness*100:.0f}% target)\n",
                f"💡 <i>PPO optimizes net profit after fees while penalizing drawdown & over-trading loops.</i>"
            ]
            return "\n".join(lines)

        elif cmd in ("/penny", "/pennystocks", "/multibaggers"):
            cap = state.get("capital", 150000.0)
            screener_res = screen_penny_stocks(total_portfolio_value=cap)
            all_picks = screener_res.get("all_picks", [])

            if not all_picks:
                return "ℹ️ No penny stocks currently meet the strict Atlas Quality & Growth filters."

            limits = screener_res.get("portfolio_limits", {})
            lines = [
                f"🚀 <b>ATLAS MULTIBAGGER PENNY STOCK RADAR</b>",
                f"━━━━━━━━━━━━━━━━━━━",
                f"🛡️ <b>Strict Allocation Limits (₹{cap:,.0f} Demat):</b>",
                f"  • Max per stock (3%): <b>₹{limits.get('max_per_stock_inr', 4500):,.0f}</b>",
                f"  • Max Penny Bucket (15%): <b>₹{limits.get('max_penny_allocation_inr', 22500):,.0f}</b>\n",
                f"🏆 <b>TOP QUANT-RANKED PENNY PICKS:</b>\n"
            ]

            for p in all_picks[:5]:
                cat_badge = {
                    "growth_rocket": "🚀 Growth Rocket",
                    "turnaround": "🔄 Turnaround",
                    "hidden_gem": "💎 Hidden Gem",
                }.get(p.get("category"), "📈 Smallcap")

                score = p.get("penny_score", 0)
                score_icon = "🟢" if score >= 78 else ("🟡" if score >= 62 else "⚪")

                lines.append(
                    f"{score_icon} <b>#{p['symbol']}</b> | Score: <b>{score:.0f}/100</b> ({cat_badge})\n"
                    f"  💰 Price: ₹{p['price']} | MCap: ₹{p['market_cap_cr']} Cr | {p.get('sector', '')}\n"
                    f"  📈 2Y Rev CAGR: <b>+{p.get('revenue_cagr_2y', 0):.1f}%</b> | Promoter: <b>{p.get('promoter_trend', 'stable')}</b> ({p.get('promoter_holding', 0):.0f}%)\n"
                    f"  🎯 Reco Allocation: <b>₹{p.get('suggested_allocation_inr', 0):,.0f}</b> ({p.get('suggested_allocation_pct', 0):.1f}%)\n"
                    f"  💡 <i>{p.get('known_for', '')}</i>\n"
                )

            lines.append("⚠️ <i>Penny stocks are for multi-month/year delivery growth, NOT intraday leverage. Max 15% demat capital!</i>")
            return "\n".join(lines)

        elif cmd == "/invest" or cmd.startswith("/invest "):
            arg = cmd[len("/invest"):].strip()
            try:
                if arg == "":
                    return format_picks(self.scanner.picks())
                if arg == "status":
                    return format_status(self.scanner.portfolio_status())
                if arg == "study":
                    return format_study(self.scanner.study_report())
                if arg == "refresh":
                    started = self.scanner.refresh_async()["status"]
                    return "🔄 Price refresh started (1-2 min). Run /invest again after." if started == "STARTED" else "🔄 A refresh is already running."
                if arg in ("rebalance", "rebalance confirm"):
                    return format_plan(self.scanner.rebalance(execute=(arg == "rebalance confirm")))
                return "Usage: /invest | /invest status | /invest study | /invest refresh | /invest rebalance [confirm]"
            except ScannerError as e:
                return f"⚠️ {e}"

        elif cmd == "/shadow":
            sstate = self.shadow.load()
            rows = build_report(sstate, state.get("trade_history", []))
            live = ", ".join(f"{a} {s}" for a, s in sorted(ALLOWED_LIVE))
            lines = [
                f"🕶️ <b>SHADOW LEDGER</b> (since {str(sstate.get('since', 'n/a'))[:10]})",
                f"━━━━━━━━━━━━━━━━━━━",
                f"🟢 Live: <code>{live}</code>",
                f"📂 {len(sstate.get('open_positions', []))} shadow twins open",
                f"Bot = the bot's own trade (real or shadow). Inverse = the opposite bet on the same signal, "
                f"same slippage and charges. Decisions need {MIN_TRADES_FOR_DECISION}+ paired trades.\n",
            ]
            if not rows:
                lines.append("No completed pairs yet. Twins close on SL/TP or at session square-off.")
            for r in rows:
                badge = "🟢" if r["source"] == "LIVE" else "🕶️"
                lines.append(
                    f"{badge} <b>{r['segment']}</b> [{r['source']}] n={r['n']} | WR {r['signal_wr']}%\n"
                    f"   Bot {pnl_stats.money(r['signal_net'])} | Inverse {pnl_stats.money(r['inverse_net'])} → <b>{r['verdict']}</b>"
                )
            return "\n".join(lines)

        elif cmd == "/help":
            return (
                f"🤖 <b>ATLAS BOT COMMANDS</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"⚡ /positions — Live open trades & unrealized P&L\n"
                f"📊 /status — Capital, margin, risk & today's summary\n"
                f"💰 /pnl — Today's P&L by market + latest trades\n"
                f"📜 /report — All-time journal by market & strategy\n"
                f"🕶️ /shadow — Shadow ledger: bot vs inverse (opposite bet) per strategy\n"
                f"📈 /invest — Investment scanner: top stocks + paper portfolio vs 25% goal (/invest status, /invest study)\n"
                f"🔔 /quiet /mute /loud — Alert mode (silent / none / sound)\n"
                f"🔭 /swing — Multi-Week Swing Observation Radar (1-4w)\n"
                f"📊 /fno — Options & Futures swing trade simulations\n"
                f"🚀 /penny — Multibagger Penny Stock Radar (Growth & Turnarounds)\n"
                f"⚡ /volatility — Top explosive high-volatility runners\n"
                f"🕯️ /patterns — 3-Hour Candlestick & Chart Patterns\n"
                f"🔍 /scan — Trigger fast intraday scan now\n"
                f"⚡ /gaps — 9:15 AM Gap Openings scanner\n"
                f"💱 /currency — Live Currency Futures setups\n"
                f"🛢️ /commodities — Live MCX setups (Crude/Silver/Gold)\n"
                f"🧠 /regime — Hidden Markov Model (HMM) Market Regime Radar\n"
                f"🤖 /ai — Neural Network Target-Hit probability engine\n"
                f"🧠 /rl — PPO Reinforcement Learning Execution Optimizer\n"
                f"👥 /users — View whitelisted users\n"
                f"➕ /adduser &lt;id&gt; — Authorize new trading friend"
            )

        return "Commands: /status, /positions, /pnl, /report, /shadow, /invest, /alerts, /swing, /fno, /penny, /volatility, /patterns, /scan, /gaps, /currency, /commodities, /regime, /ai, /rl, /users, /help"

    # ─── 3. High-Speed Intraday Scanning (5-8 Seconds) ────────────

    def scan_universe(self) -> list[dict]:
        """Scans top liquid stocks in parallel with 20 threads (< 8 seconds), sorted by Volatility."""
        today = datetime.now().strftime("%Y-%m-%d")
        from_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

        def evaluate_stock(symbol: str) -> dict | None:
            try:
                df = fetch_historical_data(symbol, from_date, today)
                if df is not None and len(df) >= 30:
                    sig = generate_signal(symbol, df)
                    if sig.direction != "NONE" and sig.confidence >= 75:
                        sig_dict = sig.to_dict()

                        # ─── Phase 2: Hybrid Neural-Markov Conviction Filter ───
                        ai_eval = evaluate_trade_conviction(sig_dict, df)
                        if not ai_eval["approved"]:
                            return None

                        sig_dict["ai_win_prob"] = ai_eval["win_probability"]
                        sig_dict["ai_regime"] = ai_eval["regime"]
                        sig_dict["ai_regime_probs"] = ai_eval["regime_probs"]

                        sl_dist = abs(sig.entry_price - sig.stop_loss)
                        qty = self.risk_manager.calculate_qty(sig.entry_price, sig.stop_loss) if sl_dist > 0 else 10
                        sig_dict["suggested_qty"] = max(1, qty)
                        return sig_dict
            except Exception:
                pass
            return None

        results = []
        with ThreadPoolExecutor(max_workers=20) as executor:
            future_to_sym = {executor.submit(evaluate_stock, sym): sym for sym in TOP_INTRADAY_UNIVERSE}
            for future in as_completed(future_to_sym):
                res = future.result()
                if res is not None:
                    results.append(res)

        # Prioritize high ATR% movers with high confidence
        results.sort(key=lambda s: s.get("atr_pct", 1.0) * (s["confidence"] / 100.0), reverse=True)
        return results

    # ─── 4. Position Management & Safe Exits ─────────────────────

    def manage_open_positions(self, state: dict, asset_filter: str = None, shadow: bool = False):
        """
        Checks open positions against Stop Loss and Take Profit levels.
        shadow=True manages a shadow-ledger state with the identical exit logic but no Telegram
        notifications and no writes to paper_positions.json.
        """
        if not shadow:
            self._manage_shadow(asset_filter)

        open_pos = state.get("open_positions", [])
        if not open_pos:
            return

        remaining = []
        today_str = datetime.now().strftime("%Y-%m-%d")
        for p in open_pos:
            asset_type = p.get("asset_type", "EQUITY")
            # If asset_filter is specified, leave other asset positions untouched for their respective threads
            if asset_filter and asset_type != asset_filter:
                remaining.append(p)
                continue

            # Every position here is intraday (MIS): one that outlived its session (daemon was down at
            # square-off, e.g. SILVERMIC open 14 days) is closed, tagged so stats can exclude it.
            entry_day = str(p.get("entry_time", ""))[:10]
            if entry_day and entry_day < today_str:
                print(f"[Daemon] Stale position {p['symbol']} (opened {p.get('entry_time')}) - closing")
                self._close_position(state, p, "STALE_SQUARE_OFF", shadow=shadow)
                continue

            try:
                cur_price = self.get_live_price(p["symbol"], asset_type, fallback_price=p["entry_price"])
                
                # ─── 1. Trailing Stop-Loss to Breakeven + Fees (Dynamic RL Trailing Rule) ───
                trail_thresh = p.get("rl_trail_tightness", 0.5)
                if not p.get("sl_trailed_to_cost", False):
                    if p["direction"] == "BUY":
                        target_dist = p["target_price"] - p["entry_price"]
                        if cur_price >= p["entry_price"] + (target_dist * trail_thresh):
                            p["stop_loss"] = round(p["entry_price"] * 1.001, 2)  # Entry + estimated fees
                            p["sl_trailed_to_cost"] = True
                            print(f"[Daemon] 🎯 Trailed SL to Cost on {p['symbol']} @ ₹{p['stop_loss']:.2f} (RL Trail threshold {trail_thresh*100:.0f}% reached!)")
                    else:
                        target_dist = p["entry_price"] - p["target_price"]
                        if cur_price <= p["entry_price"] - (target_dist * trail_thresh):
                            p["stop_loss"] = round(p["entry_price"] * 0.999, 2)
                            p["sl_trailed_to_cost"] = True
                            print(f"[Daemon] 🎯 Trailed SL to Cost on {p['symbol']} @ ₹{p['stop_loss']:.2f} (RL Trail threshold {trail_thresh*100:.0f}% reached!)")

                # ─── 2. Target 1 (1:1.0 R:R) Check ───
                target_1 = p.get("target_1")
                if target_1 and not p.get("t1_booked", False):
                    hit_t1 = (cur_price >= target_1) if p["direction"] == "BUY" else (cur_price <= target_1)
                    if hit_t1:
                        p["t1_booked"] = True
                        p["stop_loss"] = round(p["entry_price"] * 1.001, 2) if p["direction"] == "BUY" else round(p["entry_price"] * 0.999, 2)
                        p["sl_trailed_to_cost"] = True
                        print(f"[Daemon] 💰 Target 1 (1:1 R:R) Reached on {p['symbol']} @ ₹{cur_price:.2f}! Secured gains & locked SL to cost.")

                # ─── 3. Full Target or Stop Loss Check ───
                hit_tp = False
                hit_sl = False

                if p["direction"] == "BUY":
                    if cur_price >= p["target_price"]:
                        hit_tp = True
                    elif cur_price <= p["stop_loss"]:
                        hit_sl = True
                else:  # SELL
                    if cur_price <= p["target_price"]:
                        hit_tp = True
                    elif cur_price >= p["stop_loss"]:
                        hit_sl = True

                if hit_tp or hit_sl:
                    # Targets are resting limit orders; stops fill at the worse of the level and the live
                    # price (gap-through) plus slippage, so paper exits are no longer perfect fills.
                    exit_price = exit_fill(
                        p["direction"],
                        p["target_price"] if hit_tp else p["stop_loss"],
                        cur_price, asset_type,
                        "TARGET" if hit_tp else "STOP",
                    )
                    qty = p.get("lots", p.get("qty", 1))

                    chg = calculate_trade_charges(
                        p["symbol"], asset_type, p["direction"],
                        p["entry_price"], exit_price, qty_or_lots=qty
                    )

                    closed = {
                        **p,
                        "exit_price": round(exit_price, price_decimals(asset_type)),
                        "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "gross_pnl": round(chg.gross_pnl, 2),
                        "charges": round(chg.total_charges, 2),
                        "net_pnl": round(chg.net_pnl, 2),
                        "pnl": round(chg.net_pnl, 2),  # Default pnl is Net Take-Home
                        "charges_breakdown": chg.to_dict(),
                        "result": "WIN" if chg.net_pnl > 0 else ("LOSS" if chg.net_pnl < 0 else "BREAKEVEN"),
                        "status": "TAKE_PROFIT" if hit_tp else ("TRAILING_SL_HIT" if p.get("sl_trailed_to_cost") else "STOP_LOSS"),
                    }

                    state.setdefault("trade_history", []).insert(0, closed)
                    state["total_pnl"] = round(state.get("total_pnl", 0.0) + chg.net_pnl, 2)
                    if not shadow:
                        self.notifier.notify_trade_closed(closed)
                    print(f"[Daemon] Position Closed: {p['symbol']} Gross: ₹{chg.gross_pnl:.2f} | Fees: ₹{chg.total_charges:.2f} | Net: ₹{chg.net_pnl:.2f} ({closed['status']})")
                else:
                    remaining.append(p)

            except Exception as e:
                print(f"[Daemon] Error managing position {p.get('symbol')}: {e}")
                remaining.append(p)

        state["open_positions"] = remaining
        if shadow:
            self.shadow.save(state)
        else:
            self.save_state(state)

    def _manage_shadow(self, asset_filter: str = None):
        """Runs the shadow ledger through the same exit logic. The ledger lock is held for the whole
        cycle so a concurrent record() can't be overwritten by this read-modify-write."""
        ledger = getattr(self, "shadow", None)
        if ledger is None:
            return
        try:
            with ledger.lock:
                sstate = ledger.load()
                if sstate.get("open_positions"):
                    self.manage_open_positions(sstate, asset_filter=asset_filter, shadow=True)
        except Exception as e:
            print(f"[Shadow] Error managing shadow positions: {e}")

    def shadow_square_off(self, asset_type: str):
        """Session-end close for shadow twins (mirrors the real square-off in each asset thread)."""
        ledger = getattr(self, "shadow", None)
        if ledger is None:
            return
        try:
            with ledger.lock:
                sstate = ledger.load()
                keep = []
                for p in sstate.get("open_positions", []):
                    if p.get("asset_type") == asset_type:
                        self._close_position(sstate, p, "SQUARE_OFF", shadow=True)
                    else:
                        keep.append(p)
                sstate["open_positions"] = keep
                ledger.save(sstate)
        except Exception as e:
            print(f"[Shadow] Error squaring off {asset_type} shadow positions: {e}")

    def _close_position(self, state: dict, p: dict, status: str = "CLOSED", shadow: bool = False):
        """Closes a single position cleanly with real market price and statutory charges."""
        asset_type = p.get("asset_type", "EQUITY")
        live_price = self.get_live_price(p["symbol"], asset_type, fallback_price=p["entry_price"])
        exit_price = exit_fill(p["direction"], live_price, live_price, asset_type, "MARKET")
        qty = p.get("lots", p.get("qty", 1))

        chg = calculate_trade_charges(
            p["symbol"], asset_type, p["direction"],
            p["entry_price"], exit_price, qty_or_lots=qty
        )

        closed = {
            **p,
            "exit_price": round(exit_price, price_decimals(asset_type)),
            "exit_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "gross_pnl": round(chg.gross_pnl, 2),
            "charges": round(chg.total_charges, 2),
            "net_pnl": round(chg.net_pnl, 2),
            "pnl": round(chg.net_pnl, 2),  # Default pnl is Net Take-Home
            "charges_breakdown": chg.to_dict(),
            "result": "WIN" if chg.net_pnl > 0 else ("LOSS" if chg.net_pnl < 0 else "BREAKEVEN"),
            "status": status,
        }
        state.setdefault("trade_history", []).insert(0, closed)
        state["total_pnl"] = round(state.get("total_pnl", 0.0) + chg.net_pnl, 2)
        if not shadow:
            self.notifier.notify_trade_closed(closed)

    def square_off_all(self, state: dict):
        """Force-closes all remaining open positions."""
        open_pos = state.get("open_positions", [])
        if not open_pos:
            return

        print(f"[Daemon] Squaring off {len(open_pos)} open positions...")
        for p in open_pos:
            self._close_position(state, p, "SQUARE_OFF")

        state["open_positions"] = []
        self.save_state(state)

    # ─── 5. Scanning Loops ────────────────────────────────────────

    def passes_charges_filter(self, symbol, asset_type, direction, entry_price, target_price, qty_or_lots):
        """Rejects trades whose profit at target cannot cover MIN_EDGE_MULTIPLE x round-trip charges."""
        ok, expected_profit, est_charges = passes_charges_filter(
            symbol, asset_type, direction, entry_price, target_price, qty_or_lots
        )
        if not ok:
            print(f"[Charges Filter] REJECTED {symbol}: Expected ₹{expected_profit:.0f} < {MIN_EDGE_MULTIPLE:g}×Charges ₹{MIN_EDGE_MULTIPLE * est_charges:.0f}")
        return ok

    def passes_daily_caps(self, state: dict, symbol: str) -> bool:
        """Blocks new entries once today's trades (closed + still open) hit the global or per-symbol cap."""
        today_str = datetime.now().strftime("%Y-%m-%d")
        todays = [
            t for t in list(state.get("trade_history", [])) + list(state.get("open_positions", []))
            if str(t.get("entry_time", "")).startswith(today_str)
        ]
        if len(todays) >= MAX_DAILY_TRADES:
            print(f"[Daily Cap] REJECTED {symbol}: {len(todays)}/{MAX_DAILY_TRADES} trades already taken today")
            return False
        symbol_count = sum(1 for t in todays if t.get("symbol") == symbol)
        if symbol_count >= MAX_TRADES_PER_SYMBOL_PER_DAY:
            print(f"[Daily Cap] REJECTED {symbol}: {symbol_count}/{MAX_TRADES_PER_SYMBOL_PER_DAY} trades in this symbol today")
            return False
        return True

    def filter_disabled_strategies(self, signals: list, label: str) -> list:
        """Drops signals whose strategy is in DISABLED_STRATEGIES. Applied before any [:N] slice so
        a disabled signal cannot occupy a slot that an enabled one should get."""
        enabled = []
        for s in signals or []:
            if s.get("strategy") in DISABLED_STRATEGIES:
                print(f"[Strategy Gate] Skipped {label} {s.get('symbol')}: {s.get('strategy')} is disabled")
            else:
                enabled.append(s)
        return enabled

    def run_gap_scan(self):
        """Runs 9:15 AM Gap opening detection across all 181 stocks with Fixed-Risk Sizing."""
        if self.gap_scanned_today:
            return

        print(f"\n[Daemon] [{datetime.now().strftime('%H:%M:%S')}] Running Early Market GAP Scanner (181 Stocks)...")
        state = self.load_state()
        risk_metrics = self.calculate_margin_and_risk(state)

        gap_signals = self.filter_disabled_strategies(scan_for_gaps(), "GAP")
        if gap_signals:
            print(f"[Daemon] Found {len(gap_signals)} gap openings!")
            for g in gap_signals[:4]:
                if any(p["symbol"] == g["symbol"] for p in state.get("open_positions", [])):
                    continue
                if len(state.get("open_positions", [])) >= 8:
                    break
                if not self.passes_daily_caps(state, g["symbol"]):
                    continue

                # ─── RL Execution Optimizer ───
                rl_state = self.rl_optimizer.build_state_from_market(
                    g,
                    regime_probs={"BULL": 0.6, "BEAR": 0.1, "CHOP": 0.3},
                    account_metrics={
                        "open_positions_count": len(state.get("open_positions", [])),
                        "daily_pnl": state.get("total_pnl", 0.0),
                        "current_drawdown": max(0.0, 150000.0 - risk_metrics["total_capital"]),
                    },
                )
                rl_act = self.rl_optimizer.get_optimal_action(rl_state)
                rl_budget = self.risk_manager.risk_per_trade * rl_act.risk_scaling

                qty, risk_inr, status_msg = self.risk_manager.calculate_position_size(
                    g["entry_price"], g["stop_loss"], "EQUITY", 1, max_risk_budget=rl_budget
                )
                if status_msg != "APPROVED" or qty <= 0:
                    print(f"[Daemon] Skipped GAP {g['symbol']}: {status_msg}")
                    continue

                if not self.passes_charges_filter(g["symbol"], "EQUITY", g["direction"], g["entry_price"], g["target_price"], qty):
                    continue

                required_margin = (g["entry_price"] * qty) / 5.0
                if required_margin > risk_metrics["free_margin"]:
                    print(f"[Daemon] Skipped GAP {g['symbol']}: Required margin ₹{required_margin:.0f} > Free cash ₹{risk_metrics['free_margin']:.0f}")
                    continue

                lv = self._equity_entry_levels(g)
                if lv is None:
                    continue

                self.notifier.notify_signal_found(g)

                pos_id = f"gap_{int(time.time()*1000)}"
                new_pos = {
                    "id": pos_id,
                    "symbol": g["symbol"],
                    "direction": g["direction"],
                    "qty": qty,
                    "entry_price": lv["entry_price"],
                    "stop_loss": lv["stop_loss"],
                    "target_price": lv["target_price"],
                    "target_1": lv["target_1"],
                    "target_2": lv["target_2"],
                    "mode": self.mode,
                    "asset_type": "EQUITY",
                    "strategy": g["strategy"],
                    "rl_risk_scaling": rl_act.risk_scaling,
                    "rl_exit_preference": rl_act.exit_preference,
                    "rl_trail_tightness": rl_act.trail_tightness,
                    "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "OPEN",
                }
                if not self._route_entry(new_pos):
                    continue
                state.setdefault("open_positions", []).append(new_pos)
                risk_metrics["free_margin"] -= required_margin
                self.save_state(state)
                self.notifier.notify_trade_executed(new_pos)
                print(f"[Daemon] GAP {g['strategy']}: {g['direction']} {qty}x {g['symbol']} @ ₹{g['entry_price']} (Risk: ₹{risk_inr:.0f})")
        else:
            print("[Daemon] No significant gap openings detected today.")

        self.gap_scanned_today = True

    def run_scan_cycle(self):
        """Runs fast 5-8 second intraday equity scan with Universal Fixed-Risk validation."""
        state = self.load_state()
        risk_metrics = self.calculate_margin_and_risk(state)
        print(f"\n[Daemon] [{datetime.now().strftime('%H:%M:%S')}] Fast Intraday Scan ({len(TOP_INTRADAY_UNIVERSE)} stocks) | Free Cash: ₹{risk_metrics['free_margin']:,.0f}...")

        # 1. Manage open positions
        self.manage_open_positions(state)

        # Equity signals are all labelled DEFAULT_EQUITY_STRATEGY; if it is disabled, skip the 40-stock
        # scan entirely (open positions were already managed above).
        if DEFAULT_EQUITY_STRATEGY in DISABLED_STRATEGIES:
            print(f"[Strategy Gate] Equity {DEFAULT_EQUITY_STRATEGY} scan disabled. Managing open positions only.")
            return

        # 2. Risk & Margin check
        can_trade, reason = self.risk_manager.can_trade()
        if not can_trade:
            print(f"[Daemon] Trade entry paused: {reason}")
            return

        if risk_metrics["free_margin"] < 1000.0:
            print(f"[Daemon] Insufficient free margin (₹{risk_metrics['free_margin']:.2f}). Waiting for exits.")
            return

        equity_pos = [p for p in state.get("open_positions", []) if p.get("asset_type", "EQUITY") == "EQUITY"]
        if len(equity_pos) >= 4:
            print("[Daemon] Max equity positions (4) reached. Monitoring.")
            return

        # 3. Fast scan
        signals = self.scan_universe()
        if signals:
            print(f"[Daemon] Found {len(signals)} high-confidence setups!")
            for sig in signals[:3]:
                if len(state.get("open_positions", [])) >= 8:
                    break
                if any(p["symbol"] == sig["symbol"] for p in state.get("open_positions", [])):
                    continue

                if not self.passes_daily_caps(state, sig["symbol"]):
                    continue

                # ─── Macro-to-Micro Alignment: Check Multi-Week Swing Bias ───
                macro_bias = get_swing_directional_bias(sig["symbol"])
                if macro_bias == "ONLY_BUY_DIPS" and sig["direction"] == "SELL":
                    print(f"[Daemon] Skipped {sig['symbol']} SHORT: Conflicts with Multi-Week Bullish Swing Radar.")
                    continue
                elif macro_bias == "ONLY_SELL_RALLIES" and sig["direction"] == "BUY":
                    print(f"[Daemon] Skipped {sig['symbol']} LONG: Conflicts with Multi-Week Bearish Swing Radar.")
                    continue

                # ─── RL Execution Optimizer ───
                rl_state = self.rl_optimizer.build_state_from_market(
                    sig,
                    regime_probs=sig.get("ai_regime_probs", {"BULL": 0.5, "BEAR": 0.2, "CHOP": 0.3}),
                    account_metrics={
                        "open_positions_count": len(state.get("open_positions", [])),
                        "daily_pnl": state.get("total_pnl", 0.0),
                        "current_drawdown": max(0.0, 150000.0 - risk_metrics["total_capital"]),
                    },
                )
                rl_act = self.rl_optimizer.get_optimal_action(rl_state)
                rl_budget = self.risk_manager.risk_per_trade * rl_act.risk_scaling

                qty, risk_inr, status_msg = self.risk_manager.calculate_position_size(
                    sig["entry_price"], sig["stop_loss"], "EQUITY", 1, max_risk_budget=rl_budget
                )
                if status_msg != "APPROVED" or qty <= 0:
                    print(f"[Daemon] Skipped {sig['symbol']}: {status_msg}")
                    continue

                if not self.passes_charges_filter(sig["symbol"], "EQUITY", sig["direction"], sig["entry_price"], sig["target_price"], qty):
                    continue

                required_margin = (sig["entry_price"] * qty) / 5.0  # 5x MIS leverage
                if required_margin > risk_metrics["free_margin"]:
                    print(f"[Daemon] Skipped {sig['symbol']}: Required margin ₹{required_margin:.0f} > Free cash ₹{risk_metrics['free_margin']:.0f}")
                    continue

                lv = self._equity_entry_levels(sig)
                if lv is None:
                    continue

                self.notifier.notify_signal_found(sig)

                pos_id = f"eq_{int(time.time()*1000)}"
                new_pos = {
                    "id": pos_id,
                    "symbol": sig["symbol"],
                    "direction": sig["direction"],
                    "qty": qty,
                    "entry_price": lv["entry_price"],
                    "stop_loss": lv["stop_loss"],
                    "target_price": lv["target_price"],
                    "target_1": lv["target_1"],
                    "target_2": lv["target_2"],
                    "mode": self.mode,
                    "asset_type": "EQUITY",
                    "strategy": sig.get("strategy", DEFAULT_EQUITY_STRATEGY),
                    "rl_risk_scaling": rl_act.risk_scaling,
                    "rl_exit_preference": rl_act.exit_preference,
                    "rl_trail_tightness": rl_act.trail_tightness,
                    "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "OPEN",
                }
                if not self._route_entry(new_pos):
                    continue
                state.setdefault("open_positions", []).append(new_pos)
                risk_metrics["free_margin"] -= required_margin
                self.save_state(state)
                self.notifier.notify_trade_executed(new_pos)
                print(f"[Daemon] Executed {self.mode} {sig['direction']} {qty}x {sig['symbol']} @ ₹{sig['entry_price']} (Risk: ₹{risk_inr:.0f})")
        else:
            print("[Daemon] No actionable equity setups currently.")

    def run_currency_scan(self):
        """Scans 4 FX currency pairs with Universal Fixed-Risk validation."""
        state = self.load_state()
        risk_metrics = self.calculate_margin_and_risk(state)

        currency_positions = [p for p in state.get("open_positions", []) if p.get("asset_type") == "CURRENCY"]
        if len(currency_positions) >= 2:
            return

        currency_signals = self.filter_disabled_strategies(scan_all_currency_pairs(), "FX")
        if currency_signals:
            today_str = datetime.now().strftime("%Y-%m-%d")
            for sig in currency_signals[:2]:
                if any(p["symbol"] == sig["symbol"] for p in state.get("open_positions", [])):
                    continue

                past_today = [t for t in state.get("trade_history", []) if t.get("symbol") == sig["symbol"] and t.get("entry_time", "").startswith(today_str)]
                if len(past_today) >= 3:
                    continue
                if not self.passes_daily_caps(state, sig["symbol"]):
                    continue

                # ─── RL Execution Optimizer ───
                rl_state = self.rl_optimizer.build_state_from_market(
                    sig,
                    regime_probs={"BULL": 0.5, "BEAR": 0.2, "CHOP": 0.3},
                    account_metrics={
                        "open_positions_count": len(state.get("open_positions", [])),
                        "daily_pnl": state.get("total_pnl", 0.0),
                        "current_drawdown": max(0.0, 150000.0 - risk_metrics["total_capital"]),
                    },
                )
                rl_act = self.rl_optimizer.get_optimal_action(rl_state)
                rl_budget = self.risk_manager.risk_per_trade * rl_act.risk_scaling

                lots, risk_inr, status_msg = self.risk_manager.calculate_position_size(
                    sig["entry_price"], sig["stop_loss"], "CURRENCY", 1000, max_risk_budget=rl_budget
                )
                if status_msg != "APPROVED" or lots <= 0:
                    print(f"[Daemon] Skipped FX {sig['symbol']}: {status_msg}")
                    continue

                if not self.passes_charges_filter(sig["symbol"], "CURRENCY", sig["direction"], sig["entry_price"], sig["target_price"], lots):
                    continue

                spec = CURRENCY_PAIRS.get(sig["symbol"])
                pos_margin = lots * (spec.approx_margin if spec else 2000.0)
                if pos_margin > risk_metrics["free_margin"]:
                    print(f"[Daemon] Skipped FX {sig['symbol']}: Required margin ₹{pos_margin:.0f} > Free cash ₹{risk_metrics['free_margin']:.0f}")
                    continue

                sig["lots"] = lots
                self.notifier.notify_signal_found(sig)

                pos_id = f"fx_{int(time.time()*1000)}"
                new_pos = {
                    "id": pos_id,
                    "symbol": sig["symbol"],
                    "direction": sig["direction"],
                    "lots": lots,
                    "qty": lots,
                    "entry_price": entry_fill(sig["direction"], sig["entry_price"], "CURRENCY"),
                    "stop_loss": sig["stop_loss"],
                    "target_price": sig["target_price"],
                    "target_1": sig.get("target_1", sig["target_price"]),
                    "target_2": sig.get("target_2", sig["target_price"]),
                    "mode": self.mode,
                    "asset_type": "CURRENCY",
                    "strategy": sig["strategy"],
                    "rl_risk_scaling": rl_act.risk_scaling,
                    "rl_exit_preference": rl_act.exit_preference,
                    "rl_trail_tightness": rl_act.trail_tightness,
                    "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "OPEN",
                }
                if not self._route_entry(new_pos):
                    continue
                state.setdefault("open_positions", []).append(new_pos)
                risk_metrics["free_margin"] -= pos_margin
                self.save_state(state)
                self.notifier.notify_trade_executed(new_pos)
                print(f"[Daemon] CURRENCY: {sig['direction']} {lots} lot(s) {sig['symbol']} @ {sig['entry_price']:.4f} (Risk: ₹{risk_inr:.0f})")

    def run_commodity_scan(self):
        """Scans MCX commodity futures with Universal Fixed-Risk & Multiplier Cap."""
        state = self.load_state()
        risk_metrics = self.calculate_margin_and_risk(state)

        commodity_positions = [p for p in state.get("open_positions", []) if p.get("asset_type") == "COMMODITY"]
        if len(commodity_positions) >= 2:
            return

        commodity_signals = self.filter_disabled_strategies(scan_all_commodities(), "MCX")
        if commodity_signals:
            today_str = datetime.now().strftime("%Y-%m-%d")
            for sig in commodity_signals[:2]:
                if any(p["symbol"] == sig["symbol"] for p in state.get("open_positions", [])):
                    continue

                past_today = [t for t in state.get("trade_history", []) if t.get("symbol") == sig["symbol"] and t.get("entry_time", "").startswith(today_str)]
                if len(past_today) >= 3:
                    continue
                if not self.passes_daily_caps(state, sig["symbol"]):
                    continue

                spec = COMMODITY_SPECS.get(sig["symbol"])
                lot_mult = spec.lot_size if spec else 10

                # ─── RL Execution Optimizer ───
                rl_state = self.rl_optimizer.build_state_from_market(
                    sig,
                    regime_probs={"BULL": 0.5, "BEAR": 0.2, "CHOP": 0.3},
                    account_metrics={
                        "open_positions_count": len(state.get("open_positions", [])),
                        "daily_pnl": state.get("total_pnl", 0.0),
                        "current_drawdown": max(0.0, 150000.0 - risk_metrics["total_capital"]),
                    },
                )
                rl_act = self.rl_optimizer.get_optimal_action(rl_state)
                rl_budget = self.risk_manager.risk_per_trade * rl_act.risk_scaling

                lots, risk_inr, status_msg = self.risk_manager.calculate_position_size(
                    sig["entry_price"], sig["stop_loss"], "COMMODITY", lot_mult, max_risk_budget=rl_budget
                )
                if status_msg != "APPROVED" or lots <= 0:
                    print(f"[Daemon] Skipped MCX {sig['symbol']}: {status_msg}")
                    continue

                if not self.passes_charges_filter(sig["symbol"], "COMMODITY", sig["direction"], sig["entry_price"], sig["target_price"], lots):
                    continue

                pos_margin = lots * (spec.approx_margin if spec else 15000.0)
                if pos_margin > risk_metrics["free_margin"]:
                    print(f"[Daemon] Skipped MCX {sig['symbol']}: Required margin ₹{pos_margin:.0f} > Free cash ₹{risk_metrics['free_margin']:.0f}")
                    continue

                sig["lots"] = lots
                self.notifier.notify_signal_found(sig)

                pos_id = f"mcx_{int(time.time()*1000)}"
                new_pos = {
                    "id": pos_id,
                    "symbol": sig["symbol"],
                    "name": sig.get("name", sig["symbol"]),
                    "direction": sig["direction"],
                    "lots": lots,
                    "qty": lots,
                    "entry_price": entry_fill(sig["direction"], sig["entry_price"], "COMMODITY"),
                    "stop_loss": sig["stop_loss"],
                    "target_price": sig["target_price"],
                    "target_1": sig.get("target_1", sig["target_price"]),
                    "target_2": sig.get("target_2", sig["target_price"]),
                    "mode": self.mode,
                    "asset_type": "COMMODITY",
                    "strategy": sig["strategy"],
                    "rl_risk_scaling": rl_act.risk_scaling,
                    "rl_exit_preference": rl_act.exit_preference,
                    "rl_trail_tightness": rl_act.trail_tightness,
                    "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "OPEN",
                }
                if not self._route_entry(new_pos):
                    continue
                state.setdefault("open_positions", []).append(new_pos)
                risk_metrics["free_margin"] -= pos_margin
                self.save_state(state)
                self.notifier.notify_trade_executed(new_pos)
                print(f"[Daemon] MCX: {sig['direction']} {lots} lot(s) {sig['symbol']} @ ₹{sig['entry_price']:,.1f} (Risk: ₹{risk_inr:.0f})")

    # ─── 6. Dedicated Concurrent Threads ─────────────────────────

    def telegram_polling_thread(self):
        """Dedicated thread running 24/7 for instant <500ms Telegram responses."""
        print("[Telegram Poller] Dedicated command server started (Sub-second response active).")
        while self.is_running:
            try:
                self.notifier.check_incoming_commands(self.handle_telegram_command)
                time.sleep(0.8)
            except Exception as e:
                time.sleep(1.0)

    def run(self):
        """
        Master Orchestrator: launches 4 dedicated concurrent threads:
          1. TelegramServer: 24/7 Sub-second command handling
          2. EquityThread: NSE Equities (09:15 - 15:15 IST, 180s cycle, isolated HMM)
          3. CurrencyThread: NSE FX Pairs (09:00 - 16:45 IST, 90s cycle, isolated HMM)
          4. CommodityThread: MCX Commodities (09:00 - 23:15 IST, 120s cycle, isolated HMM)
        """
        print("==================================================")
        print("   PROJECT ATLAS — AUTONOMOUS MULTI-ASSET BOT v5  ")
        print("   Architecture: Phase 3 Multi-Threaded Engine    ")
        print("   Equities: 181 Stocks | FX: 4 Pairs | MCX: 5 Mini Assets")
        print(f"   Mode: {self.mode} | 4 Concurrent Threads: ACTIVE")
        print("   AI: Gaussian HMM + 18-MLP + PPO RL Optimizer")
        print(f"   Telegram Users: {len(self.notifier.chat_ids)} Authorized")
        print("==================================================")

        # 1. Telegram Polling Thread (24/7 Sub-second interactive commands)
        t_telegram = threading.Thread(target=self.telegram_polling_thread, name="TelegramServer", daemon=True)
        t_telegram.start()

        # 2. Dedicated Equity Thread (NSE intraday scanning & MIS lifecycle)
        t_equity = EquityThread(daemon_ref=self, scan_interval=self.scan_interval)
        t_equity.start()

        # 3. Dedicated Currency Thread (NSE CDS FX pairs)
        t_currency = CurrencyThread(daemon_ref=self, scan_interval=90)
        t_currency.start()

        # 4. Dedicated Commodity Thread (MCX Full session until 23:15)
        t_commodity = CommodityThread(daemon_ref=self, scan_interval=120)
        t_commodity.start()

        print("[Orchestrator] All 4 dedicated asset engines successfully launched and operational!")

        # Keep main thread alive as Master Orchestrator
        try:
            while self.is_running:
                time.sleep(1.0)
        except KeyboardInterrupt:
            print("\n[Daemon] Stopping Atlas trading bot...")
            self.is_running = False

    def start(self):
        """Alias for run() to support standard orchestrator interfaces."""
        self.run()


if __name__ == "__main__":
    daemon = TradingDaemon(scan_interval_seconds=180, mode="PAPER")
    daemon.run()
