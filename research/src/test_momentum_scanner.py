"""
Tests for the momentum scanner: features, ranking, gates, weights, quality overlay, live scan and the
point-in-time backtest. Synthetic price series only: no network, no state files.
"""
import math
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8")

from scoring.momentum_scanner import (
    ScannerConfig, features_at, score_universe, breadth, risk_on, inverse_vol_weights,
    quality_check, scan, backtest, MIN_BARS, _pct_ranks,
)

IST = timezone(timedelta(hours=5, minutes=30))
DAY = 86400
START = int(datetime(2020, 1, 1, tzinfo=IST).timestamp())


def series(n, daily_drift=0.0, wiggle=0.01, start_price=100.0, volume=5_000_000):
    """Deterministic price path: exponential drift plus a fixed sine wiggle. Oldest first."""
    close = [start_price * math.exp(daily_drift * k + wiggle * math.sin(k / 3.0)) for k in range(n)]
    return {"ts": [START + k * DAY for k in range(n)], "close": close, "volume": [volume] * n, "events": []}


class TestFeatures(unittest.TestCase):
    def test_too_short_history_returns_none(self):
        h = series(MIN_BARS - 1)
        self.assertIsNone(features_at(h["close"], h["volume"], len(h["close"]) - 1))

    def test_uptrend_has_positive_momentum_and_trend(self):
        h = series(400, daily_drift=0.002)
        f = features_at(h["close"], h["volume"], 399)
        self.assertGreater(f["mom_12_1"], 0)
        self.assertGreater(f["mom_6"], 0)
        self.assertGreater(f["trend_pct"], 0)
        self.assertLessEqual(f["high_prox"], 1.0)

    def test_downtrend_is_below_200dma(self):
        h = series(400, daily_drift=-0.002)
        f = features_at(h["close"], h["volume"], 399)
        self.assertLess(f["trend_pct"], 0)
        self.assertLess(f["mom_12_1"], 0)

    def test_features_use_only_bars_up_to_i(self):
        h = series(400, daily_drift=0.001)
        base = features_at(h["close"], h["volume"], 350)
        poisoned = h["close"][:351] + [1e9] * 49              # future bars must not matter
        self.assertEqual(base, features_at(poisoned, h["volume"], 350))

    def test_turnover_in_crores(self):
        h = series(300, volume=1_000_000)                      # ~100 * 1e6 = 10 Cr
        f = features_at(h["close"], h["volume"], 299)
        self.assertAlmostEqual(f["turnover_cr"], 10.0, delta=1.5)


class TestRanking(unittest.TestCase):
    def test_pct_ranks_handle_ties(self):
        self.assertEqual(_pct_ranks([1, 2, 2, 3]), [0.0, 0.5, 0.5, 1.0])

    def _feats(self, **over):
        base = {"price": 100, "mom_12_1": 0.2, "mom_6": 0.1, "trend_pct": 5.0, "high_prox": 0.95, "vol": 0.3, "turnover_cr": 50.0}
        return {**base, **over}

    def test_stronger_momentum_ranks_higher(self):
        feats = {"A": self._feats(mom_12_1=0.6, mom_6=0.4), "B": self._feats(mom_12_1=0.1, mom_6=0.05)}
        ranked = score_universe(feats)
        self.assertEqual([r["symbol"] for r in ranked], ["A", "B"])

    def test_below_200dma_is_gated_out(self):
        feats = {"A": self._feats(), "B": self._feats(trend_pct=-3.0, mom_12_1=0.9)}
        self.assertEqual([r["symbol"] for r in score_universe(feats)], ["A"])

    def test_illiquid_is_gated_out(self):
        feats = {"A": self._feats(), "B": self._feats(turnover_cr=2.0, mom_12_1=0.9)}
        self.assertEqual([r["symbol"] for r in score_universe(feats)], ["A"])

    def test_lower_vol_wins_a_tie_on_everything_else(self):
        feats = {"CALM": self._feats(vol=0.2), "WILD": self._feats(vol=0.6)}
        self.assertEqual(score_universe(feats)[0]["symbol"], "CALM")

    def test_empty_universe(self):
        self.assertEqual(score_universe({}), [])


class TestBreadthAndWeights(unittest.TestCase):
    def _many(self, n_up, n_down):
        f = {"price": 1, "mom_12_1": 0, "mom_6": 0, "high_prox": 0.9, "vol": 0.3, "turnover_cr": 50.0}
        return {**{f"U{i}": {**f, "trend_pct": 4.0} for i in range(n_up)},
                **{f"D{i}": {**f, "trend_pct": -4.0} for i in range(n_down)}}

    def test_breadth_share_above_200dma(self):
        self.assertAlmostEqual(breadth(self._many(6, 4)), 0.6)

    def test_regime_filter_off_by_default_never_blocks(self):
        self.assertTrue(risk_on(self._many(1, 30)))

    def test_regime_filter_on_goes_risk_off_in_a_weak_market(self):
        cfg = ScannerConfig(regime_filter=True)
        self.assertFalse(risk_on(self._many(2, 30), cfg))
        self.assertTrue(risk_on(self._many(20, 10), cfg))

    def test_inverse_vol_gives_calm_stocks_more_weight(self):
        w = inverse_vol_weights([{"symbol": "CALM", "vol": 0.2}, {"symbol": "WILD", "vol": 0.4}], 0.9)
        self.assertGreater(w["CALM"], w["WILD"])
        self.assertAlmostEqual(sum(w.values()), 1.0, places=6)

    def test_weight_cap_leaves_excess_as_cash(self):
        w = inverse_vol_weights([{"symbol": "A", "vol": 0.3}, {"symbol": "B", "vol": 0.3}], 0.15)
        self.assertEqual(w, {"A": 0.15, "B": 0.15})


