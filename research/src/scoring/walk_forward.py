"""
Project Atlas - Walk-Forward Test of Simpler Signals
====================================================
Question: is there ANY variant of the scanner that beats simply holding the universe, judged honestly?

Method (fixed BEFORE any result was seen, 2026-09-21; do not edit the grid or rule after running):
  1. A pre-registered grid of 48 candidates: 6 signals x top-N (12, 30) x rebalance (monthly, quarterly)
     x weighting (inverse-vol, equal). "composite" with 12 / monthly / inverse-vol is today's scanner.
  2. FIT: score every candidate on 2015 .. 2021 only. Selection rule: highest train excess CAGR over the
     equal-weight universe, after costs. Nothing from 2022 on may influence which candidate wins.
  3. JUDGE: run the selected candidate (and today's scanner, untouched) on 2022 .. 2026 and report excess
     CAGR, t-stat, drawdown. Also report whether the train ranking predicts the test ranking across all 48
     (Spearman): if it does not, picking a winner on history is noise.
  4. GENERALISE: apply the selected candidate, unchanged, to the other markets. No parameter was fitted on
     them, so this is the cleanest out-of-sample check available.

Multiple-comparison warning: the best of 48 on the train window looks good partly by luck. With 48 correlated
candidates the best train t-stat is expected to be around 2 even if none has real skill, so a train winner
proves nothing by itself; only the untouched test window counts.
"""
import math
from dataclasses import replace
from typing import Callable, Dict, List, Optional

from scoring.momentum_scanner import (
    ScannerConfig, _stats, active_stats, rank_periods, run_periods, slice_periods,
)

# ── PRE-REGISTERED: do not edit after running ────────────────────────────────
TRAIN_END = "2021-12-31"
TEST_START = "2022-01-01"

# score weights per signal (score_universe still applies the liquidity and above-200dma gates to all of them)
SIGNALS: Dict[str, Dict[str, float]] = {
    "composite":      {"w_mom_12_1": 0.35, "w_mom_6": 0.25, "w_trend": 0.20, "w_high": 0.20, "vol_penalty": 0.15},
    "trend_only":     {"w_mom_12_1": 0.0, "w_mom_6": 0.0, "w_trend": 1.0, "w_high": 0.0, "vol_penalty": 0.0},
    "mom_12_1_only":  {"w_mom_12_1": 1.0, "w_mom_6": 0.0, "w_trend": 0.0, "w_high": 0.0, "vol_penalty": 0.0},
    "mom_6_only":     {"w_mom_12_1": 0.0, "w_mom_6": 1.0, "w_trend": 0.0, "w_high": 0.0, "vol_penalty": 0.0},
    "high_prox_only": {"w_mom_12_1": 0.0, "w_mom_6": 0.0, "w_trend": 0.0, "w_high": 1.0, "vol_penalty": 0.0},
    "mom_plus_trend": {"w_mom_12_1": 0.5, "w_mom_6": 0.0, "w_trend": 0.5, "w_high": 0.0, "vol_penalty": 0.0},
}
TOP_N = (12, 30)
REBALANCE_DAYS = (30, 90)
WEIGHTING = ("inverse_vol", "equal")
SELECTION_RULE = "highest train excess CAGR over the equal-weight universe, after costs"
BASELINE_ID = "composite|n12|r30|inverse_vol"      # today's scanner, never re-tuned
# ─────────────────────────────────────────────────────────────────────────────


def candidates() -> List[Dict]:
    out = []
    for sig in SIGNALS:
        for n in TOP_N:
            for r in REBALANCE_DAYS:
                for w in WEIGHTING:
                    out.append({"id": f"{sig}|n{n}|r{r}|{w}", "signal": sig, "top_n": n,
                                "rebalance_days": r, "weighting": w})
    return out


def candidate_cfg(base: ScannerConfig, cand: Dict) -> ScannerConfig:
    return replace(base, top_n=cand["top_n"], rebalance_days=cand["rebalance_days"],
                   weighting=cand["weighting"], **SIGNALS[cand["signal"]])


def window_metrics(run: Dict, period_days: int) -> Dict:
    """Return/risk numbers for one window of run_periods output."""
    s = _stats(run["strat"], run["dates"], period_days)
    b = _stats(run["bench"], run["dates"], period_days)
    if not s or not b:
        return {}
    act = active_stats(run["strat"], run["bench"], period_days)
    traded = run["traded"]
    return {"cagr": s["cagr"], "universe_cagr": b["cagr"], "excess": s["cagr"] - b["cagr"],
            "t_stat": act.get("t_stat"), "max_drawdown": s["max_drawdown"], "periods": s["periods"],
            "turnover_one_way_pct": 100.0 * sum(traded) / len(traded) if traded else 0.0}


