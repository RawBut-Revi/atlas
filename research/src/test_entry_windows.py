"""Tests for per-asset entry windows. Pure functions, no state file access."""
import sys
import unittest
from datetime import time as dt_time

sys.stdout.reconfigure(encoding="utf-8")

from trading.entry_windows import entries_allowed


class TestEntryWindows(unittest.TestCase):
    def test_currency_blocked_at_open_seconds(self):
        # the 09:00:10 EURINR/USDINR entries that lost on stale data
        self.assertFalse(entries_allowed("CURRENCY", dt_time(9, 0, 10)))

    def test_currency_allowed_mid_session_blocked_late(self):
        self.assertTrue(entries_allowed("CURRENCY", dt_time(9, 10)))
        self.assertTrue(entries_allowed("CURRENCY", dt_time(12, 0)))
        self.assertFalse(entries_allowed("CURRENCY", dt_time(16, 0)))

    def test_equity_blocked_before_ten_and_near_close(self):
        self.assertFalse(entries_allowed("EQUITY", dt_time(9, 30)))
        self.assertTrue(entries_allowed("EQUITY", dt_time(10, 0)))
        self.assertFalse(entries_allowed("EQUITY", dt_time(15, 0)))

    def test_commodity_blocked_at_mcx_open_and_before_square_off(self):
        self.assertFalse(entries_allowed("COMMODITY", dt_time(9, 5)))
        self.assertTrue(entries_allowed("COMMODITY", dt_time(17, 44, 23)))  # the NATGASMINI entry
        self.assertFalse(entries_allowed("COMMODITY", dt_time(22, 45)))

    def test_unknown_asset_is_unrestricted(self):
        self.assertTrue(entries_allowed("OTHER", dt_time(3, 0)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
