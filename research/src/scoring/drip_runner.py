"""
Project Atlas - DRIP Runner (executor)
======================================
One reinvestment cycle:  credit new dividends -> score -> plan -> (optionally) place orders.

Guardrails, all enforced here rather than trusted to callers:
  - dry run by default: `execute=False` writes NOTHING (no ledger, no broker state)
  - kill switch: if the file at `kill_switch_path` exists, nothing runs
  - spend only cash actually sitting in the broker account: dividends land in the bank
    account, not the broker ledger, so the spendable amount is min(ledger pool, broker funds)
  - per-run spend cap (`max_deploy_per_run`) trims the lowest-ranked orders first
  - orders are placed only while NSE is open (Mon-Fri 09:15-15:30 IST; exchange holidays are
    not modelled: the broker will reject those)
Only PaperDeliveryBroker exists. A live Upstox adapter must implement the same three methods
(get_holdings, get_funds, place_order) and is deliberately not included yet.
"""
import json
import os
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

from scoring.drip_ledger import DripLedger
from scoring.drip_planner import PlannerConfig, plan_drip
from scoring.total_return_score import ScoreConfig, score_universe
from data.dividend_events import trailing_12m_dps
from trading.charges import calculate_delivery_buy_charges

IST = timezone(timedelta(hours=5, minutes=30))


def is_market_open(now: datetime) -> bool:
    now = now.astimezone(IST)
    return now.weekday() < 5 and (9, 15) <= (now.hour, now.minute) <= (15, 30)


class PaperDeliveryBroker:
    """Simulated delivery account persisted in its own file (never touches paper_positions.json)."""

    def __init__(self, path: str):
        self.path = path
        self.state = {"cash": 0.0, "holdings": {}, "orders": []}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                self.state.update(json.load(fh))

    def save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, indent=2)
        os.replace(tmp, self.path)

    def get_holdings(self) -> Dict[str, int]:
        return {s: h["qty"] for s, h in self.state["holdings"].items() if h["qty"] > 0}

    def get_funds(self) -> float:
        return round(self.state["cash"], 2)

    def deposit(self, amount: float) -> None:
        """Stands in for the user moving dividend money from bank to broker."""
        self.state["cash"] += amount

    def place_order(self, symbol: str, qty: int, limit_price: float, ref_price: float) -> Dict:
        if ref_price > limit_price:
            return {"symbol": symbol, "status": "REJECTED", "reason": "limit below market"}
        c = calculate_delivery_buy_charges(ref_price, qty)
        cost = c["value"] + c["total"]
        if cost > self.state["cash"] + 1e-9:
            return {"symbol": symbol, "status": "REJECTED", "reason": "insufficient funds"}
        self.state["cash"] -= cost
        h = self.state["holdings"].setdefault(symbol, {"qty": 0, "cost": 0.0})
        h["qty"] += qty
        h["cost"] += cost
        res = {"symbol": symbol, "status": "FILLED", "qty": qty, "fill_price": ref_price,
               "charges": round(c["total"], 2), "cash_used": round(cost, 2)}
        self.state["orders"].append(res)
        return res


def run_drip_cycle(broker, ledger: DripLedger, fundamentals: Dict[str, Dict],
                   snapshot_fn: Callable[[str], Optional[Dict]],
                   cfg: PlannerConfig = PlannerConfig(), score_cfg: ScoreConfig = ScoreConfig(),
                   execute: bool = False, now: Optional[datetime] = None,
                   max_deploy_per_run: float = 50000.0,
                   kill_switch_path: Optional[str] = None,
                   paper_auto_fund: bool = True) -> Dict:
    now = now or datetime.now(IST)
    if kill_switch_path and os.path.exists(kill_switch_path):
        return {"status": "DISABLED", "detail": f"kill switch present: {kill_switch_path}"}

    holdings = broker.get_holdings()
    symbols = set(fundamentals) | set(holdings)
    snaps = {s: snap for s in symbols if (snap := snapshot_fn(s))}   # one network call per symbol
    prices = {s: v["price"] for s, v in snaps.items()}
    events = {s: v["events"] for s, v in snaps.items()}
    trends = {s: v["trend_pct"] for s, v in snaps.items() if v.get("trend_pct") is not None}

    asof = now.astimezone(IST).date()
    credits = ledger.credit_dividends(events, holdings, asof.isoformat())
    if paper_auto_fund and isinstance(broker, PaperDeliveryBroker):
        for c in credits:
            broker.deposit(c["net"])

    ttm = {s: trailing_12m_dps(events[s], asof) or None for s in fundamentals if events.get(s)}
    scores = score_universe(fundamentals, prices, ttm, trends, score_cfg)

    spendable = round(min(ledger.cash_pool, broker.get_funds()), 2)
    plan = plan_drip(holdings, prices, scores, spendable, cfg)

    # Per-run cap: keep best-ranked orders until the cap is reached.
    kept, spent = [], 0.0
    for o in plan.orders:
        if spent + o.est_value + o.est_charges <= max_deploy_per_run:
            kept.append(o)
            spent += o.est_value + o.est_charges
    trimmed = len(plan.orders) - len(kept)
    plan.orders = kept
    plan.cash_spent = round(spent, 2)
    plan.carry = round(spendable - spent, 2)

    report = {"status": "PLANNED", "credits": credits, "ledger_pool": ledger.cash_pool,
              "broker_funds": broker.get_funds(), "spendable": spendable,
              "plan": asdict(plan), "trimmed_by_cap": trimmed, "executed": []}

    if not execute:
        report["detail"] = "dry run: nothing written"
        return report

    if not is_market_open(now):
        ledger.save()          # dividend credits are safe to persist; orders wait for the market
        if isinstance(broker, PaperDeliveryBroker):
            broker.save()
        report["status"] = "MARKET_CLOSED"
        return report

    for o in plan.orders:
        res = broker.place_order(o.symbol, o.qty, o.limit_price, prices[o.symbol])
        report["executed"].append(res)
        if res["status"] == "FILLED":
            ledger.debit(res["cash_used"])
            ledger.record_order({**res, "time": now.isoformat(), "efficiency": o.efficiency})
    ledger.save()
    if isinstance(broker, PaperDeliveryBroker):
        broker.save()
    report["status"] = "EXECUTED"
    report["ledger_pool"] = ledger.cash_pool
    return report
