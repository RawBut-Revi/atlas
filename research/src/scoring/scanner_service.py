"""
Project Atlas - Scanner Service
===============================
The one entry point used by the CLI (run_scanner.py), the FastAPI layer (api.py, hence the desktop
tab) and the Telegram /invest command. It owns: the price cache and its background refresh, the stored
backtest and multi-market study reference, and a PAPER portfolio tracked against the 25% goal and Nifty.

The live universe is EVERY regular-series NSE equity (~2,300, about 2,100 with enough history), not the
trading bot's 181-stock list. Fundamentals only exist for the stocks in profiles_cache.json, so after each
refresh the top candidates that lack a profile get one fetched; anything still unverified is flagged.

Paper only. Nothing here places a real order.
"""
import json
import math
import os
import threading
import time
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

import requests

from data.market_history import fetch_market, fetch_one, iter_histories
from data.market_universe import universe
from data.stock_profiles import CHART_URL, YahooSession, parse_chart_history, refresh_profiles
from scoring.momentum_scanner import ScannerConfig, TARGET_ANNUAL_RETURN, backtest, scan

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IST = timezone(timedelta(hours=5, minutes=30))
LIVE_MARKET = "india_all"        # store key shared with the multi-market study
PRICE_TTL_HOURS = 20.0
PROFILE_CANDIDATES = 40          # top-ranked stocks that get fundamentals fetched if missing
STALE_REFUSE_HOURS = 60.0        # weekend-safe: refuse to trade off prices older than this
MIN_DAYS_FOR_ANNUALIZED = 90     # an annualized return over a few weeks is noise
MIN_TRADE_PCT = 0.01             # skip rebalance trades smaller than 1% of the portfolio


class ScannerError(Exception):
    """A refusal with a user-presentable reason."""


def _today() -> date:
    return datetime.now(IST).date()


