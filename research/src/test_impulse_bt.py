"""
Tests for the Impulse + Consolidation + Breakout backtest. Hand-built candle sequences, no network.
Session = 26 fifteen-minute bars, 09:30..15:45. Blocked bars: 09:30, 09:45 (first 30 min) and 15:30, 15:45 (last 30 min).
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

from impulse_bt.data import clean, load_csv, session_filter
from impulse_bt.indicators import atr_wilder, true_range
from impulse_bt.metrics import max_drawdown, pool, summarize
from impulse_bt.strategy import Params, run_backtest
from impulse_bt.volume_profile import volume_profile

DAY0 = datetime(2026, 3, 2, 9, 30)          # a Monday


def make_df(bars):
    """bars: list of (o, h, l, c, v). 26 bars per day, weekdays only."""
    idx, day = [], 0
    for k in range(len(bars)):
        if k and k % 26 == 0:
            day += 1
        d = DAY0 + timedelta(days=day + 2 * ((day + 0) // 5))
        idx.append(d + timedelta(minutes=15 * (k % 26)))
    return pd.DataFrame(bars, columns=["open", "high", "low", "close", "volume"], index=pd.DatetimeIndex(idx))


WARM = (100.0, 100.2, 99.8, 100.0, 1000)
IMPULSE_UP = (100.0, 101.6, 99.9, 101.5, 9000)
CONSOL = [(101.50, 101.60, 101.45, 101.55, 1000), (101.55, 101.60, 101.48, 101.50, 3000),
          (101.50, 101.58, 101.47, 101.52, 5000), (101.52, 101.60, 101.48, 101.50, 4000),
          (101.50, 101.55, 101.46, 101.48, 2000)]
BREAKOUT_UP = (101.52, 101.90, 101.49, 101.85, 6000)
FLAT = (101.65, 101.65, 101.65, 101.65, 500)


def levels():
    """POC / VAH / VAL of the reference consolidation."""
    a = np.array(CONSOL)
    return volume_profile(a[:, 1], a[:, 2], a[:, 4])


def long_day(tail, n_warm=14, params=None):
    """warm-up + impulse + 5 consolidation candles + breakout + tail, padded to 26 bars."""
    bars = [WARM] * n_warm + [IMPULSE_UP] + CONSOL + [BREAKOUT_UP] + tail
    bars += [FLAT] * (26 - len(bars))
    return make_df(bars)


def mirror(df, axis=200.0):
    m = df.copy()
    m["open"], m["close"] = axis - df["open"], axis - df["close"]
    m["high"], m["low"] = axis - df["low"], axis - df["high"]
    return m


PROT = Params(sl_mode="protective")


class TestIndicators(unittest.TestCase):
    def test_true_range_uses_previous_close(self):
        tr = true_range([10, 12], [9, 11.5], [9.5, 12])
        self.assertEqual(tr[0], 1.0)
        self.assertAlmostEqual(tr[1], 2.5)                     # |12 - 9.5|

    def test_wilder_atr_constant_range(self):
        h, l, c = np.full(30, 10.5), np.full(30, 9.5), np.full(30, 10.0)
        a = atr_wilder(h, l, c, 14)
        self.assertTrue(np.isnan(a[12]))
        self.assertAlmostEqual(a[13], 1.0)
        self.assertAlmostEqual(a[29], 1.0)

    def test_wilder_recursion(self):
        h = np.array([2.0] * 14 + [5.0]); l = np.array([1.0] * 14 + [1.0]); c = np.array([1.5] * 14 + [1.5])
        a = atr_wilder(h, l, c, 14)
        self.assertAlmostEqual(a[14], (1.0 * 13 + 4.0) / 14)

    def test_matches_pandas_ta_when_available(self):
        try:
            import pandas_ta as ta
        except ImportError:
            self.skipTest("pandas_ta not installed in this environment")
        rng = np.random.default_rng(0)
        c = 100 + rng.normal(0, 1, 300).cumsum(); h = c + rng.random(300); l = c - rng.random(300)
        ref = ta.atr(pd.Series(h), pd.Series(l), pd.Series(c), length=14).to_numpy()
        mine = atr_wilder(h, l, c, 14)
        self.assertLess(np.nanmax(np.abs(ref[100:] - mine[100:])), 1e-3)     # converged region


class TestVolumeProfile(unittest.TestCase):
    def test_volume_conserved_and_ordering(self):
        p = levels()
        self.assertAlmostEqual(p["volumes"].sum(), p["total"])
        self.assertLessEqual(p["val"], p["poc"])
        self.assertLessEqual(p["poc"], p["vah"])

    def test_value_area_holds_70_percent(self):
        p = levels()
        inside = p["volumes"][(p["edges"][:-1] >= p["val"] - 1e-9) & (p["edges"][1:] <= p["vah"] + 1e-9)].sum()
        self.assertGreaterEqual(inside / p["total"], 0.70 - 1e-9)

    def test_poc_is_the_heaviest_bin(self):
        p = levels()
        centres = (p["edges"][:-1] + p["edges"][1:]) / 2
        k = int(np.argmin(np.abs(centres - p["poc"])))
        self.assertAlmostEqual(p["volumes"][k], p["volumes"].max())        # the POC bin is a maximum (ties go to the middle-most)
        self.assertAlmostEqual(p["poc"], centres[k], places=9)

    def test_uniform_spread_over_overlapped_bins(self):
        p = volume_profile([2.0], [0.0], [100.0], n_bins=4)
        np.testing.assert_allclose(p["volumes"], [25, 25, 25, 25])

    def test_zero_range_and_zero_volume(self):
        p = volume_profile([5, 5], [5, 5], [10, 20])
        self.assertEqual((p["poc"], p["vah"], p["val"]), (5.0, 5.0, 5.0))
        self.assertIsNone(volume_profile([5, 6], [4, 5], [0, 0]))

    def test_single_heavy_candle_dominates(self):
        p = volume_profile([10, 10.1, 10.2], [9.9, 10.0, 10.1], [1, 1000, 1], n_bins=6)
        self.assertGreater(p["poc"], 9.99)
        self.assertLess(p["vah"] - p["val"], 0.31)


class TestData(unittest.TestCase):
    def test_clean_drops_impossible_candles_and_duplicates(self):
        idx = pd.DatetimeIndex([DAY0, DAY0 + timedelta(minutes=15), DAY0 + timedelta(minutes=15), DAY0 + timedelta(minutes=30)])
        df = pd.DataFrame({"open": [1, 1, 1, 1], "high": [2, 0.5, 2, 2], "low": [0.5, 0.4, 0.5, 0.5],
                           "close": [1.5, 1, 1.5, np.nan], "volume": [10, 10, 10, 10]}, index=idx)
        out = clean(df)
        self.assertEqual(len(out), 2)                       # bad high<open row and NaN row removed; duplicate removed

    def test_session_filter(self):
        idx = pd.date_range("2026-03-02 08:00", periods=40, freq="15min")
        df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1}, index=idx)
        out = session_filter(df, "09:30", "16:00")
        self.assertEqual(out.index[0].strftime("%H:%M"), "09:30")
        self.assertEqual(len(out), 26)                                          # exactly one 09:30..15:45 session
        self.assertTrue(all(t < pd.Timestamp("16:00").time() for t in out.index.time))

    def test_load_csv_flexible_columns_and_tz(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "x.csv")
            pd.DataFrame({"Datetime": ["2026-03-02 14:30:00+00:00", "2026-03-02 14:45:00+00:00"], "Open": [1, 1], "High": [2, 2],
                          "Low": [1, 1], "Close": [1.5, 1.5], "Volume": [10, 10]}).to_csv(path, index=False)
            df = load_csv(path, tz="America/New_York")
            self.assertEqual(df.index[0].strftime("%H:%M"), "09:30")           # 14:30 UTC = 09:30 New York (EST)
            self.assertIsNone(df.index.tz)
            with open(os.path.join(tmp, "bad.csv"), "w") as fh:
                fh.write("foo,bar\n1,2\n")
            with self.assertRaises(ValueError):
                load_csv(os.path.join(tmp, "bad.csv"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestRules(unittest.TestCase):
    def test_reference_pattern_sanity(self):
        lv = levels()
        self.assertGreater(lv["poc"], 101.49)                 # TP above the limit entry, otherwise the fixtures are wrong
        self.assertLess(lv["poc"], BREAKOUT_UP[3])
        self.assertLess(lv["vah"], BREAKOUT_UP[3])

    def test_valid_long_full_lifecycle(self):
        tail = [(101.60, 101.70, 101.48, 101.60, 800), (101.60, 101.70, 101.55, 101.65, 800)]
        r = run_backtest(long_day(tail), PROT, "T")
        self.assertEqual(len(r.trades), 1, r.rejected.to_string() if len(r.rejected) else "")
        t = r.trades.iloc[0]
        self.assertEqual(t["direction"], "LONG")
        self.assertEqual(t["limit_entry"], 101.49)
        self.assertEqual(t["stop_loss"], 101.45)              # protective: consolidation swing LOW
        self.assertAlmostEqual(t["take_profit"], levels()["poc"], places=3)
        self.assertEqual(t["consol_candles"], 5)
        self.assertEqual(t["exit_reason"], "take_profit_gap")
        self.assertGreater(t["pnl"], 0)
        self.assertEqual(t["signal_time"], pd.Timestamp("2026-03-02 14:30"))
        self.assertEqual(t["fill_time"], pd.Timestamp("2026-03-02 14:45"))

    def test_sizing_formula_and_leverage_cap(self):
        r = run_backtest(long_day([(101.60, 101.70, 101.48, 101.60, 800)]), PROT, "T")
        t = r.trades.iloc[0]
        self.assertEqual(t["qty"], int(100_000 / 101.49))    # 1% risk would be 25,000 shares: capped by 1x leverage
        self.assertTrue(bool(t["size_capped"]))
        loose = run_backtest(long_day([(101.60, 101.70, 101.48, 101.60, 800)]), Params(sl_mode="protective", max_leverage=1000.0), "T")
        self.assertEqual(loose.trades.iloc[0]["qty"], int(100_000 * 0.01 / (101.49 - 101.45)))   # the spec's formula
        self.assertFalse(bool(loose.trades.iloc[0]["size_capped"]))

    def test_literal_stop_placement_is_geometrically_impossible(self):
        r = run_backtest(long_day([(101.60, 101.70, 101.48, 101.60, 800)]), Params(sl_mode="literal"), "T")
        self.assertEqual(len(r.trades), 0)
        rej = r.rejected[r.rejected["reason"] == "invalid_geometry"]
        self.assertEqual(len(rej), 1)
        self.assertEqual(rej.iloc[0]["need"], "SL < entry < TP")
        self.assertGreater(rej.iloc[0]["sl"], rej.iloc[0]["entry"])          # swing high sits above the entry

    def test_short_is_the_exact_mirror(self):
        tail = [(101.60, 101.70, 101.48, 101.60, 800), (101.60, 101.70, 101.55, 101.65, 800)]
        long_r = run_backtest(long_day(tail), PROT, "L")
        short_r = run_backtest(mirror(long_day(tail)), PROT, "S")
        self.assertEqual(len(short_r.trades), 1, short_r.rejected.to_string() if len(short_r.rejected) else "")
        s = short_r.trades.iloc[0]
        self.assertEqual(s["direction"], "SHORT")
        self.assertAlmostEqual(s["limit_entry"], 200 - 101.49, places=6)
        self.assertAlmostEqual(s["stop_loss"], 200 - 101.45, places=6)      # protective short: swing HIGH
        self.assertGreater(s["pnl"], 0)
        self.assertAlmostEqual(s["take_profit"], 200 - long_r.trades.iloc[0]["take_profit"], places=3)

    def test_signal_in_blocked_window_is_not_traded(self):
        bars = [WARM] * 18 + [IMPULSE_UP] + CONSOL + [BREAKOUT_UP]           # breakout lands on bar 24 = 15:30
        bars += [FLAT] * (26 - len(bars))
        r = run_backtest(make_df(bars), PROT, "T")
        self.assertEqual(len(r.trades), 0)
        self.assertIn("signal_bar_in_blocked_window", set(r.rejected["reason"]))

    def test_no_fill_during_blocked_window_then_expiry(self):
        mid = (101.49 + levels()["poc"]) / 2
        quiet = (mid, mid, mid, mid, 100)                     # above the limit, below the target: neither triggers
        tail = [quiet] * 3 + [(101.6, 101.6, 101.30, 101.6, 100)]            # bar 24 (15:30, blocked) touches the limit
        bars = [WARM] * 14 + [IMPULSE_UP] + CONSOL + [BREAKOUT_UP] + tail
        bars += [FLAT] * (26 - len(bars))
        # breakout is bar 20 (14:30); bars 21-23 quiet; bar 24 touches limit but is blocked; bar 25 is past expiry
        r = run_backtest(make_df(bars), PROT, "T")
        self.assertEqual(len(r.trades), 0)
        self.assertIn("order_expired_bars", set(r.rejected["reason"]))

    def test_order_cancelled_when_target_hit_before_fill(self):
        r = run_backtest(long_day([(101.7, 101.9, 101.6, 101.8, 100)]), PROT, "T")   # never dips to the limit, trades through POC
        self.assertEqual(len(r.trades), 0)
        self.assertIn("target_reached_before_fill", set(r.rejected["reason"]))

    def test_stop_loss_is_minus_one_r(self):
        tail = [(101.60, 101.70, 101.48, 101.55, 800), (101.55, 101.56, 101.30, 101.40, 800)]
        r = run_backtest(long_day(tail), Params(sl_mode="protective", max_leverage=1000.0), "T")
        t = r.trades.iloc[0]
        self.assertEqual(t["exit_reason"], "stop_loss")
        self.assertEqual(t["exit"], 101.45)
        self.assertAlmostEqual(t["r_multiple"], -1.0, places=2)

    def test_stop_wins_when_both_levels_hit_in_one_bar(self):
        tail = [(101.60, 101.70, 101.48, 101.55, 800), (101.50, 101.90, 101.30, 101.60, 800)]   # range spans SL and TP
        r = run_backtest(long_day(tail), PROT, "T")
        self.assertEqual(r.trades.iloc[0]["exit_reason"], "stop_loss")

    def test_stop_only_on_the_fill_bar(self):
        tail = [(101.60, 101.90, 101.30, 101.80, 800)]         # fills at 101.49, wick through TP AND SL in the same bar
        r = run_backtest(long_day(tail), PROT, "T")
        self.assertEqual(r.trades.iloc[0]["exit_reason"], "stop_loss")

    def test_no_pattern_no_trade(self):
        rng = np.random.default_rng(1)
        px, bars = 100.0, []
        for _ in range(26 * 5):
            o = px; px = px * (1 + rng.normal(0, 0.0004)); bars.append((o, max(o, px) + 0.05, min(o, px) - 0.05, px, 1000))
        r = run_backtest(make_df(bars), PROT, "T")
        self.assertEqual(len(r.trades), 0)                    # nothing impulsive: strict rules -> no trades

    def test_consolidation_not_tight_is_rejected(self):
        sloppy = [(101.5, 101.9, 101.1, 101.5, 1000)] * 5     # each candle ~0.8 wide > 0.5 ATR
        bars = [WARM] * 14 + [IMPULSE_UP] + sloppy + [BREAKOUT_UP]
        bars += [FLAT] * (26 - len(bars))
        r = run_backtest(make_df(bars), PROT, "T")
        self.assertEqual(len(r.trades), 0)
        self.assertIn("consolidation_not_tight", set(r.rejected["reason"]))

    def test_consolidation_needs_min_candles(self):
        bars = [WARM] * 14 + [IMPULSE_UP] + CONSOL[:4] + [BREAKOUT_UP]      # only 4 candles before the breakout
        bars += [FLAT] * (26 - len(bars))
        r = run_backtest(make_df(bars), PROT, "T")
        self.assertEqual(len(r.trades) + len(r.rejected[r.rejected["reason"] == "invalid_geometry"]), 0)

    def test_pattern_may_not_cross_sessions(self):
        bars = [WARM] * 24 + [IMPULSE_UP, CONSOL[0]]                        # impulse at 15:30, rest of the pattern next day
        bars += CONSOL[1:] + [BREAKOUT_UP] + [FLAT] * 20
        r = run_backtest(make_df(bars), PROT, "T")
        self.assertEqual(len(r.trades), 0)
        self.assertIn("consolidation_crossed_session", set(r.rejected["reason"]))

    def test_require_impulse_direction_blocks_counter_trend(self):
        down_break = (101.52, 101.55, 101.10, 101.15, 6000)                 # closes below VAL after an UP impulse
        bars = [WARM] * 14 + [IMPULSE_UP] + CONSOL + [down_break]
        bars += [FLAT] * (26 - len(bars))
        r = run_backtest(make_df(bars), Params(sl_mode="protective", require_impulse_direction=True), "T")
        self.assertIn("breakout_against_impulse_direction", set(r.rejected["reason"]))
        self.assertEqual(len(r.trades), 0)

    def test_time_stop_and_end_of_data_close_positions(self):
        stay = [(101.60, 101.62, 101.48, 101.55, 800)]
        between = (101.50, 101.51, 101.495, 101.50, 100)                  # above the stop, below the target (POC ~101.52)
        r = run_backtest(long_day(stay + [between] * 4), Params(sl_mode="protective", max_hold_bars=3), "T")
        self.assertEqual(r.trades.iloc[0]["exit_reason"], "time_stop")
        r2 = run_backtest(long_day(stay), PROT, "T")
        self.assertIn(r2.trades.iloc[0]["exit_reason"], ("end_of_data", "take_profit_gap", "take_profit"))

    def test_costs_reduce_pnl(self):
        tail = [(101.60, 101.70, 101.48, 101.60, 800), (101.60, 101.70, 101.55, 101.65, 800)]
        free = run_backtest(long_day(tail), PROT, "T").trades.iloc[0]
        paid = run_backtest(long_day(tail), Params(sl_mode="protective", commission_per_share=0.005), "T").trades.iloc[0]
        self.assertAlmostEqual(free["pnl"] - paid["pnl"], 2 * 0.005 * free["qty"], places=2)


class TestIntegrity(unittest.TestCase):
    def build_multi(self, reps=6):
        bars = []
        for _ in range(reps):
            bars += [WARM] * 14 + [IMPULSE_UP] + CONSOL + [BREAKOUT_UP, (101.60, 101.70, 101.48, 101.60, 800),
                                                              (101.60, 101.70, 101.55, 101.65, 800)]
            bars += [FLAT] * (26 - (14 + 1 + 5 + 1 + 2))
        return make_df(bars)

    def test_one_position_at_a_time(self):
        r = run_backtest(self.build_multi(), PROT, "T")
        self.assertGreaterEqual(len(r.trades), 3)
        t = r.trades.sort_values("fill_time")
        starts, ends = list(t["fill_time"]), list(t["exit_time"])
        for k in range(1, len(t)):
            self.assertLessEqual(ends[k - 1], starts[k])

    def test_no_look_ahead_future_bars_cannot_change_past_decisions(self):
        df = self.build_multi(3)
        base = run_backtest(df, PROT, "T")
        cut = 26 + 22                                                         # after day-2's breakout, before its outcome bars settle
        wrecked = df.copy()
        wrecked.iloc[cut:, :4] = wrecked.iloc[cut:, :4].to_numpy()[::-1] * 3.0    # scramble everything after `cut`
        alt = run_backtest(wrecked, PROT, "T")
        cols = ["signal_time", "limit_entry", "stop_loss", "take_profit", "poc", "vah", "val"]
        b, a = base.trades[cols].iloc[:2].reset_index(drop=True), alt.trades[cols].iloc[:2].reset_index(drop=True)
        pd.testing.assert_frame_equal(b, a)                                   # first two setups identical

    def test_equity_curve_and_journal_fields(self):
        r = run_backtest(self.build_multi(), PROT, "T")
        self.assertEqual(len(r.equity), r.n_bars)
        self.assertFalse(r.equity.isna().any())
        self.assertAlmostEqual(r.equity.iloc[-1], 100_000 + r.trades["pnl"].sum(), places=1)
        for col in ("entry_reason", "exit_reason", "pnl", "r_multiple", "entry", "exit", "fill_time", "exit_time", "planned_rr"):
            self.assertIn(col, r.trades.columns)


class TestMetrics(unittest.TestCase):
    def test_summary_numbers(self):
        tr = pd.DataFrame({"pnl": [100.0, -50.0, 200.0, -50.0], "r_multiple": [2, -1, 4, -1], "planned_rr": [1.0] * 4,
                           "bars_held": [3, 2, 5, 4], "direction": ["LONG", "LONG", "SHORT", "LONG"],
                           "exit_reason": ["take_profit", "stop_loss", "take_profit", "stop_loss"]})
        eq = pd.Series([1000, 1100, 1050, 1250, 1200.0])
        s = summarize(tr, eq, 1000.0)
        self.assertEqual(s["trades"], 4)
        self.assertEqual(s["win_rate_pct"], 50.0)
        self.assertAlmostEqual(s["profit_factor"], 300 / 100)
        self.assertEqual(s["net_pnl"], 200.0)
        self.assertEqual(s["longest_losing_streak"], 1)
        self.assertEqual(s["short_trades"], 1)

    def test_drawdown(self):
        dd, pct = max_drawdown(pd.Series([100, 120, 90, 130, 110.0]))
        self.assertEqual(dd, 30.0)
        self.assertAlmostEqual(pct, 0.25)

    def test_empty_trades_are_handled(self):
        s = summarize(pd.DataFrame(), pd.Series([1.0, 1.0]), 1.0)
        self.assertEqual(s["trades"], 0)
        self.assertIsNone(s["win_rate_pct"])

    def test_pool_orders_by_exit_time(self):
        class R:
            def __init__(self, t):
                self.trades, self.params = t, Params()
        a = pd.DataFrame({"pnl": [10.0], "exit_time": [pd.Timestamp("2026-03-03")]})
        b = pd.DataFrame({"pnl": [-5.0], "exit_time": [pd.Timestamp("2026-03-02")]})
        t, eq = pool([R(a), R(b)], 100.0)
        self.assertEqual(list(t["pnl"]), [-5.0, 10.0])
        self.assertEqual(eq.iloc[-1], 105.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
