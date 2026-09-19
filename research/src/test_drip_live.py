"""
Tests for live-DRIP plumbing: Upstox adapter (mocked HTTP), live-mode gating in the service, and
the stock-profile builder. No network, no real state files, no real orders.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

sys.stdout.reconfigure(encoding="utf-8")

from data.stock_profiles import (build_profile, history_stats, parse_chart_history, parse_quote_summary,
                                 refresh_profiles, select_universe)
from scoring.drip_ledger import DripLedger
from scoring.drip_runner import run_drip_cycle
from scoring.drip_service import DripError, DripService
from scoring.upstox_broker import UpstoxDeliveryBroker, UpstoxError, load_token

IST = timezone(timedelta(hours=5, minutes=30))
WED = datetime(2026, 9, 16, 11, 0, tzinfo=IST)


class FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code, self._b = status, body if body is not None else {}
        self.headers = {"content-type": "application/json"}
        self.text = json.dumps(self._b)

    def json(self):
        return self._b


class FakeSession:
    """Routes by URL substring; records POSTs."""

    def __init__(self, routes):
        self.headers, self.routes, self.posts = {}, routes, []

    def get(self, url, params=None, timeout=None):
        for frag, resp in self.routes.items():
            if frag in url:
                return resp() if callable(resp) else resp
        return FakeResp(404, {})

    def post(self, url, json=None, timeout=None):
        self.posts.append((url, json))
        return self.routes["POST"]()


OK_PROFILE = FakeResp(200, {"status": "success", "data": {"user_name": "x"}})


def broker(routes, allow_orders=True, **kw):
    r = {"/user/profile": OK_PROFILE}
    r.update(routes)
    return UpstoxDeliveryBroker(allow_orders=allow_orders, token="t", session=FakeSession(r), sleep=lambda s: None, **kw)


class TestUpstoxBroker(unittest.TestCase):
    def test_expired_token_fails_fast_with_instructions(self):
        with self.assertRaises(UpstoxError) as cm:
            UpstoxDeliveryBroker(token="dead", session=FakeSession({"/user/profile": FakeResp(401, {})}))
        self.assertIn("atlas.exe auth", str(cm.exception))

    def test_missing_token_file_explains(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("UPSTOX_ACCESS_TOKEN", None)
            with self.assertRaises(UpstoxError):
                load_token(path=os.path.join(tempfile.gettempdir(), "no_such_token_file"))

    def test_holdings_and_funds_parsing(self):
        b = broker({
            "long-term-holdings": FakeResp(200, {"status": "success", "data": [
                {"trading_symbol": "ITC", "quantity": 100}, {"trading_symbol": "ITC", "quantity": 5},
                {"trading_symbol": "ONGC", "quantity": 0}]}),
            "get-funds-and-margin": FakeResp(200, {"status": "success", "data": {"equity": {"available_margin": 12345.678}}}),
        })
        self.assertEqual(b.get_holdings(), {"ITC": 105})
        self.assertEqual(b.get_funds(), 12345.68)

    def test_orders_refused_unless_enabled(self):
        b = broker({}, allow_orders=False)
        with self.assertRaises(UpstoxError):
            b.place_order("ITC", 1, 262.5, 262.3)

    def test_guards_reject_without_calling_upstox(self):
        b = broker({"POST": lambda: self.fail("must not reach the exchange")})
        self.assertIn("hard cap", b.place_order("ITC", 200, 262.5, 262.3)["reason"])           # Rs52,500 > 25,000
        self.assertIn("too far", b.place_order("ITC", 1, 300.0, 262.3)["reason"])
        self.assertIn("instrument", b.place_order("NOSUCH", 1, 10.0, 10.0)["reason"])
        self.assertIn("invalid", b.place_order("ITC", 0, 262.5, 262.3)["reason"])
        self.assertEqual(b.s.posts, [])

    def test_filled_order_payload_and_result(self):
        b = broker({"POST": lambda: FakeResp(200, {"status": "success", "data": {"order_id": "OID1"}}),
                    "order/details": FakeResp(200, {"status": "success", "data": {"status": "complete", "average_price": 262.4}})})
        res = b.place_order("ITC", 10, 262.5, 262.3)
        self.assertEqual(res["status"], "FILLED")
        self.assertEqual(res["fill_price"], 262.4)
        url, payload = b.s.posts[0]
        self.assertEqual((payload["product"], payload["order_type"], payload["transaction_type"], payload["validity"]),
                         ("D", "LIMIT", "BUY", "DAY"))
        self.assertEqual((payload["quantity"], payload["price"], payload["tag"]), (10, 262.5, "atlas-drip"))
        self.assertTrue(payload["instrument_token"].startswith("NSE_EQ|"))
        self.assertFalse(payload["is_amo"])

    def test_unfilled_order_is_pending_with_cash_reserved(self):
        b = broker({"POST": lambda: FakeResp(200, {"status": "success", "data": {"order_id": "OID2"}}),
                    "order/details": FakeResp(200, {"status": "success", "data": {"status": "open"}})})
        with mock.patch("scoring.upstox_broker.FILL_POLL_SECONDS", -1):
            res = b.place_order("ITC", 10, 262.5, 262.3)
        self.assertEqual(res["status"], "PENDING")
        self.assertGreater(res["cash_used"], 2625.0)

    def test_rejected_and_http_error(self):
        rej = broker({"POST": lambda: FakeResp(200, {"status": "success", "data": {"order_id": "O3"}}),
                      "order/details": FakeResp(200, {"status": "success", "data": {"status": "rejected", "status_message": "margin"}})})
        self.assertEqual(rej.place_order("ITC", 1, 262.5, 262.3)["status"], "REJECTED")
        bad = broker({"POST": lambda: FakeResp(400, {"status": "error"})})
        self.assertEqual(bad.place_order("ITC", 1, 262.5, 262.3)["status"], "REJECTED")

    def test_network_failure_on_submit_reserves_cash(self):
        import requests

        def boom():
            raise requests.ConnectionError("dropped")
        b = broker({"POST": boom})
        res = b.place_order("ITC", 10, 262.5, 262.3)
        self.assertEqual(res["status"], "PENDING")          # the order may have been accepted: never assume failure

    def test_runner_debits_pending_so_cash_is_not_spent_twice(self):
        tmp = tempfile.mkdtemp()
        try:
            ledger = DripLedger(os.path.join(tmp, "l.json"), tracking_start="2026-09-01")
            ledger.state["cash_pool"] = 20000.0

            class B:
                def get_holdings(self): return {"ITC": 10}
                def get_funds(self): return 20000.0
                def place_order(self, s, q, l, r): return {"symbol": s, "status": "PENDING", "cash_used": q * l}

            from data.fundamental_data import STOCK_FUNDAMENTALS as F
            snap = lambda s: {"price": 410.0, "trend_pct": 0.0, "events": []}
            rep = run_drip_cycle(B(), ledger, {"COALINDIA": F["COALINDIA"]}, snap, execute=True, now=WED)
            self.assertEqual(rep["status"], "EXECUTED")
            self.assertTrue(rep["executed"])
            self.assertLess(ledger.cash_pool, 20000.0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestServiceGating(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.svc = DripService(state_dir=self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_live_orders_need_confirm_and_env_flag(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ATLAS_LIVE_DRIP", None)
            with self.assertRaisesRegex(DripError, "confirm"):
                self.svc.broker("live", orders=True, confirm=False)
            with self.assertRaisesRegex(DripError, "ATLAS_LIVE_DRIP"):
                self.svc.broker("live", orders=True, confirm=True)

    def test_unknown_mode_and_empty_portfolio(self):
        with self.assertRaises(DripError):
            self.svc.broker("yolo")
        with self.assertRaisesRegex(DripError, "no holdings"):
            self.svc.cycle(execute=False, mode="paper")

    def test_kill_switch_blocks_execution(self):
        self.svc.init_paper_portfolio({"ITC": 10})
        self.svc.set_kill_switch(True)
        self.assertEqual(self.svc.cycle(execute=True)["status"], "DISABLED")
        self.svc.set_kill_switch(False)
        self.assertFalse(self.svc.killed)

    def test_paper_and_live_ledgers_are_separate_files(self):
        self.assertNotEqual(self.svc.ledger_path("paper"), self.svc.ledger_path("live"))

    def test_init_portfolio_refuses_overwrite(self):
        self.svc.init_paper_portfolio({"itc": 10})
        self.assertEqual(self.svc.status()["holdings"], {"ITC": 10})
        with self.assertRaises(DripError):
            self.svc.init_paper_portfolio({"ONGC": 5})


def chart_json(days=800, start_price=100.0, growth=0.0005, divs=()):
    end = int(datetime(2026, 9, 16, 5, 30, tzinfo=IST).timestamp())
    ts = [end - (days - i) * 86400 for i in range(days)]
    close = [start_price * (1 + growth) ** i for i in range(days)]
    ev = {str(t): {"amount": a, "date": t} for t, a in divs}
    return {"chart": {"result": [{"meta": {"regularMarketPrice": close[-1]}, "timestamp": ts,
            "events": {"dividends": ev} if ev else {}, "indicators": {"quote": [{"close": close, "volume": [2_000_000] * days}]}}]}}


def yearly_divs(years=6, dps=5.0, growth=0.10):
    out, end = [], datetime(2026, 9, 16, tzinfo=IST)
    for k in range(years):
        out.append((int((end - timedelta(days=365 * k + 30)).timestamp()), round(dps / (1 + growth) ** k, 4)))
    return out


def summary_json(**over):
    fd = {"returnOnEquity": {"raw": 0.2}, "debtToEquity": {"raw": 30.0}, "profitMargins": {"raw": 0.15}}
    sd = {"trailingPE": {"raw": 15.0}, "payoutRatio": {"raw": 0.4}, "marketCap": {"raw": 1e12}}
    ks = {"trailingEps": {"raw": 20.0}, "bookValue": {"raw": 100.0}, "priceToBook": {"raw": 3.0}}
    inc = [{"endDate": {"raw": 1_600_000_000 + i * 31_536_000}, "netIncome": {"raw": v}} for i, v in enumerate([100.0, 110.0, 125.0, 140.0])]
    res = {"financialData": fd, "summaryDetail": sd, "defaultKeyStatistics": ks, "assetProfile": {"sector": "Energy"},
           "incomeStatementHistory": {"incomeStatementHistory": inc}}
    res.update(over)
    return {"quoteSummary": {"result": [res]}}


class TestProfiles(unittest.TestCase):
    def test_quote_summary_fields(self):
        f = parse_quote_summary(summary_json())
        self.assertEqual(f["sector"], "Energy")
        self.assertAlmostEqual(f["roe_pct"], 20.0)
        self.assertAlmostEqual(f["debt_to_equity"], 0.30)               # Yahoo percent -> ratio
        self.assertAlmostEqual(f["payout_ratio_pct"], 40.0)
        self.assertAlmostEqual(f["profit_cagr_5y"], ((140 / 100) ** (1 / 3) - 1) * 100, places=1)
        self.assertEqual(f["market_cap_cr"], 100000.0)

    def test_roe_falls_back_to_eps_over_book_value(self):
        js = summary_json(financialData={"debtToEquity": {"raw": 10.0}})
        self.assertAlmostEqual(parse_quote_summary(js)["roe_pct"], 20.0)

    def test_missing_data_stays_none_never_zero(self):
        js = {"quoteSummary": {"result": [{"assetProfile": {}, "financialData": {}, "summaryDetail": {},
                                           "defaultKeyStatistics": {}}]}}
        f = parse_quote_summary(js)
        self.assertIsNone(f["roe_pct"])
        self.assertIsNone(f["payout_ratio_pct"])
        self.assertIsNone(f["profit_cagr_5y"])
        self.assertEqual(f["sector"], "UNKNOWN")

    def test_bad_payload(self):
        self.assertIsNone(parse_quote_summary({}))
        self.assertIsNone(parse_chart_history({}))

    def test_history_stats_dividend_consistency_and_growth(self):
        hist = parse_chart_history(chart_json(days=1500, divs=yearly_divs(6, dps=5.0, growth=0.10)))
        st = history_stats(hist, date(2026, 9, 16))
        self.assertGreaterEqual(st["years_consecutive_dividend"], 5)
        self.assertAlmostEqual(st["dividend_cagr_5y"], 10.0, delta=1.5)
        self.assertGreater(st["price_cagr_3y"], 0)
        self.assertGreater(st["avg_turnover_cr"], 10)

    def test_no_dividends_means_no_consecutive_years(self):
        st = history_stats(parse_chart_history(chart_json()), date(2026, 9, 16))
        self.assertEqual(st["years_consecutive_dividend"], 0)
        self.assertIsNone(st["dividend_cagr_5y"])

    def test_drawdown_detected(self):
        js = chart_json(days=400, growth=0.0)
        closes = js["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        for i in range(200, 260):
            closes[i] = 50.0                                               # 50% crash
        st = history_stats(parse_chart_history(js), date(2026, 9, 16))
        self.assertLessEqual(st["max_drawdown_3y_pct"], -49.0)

    def test_build_profile_and_curated_piotroski_fallback(self):
        hist = parse_chart_history(chart_json(divs=yearly_divs()))
        p = build_profile("ITC", hist, parse_quote_summary(summary_json()), date(2026, 9, 16), {"piotroski_f_score": 7})
        self.assertEqual(p["piotroski_f_score"], 7)
        self.assertEqual(p["sector"], "Energy")
        self.assertTrue(p["fundamentals_ok"])
        self.assertIsNone(build_profile("X", None, None, date(2026, 9, 16)))

    def test_select_universe_filters(self):
        P = {"A": {"years_consecutive_dividend": 5, "avg_turnover_cr": 50, "dividend_yield_pct": 2},
             "B": {"years_consecutive_dividend": 1, "avg_turnover_cr": 50, "dividend_yield_pct": 2},
             "C": {"years_consecutive_dividend": 5, "avg_turnover_cr": 1, "dividend_yield_pct": 2}}
        self.assertEqual(list(select_universe(P)), ["A"])

    def test_refresh_uses_cache_and_refetches_stale(self):
        tmp = tempfile.mkdtemp()
        try:
            calls = []

            class Sess:
                def chart(self, s, years=10):
                    calls.append(s); return chart_json(divs=yearly_divs())

                def summary(self, s):
                    return summary_json()

            path = os.path.join(tmp, "cache.json")
            now = datetime(2026, 9, 16, 12, 0, tzinfo=IST)
            out = refresh_profiles(["A", "B"], path, session=Sess(), now=now)
            self.assertEqual(out["refreshed"], 2)
            calls.clear()
            out = refresh_profiles(["A", "B"], path, session=Sess(), now=now + timedelta(days=1))
            self.assertEqual((out["refreshed"], calls), (0, []))            # fresh: no network
            out = refresh_profiles(["A", "B"], path, session=Sess(), now=now + timedelta(days=9))
            self.assertEqual(out["refreshed"], 2)                           # stale: refetched
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
