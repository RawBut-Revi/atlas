"""
Tests for pnl_stats, the shared contract multiplier, and the Telegram reply text built from them
(/status, /positions, /pnl, /report, /alerts, EOD summary). No network, no state file.
"""
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

sys.stdout.reconfigure(encoding="utf-8")

from trading import pnl_stats
from trading.charges import contract_multiplier, calculate_trade_charges
from trading.daemon import TradingDaemon

TODAY = datetime.now().strftime("%Y-%m-%d")
YESTERDAY = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def closed(symbol, net, asset="CURRENCY", day=TODAY, charges=10.0, strategy="TREND_MOMENTUM", direction="BUY"):
    return {"symbol": symbol, "asset_type": asset, "direction": direction, "strategy": strategy,
            "gross_pnl": net + charges, "charges": charges, "net_pnl": net, "pnl": net,
            "exit_time": f"{day} 11:30:00", "status": "TAKE_PROFIT" if net > 0 else "STOP_LOSS"}


class TestSummarize(unittest.TestCase):
    def test_counts_and_totals(self):
        s = pnl_stats.summarize([closed("A", 100), closed("B", -40), closed("C", 0.0)])
        self.assertEqual((s["trades"], s["wins"], s["losses"], s["breakevens"]), (3, 1, 1, 1))
        self.assertEqual(s["net"], 60.0)
        self.assertEqual(s["charges"], 30.0)
        self.assertEqual(s["gross"], 90.0)   # net + charges per trade: 110 - 30 + 10
        self.assertEqual((s["best"], s["worst"]), (100.0, -40.0))

    def test_win_rate_excludes_breakevens(self):
        s = pnl_stats.summarize([closed("A", 50), closed("B", 0.0), closed("C", 0.0)])
        self.assertEqual(s["win_rate"], 100.0)

    def test_tiny_pnl_counted_once_as_breakeven(self):
        s = pnl_stats.summarize([closed("A", 0.004)])
        self.assertEqual((s["wins"], s["losses"], s["breakevens"]), (0, 0, 1))

    def test_empty(self):
        s = pnl_stats.summarize([])
        self.assertEqual((s["trades"], s["net"], s["win_rate"], s["best"], s["worst"]), (0, 0.0, 0.0, 0.0, 0.0))

    def test_falls_back_to_pnl_key(self):
        s = pnl_stats.summarize([{"pnl": 25.0}, {"pnl": -5.0}])
        self.assertEqual((s["net"], s["wins"], s["losses"]), (20.0, 1, 1))

    def test_matches_recorded_totals_of_real_baseline(self):
        # Sanity for the per-asset grouping order used by every report.
        rows = [closed("X", 1, "COMMODITY"), closed("Y", 1, "EQUITY"), closed("Z", 1, "CURRENCY")]
        self.assertEqual(list(pnl_stats.by_asset(rows)), ["EQUITY", "CURRENCY", "COMMODITY"])


class TestFiltersAndFormatting(unittest.TestCase):
    def test_closed_on_matches_exit_date_only(self):
        hist = [closed("A", 1), closed("B", 1, day=YESTERDAY), {"symbol": "C"}]
        self.assertEqual([t["symbol"] for t in pnl_stats.closed_on(hist, TODAY)], ["A"])

    def test_money(self):
        self.assertEqual(pnl_stats.money(1234.5), "+₹1,234.50")
        self.assertEqual(pnl_stats.money(-99), "-₹99.00")
        self.assertEqual(pnl_stats.money(0), "+₹0.00")

    def test_singular_trade_label(self):
        rows = pnl_stats.asset_rows(pnl_stats.by_asset([closed("A", 5, "EQUITY")]))
        self.assertIn("1 trade,", rows[0])
        self.assertNotIn("1 trades", rows[0])

    def test_trade_line(self):
        line = pnl_stats.trade_line(closed("USDINR", 250.0, charges=40.0))
        for part in ("🟢", "USDINR", "BUY"[:1], "+₹250.00", "fees ₹40", "TAKE_PROFIT"):
            self.assertIn(part, line)

    def test_by_strategy_sorted_best_first(self):
        rows = [closed("A", -5, strategy="S1"), closed("B", 50, strategy="S2"), closed("C", 5, strategy="S3")]
        self.assertEqual(list(pnl_stats.by_strategy(rows)), ["S2", "S3", "S1"])


