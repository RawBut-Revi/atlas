"""
Project Atlas - Total-Return DRIP CLI
=====================================
Reinvests dividends into the stocks with the best quality-adjusted expected total return
(dividend yield + capital appreciation) across ~140 liquid NSE dividend payers, respecting
costs and concentration limits. Same engine as the desktop app (/api/drip/*).

    python run_drip.py                              # dry run: shows the plan, writes nothing
    python run_drip.py --execute                    # place orders (paper broker)
    python run_drip.py --init "ITC:1000,ONGC:500"   # create the paper portfolio (first run only)
    python run_drip.py --tracking-start 2026-05-01  # only count dividends with ex-date on/after this
    python run_drip.py --mode live                  # dry run against your real Upstox portfolio
    python run_drip.py --mode live --execute --confirm   # REAL orders; also needs ATLAS_LIVE_DRIP=1

Stop everything instantly by creating a file named DRIP_DISABLED in the state directory.
"""
import argparse
import sys

sys.stdout.reconfigure(encoding="utf-8")

from scoring.drip_service import HERE, DripError, DripService


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--execute", action="store_true", help="place orders (default: dry run)")
    ap.add_argument("--mode", choices=("paper", "live"), default="paper")
    ap.add_argument("--confirm", action="store_true", help="required with --mode live --execute")
    ap.add_argument("--init", help='create paper holdings, e.g. "ITC:1000,ONGC:500"')
    ap.add_argument("--tracking-start", help="ignore dividends with ex-date before YYYY-MM-DD")
    ap.add_argument("--state-dir", default=HERE)
    ap.add_argument("--max-deploy", type=float, default=50000.0, help="max Rs spent per run")
    a = ap.parse_args()

    svc = DripService(a.state_dir)
    try:
        if a.init:
            svc.init_paper_portfolio({k: int(v) for k, v in (i.split(":") for i in a.init.split(","))})
            print(f"Initialised paper portfolio: {svc.status()['holdings']}")
        rep = svc.cycle(execute=a.execute, mode=a.mode, confirm=a.confirm, max_deploy=a.max_deploy,
                        tracking_start=a.tracking_start)
    except DripError as e:
        print(f"REFUSED: {e}")
        return 2

    print(f"\nStatus: {rep['status']} ({rep.get('mode', a.mode)})  {rep.get('detail', '')}")
    if rep["status"] == "DISABLED":
        return 0
    print(f"Universe: {rep['universe_size']} liquid dividend payers"
          + (f" | no data for: {', '.join(rep['profile_errors'])}" if rep["profile_errors"] else ""))
    print("\nTop ranked by quality-adjusted expected total return:")
    for v in rep["ranking"][:10]:
        print(f"  {v['symbol']:12} eff {v['efficiency']:>5.1f} = yield {v['dividend_yield_pct']:.1f}% + growth {v['growth_pct']:.1f}%"
              f" {v['valuation_adj_pct']:+.1f} val {v['trend_adj_pct']:+.1f} trend | {v['sector']}"
              + (f"  [{'; '.join(v['flags'])}]" if v["flags"] else ""))
    print()
    for c in rep["credits"]:
        print(f"  dividend  {c['symbol']:11} ex {c['ex_date']}  {c['qty']} x Rs{c['dps']} = Rs{c['gross']:,.2f}"
              f"  (TDS Rs{c['tds']:,.2f})  net Rs{c['net']:,.2f}")
    print(f"  ledger pool Rs{rep['ledger_pool']:,.2f} | broker funds Rs{rep['broker_funds']:,.2f} | spendable Rs{rep['spendable']:,.2f}")
    plan = rep["plan"]
    for o in plan["orders"]:
        print(f"  BUY {o['symbol']:11} {o['qty']:>4} @ limit Rs{o['limit_price']:,.2f}  value Rs{o['est_value']:,.0f}"
              f"  charges Rs{o['est_charges']:,.0f}  -> {o['weight_after_pct']}% of portfolio\n      {o['reason']}")
    for sym, why in plan["skipped"][:5]:
        print(f"  skip {sym:11} {why}")
    print(f"  carry forward Rs{plan['carry']:,.2f}")
    for e in rep["executed"]:
        print(f"  executed: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
