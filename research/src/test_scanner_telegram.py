"""
Tests for the /invest Telegram command: message formatting and command routing.
Daemon is built without __init__ with a mock scanner: no network, no state files.
"""
import sys
import unittest
from unittest.mock import MagicMock

sys.stdout.reconfigure(encoding="utf-8")

from scoring.scanner_service import ScannerError
from scoring.scanner_telegram import format_picks, format_status, format_plan, format_study, format_compare, backtest_line
from trading.daemon import TradingDaemon

BT = {"first_date": "2015-09-25", "last_date": "2026-09-18", "cagr": 0.223, "max_drawdown": -0.235,
      "benchmark_cagr": 0.215, "years_meeting_target": 5, "years_total": 12}


def pick(sym="RELIANCE", **over):
    return {"symbol": sym, "weight": 0.09, "price": 1226.4, "mom_12_1": 0.45, "mom_6": 0.2, "trend_pct": 12.0,
            "vol": 0.28, "sector": "Energy", "unverified": [], **over}


def picks_out(**over):
    return {"asof": "2026-09-18", "risk_on": True, "breadth": 0.7, "picks": [pick(), pick("TCS", unverified=["ROE"])],
            "rejected": [{"symbol": "BADCO", "score": 0.8, "reasons": ["ROE 2% < 10%"]}], "invested_pct": 18.0,
            "eligible": 60, "universe_scanned": 176, "price_age_hours": 2.0, "refreshing": False, "backtest": BT, **over}


class TestFormatting(unittest.TestCase):
    def test_picks_message_always_carries_the_backtest_and_the_honesty_line(self):
        msg = format_picks(picks_out())
        self.assertIn("22.3%", msg)
        self.assertIn("5 of 12", msg)
        self.assertIn("No proven edge", msg)
        self.assertIn("not a forecast", msg)

    def test_picks_lists_symbols_weights_and_flags_unverified(self):
        msg = format_picks(picks_out())
        self.assertIn("RELIANCE", msg)
        self.assertIn("9.0%", msg)
        self.assertIn("TCS</b> (Energy) ❔", msg)
        self.assertIn("BADCO", msg)

    def test_risk_off_and_stale_prices_are_called_out(self):
        self.assertIn("Risk-off", format_picks(picks_out(risk_on=False, breadth=0.2, picks=[])))
        self.assertIn("refresh started", format_picks(picks_out(price_age_hours=30.0)))

    def test_picks_message_carries_the_cross_market_finding_when_a_study_exists(self):
        study = {"markets": 8, "beat_universe": 3, "significant": 0, "reached_25pct": 1}
        msg = format_picks(picks_out(study=study))
        self.assertIn("8 markets", msg)
        self.assertIn("<b>3</b>", msg)
        self.assertIn("significant in <b>0</b>", msg)
        self.assertNotIn("Same model on", format_picks(picks_out()))

    def test_missing_backtest_says_so(self):
        self.assertIn("not run yet", backtest_line(None))

    def test_status_shows_hurdle_verdict_and_too_early_annualized(self):
        st = {"status": "OK", "days": 12, "value": 151000.0, "capital": 150000.0, "return_pct": 0.67,
              "annualized_pct": None, "annualized_note": "too early: needs 90+ days", "hurdle_value": 150900.0,
              "vs_hurdle": 100.0, "on_track": True, "nifty_return_pct": 0.3, "vs_nifty_pct": 0.37,
              "max_drawdown_pct": -1.2, "cash": 900.0, "next_rebalance": "2026-10-21",
              "holdings": [{"symbol": "TCS", "qty": 10, "avg_cost": 2300.0, "price": 2400.0, "pnl_pct": 4.3}]}
        msg = format_status(st)
        self.assertIn("too early", msg)
        self.assertIn("ahead of", msg)
        self.assertIn("25%/yr", msg)
        self.assertIn("TCS", msg)

    def test_status_behind_hurdle(self):
        st = {"status": "OK", "days": 200, "value": 140000.0, "capital": 150000.0, "return_pct": -6.7,
              "annualized_pct": -11.0, "annualized_note": None, "hurdle_value": 170000.0, "vs_hurdle": -30000.0,
              "on_track": False, "nifty_return_pct": None, "vs_nifty_pct": None, "max_drawdown_pct": -9.0,
              "cash": 100.0, "next_rebalance": None, "holdings": []}
        msg = format_status(st)
        self.assertIn("behind", msg)
        self.assertIn("-11.0%", msg)

    def test_no_portfolio_tells_how_to_create_one(self):
        self.assertIn("/invest rebalance confirm", format_status({"status": "NO_PORTFOLIO"}))

    def test_no_portfolio_for_a_named_variant_gives_the_variant_specific_command(self):
        msg = format_status({"status": "NO_PORTFOLIO", "variant": "quarterly", "label": "Quarterly (forward test, started 2026-09-22)"})
        self.assertIn("/invest rebalance quarterly confirm", msg)
        self.assertIn("Quarterly", msg)

    def test_status_shows_the_variant_label_when_present(self):
        st = {"status": "OK", "variant": "quarterly", "label": "Quarterly (forward test, started 2026-09-22)",
              "days": 5, "value": 150000.0, "capital": 150000.0, "return_pct": 0.0, "annualized_pct": None,
              "annualized_note": "too early: needs 90+ days", "hurdle_value": 150100.0, "vs_hurdle": -100.0,
              "on_track": False, "nifty_return_pct": None, "vs_nifty_pct": None, "max_drawdown_pct": 0.0,
              "cash": 150000.0, "next_rebalance": "2026-12-21", "holdings": []}
        self.assertIn("Quarterly (forward test", format_status(st))

    def test_plan_vs_executed_wording(self):
        plan = {"status": "PLAN", "portfolio_value": 150000.0, "est_costs": 300.0, "cash_after": 500.0,
                "orders": [{"side": "BUY", "qty": 5, "symbol": "TCS", "price": 2400.0, "value": 12000.0}],
                "note": "PAPER ONLY. No real orders are placed."}
        self.assertIn("nothing executed", format_plan(plan))
        self.assertIn("rebalance confirm", format_plan(plan))
        self.assertIn("EXECUTED", format_plan({**plan, "status": "EXECUTED"}))
        self.assertIn("next rebalance", format_plan({"status": "NOT_DUE", "detail": "next rebalance 2026-10-21"}))


