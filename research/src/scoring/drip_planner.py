"""
Project Atlas - DRIP Planner
============================
Turns reinvestable cash into a concrete, cost-aware set of delivery buy orders.

India has no broker-side DRIP, so every reinvestment is an ordinary market purchase and pays
real charges. The planner therefore enforces what a DRIP would hide:
  - whole shares only (leftover cash carries forward)
  - a cost ceiling: a tiny order is wasted on the flat brokerage, so it waits and batches
  - concentration caps per stock and per sector, measured on the post-reinvestment portfolio,
    so the top-ranked stock cannot absorb everything
  - at most `max_orders` orders per run, best efficiency first
Pure functions: no I/O, no broker, no network.
"""
import math
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from trading.charges import calculate_delivery_buy_charges

TICK = 0.05


@dataclass(frozen=True)
class PlannerConfig:
    max_stock_weight_pct: float = 20.0
    max_sector_weight_pct: float = 40.0
    max_orders: int = 3
    min_order_value: float = 2000.0
    max_cost_pct: float = 0.75          # charges as % of order value
    min_efficiency: float = 8.0         # must beat a safe-rate hurdle after quality adjustment
    limit_buffer_pct: float = 0.30      # LIMIT above LTP so the order fills


@dataclass
class DripOrder:
    symbol: str
    qty: int
    limit_price: float
    est_value: float
    est_charges: float
    efficiency: float
    expected_return_pct: float
    weight_after_pct: float
    reason: str


@dataclass
class DripPlan:
    orders: List[DripOrder] = field(default_factory=list)
    cash_in: float = 0.0
    cash_spent: float = 0.0             # order value + charges
    carry: float = 0.0
    base_value: float = 0.0             # portfolio + cash, the denominator for weights
    skipped: List[Tuple[str, str]] = field(default_factory=list)


def _round_tick(price: float) -> float:
    return round(math.ceil(price / TICK - 1e-9) * TICK, 2)


def plan_drip(holdings: Dict[str, int], prices: Dict[str, float], scores: Dict[str, Dict],
              cash: float, cfg: PlannerConfig = PlannerConfig(),
              blocked: frozenset = frozenset()) -> DripPlan:
    plan = DripPlan(cash_in=max(cash, 0.0), carry=max(cash, 0.0))
    if cash <= 0:
        return plan

    held_value = {s: q * prices.get(s, 0.0) for s, q in holdings.items() if q > 0}
    sector_value: Dict[str, float] = {}
    for s, v in held_value.items():
        sec = scores.get(s, {}).get("sector", "UNKNOWN")
        sector_value[sec] = sector_value.get(sec, 0.0) + v
    base = sum(held_value.values()) + cash
    plan.base_value = base

    remaining = cash
    for s in sorted(scores, key=lambda k: -scores[k]["efficiency"]):
        if len(plan.orders) >= cfg.max_orders or remaining <= 0:
            break
        sc = scores[s]
        if s in blocked:
            plan.skipped.append((s, "blocked"))
            continue
        if sc["efficiency"] < cfg.min_efficiency:
            plan.skipped.append((s, f"efficiency {sc['efficiency']:.1f} < {cfg.min_efficiency:.1f}"))
            continue

        price, sec = prices[s], sc["sector"]
        room = min(cfg.max_stock_weight_pct / 100.0 * base - held_value.get(s, 0.0),
                   cfg.max_sector_weight_pct / 100.0 * base - sector_value.get(sec, 0.0))
        limit = _round_tick(price * (1 + cfg.limit_buffer_pct / 100.0))
        qty = int(min(remaining, room) // limit)
        while qty > 0 and qty * limit + calculate_delivery_buy_charges(limit, qty)["total"] > remaining:
            qty -= 1
        if qty < 1:
            plan.skipped.append((s, "concentration cap reached" if room < limit else "cash below one share"))
            continue

        c = calculate_delivery_buy_charges(limit, qty)
        value = qty * limit
        if value < cfg.min_order_value:
            plan.skipped.append((s, f"order Rs{value:,.0f} below minimum Rs{cfg.min_order_value:,.0f}"))
            continue
        if c["total"] / value * 100.0 > cfg.max_cost_pct:
            plan.skipped.append((s, f"charges {c['total'] / value * 100:.2f}% > {cfg.max_cost_pct}%"))
            continue

        held_value[s] = held_value.get(s, 0.0) + value
        sector_value[sec] = sector_value.get(sec, 0.0) + value
        remaining -= value + c["total"]
        plan.orders.append(DripOrder(
            symbol=s, qty=qty, limit_price=limit, est_value=round(value, 2),
            est_charges=round(c["total"], 2), efficiency=sc["efficiency"],
            expected_return_pct=sc["expected_return_pct"],
            weight_after_pct=round(held_value[s] / base * 100.0, 2),
            reason=f"yield {sc['dividend_yield_pct']}% + growth {sc['growth_pct']}% "
                   f"{sc['valuation_adj_pct']:+}% valuation, quality x{sc['quality_mult']}"))

    plan.cash_spent = round(cash - remaining, 2)
    plan.carry = round(remaining, 2)
    return plan
