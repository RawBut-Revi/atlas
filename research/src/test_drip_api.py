"""
Tests for the /api/drip/* endpoints. Endpoint functions are called directly against a DripService
pointed at a temp directory, with network-facing methods stubbed. No real state, no network.
"""
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.stdout.reconfigure(encoding="utf-8")

import api
from data.fundamental_data import STOCK_FUNDAMENTALS as F
from fastapi import HTTPException
from scoring.drip_service import DripService

UNIVERSE = {k: {**F[k], "symbol": k} for k in ("COALINDIA", "ONGC", "ITC")}
SNAPS = {"COALINDIA": {"price": 410.0, "trend_pct": 0.0, "events": []},
         "ONGC": {"price": 233.0, "trend_pct": 0.0, "events": []},
         "ITC": {"price": 262.0, "trend_pct": 0.0, "events": [{"ex_date": "2026-09-10", "dps": 8.0}]}}


class TestDripApi(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.svc = DripService(state_dir=self.dir)
        self.svc.universe = lambda holdings: (UNIVERSE, {k: F[k]["sector"] for k in F}, {})
        self.svc._snapshots = lambda symbols: {s: SNAPS.get(s) for s in symbols}
        self.patch = mock.patch.object(api, "drip", self.svc)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def req(self, **kw):
        return api.DripRunRequest(**kw)

    def test_routes_registered(self):
        paths = {r.path for r in api.app.routes}
        for p in ("/api/drip/status", "/api/drip/plan", "/api/drip/execute", "/api/drip/kill", "/api/drip/portfolio"):
            self.assertIn(p, paths)

    def test_status_on_fresh_install(self):
        st = api.drip_status()
        self.assertEqual(st["holdings"], {})
        self.assertFalse(st["killed"])

    def test_plan_without_portfolio_is_400_with_reason(self):
        with self.assertRaises(HTTPException) as cm:
            api.drip_plan(self.req(tracking_start="2026-09-01"))
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("no holdings", cm.exception.detail)

    def test_init_then_plan_is_a_dry_run(self):
        api.drip_init_portfolio(api.DripInitRequest(holdings={"itc": 1000}))
        rep = api.drip_plan(self.req(tracking_start="2026-09-01"))
        self.assertEqual(rep["status"], "PLANNED")
        self.assertEqual(rep["mode"], "paper")
        self.assertTrue(rep["ranking"])
        self.assertEqual(rep["ranking"][0]["symbol"], "COALINDIA")
        self.assertTrue(rep["plan"]["orders"])
        self.assertEqual(rep["credits"][0]["net"], 8000.0)
        self.assertFalse(os.path.exists(self.svc.ledger_path("paper")))     # dry run wrote nothing

    def test_init_refuses_overwrite(self):
        api.drip_init_portfolio(api.DripInitRequest(holdings={"ITC": 10}))
        with self.assertRaises(HTTPException) as cm:
            api.drip_init_portfolio(api.DripInitRequest(holdings={"ONGC": 5}))
        self.assertEqual(cm.exception.status_code, 400)

    def test_kill_switch_blocks_execute(self):
        api.drip_init_portfolio(api.DripInitRequest(holdings={"ITC": 10}))
        self.assertTrue(api.drip_kill(api.DripKillRequest(enabled=True))["killed"])
        self.assertEqual(api.drip_execute(self.req())["status"], "DISABLED")
        self.assertFalse(api.drip_kill(api.DripKillRequest(enabled=False))["killed"])

    def test_live_execute_needs_confirm_then_env_flag(self):
        os.environ.pop("ATLAS_LIVE_DRIP", None)
        with self.assertRaises(HTTPException) as cm:
            api.drip_execute(self.req(mode="live"))
        self.assertIn("confirm", cm.exception.detail)
        with self.assertRaises(HTTPException) as cm:
            api.drip_execute(self.req(mode="live", confirm=True))
        self.assertIn("ATLAS_LIVE_DRIP", cm.exception.detail)

    def test_unexpected_error_becomes_500_not_a_crash(self):
        api.drip_init_portfolio(api.DripInitRequest(holdings={"ITC": 10}))
        self.svc._snapshots = mock.Mock(side_effect=RuntimeError("yahoo down"))
        with self.assertRaises(HTTPException) as cm:
            api.drip_plan(self.req())
        self.assertEqual(cm.exception.status_code, 500)

    def test_overlapping_cycles_are_refused(self):
        api.drip_init_portfolio(api.DripInitRequest(holdings={"ITC": 10}))
        gate, inside = threading.Event(), threading.Event()
        orig = self.svc._snapshots

        def slow(symbols):
            inside.set()
            gate.wait(5)
            return orig(symbols)
        self.svc._snapshots = slow
        t = threading.Thread(target=lambda: api.drip_plan(self.req()))
        t.start()
        self.assertTrue(inside.wait(5))
        with self.assertRaises(HTTPException) as cm:
            api.drip_plan(self.req())
        self.assertIn("already running", cm.exception.detail)
        gate.set()
        t.join()


if __name__ == "__main__":
    unittest.main(verbosity=2)
