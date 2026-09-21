"""
Tests for ScannerService: price cache, backtest report, picks, and the paper portfolio tracked against
the 25% goal and Nifty. Temp dirs + injected fetchers: no network, no repo state files.
"""
import json
import math
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8")

from scoring.scanner_service import ScannerService, ScannerError, PRICE_TTL_HOURS

IST = timezone(timedelta(hours=5, minutes=30))
DAY = 86400
START = int(datetime(2020, 1, 1, tzinfo=IST).timestamp())


def series(n, drift=0.0, wiggle=0.01, volume=5_000_000):
    close = [100.0 * math.exp(drift * k + wiggle * math.sin(k / 3.0)) for k in range(n)]
    return {"ts": [START + k * DAY for k in range(n)], "close": close, "volume": [volume] * n, "events": []}


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _dump(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


def universe_histories(n=420, count=30):
    h = {f"UP{k}": series(n, 0.0004 + 0.0001 * k) for k in range(count)}
    h.update({f"DN{k}": series(n, -0.001) for k in range(5)})
    return h


class ServiceCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.data = os.path.join(self.tmp, "data")
        os.makedirs(self.data)
        self.svc = ScannerService(state_dir=self.tmp, data_dir=self.data)
        self.svc.refresh_async = lambda: {"status": "STARTED"}          # never spawn threads in tests

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_prices(self, age_hours=1.0, nifty_last=20000.0, histories=None):
        histories = histories or universe_histories()
        prices = {s: {k: h[k] for k in ("ts", "close", "volume")} for s, h in histories.items()}
        n = len(next(iter(prices.values()))["ts"])
        nifty = {"ts": [START + k * DAY for k in range(n)], "close": [nifty_last] * n}
        stamp = (datetime.now(IST) - timedelta(hours=age_hours)).isoformat()
        with open(self.svc.price_path, "w", encoding="utf-8") as fh:
            json.dump({"fetched_at": stamp, "prices": prices, "nifty": nifty}, fh)


class TestPricesAndPicks(ServiceCase):
    def test_picks_without_cache_explains_itself(self):
        with self.assertRaises(ScannerError) as cm:
            self.svc.picks()
        self.assertIn("refresh", str(cm.exception).lower())

    def entries(self, n=30):
        return [{"symbol": f"S{k}", "ticker": f"S{k}.NS", "name": ""} for k in range(n)]

    def make(self, fetchers, n=30, profiles=None):
        """Service with a small fake universe and no network anywhere (universe, prices, fundamentals)."""
        return ScannerService(state_dir=self.tmp, data_dir=self.data, fetchers=fetchers,
                              universe_fn=lambda: self.entries(n),
                              profile_refresher=(profiles.append if profiles is not None else (lambda syms: None)))

    def test_refresh_writes_cache_from_injected_fetchers(self):
        hist = series(400, 0.001)
        svc = self.make(lambda: (lambda t: hist, lambda: hist))
        out = svc.refresh_prices()
        self.assertEqual(out["status"], "OK")
        self.assertEqual(out["stocks"], 30)
        self.assertLess(svc.price_age_hours(), 0.1)

    def test_refresh_covers_the_whole_universe_it_is_given_not_a_fixed_list(self):
        hist = series(400, 0.001)
        svc = self.make(lambda: (lambda t: hist, lambda: hist), n=250)
        self.assertEqual(svc.refresh_prices()["stocks"], 250)
        self.assertEqual(len(svc.picks()["picks"]), 12)
        self.assertEqual(svc.picks()["universe_scanned"], 250)

    def test_refresh_with_almost_no_data_is_an_error_not_a_silent_empty_cache(self):
        svc = self.make(lambda: (lambda t: None, lambda: None))
        with self.assertRaises(ScannerError):
            svc.refresh_prices()
        self.assertFalse(os.path.exists(svc.price_path))

    def test_network_failure_becomes_a_friendly_error_with_the_offline_hint(self):
        import requests

        def boom():
            raise requests.ConnectionError("dns")
        svc = self.make(boom)
        with self.assertRaises(ScannerError) as cm:
            svc.refresh_prices()
        self.assertIn("--from-history", str(cm.exception))
        self.assertFalse(svc.refreshing)                                 # flag released after the failure

    def test_fundamentals_are_fetched_for_top_candidates_that_lack_a_profile(self):
        asked = []
        hist = series(400, 0.001)
        svc = self.make(lambda: (lambda t: hist, lambda: hist), profiles=asked)
        svc.refresh_prices()
        self.assertEqual(len(asked), 1)
        self.assertTrue(asked[0])                                        # symbols with no profile were requested
        self.assertTrue(all(s.startswith("S") for s in asked[0]))

    def test_no_fundamentals_fetch_for_stocks_that_already_have_a_profile(self):
        asked = []
        _dump(self.svc.profile_path, {f"S{k}": {"roe_pct": 20, "debt_to_equity": 0.2} for k in range(30)})
        hist = series(400, 0.001)
        svc = self.make(lambda: (lambda t: hist, lambda: hist), profiles=asked)
        svc.refresh_prices()
        self.assertEqual(asked, [])

    def test_a_fundamentals_failure_never_fails_the_price_refresh(self):
        def boom(syms):
            raise RuntimeError("yahoo summary down")
        hist = series(400, 0.001)
        svc = ScannerService(state_dir=self.tmp, data_dir=self.data, fetchers=lambda: (lambda t: hist, lambda: hist),
                             universe_fn=lambda: self.entries(30), profile_refresher=boom)
        self.assertEqual(svc.refresh_prices()["status"], "OK")

    def test_seed_prefers_the_per_stock_store_and_uses_its_file_age(self):
        hist = series(400, 0.001)
        svc = self.make(lambda: (lambda t: hist, lambda: hist))
        svc.refresh_prices()
        os.remove(svc.price_path)
        stored = os.path.join(svc.market_dir, "india_all")
        old = datetime.now().timestamp() - 5 * 86400
        for f in os.listdir(stored):
            os.utime(os.path.join(stored, f), (old, old))
        out = svc.seed_from_history()
        self.assertEqual(out["status"], "SEEDED")
        self.assertEqual(out["stocks"], 30)
        self.assertAlmostEqual(svc.price_age_hours(), 120.0, delta=1.0)

    def test_seed_from_history_builds_a_usable_cache_with_its_real_age(self):
        _dump(self.svc.history_path, universe_histories(n=800))
        os.utime(self.svc.history_path, (0, datetime.now().timestamp() - 3 * 86400))   # file is 3 days old
        out = self.svc.seed_from_history()
        self.assertEqual(out["status"], "SEEDED")
        self.assertAlmostEqual(self.svc.price_age_hours(), 72.0, delta=1.0)
        self.assertEqual(len(self.svc.picks()["picks"]), 12)

    def test_seed_from_history_without_a_history_cache_is_an_error(self):
        with self.assertRaises(ScannerError):
            self.svc.seed_from_history()

    def test_picks_returns_ranked_weighted_stocks_and_the_25pct_target(self):
        self.write_prices()
        out = self.svc.picks()
        self.assertEqual(len(out["picks"]), 12)
        self.assertEqual(out["target_annual_return"], 0.25)
        self.assertLessEqual(out["invested_pct"], 100.0)
        self.assertTrue(out["risk_on"])                                  # switch is off by default

    def test_old_prices_trigger_background_refresh(self):
        calls = []
        self.svc.refresh_async = lambda: calls.append(1)
        self.write_prices(age_hours=PRICE_TTL_HOURS + 5)
        self.svc.picks()
        self.assertEqual(calls, [1])

    def test_fresh_prices_do_not_refresh(self):
        calls = []
        self.svc.refresh_async = lambda: calls.append(1)
        self.write_prices(age_hours=1)
        self.svc.picks()
        self.assertEqual(calls, [])


class TestBacktestReport(ServiceCase):
    def test_missing_history_cache_explains_how_to_get_it(self):
        with self.assertRaises(ScannerError) as cm:
            self.svc.backtest_report()
        self.assertIn("run_drip_backtest", str(cm.exception))

    def test_report_contains_default_variants_and_honest_caveats(self):
        with open(self.svc.history_path, "w", encoding="utf-8") as fh:
            json.dump(universe_histories(n=365 * 4), fh)
        rep = self.svc.backtest_report()
        self.assertEqual(rep["target_annual_return"], 0.25)
        for key in ("risk_off_switch_on", "costs_doubled", "top_8", "top_20"):
            self.assertIn(key, rep["variants"])
        for field in ("cagr", "max_drawdown", "benchmark_cagr", "years_meeting_target", "years_total", "yearly"):
            self.assertIn(field, rep["default"])
        self.assertTrue(any("survivorship" in c for c in rep["caveats"]))
        self.assertTrue(any("after seeing" in c for c in rep["caveats"]))

    def test_second_call_uses_the_stored_report(self):
        with open(self.svc.history_path, "w", encoding="utf-8") as fh:
            json.dump(universe_histories(n=365 * 4), fh)
        first = self.svc.backtest_report()
        os.remove(self.svc.history_path)                                 # would raise if it recomputed
        self.assertEqual(self.svc.backtest_report()["ran_at"], first["ran_at"])


class TestPaperPortfolio(ServiceCase):
    def test_no_portfolio_yet(self):
        self.write_prices()
        self.assertEqual(self.svc.portfolio_status()["status"], "NO_PORTFOLIO")

    def test_plan_does_not_write_state(self):
        self.write_prices()
        plan = self.svc.rebalance(execute=False, capital=150000)
        self.assertEqual(plan["status"], "PLAN")
        self.assertTrue(plan["orders"])
        self.assertFalse(os.path.exists(self.svc.portfolio_path))
        self.assertIn("PAPER ONLY", plan["note"])

    def test_execute_buys_whole_shares_within_cash_and_charges_costs(self):
        self.write_prices()
        out = self.svc.rebalance(execute=True, capital=150000)
        self.assertEqual(out["status"], "EXECUTED")
        state = _load(self.svc.portfolio_path)
        self.assertGreaterEqual(state["cash"], 0.0)
        self.assertTrue(all(isinstance(h["qty"], int) and h["qty"] > 0 for h in state["holdings"].values()))
        self.assertLessEqual(len(state["holdings"]), 12)
        self.assertGreater(sum(t["cost"] for t in state["trades"]), 0)
        st = self.svc.portfolio_status()
        self.assertLess(st["value"], 150000)                            # costs paid, prices flat since the buy
        self.assertGreater(st["value"], 150000 * 0.95)

    def test_second_rebalance_inside_the_month_is_not_due_unless_forced(self):
        self.write_prices()
        self.svc.rebalance(execute=True, capital=150000)
        self.assertEqual(self.svc.rebalance(execute=True)["status"], "NOT_DUE")
        self.assertEqual(self.svc.rebalance(execute=False, force=True)["status"], "PLAN")

    def test_stale_prices_refuse_to_trade(self):
        self.write_prices(age_hours=200)
        with self.assertRaises(ScannerError) as cm:
            self.svc.rebalance(execute=True, capital=150000)
        self.assertIn("old", str(cm.exception))

    def test_day_zero_status_has_no_annualized_number_and_hurdle_equals_capital(self):
        self.write_prices()
        self.svc.rebalance(execute=True, capital=150000)
        st = self.svc.portfolio_status()
        self.assertIsNone(st["annualized_pct"])
        self.assertIn("too early", st["annualized_note"])
        self.assertEqual(st["hurdle_value"], 150000.0)
        self.assertEqual(st["nifty_return_pct"], 0.0)

    def _age_portfolio(self, days, factor):
        """Pretend the portfolio is `days` old and every holding's price moved by `factor`."""
        state = _load(self.svc.portfolio_path)
        state["created"] = (datetime.now(IST).date() - timedelta(days=days)).isoformat()
        state["snapshots"] = []
        _dump(self.svc.portfolio_path, state)
        blob = _load(self.svc.price_path)
        for sym in state["holdings"]:
            blob["prices"][sym]["close"][-1] *= factor
        _dump(self.svc.price_path, blob)

    def test_outperforming_the_25pct_hurdle_is_reported_as_on_track(self):
        self.write_prices()
        self.svc.rebalance(execute=True, capital=150000)
        self._age_portfolio(days=180, factor=1.40)
        st = self.svc.portfolio_status()
        self.assertTrue(st["on_track"])
        self.assertGreater(st["vs_hurdle"], 0)
        self.assertIsNotNone(st["annualized_pct"])
        self.assertGreater(st["annualized_pct"], 25.0)

    def test_underperforming_the_hurdle_is_reported_plainly(self):
        self.write_prices()
        self.svc.rebalance(execute=True, capital=150000)
        self._age_portfolio(days=300, factor=1.02)
        st = self.svc.portfolio_status()
        self.assertFalse(st["on_track"])
        self.assertLess(st["vs_hurdle"], 0)

    def test_drawdown_is_measured_from_snapshots(self):
        self.write_prices()
        self.svc.rebalance(execute=True, capital=150000)
        state = _load(self.svc.portfolio_path)
        state["snapshots"] = [{"date": "2026-01-01", "value": 200000.0, "nifty": 1.0}]
        _dump(self.svc.portfolio_path, state)
        st = self.svc.portfolio_status()
        self.assertLess(st["max_drawdown_pct"], -20.0)                  # 200k peak -> ~149k now


def fake_study(**overrides):
    """A study result shaped like run_market_study's, built by the real engine on synthetic trending data."""
    from dataclasses import replace
    from scoring.momentum_scanner import ScannerConfig, make_dates, prepare_periods, summarize
    h = {f"UP{k}": series(365 * 4, 0.0003 + 0.00005 * k) for k in range(25)}
    h.update({f"DN{k}": series(365 * 4, -0.0002) for k in range(10)})
    cfg = ScannerConfig()
    dates, end = make_dates(h["UP0"]["ts"], cfg)
    prepared = prepare_periods(h.items(), dates, end, cfg)

    def compact(r):
        return {"cagr": r["strategy"]["cagr"], "max_drawdown": r["strategy"]["max_drawdown"],
                "benchmark_cagr": r["benchmark"]["cagr"]}

    def result(name, stocks, label=None):
        r = summarize(prepared, cfg, random_runs=20)
        r["variants"] = {"risk_off_switch_on": compact(summarize(prepared, replace(cfg, regime_filter=True))),
                         "costs_doubled": compact(summarize(prepared, replace(cfg, cost_per_side=0.005)))}
        r.update({"market": label or name, "name": name, "stocks_with_data": stocks, "index_cagr_price": 0.10})
        return r

    results = {"india_all": result("India - all NSE equities", 2081), "india_176": result("india_176", 176),
               "us_sp500": result("USA - S&P 500", 503)}
    results.update(overrides)
    return {"generated_at": "2026-09-21T10:00:00+00:00", "runs": 20, "results": results,
            "caveats": ["universes are today's constituents: survivorship-flattered"]}


class TestStudyReports(ServiceCase):
    def put_study(self, study):
        _dump(self.svc.study_path, study)

    def test_no_study_yet_is_a_friendly_error_and_picks_still_work(self):
        with self.assertRaises(ScannerError) as cm:
            self.svc.study_report()
        self.assertIn("run_market_study", str(cm.exception))
        self.write_prices()
        out = self.svc.picks()
        self.assertIsNone(out["study"])

    def test_backtest_reference_uses_the_india_all_study_when_present(self):
        study = fake_study()
        self.put_study(study)
        rep = self.svc.backtest_report()
        self.assertEqual(rep["source"], "market_study")
        self.assertIn("2081", rep["universe"])
        self.assertAlmostEqual(rep["default"]["cagr"], study["results"]["india_all"]["strategy"]["cagr"])
        self.assertEqual(set(rep["variants"]), {"risk_off_switch_on", "costs_doubled"})
        self.assertTrue(any("small caps" in c for c in rep["caveats"]))
        self.assertIsNotNone(rep["default"]["random_percentile"])
        self.assertIsNotNone(rep["default"]["cagr_before_costs"])

    def test_picks_carry_the_study_summary_and_the_study_backed_reference(self):
        self.put_study(fake_study())
        self.write_prices()
        out = self.svc.picks()
        self.assertEqual(out["study"]["markets"], 2)                     # india_all + us_sp500; india_176 is a reference row
        self.assertIn("beat_universe", out["study"])
        self.assertIsNotNone(out["backtest"])

    def test_verdict_rules(self):
        v = self.svc._verdict
        self.assertEqual(v(-0.01, 3.0), "behind universe")
        self.assertEqual(v(0.0, 3.0), "behind universe")
        self.assertEqual(v(0.05, 1.35), "ahead, not significant")
        self.assertEqual(v(0.05, None), "ahead, not significant")
        self.assertEqual(v(0.05, 2.4), "ahead, significant")

    def test_rows_flag_the_reference_slice_and_exclude_it_from_the_summary(self):
        self.put_study(fake_study())
        rep = self.svc.study_report()
        by = {r["market"]: r for r in rep["rows"]}
        self.assertTrue(by["india_176"]["reference"])
        self.assertFalse(by["india_all"]["reference"])
        self.assertEqual(rep["summary"]["markets"], 2)
        for field in ("scanner_cagr", "universe_cagr", "excess", "t_stat", "skill_percentile", "max_drawdown",
                      "half_excess", "costs_doubled_cagr", "verdict"):
            self.assertIn(field, by["us_sp500"])

    def test_errored_markets_are_skipped_not_crashed_on(self):
        self.put_study(fake_study(kr_kospi200={"error": "no stored history: run with --fetch"}))
        rep = self.svc.study_report()
        self.assertNotIn("kr_kospi200", [r["market"] for r in rep["rows"]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