class TestQuality(unittest.TestCase):
    def test_good_profile_passes(self):
        ok, fails, _ = quality_check({"roe_pct": 18, "debt_to_equity": 0.4, "profit_cagr_5y": 12, "pe_ratio": 25})
        self.assertTrue(ok)
        self.assertEqual(fails, [])

    def test_failures_are_explained(self):
        ok, fails, _ = quality_check({"roe_pct": 4, "debt_to_equity": 5.0, "profit_cagr_5y": -10, "pe_ratio": 200})
        self.assertFalse(ok)
        self.assertEqual(len(fails), 4)

    def test_banks_are_exempt_from_the_debt_test(self):
        ok, _, _ = quality_check({"roe_pct": 15, "debt_to_equity": 9.0, "sector": "Financial Services"})
        self.assertTrue(ok)

    def test_missing_data_never_rejects_but_is_flagged(self):
        ok, fails, missing = quality_check({"sector": "Energy"})
        self.assertTrue(ok)
        self.assertIn("ROE", missing)
        ok2, _, missing2 = quality_check(None)
        self.assertTrue(ok2)
        self.assertEqual(missing2, ["no fundamentals"])


class TestScan(unittest.TestCase):
    def setUp(self):
        self.prices = {}
        for k in range(20):                                    # 20 liquid uptrenders of rising strength
            self.prices[f"UP{k}"] = series(400, daily_drift=0.0004 + 0.0001 * k)
        for k in range(5):
            self.prices[f"DN{k}"] = series(400, daily_drift=-0.001)

    def test_picks_are_the_strongest_uptrenders(self):
        out = scan(self.prices)
        syms = [p["symbol"] for p in out["picks"]]
        self.assertEqual(len(syms), 12)
        self.assertTrue(all(s.startswith("UP") for s in syms))
        self.assertIn("UP19", syms)
        self.assertNotIn("UP0", syms)

    def test_weights_sum_to_at_most_one_and_respect_cap(self):
        out = scan(self.prices)
        ws = [p["weight"] for p in out["picks"]]
        self.assertLessEqual(sum(ws), 1.0 + 1e-6)
        self.assertLessEqual(max(ws), ScannerConfig().max_weight + 1e-9)

    def test_quality_failures_are_rejected_with_reasons(self):
        profiles = {"UP19": {"roe_pct": 2, "debt_to_equity": 0.1}}
        out = scan(self.prices, profiles)
        self.assertNotIn("UP19", [p["symbol"] for p in out["picks"]])
        self.assertEqual(out["rejected"][0]["symbol"], "UP19")
        self.assertTrue(out["rejected"][0]["reasons"])

    def test_stale_stock_is_dropped(self):
        prices = dict(self.prices)
        old = series(400, daily_drift=0.002)
        old["ts"] = [t - 60 * DAY for t in old["ts"]]          # last print 60 days before the others
        prices["OLD"] = old
        out = scan(prices)
        self.assertNotIn("OLD", [p["symbol"] for p in out["picks"]])

    def test_risk_off_means_no_weights(self):
        weak = {f"DN{k}": series(400, daily_drift=-0.001) for k in range(30)}
        weak["UP"] = series(400, daily_drift=0.002)
        out = scan(weak, cfg=ScannerConfig(regime_filter=True))
        self.assertFalse(out["risk_on"])
        self.assertEqual(out["invested_pct"], 0.0)


class TestBacktest(unittest.TestCase):
    def _histories(self, years=4):
        n = 365 * years
        h = {}
        for k in range(25):
            h[f"UP{k}"] = series(n, daily_drift=0.0003 + 0.00005 * k)
        for k in range(10):
            h[f"DN{k}"] = series(n, daily_drift=-0.0002)
        return h

    def test_momentum_beats_the_universe_on_persistent_trends(self):
        r = backtest(self._histories())
        self.assertGreater(r["strategy"]["cagr"], r["benchmark"]["cagr"])
        self.assertGreater(r["strategy"]["periods"], 20)

    def test_costs_reduce_returns(self):
        h = self._histories()
        free = backtest(h, ScannerConfig(cost_per_side=0.0))
        costly = backtest(h, ScannerConfig(cost_per_side=0.01))
        self.assertGreater(free["strategy"]["cagr"], costly["strategy"]["cagr"])

    def test_no_lookahead_future_prices_do_not_change_earlier_selection(self):
        h = self._histories(years=4)
        full = backtest(h)
        # truncate the last ~year: the earlier periods' returns must be identical
        cut = {s: {**v, "ts": v["ts"][:-365], "close": v["close"][:-365], "volume": v["volume"][:-365]} for s, v in h.items()}
        short = backtest(cut)
        y0 =sorted(short["strategy"]["yearly"])[0]
        self.assertAlmostEqual(full["strategy"]["yearly"][y0], short["strategy"]["yearly"][y0], places=6)

    def test_reports_target_year_count_and_caveats(self):
        r = backtest(self._histories())
        self.assertEqual(r["target_annual_return"], 0.25)
        self.assertLessEqual(r["years_meeting_target"], r["years_total"])
        self.assertTrue(r["caveats"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
