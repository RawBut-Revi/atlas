"""
Tests for the global daily trade cap and the per-symbol daily cap in TradingDaemon.
Every collaborator is mocked (load_state, save_state, risk manager, RL, scanners), so neither
paper_positions.json nor the network is touched.
"""
import sys
import io
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.stdout.reconfigure(encoding="utf-8")

import trading.daemon as daemon_mod
from trading.daemon import TradingDaemon, MAX_DAILY_TRADES, MAX_TRADES_PER_SYMBOL_PER_DAY

TODAY = datetime.now().strftime("%Y-%m-%d")
YESTERDAY = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def trade(symbol, day=TODAY):
    return {"symbol": symbol, "entry_time": f"{day} 10:15:00"}


def make_daemon(state=None):
    d = TradingDaemon.__new__(TradingDaemon)
    d.mode = "PAPER"
    d.gap_scanned_today = False
    d.load_state = MagicMock(return_value=state or {"open_positions": [], "trade_history": []})
    d.save_state = MagicMock()
    d.calculate_margin_and_risk = MagicMock(return_value={"free_margin": 100000.0, "total_capital": 150000.0})
    d.manage_open_positions = MagicMock()
    d.notifier = MagicMock()
    d.rl_optimizer = MagicMock()
    d.rl_optimizer.get_optimal_action.return_value = SimpleNamespace(
        risk_scaling=1.0, exit_preference=0.5, trail_tightness=0.5)
    d.risk_manager = MagicMock()
    d.risk_manager.risk_per_trade = 2250.0
    d.risk_manager.can_trade.return_value = (True, "")
    d.risk_manager.calculate_position_size.return_value = (0, 0.0, "REJECTED")
    return d


def sig(symbol, entry=100):
    return {"symbol": symbol, "strategy": "TREND_MOMENTUM", "direction": "BUY", "entry_price": entry,
            "stop_loss": entry - 1, "target_price": entry + 5}


