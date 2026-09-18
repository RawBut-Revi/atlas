"""
Tests for the Phase 4 Task 1 charges-aware trade filter.
Pure-function tests plus one wiring test on TradingDaemon (no __init__, no state file access).
"""
import sys
import io
import unittest
from contextlib import redirect_stdout

sys.stdout.reconfigure(encoding="utf-8")

from trading.charges import calculate_trade_charges, passes_charges_filter, MIN_EDGE_MULTIPLE


class TestChargesFilter(unittest.TestCase):
    # ─── Equity (SBIN, 18 qty @ 1047.5 -> ~₹29 round-trip charges, 2x = ~₹58) ───
    def test_equity_tiny_target_rejected(self):
        ok, expected, charges = passes_charges_filter("SBIN", "EQUITY", "BUY", 1047.5, 1048.5, 18)
        self.assertFalse(ok)
        self.assertAlmostEqual(expected, 18.0, places=2)
        self.assertGreater(charges, 25.0)

    def test_equity_just_below_two_x_rejected(self):
        # ₹3 move = ₹54 gross < 2 x ~₹29
        self.assertFalse(passes_charges_filter("SBIN", "EQUITY", "BUY", 1047.5, 1050.5, 18)[0])

    def test_equity_five_rupee_move_passes(self):
        # ₹5 move = ₹90 gross >= 2 x ~₹29 (blueprint assumed this would be rejected; it is not)
        self.assertTrue(passes_charges_filter("SBIN", "EQUITY", "BUY", 1047.5, 1052.5, 18)[0])

    def test_equity_large_target_passes(self):
        self.assertTrue(passes_charges_filter("SBIN", "EQUITY", "BUY", 1047.5, 1067.5, 18)[0])

    def test_equity_sell_symmetric(self):
        self.assertTrue(passes_charges_filter("SBIN", "EQUITY", "SELL", 1047.5, 1037.5, 18)[0])
        self.assertFalse(passes_charges_filter("SBIN", "EQUITY", "SELL", 1047.5, 1046.5, 18)[0])

    def test_zero_move_rejected(self):
        # The INTRADAY ₹-37..-42 scratch trades: target == entry
        ok, expected, _ = passes_charges_filter("SBIN", "EQUITY", "BUY", 1047.5, 1047.5, 18)
        self.assertFalse(ok)
        self.assertEqual(expected, 0.0)

    # ─── Wrong-side target must not pass (abs() bug in the handover helper) ───
    def test_wrong_side_target_rejected(self):
        ok, expected, _ = passes_charges_filter("SBIN", "EQUITY", "BUY", 1047.5, 1037.5, 18)
        self.assertFalse(ok)
        self.assertLess(expected, 0)
        ok, expected, _ = passes_charges_filter("SBIN", "EQUITY", "SELL", 1047.5, 1057.5, 18)
        self.assertFalse(ok)
        self.assertLess(expected, 0)

    # ─── Currency ───
    def test_currency_usdinr_real_trade_passes(self):
        # ₹290 gross vs ~₹50 charges (charges.py self-test example)
        self.assertTrue(passes_charges_filter("USDINR", "CURRENCY", "SELL", 95.44, 95.15, 1)[0])

    def test_currency_sub_breakeven_rejected(self):
        # 5 paise = ₹50 gross ~= 1x charges, well under 2x
        self.assertFalse(passes_charges_filter("USDINR", "CURRENCY", "SELL", 95.44, 95.39, 1)[0])

    def test_currency_jpyinr_multiplier(self):
        # 100,000x lot multiplier: 1 paisa = ₹1,000 gross
        ok, expected, _ = passes_charges_filter("JPYINR", "CURRENCY", "BUY", 0.60, 0.61, 1)
        self.assertTrue(ok)
        self.assertAlmostEqual(expected, 1000.0, places=2)

    # ─── Commodity ───
    def test_commodity_crude_small_rejected_large_passes(self):
        self.assertFalse(passes_charges_filter("CRUDEOILM", "COMMODITY", "BUY", 7242.85, 7247.85, 1)[0])
        self.assertTrue(passes_charges_filter("CRUDEOILM", "COMMODITY", "BUY", 7242.85, 7276.85, 1)[0])

    # ─── Boundary ───
    def test_exact_multiple_boundary(self):
        est = calculate_trade_charges("SBIN", "EQUITY", "BUY", 1047.5, 1052.5, 18)
        exact = est.gross_pnl / est.total_charges  # multiple this trade achieves
        self.assertTrue(passes_charges_filter("SBIN", "EQUITY", "BUY", 1047.5, 1052.5, 18, min_edge_multiple=exact)[0])
        self.assertFalse(passes_charges_filter("SBIN", "EQUITY", "BUY", 1047.5, 1052.5, 18, min_edge_multiple=exact + 0.01)[0])

    def test_default_multiple_is_two(self):
        self.assertEqual(MIN_EDGE_MULTIPLE, 2.0)


class TestDaemonWiring(unittest.TestCase):
    def test_daemon_method_delegates_and_logs(self):
        from trading.daemon import TradingDaemon
        d = TradingDaemon.__new__(TradingDaemon)  # skip __init__: no notifier / state file
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertFalse(d.passes_charges_filter("SBIN", "EQUITY", "BUY", 1047.5, 1048.5, 18))
            self.assertTrue(d.passes_charges_filter("SBIN", "EQUITY", "BUY", 1047.5, 1067.5, 18))
        out = buf.getvalue()
        self.assertIn("[Charges Filter] REJECTED SBIN", out)
        self.assertEqual(out.count("REJECTED"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
