"""
Project Atlas — Multi-User Telegram Bot Notifier & Command Center
Allows multiple authorized users to monitor, receive alerts, and control the trading bot.
Persists whitelist to disk so authorized friends are never lost on restart.
"""

import os
import json
import requests
import time
from datetime import datetime
from typing import Callable, Optional, List

USERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "authorized_users.json")


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
        self.chat_ids: list[str] = self._load_authorized_users(chat_id)
        self.last_update_id = 0
        self.is_enabled = bool(self.token and len(self.chat_ids) > 0)

    def _load_authorized_users(self, override_chat_id: Optional[str] = None) -> list[str]:
        """Loads whitelist from JSON file and .env."""
        users = set()
        
        # 1. From authorized_users.json
        if os.path.exists(USERS_FILE):
            try:
                with open(USERS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        for u in data:
                            if str(u).strip():
                                users.add(str(u).strip())
            except Exception:
                pass

        # 2. From .env / override
        raw_env = override_chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
        for cid in raw_env.replace(",", " ").split():
            if cid.strip():
                users.add(cid.strip())

        # Always include default authorized users
        users.add("2095090861")
        users.add("1321498518")

        user_list = sorted(list(users))
        self._save_authorized_users(user_list)
        return user_list

    def _save_authorized_users(self, user_list: list[str]):
        """Persists whitelist to disk."""
        try:
            with open(USERS_FILE, "w", encoding="utf-8") as f:
                json.dump(user_list, f, indent=2)
        except Exception:
            pass

    def add_authorized_user(self, new_chat_id: str) -> bool:
        """Dynamically whitelists a new user and saves to disk permanently."""
        new_chat_id = str(new_chat_id).strip()
        if new_chat_id and new_chat_id not in self.chat_ids:
            self.chat_ids.append(new_chat_id)
            self._save_authorized_users(self.chat_ids)
            os.environ["TELEGRAM_CHAT_ID"] = ",".join(self.chat_ids)
            self.is_enabled = True
            return True
        return False

    def remove_authorized_user(self, chat_id: str) -> bool:
        """Removes a user from whitelist."""
        chat_id = str(chat_id).strip()
        if chat_id in self.chat_ids:
            self.chat_ids.remove(chat_id)
            self._save_authorized_users(self.chat_ids)
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
                resp = requests.post(url, json=payload, timeout=6)
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
        qty_str = f"{trade.get('lots', trade.get('qty', 1))} lot(s)" if trade.get("asset_type") in ("CURRENCY", "COMMODITY") else f"{trade.get('qty', 10)}x"

        msg = (
            f"⚡ <b>TRADE EXECUTED {mode_tag}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"{action} <b>{qty_str} {trade.get('symbol')}</b> @ ₹{trade.get('entry_price')}\n"
            f"🎯 Target: ₹{trade.get('target_price')} | 🛑 SL: ₹{trade.get('stop_loss')}\n"
            f"⏰ Time: {trade.get('entry_time', datetime.now().strftime('%H:%M:%S'))}"
        )
        self.send_message(msg)

    def notify_trade_closed(self, trade: dict):
        """Broadcast trade exit & P&L alert with real-world tax breakdown."""
        gross_pnl = trade.get("gross_pnl", trade.get("pnl", 0.0))
        charges = trade.get("charges", 0.0)
        net_pnl = trade.get("net_pnl", gross_pnl - charges)
        
        is_win = net_pnl > 0
        is_scratch = abs(gross_pnl) < 0.01 and charges == 0.0

        if is_win:
            icon = "🎉 <b>WINNER TAKE-PROFIT</b>"
        elif is_scratch:
            icon = "⚪ <b>POSITION SQUARED-OFF</b>"
        else:
            icon = "🛑 <b>TRADE CLOSED</b>" if gross_pnl >= 0 else "🛑 <b>STOP-LOSS HIT</b>"

        gross_str = f"+₹{gross_pnl:.2f}" if gross_pnl >= 0 else f"-₹{abs(gross_pnl):.2f}"
        net_str = f"+₹{net_pnl:.2f}" if net_pnl >= 0 else f"-₹{abs(net_pnl):.2f}"

        charges_line = f"🧾 <b>Taxes & Fees:</b> -₹{charges:.2f}\n" if charges > 0 else ""

        msg = (
            f"{icon}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>{trade.get('symbol')}</b> ({trade.get('direction')})\n"
            f"💵 <b>Exit:</b> ₹{trade.get('exit_price')} (Entry: ₹{trade.get('entry_price')})\n"
            f"💰 <b>Gross Profit:</b> <code>{gross_str}</code>\n"
            f"{charges_line}"
            f"💵 <b>Net Take-Home:</b> <code>{net_str}</code>\n"
            f"⏰ Exit Time: {trade.get('exit_time')}"
        )
        self.send_message(msg)

    def notify_daily_summary(self, summary: dict):
        """Broadcast end of day summary report with gross & net P&L."""
        net_pnl = summary.get("total_net_pnl", summary.get("total_pnl", 0.0))
        gross_pnl = summary.get("total_gross_pnl", net_pnl)
        total_charges = summary.get("total_charges_paid", 0.0)

        pnl_emoji = "🟢" if net_pnl >= 0 else "🔴"
        net_str = f"+₹{net_pnl:.2f}" if net_pnl >= 0 else f"-₹{abs(net_pnl):.2f}"
        gross_str = f"+₹{gross_pnl:.2f}" if gross_pnl >= 0 else f"-₹{abs(gross_pnl):.2f}"

        msg = (
            f"📊 <b>DAILY REAL-WORLD P&L REPORT</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📅 <b>Date:</b> {datetime.now().strftime('%d %b %Y')}\n"
            f"🔢 <b>Total Trades:</b> {summary.get('total_trades', 0)}\n"
            f"✅ <b>Wins:</b> {summary.get('wins', 0)} | ❌ <b>Losses:</b> {summary.get('losses', 0)}\n"
            f"🎯 <b>Win Rate:</b> {summary.get('win_rate', 0)}%\n"
            f"💰 <b>Gross P&L:</b> <code>{gross_str}</code>\n"
            f"🧾 <b>Brokerage & Taxes:</b> <code>-₹{total_charges:.2f}</code>\n"
            f"{pnl_emoji} <b>Net Realized P&L:</b> <code>{net_str}</code>\n"
            f"💼 <b>Closing Capital:</b> ₹{summary.get('capital', 10000) + net_pnl:,.2f}"
        )
        self.send_message(msg)

    def check_incoming_commands(self, command_handler: Callable[[str, str], str]):
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
                                f"✅ User <code>{new_id}</code> permanently authorized!\nWhitelist saved to disk.",
                                target_chat_id=sender_id,
                            )
                            self.send_message(
                                "🎉 <b>Welcome to Project Atlas Trading Bot!</b>\nYou are now authorized to monitor and chat with the bot.\nSend /status or /positions to get started.",
                                target_chat_id=new_id,
                            )
                        else:
                            self.send_message("❌ Only authorized admins can add new users.", target_chat_id=sender_id)
                    continue

                # Admin command: /users
                if text == "/users":
                    if sender_id in self.chat_ids:
                        lines = ["👥 <b>AUTHORIZED USERS WHITELIST</b>\n━━━━━━━━━━━━━━━━━━━"]
                        for i, uid in enumerate(self.chat_ids, 1):
                            lines.append(f"{i}. <code>{uid}</code>")
                        self.send_message("\n".join(lines), target_chat_id=sender_id)
                    continue

                # Authorized User Check
                if sender_id in self.chat_ids:
                    reply = command_handler(text, sender_id)
                    if reply:
                        self.send_message(reply, target_chat_id=sender_id)
                else:
                    # Unauthorized user helper
                    sender_name = msg.get("from", {}).get("first_name", "Trader")
                    help_msg = (
                        f"🔒 <b>Access Restricted</b>\n\n"
                        f"Hello {sender_name}!\n"
                        f"Your Telegram Chat ID is: <code>{sender_id}</code>\n\n"
                        f"Please ask your friend (the bot admin) to run:\n"
                        f"<code>/adduser {sender_id}</code>\n\n"
                        f"Once added, you can chat with the bot and receive all real-time market alerts!"
                    )
                    self.send_message(help_msg, target_chat_id=sender_id)

        except Exception:
            pass
