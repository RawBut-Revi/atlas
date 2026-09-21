"""
Tests for the multi-market layer: universe parsers, the per-stock history store, the study engine pieces
(turnover scale, active stats, random baseline, streaming prepare/run) and the study runner helpers.
Temp dirs and fake fetchers only: no network, no repo state files.
"""
import math
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd

from data.market_history import fetch_market, iter_histories, load_history, save_history, stored_count, _safe
from data.market_universe import (
    MARKETS, parse_asx200, parse_ftse100, parse_hang_seng, parse_kospi200, parse_nse_equity_csv, parse_sp500,
    parse_suffixed_tickers, parse_tsx60, universe,
)
from scoring.momentum_scanner import (
    ScannerConfig, active_stats, features_at, make_dates, prepare_periods, random_baseline, rank_periods,
    run_periods, summarize,
)
from run_market_study import market_cfg, index_cagr

IST = timezone(timedelta(hours=5, minutes=30))
DAY = 86400
START = int(datetime(2018, 1, 1, tzinfo=IST).timestamp())


def series(n, drift=0.0, wiggle=0.01, volume=5_000_000, price=100.0):
    return {"ts": [START + k * DAY for k in range(n)],
            "close": [price * math.exp(drift * k + wiggle * math.sin(k / 3.0)) for k in range(n)],
            "volume": [volume] * n, "events": []}


class TestParsers(unittest.TestCase):
    def test_nse_csv_keeps_only_regular_series(self):
        csv_text = ("SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING\n"
                    "TCS,Tata Consultancy,EQ,25-AUG-2004\n"
                    "JUNK,Surveillance Co,BE,01-JAN-2010\n"
                    "M&M,Mahindra,EQ,01-JAN-1990\n"
                    "TTT,Trade to trade,BZ,01-JAN-2015\n")
        out = parse_nse_equity_csv(csv_text.replace(", SERIES", ",SERIES"))
        self.assertEqual([e["symbol"] for e in out], ["TCS", "M&M"])
        self.assertEqual(out[1]["ticker"], "M&M.NS")

    def test_sp500_dots_become_dashes(self):
        out = parse_sp500(pd.DataFrame({"Symbol": ["AAPL", "BRK.B", None], "Security": ["Apple", "Berkshire", "x"]}))
        self.assertEqual([e["ticker"] for e in out], ["AAPL", "BRK-B"])

    def test_ftse_gets_l_suffix_and_dash(self):
        out = parse_ftse100(pd.DataFrame({"Ticker": ["AZN", "BT.A"], "Company": ["AstraZeneca", "BT"]}))
        self.assertEqual([e["ticker"] for e in out], ["AZN.L", "BT-A.L"])

    def test_dax_cac_keep_their_own_suffix_and_skip_untagged_rows(self):
        out = parse_suffixed_tickers(pd.DataFrame({"Ticker": ["ADS.DE", "AIR.PA", "NOSUFFIX"], "Company": ["a", "b", "c"]}))
        self.assertEqual([e["ticker"] for e in out], ["ADS.DE", "AIR.PA"])

    def test_hang_seng_codes_are_zero_padded(self):
        out = parse_hang_seng(pd.DataFrame({"Ticker": ["SEHK:\xa05", "SEHK:\xa01299"], "Name": ["HSBC", "AIA"]}))
        self.assertEqual([e["ticker"] for e in out], ["0005.HK", "1299.HK"])

    def test_asx_tsx_kospi(self):
        self.assertEqual(parse_asx200(pd.DataFrame({"Code": ["BHP"], "Company": ["BHP"]}))[0]["ticker"], "BHP.AX")
        tsx = parse_tsx60(pd.DataFrame({"Symbol": ["RY", "BIP.UN", None], "Company": ["a", "b", "c"]}))
        self.assertEqual([e["ticker"] for e in tsx], ["RY.TO", "BIP-UN.TO"])
        kospi = parse_kospi200(pd.DataFrame({"Symbol": ["5930", "090430", "ABC"], "Company": ["Samsung", "Amore", "bad"]}))
        self.assertEqual([e["ticker"] for e in kospi], ["005930.KS", "090430.KS"])

    def test_registry_is_complete_and_sane(self):
        self.assertIn("india_all", MARKETS)
        for spec in MARKETS.values():
            self.assertGreater(spec.cost_per_side, 0)
            self.assertGreater(spec.min_turnover, 0)
            self.assertTrue(spec.benchmark.startswith("^"))
        self.assertEqual(MARKETS["uk_ftse100"].turnover_scale, 0.01)          # LSE quotes pence


class TestUniverseCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def entries(self, n):
        return [{"symbol": f"S{k}", "ticker": f"S{k}.NS", "name": ""} for k in range(n)]

    def test_second_call_uses_cache(self):
        calls = []
        loader = lambda: calls.append(1) or self.entries(30)                  # noqa: E731
        universe("india_all", market_dir=self.tmp, loader=loader)
        universe("india_all", market_dir=self.tmp, loader=loader)
        self.assertEqual(len(calls), 1)

    def test_too_few_symbols_is_an_error_not_a_silent_empty_universe(self):
        with self.assertRaises(ValueError):
            universe("india_all", market_dir=self.tmp, loader=lambda: self.entries(3))

    def test_stale_cache_is_used_when_the_fetch_fails(self):
        universe("india_all", market_dir=self.tmp, loader=lambda: self.entries(30))

        def boom():
            raise RuntimeError("site down")
        self.assertEqual(len(universe("india_all", refresh=True, market_dir=self.tmp, loader=boom)), 30)


class TestHistoryStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.entries = [{"symbol": f"S{k}", "ticker": f"S{k}.NS", "name": ""} for k in range(5)]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_roundtrip_and_filename_safety(self):
        save_history("t", "M&M.NS", series(300), self.tmp)
        h = load_history("t", "M&M.NS", self.tmp)
        self.assertEqual(len(h["close"]), 300)
        self.assertEqual(_safe("M&M.NS"), "M_and_M.NS")

    def test_fetch_stores_successes_and_remembers_failures(self):
        def fetch(t):
            return (series(300), "ok") if t != "S3.NS" else (None, "not found")
        c = fetch_market("t", self.entries, market_dir=self.tmp, fetch=fetch, workers=2)
        self.assertEqual((c["fetched"], c["failed"]), (4, 1))
        self.assertEqual(stored_count("t", self.entries, self.tmp), 4)

    def test_resume_skips_fresh_files_and_known_failures(self):
        calls = []

        def fetch(t):
            calls.append(t)
            return (series(300), "ok") if t != "S3.NS" else (None, "not found")
        fetch_market("t", self.entries, market_dir=self.tmp, fetch=fetch, workers=2)
        calls.clear()
        c = fetch_market("t", self.entries, market_dir=self.tmp, fetch=fetch, workers=2)
        self.assertEqual(calls, [])
        self.assertEqual(c["skipped"], 5)

    def test_iter_streams_only_stored_stocks(self):
        save_history("t", "S1.NS", series(300), self.tmp)
        got = [s for s, _ in iter_histories("t", self.entries, self.tmp)]
        self.assertEqual(got, ["S1"])


class TestEngineMultiMarket(unittest.TestCase):
    def test_turnover_scale_lets_pence_quoted_stocks_pass_the_liquidity_floor(self):
        h = series(300, price=1000.0, volume=1000)               # 1000 pence * 1000 sh = 1e6 pence = 10k GBP/day
        raw = features_at(h["close"], h["volume"], 299)
        scaled = features_at(h["close"], h["volume"], 299, turnover_scale=0.01)
        self.assertAlmostEqual(scaled["turnover_cr"], raw["turnover_cr"] * 0.01, places=9)

    def test_market_cfg_swaps_only_cost_and_liquidity(self):
        cfg = market_cfg(MARKETS["uk_ftse100"])
        base = ScannerConfig()
        self.assertEqual(cfg.cost_per_side, 0.0035)
        self.assertEqual(cfg.turnover_scale, 0.01)
        for f in ("w_mom_12_1", "w_mom_6", "w_trend", "w_high", "vol_penalty", "top_n", "rebalance_days", "regime_filter"):
            self.assertEqual(getattr(cfg, f), getattr(base, f))          # the model itself is never tuned per market

    def test_active_stats_flags_a_consistent_edge_and_a_noisy_one(self):
        steady = active_stats([0.02 + 0.001 * (k % 2) for k in range(60)], [0.01] * 60, 30)
        self.assertGreater(steady["t_stat"], 5)
        self.assertEqual(steady["hit_rate_pct"], 100.0)
        noisy = active_stats([0.05 if k % 2 else -0.05 for k in range(60)], [0.0] * 60, 30)
        self.assertLess(abs(noisy["t_stat"]), 1.0)
        self.assertEqual(active_stats([0.1], [0.0], 30), {})

    def _prepared(self, n_up=25, n_dn=10, years=4):
        h = {f"UP{k}": series(365 * years, 0.0003 + 0.00005 * k) for k in range(n_up)}
        h.update({f"DN{k}": series(365 * years, -0.0002) for k in range(n_dn)})
        cfg = ScannerConfig()
        ref = max(h.values(), key=lambda x: len(x["ts"]))["ts"]
        dates, end = make_dates(ref, cfg)
        return prepare_periods(h.items(), dates, end, cfg), cfg

    def test_streaming_prepare_accepts_a_generator(self):
        h = {f"UP{k}": series(900, 0.0004) for k in range(20)}
        cfg = ScannerConfig()
        dates, end = make_dates(h["UP0"]["ts"], cfg)
        prepared = prepare_periods(((k, v) for k, v in h.items()), dates, end, cfg)
        self.assertEqual(len(prepared["table"]), len(dates))
        self.assertTrue(any(prepared["table"]))

    def test_random_baseline_is_reproducible_and_ranking_beats_it_on_persistent_trends(self):
        prepared, cfg = self._prepared()
        ranked, on = rank_periods(prepared, cfg)
        free = ScannerConfig(cost_per_side=0.0)
        a = random_baseline(prepared, ranked, on, free, runs=40, seed=3)
        b = random_baseline(prepared, ranked, on, free, runs=40, seed=3)
        self.assertEqual(a["cagrs"], b["cagrs"])
        top = run_periods(prepared, ranked, on, free)
        from scoring.momentum_scanner import _stats
        scanner_cagr = _stats(top["strat"], top["dates"], 30)["cagr"]
        self.assertGreater(scanner_cagr, a["median_cagr"])

    def test_summary_reports_turnover_and_a_before_cost_random_test(self):
        prepared, cfg = self._prepared()
        out = summarize(prepared, cfg, random_runs=30)
        self.assertGreaterEqual(out["turnover"]["cost_drag_per_year"], 0)
        self.assertGreater(out["turnover"]["cagr_before_costs"], out["strategy"]["cagr"])
        self.assertIn("before costs", out["random"]["note"])
        self.assertGreater(out["random"]["scanner_percentile"], 50)
        self.assertIn("t_stat", out["active"])

    def test_index_cagr_uses_the_window_only(self):
        ts = [START + k * DAY for k in range(730)]
        close = [100.0 * 1.0005 ** k for k in range(730)]
        c = index_cagr((ts, close), START, ts[-1])
        self.assertAlmostEqual(c, 1.0005 ** 365.25 - 1.0, places=2)
        self.assertIsNone(index_cagr((ts, close), ts[-1], ts[-1]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
