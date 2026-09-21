"""
Project Atlas - Multi-Market Efficiency Study
=============================================
Runs the SAME fixed scanner model (same score weights, top 12, monthly, no per-market tuning) on India's
full NSE equity list and on the main index constituents of other markets, to see whether its edge
generalises. Only the market's own transaction cost and liquidity floor change.

    python run_market_study.py                      # study every market with stored data
    python run_market_study.py --fetch              # download missing history first (India: ~10 min)
    python run_market_study.py --markets us_sp500,india_all
    python run_market_study.py --runs 300           # random-portfolio null test size

How to read it (see scoring/momentum_scanner.py):
  * "vs universe" is the scanner minus the equal-weight universe of the same stocks, after costs.
  * "skill" is the scanner vs RANDOM picks from the same eligible pool, both before costs: does the
    ranking beat chance? "beats X% random" near 50 means no skill; near 100 means real selection skill.
  * t-stat is the monthly excess return over the universe; |t| under 2 is not distinguishable from luck.
Every universe is TODAY'S constituents: results are survivorship-flattered, India-all the most so.
"""
import argparse
import json
import os
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8")

from data.market_history import fetch_market, iter_histories, load_history, stored_count
from data.market_universe import MARKETS, universe
from scoring.momentum_scanner import ScannerConfig, make_dates, prepare_periods, summarize
from trading.universe import NSE_UNIVERSE

HERE = os.path.dirname(os.path.abspath(__file__))
STUDY_PATH = os.path.join(HERE, "data", "market_study.json")


def market_cfg(spec) -> ScannerConfig:
    """The fixed model with only the market's own cost and liquidity floor swapped in."""
    return replace(ScannerConfig(), cost_per_side=spec.cost_per_side, min_turnover_cr=spec.min_turnover / 1e7,
                   turnover_scale=spec.turnover_scale)


def index_cagr(bench_ts_close, first_ts: int, last_ts: int):
    """Price-only CAGR of the real index over the study window (no dividends: understates a total-return fund)."""
    ts, close = bench_ts_close
    import bisect
    i, j = bisect.bisect_left(ts, first_ts), bisect.bisect_right(ts, last_ts) - 1
    if i >= j or close[i] <= 0:
        return None
    years = (ts[j] - ts[i]) / (365.25 * 86400)
    return (close[j] / close[i]) ** (1.0 / years) - 1.0 if years > 0 else None


def study_market(key: str, entries, spec, runs: int, label: str = None) -> dict:
    cfg = market_cfg(spec)
    bench = load_history("_benchmarks", spec.benchmark)
    ref = bench["ts"] if bench else None
    if ref is None:
        first = next(iter_histories(key, entries), None)
        if first is None:
            return {"error": "no stored history"}
        ref = first[1]["ts"]
    dates, end = make_dates(ref, cfg)
    t = time.time()
    prepared = prepare_periods(iter_histories(key, entries), dates, end, cfg)
    prep_s = time.time() - t
    out = summarize(prepared, cfg, random_runs=runs)
    out["variants"] = {
        "risk_off_switch_on": _compact(summarize(prepared, replace(cfg, regime_filter=True))),
        "costs_doubled": _compact(summarize(prepared, replace(cfg, cost_per_side=cfg.cost_per_side * 2))),
    }
    out.update({"market": label or key, "name": spec.name, "currency": spec.currency,
                "stocks_in_universe": len(entries), "stocks_with_data": stored_count(key, entries),
                "cost_per_side": spec.cost_per_side, "prep_seconds": round(prep_s, 1),
                "index_cagr_price": index_cagr((bench["ts"], bench["close"]), dates[0], end) if bench and dates else None})
    return out


def _compact(r: dict) -> dict:
    return {"cagr": r["strategy"].get("cagr"), "max_drawdown": r["strategy"].get("max_drawdown"),
            "benchmark_cagr": r["benchmark"].get("cagr")}


def pct(v, nd=1):
    return "  n/a" if v is None else f"{v * 100:{nd + 4}.{nd}f}%"


