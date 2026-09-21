"""
Tests for the walk-forward simpler-signal study: the pre-registered grid, train/test slicing, and above all
that selection reads NO test-window number. Synthetic prices only: no network, no state files.
"""
import copy
import math
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8")

from scoring.momentum_scanner import (
    ScannerConfig, _d, make_dates, portfolio_weights, prepare_periods, slice_periods,
)
from scoring.walk_forward import (
    BASELINE_ID, SIGNALS, TEST_START, TRAIN_END, analyse, candidate_cfg, candidates, dimension_summary,
    evaluate_grid, select_on_train, spearman, verdict,
)

IST = timezone(timedelta(hours=5, minutes=30))
DAY = 86400
START = int(datetime(2014, 6, 1, tzinfo=IST).timestamp())


def series(n, drift, wiggle=0.01, volume=5_000_000):
    return {"ts": [START + k * DAY for k in range(n)],
            "close": [100.0 * math.exp(drift * k + wiggle * math.sin(k / 3.0)) for k in range(n)],
            "volume": [volume] * n, "events": []}


class TestPreRegisteredGrid(unittest.TestCase):
    def test_grid_is_48_unique_candidates(self):
        ids = [c["id"] for c in candidates()]
        self.assertEqual(len(ids), 48)
        self.assertEqual(len(set(ids)), 48)

    def test_baseline_is_todays_scanner_untouched(self):
        cand = next(c for c in candidates() if c["id"] == BASELINE_ID)
        cfg, base = candidate_cfg(ScannerConfig(), cand), ScannerConfig()
        for f in ("w_mom_12_1", "w_mom_6", "w_trend", "w_high", "vol_penalty", "top_n", "rebalance_days", "weighting"):
            self.assertEqual(getattr(cfg, f), getattr(base, f), f)

    def test_train_window_ends_before_test_begins(self):
        self.assertLess(TRAIN_END, TEST_START)

    def test_candidate_cfg_applies_signal_weights(self):
        cand = next(c for c in candidates() if c["signal"] == "trend_only" and c["top_n"] == 30 and c["rebalance_days"] == 90)
        cfg = candidate_cfg(ScannerConfig(), cand)
        self.assertEqual((cfg.w_trend, cfg.w_mom_12_1, cfg.top_n, cfg.rebalance_days), (1.0, 0.0, 30, 90))


class TestStatsHelpers(unittest.TestCase):
    def test_spearman(self):
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [4, 3, 2, 1]), -1.0)
        self.assertIsNone(spearman([1, 2], [1, 2]))
        self.assertIsNone(spearman([1, 1, 1], [1, 2, 3]))

    def test_verdict_thresholds(self):
        self.assertIn("FOUND", verdict({"excess": 0.05, "t_stat": 2.4}))
        self.assertIn("NOT significant", verdict({"excess": 0.05, "t_stat": 1.9}))
        self.assertIn("NOT significant", verdict({"excess": 0.05, "t_stat": None}))
        self.assertIn("NOTHING", verdict({"excess": -0.01, "t_stat": 3.0}))
        self.assertEqual(verdict({}), "no test data")


class TestWeights(unittest.TestCase):
    def picks(self, n):
        return [{"symbol": f"S{k}", "vol": 0.2 + 0.05 * k} for k in range(n)]

    def test_equal_weights_sum_to_one_when_uncapped(self):
        w = portfolio_weights(self.picks(12), ScannerConfig(weighting="equal"))
        self.assertAlmostEqual(sum(w.values()), 1.0)
        self.assertAlmostEqual(w["S0"], 1 / 12)

    def test_equal_weights_respect_the_cap_and_leave_cash(self):
        w = portfolio_weights(self.picks(5), ScannerConfig(weighting="equal"))
        self.assertEqual(set(w.values()), {0.15})
        self.assertEqual(portfolio_weights([], ScannerConfig(weighting="equal")), {})

    def test_default_is_still_inverse_vol(self):
        w = portfolio_weights(self.picks(12), ScannerConfig())
        self.assertGreater(w["S0"], w["S11"])


