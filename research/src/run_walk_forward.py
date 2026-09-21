"""
Project Atlas - Walk-Forward Test of Simpler Signals
====================================================
    python run_walk_forward.py            # needs the stored history: run_scanner.py --refresh / run_market_study.py --fetch

Fits 48 pre-registered scanner variants on India-all 2015-2021, judges the winner (and today's scanner) on
2022-2026 only, then applies the winner unchanged to the other markets. Read scoring/walk_forward.py for the
method and its multiple-comparison caveat. Results are saved to data/walk_forward.json.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8")

from data.market_history import iter_histories, load_history
from data.market_universe import MARKETS, universe
from run_market_study import market_cfg
from scoring.momentum_scanner import make_dates, prepare_periods, run_periods, rank_periods, slice_periods
from scoring.walk_forward import (
    BASELINE_ID, REBALANCE_DAYS, SIGNALS, TEST_START, analyse, candidate_cfg, candidates, evaluate_grid, verdict,
    window_metrics,
)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "walk_forward.json")


def prepare(key: str, cfg, rebalance_days: int):
    from dataclasses import replace
    c = replace(cfg, rebalance_days=rebalance_days)
    spec = MARKETS[key]
    bench = load_history("_benchmarks", spec.benchmark)
    entries = universe(key)
    ref = bench["ts"] if bench else next(iter_histories(key, entries))[1]["ts"]
    dates, end = make_dates(ref, c)
    return prepare_periods(iter_histories(key, entries), dates, end, c)


def pct(v, nd=1):
    return "   n/a" if v is None else f"{v * 100:6.{nd}f}%"


def fmt_row(r, label=None):
    tr, te = r["train"], r["test"]
    t = lambda x: "  n/a" if x is None else f"{x:5.2f}"          # noqa: E731
    return (f"{(label or r['id']):38}{pct(tr['cagr'])}{pct(tr['excess'])}{t(tr['t_stat'])}   |"
            f"{pct(te['cagr'])}{pct(te['universe_cagr'])}{pct(te['excess'])}{t(te['t_stat'])}{pct(te['max_drawdown'], 0)}")


def main() -> int:
    t0 = time.time()
    base = market_cfg(MARKETS["india_all"])
    print(f"Preparing India-all point-in-time tables (monthly and quarterly)...", flush=True)
    prepared = {r: prepare("india_all", base, r) for r in REBALANCE_DAYS}
    print(f"  prepared in {time.time() - t0:.0f}s. Evaluating {len(candidates())} pre-registered candidates...", flush=True)
    rows = evaluate_grid(prepared, base)
    res = analyse(rows)
    sel, bl = res["selected"], res["baseline"]

    print(f"\nFIT on 2015-{res['train_end'][:4]}, JUDGE on {TEST_START[:4]}-2026. Selection rule: {res['selection_rule']}")
    hdr = f"{'candidate':38}{'trainCAGR':>9}{'trainXS':>8}{'t':>6}   |{'testCAGR':>8}{'univ':>8}{'testXS':>8}{'t':>6}{'maxDD':>8}"
    print("\n" + hdr)
    print(fmt_row(sel, "SELECTED " + sel["id"]))
    print(fmt_row(bl, "TODAY'S SCANNER " + bl["id"]))
    print("\nTop 5 by train (what the selection rule saw) and what they did afterwards:")
    for x in res["top5_by_train"]:
        print(f"  {x['id']:38} train excess {pct(x['train_excess'])}  ->  test excess {pct(x['test_excess'])}")
    print(f"\nRank persistence (Spearman of train excess vs test excess across all {res['candidates']}): "
          f"{res['rank_persistence_spearman']:.2f}   (0 = the past ranking tells you nothing about the future one)")
    print(f"Average test excess: top-5-by-train {pct(res['top5_mean_test_excess'])} vs all candidates {pct(res['all_mean_test_excess'])}")
    print(f"Candidates that beat the universe in the test window: {res['candidates_beating_universe_in_test']} of {res['candidates']}")
    hb = res["hindsight_best_in_test"]
    print(f"Hindsight best in test (NOT usable, luck yardstick): {hb['id']} {pct(hb['test_excess'])}")
    print("\nWhich design choices helped in BOTH windows (average excess over the universe, after costs)?")
    for dim, levels in res["dimensions"].items():
        for lv in levels:
            mark = "  <- positive in both" if lv["both_positive"] else ""
            print(f"  {dim:15}{str(lv['level']):16} train {pct(lv['train_excess'])}   test {pct(lv['test_excess'])}{mark}")
    print(f"\nSELECTED  -> {res['selected_verdict']}")
    print(f"BASELINE  -> {res['baseline_verdict']}")

    # Generalise: the selected candidate and today's scanner on other markets, nothing refitted.
    print("\nSame selected candidate, unchanged, on the other markets (no parameter was fitted on them).")
    print("CAUTION: these universes are TODAY'S index members, so they contain stocks that entered the index AFTER their rise")
    print("(e.g. HOOD, CVNA, LITE, BE in the S&P 500). A trend-chaser picking from that list looks brilliant through hindsight, not skill.")
    print("Treat any large cross-market number below as look-ahead, not as evidence.")
    print(f"{'market':13}{'selected 2022+':>15}{'universe':>10}{'excess':>9}{'t':>6}{'| today 2022+':>14}{'excess':>9}{'| selected full':>16}{'excess':>9}")
    cross = {}
    for key, spec in MARKETS.items():
        if key == "india_all":
            continue
        mcfg = market_cfg(spec)
        out = {}
        for label, cand in (("selected", sel), ("baseline", bl)):
            cfg = candidate_cfg(mcfg, cand)
            P = prepare(key, mcfg, cand["rebalance_days"])
            for wname, win in (("test", slice_periods(P, TEST_START, None)), ("full", P)):
                if not win["table"]:
                    continue
                rk, on = rank_periods(win, cfg)
                out[f"{label}_{wname}"] = window_metrics(run_periods(win, rk, on, cfg), cand["rebalance_days"])
        cross[key] = out
        s, b, f = out.get("selected_test", {}), out.get("baseline_test", {}), out.get("selected_full", {})
        if s and b and f:
            t = "  n/a" if s["t_stat"] is None else f"{s['t_stat']:5.2f}"
            print(f"{key:13}{pct(s['cagr']):>15}{pct(s['universe_cagr']):>10}{pct(s['excess']):>9}{t:>6}"
                  f"{pct(b['cagr']):>14}{pct(b['excess']):>9}{pct(f['cagr']):>16}{pct(f['excess']):>9}")
    beat = sum(1 for o in cross.values() if o.get("selected_test", {}).get("excess", -1) > 0)
    print(f"\nAhead of the universe in 2022+ in {beat} of {len(cross)} other markets: NOT trustworthy evidence (see caution above).")

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"generated_at": datetime.now(timezone.utc).isoformat(), "result": res, "cross_market": cross,
                   "grid": rows}, fh)
    print(f"\nSaved {OUT}  ({time.time() - t0:.0f}s total)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