def study_row(market, scan, univ, t, verdict, reference=False):
    return {"market": market, "reference": reference, "scanner_cagr": scan, "universe_cagr": univ,
            "excess": scan - univ, "t_stat": t, "verdict": verdict}


STUDY = {"rows": [study_row("india_all", 0.24, 0.171, 1.35, "ahead, not significant"),
                  study_row("india_176", 0.203, 0.215, -0.15, "behind universe", reference=True),
                  study_row("us_sp500", 0.142, 0.177, -0.76, "behind universe"),
                  study_row("hk_hsi", 0.163, 0.129, None, "ahead, not significant")],
         "summary": {"markets": 3, "beat_universe": 2, "significant": 0, "reached_25pct": 0},
         "caveats": ["survivorship"]}


class TestStudyMessage(unittest.TestCase):
    def test_table_lists_every_market_and_marks_the_reference_slice(self):
        msg = format_study(STUDY)
        for name in ("india all", "india 176*", "us sp500", "hk hsi"):
            self.assertIn(name, msg)
        self.assertIn("<pre>", msg)
        self.assertIn("+6.9%", msg)

    def test_summary_states_beat_significant_and_25pct_counts_plainly(self):
        msg = format_study(STUDY)
        self.assertIn("2 of 3", msg)
        self.assertIn("significant in <b>0</b>", msg)
        self.assertIn("25%/yr reached in <b>0</b>", msg)

    def test_carries_the_luck_and_survivorship_warnings(self):
        msg = format_study(STUDY)
        self.assertIn("could be luck", msg)
        self.assertIn("survivors", msg)

    def test_missing_t_stat_does_not_crash(self):
        self.assertIn("n/a", format_study(STUDY))


RULE = "Pre-registered 2026-09-21: whichever of monthly/quarterly has the higher AFTER-COST return_pct wins."


class TestCompareMessage(unittest.TestCase):
    def test_shows_both_labels_and_the_verdict_and_the_rule(self):
        cmp = {"variants": {
            "monthly": {"status": "OK", "label": "Monthly (default)", "days": 60, "return_pct": 20.0,
                       "annualized_pct": None, "annualized_note": "too early: needs 90+ days", "max_drawdown_pct": -2.0},
            "quarterly": {"status": "OK", "label": "Quarterly (forward test, started 2026-09-22)", "days": 60,
                         "return_pct": 5.0, "annualized_pct": None, "annualized_note": "too early: needs 90+ days",
                         "max_drawdown_pct": -1.0}},
            "rule": RULE, "min_days": 30, "verdict": "monthly ahead on after-cost return: monthly +20.00% vs quarterly +5.00%"}
        msg = format_compare(cmp)
        self.assertIn("Monthly (default)", msg)
        self.assertIn("Quarterly (forward test", msg)
        self.assertIn("monthly ahead", msg)
        self.assertIn(RULE, msg)

    def test_not_started_variant_shows_its_detail_instead_of_numbers(self):
        cmp = {"variants": {
            "monthly": {"status": "OK", "label": "Monthly (default)", "days": 10, "return_pct": 1.0,
                       "annualized_pct": None, "annualized_note": "too early: needs 90+ days", "max_drawdown_pct": 0.0},
            "quarterly": {"status": "NO_PORTFOLIO", "label": "Quarterly (forward test, started 2026-09-22)",
                         "detail": "No paper portfolio yet. Rebalance with execute to create one."}},
            "rule": RULE, "min_days": 30, "verdict": "not started yet: Quarterly (forward test, started 2026-09-22) has no portfolio"}
        msg = format_compare(cmp)
        self.assertIn("No paper portfolio yet", msg)
        self.assertIn("not started yet", msg)