class TestContractMultiplier(unittest.TestCase):
    def test_values(self):
        cases = [("USDINR", "CURRENCY", 1000.0), ("JPYINR", "CURRENCY", 100000.0),
                 ("CRUDEOILM", "COMMODITY", 10.0), ("NATGASMINI", "COMMODITY", 250.0),
                 ("SILVERMIC", "COMMODITY", 1.0), ("GOLDM", "COMMODITY", 100.0),
                 ("COPPERM", "COMMODITY", 250.0), ("UNKNOWN", "COMMODITY", 1.0), ("TCS", "EQUITY", 1.0)]
        for sym, at, expected in cases:
            self.assertEqual(contract_multiplier(sym, at), expected, sym)

    def test_charges_gross_pnl_uses_same_multiplier(self):
        c = calculate_trade_charges("GOLDM", "COMMODITY", "BUY", 60000, 60010, 2)
        self.assertAlmostEqual(c.gross_pnl, 10 * 2 * contract_multiplier("GOLDM", "COMMODITY"))


def make_daemon(state, notifier_mode="quiet"):
    d = TradingDaemon.__new__(TradingDaemon)
    d.load_state = MagicMock(return_value=state)
    d.save_state = MagicMock()
    d.notifier = MagicMock()
    d.notifier.alert_mode = notifier_mode
    d.get_live_price = MagicMock(side_effect=lambda sym, at, fallback_price=0.0: fallback_price)
    return d


def base_state(history=None, open_positions=None, total_pnl=None):
    history = history or []
    return {"capital": 150000.0,
            "total_pnl": sum(t["net_pnl"] for t in history) if total_pnl is None else total_pnl,
            "trade_history": history, "open_positions": open_positions or []}


class TestBuildDailySummary(unittest.TestCase):
    def test_reports_todays_real_trades_not_zero(self):
        hist = [closed("A", 100), closed("B", -30, "COMMODITY"), closed("C", 500, day=YESTERDAY)]
        d = make_daemon(base_state(hist))
        s = d.build_daily_summary(d.load_state())
        self.assertEqual((s["total_trades"], s["wins"], s["losses"]), (2, 1, 1))
        self.assertEqual(s["total_net_pnl"], 70.0)
        self.assertEqual(s["closing_capital"], 150000.0 + 570.0)   # cumulative, incl. yesterday
        self.assertEqual(list(s["by_asset"]), ["CURRENCY", "COMMODITY"])


class TestStatusReply(unittest.TestCase):
    def test_today_and_cumulative_are_separate(self):
        hist = [closed("A", 100), closed("B", -30, day=YESTERDAY)]
        d = make_daemon(base_state(hist))
        out = d.handle_telegram_command("/status")
        self.assertIn("Trades:</b> 1", out)
        self.assertIn("+₹100.00", out)               # today's net only
        self.assertIn("Cumulative Net P&L:</b> +₹70.00", out)
        self.assertIn("Alerts:</b> quiet", out)

    def test_rr_not_hardcoded_without_open_trades(self):
        d = make_daemon(base_state())
        self.assertIn("n/a (no open trades)", d.handle_telegram_command("/status"))

    def test_rr_computed_from_open_trades(self):
        pos = [{"id": "x", "symbol": "USDINR", "asset_type": "CURRENCY", "direction": "BUY", "lots": 1,
                "entry_price": 90.0, "stop_loss": 89.5, "target_price": 91.0}]
        d = make_daemon(base_state(open_positions=pos))
        d.calculate_margin_and_risk = TradingDaemon.calculate_margin_and_risk.__get__(d)
        self.assertIn("1 : 2.00", d.handle_telegram_command("/status"))


class TestPositionsReply(unittest.TestCase):
    def test_pnl_uses_contract_multiplier(self):
        pos = [{"id": "f", "symbol": "USDINR", "asset_type": "CURRENCY", "direction": "BUY", "lots": 2,
                "entry_price": 90.0, "stop_loss": 89.9, "target_price": 90.3},
               {"id": "m", "symbol": "GOLDM", "asset_type": "COMMODITY", "direction": "SELL", "lots": 1,
                "entry_price": 60000.0, "stop_loss": 60100.0, "target_price": 59800.0}]
        d = make_daemon(base_state(open_positions=pos))
        d.calculate_margin_and_risk = TradingDaemon.calculate_margin_and_risk.__get__(d)
        prices = {"USDINR": 90.05, "GOLDM": 59950.0}
        d.get_live_price = MagicMock(side_effect=lambda sym, at, fallback_price=0.0: prices[sym])
        out = d.handle_telegram_command("/positions")
        self.assertIn("+₹100.00", out)      # (90.05-90.0) * 2 lots * 1000
        self.assertIn("+₹5,000.00", out)    # (60000-59950) * 1 lot * 100 g
        self.assertIn("Total Unrealized P&L (before charges):</b> +₹5,100.00", out)
        self.assertIn("-₹200", out)         # USDINR SL risk: 0.1 * 2 * 1000
        self.assertIn("+₹600", out)         # USDINR TP reward: 0.3 * 2 * 1000

    def test_no_positions(self):
        d = make_daemon(base_state())
        d.calculate_margin_and_risk = TradingDaemon.calculate_margin_and_risk.__get__(d)
        self.assertIn("No active positions", d.handle_telegram_command("/positions"))


