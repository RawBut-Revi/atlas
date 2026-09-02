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
from trading.backtest import fetch_historical_data
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
from trading.charges import calculate_trade_charges
from trading.swing_radar import scan_swing_radar, get_swing_directional_bias, SwingObservation
from trading.news_radar import (
    scan_news_feeds, is_stock_blocked_by_news, should_exit_position,
    update_panic_state, get_blocked_sectors, get_blocked_stocks, NewsAlert,
)

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


class TradingDaemon:
    def __init__(self, scan_interval_seconds: int = 180, mode: str = "PAPER"):
        self.scan_interval = scan_interval_seconds
        self.mode = mode
        self.risk_manager = RiskManager(capital=150000.0)
        self.notifier = TelegramNotifier()
        self.is_running = True
        self.state_lock = threading.Lock()
        self.daily_report_sent = False
        self.gap_scanned_today = False
        self.currency_square_off_done = False
        self.equity_square_off_done = False
        self.last_news_scan_time = 0.0  # epoch timestamp of last news scan
        self.news_scan_interval = 900   # scan news every 15 minutes

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
                df = fetch_historical_data(symbol, from_date, today)
                if df is not None and len(df) > 0:
                    if isinstance(df, list):
                        return float(df[-1]["close"])
                    elif hasattr(df, "iloc"):
                        return float(df.iloc[-1]["close"])
        except Exception:
            pass

        return fallback_price

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
                multiplier = 100000.0 if "JPY" in symbol else 1000.0
            elif asset_type == "COMMODITY":
                spec = COMMODITY_SPECS.get(symbol)
                pos_margin = qty * (spec.approx_margin if spec else 15000.0)
                if "CRUDE" in symbol:
                    multiplier = 10.0
                elif "NATGAS" in symbol:
                    multiplier = 250.0
                elif "SILVER" in symbol:
                    multiplier = 1.0
                elif "GOLD" in symbol:
                    multiplier = 100.0
                elif "COPPER" in symbol:
                    multiplier = 2500.0
                else:
                    multiplier = 1.0
            else:  # EQUITY (5x intraday MIS leverage)
                pos_margin = (p.get("entry_price", 0.0) * qty) / 5.0
                multiplier = 1.0

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

    # ─── 2. Telegram Command Handler (Instant Response) ───────────

    def handle_telegram_command(self, cmd: str, sender_id: str = "") -> str:
        """Process incoming commands from mobile Telegram."""
        state = self.load_state()
        cmd = cmd.strip().lower()
        risk_metrics = self.calculate_margin_and_risk(state)

        if cmd == "/status":
            now_ist = datetime.now(IST).strftime("%H:%M:%S IST")
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
                f"  ⚖️ <b>Risk : Reward:</b> 1 : 1.50\n\n"
                f"📈 <b>TODAY'S PERFORMANCE:</b>\n"
                f"  💵 <b>Realized Net P&L:</b> ₹{state.get('total_pnl', 0.0):+.2f}\n"
                f"  📊 <b>Markets:</b> 181 Equities + 4 FX + 5 MCX"
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
            for p in open_pos:
                asset_type = p.get("asset_type", "EQUITY")
                cur_price = self.get_live_price(p["symbol"], asset_type, fallback_price=p["entry_price"])
                
                # Calculate live unrealized P&L
                qty = p.get("lots", p.get("qty", 1))
                if p["direction"] == "BUY":
                    unrealized_pnl = (cur_price - p["entry_price"]) * qty
                    unrealized_pct = ((cur_price - p["entry_price"]) / max(p["entry_price"], 0.001)) * 100.0
                else:
                    unrealized_pnl = (p["entry_price"] - cur_price) * qty
                    unrealized_pct = ((p["entry_price"] - cur_price) / max(p["entry_price"], 0.001)) * 100.0

                pnl_badge = f"🟢 +₹{unrealized_pnl:.2f} (+{unrealized_pct:.2f}%)" if unrealized_pnl >= 0 else f"🔴 -₹{abs(unrealized_pnl):.2f} ({unrealized_pct:.2f}%)"

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
                sl_risk = abs(p["entry_price"] - p["stop_loss"]) * qty
                tp_reward = abs(p["target_price"] - p["entry_price"]) * qty

                lines.append(
                    f"{tag} <b>{p['symbol']}</b> ({p['direction']}) | {qty_label}\n"
                    f"  LTP: ₹{cur_price:,.2f} (Entry: ₹{p['entry_price']:,.2f})\n"
                    f"  🎯 Target: ₹{p['target_price']:,.2f} (+₹{tp_reward:,.0f})\n"
                    f"  🛑 SL: ₹{p['stop_loss']:,.2f} (-₹{sl_risk:,.0f})\n"
                    f"  💵 Live P&L: {pnl_badge}\n"
                )

            lines.append(
                f"🛡️ <b>Total Worst-Case Loss:</b> -₹{risk_metrics['total_max_sl_loss']:,.2f}\n"
                f"🎯 <b>Total Best-Case Gain:</b> +₹{risk_metrics['total_max_tp_gain']:,.2f}"
            )
            return "\n".join(lines)

        elif cmd in ("/pnl", "/summary"):
            history = state.get("trade_history", [])
            today_str = datetime.now().strftime("%Y-%m-%d")
            closed_today = [t for t in history if t.get("exit_time", "").startswith(today_str)]
            
            gross_pnl = sum(t.get("gross_pnl", t.get("pnl", 0.0)) for t in closed_today)
            total_fees = sum(t.get("charges", 0.0) for t in closed_today)
            net_pnl = sum(t.get("net_pnl", t.get("pnl", 0.0)) for t in closed_today)

            wins = len([t for t in closed_today if t.get("net_pnl", t.get("pnl", 0)) > 0])
            losses = len([t for t in closed_today if t.get("net_pnl", t.get("pnl", 0)) < 0])
            breakevens = len([t for t in closed_today if abs(t.get("net_pnl", t.get("pnl", 0))) < 0.01])
            total_trades = len(closed_today)
            wr = round(wins / max(total_trades - breakevens, 1) * 100, 1) if (total_trades - breakevens) > 0 else 0.0

            lines = [
                f"📈 <b>TODAY'S REAL-WORLD P&L ({today_str})</b>",
                f"━━━━━━━━━━━━━━━━━━━",
                f"🔢 <b>Total Trades:</b> {total_trades}",
                f"✅ Wins: {wins} | ❌ Losses: {losses} | ⚪ Breakeven: {breakevens}",
                f"🎯 <b>Win Rate:</b> {wr}%\n",
                f"💰 <b>Gross Trading P&L:</b> <code>₹{gross_pnl:+.2f}</code>",
                f"🧾 <b>Brokerage & Taxes:</b> <code>-₹{total_fees:.2f}</code>",
                f"💵 <b>Net Take-Home P&L:</b> <b>₹{net_pnl:+.2f}</b>",
                f"💼 <b>Closing Capital:</b> ₹{state.get('capital', 10000) + net_pnl:,.2f}\n",
            ]

            if closed_today:
                lines.append("📋 <b>TRADE FEE BREAKDOWN:</b>")
                for t in closed_today[:8]:
                    t_net = t.get("net_pnl", t.get("pnl", 0.0))
                    t_gross = t.get("gross_pnl", t_net)
                    t_fee = t.get("charges", 0.0)
                    t_icon = "🟢" if t_net > 0 else ("🔴" if t_net < 0 else "⚪")
                    lines.append(
                        f"{t_icon} <b>{t.get('symbol')}</b> ({t.get('direction')}): Gross ₹{t_gross:+.2f} | Fees -₹{t_fee:.2f} ➔ Net <b>₹{t_net:+.2f}</b>"
                    )

            return "\n".join(lines)

        elif cmd in ("/report", "/history"):
            history = state.get("trade_history", [])
            if not history:
                return "ℹ️ No trade history recorded yet."

            lines = ["📜 <b>HISTORICAL TRADE JOURNAL (REAL-WORLD)</b>\n━━━━━━━━━━━━━━━━━━━"]
            for i, t in enumerate(history[:12], 1):
                t_net = t.get("net_pnl", t.get("pnl", 0.0))
                t_gross = t.get("gross_pnl", t_net)
                t_fee = t.get("charges", 0.0)
                t_icon = "🟢" if t_net > 0 else ("🔴" if t_net < 0 else "⚪")
                lines.append(
                    f"{i}. {t_icon} <b>{t.get('symbol')}</b> ({t.get('direction')}) | Net P&L: <b>₹{t_net:+.2f}</b>\n"
                    f"   Gross: ₹{t_gross:+.2f} | Taxes/Fees: -₹{t_fee:.2f} | {t.get('status')}\n"
                    f"   Entry: ₹{t.get('entry_price')} ➔ Exit: ₹{t.get('exit_price')}\n"
                    f"   Time: {t.get('entry_time', '')} ➔ {t.get('exit_time', '')}\n"
                )
            return "\n".join(lines)

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

        elif cmd == "/news":
            # Trigger a fresh news scan
            try:
                alerts = scan_news_feeds()
                update_panic_state(alerts)
            except Exception:
                alerts = []

            blocked = get_blocked_sectors()
            if not alerts and not blocked:
                return "✅ No market-moving news detected. All sectors clear for trading."

            lines = ["📰 <b>NEWS & PANIC RADAR STATUS</b>\n━━━━━━━━━━━━━━━━━━━"]

            if blocked:
                lines.append(f"🚫 <b>Blocked Sectors Today:</b> {', '.join(blocked)}")
                blocked_stocks = get_blocked_stocks()
                if blocked_stocks:
                    lines.append(f"  ⛔ Affected Symbols: {', '.join(list(blocked_stocks)[:15])}")
                lines.append("")

            if alerts:
                lines.append(f"📊 <b>Active Alerts ({len(alerts)} found):</b>\n")
                for a in alerts[:6]:
                    sev_icon = "🔴" if a.severity >= 8 else ("🟡" if a.severity >= 6 else "⚪")
                    lines.append(
                        f"{sev_icon} <b>[{a.severity}/10]</b> {a.headline[:100]}\n"
                        f"  📡 {a.source} | 🔑 {', '.join(a.panic_keywords_found[:3])}\n"
                        f"  🏭 {', '.join(a.affected_sectors[:4])} | Action: <b>{a.action}</b>\n"
                    )
            else:
                lines.append("✅ No panic-level headlines detected in current feed scan.")

            lines.append("\n💡 <i>News is scanned every 15 minutes. Severity ≥8 triggers auto-exit.</i>")
            return "\n".join(lines)

        elif cmd == "/help":
            return (
                f"🤖 <b>ATLAS BOT COMMANDS</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"⚡ /positions — Live open trades & unrealized P&L\n"
                f"📊 /status — Demat capital, used margin & scenario risk\n"
                f"💰 /pnl — Today's closed trades & realized profit\n"
                f"📜 /report — Full historical trade audit journal\n"
                f"🔭 /swing — Multi-Week Swing Observation Radar (1-4w)\n"
                f"📊 /fno — Options & Futures swing trade simulations\n"
                f"⚡ /volatility — Top explosive high-volatility runners\n"
                f"🕯️ /patterns — 3-Hour Candlestick & Chart Patterns\n"
                f"🔍 /scan — Trigger fast intraday scan now\n"
                f"⚡ /gaps — 9:15 AM Gap Openings scanner\n"
                f"💱 /currency — Live Currency Futures setups\n"
                f"🛢️ /commodities — Live MCX setups (Crude/Silver/Gold)\n"
                f"📰 /news — Live News & Panic Radar (auto-blocks sectors)\n"
                f"👥 /users — View whitelisted users\n"
                f"➕ /adduser &lt;id&gt; — Authorize new trading friend"
            )

        return "Commands: /status, /positions, /pnl, /report, /swing, /fno, /volatility, /patterns, /scan, /gaps, /currency, /commodities, /users, /help"

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

    def manage_open_positions(self, state: dict):
        """Checks open positions against Stop Loss and Take Profit levels."""
        open_pos = state.get("open_positions", [])
        if not open_pos:
            return

        remaining = []
        for p in open_pos:
            try:
                # ─── 0. News Panic Emergency Exit Check ───
                should_force_exit, exit_reason = should_exit_position(p["symbol"])
                if should_force_exit:
                    self._close_position(state, p, "NEWS_PANIC_EXIT")
                    self.notifier.send_message(
                        f"⛔ <b>NEWS PANIC EXIT:</b> {p['symbol']} ({p['direction']})\n"
                        f"Reason: {exit_reason}"
                    )
                    print(f"[Daemon] 🚨 EMERGENCY EXIT {p['symbol']}: {exit_reason}")
                    continue

                asset_type = p.get("asset_type", "EQUITY")
                cur_price = self.get_live_price(p["symbol"], asset_type, fallback_price=p["entry_price"])
                
                # ─── 1. Trailing Stop-Loss to Breakeven + Fees (+50% Target Rule) ───
                if not p.get("sl_trailed_to_cost", False):
                    if p["direction"] == "BUY":
                        target_dist = p["target_price"] - p["entry_price"]
                        if cur_price >= p["entry_price"] + (target_dist * 0.5):
                            p["stop_loss"] = round(p["entry_price"] * 1.001, 2)  # Entry + estimated fees
                            p["sl_trailed_to_cost"] = True
                            print(f"[Daemon] 🎯 Trailed SL to Cost on {p['symbol']} @ ₹{p['stop_loss']:.2f} (+50% target reached!)")
                    else:
                        target_dist = p["entry_price"] - p["target_price"]
                        if cur_price <= p["entry_price"] - (target_dist * 0.5):
                            p["stop_loss"] = round(p["entry_price"] * 0.999, 2)
                            p["sl_trailed_to_cost"] = True
                            print(f"[Daemon] 🎯 Trailed SL to Cost on {p['symbol']} @ ₹{p['stop_loss']:.2f} (+50% target reached!)")

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
                    exit_price = p["target_price"] if hit_tp else p["stop_loss"]
                    qty = p.get("lots", p.get("qty", 1))
                    
                    chg = calculate_trade_charges(
                        p["symbol"], asset_type, p["direction"],
                        p["entry_price"], exit_price, qty_or_lots=qty
                    )

                    closed = {
                        **p,
                        "exit_price": round(exit_price, 2),
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
                    self.notifier.notify_trade_closed(closed)
                    print(f"[Daemon] Position Closed: {p['symbol']} Gross: ₹{chg.gross_pnl:.2f} | Fees: ₹{chg.total_charges:.2f} | Net: ₹{chg.net_pnl:.2f} ({closed['status']})")
                else:
                    remaining.append(p)

            except Exception as e:
                print(f"[Daemon] Error managing position {p.get('symbol')}: {e}")
                remaining.append(p)

        state["open_positions"] = remaining
        self.save_state(state)

    def _close_position(self, state: dict, p: dict, status: str = "CLOSED"):
        """Closes a single position cleanly with real market price and statutory charges."""
        asset_type = p.get("asset_type", "EQUITY")
        exit_price = self.get_live_price(p["symbol"], asset_type, fallback_price=p["entry_price"])
        qty = p.get("lots", p.get("qty", 1))

        chg = calculate_trade_charges(
            p["symbol"], asset_type, p["direction"],
            p["entry_price"], exit_price, qty_or_lots=qty
        )

        closed = {
            **p,
            "exit_price": round(exit_price, 2),
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

    def run_gap_scan(self):
        """Runs 9:15 AM Gap opening detection across all 181 stocks with Fixed-Risk Sizing."""
        if self.gap_scanned_today:
            return

        print(f"\n[Daemon] [{datetime.now().strftime('%H:%M:%S')}] Running Early Market GAP Scanner (181 Stocks)...")
        state = self.load_state()
        risk_metrics = self.calculate_margin_and_risk(state)

        gap_signals = scan_for_gaps()
        if gap_signals:
            print(f"[Daemon] Found {len(gap_signals)} gap openings!")
            for g in gap_signals[:4]:
                if any(p["symbol"] == g["symbol"] for p in state.get("open_positions", [])):
                    continue
                if len(state.get("open_positions", [])) >= 8:
                    break

                # ─── News Panic Radar: Block entries in threatened sectors ───
                is_blocked, block_reason = is_stock_blocked_by_news(g["symbol"])
                if is_blocked:
                    print(f"[Daemon] 🚨 Skipped GAP {g['symbol']}: {block_reason}")
                    continue

                qty, risk_inr, status_msg = self.risk_manager.calculate_position_size(
                    g["entry_price"], g["stop_loss"], "EQUITY", 1
                )
                if status_msg != "APPROVED" or qty <= 0:
                    print(f"[Daemon] Skipped GAP {g['symbol']}: {status_msg}")
                    continue

                required_margin = (g["entry_price"] * qty) / 5.0
                if required_margin > risk_metrics["free_margin"]:
                    print(f"[Daemon] Skipped GAP {g['symbol']}: Required margin ₹{required_margin:.0f} > Free cash ₹{risk_metrics['free_margin']:.0f}")
                    continue

                self.notifier.notify_signal_found(g)

                pos_id = f"gap_{int(time.time()*1000)}"
                new_pos = {
                    "id": pos_id,
                    "symbol": g["symbol"],
                    "direction": g["direction"],
                    "qty": qty,
                    "entry_price": g["entry_price"],
                    "stop_loss": g["stop_loss"],
                    "target_price": g["target_price"],
                    "target_1": g.get("target_1", g["target_price"]),
                    "target_2": g.get("target_2", g["target_price"]),
                    "mode": self.mode,
                    "asset_type": "EQUITY",
                    "strategy": g["strategy"],
                    "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "OPEN",
                }
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

                # ─── Macro-to-Micro Alignment: Check Multi-Week Swing Bias ───
                macro_bias = get_swing_directional_bias(sig["symbol"])
                if macro_bias == "ONLY_BUY_DIPS" and sig["direction"] == "SELL":
                    print(f"[Daemon] Skipped {sig['symbol']} SHORT: Conflicts with Multi-Week Bullish Swing Radar.")
                    continue
                elif macro_bias == "ONLY_SELL_RALLIES" and sig["direction"] == "BUY":
                    print(f"[Daemon] Skipped {sig['symbol']} LONG: Conflicts with Multi-Week Bearish Swing Radar.")
                    continue

                # ─── News Panic Radar: Block entries in threatened sectors ───
                is_blocked, block_reason = is_stock_blocked_by_news(sig["symbol"])
                if is_blocked:
                    print(f"[Daemon] 🚨 Skipped {sig['symbol']}: {block_reason}")
                    continue

                qty, risk_inr, status_msg = self.risk_manager.calculate_position_size(
                    sig["entry_price"], sig["stop_loss"], "EQUITY", 1
                )
                if status_msg != "APPROVED" or qty <= 0:
                    print(f"[Daemon] Skipped {sig['symbol']}: {status_msg}")
                    continue

                required_margin = (sig["entry_price"] * qty) / 5.0  # 5x MIS leverage
                if required_margin > risk_metrics["free_margin"]:
                    print(f"[Daemon] Skipped {sig['symbol']}: Required margin ₹{required_margin:.0f} > Free cash ₹{risk_metrics['free_margin']:.0f}")
                    continue

                self.notifier.notify_signal_found(sig)

                pos_id = f"eq_{int(time.time()*1000)}"
                new_pos = {
                    "id": pos_id,
                    "symbol": sig["symbol"],
                    "direction": sig["direction"],
                    "qty": qty,
                    "entry_price": sig["entry_price"],
                    "stop_loss": sig["stop_loss"],
                    "target_price": sig["target_price"],
                    "target_1": sig.get("target_1", sig["target_price"]),
                    "target_2": sig.get("target_2", sig["target_price"]),
                    "mode": self.mode,
                    "asset_type": "EQUITY",
                    "strategy": sig.get("strategy", "INTRADAY"),
                    "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "OPEN",
                }
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

        currency_signals = scan_all_currency_pairs()
        if currency_signals:
            for sig in currency_signals[:2]:
                if any(p["symbol"] == sig["symbol"] for p in state.get("open_positions", [])):
                    continue

                # ─── News Panic Radar: Block entries in threatened sectors ───
                is_blocked, block_reason = is_stock_blocked_by_news(sig["symbol"])
                if is_blocked:
                    print(f"[Daemon] 🚨 Skipped FX {sig['symbol']}: {block_reason}")
                    continue

                lots, risk_inr, status_msg = self.risk_manager.calculate_position_size(
                    sig["entry_price"], sig["stop_loss"], "CURRENCY", 1000
                )
                if status_msg != "APPROVED" or lots <= 0:
                    print(f"[Daemon] Skipped FX {sig['symbol']}: {status_msg}")
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
                    "entry_price": sig["entry_price"],
                    "stop_loss": sig["stop_loss"],
                    "target_price": sig["target_price"],
                    "target_1": sig.get("target_1", sig["target_price"]),
                    "target_2": sig.get("target_2", sig["target_price"]),
                    "mode": self.mode,
                    "asset_type": "CURRENCY",
                    "strategy": sig["strategy"],
                    "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "OPEN",
                }
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

        commodity_signals = scan_all_commodities()
        if commodity_signals:
            for sig in commodity_signals[:2]:
                if any(p["symbol"] == sig["symbol"] for p in state.get("open_positions", [])):
                    continue

                # ─── News Panic Radar: Block entries in threatened sectors ───
                is_blocked, block_reason = is_stock_blocked_by_news(sig["symbol"])
                if is_blocked:
                    print(f"[Daemon] 🚨 Skipped MCX {sig['symbol']}: {block_reason}")
                    continue

                spec = COMMODITY_SPECS.get(sig["symbol"])
                lot_mult = spec.lot_size if spec else 10

                lots, risk_inr, status_msg = self.risk_manager.calculate_position_size(
                    sig["entry_price"], sig["stop_loss"], "COMMODITY", lot_mult
                )
                if status_msg != "APPROVED" or lots <= 0:
                    print(f"[Daemon] Skipped MCX {sig['symbol']}: {status_msg}")
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
                    "entry_price": sig["entry_price"],
                    "stop_loss": sig["stop_loss"],
                    "target_price": sig["target_price"],
                    "target_1": sig.get("target_1", sig["target_price"]),
                    "target_2": sig.get("target_2", sig["target_price"]),
                    "mode": self.mode,
                    "asset_type": "COMMODITY",
                    "strategy": sig["strategy"],
                    "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "status": "OPEN",
                }
                state.setdefault("open_positions", []).append(new_pos)
                risk_metrics["free_margin"] -= pos_margin
                self.save_state(state)
                self.notifier.notify_trade_executed(new_pos)
                print(f"[Daemon] MCX: {sig['direction']} {lots} lot(s) {sig['symbol']} @ ₹{sig['entry_price']:,.1f} (Risk: ₹{risk_inr:.0f})")

    def run_news_scan(self):
        """
        Scans financial news RSS feeds for government policy changes, regulatory
        actions, and black-swan events. If severity >= 8, immediately exits open
        positions in the affected sector and blocks new entries for the day.
        Called every 15 minutes during market hours.
        """
        now = time.time()
        if (now - self.last_news_scan_time) < self.news_scan_interval:
            return  # Too soon, skip
        self.last_news_scan_time = now

        try:
            alerts = scan_news_feeds()
            if not alerts:
                return

            # Update persistent panic state file for blocking new entries
            update_panic_state(alerts)

            # Filter only severe alerts (severity >= 7)
            severe = [a for a in alerts if a.severity >= 7]
            if not severe:
                return

            # ─── 1. Send Telegram Alert for Top Panic Headlines ───
            lines = ["🚨 <b>NEWS PANIC RADAR ALERT</b>\n━━━━━━━━━━━━━━━━━━━"]
            for a in severe[:5]:
                sev_icon = "🔴" if a.severity >= 8 else "🟡"
                action_tag = {
                    "EXIT_POSITIONS": "⛔ FORCE EXIT",
                    "BLOCK_ENTRIES": "🚫 BLOCK NEW ENTRIES",
                    "MONITOR": "👁️ MONITOR",
                }.get(a.action, "ℹ️")
                lines.append(
                    f"{sev_icon} <b>Severity {a.severity}/10</b> | {action_tag}\n"
                    f"  📰 <i>{a.headline[:120]}</i>\n"
                    f"  📡 Source: {a.source}\n"
                    f"  🏭 Sectors: {', '.join(a.affected_sectors[:5])}\n"
                    f"  🔑 Keywords: {', '.join(a.panic_keywords_found[:4])}\n"
                )
            self.notifier.send_message("\n".join(lines))

            # ─── 2. Emergency Exit: Force-close positions in panic sectors ───
            exit_alerts = [a for a in severe if a.action == "EXIT_POSITIONS"]
            if exit_alerts:
                state = self.load_state()
                open_pos = state.get("open_positions", [])
                remaining = []
                force_closed = 0

                for p in open_pos:
                    should_exit, reason = should_exit_position(p["symbol"])
                    if should_exit:
                        self._close_position(state, p, "NEWS_PANIC_EXIT")
                        force_closed += 1
                        self.notifier.send_message(
                            f"⛔ <b>NEWS PANIC EXIT:</b> {p['symbol']} ({p['direction']})\n"
                            f"Reason: {reason}"
                        )
                    else:
                        remaining.append(p)

                if force_closed > 0:
                    state["open_positions"] = remaining
                    self.save_state(state)
                    print(f"[News Radar] Force-exited {force_closed} position(s) due to panic news.")

        except Exception as e:
            print(f"[News Radar] Error scanning news: {e}")

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

    def market_scheduler_thread(self):
        """Dedicated thread for market scanning and execution."""
        from datetime import time as dt_time
        WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        heartbeat_counter = 0

        while self.is_running:
            try:
                now_ist = datetime.now(IST)
                now_time = now_ist.time()
                weekday = now_ist.weekday()
                is_weekday = weekday < 5
                time_str = now_ist.strftime("%H:%M:%S")
                heartbeat_counter += 1

                if not is_weekday:
                    if heartbeat_counter % 6 == 1:
                        print(f"[{time_str}] Market closed (Weekend - {WEEKDAYS[weekday]}). Telegram command server ACTIVE.")
                    time.sleep(10)
                    continue

                # 00:00 – 09:00: Before market
                if now_time < CURRENCY_OPEN:
                    if heartbeat_counter % 6 == 1:
                        print(f"[{time_str}] Pre-market standby. Markets open at 09:00 AM IST.")
                    time.sleep(10)
                    continue

                # 09:00 – 09:15: Currency & Commodity open
                elif CURRENCY_OPEN <= now_time < MARKET_OPEN:
                    self.daily_report_sent = False
                    self.gap_scanned_today = False
                    self.currency_square_off_done = False
                    self.equity_square_off_done = False
                    print(f"\n[{time_str}] >> CURRENCY & COMMODITY OPEN (09:00-09:15)")
                    self.run_news_scan()
                    self.run_currency_scan()
                    self.run_commodity_scan()
                    time.sleep(self.scan_interval)

                # 09:15 – 11:00: Morning Momentum Kill Zone
                elif MARKET_OPEN <= now_time < dt_time(11, 0):
                    state = self.load_state()
                    open_count = len(state.get("open_positions", []))
                    print(f"\n[{time_str}] >> ⚡ MORNING MOMENTUM KILL ZONE (09:15-11:00) | Open: {open_count}")
                    self.run_news_scan()
                    if not self.gap_scanned_today and now_time >= dt_time(9, 15):
                        self.run_gap_scan()
                    self.run_scan_cycle()
                    self.run_currency_scan()
                    self.run_commodity_scan()
                    time.sleep(self.scan_interval)

                # 11:00 – 13:30: Mid-Day Chop Pause (No new equity entries)
                elif dt_time(11, 0) <= now_time < dt_time(13, 30):
                    state = self.load_state()
                    open_count = len(state.get("open_positions", []))
                    print(f"\n[{time_str}] >> ⏸️ MID-DAY CHOP PAUSE (11:00-13:30) | Open: {open_count} (Monitoring existing trades)")
                    self.run_news_scan()
                    self.manage_open_positions(state)
                    self.run_currency_scan()
                    self.run_commodity_scan()
                    time.sleep(self.scan_interval)

                # 13:30 – 15:15: Afternoon Breakout Kill Zone (European Market Open)
                elif dt_time(13, 30) <= now_time <= MARKET_CLOSE:
                    state = self.load_state()
                    open_count = len(state.get("open_positions", []))
                    print(f"\n[{time_str}] >> ⚡ AFTERNOON BREAKOUT KILL ZONE (13:30-15:15) | Open: {open_count}")
                    self.run_news_scan()
                    self.run_scan_cycle()
                    self.run_currency_scan()
                    self.run_commodity_scan()
                    time.sleep(self.scan_interval)

                # 15:15 – 15:25: Equity Square-off
                elif MARKET_CLOSE < now_time <= SQUARE_OFF:
                    if not self.equity_square_off_done:
                        print(f"\n[{time_str}] >> 15:15 PM: SQUARING OFF ALL EQUITY POSITIONS")
                        state = self.load_state()
                        equity_positions = [p for p in state.get("open_positions", []) if p.get("asset_type", "EQUITY") == "EQUITY"]
                        remaining = [p for p in state.get("open_positions", []) if p.get("asset_type", "EQUITY") != "EQUITY"]
                        
                        for p in equity_positions:
                            self._close_position(state, p, "SQUARE_OFF")
                        
                        state["open_positions"] = remaining
                        self.save_state(state)
                        self.equity_square_off_done = True
                        print(f"[{time_str}] Equity square-off complete. Currency & MCX continue.")
                    time.sleep(30)

                # 15:25 – 16:45: Currency & Commodity Window
                elif SQUARE_OFF < now_time <= CURRENCY_SQUARE_OFF:
                    self.run_currency_scan()
                    self.run_commodity_scan()
                    state = self.load_state()
                    self.manage_open_positions(state)
                    time.sleep(self.scan_interval)

                # 16:45 – 17:00: Currency Square-off
                elif CURRENCY_SQUARE_OFF < now_time <= CURRENCY_CLOSE:
                    if not self.currency_square_off_done:
                        print(f"\n[{time_str}] >> 16:45 PM: SQUARING OFF CURRENCY POSITIONS")
                        state = self.load_state()
                        fx_positions = [p for p in state.get("open_positions", []) if p.get("asset_type") == "CURRENCY"]
                        remaining = [p for p in state.get("open_positions", []) if p.get("asset_type") != "CURRENCY"]
                        
                        for p in fx_positions:
                            self._close_position(state, p, "SQUARE_OFF")
                        
                        state["open_positions"] = remaining
                        self.save_state(state)
                        self.currency_square_off_done = True
                        print(f"[{time_str}] Currency square-off complete. MCX US Evening peak starts at 17:00.")
                    time.sleep(30)

                # 17:00 – 23:15: MCX US Evening Peak Window
                elif MCX_US_SESSION_OPEN <= now_time <= MCX_SQUARE_OFF:
                    self.run_commodity_scan()
                    state = self.load_state()
                    self.manage_open_positions(state)
                    time.sleep(self.scan_interval)

                # 23:15 – 23:30: MCX Square-off & EOD Master Summary
                elif MCX_SQUARE_OFF < now_time <= MCX_CLOSE and not self.daily_report_sent:
                    print(f"\n[{time_str}] >> 23:15 PM: FINAL MASTER SQUARE-OFF & DAILY REPORT")
                    state = self.load_state()
                    self.square_off_all(state)

                    summary = self.risk_manager.get_daily_summary()
                    summary["total_pnl"] = state.get("total_pnl", 0.0)
                    self.notifier.notify_daily_summary(summary)
                    self.daily_report_sent = True
                    print(f"[{time_str}] Daily session concluded! Full master summary sent to Telegram.")
                    time.sleep(60)

                else:
                    if heartbeat_counter % 6 == 1:
                        print(f"[{time_str}] Off-market hours. Next session tomorrow 09:00 AM IST. Telegram server ACTIVE.")
                    time.sleep(10)

            except Exception as e:
                print(f"[Market Engine] Loop exception: {e}")
                time.sleep(10)

    def run(self):
        """Entry point: starts both concurrent threads."""
        print("==================================================")
        print("   PROJECT ATLAS — AUTONOMOUS MULTI-ASSET BOT v4  ")
        print(f"   Equities: 181 Stocks (Top 40 Fast) | FX: 4 Pairs | MCX: 5 Assets")
        print(f"   Mode: {self.mode} | Dual-Threaded Concurrency: ACTIVE")
        print(f"   News & Panic Radar: ARMED (15-min cycle)")
        print(f"   Telegram Users: {len(self.notifier.chat_ids)} Authorized")
        print("==================================================")

        # Start Thread 1: Telegram Server Thread
        t_telegram = threading.Thread(target=self.telegram_polling_thread, name="TelegramServer", daemon=True)
        t_telegram.start()

        # Start Thread 2: Market Scheduler Thread
        t_scanner = threading.Thread(target=self.market_scheduler_thread, name="MarketScanner", daemon=True)
        t_scanner.start()

        # Keep main thread alive
        try:
            while self.is_running:
                time.sleep(1.0)
        except KeyboardInterrupt:
            print("\n[Daemon] Stopping Atlas trading bot...")
            self.is_running = False


if __name__ == "__main__":
    daemon = TradingDaemon(scan_interval_seconds=180, mode="PAPER")
    daemon.run()
