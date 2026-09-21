"""
Integration tests: live/shadow routing, shadow position management, and the /shadow command.
Daemon is built without __init__; ledger uses a temp file; notifier/save_state/load_state are mocked,
so paper_positions.json and shadow_ledger.json are never touched.
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import MagicMock

sys.stdout.reconfigure(encoding="utf-8")

from trading.daemon import TradingDaemon, ALLOWED_LIVE
from trading.fills import entry_fill
from trading.shadow import ShadowLedger


def _pos(pid, asset="CURRENCY", strategy="TREND_MOMENTUM", symbol="USDINR", direction="BUY"):
    return {
        "id": pid, "symbol": symbol, "direction": direction, "qty": 1, "lots": 1, "asset_type": asset,
        "strategy": strategy, "entry_price": entry_fill(direction, 95.0, asset),
        "stop_loss": 94.5, "target_price": 96.0, "target_1": 95.5, "target_2": 96.0,
        "rl_trail_tightness": 0.5, "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "status": "OPEN",
    }


class ShadowDaemonTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(self.path)
        self.d = TradingDaemon.__new__(TradingDaemon)
        self.d.shadow = ShadowLedger(self.path)
        self.d.notifier = MagicMock()
        self.d.save_state = MagicMock()
        self.d.get_live_price = lambda *a, **k: 95.0

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)


class TestRouting(ShadowDaemonTest):
    def test_allowed_pair_opens_live_and_records_only_inverse(self):
        self.assertTrue(self.d._route_entry(_pos("fx_1")))
        kinds = [t["shadow_kind"] for t in self.d.shadow.load()["open_positions"]]
        self.assertEqual(kinds, ["INVERSE"])

    def test_disallowed_pair_is_shadow_only_with_inverse(self):
        self.assertFalse(self.d._route_entry(_pos("mcx_1", asset="COMMODITY", strategy="3H_PATTERN_BREAKOUT", symbol="SILVERMIC")))
        kinds = sorted(t["shadow_kind"] for t in self.d.shadow.load()["open_positions"])
        self.assertEqual(kinds, ["INVERSE", "SHADOW"])

    def test_disabled_equity_strategies_are_not_live(self):
        for strategy in ("INTRADAY", "US_SESSION_MOMENTUM", "GAP_FADE"):
            self.assertNotIn(("EQUITY", strategy), ALLOWED_LIVE)
            self.assertNotIn(("COMMODITY", strategy), ALLOWED_LIVE)


class TestShadowManagement(ShadowDaemonTest):
    def test_inverse_twin_closes_on_its_target_without_touching_real_state_or_telegram(self):
        self.d._route_entry(_pos("fx_1", direction="BUY"))  # inverse = SELL, target 94.5, stop 96.0
        self.d.get_live_price = lambda *a, **k: 94.4  # price fell through the inverse target
        self.d._manage_shadow("CURRENCY")

        state = self.d.shadow.load()
        self.assertEqual(state["open_positions"], [])
        closed = state["trade_history"][0]
        self.assertEqual(closed["shadow_kind"], "INVERSE")
        self.assertEqual(closed["status"], "TAKE_PROFIT")
        self.assertGreater(closed["gross_pnl"], 0)
        self.d.notifier.notify_trade_closed.assert_not_called()
        self.d.save_state.assert_not_called()

    def test_shadow_square_off_closes_only_that_asset(self):
        self.d._route_entry(_pos("fx_1"))
        self.d._route_entry(_pos("mcx_1", asset="COMMODITY", strategy="3H_PATTERN_BREAKOUT", symbol="SILVERMIC"))
        self.d.shadow_square_off("COMMODITY")
        state = self.d.shadow.load()
        self.assertEqual({t["asset_type"] for t in state["open_positions"]}, {"CURRENCY"})
        self.assertTrue(all(t["asset_type"] == "COMMODITY" and t["status"] == "SQUARE_OFF" for t in state["trade_history"]))
        self.assertEqual(len(state["trade_history"]), 2)  # its INVERSE and SHADOW twins
        self.d.notifier.notify_trade_closed.assert_not_called()

    def test_real_manage_also_drives_shadow_even_with_no_real_positions(self):
        self.d._route_entry(_pos("fx_1", direction="BUY"))
        self.d.get_live_price = lambda *a, **k: 94.4
        self.d.manage_open_positions({"open_positions": [], "trade_history": []}, asset_filter="CURRENCY")
        self.assertEqual(self.d.shadow.load()["open_positions"], [])


class TestShadowCommand(ShadowDaemonTest):
    def _cmd(self, real_history):
        self.d.load_state = lambda: {"open_positions": [], "trade_history": real_history, "capital": 150000.0, "total_pnl": 0.0}
        self.d.calculate_margin_and_risk = lambda s: {}
        return self.d.handle_telegram_command("/shadow")

    def test_empty_ledger_explains_itself(self):
        out = self._cmd([])
        self.assertIn("SHADOW LEDGER", out)
        self.assertIn("No completed pairs yet", out)
        self.assertIn("CURRENCY TREND_MOMENTUM", out)

    def test_pair_is_reported_bot_vs_inverse(self):
        real = _pos("fx_1", direction="BUY")
        self.d._route_entry(real)
        self.d.get_live_price = lambda *a, **k: 94.4
        self.d._manage_shadow("CURRENCY")
        out = self._cmd([{**real, "net_pnl": 120.0}])
        self.assertIn("CURRENCY TREND_MOMENTUM", out)
        self.assertIn("[LIVE]", out)
        self.assertIn("Inverse", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
