"""
Tests for the total-return DRIP engine: costs, scoring, planner, ledger, dividend parsing, runner.
No network and no real state files: every test works in a temp directory with stubbed snapshots.
"""
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8")

from data.dividend_events import parse_dividend_events, parse_snapshot, trailing_12m_dps
from data.fundamental_data import STOCK_FUNDAMENTALS as F
from scoring.drip_ledger import DripLedger, fiscal_year
from scoring.drip_planner import PlannerConfig, plan_drip
from scoring.drip_runner import PaperDeliveryBroker, is_market_open, run_drip_cycle
from scoring.total_return_score import score_stock, score_universe
from trading.charges import calculate_delivery_buy_charges

IST = timezone(timedelta(hours=5, minutes=30))
WED_OPEN = datetime(2026, 9, 16, 11, 0, tzinfo=IST)     # Wednesday, market hours
SAT = datetime(2026, 9, 19, 11, 0, tzinfo=IST)          # Saturday


class TestDeliveryCharges(unittest.TestCase):
    def test_ten_thousand_rupee_buy(self):
        c = calculate_delivery_buy_charges(100.0, 100)          # Rs10,000
        self.assertAlmostEqual(c["stt"], 10.0, places=2)
        self.assertAlmostEqual(c["stamp"], 1.5, places=2)
        self.assertAlmostEqual(c["brokerage"], 20.0, places=2)
        self.assertAlmostEqual(c["total"], 35.5, delta=0.2)

    def test_brokerage_capped_by_percentage_on_tiny_order(self):
        c = calculate_delivery_buy_charges(10.0, 1)             # Rs10 order: 2.5% = Rs0.25 < Rs20
        self.assertAlmostEqual(c["brokerage"], 0.25, places=2)

    def test_zero_qty_costs_nothing(self):
        self.assertEqual(calculate_delivery_buy_charges(100.0, 0)["total"], 0.0)


class TestScore(unittest.TestCase):
    def test_yield_uses_live_price_and_ttm_dps(self):
        s = score_stock("ITC", F["ITC"], 250.0, ttm_dps=10.0)
        self.assertAlmostEqual(s["dividend_yield_pct"], 4.0, places=2)

    def test_falling_price_raises_yield(self):
        hi = score_stock("ITC", F["ITC"], 400.0, ttm_dps=14.5)["dividend_yield_pct"]
        lo = score_stock("ITC", F["ITC"], 300.0, ttm_dps=14.5)["dividend_yield_pct"]
        self.assertGreater(lo, hi)

    def test_downtrend_cannot_rank_on_yield_alone(self):
        flat = score_stock("ITC", F["ITC"], 300.0, ttm_dps=14.5, trend_pct=0.0)
        crash = score_stock("ITC", F["ITC"], 300.0, ttm_dps=14.5, trend_pct=-40.0)
        self.assertLess(crash["efficiency"], flat["efficiency"])
        self.assertTrue(any("yield-trap" in f for f in crash["flags"]))

    def test_uptrend_adds_capital_appreciation_credit(self):
        base = score_stock("TCS", F["TCS"], F["TCS"]["current_price"], trend_pct=0.0)
        up = score_stock("TCS", F["TCS"], F["TCS"]["current_price"], trend_pct=10.0)
        self.assertGreater(up["efficiency"], base["efficiency"])

    def test_stale_table_skips_pe_rescale_and_cuts_confidence(self):
        s = score_stock("HDFCBANK", F["HDFCBANK"], 731.0)       # table says 1680: bonus/stale
        self.assertEqual(s["valuation_adj_pct"], 0.0)
        self.assertTrue(any("stale" in f for f in s["flags"]))
        ok = score_stock("HDFCBANK", F["HDFCBANK"], F["HDFCBANK"]["current_price"])
        self.assertLess(s["quality_mult"], ok["quality_mult"])

    def test_growth_is_capped(self):
        s = score_stock("COALINDIA", F["COALINDIA"], F["COALINDIA"]["current_price"])
        self.assertLessEqual(s["growth_pct"], 13.5)

    def test_bad_price_rejected_and_universe_omits_unpriced(self):
        with self.assertRaises(ValueError):
            score_stock("ITC", F["ITC"], 0.0)
        out = score_universe({"ITC": F["ITC"], "TCS": F["TCS"]}, {"ITC": 262.0})
        self.assertEqual(list(out), ["ITC"])


def mk(sym, eff, sector="S", price=None):
    return {"symbol": sym, "sector": sector, "efficiency": eff, "expected_return_pct": eff,
            "dividend_yield_pct": 3.0, "growth_pct": 8.0, "valuation_adj_pct": 0.0, "quality_mult": 0.9}


