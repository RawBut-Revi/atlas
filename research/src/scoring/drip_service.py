"""
Project Atlas - DRIP Service
============================
The one entry point used by the CLI (run_drip.py), the FastAPI layer (api.py) and therefore the
desktop app. It owns: which stocks are candidates, profile/snapshot caching, which broker is
used, and the safety rules for going live.

Live ordering requires ALL of: mode="live", confirm=True, env ATLAS_LIVE_DRIP=1, and a valid
Upstox token. Anything missing refuses with a clear reason instead of falling back to paper.
"""
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional

from data.dividend_events import fetch_snapshot
from data.fundamental_data import STOCK_FUNDAMENTALS
from data.stock_profiles import refresh_profiles, select_universe
from scoring.drip_ledger import DripLedger
from scoring.drip_planner import PlannerConfig
from scoring.drip_runner import PaperDeliveryBroker, run_drip_cycle
from trading.universe import NSE_UNIVERSE

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOT_TTL = 90.0            # seconds: keeps repeated UI clicks from hammering Yahoo
LIVE_ENV_FLAG = "ATLAS_LIVE_DRIP"


class DripError(Exception):
    """A refusal with a user-presentable reason."""


class DripService:
    def __init__(self, state_dir: str = HERE):
        self.dir = state_dir
        self.portfolio_path = os.path.join(state_dir, "drip_portfolio.json")
        self.kill_path = os.path.join(state_dir, "DRIP_DISABLED")
        self.profile_cache = os.path.join(HERE, "data", "profiles_cache.json")
        self._snaps: Dict[str, tuple] = {}
        self._cycle_lock = threading.Lock()

    # ── state ────────────────────────────────────────────────────────────────
    def ledger_path(self, mode: str) -> str:
        """Paper and live keep separate ledgers so their dividend accounting can never mix."""
        return os.path.join(self.dir, "drip_ledger.json" if mode == "paper" else "drip_ledger_live.json")

    @property
    def killed(self) -> bool:
        return os.path.exists(self.kill_path)

    def set_kill_switch(self, on: bool) -> None:
        if on:
            open(self.kill_path, "w").close()
        elif os.path.exists(self.kill_path):
            os.remove(self.kill_path)

    def init_paper_portfolio(self, holdings: Dict[str, int]) -> None:
        broker = PaperDeliveryBroker(self.portfolio_path)
        if broker.get_holdings():
            raise DripError("paper portfolio already exists; delete drip_portfolio.json to re-initialise")
        clean = {s.strip().upper(): int(q) for s, q in holdings.items() if int(q) > 0}
        if not clean:
            raise DripError("no holdings given")
        for s, q in clean.items():
            broker.state["holdings"][s] = {"qty": q, "cost": 0.0}
        broker.save()

    def broker(self, mode: str, orders: bool = False, confirm: bool = False):
        """`orders` = the caller intends to place orders. Reading a live portfolio for a dry run
        needs only a valid token; placing live orders needs the full set of switches."""
        if mode == "paper":
            return PaperDeliveryBroker(self.portfolio_path)
        if mode != "live":
            raise DripError(f"unknown mode {mode!r}")
        if orders:
            if not confirm:
                raise DripError("live orders need confirm=true")
            if os.environ.get(LIVE_ENV_FLAG) != "1":
                raise DripError(f"live orders are switched off: set {LIVE_ENV_FLAG}=1 in the environment to enable")
        from scoring.upstox_broker import UpstoxDeliveryBroker, UpstoxError
        try:
            return UpstoxDeliveryBroker(allow_orders=orders)
        except UpstoxError as e:
            raise DripError(str(e))

    # ── data ─────────────────────────────────────────────────────────────────
    def _snapshots(self, symbols) -> Dict[str, Optional[Dict]]:
        now, out, todo = time.time(), {}, []
        for s in symbols:
            hit = self._snaps.get(s)
            if hit and now - hit[0] < SNAPSHOT_TTL:
                out[s] = hit[1]
            else:
                todo.append(s)
        if todo:
            with ThreadPoolExecutor(8) as ex:
                for s, snap in zip(todo, ex.map(fetch_snapshot, todo)):
                    self._snaps[s] = (now, snap)
                    out[s] = snap
        return out

    def universe(self, holdings: Dict[str, int]):
        """(candidate profiles, sector map covering every candidate AND every holding)."""
        symbols = sorted(set(NSE_UNIVERSE) | set(holdings))
        got = refresh_profiles(symbols, self.profile_cache, curated=STOCK_FUNDAMENTALS)
        profiles = got["profiles"]
        sectors = {s: p.get("sector", "UNKNOWN") for s, p in profiles.items()}
        return select_universe(profiles), sectors, got["errors"]

    # ── operations ───────────────────────────────────────────────────────────
    def cycle(self, *args, **kw) -> Dict:
        """Serialised: two overlapping cycles could read the same cash pool and spend it twice."""
        if not self._cycle_lock.acquire(blocking=False):
            raise DripError("another DRIP cycle is already running")
        try:
            return self._cycle(*args, **kw)
        finally:
            self._cycle_lock.release()

    def _cycle(self, execute: bool = False, mode: str = "paper", confirm: bool = False,
              max_deploy: float = 50000.0, tracking_start: Optional[str] = None,
              top_n: int = 15) -> Dict:
        if execute and self.killed:
            return {"status": "DISABLED", "detail": "kill switch is on"}
        broker = self.broker(mode, orders=execute, confirm=confirm)
        holdings = broker.get_holdings()
        if not holdings:
            raise DripError("no holdings: create a paper portfolio first" if mode == "paper" else "no holdings at broker")

        ledger = DripLedger(self.ledger_path(mode), tracking_start=tracking_start)
        if tracking_start:
            ledger.state["tracking_start"] = tracking_start
        universe, sectors, errors = self.universe(holdings)
        snaps = self._snapshots(sorted(set(universe) | set(holdings)))

        rep = run_drip_cycle(broker, ledger, universe, lambda s: snaps.get(s), PlannerConfig(),
                             execute=execute, max_deploy_per_run=max_deploy,
                             kill_switch_path=self.kill_path, sector_map=sectors)
        ranked = sorted(rep.get("scores", {}).values(), key=lambda v: -v["efficiency"])
        rep["ranking"] = ranked[:top_n]
        rep["scores"] = {}                                    # the ranking replaces the full dump
        rep["universe_size"] = len(universe)
        rep["profile_errors"] = sorted(errors)
        rep["mode"] = mode
        return rep

    def status(self) -> Dict:
        broker = PaperDeliveryBroker(self.portfolio_path)
        ledger = DripLedger(self.ledger_path("paper"))
        return {"mode_default": "paper", "killed": self.killed,
                "live_enabled": os.environ.get(LIVE_ENV_FLAG) == "1",
                "holdings": broker.get_holdings(), "broker_funds": broker.get_funds(),
                "ledger_pool": ledger.cash_pool, "tracking_start": ledger.state["tracking_start"],
                "credited_dividends": list(ledger.state["processed"].values())[-20:],
                "orders": ledger.state["orders"][-20:]}