class ScannerService:
    def __init__(self, state_dir: str = HERE, data_dir: Optional[str] = None,
                 fetchers: Optional[Callable[[], tuple]] = None, cfg: ScannerConfig = ScannerConfig(),
                 universe_fn: Optional[Callable[[], List[Dict]]] = None,
                 profile_refresher: Optional[Callable[[List[str]], None]] = None):
        data_dir = data_dir or os.path.join(HERE, "data")
        self.cfg = cfg
        self.market_dir = os.path.join(data_dir, "markets")
        self.study_path = os.path.join(data_dir, "market_study.json")
        self._universe_fn = universe_fn or (lambda: universe(LIVE_MARKET, market_dir=self.market_dir))
        self._profile_refresher = profile_refresher
        self.price_path = os.path.join(data_dir, "scanner_prices.json")
        self.profile_path = os.path.join(data_dir, "profiles_cache.json")
        self.history_path = os.path.join(data_dir, "history_cache.json")
        self.backtest_path = os.path.join(data_dir, "scanner_backtest.json")
        self.portfolio_path = os.path.join(state_dir, "scanner_portfolio.json")
        self._fetchers = fetchers                   # () -> (stock_fn(ticker), nifty_fn()); injectable for tests
        self._refresh_lock = threading.Lock()
        self.refreshing = False

    # ── small io helpers ─────────────────────────────────────────────────────
    @staticmethod
    def _read(path: str):
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except (OSError, ValueError):
                return None
        return None

    @staticmethod
    def _write(path: str, obj) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2 if path.endswith("portfolio.json") else None)
        os.replace(tmp, path)

    # ── prices ───────────────────────────────────────────────────────────────
    def price_age_hours(self) -> Optional[float]:
        blob = self._read(self.price_path)
        if not blob or "fetched_at" not in blob:
            return None
        try:
            return (datetime.now(IST) - datetime.fromisoformat(blob["fetched_at"])).total_seconds() / 3600.0
        except ValueError:
            return None

    def _default_fetchers(self):
        session = YahooSession()

        def stock(ticker):
            return fetch_one(session, ticker, years=12)[0]

        def nifty():
            end = int(time.time())
            js = session._get(CHART_URL.format(ticker="%5ENSEI"),
                              {"period1": end - 2 * 366 * 86400, "period2": end, "interval": "1d", "events": "div"})
            return parse_chart_history(js or {})
        return stock, nifty

    def _prices_from_store(self, entries: List[Dict], years: int = 2) -> Dict:
        """Last `years` of bars for every stored stock with enough history (streamed from the per-stock store)."""
        keep = years * 252 + 5
        return {sym: {"ts": h["ts"][-keep:], "close": h["close"][-keep:], "volume": h["volume"][-keep:]}
                for sym, h in iter_histories(LIVE_MARKET, entries, self.market_dir) if len(h["close"]) >= 253}

    def _ensure_profiles(self, prices: Dict) -> None:
        """Best effort: fetch fundamentals for top-ranked candidates that have none, so the quality filter
        can judge them. Failures are logged and leave the stock flagged as unverified."""
        try:
            profiles = self._read(self.profile_path) or {}
            out = scan(prices, profiles, replace(self.cfg, top_n=PROFILE_CANDIDATES))
            missing = [p["symbol"] for p in out["picks"] if "no fundamentals" in p.get("unverified", [])]
            if missing:
                (self._profile_refresher or self._default_profile_refresher)(missing)
        except Exception as e:                      # noqa: BLE001 - never fail a price refresh over fundamentals
            print(f"[Scanner] Fundamentals fetch skipped: {type(e).__name__}: {e}")

    def _default_profile_refresher(self, symbols: List[str]) -> None:
        refresh_profiles(symbols, self.profile_path, workers=4)

    def refresh_prices(self) -> Dict:
        """Blocking: downloads any stale stock history for the whole NSE universe (~2 min on a real network),
        rebuilds the 2-year price cache, then fills in fundamentals for the top candidates."""
        if not self._refresh_lock.acquire(blocking=False):
            return {"status": "ALREADY_REFRESHING"}
        self.refreshing = True
        try:
            entries = self._universe_fn()
            try:
                stock_fn, nifty_fn = (self._fetchers or self._default_fetchers)()
                fetch_market(LIVE_MARKET, entries, workers=6, max_age_days=PRICE_TTL_HOURS / 24.0,
                             market_dir=self.market_dir, fetch=lambda t: (stock_fn(t), "no data"))
            except requests.RequestException as e:
                raise ScannerError(f"Could not reach Yahoo Finance ({type(e).__name__}). Check the connection, or use "
                                   f"the offline fallback: python run_scanner.py --from-history") from e
            prices = self._prices_from_store(entries)
            if len(prices) < 20:
                raise ScannerError(f"price refresh got data for only {len(prices)} stocks: network or Yahoo problem")
            nifty = nifty_fn()
            self._write(self.price_path, {
                "fetched_at": datetime.now(IST).isoformat(), "prices": prices,
                "nifty": {"ts": nifty["ts"], "close": nifty["close"]} if nifty else None})
            self._ensure_profiles(prices)
            return {"status": "OK", "stocks": len(prices), "nifty": bool(nifty)}
        finally:
            self.refreshing = False
            self._refresh_lock.release()

    def seed_from_history(self, years: int = 2) -> Dict:
        """
        Offline fallback: rebuild the price cache from data already on disk when Yahoo is unreachable: the
        per-stock store if present, else the legacy 12-year history cache (data/history_cache.json). The data
        is only as fresh as those files, and `fetched_at` says so, so picks show its real age and
        rebalancing refuses once it is older than STALE_REFUSE_HOURS.
        """
        stored_dir = os.path.join(self.market_dir, LIVE_MARKET)
        prices, newest = {}, 0.0
        if os.path.isdir(stored_dir):
            prices = self._prices_from_store(self._universe_fn(), years)
            files = [os.path.join(stored_dir, f) for f in os.listdir(stored_dir) if f.endswith(".json.gz")]
            newest = max((os.path.getmtime(f) for f in files), default=0.0)
        if len(prices) < 20:
            hist = self._read(self.history_path)
            if not hist:
                raise ScannerError("No stored history to seed from. Run `python run_scanner.py --refresh` "
                                   "(or `python run_drip_backtest.py --refresh`) once.")
            keep = years * 252 + 5
            prices = {s: {"ts": h["ts"][-keep:], "close": h["close"][-keep:], "volume": h["volume"][-keep:]}
                      for s, h in hist.items() if len(h.get("close", [])) >= 253}
            newest = os.path.getmtime(self.history_path)
        stamp = datetime.fromtimestamp(newest, tz=IST).isoformat()
        self._write(self.price_path, {"fetched_at": stamp, "prices": prices, "nifty": None})
        return {"status": "SEEDED", "stocks": len(prices), "data_as_of": stamp}

    def refresh_async(self) -> Dict:
        """Starts a background refresh and returns immediately (Telegram/UI must never block on Yahoo)."""
        if self.refreshing:
            return {"status": "ALREADY_REFRESHING"}

        def work():
            try:
                self.refresh_prices()
            except Exception as e:                  # noqa: BLE001 - background thread: log, never raise
                print(f"[Scanner] Background price refresh failed: {type(e).__name__}: {e}")
        threading.Thread(target=work, name="ScannerRefresh", daemon=True).start()
        return {"status": "STARTED"}

    def _prices(self) -> Dict:
        blob = self._read(self.price_path)
        if not blob or not blob.get("prices"):
            raise ScannerError("No price data yet. Run a refresh first (takes 1-2 minutes).")
        return blob

    # ── backtest reference ───────────────────────────────────────────────────
    @staticmethod
    def _compact(r: Dict) -> Dict:
        s, b = r["strategy"], r["benchmark"]
        return {"cagr": s["cagr"], "max_drawdown": s["max_drawdown"], "ann_vol": s["ann_vol"],
                "first_half_cagr": r["first_half"].get("cagr"), "second_half_cagr": r["second_half"].get("cagr"),
                "benchmark_cagr": b["cagr"], "benchmark_max_drawdown": b["max_drawdown"],
                "years_meeting_target": r["years_meeting_target"], "years_total": r["years_total"],
                "yearly": s["yearly"], "benchmark_yearly": b["yearly"],
                "first_date": r["first_date"], "last_date": r["last_date"], "periods": s["periods"],
                # present only for study runs (the legacy 176-stock report has none of these)
                "cagr_before_costs": (r.get("turnover") or {}).get("cagr_before_costs"),
                "avg_one_way_monthly_pct": (r.get("turnover") or {}).get("avg_one_way_monthly_pct"),
                "active_t_stat": (r.get("active") or {}).get("t_stat"),
                "random_percentile": (r.get("random") or {}).get("scanner_percentile"),
                "index_cagr_price": r.get("index_cagr_price")}

    def _study(self) -> Optional[Dict]:
        return self._read(self.study_path)

    def backtest_report(self, refresh: bool = False) -> Dict:
        """
        Reference numbers shown next to the picks. When the multi-market study exists (python run_market_study.py)
        its India-all result is used, because the live scanner covers that same universe. Otherwise falls back to
        the legacy replay of the 176 stocks in the 12-year cache. `refresh` only applies to the legacy replay.
        """
        study = self._study()
        india = ((study or {}).get("results") or {}).get("india_all")
        if india and "strategy" in india:
            return {
                "ran_at": study["generated_at"], "source": "market_study", "target_annual_return": TARGET_ANNUAL_RETURN,
                "universe": f"All NSE equities ({india['stocks_with_data']} with data)",
                "default": self._compact(india),
                "variants": {k: v for k, v in india["variants"].items()},
                "caveats": india["caveats"] + study["caveats"] + [
                    "small caps are in this universe: the assumed 0.25% cost is optimistic for them (see costs_doubled)",
                    "the 25% goal was not reached consistently: see years_meeting_target"],
            }
        if not refresh:
            cached = self._read(self.backtest_path)
            if cached:
                return cached
        hist = self._read(self.history_path)
        if not hist:
            raise ScannerError("No 12-year history cache. Run `python run_drip_backtest.py --refresh` once.")
        default = backtest(hist, self.cfg)
        report = {
            "ran_at": datetime.now(IST).isoformat(), "source": "history_cache",
            "target_annual_return": TARGET_ANNUAL_RETURN,
            "default": self._compact(default),
            "variants": {
                "risk_off_switch_on": self._compact(backtest(hist, replace(self.cfg, regime_filter=True))),
                "costs_doubled": self._compact(backtest(hist, replace(self.cfg, cost_per_side=self.cfg.cost_per_side * 2))),
                "top_8": self._compact(backtest(hist, replace(self.cfg, top_n=8))),
                "top_20": self._compact(backtest(hist, replace(self.cfg, top_n=20))),
            },
            "caveats": default["caveats"] + [
                "risk-off switch default was chosen after seeing this backtest",
                "the 25% goal was not reached consistently: see years_meeting_target"],
        }
        self._write(self.backtest_path, report)
        return report

    # ── multi-market study ───────────────────────────────────────────────────
    @staticmethod
    def _verdict(excess: float, t: Optional[float]) -> str:
        if excess <= 0:
            return "behind universe"
        return "ahead, significant" if t is not None and t >= 2.0 else "ahead, not significant"

    def study_report(self) -> Dict:
        """One row per market: the same fixed model's result, and whether its edge is distinguishable from luck."""
        study = self._study()
        if not study:
            raise ScannerError("No market study yet. Run `python run_market_study.py` (about a minute once history is downloaded).")
        rows = []
        for key, r in study["results"].items():
            if "strategy" not in r:
                continue
            s_, b_, a_ = r["strategy"], r["benchmark"], r.get("active") or {}
            excess, t = s_["cagr"] - b_["cagr"], a_.get("t_stat")
            h1 = r["first_half"].get("cagr", 0) - r["benchmark_first_half"].get("cagr", 0)
            h2 = r["second_half"].get("cagr", 0) - r["benchmark_second_half"].get("cagr", 0)
            rows.append({
                "market": key, "name": r.get("name", key), "reference": key == "india_176",
                "stocks": r["stocks_with_data"], "eligible": r["avg_eligible"],
                "scanner_cagr": s_["cagr"], "universe_cagr": b_["cagr"], "index_cagr_price": r.get("index_cagr_price"),
                "excess": excess, "t_stat": t, "cagr_before_costs": r["turnover"]["cagr_before_costs"],
                "random_median": (r.get("random") or {}).get("median_cagr"),
                "skill_percentile": (r.get("random") or {}).get("scanner_percentile"),
                "max_drawdown": s_["max_drawdown"], "years_meeting_target": r["years_meeting_target"],
                "years_total": r["years_total"], "half_excess": [h1, h2],
                "costs_doubled_cagr": (r.get("variants") or {}).get("costs_doubled", {}).get("cagr"),
                "verdict": self._verdict(excess, t),
            })
        core = [x for x in rows if not x["reference"]]
        return {
            "generated_at": study["generated_at"], "runs": study.get("runs"), "rows": rows,
            "summary": {"markets": len(core),
                        "beat_universe": sum(1 for x in core if x["excess"] > 0),
                        "significant": sum(1 for x in core if x["verdict"] == "ahead, significant"),
                        "reached_25pct": sum(1 for x in core if x["scanner_cagr"] >= TARGET_ANNUAL_RETURN)},
            "caveats": study["caveats"],
        }

    # ── picks ────────────────────────────────────────────────────────────────
    def picks(self) -> Dict:
        blob = self._prices()
        profiles = self._read(self.profile_path) or {}
        out = scan(blob["prices"], profiles, self.cfg)
        age = self.price_age_hours()
        if age is None or age > PRICE_TTL_HOURS:
            self.refresh_async()
        out.update({
            "price_age_hours": None if age is None else round(age, 1),
            "refreshing": self.refreshing,
            "target_annual_return": TARGET_ANNUAL_RETURN,
            "backtest": self._safe_backtest(),
            "study": self._safe_study_summary(),
        })
        return out

    def _safe_backtest(self) -> Optional[Dict]:
        try:
            return self.backtest_report()["default"]
        except ScannerError:
            return None

    def _safe_study_summary(self) -> Optional[Dict]:
        try:
            return self.study_report()["summary"]
        except ScannerError:
            return None

    # ── paper portfolio ──────────────────────────────────────────────────────
    def _last_price(self, blob: Dict, sym: str) -> Optional[float]:
        h = blob["prices"].get(sym)
        return float(h["close"][-1]) if h and h["close"] else None

    @staticmethod
    def _nifty_last(blob: Dict) -> Optional[float]:
        n = blob.get("nifty")
        return float(n["close"][-1]) if n and n.get("close") else None

    def _load_portfolio(self) -> Optional[Dict]:
        return self._read(self.portfolio_path)

    def _value(self, state: Dict, blob: Dict) -> float:
        total = state["cash"]
        for sym, h in state["holdings"].items():
            px = self._last_price(blob, sym) or (h["cost"] / h["qty"] if h["qty"] else 0.0)
            total += h["qty"] * px
        return total

    def rebalance(self, execute: bool = False, capital: float = 150000.0, force: bool = False) -> Dict:
        """Plan (execute=False) or apply (execute=True) a paper rebalance to the current picks."""
        blob = self._prices()
        age = self.price_age_hours()
        if age is None or age > STALE_REFUSE_HOURS:
            self.refresh_async()
            raise ScannerError(f"Prices are {'missing' if age is None else f'{age:.0f}h old'}: refreshing in the background, try again in 2 minutes.")

        state = self._load_portfolio()
        today = _today()
        if state and not force:
            due = date.fromisoformat(state["last_rebalance"]) + timedelta(days=self.cfg.rebalance_days)
            if today < due:
                return {"status": "NOT_DUE", "next_due": due.isoformat(),
                        "detail": f"Monthly strategy: next rebalance {due.isoformat()} (force=true overrides)."}
        if not state:
            if capital <= 0:
                raise ScannerError("capital must be positive")
            state = {"created": today.isoformat(), "capital": float(capital), "cash": float(capital),
                     "holdings": {}, "last_rebalance": None, "nifty_start": None, "snapshots": [], "trades": []}

        result = scan(blob["prices"], self._read(self.profile_path) or {}, self.cfg)
        total = self._value(state, blob)
        targets = {p["symbol"]: p["weight"] * total for p in result["picks"] if p["weight"] > 0}

        sells, buys = [], []
        for sym in set(state["holdings"]) | set(targets):
            px = self._last_price(blob, sym)
            if not px:
                continue
            have = state["holdings"].get(sym, {}).get("qty", 0)
            want = int(math.floor(targets.get(sym, 0.0) / px))
            delta = want - have
            if abs(delta) * px < MIN_TRADE_PCT * total and sym in targets and have > 0:
                continue                                         # not worth churning
            if delta < 0:
                sells.append({"symbol": sym, "side": "SELL", "qty": -delta, "price": px})
            elif delta > 0:
                buys.append({"symbol": sym, "side": "BUY", "qty": delta, "price": px})

        cost_rate = self.cfg.cost_per_side
        cash = state["cash"] + sum(o["qty"] * o["price"] * (1 - cost_rate) for o in sells)
        for o in sorted(buys, key=lambda o: -o["qty"] * o["price"]):
            affordable = int(cash // (o["price"] * (1 + cost_rate)))
            o["qty"] = min(o["qty"], max(0, affordable))
            cash -= o["qty"] * o["price"] * (1 + cost_rate)
        buys = [o for o in buys if o["qty"] > 0]
        for o in sells + buys:
            o["value"] = round(o["qty"] * o["price"], 2)
            o["est_cost"] = round(o["value"] * cost_rate, 2)

        plan = {
            "status": "PLAN", "asof": result["asof"], "risk_on": result["risk_on"],
            "portfolio_value": round(total, 2), "orders": sells + buys,
            "est_costs": round(sum(o["est_cost"] for o in sells + buys), 2),
            "cash_after": round(cash, 2), "note": "PAPER ONLY. No real orders are placed.",
        }
        if not execute:
            return plan

        for o in sells + buys:
            h = state["holdings"].setdefault(o["symbol"], {"qty": 0, "cost": 0.0})
            if o["side"] == "SELL":
                avg = h["cost"] / h["qty"] if h["qty"] else o["price"]
                h["qty"] -= o["qty"]
                h["cost"] -= avg * o["qty"]
                state["cash"] += o["value"] - o["est_cost"]
            else:
                h["qty"] += o["qty"]
                h["cost"] += o["value"]
                state["cash"] -= o["value"] + o["est_cost"]
            state["trades"].append({"date": today.isoformat(), "symbol": o["symbol"], "side": o["side"],
                                    "qty": o["qty"], "price": o["price"], "cost": o["est_cost"]})
        state["holdings"] = {s: h for s, h in state["holdings"].items() if h["qty"] > 0}
        state["last_rebalance"] = today.isoformat()
        if state["nifty_start"] is None:
            state["nifty_start"] = self._nifty_last(blob)
        self._snapshot(state, blob)
        self._write(self.portfolio_path, state)
        return {**plan, "status": "EXECUTED"}

    def _snapshot(self, state: Dict, blob: Dict) -> None:
        today = _today().isoformat()
        snap = {"date": today, "value": round(self._value(state, blob), 2), "nifty": self._nifty_last(blob)}
        if state["snapshots"] and state["snapshots"][-1]["date"] == today:
            state["snapshots"][-1] = snap
        else:
            state["snapshots"].append(snap)

    def portfolio_status(self) -> Dict:
        state = self._load_portfolio()
        if not state:
            return {"status": "NO_PORTFOLIO", "detail": "No paper portfolio yet. Rebalance with execute to create one.",
                    "target_annual_return": TARGET_ANNUAL_RETURN}
        blob = self._prices()
        value = self._value(state, blob)
        capital = state["capital"]
        days = max(0, (_today() - date.fromisoformat(state["created"])).days)
        ret = value / capital - 1.0
        annualized = (value / capital) ** (365.0 / days) - 1.0 if days >= MIN_DAYS_FOR_ANNUALIZED and value > 0 else None
        hurdle_value = capital * (1.0 + TARGET_ANNUAL_RETURN) ** (days / 365.0)

        nifty_now, nifty_start = self._nifty_last(blob), state.get("nifty_start")
        nifty_ret = nifty_now / nifty_start - 1.0 if nifty_now and nifty_start else None

        self._snapshot(state, blob)
        self._write(self.portfolio_path, state)
        peak, mdd = 0.0, 0.0
        for s in state["snapshots"]:
            peak = max(peak, s["value"])
            if peak > 0:
                mdd = min(mdd, s["value"] / peak - 1.0)

        rows = []
        for sym, h in sorted(state["holdings"].items()):
            px = self._last_price(blob, sym) or (h["cost"] / h["qty"])
            rows.append({"symbol": sym, "qty": h["qty"], "avg_cost": round(h["cost"] / h["qty"], 2), "price": px,
                         "value": round(h["qty"] * px, 2), "weight": round(h["qty"] * px / value, 4) if value else 0,
                         "pnl_pct": round((px / (h["cost"] / h["qty"]) - 1.0) * 100.0, 2)})
        nxt = date.fromisoformat(state["last_rebalance"]) + timedelta(days=self.cfg.rebalance_days) if state.get("last_rebalance") else None
        return {
            "status": "OK", "created": state["created"], "days": days, "capital": capital,
            "value": round(value, 2), "cash": round(state["cash"], 2), "return_pct": round(ret * 100.0, 2),
            "annualized_pct": None if annualized is None else round(annualized * 100.0, 2),
            "annualized_note": None if annualized is not None else f"too early: needs {MIN_DAYS_FOR_ANNUALIZED}+ days",
            "target_annual_return": TARGET_ANNUAL_RETURN,
            "hurdle_value": round(hurdle_value, 2), "vs_hurdle": round(value - hurdle_value, 2),
            "on_track": value >= hurdle_value,
            "nifty_return_pct": None if nifty_ret is None else round(nifty_ret * 100.0, 2),
            "vs_nifty_pct": None if nifty_ret is None else round((ret - nifty_ret) * 100.0, 2),
            "max_drawdown_pct": round(mdd * 100.0, 2), "next_rebalance": nxt.isoformat() if nxt else None,
            "holdings": rows,
        }