class TestPlanner(unittest.TestCase):
    prices = {"A": 100.0, "B": 200.0, "C": 300.0, "D": 400.0}

    def test_best_efficiency_bought_first_and_cash_conserved(self):
        scores = {"A": mk("A", 12, "S1"), "B": mk("B", 20, "S2"), "C": mk("C", 15, "S3")}
        plan = plan_drip({}, self.prices, scores, 30000.0, PlannerConfig(max_stock_weight_pct=100, max_sector_weight_pct=100))
        self.assertEqual(plan.orders[0].symbol, "B")
        spent = sum(o.est_value + o.est_charges for o in plan.orders)
        self.assertAlmostEqual(plan.cash_spent, spent, delta=0.05)
        self.assertAlmostEqual(plan.cash_spent + plan.carry, 30000.0, delta=0.05)
        self.assertGreaterEqual(plan.carry, 0)

    def test_stock_cap_forces_diversification(self):
        scores = {"A": mk("A", 12, "S1"), "B": mk("B", 20, "S2"), "C": mk("C", 15, "S3")}
        cfg = PlannerConfig(max_stock_weight_pct=40, max_sector_weight_pct=100)
        plan = plan_drip({}, self.prices, scores, 30000.0, cfg)
        self.assertGreater(len(plan.orders), 1)
        for o in plan.orders:
            self.assertLessEqual(o.weight_after_pct, 40.0 + 0.01)

    def test_sector_cap(self):
        scores = {"A": mk("A", 20, "SAME"), "B": mk("B", 19, "SAME"), "C": mk("C", 15, "OTHER")}
        cfg = PlannerConfig(max_stock_weight_pct=100, max_sector_weight_pct=50)
        plan = plan_drip({}, self.prices, scores, 40000.0, cfg)
        same = sum(o.est_value for o in plan.orders if o.symbol in ("A", "B"))
        self.assertLessEqual(same, 0.5 * plan.base_value + 1.0)
        self.assertIn("C", [o.symbol for o in plan.orders])

    def test_existing_holding_counts_toward_cap(self):
        scores = {"A": mk("A", 20, "S1"), "B": mk("B", 15, "S2")}
        cfg = PlannerConfig(max_stock_weight_pct=30, max_sector_weight_pct=100)
        plan = plan_drip({"A": 500}, self.prices, scores, 10000.0, cfg)   # A already 50000 of 60000
        self.assertNotIn("A", [o.symbol for o in plan.orders])
        self.assertTrue(any(s == "A" and "cap" in r for s, r in plan.skipped))

    def test_small_cash_carries_forward(self):
        plan = plan_drip({}, self.prices, {"A": mk("A", 20)}, 1500.0)
        self.assertEqual(plan.orders, [])
        self.assertEqual(plan.carry, 1500.0)

    def test_flat_brokerage_makes_small_order_too_costly(self):
        cfg = PlannerConfig(min_order_value=0, max_cost_pct=0.5, max_stock_weight_pct=100, max_sector_weight_pct=100)
        plan = plan_drip({}, self.prices, {"A": mk("A", 20)}, 3000.0, cfg)   # ~Rs23.6 + STT etc = >0.5%
        self.assertEqual(plan.orders, [])
        self.assertTrue(any("charges" in r for _, r in plan.skipped))

    def test_min_efficiency_and_blocked(self):
        scores = {"A": mk("A", 5), "B": mk("B", 20)}
        plan = plan_drip({}, self.prices, scores, 20000.0, PlannerConfig(max_stock_weight_pct=100, max_sector_weight_pct=100),
                         blocked=frozenset({"B"}))
        self.assertEqual(plan.orders, [])
        reasons = dict(plan.skipped)
        self.assertEqual(reasons["B"], "blocked")
        self.assertIn("efficiency", reasons["A"])

    def test_max_orders(self):
        scores = {k: mk(k, 20 - i, k) for i, k in enumerate("ABCD")}
        cfg = PlannerConfig(max_orders=2, max_stock_weight_pct=100, max_sector_weight_pct=100)
        self.assertLessEqual(len(plan_drip({}, self.prices, scores, 200000.0, cfg).orders), 2)

    def test_limit_price_is_tick_aligned_above_ltp(self):
        plan = plan_drip({}, {"A": 262.3}, {"A": mk("A", 20)}, 20000.0,
                         PlannerConfig(max_stock_weight_pct=100, max_sector_weight_pct=100))
        lp = plan.orders[0].limit_price
        self.assertGreaterEqual(lp, 262.3)
        self.assertAlmostEqual(round(lp / 0.05) * 0.05, lp, places=6)

    def test_no_cash_no_orders(self):
        self.assertEqual(plan_drip({"A": 10}, self.prices, {"A": mk("A", 20)}, 0.0).orders, [])


