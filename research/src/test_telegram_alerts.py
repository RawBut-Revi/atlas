"""
Tests for Telegram alert modes (normal / quiet / mute). requests.post is mocked and the notifier is built
without __init__, so the real whitelist file, alert_mode.json and the network are never touched.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.stdout.reconfigure(encoding="utf-8")

import trading.telegram_bot as tb
from trading.telegram_bot import TelegramNotifier


def make_notifier(mode="quiet", chat_ids=("111", "222")):
    n = TelegramNotifier.__new__(TelegramNotifier)
    n.token = "TEST_TOKEN"
    n.chat_ids = list(chat_ids)
    n.alert_mode = mode
    n.last_update_id = 0
    return n


def ok_response():
    r = MagicMock()
    r.status_code = 200
    return r


def sent_payloads(post):
    return [c.kwargs["json"] for c in post.call_args_list]


SIG = {"symbol": "USDINR", "direction": "BUY", "entry_price": 90.0, "stop_loss": 89.5, "target_price": 91.0}
TRADE = {**SIG, "asset_type": "CURRENCY", "lots": 1, "mode": "PAPER", "entry_time": "2026-09-19 10:00:00"}
CLOSED = {**TRADE, "exit_price": 91.0, "exit_time": "2026-09-19 11:00:00", "gross_pnl": 1000.0,
          "charges": 60.0, "net_pnl": 940.0}
SUMMARY = {"total_trades": 4, "wins": 3, "losses": 1, "win_rate": 75.0, "total_gross_pnl": 1200.0,
           "total_charges_paid": 240.0, "total_net_pnl": 960.0, "closing_capital": 150960.0,
           "by_asset": {"CURRENCY": {"trades": 3, "wins": 3, "losses": 0, "net": 1100.0},
                        "COMMODITY": {"trades": 1, "wins": 0, "losses": 1, "net": -140.0}}}


class TestSilentDelivery(unittest.TestCase):
    def test_default_is_loud(self):
        with patch.object(tb.requests, "post", return_value=ok_response()) as post:
            make_notifier().send_message("hi")
        self.assertFalse(sent_payloads(post)[0]["disable_notification"])

    def test_silent_flag(self):
        with patch.object(tb.requests, "post", return_value=ok_response()) as post:
            make_notifier().send_message("hi", silent=True)
        self.assertTrue(all(p["disable_notification"] for p in sent_payloads(post)))


class TestModes(unittest.TestCase):
    def run_all_alerts(self, mode):
        n = make_notifier(mode)
        with patch.object(tb.requests, "post", return_value=ok_response()) as post:
            n.notify_signal_found(SIG)
            n.notify_trade_executed(TRADE)
            n.notify_trade_closed(CLOSED)
            n.notify_daily_summary(SUMMARY)
        return sent_payloads(post)

    def test_normal_sends_everything_with_sound(self):
        payloads = self.run_all_alerts("normal")
        self.assertEqual(len(payloads), 4 * 2)                      # 4 alerts x 2 recipients
        self.assertFalse(any(p["disable_notification"] for p in payloads))

    def test_quiet_is_silent_and_drops_setup_alerts(self):
        payloads = self.run_all_alerts("quiet")
        self.assertEqual(len(payloads), 3 * 2)                      # signal_found dropped
        self.assertTrue(all(p["disable_notification"] for p in payloads))
        self.assertFalse(any("SETUP FOUND" in p["text"] for p in payloads))

    def test_mute_sends_nothing(self):
        self.assertEqual(self.run_all_alerts("mute"), [])

    def test_command_replies_still_delivered_when_muted_and_are_not_silent(self):
        n = make_notifier("mute")
        with patch.object(tb.requests, "post", return_value=ok_response()) as post:
            n.send_message("reply", target_chat_id="111")
        payloads = sent_payloads(post)
        self.assertEqual(len(payloads), 1)
        self.assertFalse(payloads[0]["disable_notification"])

    def test_incoming_command_gets_a_reply_in_mute_mode(self):
        n = make_notifier("mute")
        update = {"result": [{"update_id": 5, "message": {"text": "/pnl", "chat": {"id": 111}}}]}
        get_resp = MagicMock(status_code=200)
        get_resp.json.return_value = update
        with patch.object(tb.requests, "get", return_value=get_resp), \
                patch.object(tb.requests, "post", return_value=ok_response()) as post:
            n.check_incoming_commands(lambda text, sender: f"handled {text}")
        payloads = sent_payloads(post)
        self.assertEqual([p["text"] for p in payloads], ["handled /pnl"])
        self.assertFalse(payloads[0]["disable_notification"])


class TestDailySummaryText(unittest.TestCase):
    def test_uses_closing_capital_and_lists_markets(self):
        n = make_notifier("normal", chat_ids=("111",))
        with patch.object(tb.requests, "post", return_value=ok_response()) as post:
            n.notify_daily_summary(SUMMARY)
        text = sent_payloads(post)[0]["text"]
        self.assertIn("Total Trades:</b> 4", text)
        self.assertIn("₹150,960.00", text)
        self.assertIn("Currency: 3 trades, net +₹1,100.00", text)
        self.assertIn("Commodity: 1 trade, net -₹140.00", text)

    def test_without_by_asset_still_renders(self):
        n = make_notifier("normal", chat_ids=("111",))
        slim = {k: v for k, v in SUMMARY.items() if k != "by_asset"}
        with patch.object(tb.requests, "post", return_value=ok_response()) as post:
            n.notify_daily_summary(slim)
        self.assertNotIn("By market", sent_payloads(post)[0]["text"])


class TestAlertModePersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "alert_mode.json")
        patcher = patch.object(tb, "ALERT_MODE_FILE", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_default_is_quiet_when_no_file(self):
        self.assertEqual(make_notifier()._load_alert_mode(), "quiet")
        self.assertEqual(tb.DEFAULT_ALERT_MODE, "quiet")

    def test_set_persists_and_reloads(self):
        n = make_notifier("normal")
        self.assertTrue(n.set_alert_mode("MUTE"))
        self.assertEqual(n.alert_mode, "mute")
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"mode": "mute"})
        self.assertEqual(make_notifier("normal")._load_alert_mode(), "mute")

    def test_unknown_mode_rejected_and_unchanged(self):
        n = make_notifier("quiet")
        self.assertFalse(n.set_alert_mode("shout"))
        self.assertEqual(n.alert_mode, "quiet")
        self.assertFalse(os.path.exists(self.path))

    def test_corrupt_file_falls_back_to_default(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(make_notifier("normal")._load_alert_mode(), "quiet")

    def test_invalid_stored_mode_falls_back_to_default(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"mode": "loudest"}, f)
        self.assertEqual(make_notifier("normal")._load_alert_mode(), "quiet")


if __name__ == "__main__":
    unittest.main(verbosity=2)