def quiet_call(fn, *args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = fn(*args)
    return result, buf.getvalue()


class TestConstants(unittest.TestCase):
    def test_values(self):
        self.assertEqual(MAX_DAILY_TRADES, 8)
        self.assertEqual(MAX_TRADES_PER_SYMBOL_PER_DAY, 3)


class TestPassesDailyCaps(unittest.TestCase):
    def test_empty_state_allows(self):
        d = make_daemon()
        ok, _ = quiet_call(d.passes_daily_caps, {}, "USDINR")
        self.assertTrue(ok)

    def test_seven_trades_allow_eighth(self):
        d = make_daemon()
        state = {"trade_history": [trade(f"S{i}") for i in range(7)], "open_positions": []}
        ok, _ = quiet_call(d.passes_daily_caps, state, "NEW")
        self.assertTrue(ok)

    def test_eight_trades_block_ninth(self):
        d = make_daemon()
        state = {"trade_history": [trade(f"S{i}") for i in range(8)], "open_positions": []}
        ok, out = quiet_call(d.passes_daily_caps, state, "NEW")
        self.assertFalse(ok)
        self.assertIn("[Daily Cap] REJECTED NEW: 8/8 trades already taken today", out)

    def test_open_positions_count_toward_global_cap(self):
        d = make_daemon()
        state = {"trade_history": [trade(f"S{i}") for i in range(5)],
                 "open_positions": [trade(f"O{i}") for i in range(3)]}
        ok, _ = quiet_call(d.passes_daily_caps, state, "NEW")
        self.assertFalse(ok)

    def test_yesterdays_trades_do_not_count(self):
        d = make_daemon()
        state = {"trade_history": [trade(f"S{i}", YESTERDAY) for i in range(20)], "open_positions": []}
        ok, _ = quiet_call(d.passes_daily_caps, state, "NEW")
        self.assertTrue(ok)

    def test_symbol_cap_blocks_only_that_symbol(self):
        d = make_daemon()
        state = {"trade_history": [trade("HCLTECH") for _ in range(3)], "open_positions": []}
        blocked, out = quiet_call(d.passes_daily_caps, state, "HCLTECH")
        allowed, _ = quiet_call(d.passes_daily_caps, state, "ITC")
        self.assertFalse(blocked)
        self.assertIn("3/3 trades in this symbol today", out)
        self.assertTrue(allowed)

    def test_open_position_counts_toward_symbol_cap(self):
        d = make_daemon()
        state = {"trade_history": [trade("ITC"), trade("ITC")], "open_positions": [trade("ITC")]}
        ok, _ = quiet_call(d.passes_daily_caps, state, "ITC")
        self.assertFalse(ok)

    def test_missing_entry_time_ignored(self):
        d = make_daemon()
        state = {"trade_history": [{"symbol": "X"}] * 10, "open_positions": [{"symbol": "X", "entry_time": None}]}
        ok, _ = quiet_call(d.passes_daily_caps, state, "X")
        self.assertTrue(ok)


class TestScanWiring(unittest.TestCase):
    """When the cap is hit, the scans must stop before position sizing."""

    def full_state(self):
        return {"open_positions": [], "trade_history": [trade(f"S{i}") for i in range(MAX_DAILY_TRADES)]}

    def test_gap_scan_blocked(self):
        d = make_daemon(self.full_state())
        with patch.object(daemon_mod, "scan_for_gaps", return_value=[sig("RELIANCE")]), redirect_stdout(io.StringIO()):
            d.run_gap_scan()
        d.risk_manager.calculate_position_size.assert_not_called()

    def test_currency_scan_blocked(self):
        d = make_daemon(self.full_state())
        with patch.object(daemon_mod, "scan_all_currency_pairs", return_value=[sig("USDINR", 90)]), \
                redirect_stdout(io.StringIO()):
            d.run_currency_scan()
        d.risk_manager.calculate_position_size.assert_not_called()

    def test_commodity_scan_blocked(self):
        d = make_daemon(self.full_state())
        with patch.object(daemon_mod, "scan_all_commodities", return_value=[sig("GOLDM", 60000)]), \
                redirect_stdout(io.StringIO()):
            d.run_commodity_scan()
        d.risk_manager.calculate_position_size.assert_not_called()

    def test_equity_scan_blocked(self):
        d = make_daemon(self.full_state())
        d.scan_universe = MagicMock(return_value=[sig("TCS", 4000)])
        with patch.object(daemon_mod, "DISABLED_STRATEGIES", frozenset()), \
                patch.object(daemon_mod, "get_swing_directional_bias", return_value="NEUTRAL"), \
                redirect_stdout(io.StringIO()):
            d.run_scan_cycle()
        d.risk_manager.calculate_position_size.assert_not_called()

    def test_scans_proceed_to_sizing_under_cap(self):
        d = make_daemon({"open_positions": [], "trade_history": [trade("S0")]})
        with patch.object(daemon_mod, "scan_all_currency_pairs", return_value=[sig("USDINR", 90)]), \
                redirect_stdout(io.StringIO()):
            d.run_currency_scan()
        d.risk_manager.calculate_position_size.assert_called_once()

    def test_equity_per_symbol_cap_enforced(self):
        # Equity had no per-symbol cap before: HCLTECH re-entered 10-11 times in one day.
        state = {"open_positions": [], "trade_history": [trade("HCLTECH") for _ in range(3)]}
        d = make_daemon(state)
        d.scan_universe = MagicMock(return_value=[sig("HCLTECH", 1500), sig("ITC", 400)])
        with patch.object(daemon_mod, "DISABLED_STRATEGIES", frozenset()), \
                patch.object(daemon_mod, "get_swing_directional_bias", return_value="NEUTRAL"), \
                redirect_stdout(io.StringIO()):
            d.run_scan_cycle()
        sized = [c.args[0] for c in d.risk_manager.calculate_position_size.call_args_list]
        self.assertEqual(sized, [400])


if __name__ == "__main__":
    unittest.main(verbosity=2)
