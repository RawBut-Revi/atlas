"""
Project Atlas — Telegram Bot Notifier & Command Center
Allows monitoring and commanding the trading bot directly from your mobile phone via Telegram.
"""

import os
import requests
import time
from datetime import datetime
from typing import Callable, Optional

def _load_env_file():
    """Loads .env file from project directories if present."""
    search_dirs = [
        os.getcwd(),
        os.path.dirname(os.path.abspath(__file__)),
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ]
    for d in search_dirs:
        env_path = os.path.join(d, ".env")
        if os.path.isfile(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip("'\"")
                            if k not in os.environ:
                                os.environ[k] = v
            except Exception:
                pass

_load_env_file()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""


class TelegramNotifier:
    """Handles sending push notifications and alerts to your Telegram app."""

    def __init__(self, token: str = None, chat_id: str = None):
        _load_env_file()
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
        self.last_update_id = 0
        self.is_enabled = bool(self.token and self.chat_id)

    def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Sends a formatted message to your phone."""
        if not self.is_enabled:
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }

        try:
            resp = requests.post(url, json=payload, timeout=8)
            return resp.status_code == 200
        except Exception as e:
            print(f"[Telegram] Failed to send alert: {e}")
            return False

    def notify_signal_found(self, sig: dict):
        """Send trade opportunity alert."""
        icon = "🟢 <b>BUY SETUP FOUND</b>" if sig.get("direction") == "BUY" else "🔴 <b>SELL SETUP FOUND</b>"
        msg = (
            f"{icon}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📈 <b>Stock:</b> #{sig.get('symbol')}\n"
            f"🎯 <b>Confidence:</b> {sig.get('confidence')}%\n"
            f"💰 <b>Entry:</b> ₹{sig.get('entry_price')}\n"
            f"🛑 <b>Stop Loss:</b> ₹{sig.get('stop_loss')}\n"
            f"🎯 <b>Target:</b> ₹{sig.get('target_price')} (1:1.5 R:R)\n"
            f"📦 <b>Suggested Qty:</b> {sig.get('suggested_qty', 10)}\n"
            f"💡 <i>{sig.get('rationale', '')}</i>\n"
            f"⏰ <i>{datetime.now().strftime('%H:%M:%S IST')}</i>"
        )
        self.send_message(msg)

    def notify_trade_executed(self, trade: dict):
        """Send trade execution alert."""
        mode_tag = f"[{trade.get('mode', 'PAPER')}]"
        action = "🟢 BOUGHT" if trade.get("direction") == "BUY" else "🔴 SOLD"
        msg = (
            f"⚡ <b>TRADE EXECUTED {mode_tag}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"{action} <b>{trade.get('qty')}x {trade.get('symbol')}</b> @ ₹{trade.get('entry_price')}\n"
            f"🎯 Target: ₹{trade.get('target_price')} | 🛑 SL: ₹{trade.get('stop_loss')}\n"
            f"⏰ Time: {trade.get('entry_time', datetime.now().strftime('%H:%M:%S'))}"
        )
        self.send_message(msg)

    def notify_trade_closed(self, trade: dict):
        """Send trade exit & P&L alert."""
        pnl = trade.get("pnl", 0.0)
        is_win = pnl >= 0
        icon = "🎉 <b>WINNER TAKE-PROFIT</b>" if is_win else "🛑 <b>STOP-LOSS HIT</b>"
        pnl_str = f"+₹{pnl:.2f}" if is_win else f"-₹{abs(pnl):.2f}"
        
        msg = (
            f"{icon}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>{trade.get('symbol')}</b> ({trade.get('direction')})\n"
            f"💵 <b>Exit Price:</b> ₹{trade.get('exit_price')} (Entry: ₹{trade.get('entry_price')})\n"
            f"💰 <b>Realized P&L:</b> <code>{pnl_str}</code>\n"
            f"⏰ Exit Time: {trade.get('exit_time')}"
        )
        self.send_message(msg)

    def notify_daily_summary(self, summary: dict):
        """Send end of day summary report."""
        pnl = summary.get("total_pnl", 0.0)
        pnl_emoji = "🟢" if pnl >= 0 else "🔴"
        pnl_str = f"+₹{pnl:.2f}" if pnl >= 0 else f"-₹{abs(pnl):.2f}"

        msg = (
            f"📊 <b>DAILY TRADING SUMMARY</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📅 <b>Date:</b> {datetime.now().strftime('%d %b %Y')}\n"
            f"🔢 <b>Total Trades:</b> {summary.get('total_trades', 0)}\n"
            f"✅ <b>Wins:</b> {summary.get('wins', 0)} | ❌ <b>Losses:</b> {summary.get('losses', 0)}\n"
            f"🎯 <b>Win Rate:</b> {summary.get('win_rate', 0)}%\n"
            f"{pnl_emoji} <b>Net P&L:</b> <code>{pnl_str}</code>\n"
            f"💼 <b>Closing Capital:</b> ₹{summary.get('capital', 10000) + pnl:,.2f}"
        )
        self.send_message(msg)

    def check_incoming_commands(self, command_handler: Callable[[str], str]):
        """Polls for commands sent from your phone (e.g., /status, /pnl, /positions)."""
        if not self.is_enabled:
            return

        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        params = {"offset": self.last_update_id + 1, "timeout": 2}

        try:
            resp = requests.get(url, params=params, timeout=5)
            if resp.status_code != 200:
                return

            data = resp.json()
            for update in data.get("result", []):
                self.last_update_id = update["update_id"]
                msg = update.get("message", {})
                text = msg.get("text", "").strip()
                sender_id = str(msg.get("chat", {}).get("id", ""))

                # Only respond to authorized chat
                if sender_id == str(self.chat_id) and text.startswith("/"):
                    reply = command_handler(text)
                    if reply:
                        self.send_message(reply)

        except Exception as e:
            pass