def print_table(results: dict) -> None:
    print(f"\n{'market':13}{'stocks':>7}{'elig':>6}{'scanner':>9}{'univ':>8}{'index':>8}{'vs univ':>9}{'t-stat':>7}"
          f"{'noCost':>8}{'random':>8}{'beats%':>8}{'maxDD':>8}{'yrs>=25':>9}{'half1/half2 (scanner - univ)':>32}")
    for k, r in results.items():
        if "error" in r:
            print(f"{k:13} {r['error']}")
            continue
        s, b, a, rn, tn = r["strategy"], r["benchmark"], r.get("active", {}), r.get("random", {}), r["turnover"]
        h1 = r["first_half"].get("cagr", 0) - r["benchmark_first_half"].get("cagr", 0)
        h2 = r["second_half"].get("cagr", 0) - r["benchmark_second_half"].get("cagr", 0)
        t = a.get("t_stat")
        print(f"{k:13}{r['stocks_with_data']:>7}{r['avg_eligible']:>6.0f}{pct(s['cagr'])}{pct(b['cagr'])}{pct(r['index_cagr_price'])}"
              f"{pct(s['cagr'] - b['cagr'])}{(f'{t:7.2f}' if t is not None else '    n/a')}{pct(tn['cagr_before_costs'])}"
              f"{pct(rn.get('median_cagr'))}{rn.get('scanner_percentile', float('nan')):>7.0f}%{pct(s['max_drawdown'], 0)}"
              f"{r['years_meeting_target']:>5}/{r['years_total']:<3}   {h1 * 100:+6.1f}% / {h2 * 100:+6.1f}%")
    print("\nscanner/univ/index = CAGR after costs (index is price-only). noCost = scanner before costs. random = median random-pick "
          "CAGR before costs.\nbeats% = share of random portfolios the scanner beat before costs (50 = no skill). t-stat = monthly excess over universe.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--markets", default="", help="comma list of market keys (default: all with data) + india_176")
    ap.add_argument("--fetch", action="store_true", help="download missing/stale history first")
    ap.add_argument("--refresh-universe", action="store_true")
    ap.add_argument("--runs", type=int, default=200)
    a = ap.parse_args()

    keys = [k for k in a.markets.split(",") if k] or list(MARKETS)
    results, t0 = {}, time.time()
    for key in keys:
        if key == "india_176":
            continue
        spec = MARKETS[key]
        entries = universe(key, refresh=a.refresh_universe)
        if a.fetch:
            print(f"[{key}] fetching...", flush=True)
            print(f"[{key}]", fetch_market(key, entries, progress=lambda d, n, k=key: print(f"  [{k}] {d}/{n}", flush=True)))
        if stored_count(key, entries) < 20:
            results[key] = {"error": "no stored history: run with --fetch"}
            continue
        print(f"[{key}] {stored_count(key, entries)} stocks stored: running study...", flush=True)
        results[key] = study_market(key, entries, spec, a.runs)
        print(f"[{key}] done in {time.time() - t0:.0f}s total", flush=True)
        if key == "india_all" and not a.markets or key == "india_all" and "india_176" in a.markets:
            slim = [e for e in entries if e["symbol"] in NSE_UNIVERSE]
            print(f"[india_176] {len(slim)} curated stocks (the old scan universe): running study...", flush=True)
            results["india_176"] = study_market("india_all", slim, spec, a.runs, label="india_176")

    print_table(results)
    with open(STUDY_PATH, "w", encoding="utf-8") as fh:
        json.dump({"generated_at": datetime.now(timezone.utc).isoformat(), "runs": a.runs, "results": results,
                   "caveats": ["universes are today's constituents: survivorship-flattered (India-all the most)",
                               "world markets use main-index constituents, not every listed stock",
                               "costs are stated assumptions per market", "same fixed model everywhere: no per-market tuning"]},
                  fh)
    print(f"\nSaved {STUDY_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