class TestSlicing(unittest.TestCase):
    def setUp(self):
        h = {f"UP{k}": series(365 * 11, 0.0003 + 0.00005 * k) for k in range(25)}
        h.update({f"DN{k}": series(365 * 11, -0.0002) for k in range(10)})
        self.cfg = ScannerConfig()
        dates, end = make_dates(h["UP0"]["ts"], self.cfg)
        self.prepared = prepare_periods(h.items(), dates, end, self.cfg)

    def test_train_and_test_windows_partition_the_periods_without_overlap(self):
        tr = slice_periods(self.prepared, None, TRAIN_END)
        te = slice_periods(self.prepared, TEST_START, None)
        self.assertEqual(len(tr["table"]) + len(te["table"]), len(self.prepared["table"]))
        self.assertLessEqual(_d(tr["dates"][-1]).isoformat(), TRAIN_END)
        self.assertGreaterEqual(_d(te["dates"][0]).isoformat(), TEST_START)

    def test_window_uses_the_same_point_in_time_data_as_the_full_table(self):
        te = slice_periods(self.prepared, TEST_START, None)
        k0 = self.prepared["dates"].index(te["dates"][0])
        self.assertIs(te["table"][0], self.prepared["table"][k0])         # nothing recomputed or leaked

    def test_last_train_period_ends_at_the_first_test_date(self):
        tr = slice_periods(self.prepared, None, TRAIN_END)
        te = slice_periods(self.prepared, TEST_START, None)
        self.assertEqual(tr["end"], te["dates"][0])

    def test_empty_window(self):
        self.assertEqual(slice_periods(self.prepared, "2099-01-01", None)["table"], [])


class TestSelectionIsBlindToTheTestWindow(unittest.TestCase):
    def rows(self):
        def row(cid, train_xs, test_xs):
            return {"id": cid, "signal": "s", "top_n": 12, "rebalance_days": 30, "weighting": "equal",
                    "train": {"excess": train_xs, "t_stat": 1.0}, "test": {"excess": test_xs, "t_stat": 1.0}}
        return [row("a", 0.05, 0.30), row("b", 0.12, -0.20), row("c", 0.08, 0.10)]

    def test_selects_the_highest_train_excess(self):
        self.assertEqual(select_on_train(self.rows())["id"], "b")

    def test_changing_every_test_number_cannot_change_the_selection(self):
        a = select_on_train(self.rows())["id"]
        mutated = copy.deepcopy(self.rows())
        for r in mutated:
            r["test"]["excess"] = -r["test"]["excess"] * 100 + 7
        self.assertEqual(select_on_train(mutated)["id"], a)

    def test_dimension_summary_flags_choices_positive_in_both_windows(self):
        rows = self.rows()
        rows[0]["weighting"] = "inverse_vol"
        d = dimension_summary(rows)["weighting"]
        by = {x["level"]: x for x in d}
        self.assertAlmostEqual(by["inverse_vol"]["train_excess"], 0.05)
        self.assertTrue(by["inverse_vol"]["both_positive"])
        self.assertFalse(by["equal"]["both_positive"])                    # mean test excess of b and c is -0.05


class TestEvaluateGrid(unittest.TestCase):
    def test_full_grid_runs_on_synthetic_data_and_analyse_is_consistent(self):
        h = {f"UP{k}": series(365 * 11, 0.0003 + 0.00005 * k) for k in range(25)}
        h.update({f"DN{k}": series(365 * 11, -0.0002) for k in range(10)})
        base = ScannerConfig()
        prepared = {}
        for r in (30, 90):
            from dataclasses import replace
            c = replace(base, rebalance_days=r)
            dates, end = make_dates(h["UP0"]["ts"], c)
            prepared[r] = prepare_periods(h.items(), dates, end, c)
        rows = evaluate_grid(prepared, base)
        self.assertEqual(len(rows), 48)
        self.assertTrue(all(r["train"] and r["test"] for r in rows))
        res = analyse(rows)
        self.assertIn(res["selected"]["id"], [r["id"] for r in rows])
        self.assertEqual(res["baseline"]["id"], BASELINE_ID)
        self.assertEqual(len(res["top5_by_train"]), 5)
        self.assertEqual(set(res["dimensions"]), {"signal", "top_n", "rebalance_days", "weighting"})
        # on persistent synthetic trends every momentum variant should beat the universe in both windows
        self.assertGreater(res["candidates_beating_universe_in_test"], 40)


if __name__ == "__main__":
    unittest.main(verbosity=2)
