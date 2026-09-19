"""
Project Atlas - Total-Return DRIP CLI
=====================================
Reinvests dividends into the stocks with the best quality-adjusted expected total return
(dividend yield + capital appreciation), respecting costs and concentration limits.

    python run_drip.py                              # dry run: shows the plan, writes nothing
    python run_drip.py --execute                    # place orders (paper broker only)
    python run_drip.py --init "ITC:1000,ONGC:500"   # create the paper portfolio (first run only)
    python run_drip.py --tracking-start 2026-05-01  # only count dividends with ex-date on/after this

Stop everything instantly by creating a file named DRIP_DISABLED in the state directory.
Live Upstox execution is intentionally not available yet.
"""
import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8")

from data.dividend_events import fetch_snapshot
from data.fundamental_data import STOCK_FUNDAMENTALS
from scoring.drip_ledger import DripLedger
from scoring.drip_planner import PlannerConfig
from scoring.drip_runner import PaperDeliveryBroker, run_drip_cycle

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--execute", action="store_true", help="place orders (default: dry run)")
    ap.add_argument("--live", action="store_true", help="real Upstox orders (NOT available yet)")
    ap.add_argument("--init", help='create paper holdings, e.g. "ITC:1000,ONGC:500"')
    ap.add_argument("--tracking-start", help="ignore dividends with ex-date before YYYY-MM-DD")
    ap.add_argument("--state-dir", default=HERE)
    ap.add_argument("--max-deploy", type=float, default=50000.0, help="max Rs spent per run")
    a = ap.parse_args()

    if a.live:
        print("REFUSED: live Upstox execution is not implemented. Paper mode only.")
        return 2

    pf, lf = (os.path.join(a.state_dir, n) for n in ("drip_portfolio.json", "drip_ledger.json"))
    broker = PaperDeliveryBroker(pf)
    if a.init:
        if broker.get_holdings():
            print("REFUSED: paper portfolio already exists; delete drip_portfolio.json to re-init.")
            return 2
        for item in a.init.split(","):
            sym, qty = item.split(":")
            broker.state["holdings"][sym.strip().upper()] = {"qty": int(qty), "cost": 0.0}
        broker.save()
        print(f"Initialised paper portfolio: {broker.get_holdings()}")

    ledger = DripLedger(lf, tracking_start=a.tracking_start)
    if a.tracking_start:
        ledger.state["tracking_start"] = a.tracking_start
    holdings = broker.get_holdings()
    if not holdings:
        print("No holdings. Create a paper portfolio with --init \"SYMBOL:QTY,...\"")
        return 1

    symbols = sorted(set(STOCK_FUNDAMENTALS) | set(holdings))
    with ThreadPoolExecutor(8) as ex:
        cache = dict(zip(symbols, ex.map(fetch_snapshot, symbols)))

    rep = run_drip_cycle(broker, ledger, STOCK_FUNDAMENTALS, lambda s: cache.get(s), PlannerConfig(),
                         execute=a.execute, max_deploy_per_run=a.max_deploy,
                         kill_switch_path=os.path.join(a.state_dir, "DRIP_DISABLED"))

    print(f"\nStatus: {rep['status']}  {rep.get('detail', '')}")
    if rep["status"] == "DISABLED":
        return 0
    for c in rep["credits"]:
        print(f"  dividend  {c['symbol']:11} ex {c['ex_date']}  {c['qty']} x Rs{c['dps']} = Rs{c['gross']:,.2f}"
              f"  (TDS Rs{c['tds']:,.2f})  net Rs{c['net']:,.2f}")
    print(f"  ledger pool Rs{rep['ledger_pool']:,.2f} | broker funds Rs{rep['broker_funds']:,.2f} | "
          f"spendable Rs{rep['spendable']:,.2f}")
    plan = rep["plan"]
    for o in plan["orders"]:
        print(f"  BUY {o['symbol']:11} {o['qty']:>4} @ limit Rs{o['limit_price']:,.2f}  value Rs{o['est_value']:,.0f}"
              f"  charges Rs{o['est_charges']:,.0f}  -> {o['weight_after_pct']}% of portfolio\n      {o['reason']}")
    for sym, why in plan["skipped"][:6]:
        print(f"  skip {sym:11} {why}")
    print(f"  carry forward Rs{plan['carry']:,.2f}")
    for e in rep["executed"]:
        print(f"  executed: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
