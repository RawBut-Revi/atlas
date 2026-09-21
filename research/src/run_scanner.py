"""
Project Atlas - Investment Scanner CLI
======================================
    python run_scanner.py                       # ranked picks + backtest reference (cached prices)
    python run_scanner.py --refresh             # refetch 2 years of prices first (1-2 min)
    python run_scanner.py --from-history        # offline: seed prices from the 12-year history cache
    python run_scanner.py --backtest            # recompute the legacy 176-stock backtest report
    python run_scanner.py --study               # the same model on India-all and other markets
    python run_scanner.py --status              # paper portfolio vs the 25% goal and Nifty
    python run_scanner.py --rebalance           # plan a paper rebalance (executes nothing)
    python run_scanner.py --rebalance --execute [--capital 150000] [--force]

Momentum-led ranking, monthly rebalance. Read scoring/momentum_scanner.py for the honest caveats:
in the 2015-2026 backtest it showed no edge over holding the whole (survivorship-flattered) universe
and met the 25% goal in only some calendar years. Paper only: nothing here places a real order.
"""
import argparse
import sys

sys.stdout.reconfigure(encoding="utf-8")

from scoring.scanner_service import ScannerError, ScannerService


def pct(v, nd=1):
    return "n/a" if v is None else f"{v * 100:.{nd}f}%"


def print_backtest(rep):
    d = rep["default"]
    print(f"\nBACKTEST {d['first_date']} to {d['last_date']} ({d['periods']} monthly periods, costs included)")
    print(f"  Scanner           CAGR {pct(d['cagr'])}  vol {pct(d['ann_vol'])}  max drawdown {pct(d['max_drawdown'])}")
    print(f"  Whole universe    CAGR {pct(d['benchmark_cagr'])}  max drawdown {pct(d['benchmark_max_drawdown'])}  (equal-weight, no costs)")
    print(f"  First / second half CAGR: {pct(d['first_half_cagr'])} / {pct(d['second_half_cagr'])}")
    print(f"  25% goal met in {d['years_meeting_target']} of {d['years_total']} calendar years")
    yrs = "  ".join(f"{y}:{v * 100:+.0f}%" for y, v in d["yearly"].items())
    print(f"  By year  {yrs}")
    print("  Variants tried (reported, not hidden):")
    for name, v in rep["variants"].items():
        print(f"    {name:20s} CAGR {pct(v['cagr'])}  max drawdown {pct(v['max_drawdown'])}")
    for c in rep["caveats"]:
        print(f"  ! {c}")


def print_study(rep):
    print(f"\nSAME FIXED MODEL, EVERY MARKET (after costs; generated {rep['generated_at'][:10]}, {rep['runs']} random runs)")
    print(f"{'market':14}{'stocks':>7}{'scanner':>9}{'univ':>8}{'diff':>8}{'t':>6}{'skill%':>8}{'maxDD':>8}{'>=25%':>7}  verdict")
    for r in rep["rows"]:
        t = "n/a" if r["t_stat"] is None else f"{r['t_stat']:.2f}"
        sk = "n/a" if r["skill_percentile"] is None else f"{r['skill_percentile']:.0f}"
        name = r["market"] + ("*" if r["reference"] else "")
        print(f"{name:14}{r['stocks']:>7}{pct(r['scanner_cagr']):>9}{pct(r['universe_cagr']):>8}{r['excess'] * 100:>+7.1f}%"
              f"{t:>6}{sk:>8}{pct(r['max_drawdown'], 0):>8}{r['years_meeting_target']:>4}/{r['years_total']:<2}  {r['verdict']}")
    sm = rep["summary"]
    print(f"\nAhead of own-market universe: {sm['beat_universe']} of {sm['markets']} | statistically significant: "
          f"{sm['significant']} | reached 25%/yr: {sm['reached_25pct']}")
    print("skill% = share of RANDOM portfolios (same eligible pool) the scanner beat before costs; ~50 means no selection skill.")
    print("t < 2: the gap over the universe could be luck.   * = the old 176-stock list, for comparison.")
    for c in rep["caveats"]:
        print(f"  ! {c}")