class TestRouting(unittest.TestCase):
    def setUp(self):
        self.d = TradingDaemon.__new__(TradingDaemon)
        self.d.load_state = lambda: {"open_positions": [], "trade_history": [], "capital": 150000.0, "total_pnl": 0.0}
        self.d.calculate_margin_and_risk = lambda s: {}
        self.d._scanner = MagicMock()

    def test_bare_invest_returns_picks(self):
        self.d._scanner.picks.return_value = picks_out()
        self.assertIn("INVEST SCANNER", self.d.handle_telegram_command("/invest"))

    def test_status_subcommand(self):
        self.d._scanner.portfolio_status.return_value = {"status": "NO_PORTFOLIO"}
        self.assertIn("No paper portfolio", self.d.handle_telegram_command("/invest status"))
        self.d._scanner.portfolio_status.assert_called_with("monthly")

    def test_status_quarterly_subcommand_passes_the_variant_through(self):
        self.d._scanner.portfolio_status.return_value = {"status": "NO_PORTFOLIO", "variant": "quarterly",
                                                         "label": "Quarterly (forward test, started 2026-09-22)"}
        self.assertIn("Quarterly", self.d.handle_telegram_command("/invest status quarterly"))
        self.d._scanner.portfolio_status.assert_called_with("quarterly")

    def test_compare_subcommand(self):
        self.d._scanner.compare_variants.return_value = {
            "variants": {"monthly": {"status": "NO_PORTFOLIO", "label": "Monthly (default)",
                                     "detail": "No paper portfolio yet."},
                        "quarterly": {"status": "NO_PORTFOLIO", "label": "Quarterly (forward test, started 2026-09-22)",
                                     "detail": "No paper portfolio yet."}},
            "rule": "rule text", "min_days": 30, "verdict": "not started yet"}
        self.assertIn("MONTHLY vs QUARTERLY", self.d.handle_telegram_command("/invest compare"))

    def test_refresh_is_non_blocking_and_reports_start(self):
        self.d._scanner.refresh_async.return_value = {"status": "STARTED"}
        self.assertIn("refresh started", self.d.handle_telegram_command("/invest refresh").lower())
        self.d._scanner.refresh_async.return_value = {"status": "ALREADY_REFRESHING"}
        self.assertIn("already running", self.d.handle_telegram_command("/invest refresh"))

    def test_rebalance_without_confirm_only_plans(self):
        self.d._scanner.rebalance.return_value = {"status": "PLAN", "portfolio_value": 1.0, "est_costs": 0.0,
                                                  "cash_after": 1.0, "orders": [], "note": "PAPER ONLY."}
        self.d.handle_telegram_command("/invest rebalance")
        self.d._scanner.rebalance.assert_called_once_with(execute=False)

    def test_rebalance_confirm_executes(self):
        self.d._scanner.rebalance.return_value = {"status": "EXECUTED", "portfolio_value": 1.0, "est_costs": 0.0,
                                                  "cash_after": 1.0, "orders": [], "note": "PAPER ONLY."}
        self.d.handle_telegram_command("/invest rebalance confirm")
        self.d._scanner.rebalance.assert_called_once_with(execute=True)

    def test_rebalance_quarterly_without_confirm_only_plans_that_variant(self):
        self.d._scanner.rebalance.return_value = {"status": "PLAN", "portfolio_value": 1.0, "est_costs": 0.0,
                                                  "cash_after": 1.0, "orders": [], "note": "PAPER ONLY."}
        self.d.handle_telegram_command("/invest rebalance quarterly")
        self.d._scanner.rebalance.assert_called_once_with(execute=False, variant="quarterly")

    def test_rebalance_quarterly_confirm_executes_that_variant(self):
        self.d._scanner.rebalance.return_value = {"status": "EXECUTED", "portfolio_value": 1.0, "est_costs": 0.0,
                                                  "cash_after": 1.0, "orders": [], "note": "PAPER ONLY."}
        self.d.handle_telegram_command("/invest rebalance quarterly confirm")
        self.d._scanner.rebalance.assert_called_once_with(execute=True, variant="quarterly")

    def test_rebalance_with_garbage_after_confirm_shows_usage_not_a_crash(self):
        self.assertIn("Usage: /invest", self.d.handle_telegram_command("/invest rebalance quarterly confirm now"))
        self.d._scanner.rebalance.assert_not_called()

    def test_scanner_errors_become_a_friendly_message_not_a_crash(self):
        self.d._scanner.picks.side_effect = ScannerError("No price data yet.")
        self.assertEqual(self.d.handle_telegram_command("/invest"), "⚠️ No price data yet.")

    def test_study_subcommand(self):
        self.d._scanner.study_report.return_value = STUDY
        self.assertIn("DOES THE MODEL WORK ELSEWHERE", self.d.handle_telegram_command("/invest study"))

    def test_study_missing_is_a_friendly_message(self):
        self.d._scanner.study_report.side_effect = ScannerError("No market study yet.")
        self.assertEqual(self.d.handle_telegram_command("/invest study"), "⚠️ No market study yet.")

    def test_unknown_subcommand_shows_usage(self):
        self.assertIn("Usage: /invest", self.d.handle_telegram_command("/invest banana"))

    def test_help_lists_invest(self):
        self.assertIn("/invest", self.d.handle_telegram_command("/help"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