def spearman(a: List[float], b: List[float]) -> Optional[float]:
    if len(a) < 3 or len(a) != len(b):
        return None

    def ranks(x):
        order = sorted(range(len(x)), key=lambda i: x[i])
        r, i = [0.0] * len(x), 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and x[order[j + 1]] == x[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2.0 + 1.0
            i = j + 1
        return r
    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va, vb = sum((x - ma) ** 2 for x in ra), sum((y - mb) ** 2 for y in rb)
    return cov / math.sqrt(va * vb) if va > 0 and vb > 0 else None


def verdict(test: Dict) -> str:
    if not test:
        return "no test data"
    t = test.get("t_stat")
    if test["excess"] > 0 and t is not None and t >= 2.0:
        return "FOUND: positive and statistically significant out-of-sample"
    if test["excess"] > 0:
        return "positive out-of-sample but NOT significant (could be luck)"
    return "NOTHING: did not beat the universe out-of-sample"


def evaluate_grid(prepared_by_rebalance: Dict[int, Dict], base: ScannerConfig,
                  progress: Optional[Callable[[str], None]] = None) -> List[Dict]:
    """Every candidate on the train window and the test window. Ranking is cached per (signal, rebalance)."""
    out = []
    for r in REBALANCE_DAYS:
        full = prepared_by_rebalance[r]
        windows = {"train": slice_periods(full, None, TRAIN_END), "test": slice_periods(full, TEST_START, None)}
        for sig, weights in SIGNALS.items():
            sig_cfg = replace(base, rebalance_days=r, **weights)
            ranked = {w: rank_periods(win, sig_cfg) for w, win in windows.items()}
            for n in TOP_N:
                for wt in WEIGHTING:
                    cand = {"id": f"{sig}|n{n}|r{r}|{wt}", "signal": sig, "top_n": n, "rebalance_days": r, "weighting": wt}
                    cfg = replace(sig_cfg, top_n=n, weighting=wt)
                    row = dict(cand)
                    for w, win in windows.items():
                        rk, on = ranked[w]
                        row[w] = window_metrics(run_periods(win, rk, on, cfg), r) if win["table"] else {}
                    out.append(row)
            if progress:
                progress(f"rebalance {r}d / {sig} done")
    return out


def select_on_train(rows: List[Dict]) -> Dict:
    """The candidate with the highest TRAIN excess CAGR. Reads no test number."""
    valid = [r for r in rows if r.get("train")]
    return max(valid, key=lambda r: r["train"]["excess"])


def dimension_summary(rows: List[Dict]) -> Dict[str, List[Dict]]:
    """
    Average train and test excess for each design choice (signal, top-N, rebalance, weighting), over every
    candidate that uses it. Less exposed to best-of-48 luck than one winner: a choice is only interesting if it
    helps in BOTH windows, which is reported as `both_positive`.
    """
    both = [r for r in rows if r.get("train") and r.get("test")]
    out: Dict[str, List[Dict]] = {}
    for dim in ("signal", "top_n", "rebalance_days", "weighting"):
        levels = []
        for lvl in sorted({r[dim] for r in both}, key=str):
            grp = [r for r in both if r[dim] == lvl]
            tr = sum(r["train"]["excess"] for r in grp) / len(grp)
            te = sum(r["test"]["excess"] for r in grp) / len(grp)
            levels.append({"level": lvl, "n": len(grp), "train_excess": tr, "test_excess": te,
                           "both_positive": tr > 0 and te > 0})
        out[dim] = levels
    return out


def analyse(rows: List[Dict]) -> Dict:
    """Selection, its out-of-sample result, the untouched baseline, and rank persistence."""
    sel = select_on_train(rows)
    base = next(r for r in rows if r["id"] == BASELINE_ID)
    both = [r for r in rows if r.get("train") and r.get("test")]
    rho = spearman([r["train"]["excess"] for r in both], [r["test"]["excess"] for r in both])
    top5 = sorted(both, key=lambda r: -r["train"]["excess"])[:5]
    best_test = max(both, key=lambda r: r["test"]["excess"])
    beat_universe_test = sum(1 for r in both if r["test"]["excess"] > 0)
    return {
        "selection_rule": SELECTION_RULE, "candidates": len(rows), "train_end": TRAIN_END, "test_start": TEST_START,
        "selected": sel, "selected_verdict": verdict(sel.get("test", {})),
        "baseline": base, "baseline_verdict": verdict(base.get("test", {})),
        "rank_persistence_spearman": rho,
        "dimensions": dimension_summary(rows),
        "top5_by_train": [{"id": r["id"], "train_excess": r["train"]["excess"], "test_excess": r["test"]["excess"]} for r in top5],
        "top5_mean_test_excess": sum(r["test"]["excess"] for r in top5) / len(top5) if top5 else None,
        "all_mean_test_excess": sum(r["test"]["excess"] for r in both) / len(both) if both else None,
        "candidates_beating_universe_in_test": beat_universe_test,
        "hindsight_best_in_test": {"id": best_test["id"], "test_excess": best_test["test"]["excess"],
                                   "note": "chosen with hindsight: shown only to size how lucky a best-of-48 looks"},
    }
