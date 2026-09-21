"""
Tests for the shadow ledger (inverse twins + shadow-only signals).
Uses a temp file: paper_positions.json and shadow_ledger.json are never touched.
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

from trading.fills import entry_fill
from trading.shadow import ShadowLedger, make_inverse, make_signal_twin, build_report, MAX_TWINS_PER_SYMBOL_PER_DAY


def _pos(symbol="USDINR", direction="BUY", pid="fx_1", asset="CURRENCY", strategy="TREND_MOMENTUM"):
    raw = 95.0
    return {
        "id": pid, "symbol": symbol, "direction": direction, "qty": 1, "lots": 1, "asset_type": asset,
        "strategy": strategy, "entry_price": entry_fill(direction, raw, asset),
        "stop_loss": 94.5 if direction == "BUY" else 95.5,
        "target_price": 96.0 if direction == "BUY" else 94.0,
        "target_1": 95.5, "target_2": 96.0, "sl_trailed_to_cost": True, "t1_booked": True,
        "rl_trail_tightness": 0.5, "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "status": "OPEN",
    }


class TestTwins(unittest.TestCase):
    def test_inverse_flips_direction_and_swaps_stop_and_target(self):
        real = _pos(direction="BUY")
        inv = make_inverse(real)
        self.assertEqual(inv["direction"], "SELL")
        self.assertEqual(inv["stop_loss"], real["target_price"])
        self.assertEqual(inv["target_price"], real["stop_loss"])
        self.assertEqual(inv["twin_of"], real["id"])
        self.assertEqual(inv["shadow_kind"], "INVERSE")

    def test_inverse_gets_its_own_adverse_fill_from_the_raw_signal_price(self):
        inv = make_inverse(_pos(direction="BUY"))
        # the real fill was rounded to 4 decimals before the raw price is recovered: allow 1-2 ticks
        self.assertAlmostEqual(inv["entry_price"], entry_fill("SELL", 95.0, "CURRENCY"), delta=0.0002)
        self.assertLess(inv["entry_price"], 95.0)  # a SELL fills below the signal price

    def test_inverse_resets_trailing_state(self):
        inv = make_inverse(_pos())
        self.assertNotIn("sl_trailed_to_cost", inv)
        self.assertNotIn("t1_booked", inv)

    def test_signal_twin_keeps_direction_and_levels(self):
        real = _pos(direction="SELL")
        sh = make_signal_twin(real)
        self.assertEqual(sh["direction"], "SELL")
        self.assertEqual(sh["stop_loss"], real["stop_loss"])
        self.assertEqual(sh["shadow_kind"], "SHADOW")


class TestLedger(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.remove(self.path)
        self.ledger = ShadowLedger(self.path)

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_record_persists_twin(self):
        self.assertTrue(self.ledger.record(_pos(), "INVERSE"))
        state = self.ledger.load()
        self.assertEqual(len(state["open_positions"]), 1)
        self.assertEqual(state["open_positions"][0]["shadow_kind"], "INVERSE")

    def test_same_symbol_same_kind_open_twin_is_not_duplicated(self):
        self.assertTrue(self.ledger.record(_pos(pid="a"), "INVERSE"))
        self.assertFalse(self.ledger.record(_pos(pid="b"), "INVERSE"))  # rescan of the same setup

    def test_different_kinds_can_coexist_for_one_symbol(self):
        self.assertTrue(self.ledger.record(_pos(pid="a"), "INVERSE"))
        self.assertTrue(self.ledger.record(_pos(pid="a"), "SHADOW"))

    def test_per_symbol_daily_cap(self):
        for i in range(MAX_TWINS_PER_SYMBOL_PER_DAY):
            self.assertTrue(self.ledger.record(_pos(pid=f"p{i}"), "INVERSE"))
            state = self.ledger.load()  # close it, as the daemon would
            twin = state["open_positions"].pop()
            state["trade_history"].append({**twin, "net_pnl": -10.0})
            self.ledger.save(state)
        self.assertFalse(self.ledger.record(_pos(pid="one_too_many"), "INVERSE"))

    def test_missing_file_gives_empty_ledger(self):
        state = self.ledger.load()
        self.assertEqual(state["open_positions"], [])
        self.assertEqual(state["trade_history"], [])


class TestReport(unittest.TestCase):
    @staticmethod
    def _closed(pos, kind, net):
        t = make_inverse(pos) if kind == "INVERSE" else make_signal_twin(pos)
        t["net_pnl"] = net
        return t

    def test_live_pair_compares_real_trade_to_inverse_twin(self):
        real = {**_pos(pid="r1"), "net_pnl": 100.0}
        state = {"trade_history": [self._closed(_pos(pid="r1"), "INVERSE", -130.0)]}
        rows = build_report(state, [real])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "LIVE")
        self.assertEqual(rows[0]["signal_net"], 100.0)
        self.assertEqual(rows[0]["inverse_net"], -130.0)

    def test_shadow_pair_uses_shadow_twin_as_signal_side(self):
        p = _pos(pid="s1", asset="EQUITY", strategy="GAP_AND_GO")
        state = {"trade_history": [self._closed(p, "SHADOW", 50.0), self._closed(p, "INVERSE", -80.0)]}
        rows = build_report(state, [])
        self.assertEqual(rows[0]["source"], "SHADOW")
        self.assertEqual(rows[0]["segment"], "EQUITY GAP_AND_GO")

    def test_unpaired_inverse_is_ignored(self):
        state = {"trade_history": [self._closed(_pos(pid="lonely"), "INVERSE", 10.0)]}
        self.assertEqual(build_report(state, []), [])

    def test_small_sample_says_need_more_and_never_recommends(self):
        real = {**_pos(pid="r1"), "net_pnl": 100.0}
        state = {"trade_history": [self._closed(_pos(pid="r1"), "INVERSE", 500.0)]}
        self.assertIn("need", build_report(state, [real])[0]["verdict"])

    def test_verdicts_with_enough_trades(self):
        def rows_for(signal_net, inverse_net, source):
            hist, state_hist = [], []
            for i in range(30):
                p = _pos(pid=f"x{i}", asset="EQUITY", strategy="GAP_AND_GO")
                if source == "LIVE":
                    hist.append({**p, "net_pnl": signal_net / 30})
                else:
                    state_hist.append(self._closed(p, "SHADOW", signal_net / 30))
                state_hist.append(self._closed(p, "INVERSE", inverse_net / 30))
            return build_report({"trade_history": state_hist}, hist)[0]["verdict"]

        self.assertEqual(rows_for(300, -400, "SHADOW"), "PROMOTE")
        self.assertEqual(rows_for(300, -400, "LIVE"), "KEEP")
        self.assertEqual(rows_for(-300, -400, "SHADOW"), "DROP")
        self.assertEqual(rows_for(-300, 200, "LIVE"), "INVERSE better")


if __name__ == "__main__":
    unittest.main(verbosity=2)