class TestLedger(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "ledger.json")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_credit_once_only(self):
        led = DripLedger(self.path, tracking_start="2026-09-01")
        ev = {"ITC": [{"ex_date": "2026-09-10", "dps": 8.0}]}
        first = led.credit_dividends(ev, {"ITC": 100}, "2026-09-16")
        second = led.credit_dividends(ev, {"ITC": 100}, "2026-09-16")
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(led.cash_pool, 800.0)

    def test_ignores_events_before_tracking_start_and_future(self):
        led = DripLedger(self.path, tracking_start="2026-09-01")
        ev = {"ITC": [{"ex_date": "2026-05-27", "dps": 8.0}, {"ex_date": "2026-10-01", "dps": 8.0}]}
        self.assertEqual(led.credit_dividends(ev, {"ITC": 100}, "2026-09-16"), [])

    def test_no_holding_no_credit(self):
        led = DripLedger(self.path, tracking_start="2026-09-01")
        self.assertEqual(led.credit_dividends({"ITC": [{"ex_date": "2026-09-10", "dps": 8.0}]}, {}, "2026-09-16"), [])

    def test_tds_only_above_threshold(self):
        led = DripLedger(self.path, tracking_start="2026-04-01", tds_threshold=10000.0)
        small = led.credit_dividends({"ITC": [{"ex_date": "2026-05-27", "dps": 8.0}]}, {"ITC": 1000}, "2026-09-16")
        self.assertEqual(small[0]["tds"], 0.0)                  # Rs8,000 <= 10,000
        big = led.credit_dividends({"ITC": [{"ex_date": "2026-09-10", "dps": 6.5}]}, {"ITC": 1000}, "2026-09-16")
        self.assertAlmostEqual(big[0]["tds"], 650.0, places=2)  # cumulative 14,500 > 10,000: 10% of this payment
        self.assertAlmostEqual(big[0]["net"], 5850.0, places=2)

    def test_fiscal_year(self):
        self.assertEqual(fiscal_year("2026-03-31"), "FY2025-26")
        self.assertEqual(fiscal_year("2026-04-01"), "FY2026-27")

    def test_persistence_roundtrip_and_debit_floor(self):
        led = DripLedger(self.path, tracking_start="2026-09-01")
        led.credit_dividends({"ITC": [{"ex_date": "2026-09-10", "dps": 8.0}]}, {"ITC": 100}, "2026-09-16")
        led.save()
        again = DripLedger(self.path)
        self.assertEqual(again.cash_pool, 800.0)
        self.assertEqual(again.state["tracking_start"], "2026-09-01")
        again.debit(5000.0)
        self.assertEqual(again.cash_pool, 0.0)
        self.assertFalse(any(f.endswith(".tmp") for f in os.listdir(self.dir)))


class TestDividendParsing(unittest.TestCase):
    def chart(self):
        ts = int(datetime(2026, 5, 27, 5, 30, tzinfo=IST).timestamp())
        closes = [100.0 + i * 0.1 for i in range(250)]
        return {"chart": {"result": [{"meta": {"regularMarketPrice": 125.0},
                "events": {"dividends": {str(ts): {"amount": 8.0, "date": ts}, "0": {"amount": 0, "date": 1}}},
                "indicators": {"quote": [{"close": closes}]}}]}}

    def test_events_parsed_and_zero_dropped(self):
        ev = parse_dividend_events(self.chart())
        self.assertEqual(ev, [{"ex_date": "2026-05-27", "dps": 8.0}])

    def test_snapshot_price_events_trend(self):
        s = parse_snapshot(self.chart())
        self.assertEqual(s["price"], 125.0)
        self.assertEqual(len(s["events"]), 1)
        self.assertGreater(s["trend_pct"], 0)                   # price above its 200d mean

    def test_bad_payloads_return_empty(self):
        self.assertEqual(parse_dividend_events({}), [])
        self.assertIsNone(parse_snapshot({"chart": {"result": None}}))

    def test_trailing_12m(self):
        ev = [{"ex_date": "2025-05-28", "dps": 7.85}, {"ex_date": "2026-02-04", "dps": 6.5}, {"ex_date": "2026-05-27", "dps": 8.0}]
        self.assertAlmostEqual(trailing_12m_dps(ev, date(2026, 9, 16)), 14.5, places=2)


class TestMarketHours(unittest.TestCase):
    def test_hours(self):
        self.assertTrue(is_market_open(WED_OPEN))
        self.assertFalse(is_market_open(WED_OPEN.replace(hour=9, minute=14)))
        self.assertTrue(is_market_open(WED_OPEN.replace(hour=15, minute=30)))
        self.assertFalse(is_market_open(WED_OPEN.replace(hour=15, minute=31)))
        self.assertFalse(is_market_open(SAT))


