"""
Tests for the order-flow backtest. Synthetic data only: no network.
Feature tests build small hand-made sessions; strategy tests inject the feature columns directly so each
rule (window, 2-trades-a-day, sizing, GEX, balance days, EOD flat) is exercised on its own.
"""
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

from orderflow_bt.costs import nse_intraday_commission, round_trip_charges
from orderflow_bt.features import FeatureConfig, build_features, drop_filler, proxy_flow, session_profile, wilder_smooth
from orderflow_bt.plotting import plot_day
from orderflow_bt.runner import VARIANTS, enrich, pool, run_control, run_symbol, wilson_ci
from orderflow_bt.strategy import OrderFlowStrategy as OrderFlowStrategyDefaults, _ceil_tick, _floor_tick
from trading.charges import calculate_trade_charges

BARS = 355                                                    # 09:15 .. 15:09


def day_index(d0, n=BARS):
    return pd.DatetimeIndex([datetime(2026, 3, 2, 9, 15) + timedelta(days=d0) + timedelta(minutes=m) for m in range(n)])


def make_days(n_days=4, seed=0, base=100.0):
    """Random-walk sessions with random volume; OHLC always consistent."""
    rng = np.random.default_rng(seed)
    frames, px = [], base
    for d in range(n_days):
        idx = day_index(d)
        c = px * np.exp(np.cumsum(rng.normal(0, 0.0004, BARS)))
        o = np.r_[c[0], c[:-1]]
        h = np.maximum(o, c) * (1 + rng.random(BARS) * 0.0004)
        l = np.minimum(o, c) * (1 - rng.random(BARS) * 0.0004)
        frames.append(pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": rng.integers(1000, 9000, BARS)}, index=idx))
        px = c[-1]
    return pd.concat(frames)


class TestCosts(unittest.TestCase):
    def test_callable_sums_to_the_exact_round_trip(self):
        for entry, exit_, qty, side in [(1300.0, 1302.0, 500, "BUY"), (1300.0, 1297.5, 500, "SELL"), (250.0, 250.4, 40, "BUY")]:
            ref = calculate_trade_charges("X", "EQUITY", side, entry, exit_, qty).total_charges
            got = nse_intraday_commission(qty, entry) + nse_intraday_commission(qty, exit_)
            self.assertAlmostEqual(got, ref, delta=ref * 0.005, msg=f"{side} {entry}->{exit_} x{qty}")

    def test_costs_scale_and_flat_fee_cap(self):
        small, big = round_trip_charges(100.0, 10), round_trip_charges(1300.0, 500)
        self.assertLess(small, big)
        self.assertGreater(big / (1300 * 500), 0.0004)         # roughly 0.05-0.06% of turnover round trip
        self.assertLess(big / (1300 * 500), 0.0008)


class TestConfidence(unittest.TestCase):
    def test_wilson_interval_is_wide_for_few_trades(self):
        lo, hi = wilson_ci(4, 5)
        self.assertAlmostEqual(lo, 37.6, delta=0.3)
        self.assertAlmostEqual(hi, 96.4, delta=0.3)
        lo2, hi2 = wilson_ci(400, 500)
        self.assertLess(hi2 - lo2, 8)                              # 500 trades pin the win rate down
        self.assertIsNone(wilson_ci(0, 0))


class TestTickRounding(unittest.TestCase):
    def test_float_noise_does_not_lose_a_tick(self):
        self.assertEqual(_floor_tick(100.6), 100.6)            # 100.6 / 0.05 = 2011.9999999999998
        self.assertEqual(_floor_tick(100.64), 100.60)
        self.assertEqual(_ceil_tick(100.6), 100.6)
        self.assertEqual(_ceil_tick(100.61), 100.65)
        self.assertEqual(_floor_tick(99.5), 99.5)


class TestProxies(unittest.TestCase):
    def test_close_location_split(self):
        o = np.array([10.0, 10.0, 10.0, 10.0]); h = np.array([11.0, 11.0, 11.0, 10.0]); l = np.array([9.0, 9.0, 9.0, 10.0])
        c = np.array([11.0, 9.0, 10.0, 10.0]); v = np.array([100.0, 100.0, 100.0, 100.0])
        buy, sell, delta = proxy_flow(o, h, l, c, v)
        np.testing.assert_allclose(buy, [100, 0, 50, 50])      # high close = all buy, low close = all sell, flat = 50/50
        np.testing.assert_allclose(buy + sell, v)
        np.testing.assert_allclose(delta, [100, -100, 0, 0])

    def test_wilder_smooth_constant(self):
        out = wilder_smooth(np.full(40, 2.0), 14)
        self.assertTrue(np.isnan(out[12]))
        self.assertAlmostEqual(out[13], 2.0)
        self.assertAlmostEqual(out[39], 2.0)

    def test_filler_bars_are_dropped(self):
        df = make_days(1).iloc[:5].copy()
        df.iloc[2] = [100, 100, 100, 100, 0]
        self.assertEqual(len(drop_filler(df)), 4)


class TestProfile(unittest.TestCase):
    def test_two_clusters_become_two_hvn_zones(self):
        h = np.r_[np.full(50, 100.3), np.full(50, 110.3), np.linspace(101, 109, 40)]
        l = np.r_[np.full(50, 99.7), np.full(50, 109.7), np.linspace(100.5, 108.5, 40)]
        v = np.r_[np.full(50, 5000.0), np.full(50, 4000.0), np.full(40, 100.0)]
        p = session_profile(h, l, v, FeatureConfig())
        self.assertEqual(len(p["zones"]), 2)
        peaks = sorted(z[2] for z in p["zones"])
        self.assertLess(abs(peaks[0] - 100.0), 0.5)
        self.assertLess(abs(peaks[1] - 110.0), 0.5)
        self.assertLess(abs(p["poc"] - 100.0), 0.5)             # the heavier cluster

    def test_value_area_is_poc_plus_minus_sigma(self):
        rng = np.random.default_rng(1)
        px = rng.normal(100, 2, 4000)
        h, l = px + 0.05, px - 0.05
        p = session_profile(h, l, np.ones(4000), FeatureConfig(n_bins=60))
        self.assertAlmostEqual(p["sigma"], 2.0, delta=0.15)
        self.assertAlmostEqual(p["va_hi"] - p["poc"], p["sigma"], places=9)
        self.assertAlmostEqual(p["poc"] - p["va_lo"], p["sigma"], places=9)
        self.assertLess(abs(p["poc"] - 100), 0.8)


class TestFeatures(unittest.TestCase):
    def test_first_session_has_no_signals(self):
        f = build_features(make_days(2, seed=3))
        d0 = f[f.index.normalize() == f.index[0].normalize()]
        self.assertTrue(d0["poc"].isna().all())
        self.assertTrue((d0[["hvn_long", "abs_long", "div_bull"]].sum().sum()) == 0)

    def test_imbalance_state_from_the_open(self):
        base = make_days(2, seed=5)
        d1 = base[base.index.normalize() == base.index[0].normalize()]
        prof = session_profile(d1["High"].to_numpy(), d1["Low"].to_numpy(), d1["Volume"].to_numpy(), FeatureConfig())
        for shift, want in ((prof["va_hi"] + 5 - base["Open"].iloc[BARS], 1.0), (prof["va_lo"] - 5 - base["Open"].iloc[BARS], -1.0),
                            (prof["poc"] - base["Open"].iloc[BARS], 0.0)):
            df = base.copy()
            df.iloc[BARS:, :4] = df.iloc[BARS:, :4] + shift        # move day 2 so its open lands above / below / inside yesterday's value area
            f = build_features(df)
            self.assertEqual(f["imb"].iloc[BARS], want)
            self.assertTrue((f["imb"].iloc[BARS:] == want).all())  # constant for the whole session

    def test_no_look_ahead_rows_match_when_data_is_truncated(self):
        df = make_days(4, seed=11)
        full = build_features(df)
        for cut in (BARS + 40, 2 * BARS + 173, 3 * BARS + 10):
            part = build_features(df.iloc[:cut])
            pd.testing.assert_frame_equal(part, full.iloc[:cut], check_exact=False, rtol=1e-9, atol=1e-9)

    def test_hvn_touch_and_next_hvn_target(self):
        prior = make_days(1, seed=2).copy()
        n = len(prior)
        prior[["Open", "High", "Low", "Close"]] = 100.0
        prior.iloc[:150, [1]] = 100.3; prior.iloc[:150, [2]] = 99.7
        prior.iloc[150:250, [1]] = 105.3; prior.iloc[150:250, [2]] = 104.7
        prior.iloc[250:, [1]] = 100.3; prior.iloc[250:, [2]] = 99.7     # rest of the session stays inside the first node
        prior["Volume"] = np.full(n, 6000)
        today = make_days(1, seed=4); today.index = today.index + timedelta(days=1)
        today[["Open", "High", "Low", "Close"]] = 101.5              # away from the zones
        today.iloc[20:30, [1]] = 100.4; today.iloc[20:30, [2]] = 100.0; today.iloc[20:30, [0]] = 100.2; today.iloc[20:30, [3]] = 100.2
        f = build_features(pd.concat([prior, today]))
        t = f[f.index.normalize() == today.index[0].normalize()]
        self.assertEqual(t["hvn_long"].iloc[25], 1.0)
        self.assertEqual(t["hvn_long"].iloc[5], 0.0)                 # 101.5 is between the nodes
        self.assertLess(abs(t["hvn_lvl"].iloc[25] - 100.0), 0.5)
        self.assertLess(abs(t["next_hvn_up"].iloc[25] - 105.0), 0.6)   # the next node above

    def make_session_with_lows(self):
        """Prior day + a session engineered for a bullish delta divergence: price lower low at bar 30, delta higher low."""
        prior = make_days(1, seed=7)
        idx = day_index(1)
        o = np.full(BARS, 101.0); h = np.full(BARS, 101.4); l = np.full(BARS, 100.6); c = np.full(BARS, 101.0)
        v = np.full(BARS, 1000.0)
        c[0:10] = 100.6                                              # sell-heavy: closes on the low  -> delta strongly negative
        l[10] = 100.0; h[10] = 100.7; c[10] = 100.2                  # pivot low #1 (bar 10)
        c[11:30] = 101.4                                             # buy-heavy: closes on the high  -> delta recovers
        l[30] = 99.0; h[30] = 101.0; c[30] = 100.9                   # pivot low #2: LOWER low in price, closes high (delta up)
        l[31:] = 100.6
        h[np.arange(BARS) == 30] = 101.0
        today = pd.DataFrame({"Open": o, "High": np.maximum(h, np.maximum(o, c)), "Low": np.minimum(l, np.minimum(o, c)),
                              "Close": c, "Volume": v}, index=idx)
        return pd.concat([prior, today])

    def test_delta_divergence_flag_appears_only_after_confirmation(self):
        f = build_features(self.make_session_with_lows())
        t = f[f.index.normalize() == day_index(1)[0].normalize()]
        self.assertGreater(t["cumdelta"].iloc[30], t["cumdelta"].iloc[10])          # delta made a HIGHER low
        self.assertLess(t["Low"].iloc[30], t["Low"].iloc[10])                       # while price made a LOWER low
        cfg = FeatureConfig()
        self.assertEqual(t["div_bull"].iloc[30 + cfg.pivot_k - 1], 0.0)             # pivot not yet confirmed
        self.assertEqual(t["div_bull"].iloc[30 + cfg.pivot_k], 1.0)                 # confirmed k bars later, not before
        self.assertEqual(t["div_bull"].iloc[30 + cfg.pivot_k + cfg.div_valid_bars], 1.0)
        self.assertEqual(t["div_bull"].iloc[30 + cfg.pivot_k + cfg.div_valid_bars + 1], 0.0)   # expires
        self.assertEqual(t["div_bear"].sum(), 0.0)

    def absorption_frame(self, closes_on_low=True, new_low_at_end=False):
        prior = make_days(1, seed=9)
        idx = day_index(1)
        n = BARS
        o = np.full(n, 101.0); h = np.full(n, 101.3); l = np.full(n, 100.8); c = np.full(n, 101.05); v = np.full(n, 1000.0)
        for i in range(30, 35):                                       # 5-bar selling window pushing to a lower low
            l[i] = 100.0 - 0.02 * (i - 30); h[i] = l[i] + 0.3
            c[i] = l[i] + (0.0 if closes_on_low else 0.15); o[i] = h[i]
        l[35] = 99.5 if new_low_at_end else 99.95; h[35] = 100.4; c[35] = 100.3 if not new_low_at_end else 99.6; o[35] = 100.2
        today = pd.DataFrame({"Open": o, "High": np.maximum(h, np.maximum(o, c)), "Low": np.minimum(l, np.minimum(o, c)),
                              "Close": c, "Volume": v}, index=idx)
        return pd.concat([prior, today])

    def test_absorption_detected_when_sellers_dominate_but_low_holds(self):
        # window = bars 31..35: closes on lows for 31..34, bar 35 holds above the low
        f = build_features(self.absorption_frame())
        t = f[f.index.normalize() == day_index(1)[0].normalize()]
        self.assertEqual(t["abs_long"].iloc[35], 1.0)
        self.assertAlmostEqual(t["abs_lvl_long"].iloc[35], t["Low"].iloc[31:36].min(), places=6)
        self.assertEqual(t["abs_short"].iloc[35], 0.0)

    def test_no_absorption_if_price_breaks_down_or_ratio_is_low(self):
        broke = build_features(self.absorption_frame(new_low_at_end=True))
        tb = broke[broke.index.normalize() == day_index(1)[0].normalize()]
        self.assertEqual(tb["abs_long"].iloc[35], 0.0)
        mild = build_features(self.absorption_frame(closes_on_low=False))
        tm = mild[mild.index.normalize() == day_index(1)[0].normalize()]
        self.assertEqual(tm["abs_long"].iloc[35], 0.0)               # sell proxy is not 5x buy

    def test_short_absorption_is_the_mirror(self):
        m = self.absorption_frame()
        mirror = m.copy()
        mirror["Open"], mirror["Close"] = 200 - m["Open"], 200 - m["Close"]
        mirror["High"], mirror["Low"] = 200 - m["Low"], 200 - m["High"]
        f = build_features(mirror)
        t = f[f.index.normalize() == day_index(1)[0].normalize()]
        self.assertEqual(t["abs_short"].iloc[35], 1.0)
        self.assertEqual(t["abs_long"].iloc[35], 0.0)

    def test_gex_is_joined_by_date_and_absent_by_default(self):
        df = make_days(2, seed=1)
        self.assertTrue(build_features(df)["gex"].isna().all())
        g = pd.Series([-5.0, 3.0], index=pd.to_datetime([df.index[0].date(), df.index[BARS].date()]))
        f = build_features(df, gex=g)
        self.assertTrue((f["gex"].iloc[:BARS] == -5.0).all())
        self.assertTrue((f["gex"].iloc[BARS:] == 3.0).all())


def blank(n_days=2, price=100.0):
    """Feature frame with every column present and no signals: tests set flags where they want them."""
    idx = pd.DatetimeIndex(list(day_index(0)) + list(day_index(1))[:BARS]) if n_days == 2 else day_index(0)
    df = pd.DataFrame({"Open": price, "High": price + 0.1, "Low": price - 0.1, "Close": price, "Volume": 1000.0}, index=idx)
    day = df.index.normalize()
    ts = df.index.to_series()
    df["minute"] = (ts - ts.groupby(day).transform("first")).dt.total_seconds() / 60.0
    df["atr"], df["delta"], df["cumdelta"], df["poc"] = 1.0, 0.0, 0.0, price
    df["imb"] = 1.0
    for c in ("hvn_long", "hvn_short", "abs_long", "abs_short", "div_bull", "div_bear"):
        df[c] = 0.0
    for c in ("hvn_lvl", "next_hvn_up", "next_hvn_dn", "abs_lvl_long", "abs_lvl_short", "gex"):
        df[c] = np.nan
    df["va_lo"], df["va_hi"], df["day_open"] = price - 1, price + 1, price
    return df


def signal_long(df, pos, win=True):
    """Flag a long at bar `pos`; the next bar either hits the 101.0 target (win) or the 99.5 stop."""
    df.iloc[pos, df.columns.get_loc("hvn_long")] = 1.0
    if win:
        df.iloc[pos + 1, df.columns.get_loc("High")] = 101.2
    else:
        df.iloc[pos + 1, df.columns.get_loc("Low")] = 99.3
    return df


ONLY_HVN = dict(req_hvn=True, req_absorb=False, req_delta=False, req_imb=False)


class TestStrategyRules(unittest.TestCase):
    def test_long_trade_sizing_target_and_costs(self):
        df = signal_long(blank(), 30)
        _, j, _ = run_symbol(df, ONLY_HVN, "T")
        self.assertEqual(len(j), 1)
        t = j.iloc[0]
        self.assertEqual(t["direction"], "LONG")
        self.assertEqual(t["entry_time"], df.index[31])               # market order fills at the NEXT bar's open
        self.assertEqual(t["size"], int(150_000 * 0.0075 / 0.5))      # 0.75% risk / (0.5 x ATR of 1.0)
        self.assertEqual(t["exit_reason"], "target")
        self.assertAlmostEqual(t["take_profit"], 101.0)
        self.assertAlmostEqual(t["stop_loss"], 99.5)
        self.assertAlmostEqual(t["planned_rr"], 2.0, places=2)
        self.assertGreater(t["fees"], 0)
        self.assertLess(t["pnl"], t["size"] * 1.0)                    # gross 2250 minus real NSE costs

    def test_stop_loss_exit(self):
        _, j, _ = run_symbol(signal_long(blank(), 30, win=False), ONLY_HVN, "T")
        self.assertEqual(j.iloc[0]["exit_reason"], "stop_loss")
        self.assertLess(j.iloc[0]["pnl"], -1000)

    def test_short_mirror(self):
        df = blank()
        df.iloc[30, df.columns.get_loc("hvn_short")] = 1.0
        df.iloc[31, df.columns.get_loc("Low")] = 98.8
        _, j, _ = run_symbol(df, ONLY_HVN, "T")
        t = j.iloc[0]
        self.assertEqual((t["direction"], t["exit_reason"]), ("SHORT", "target"))
        self.assertAlmostEqual(t["stop_loss"], 100.5)
        self.assertAlmostEqual(t["take_profit"], 99.0)

    def test_no_trades_after_the_first_two_hours(self):
        df = signal_long(blank(), 125)                               # minute 125 > 120
        _, j, _ = run_symbol(df, ONLY_HVN, "T")
        self.assertEqual(len(j), 0)
        df2 = signal_long(blank(), 119)
        self.assertEqual(len(run_symbol(df2, ONLY_HVN, "T")[1]), 1)

    def test_max_two_trades_a_day(self):
        df = blank()
        for pos in (20, 30, 40, 50):
            signal_long(df, pos)
        _, j, _ = run_symbol(df, ONLY_HVN, "T")
        self.assertEqual(len(j), 2)
        df2 = blank()
        signal_long(df2, 20)
        signal_long(df2, BARS + 20)                                  # a new day resets the counter
        self.assertEqual(len(run_symbol(df2, ONLY_HVN, "T")[1]), 2)

    def test_balance_day_takes_no_trade_when_imbalance_is_required(self):
        df = signal_long(blank(), 30)
        df["imb"] = 0.0
        strict = dict(req_hvn=True, req_absorb=False, req_delta=False, req_imb=True)
        self.assertEqual(len(run_symbol(df, strict, "T")[1]), 0)
        df["imb"] = -1.0                                             # imbalance DOWN forbids the long
        self.assertEqual(len(run_symbol(df, strict, "T")[1]), 0)
        df["imb"] = 1.0
        self.assertEqual(len(run_symbol(df, strict, "T")[1]), 1)

    def test_every_required_concept_must_agree(self):
        df = signal_long(blank(), 30)
        df["imb"] = 1.0
        allreq = dict(req_hvn=True, req_absorb=True, req_delta=True, req_imb=True)
        self.assertEqual(len(run_symbol(df, allreq, "T")[1]), 0)     # HVN alone is not enough
        i = df.columns.get_loc
        df.iloc[30, i("abs_long")] = 1.0; df.iloc[30, i("abs_lvl_long")] = 99.6
        self.assertEqual(len(run_symbol(df, allreq, "T")[1]), 0)     # still no delta
        df.iloc[30, i("div_bull")] = 1.0; df.iloc[30, i("delta")] = -5.0
        self.assertEqual(len(run_symbol(df, allreq, "T")[1]), 0)     # divergence but negative bar delta
        df.iloc[30, i("delta")] = 5.0
        j = run_symbol(df, allreq, "T")[1]
        self.assertEqual(len(j), 1)
        self.assertAlmostEqual(j.iloc[0]["stop_loss"], 99.5, places=2)   # 0.5 ATR (99.5) is farther than absorption 99.6 - tick

    def test_stop_is_the_farther_of_atr_and_absorption_level(self):
        df = signal_long(blank(), 30)
        i = df.columns.get_loc
        df.iloc[30, i("abs_long")] = 1.0; df.iloc[30, i("abs_lvl_long")] = 98.8   # farther than 0.5 ATR
        j = run_symbol(df, dict(req_hvn=True, req_absorb=True, req_delta=False, req_imb=False), "T")[1]
        self.assertAlmostEqual(j.iloc[0]["stop_loss"], 98.75, places=2)

    def test_target_is_the_nearer_of_next_hvn_and_2r(self):
        df = signal_long(blank(), 30)
        df.iloc[30, df.columns.get_loc("next_hvn_up")] = 100.6       # closer than 2R (101.0)
        df.iloc[31, df.columns.get_loc("High")] = 100.7
        j = run_symbol(df, ONLY_HVN, "T")[1]
        self.assertAlmostEqual(j.iloc[0]["take_profit"], 100.6, places=2)
        self.assertEqual(j.iloc[0]["exit_reason"], "target")

    def test_positions_are_flat_by_end_of_day(self):
        df = blank()
        df.iloc[30, df.columns.get_loc("hvn_long")] = 1.0             # nothing ever hits stop or target
        _, j, _ = run_symbol(df, ONLY_HVN, "T", eod_minute=13 * 60)      # these synthetic sessions end 15:09, so flatten at 13:00
        t = j.iloc[0]
        self.assertEqual(t["exit_reason"], "eod_or_other")
        self.assertEqual(pd.Timestamp(t["exit_time"]).strftime("%H:%M"), "13:01")   # decided at the 13:00 close, filled at the next open
        self.assertEqual(OrderFlowStrategyDefaults.eod_minute, 15 * 60 + 10)   # the real default is before the 15:15 feed gap

    def test_gex_gating(self):
        df = signal_long(blank(), 30)
        req = dict(gex_mode="require")
        self.assertEqual(len(run_symbol(df, ONLY_HVN, "T", **req)[1]), 0)          # no reading: cannot trade
        df["gex"] = 0.0
        self.assertEqual(len(run_symbol(df, ONLY_HVN, "T", **req)[1]), 0)          # near zero: skip
        df["gex"] = 500.0
        self.assertEqual(len(run_symbol(df, ONLY_HVN, "T", **req)[1]), 0)          # positive gamma: skip
        half = run_symbol(df, ONLY_HVN, "T", gex_mode="require", pos_gamma="half")[1]
        df["gex"] = -500.0
        full = run_symbol(df, ONLY_HVN, "T", **req)[1]
        self.assertEqual(len(full), 1)
        self.assertEqual(len(half), 1)
        self.assertEqual(half.iloc[0]["size"], full.iloc[0]["size"] // 2)          # positive gamma trades at 50% size
        df["gex"] = 3.0
        self.assertEqual(len(run_symbol(df, ONLY_HVN, "T", gex_mode="require", gex_eps=5.0)[1]), 0)   # |3| <= eps -> "near zero"

    def test_ambiguous_double_signal_is_skipped_not_guessed(self):
        df = signal_long(blank(), 30)
        df.iloc[30, df.columns.get_loc("hvn_short")] = 1.0
        _, j, rej = run_symbol(df, ONLY_HVN, "T")
        self.assertEqual(len(j), 0)
        self.assertEqual(rej["ambiguous_both_directions"], 1)

    def test_random_control_is_seeded_and_obeys_the_same_rules(self):
        df = blank()
        none = dict(req_hvn=False, req_absorb=False, req_delta=False, req_imb=False)
        a = run_symbol(df, none, "T", random_p=0.05, seed=3)[1]
        b = run_symbol(df, none, "T", random_p=0.05, seed=3)[1]
        pd.testing.assert_frame_equal(a, b)
        self.assertLessEqual(a.groupby(pd.to_datetime(a["entry_time"]).dt.date).size().max(), 2)
        self.assertTrue((a["entry_time"].apply(lambda t: (pd.Timestamp(t) - pd.Timestamp(t).normalize()).total_seconds() / 60 - 555) < 121).all())


class TestJournalAndPooling(unittest.TestCase):
    def test_enrich_columns(self):
        _, j, _ = run_symbol(signal_long(blank(), 30), ONLY_HVN, "SYM")
        for col in ("symbol", "direction", "entry_time", "exit_time", "entry", "exit", "size", "pnl", "fees", "planned_rr",
                    "r_multiple", "exit_reason", "stop_loss", "take_profit"):
            self.assertIn(col, j.columns)
        self.assertEqual(j.iloc[0]["symbol"], "SYM")
        self.assertGreater(j.iloc[0]["r_multiple"], 0)
        self.assertTrue(np.isfinite(j["r_multiple"]).all())

    def test_pool_orders_by_exit_time_and_sums(self):
        a = pd.DataFrame({"pnl": [10.0], "exit_time": [pd.Timestamp("2026-03-03")]})
        b = pd.DataFrame({"pnl": [-4.0], "exit_time": [pd.Timestamp("2026-03-02")]})
        t, eq = pool([a, b], 100.0)
        self.assertEqual(list(t["pnl"]), [-4.0, 10.0])
        self.assertEqual(eq.iloc[-1], 106.0)

    def test_control_runs_all_seeds(self):
        runs = run_control({"T": blank()}, seeds=(0, 1), p=0.03)
        self.assertEqual(len(runs), 2)

    def test_variants_are_the_predeclared_set(self):
        self.assertEqual(len(VARIANTS), 9)
        self.assertTrue(all(k in v for v in VARIANTS.values() for k in ("req_hvn", "req_absorb", "req_delta", "req_imb")))
        strict = next(iter(VARIANTS.values()))
        self.assertTrue(all(strict.values()))


class TestPlot(unittest.TestCase):
    def test_plot_writes_a_png(self):
        tmp = tempfile.mkdtemp()
        try:
            f = build_features(make_days(3, seed=6))
            path = os.path.join(tmp, "x.png")
            self.assertTrue(plot_day(f, f.index[BARS + 5].date(), path, "SYN"))
            self.assertGreater(os.path.getsize(path), 5000)
            self.assertFalse(plot_day(f, f.index[0].date(), os.path.join(tmp, "none.png"), "SYN"))   # no prior session
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