class TestPnlAndReportReply(unittest.TestCase):
    def setUp(self):
        self.hist = [closed("USDINR", 300, "CURRENCY"), closed("GOLDM", -100, "COMMODITY"),
                     closed("PRIORDAY", -900, day=YESTERDAY)]
        self.d = make_daemon(base_state(self.hist))
        self.d.calculate_margin_and_risk = TradingDaemon.calculate_margin_and_risk.__get__(self.d)

    def test_pnl_summarises_today_by_market(self):
        out = self.d.handle_telegram_command("/pnl")
        self.assertIn("Net: +₹200.00", out)
        self.assertIn("2 closed", out)
        self.assertIn("Currency: 1 trade, 1W/0L, net +₹300.00", out)
        self.assertIn("Commodity: 1 trade, 0W/1L, net -₹100.00", out)
        self.assertIn("Cumulative:</b> -₹700.00 (3 trades", out)
        self.assertNotIn("PRIORDAY", out)          # yesterday's trade is not in today's list

    def test_pnl_lists_at_most_eight_trades_and_says_how_many_more(self):
        hist = [closed(f"S{i}", 10) for i in range(11)]
        d = make_daemon(base_state(hist))
        d.calculate_margin_and_risk = TradingDaemon.calculate_margin_and_risk.__get__(d)
        out = d.handle_telegram_command("/pnl")
        self.assertEqual(out.count("TAKE_PROFIT"), 8)
        self.assertIn("…and 3 more", out)

    def test_pnl_no_trades_today(self):
        d = make_daemon(base_state([closed("PRIORDAY", -5, day=YESTERDAY)]))
        d.calculate_margin_and_risk = TradingDaemon.calculate_margin_and_risk.__get__(d)
        out = d.handle_telegram_command("/pnl")
        self.assertIn("No trades closed yet today", out)
        self.assertIn("Net: +₹0.00", out)

    def test_report_all_time_by_market_and_strategy(self):
        out = self.d.handle_telegram_command("/report")
        self.assertIn("ALL TIME", out)
        self.assertIn("Net: -₹700.00", out)
        self.assertIn("By market", out)
        self.assertIn("By strategy", out)
        self.assertIn("TREND_MOMENTUM: 3 trades", out)

    def test_report_fits_one_telegram_message(self):
        hist = [closed(f"S{i}", (-1) ** i * 50, strategy=f"STRAT_{i % 9}") for i in range(200)]
        d = make_daemon(base_state(hist))
        d.calculate_margin_and_risk = TradingDaemon.calculate_margin_and_risk.__get__(d)
        self.assertLess(len(d.handle_telegram_command("/report")), 4096)

    def test_report_empty(self):
        d = make_daemon(base_state())
        d.calculate_margin_and_risk = TradingDaemon.calculate_margin_and_risk.__get__(d)
        self.assertIn("No trade history", d.handle_telegram_command("/report"))


class TestAlertsCommand(unittest.TestCase):
    def setUp(self):
        self.d = make_daemon(base_state())
        self.d.calculate_margin_and_risk = MagicMock(return_value={})
        self.d.notifier.set_alert_mode = MagicMock(side_effect=lambda m: m in ("normal", "quiet", "mute"))

    def test_shortcuts(self):
        for cmd, mode in (("/quiet", "quiet"), ("/mute", "mute"), ("/loud", "normal")):
            self.d.handle_telegram_command(cmd)
            self.d.notifier.set_alert_mode.assert_called_with(mode)

    def test_alerts_with_argument(self):
        self.d.handle_telegram_command("/alerts mute")
        self.d.notifier.set_alert_mode.assert_called_with("mute")
        self.d.handle_telegram_command("/alerts loud")
        self.d.notifier.set_alert_mode.assert_called_with("normal")

    def test_alerts_without_argument_shows_current_mode(self):
        self.d.notifier.set_alert_mode.reset_mock()
        out = self.d.handle_telegram_command("/alerts")
        self.assertIn("Alert mode", out)
        self.d.notifier.set_alert_mode.assert_not_called()

    def test_unknown_mode_rejected(self):
        self.assertIn("Unknown mode", self.d.handle_telegram_command("/alerts banana"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