class TestRunner(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.pf = os.path.join(self.dir, "portfolio.json")
        self.lf = os.path.join(self.dir, "ledger.json")
        self.kill = os.path.join(self.dir, "DRIP_DISABLED")
        self.funds = {k: F[k] for k in ("COALINDIA", "ONGC", "ITC")}
        prices = {"COALINDIA": 410.0, "ONGC": 233.0, "ITC": 262.0}
        self.snap = lambda s: {"price": prices[s], "trend_pct": 0.0,
                               "events": [{"ex_date": "2026-09-10", "dps": 8.0}] if s == "ITC" else []}
        self.broker = PaperDeliveryBroker(self.pf)
        self.broker.state["holdings"] = {"ITC": {"qty": 1000, "cost": 250000.0}}
        self.ledger = DripLedger(self.lf, tracking_start="2026-09-01")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_cycle(self, **kw):
        kw.setdefault("now", WED_OPEN)
        return run_drip_cycle(self.broker, self.ledger, self.funds, self.snap, kill_switch_path=self.kill, **kw)

    def test_dry_run_writes_nothing(self):
        rep = self.run_cycle(execute=False)
        self.assertEqual(rep["status"], "PLANNED")
        self.assertEqual(rep["credits"][0]["net"], 8000.0)
        self.assertTrue(rep["plan"]["orders"])
        self.assertFalse(os.path.exists(self.pf))
        self.assertFalse(os.path.exists(self.lf))

    def test_execute_buys_best_stock_and_keeps_books_consistent(self):
        rep = self.run_cycle(execute=True)
        self.assertEqual(rep["status"], "EXECUTED")
        filled = [e for e in rep["executed"] if e["status"] == "FILLED"]
        self.assertTrue(filled)
        self.assertEqual(filled[0]["symbol"], "COALINDIA")          # highest efficiency in this set
        self.assertIn("COALINDIA", self.broker.get_holdings())
        self.assertAlmostEqual(self.broker.get_funds(), self.ledger.cash_pool, places=2)
        self.assertLess(self.ledger.cash_pool, 8000.0)
        self.assertGreaterEqual(self.ledger.cash_pool, 0.0)
        self.assertTrue(os.path.exists(self.pf) and os.path.exists(self.lf))

    def test_second_run_is_idempotent(self):
        self.run_cycle(execute=True)
        pool, holdings = self.ledger.cash_pool, dict(self.broker.get_holdings())
        again = DripLedger(self.lf)
        rep = run_drip_cycle(PaperDeliveryBroker(self.pf), again, self.funds, self.snap,
                             now=WED_OPEN, execute=True, kill_switch_path=self.kill)
        self.assertEqual(rep["credits"], [])
        self.assertEqual(rep["executed"], [])
        self.assertEqual(again.cash_pool, pool)
        self.assertEqual(PaperDeliveryBroker(self.pf).get_holdings(), holdings)

    def test_market_closed_persists_credit_but_places_no_orders(self):
        rep = self.run_cycle(execute=True, now=SAT)
        self.assertEqual(rep["status"], "MARKET_CLOSED")
        self.assertEqual(rep["executed"], [])
        self.assertEqual(DripLedger(self.lf).cash_pool, 8000.0)
        self.assertNotIn("COALINDIA", PaperDeliveryBroker(self.pf).get_holdings())

    def test_kill_switch_stops_everything(self):
        open(self.kill, "w").close()
        rep = self.run_cycle(execute=True)
        self.assertEqual(rep["status"], "DISABLED")
        self.assertFalse(os.path.exists(self.lf))
        self.assertFalse(os.path.exists(self.pf))

    def test_spend_cap_trims_orders(self):
        rep = self.run_cycle(execute=True, max_deploy_per_run=1000.0)
        self.assertEqual(rep["executed"], [])
        self.assertGreaterEqual(rep["trimmed_by_cap"], 1)

    def test_only_cash_in_broker_account_is_spendable(self):
        rep = self.run_cycle(execute=True, paper_auto_fund=False)   # dividend still in bank, not at broker
        self.assertEqual(rep["spendable"], 0.0)
        self.assertEqual(rep["executed"], [])
        self.assertEqual(self.ledger.cash_pool, 8000.0)

    def test_paper_broker_rejects_underfunded_and_unmarketable(self):
        b = PaperDeliveryBroker(self.pf)
        self.assertEqual(b.place_order("X", 10, 100.0, 100.0)["reason"], "insufficient funds")
        b.deposit(100000.0)
        self.assertEqual(b.place_order("X", 10, 100.0, 101.0)["reason"], "limit below market")
        self.assertEqual(b.place_order("X", 10, 101.0, 100.0)["status"], "FILLED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
