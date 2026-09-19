"""
Tests for the point-in-time DRIP backtest machinery. Synthetic data only: no network.
The key property is NO LOOK-AHEAD: a score computed on date t must not change if every bar
and dividend after t is replaced.
"""
import copy
import math
import random
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8")

from scoring.drip_backtest import (DAY, HORIZONS_DAYS, _idx_at, features_at, forward_return, make_signals, prepare,
                                   run_backtest, spearman)
from scoring.total_return_score import score_stock

IST = timezone(timedelta(hours=5, minutes=30))
T0 = int(datetime(2014, 1, 1, tzinfo=IST).timestamp())


def make_hist(days=3000, annual=0.10, vol=0.01, dps_yield=0.03, seed=1, volume=5_000_000):
    """Geometric random walk with a fixed annual drift and one dividend per year."""
    rng = random.Random(seed)
    ts, close, px = [], [], 100.0
    for i in range(days):
        ts.append(T0 + i * DAY)
        px *= math.exp(annual / 365 + rng.gauss(0, vol))
        close.append(px)
    events = []
    for y in range(int(days / 365)):
        k = min(len(ts) - 1, y * 365 + 200)
        events.append({"ex_date": datetime.fromtimestamp(ts[k], IST).strftime("%Y-%m-%d"), "dps": round(close[k] * dps_yield, 4)})
    return {"ts": ts, "close": close, "volume": [volume] * days, "events": events}


class TestSpearman(unittest.TestCase):
    def test_perfect_and_inverse(self):
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)
        self.assertAlmostEqual(spearman([1, 2, 3, 4], [40, 30, 20, 10]), -1.0)

    def test_monotone_nonlinear_still_one(self):
        self.assertAlmostEqual(spearman([1, 2, 3, 4, 5], [1, 4, 9, 16, 1000]), 1.0)

    def test_ties_and_degenerate(self):
        self.assertIsNotNone(spearman([1, 1, 2, 3], [1, 2, 2, 3]))
        self.assertIsNone(spearman([5, 5, 5, 5], [1, 2, 3, 4]))       # no variance
        self.assertIsNone(spearman([1, 2], [1, 2]))                   # too few points


class TestNoLookAhead(unittest.TestCase):
    def test_score_unchanged_when_future_is_destroyed(self):
        h = prepare({"X": make_hist()})["X"]
        i = 2000
        base = features_at(h, i)
        self.assertIsNotNone(base)
        wrecked = copy.deepcopy(h)
        for k in range(i + 1, len(wrecked["close"])):
            wrecked["close"][k] = 1e-3 if k % 2 else 1e9                  # absurd future prices
        wrecked["events"] = [e for e in wrecked["events"] if e["ex_date"] <= datetime.fromtimestamp(h["ts"][i], IST).strftime("%Y-%m-%d")]
        wrecked["events"] += [{"ex_date": "2099-01-01", "dps": 1e6}]
        wrecked = prepare({"X": wrecked})["X"]
        alt = features_at(wrecked, i)
        self.assertEqual(base[1], alt[1])                                  # price
        self.assertEqual(base[2], alt[2])                                  # ttm dividend
        self.assertEqual(base[3], alt[3])                                  # trend
        self.assertEqual(base[0], alt[0])                                  # entire profile
        s1 = score_stock("X", base[0], base[1], base[2], base[3])["efficiency"]
        s2 = score_stock("X", alt[0], alt[1], alt[2], alt[3])["efficiency"]
        self.assertEqual(s1, s2)

    def test_short_history_is_excluded(self):
        h = prepare({"X": make_hist(days=3000)})["X"]
        self.assertIsNone(features_at(h, 500))                            # ~1.4 years of data


class TestForwardReturn(unittest.TestCase):
    def test_includes_dividends_and_matches_manual(self):
        h = prepare({"X": make_hist(days=2000, annual=0.0, vol=0.0)})["X"]     # flat price 100
        i = 500
        h["close"] = [100.0] * len(h["close"])
        h["events"] = [{"ex_date": datetime.fromtimestamp(h["ts"][i + 30], IST).strftime("%Y-%m-%d"), "dps": 4.0}]
        h["ev_dates"] = [e["ex_date"] for e in h["events"]]
        self.assertAlmostEqual(forward_return(h, i, 91), 0.04, places=6)      # 4 / 100
        self.assertAlmostEqual(forward_return(h, i, 10), 0.0, places=6)       # dividend not yet paid

    def test_dividend_before_t_is_not_counted(self):
        h = prepare({"X": make_hist(days=2000, annual=0.0, vol=0.0)})["X"]
        i = 500
        h["close"] = [100.0] * len(h["close"])
        h["events"] = [{"ex_date": datetime.fromtimestamp(h["ts"][i - 5], IST).strftime("%Y-%m-%d"), "dps": 4.0}]
        h["ev_dates"] = [e["ex_date"] for e in h["events"]]
        self.assertAlmostEqual(forward_return(h, i, 91), 0.0, places=6)

    def test_beyond_end_of_data_is_none(self):
        h = prepare({"X": make_hist(days=1000)})["X"]
        self.assertIsNone(forward_return(h, 990, 91))

    def test_stale_bar_lookup(self):
        ts = [T0, T0 + DAY, T0 + 30 * DAY]
        self.assertEqual(_idx_at(ts, T0 + DAY + 3 * DAY), 1)
        self.assertIsNone(_idx_at(ts, T0 + 20 * DAY))                     # last print 19 days old: untradeable


def persistent_universe(n=40, days=3300, seed=7):
    """Stock i has a fixed drift from -15% to +35% a year: trailing momentum genuinely predicts the future."""
    hs = {}
    for k in range(n):
        drift = -0.15 + 0.5 * k / (n - 1)
        hs[f"S{k:02d}"] = make_hist(days=days, annual=drift, vol=0.006, seed=100 + k)
    return hs


class TestBacktestMachinery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = run_backtest(persistent_universe(), step_days=30, min_universe=20, seed=3)
        cls.s = cls.out["summary"]

    def test_detects_a_real_signal(self):
        m = self.s["momentum_3y"]["6m"]
        self.assertGreater(m["mean_ic"], 0.5)
        self.assertGreater(m["top_minus_universe"], 0.02)
        self.assertGreater(m["top_ret"], m["bottom_ret"])

    def test_random_signal_has_no_edge(self):
        for h in HORIZONS_DAYS:
            r = self.s["random"][h]
            self.assertLess(abs(r["mean_ic"]), 0.15, h)

    def test_trend_signal_also_positive(self):
        self.assertGreater(self.s["trend_only"]["6m"]["mean_ic"], 0.3)

    def test_output_shape_and_dates(self):
        self.assertEqual(set(self.s), {"atlas_history", "yield_only", "trend_only", "momentum_3y", "random"})
        self.assertGreater(self.s["atlas_history"]["3m"]["dates"], 30)
        self.assertLessEqual(self.s["atlas_history"]["12m"]["dates"], self.s["atlas_history"]["3m"]["dates"])
        self.assertLess(self.out["first_date"], self.out["last_date"])

    def test_independent_dates_are_fewer_than_all_dates(self):
        r = self.s["momentum_3y"]["12m"]
        self.assertLess(r["independent_dates"], r["dates"])

    def test_universe_too_small_yields_no_dates(self):
        tiny = dict(list(persistent_universe().items())[:5])
        out = run_backtest(tiny, min_universe=20)
        self.assertEqual(out["summary"]["momentum_3y"]["6m"]["dates"], 0)
        self.assertIsNone(out["summary"]["momentum_3y"]["6m"]["mean_ic"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
