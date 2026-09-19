"""
Tests for Phase 4 Task 2: disabled-strategy gate in TradingDaemon.
Every daemon collaborator is mocked (load_state, save_state, risk manager, RL, scanners), so
neither paper_positions.json nor the network is touched.
"""
import sys
import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.stdout.reconfigure(encoding="utf-8")

import trading.daemon as daemon_mod
from trading.daemon import TradingDaemon, DISABLED_STRATEGIES


def make_daemon():
    """TradingDaemon without __init__ (no notifier / state file) and with mocked collaborators."""
    d = TradingDaemon.__new__(TradingDaemon)
    d.mode = "PAPER"
    d.gap_scanned_today = False
    d.load_state = MagicMock(return_value={"open_positions": [], "trade_history": []})
    d.save_state = MagicMock()
    d.calculate_margin_and_risk = MagicMock(return_value={"free_margin": 100000.0, "total_capital": 150000.0})
    d.manage_open_positions = MagicMock()
    d.notifier = MagicMock()
    d.rl_optimizer = MagicMock()
    d.rl_optimizer.get_optimal_action.return_value = SimpleNamespace(
        risk_scaling=1.0, exit_preference=0.5, trail_tightness=0.5)
    d.risk_manager = MagicMock()
    d.risk_manager.risk_per_trade = 2250.0
    # Reject at sizing: the flow stops there, so the calls show exactly which signals got past the gate.
    d.risk_manager.calculate_position_size.return_value = (0, 0.0, "REJECTED")
    return d


def sized_prices(d):
    """Entry prices that reached position sizing."""
    return [c.args[0] for c in d.risk_manager.calculate_position_size.call_args_list]


def sig(symbol, strategy, entry):
    return {"symbol": symbol, "strategy": strategy, "direction": "BUY", "entry_price": entry,
            "stop_loss": entry - 1, "target_price": entry + 5}


class TestConstants(unittest.TestCase):
    def test_disabled_set(self):
        self.assertEqual(DISABLED_STRATEGIES, {"INTRADAY", "US_SESSION_MOMENTUM", "GAP_FADE"})

    def test_winning_strategies_not_disabled(self):
        for s in ("TREND_MOMENTUM", "GAP_AND_GO", "3H_PATTERN_BREAKOUT", "PULLBACK_DIP", "PULLBACK_RALLY"):
            self.assertNotIn(s, DISABLED_STRATEGIES)


class TestFilter(unittest.TestCase):
    def test_removes_disabled_keeps_enabled_in_order(self):
        d = make_daemon()
        signals = [sig("A", "GAP_FADE", 1), sig("B", "GAP_AND_GO", 2), sig("C", "US_SESSION_MOMENTUM", 3),
                   sig("D", "TREND_MOMENTUM", 4)]
        buf = io.StringIO()
        with redirect_stdout(buf):
            out = d.filter_disabled_strategies(signals, "TEST")
        self.assertEqual([s["symbol"] for s in out], ["B", "D"])
        self.assertIn("[Strategy Gate] Skipped TEST A: GAP_FADE is disabled", buf.getvalue())
        self.assertIn("US_SESSION_MOMENTUM is disabled", buf.getvalue())

    def test_empty_and_none(self):
        d = make_daemon()
        self.assertEqual(d.filter_disabled_strategies([], "X"), [])
        self.assertEqual(d.filter_disabled_strategies(None, "X"), [])

    def test_signal_without_strategy_key_kept(self):
        d = make_daemon()
        self.assertEqual(len(d.filter_disabled_strategies([{"symbol": "Z"}], "X")), 1)


class TestScanMethods(unittest.TestCase):
    def test_gap_scan_skips_gap_fade_and_filters_before_slice(self):
        d = make_daemon()
        # Four GAP_FADE ahead of one GAP_AND_GO: with the old [:4] slice first, GAP_AND_GO would be dropped.
        gaps = [sig(f"F{i}", "GAP_FADE", 100 + i) for i in range(4)] + [sig("GO", "GAP_AND_GO", 500)]
        with patch.object(daemon_mod, "scan_for_gaps", return_value=gaps), redirect_stdout(io.StringIO()):
            d.run_gap_scan()
        self.assertEqual(sized_prices(d), [500])
        self.assertTrue(d.gap_scanned_today)

    def test_currency_scan_skips_disabled(self):
        d = make_daemon()
        signals = [sig("USDINR", "US_SESSION_MOMENTUM", 90), sig("GBPINR", "TREND_MOMENTUM", 120),
                   sig("EURINR", "GAP_FADE", 100)]
        with patch.object(daemon_mod, "scan_all_currency_pairs", return_value=signals), redirect_stdout(io.StringIO()):
            d.run_currency_scan()
        self.assertEqual(sized_prices(d), [120])

    def test_commodity_scan_skips_us_session_and_filters_before_slice(self):
        d = make_daemon()
        signals = [sig("CRUDEOILM", "US_SESSION_MOMENTUM", 7000), sig("GOLDM", "US_SESSION_MOMENTUM", 60000),
                   sig("SILVERMIC", "3H_PATTERN_BREAKOUT", 80000)]
        with patch.object(daemon_mod, "scan_all_commodities", return_value=signals), redirect_stdout(io.StringIO()):
            d.run_commodity_scan()
        self.assertEqual(sized_prices(d), [80000])

    def test_equity_scan_disabled_manages_positions_but_never_scans(self):
        d = make_daemon()
        d.scan_universe = MagicMock(return_value=[])
        buf = io.StringIO()
        with redirect_stdout(buf):
            d.run_scan_cycle()
        d.manage_open_positions.assert_called_once()   # exits still handled
        d.scan_universe.assert_not_called()            # 40 data fetches saved
        d.risk_manager.calculate_position_size.assert_not_called()
        self.assertIn("[Strategy Gate] Equity INTRADAY scan disabled", buf.getvalue())

    def test_equity_scan_runs_when_intraday_enabled(self):
        d = make_daemon()
        d.risk_manager.can_trade.return_value = (True, "")
        d.scan_universe = MagicMock(return_value=[])
        with patch.object(daemon_mod, "DISABLED_STRATEGIES", frozenset()), redirect_stdout(io.StringIO()):
            d.run_scan_cycle()
        d.scan_universe.assert_called_once()


class TestTelegramAi(unittest.TestCase):
    def test_ai_command_lists_disabled_strategies(self):
        d = make_daemon()
        out = d.handle_telegram_command("/ai")
        for name in ("GAP_FADE", "INTRADAY", "US_SESSION_MOMENTUM"):
            self.assertIn(name, out)
        self.assertIn("Disabled Strategies", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
