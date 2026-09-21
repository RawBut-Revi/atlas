"""
Tests for paper-fill realism (slippage + gap-through stop fills).
Pure-function tests only: no state file access.
"""
import sys
import unittest
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

from trading.fills import entry_fill, exit_fill, rebase_levels, price_decimals, SLIPPAGE_BPS, MAX_ENTRY_DRIFT_PCT


class TestEntryFill(unittest.TestCase):
    def test_buy_fills_above_signal(self):
        self.assertGreater(entry_fill("BUY", 1000.0, "EQUITY"), 1000.0)

    def test_sell_fills_below_signal(self):
        self.assertLess(entry_fill("SELL", 1000.0, "EQUITY"), 1000.0)

    def test_equity_slippage_is_five_bps(self):
        self.assertAlmostEqual(entry_fill("BUY", 1000.0, "EQUITY"), 1000.5, places=2)

    def test_currency_keeps_four_decimals(self):
        # JPYINR ~0.61: 2-decimal rounding would erase the slippage entirely
        fill = entry_fill("BUY", 0.6124, "CURRENCY")
        self.assertGreater(fill, 0.6124)
        self.assertEqual(price_decimals("CURRENCY"), 4)

    def test_unknown_asset_has_no_slippage(self):
        self.assertEqual(entry_fill("BUY", 100.0, "OTHER"), 100.0)


class TestExitFill(unittest.TestCase):
    def test_target_fills_exactly_at_level(self):
        self.assertEqual(exit_fill("BUY", 1050.0, 1055.0, "EQUITY", "TARGET"), 1050.0)
        self.assertEqual(exit_fill("SELL", 950.0, 940.0, "EQUITY", "TARGET"), 950.0)

    def test_buy_stop_gap_through_fills_at_worse_live_price(self):
        # BUY stop at 1000, price gapped down to 980: fill near 980, not 1000
        fill = exit_fill("BUY", 1000.0, 980.0, "EQUITY", "STOP")
        self.assertLess(fill, 980.0)

    def test_sell_stop_gap_through_fills_at_worse_live_price(self):
        fill = exit_fill("SELL", 1000.0, 1020.0, "EQUITY", "STOP")
        self.assertGreater(fill, 1020.0)

    def test_buy_stop_touched_without_gap_fills_below_level(self):
        # live price still above the stop: fill at the level minus slippage
        fill = exit_fill("BUY", 1000.0, 1001.0, "EQUITY", "STOP")
        self.assertAlmostEqual(fill, 999.5, places=2)

    def test_sell_stop_touched_without_gap_fills_above_level(self):
        fill = exit_fill("SELL", 1000.0, 999.0, "EQUITY", "STOP")
        self.assertAlmostEqual(fill, 1000.5, places=2)

    def test_market_close_of_buy_is_worse_than_live(self):
        self.assertLess(exit_fill("BUY", 0.0, 1000.0, "EQUITY", "MARKET"), 1000.0)

    def test_market_close_of_sell_is_worse_than_live(self):
        self.assertGreater(exit_fill("SELL", 0.0, 1000.0, "EQUITY", "MARKET"), 1000.0)

    def test_slippage_config_covers_all_traded_assets(self):
        for asset in ("EQUITY", "CURRENCY", "COMMODITY"):
            self.assertGreater(SLIPPAGE_BPS[asset], 0.0)


class TestRebaseLevels(unittest.TestCase):
    LEVELS = {"stop_loss": 990.0, "target_price": 1030.0}

    def test_entry_fills_at_live_price_not_stale_signal(self):
        out = rebase_levels("BUY", 1000.0, 1010.0, self.LEVELS, "EQUITY")
        self.assertAlmostEqual(out["entry_price"], 1010.505, places=2)

    def test_levels_shift_by_same_amount_preserving_distances(self):
        out = rebase_levels("BUY", 1000.0, 1010.0, self.LEVELS, "EQUITY")
        self.assertAlmostEqual(out["entry_price"] - out["stop_loss"], 1000.0 - 990.0, places=1)
        self.assertAlmostEqual(out["target_price"] - out["entry_price"], 1030.0 - 1000.0, places=1)

    def test_no_live_price_keeps_signal_levels(self):
        out = rebase_levels("SELL", 1000.0, 0.0, self.LEVELS, "EQUITY")
        self.assertAlmostEqual(out["entry_price"], 999.5, places=2)  # signal price minus slippage

    def test_stale_signal_beyond_drift_is_rejected(self):
        self.assertIsNone(rebase_levels("BUY", 1000.0, 1020.0, self.LEVELS, "EQUITY"))  # 2% drift
        self.assertIsNone(rebase_levels("BUY", 1000.0, 980.0, self.LEVELS, "EQUITY"))

    def test_drift_at_limit_is_accepted(self):
        self.assertIsNotNone(rebase_levels("BUY", 1000.0, 1000.0 * (1 + MAX_ENTRY_DRIFT_PCT / 100.0), self.LEVELS, "EQUITY"))


class TestDaemonExitWiring(unittest.TestCase):
    """manage_open_positions must use exit_fill, and must never touch paper_positions.json."""

    def _daemon(self, live_price):
        from unittest.mock import MagicMock
        from trading.daemon import TradingDaemon
        d = TradingDaemon.__new__(TradingDaemon)  # skip __init__: no notifier / state file
        d.notifier = MagicMock()
        d.save_state = MagicMock()
        d.get_live_price = lambda *a, **k: live_price
        return d

    @staticmethod
    def _buy_position():
        return {
            "id": "t1", "symbol": "SBIN", "direction": "BUY", "qty": 18, "entry_price": 1047.5,
            "stop_loss": 1036.9, "target_price": 1063.4, "asset_type": "EQUITY",
            "sl_trailed_to_cost": True, "t1_booked": True, "status": "OPEN",
            "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def test_gap_through_stop_fills_at_live_price_not_stop_level(self):
        d = self._daemon(live_price=1020.0)  # gapped 16.9 below the 1036.9 stop
        state = {"open_positions": [self._buy_position()], "trade_history": [], "total_pnl": 0.0}
        d.manage_open_positions(state)
        closed = state["trade_history"][0]
        self.assertEqual(closed["status"], "TRAILING_SL_HIT")
        self.assertLess(closed["exit_price"], 1020.0)
        self.assertEqual(state["open_positions"], [])

    def test_target_still_fills_exactly_at_target(self):
        d = self._daemon(live_price=1070.0)
        state = {"open_positions": [self._buy_position()], "trade_history": [], "total_pnl": 0.0}
        d.manage_open_positions(state)
        closed = state["trade_history"][0]
        self.assertEqual(closed["status"], "TAKE_PROFIT")
        self.assertEqual(closed["exit_price"], 1063.4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
