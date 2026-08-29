"""
Project Atlas — Telegram Channel Verification Script
Run this script to test if your Telegram Bot is connected to your phone.
"""

import sys
import os
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from trading.telegram_bot import TelegramNotifier

def test_telegram():
    print("=== TELEGRAM BOT CONNECTION TEST ===")
    bot = TelegramNotifier()
    
    print(f"Bot Token: {bot.token[:10]}...{bot.token[-5:]}" if bot.token else "Bot Token: NOT FOUND")
    print(f"Chat ID:   {bot.chat_id}")
    
    if not bot.is_enabled:
        print("\n❌ Error: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing in .env")
        return

    # Check bot info
    try:
        r = requests.get(f"https://api.telegram.org/bot{bot.token}/getMe", timeout=5)
        bot_info = r.json()
        if bot_info.get("ok"):
            bot_username = bot_info["result"]["username"]
            print(f"Bot Name:  @{bot_username} (Verified)")
        else:
            print(f"❌ Invalid Bot Token: {bot_info}")
            return
    except Exception as e:
        print(f"❌ Connection error: {e}")
        return

    # Send test message
    msg = (
        "🚀 <b>Project Atlas Trading Bot Connected!</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "✅ <i>Telegram notification channel verified successfully.</i>\n\n"
        "📱 <b>Available Mobile Commands:</b>\n"
        "• <code>/status</code> — View bot uptime & scan count\n"
        "• <code>/positions</code> — View active open trades\n"
        "• <code>/pnl</code> — View today's realized profit/loss\n"
        "• <code>/scan</code> — Trigger an immediate market scan\n\n"
        "⚡ <i>Ready for live market paper trading!</i>"
    )
    
    url = f"https://api.telegram.org/bot{bot.token}/sendMessage"
    payload = {"chat_id": bot.chat_id, "text": msg, "parse_mode": "HTML"}
    
    resp = requests.post(url, json=payload, timeout=8)
    data = resp.json()
    
    if data.get("ok"):
        print("\n[SUCCESS] Test message delivered to your Telegram app!")
        print("Check your phone now!")
    else:
        desc = data.get('description', '')
        print(f"\n[Telegram Response]: {desc}")
        if "chat not found" in desc.lower():
            print(f"\n[ACTION REQUIRED]: Open Telegram on your phone, search for @{bot_username}, and tap START or send 'hi'.")
            print("Then run this test again!")

if __name__ == "__main__":
    test_telegram()
