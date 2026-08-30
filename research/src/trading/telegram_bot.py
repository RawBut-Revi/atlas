"""
Project Atlas — Multi-User Telegram Bot Notifier & Command Center
Allows multiple authorized users to monitor, receive alerts, and control the trading bot.
"""

import os
import requests
import time
from datetime import datetime
from typing import Callable, Optional, List


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


class TelegramNotifier:
    """Handles multi-user push notifications and command routing."""

    def __init__(self, token: str = None, chat_id: str = None):
        _load_env_file()
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        raw_chat_ids = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
        
        # Parse comma-separated or space-separated list of Chat IDs
        self.chat_ids: list[str] = [
            cid.strip() for cid in raw_chat_ids.replace(",", " ").split() if cid.strip()
        ]
        self.last_update_id = 0
        self.is_enabled = bool(self.token and len(self.chat_ids) > 0)

    def add_authorized_user(self, new_chat_id: str) -> bool:
        """Dynamically whitelists a new user chat ID."""
        new_chat_id = str(new_chat_id).strip()
        if new_chat_id and new_chat_id not in self.chat_ids:
            self.chat_ids.append(new_chat_id)
            # Update environment variable
            os.environ["TELEGRAM_CHAT_ID"] = ",".join(self.chat_ids)
            return True
        return False

    def send_message(self, text: str, parse_mode: str = "HTML", target_chat_id: Optional[str] = None) -> bool:
        """
        Sends a message to Telegram.
        If target_chat_id is specified, sends only to that user.
        If target_chat_id is None, broadcasts to ALL authorized users.
        """
        if not self.token:
            return False

        recipients = [target_chat_id] if target_chat_id else self.chat_ids
        if not recipients:
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        success = True

        for cid in recipients:
            if not cid:
                continue
            payload = {
                "chat_id": cid,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }
            try:
                resp = requests.post(url, json=payload, timeout=8)
                if resp.status_code != 200:
                    success = False
            except Exception as e:
                print(f"[Telegram] Failed to send alert to {cid}: {e}")
                success = False

        return success

    def notify_signal_found(self, sig: dict):
        """Broadcast trade opportunity alert to all authorized users."""
        icon = "🟢 <b>BUY SETUP FOUND</b>" if sig.get("direction") == "BUY" else "🔴 <b>SELL SETUP FOUND</b>"
        timeframe_tag = f"[{sig.get('timeframe', '3H')}]" if 'timeframe' in sig else ""
        pattern_line = f"🕯️ <b>3H Pattern:</b> {sig.get('pattern_3h')}\n" if sig.get("pattern_3h") else ""

        msg = (
            f"{icon} {timeframe_tag}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📈 <b>Asset:</b> #{sig.get('symbol')}\n"
            f"🎯 <b>Confidence:</b> {sig.get('confidence')}%\n"
            f"💰 <b>Entry:</b> ₹{sig.get('entry_price')}\n"
            f"🛑 <b>Stop Loss:</b> ₹{sig.get('stop_loss')}\n"
            f"🎯 <b>Target:</b> ₹{sig.get('target_price')} (1:1.5 R:R)\n"
            f"📦 <b>Suggested Qty/Lots:</b> {sig.get('suggested_qty', sig.get('lots', 10))}\n"
            f"{pattern_line}"
            f"💡 <i>{sig.get('rationale', '')}</i>\n"
            f"⏰ <i>{datetime.now().strftime('%H:%M:%S IST')}</i>"
        )
        self.send_message(msg)

    def notify_trade_executed(self, trade: dict):
        """Broadcast trade execution alert to all authorized users."""
        mode_tag = f"[{trade.get('mode', 'PAPER')}]"
        action = "🟢 BOUGHT" if trade.get("direction") == "BUY" else "🔴 SOLD"
        qty_str = f"{trade.get('lots')} lot(s)" if trade.get("asset_type") in ("CURRENCY", "COMMODITY") else f"{trade.get('qty')}x"

        msg = (
            f"⚡ <b>TRADE EXECUTED {mode_tag}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"{action} <b>{qty_str} {trade.get('symbol')}</b> @ ₹{trade.get('entry_price')}\n"
            f"🎯 Target: ₹{trade.get('target_price')} | 🛑 SL: ₹{trade.get('stop_loss')}\n"
            f"⏰ Time: {trade.get('entry_time', datetime.now().strftime('%H:%M:%S'))}"
        )
        self.send_message(msg)

    def notify_trade_closed(self, trade: dict):
        """Broadcast trade exit & P&L alert to all authorized users."""
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
        """Broadcast end of day summary report to all authorized users."""
        pnl = summary.get("total_pnl", 0.0)
        pnl_emoji = "🟢" if pnl >= 0 else "🔴"
        pnl_str = f"+₹{pnl:.2f}" if pnl >= 0 else f"-₹{abs(pnl):.2f}"

        msg = (
            f"📊 <b>DAILY MULTI-ASSET SUMMARY</b>\n"
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
        """
        Polls for commands from any user.
        - If the sender is authorized, runs the command and replies directly to them.
        - If unauthorized, returns their Chat ID so the admin can whitelist them.
        """
        if not self.token:
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

                if not text.startswith("/") or not sender_id:
                    continue

                # Admin command: /adduser <chat_id>
                if text.startswith("/adduser"):
                    parts = text.split()
                    if len(parts) >= 2:
                        new_id = parts[1].strip()
                        if sender_id in self.chat_ids:
                            self.add_authorized_user(new_id)
                            self.send_message(
                                f"✅ User <code>{new_id}</code> has been authorized!\nThey can now monitor and chat with the bot.",
                                target_chat_id=sender_id,
                            )
                            self.send_message(
                                "🎉 <b>Welcome to Project Atlas Trading Bot!</b>\nYou are now authorized to monitor and chat with the bot.\nSend /status or /patterns to get started.",
                                target_chat_id=new_id,
                            )
                        else:
                            self.send_message("❌ Only authorized admins can add new users.", target_chat_id=sender_id)
                    continue

                # Authorized User Check
                if sender_id in self.chat_ids:
                    reply = command_handler(text)
                    if reply:
                        self.send_message(reply, target_chat_id=sender_id)
                else:
                    # Unauthorized user helper
                    sender_name = msg.get("from", {}).get("first_name", "Friend")
                    help_msg = (
                        f"🔒 <b>Access Restricted</b>\n\n"
                        f"Hello {sender_name}!\n"
                        f"Your Telegram Chat ID is: <code>{sender_id}</code>\n\n"
                        f"Please ask your friend (the bot admin) to run:\n"
                        f"<code>/adduser {sender_id}</code>\n\n"
                        f"Once added, you can chat with the bot and receive all real-time market alerts!"
                    )
                    self.send_message(help_msg, target_chat_id=sender_id)

        except Exception as e:
            pass
