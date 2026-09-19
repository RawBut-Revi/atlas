"""
Project Atlas - DRIP score backtest CLI
=======================================
    python run_drip_backtest.py             # uses cached 12y history if under 7 days old
    python run_drip_backtest.py --refresh   # refetch from Yahoo

Ranks the ~144-stock dividend universe monthly using only data available on each date and
reports whether top-ranked stocks earned better forward total returns. Read the caveats in
scoring/drip_backtest.py: it tests the price/dividend half of the score only, and is flattered
by survivorship bias and the absence of transaction costs.
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8")

from data.stock_profiles import YahooSession, parse_chart_history
from scoring.drip_backtest import HORIZONS_DAYS, run_backtest
from trading.universe import NSE_UNIVERSE

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data", "history_cache.json")


def load_histories(refresh: bool):
    if not refresh and os.path.exists(CACHE) and time.time() - os.path.getmtime(CACHE) < 7 * 86400:
        with open(CACHE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    s = YahooSession()
    syms = list(NSE_UNIVERSE)
    with ThreadPoolExecutor(6) as ex:
        raw = list(ex.map(lambda x: parse_chart_history(s.chart(x, 12) or {}), syms))
    out = {k: v for k, v in zip(syms, raw) if v}
    tmp = CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh)
    os.replace(tmp, CACHE)
    return out


def fmt(v, pct=False, nd=2):
    if v is None:
        return "   n/a"
    return f"{v * 100:6.1f}%" if pct else f"{v:6.{nd}f}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    t0 = time.time()
    hist = load_histories(a.refresh)
    print(f"{len(hist)} stocks loaded ({time.time() - t0:.0f}s). Running point-in-time backtest...")
    out = run_backtest(hist, seed=a.seed)
    print(f"Rebalance dates {out['first_date']} to {out['last_date']} ({time.time() - t0:.0f}s total)\n")

    for h in HORIZONS_DAYS:
        print(f"=== Forward horizon {h} ===")
        print(f"{'signal':15}{'meanIC':>8}{'IC t':>7}{'IC>0':>7}{'1st half':>9}{'2nd half':>9}"
              f"{'top20%':>9}{'bottom':>9}{'universe':>9}{'top-univ':>9}{'excess t':>9}{'n(indep)':>9}")
        for name, byh in out["summary"].items():
            r = byh[h]
            print(f"{name:15}{fmt(r['mean_ic'])}{fmt(r['ic_t'], nd=1):>7}"
                  f"{('%5.0f%%' % r['ic_positive_pct']) if r['ic_positive_pct'] is not None else '   n/a':>7}"
                  f"{fmt(r['ic_first_half']):>9}{fmt(r['ic_second_half']):>9}"
                  f"{fmt(r['top_ret'], True):>9}{fmt(r['bottom_ret'], True):>9}{fmt(r['universe_ret'], True):>9}"
                  f"{fmt(r['top_minus_universe'], True):>9}{fmt(r['excess_t'], nd=1):>9}{r['independent_dates']:>9}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
