"""
Shadow ledger: paper-only twins that test ideas on live signals without touching the real account.

Two kinds of twin are recorded for every signal that passes all entry gates:
  INVERSE  the opposite bet: direction flipped, stop and target swapped (so the twin wins exactly
           when the real trade would lose), filled with its own slippage.
  SHADOW   the bot's own trade, for (asset, strategy) pairs not yet allowed live. No real position
           is opened, so no margin is used and no real charges are paid.

Twins are managed by the daemon's normal exit logic (same trailing, stops, slippage, charges) and
live in shadow_ledger.json, never in paper_positions.json. Every twin carries `twin_of` = the id of
the signal it mirrors so real / shadow / inverse outcomes can be compared trade-for-trade.
"""
import json
import os
import threading
from datetime import datetime

from trading.fills import entry_fill, signal_price

SHADOW_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shadow_ledger.json")

# Mirror the live churn guards so twins can't recreate the Sep 4 loop.
MAX_TWINS_PER_SYMBOL_PER_DAY = 3
MAX_TWINS_PER_DAY = 8

# Evidence needed before acting on shadow results.
MIN_TRADES_FOR_DECISION = 30


def _flip(direction: str) -> str:
    return "SELL" if direction == "BUY" else "BUY"


def make_inverse(pos: dict) -> dict:
    """Opposite bet on the same signal: flipped direction, stop and target swapped."""
    asset = pos.get("asset_type", "EQUITY")
    direction = _flip(pos["direction"])
    raw = signal_price(pos["direction"], pos["entry_price"], asset)
    twin = dict(pos)
    twin.update({
        "id": f"inv_{pos['id']}",
        "twin_of": pos["id"],
        "shadow_kind": "INVERSE",
        "direction": direction,
        "entry_price": entry_fill(direction, raw, asset),
        "stop_loss": pos["target_price"],
        "target_price": pos["stop_loss"],
        "target_1": pos["stop_loss"],
        "target_2": pos["stop_loss"],
    })
    twin.pop("sl_trailed_to_cost", None)
    twin.pop("t1_booked", None)
    return twin


def make_signal_twin(pos: dict) -> dict:
    """The bot's own trade, tracked in shadow only."""
    twin = dict(pos)
    twin.update({"id": f"sh_{pos['id']}", "twin_of": pos["id"], "shadow_kind": "SHADOW"})
    return twin


class ShadowLedger:
    def __init__(self, path: str = SHADOW_FILE):
        self.path = path
        # Reentrant: the daemon holds it across a whole manage cycle while save() takes it again.
        self.lock = threading.RLock()

    def load(self) -> dict:
        with self.lock:
            if os.path.exists(self.path):
                try:
                    with open(self.path, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception:
                    pass
            return {"open_positions": [], "trade_history": [], "total_pnl": 0.0,
                    "since": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    def save(self, state: dict):
        with self.lock:
            try:
                with open(self.path, "w", encoding="utf-8") as f:
                    json.dump(state, f, indent=2)
            except Exception as e:
                print(f"[Shadow] Error saving ledger: {e}")

    def record(self, pos: dict, kind: str) -> bool:
        """Adds a twin of `pos`. Returns False if skipped by dedupe / churn caps."""
        twin = make_inverse(pos) if kind == "INVERSE" else make_signal_twin(pos)
        today = datetime.now().strftime("%Y-%m-%d")
        with self.lock:
            state = self.load()
            same_kind = [t for t in state["open_positions"] + state["trade_history"] if t.get("shadow_kind") == kind]

            if any(t["symbol"] == twin["symbol"] for t in state["open_positions"] if t.get("shadow_kind") == kind):
                return False
            todays = [t for t in same_kind if str(t.get("entry_time", ""))[:10] == today]
            if len(todays) >= MAX_TWINS_PER_DAY:
                return False
            if sum(1 for t in todays if t["symbol"] == twin["symbol"]) >= MAX_TWINS_PER_SYMBOL_PER_DAY:
                return False

            state["open_positions"].append(twin)
            self.save(state)
            return True


def build_report(shadow_state: dict, real_history: list) -> list:
    """
    Trade-for-trade comparison per (asset_type, strategy). A signal counts only once both sides are
    closed: the signal side is a real trade (`real_history`, matched by id) or a SHADOW twin;
    the other side is its INVERSE twin.

    Returns rows sorted by signal-side count: {"segment", "source" (LIVE|SHADOW), "n", "signal_net",
    "inverse_net", "signal_wr", "verdict"}.
    """
    closed = shadow_state.get("trade_history", [])
    inverse = {t["twin_of"]: t for t in closed if t.get("shadow_kind") == "INVERSE"}
    shadow_side = {t["twin_of"]: t for t in closed if t.get("shadow_kind") == "SHADOW"}
    real = {t["id"]: t for t in real_history if t.get("id") in inverse}

    agg = {}
    for twin_of, inv in inverse.items():
        if twin_of in real:
            sig, source = real[twin_of], "LIVE"
        elif twin_of in shadow_side:
            sig, source = shadow_side[twin_of], "SHADOW"
        else:
            continue
        key = (sig.get("asset_type"), sig.get("strategy"), source)
        row = agg.setdefault(key, {"n": 0, "signal_net": 0.0, "inverse_net": 0.0, "wins": 0})
        row["n"] += 1
        row["signal_net"] += sig.get("net_pnl", 0.0)
        row["inverse_net"] += inv.get("net_pnl", 0.0)
        row["wins"] += 1 if sig.get("net_pnl", 0.0) > 0 else 0

    rows = []
    for (asset, strategy, source), r in agg.items():
        n = r["n"]
        if n < MIN_TRADES_FOR_DECISION:
            verdict = f"need {MIN_TRADES_FOR_DECISION - n} more"
        elif r["inverse_net"] > 0 and r["inverse_net"] > r["signal_net"]:
            verdict = "INVERSE better"
        elif r["signal_net"] > 0:
            verdict = "PROMOTE" if source == "SHADOW" else "KEEP"
        else:
            verdict = "DROP"
        rows.append({
            "segment": f"{asset} {strategy}", "source": source, "n": n,
            "signal_net": round(r["signal_net"], 2), "inverse_net": round(r["inverse_net"], 2),
            "signal_wr": round(100.0 * r["wins"] / n, 1), "verdict": verdict,
        })
    rows.sort(key=lambda r: -r["n"])
    return rows