def print_picks(out):
    print(f"\nINVEST SCANNER  prices as of {out['asof']}  ({out.get('price_age_hours')}h old)")
    print(f"Scanned {out['universe_scanned']}, eligible {out['eligible']}, invested {out['invested_pct']}%"
          + ("" if out["risk_on"] else "  [RISK-OFF: holding cash]"))
    print(f"{'#':>2} {'symbol':12}{'weight':>7}{'price':>10}{'12-1m':>8}{'6m':>7}{'trend':>8}{'vol':>6}  sector")
    for k, p in enumerate(out["picks"], 1):
        flag = "*" if p.get("unverified") else " "
        print(f"{k:>2} {p['symbol']:12}{p['weight'] * 100:6.1f}%{p['price']:>10,.2f}{p['mom_12_1'] * 100:>7.0f}%"
              f"{p['mom_6'] * 100:>6.0f}%{p['trend_pct']:>7.1f}%{p['vol'] * 100:>5.0f}%  {p.get('sector') or ''}{flag}")
    if any(p.get("unverified") for p in out["picks"]):
        print("   * fundamentals missing: quality filter could not verify this stock")
    for r in out["rejected"][:5]:
        print(f"   skipped {r['symbol']}: {', '.join(r['reasons'])}")


def print_status(st):
    if st["status"] == "NO_PORTFOLIO":
        print(st["detail"])
        return
    ann = f"{st['annualized_pct']}%" if st["annualized_pct"] is not None else st["annualized_note"]
    print(f"\nPAPER PORTFOLIO  day {st['days']}  value Rs {st['value']:,.0f} from Rs {st['capital']:,.0f}  "
          f"return {st['return_pct']:+.2f}%  annualized: {ann}")
    print(f"  25%/yr line needs Rs {st['hurdle_value']:,.0f}: {'AHEAD' if st['on_track'] else 'BEHIND'} by Rs {abs(st['vs_hurdle']):,.0f}")
    print(f"  Nifty since start: {st['nifty_return_pct']}%  (vs Nifty {st['vs_nifty_pct']}%)   max drawdown {st['max_drawdown_pct']}%")
    for h in st["holdings"]:
        print(f"  {h['symbol']:12}{h['qty']:>6} @ {h['avg_cost']:>10,.2f} -> {h['price']:>10,.2f}  {h['pnl_pct']:+.1f}%")


def print_plan(plan):
    if plan["status"] == "NOT_DUE":
        print(plan["detail"])
        return
    print(f"\n{'EXECUTED' if plan['status'] == 'EXECUTED' else 'PLAN (nothing executed)'}: portfolio Rs {plan['portfolio_value']:,.0f}, "
          f"est. costs Rs {plan['est_costs']:,.0f}, cash after Rs {plan['cash_after']:,.0f}")
    for o in plan["orders"]:
        print(f"  {o['side']:4} {o['qty']:>5} x {o['symbol']:12} @ {o['price']:>10,.2f}  (Rs {o['value']:,.0f})")
    print(plan["note"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--from-history", action="store_true",
                    help="offline fallback: seed prices from the 12-year history cache (as old as that file)")
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--study", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--rebalance", action="store_true")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--capital", type=float, default=150000.0)
    a = ap.parse_args()

    svc = ScannerService()
    try:
        if a.refresh:
            print("Refreshing prices (1-2 min)...")
            print(svc.refresh_prices())
        if a.from_history:
            print(svc.seed_from_history())
        if a.backtest:
            print_backtest(svc.backtest_report(refresh=True))
        elif a.study:
            print_study(svc.study_report())
        elif a.status:
            print_status(svc.portfolio_status())
        elif a.rebalance:
            print_plan(svc.rebalance(execute=a.execute, capital=a.capital, force=a.force))
        else:
            print_picks(svc.picks())
            try:
                print_backtest(svc.backtest_report())
            except ScannerError as e:
                print(f"\n(backtest reference unavailable: {e})")
    except ScannerError as e:
        print(f"ERROR: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
