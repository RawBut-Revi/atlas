"""
Project Atlas - DRIP Ledger
===========================
Durable, idempotent record of dividends credited and reinvested.

  - Each (symbol, ex_date) is credited once, ever: rerunning the job cannot double-count.
  - Only events on/after `tracking_start` count, so switching DRIP on never sweeps in old dividends.
  - Quantity is taken from holdings when the event is processed (recent events only: run
    daily). Holdings that changed between ex-date and processing are a known approximation.
  - TDS: a resident's dividend from one company is deducted at `tds_rate` once that company's
    FY total passes `tds_threshold`. The threshold (Rs10,000 from FY2025-26) is a rule that
    has changed before: verify the current figure and adjust it here.
  - Atomic writes (temp file + replace): a crash cannot leave a half-written ledger.
"""
import json
import os
import tempfile
from datetime import date
from typing import Dict, List


def fiscal_year(d: str) -> str:
    y, m = int(d[:4]), int(d[5:7])
    start = y if m >= 4 else y - 1
    return f"FY{start}-{str(start + 1)[2:]}"


class DripLedger:
    def __init__(self, path: str, tracking_start: str = None, tds_rate: float = 0.10,
                 tds_threshold: float = 10000.0):
        self.path = path
        self.tds_rate, self.tds_threshold = tds_rate, tds_threshold
        self.state = {"tracking_start": tracking_start or date.today().isoformat(), "cash_pool": 0.0,
                      "processed": {}, "fy_dividends": {}, "orders": []}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                self.state.update(json.load(fh))

    @property
    def cash_pool(self) -> float:
        return round(self.state["cash_pool"], 2)

    def save(self) -> None:
        d = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, indent=2)
        os.replace(tmp, self.path)

    def credit_dividends(self, events_by_symbol: Dict[str, List[Dict]], holdings: Dict[str, int],
                         asof: str) -> List[Dict]:
        """Credit new dividends into the cash pool. Returns the credits made this call."""
        credits = []
        for sym, events in events_by_symbol.items():
            qty = holdings.get(sym, 0)
            if qty <= 0:
                continue
            for e in sorted(events, key=lambda x: x["ex_date"]):
                key = f"{sym}|{e['ex_date']}"
                if key in self.state["processed"] or not (self.state["tracking_start"] <= e["ex_date"] <= asof):
                    continue
                gross = e["dps"] * qty
                fy_key = f"{sym}|{fiscal_year(e['ex_date'])}"
                fy_total = self.state["fy_dividends"].get(fy_key, 0.0) + gross
                tds = gross * self.tds_rate if fy_total > self.tds_threshold else 0.0
                net = round(gross - tds, 2)
                self.state["fy_dividends"][fy_key] = fy_total
                self.state["cash_pool"] += net
                rec = {"symbol": sym, "ex_date": e["ex_date"], "dps": e["dps"], "qty": qty,
                       "gross": round(gross, 2), "tds": round(tds, 2), "net": net}
                self.state["processed"][key] = rec
                credits.append(rec)
        return credits

    def debit(self, amount: float) -> None:
        self.state["cash_pool"] = max(0.0, self.state["cash_pool"] - amount)

    def record_order(self, order: Dict) -> None:
        self.state["orders"].append(order)
