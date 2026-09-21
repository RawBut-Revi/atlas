"""
Tests for the stale-position guard in TradingDaemon.manage_open_positions.
Daemon is built without __init__ and save_state/notifier are mocked: paper_positions.json is never touched.
"""
import sys
import unittest
from datetime import datetime
from unittest.mock import MagicMock

sys.stdout.reconfigure(encoding="utf-8")

from trading.daemon import TradingDaemon


def _daemon(live_price):
    d = TradingDaemon.__new__(TradingDaemon)
    d.notifier = MagicMock()
    d.save_state = MagicMock()
    d.get_live_price = lambda *a, **k: live_price
    return d


def _position(symbol, entry_time, asset_type="COMMODITY"):
    return {
        "id": symbol, "symbol": symbol, "direction": "BUY", "qty": 1, "lots": 1, "entry_price": 90109.8,
        "stop_loss": 87960.26, "target_price": 93334.11, "asset_type": asset_type,
        "entry_time": entry_time, "status": "OPEN",
    }


class TestStalePositions(unittest.TestCase):
    def test_position_from_earlier_day_is_closed_as_stale(self):
        d = _daemon(live_price=90500.0)
        state = {"open_positions": [_position("SILVERMIC", "2026-09-07 09:02:26")], "trade_history": [], "total_pnl": 0.0}
        d.manage_open_positions(state, asset_filter="COMMODITY")
        self.assertEqual(state["open_positions"], [])
        self.assertEqual(state["trade_history"][0]["status"], "STALE_SQUARE_OFF")

    def test_position_opened_today_is_kept(self):
        d = _daemon(live_price=90500.0)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        state = {"open_positions": [_position("SILVERMIC", now)], "trade_history": [], "total_pnl": 0.0}
        d.manage_open_positions(state, asset_filter="COMMODITY")
        self.assertEqual(len(state["open_positions"]), 1)
        self.assertEqual(state["trade_history"], [])

    def test_other_asset_stale_position_untouched_by_filter(self):
        d = _daemon(live_price=90500.0)
        state = {"open_positions": [_position("TCS", "2026-09-07 09:20:00", "EQUITY")], "trade_history": [], "total_pnl": 0.0}
        d.manage_open_positions(state, asset_filter="COMMODITY")
        self.assertEqual(len(state["open_positions"]), 1)  # equity thread will handle it


if __name__ == "__main__":
    unittest.main(verbosity=2)
