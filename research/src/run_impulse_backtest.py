"""
Impulse + Consolidation + Breakout: 15-minute backtest CLI
==========================================================
    python run_impulse_backtest.py --csv AAPL_15m.csv MSFT_15m.csv          # your own OHLCV CSVs
    python run_impulse_backtest.py --csv-dir intraday/                       # every *.csv in a folder
    python run_impulse_backtest.py --fetch AAPL,MSFT,NVDA                    # download last ~60 days (Yahoo's 15m limit)
    python run_impulse_backtest.py --fetch AAPL --compare                    # run the spec's readings side by side

CSV: a timestamp column (timestamp/datetime/date/time) + open, high, low, close, volume. Bars outside
--session-start/--session-end are dropped (default 09:30-16:00, NASDAQ, exchange-local time; use
--tz if your timestamps carry a timezone, e.g. America/New_York). For NSE: --session-end 15:30 --session-start 09:15.

Outputs (in --out): trade_journal.csv, rejected_setups.csv, equity_curve.csv/.png, summary.json.
"""
import argparse
import glob
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

from impulse_bt.data import fetch_yahoo_15m, load_csv, save_csv, session_filter
from impulse_bt.metrics import pool, summarize
from impulse_bt.report import write_outputs
from impulse_bt.strategy import Params, run_backtest

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(HERE), "intraday_data")

# The spec is ambiguous in three places; --compare runs the sensible combinations.
VARIANTS = {
    "1 as written":                  dict(consol_mode="window", sl_mode="literal", require_impulse_direction=False),
    "2 as written, per-candle range": dict(consol_mode="candle", sl_mode="literal", require_impulse_direction=False),
    "3 stops on the losing side":    dict(consol_mode="window", sl_mode="protective", require_impulse_direction=False),
    "4 stops fixed, per-candle":     dict(consol_mode="candle", sl_mode="protective", require_impulse_direction=False),
    "5 #4 + with-impulse only":      dict(consol_mode="candle", sl_mode="protective", require_impulse_direction=True),
}


def run_all(data, params):
    return [run_backtest(df, params, sym) for sym, df in data.items()]


def line(name, results, params):
    trades, eq = pool(results, params.initial_account)
    s = summarize(trades, eq, params.initial_account)
    rej = pd.concat([r.rejected for r in results if len(r.rejected)], ignore_index=True) if any(len(r.rejected) for r in results) else pd.DataFrame(columns=["reason"])
    counts = rej["reason"].value_counts().to_dict()
    return s, counts, trades, eq


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", nargs="*", default=[])
    ap.add_argument("--csv-dir")
    ap.add_argument("--fetch", help="comma-separated symbols to download (Yahoo, last ~60 days)")
    ap.add_argument("--tz", default=None)
    ap.add_argument("--fetch-tz", default="America/New_York", help="exchange timezone for --fetch (NSE: Asia/Kolkata)")
    ap.add_argument("--session-start", default="09:30")
    ap.add_argument("--session-end", default="16:00")
    ap.add_argument("--account", type=float, default=100_000.0)
    ap.add_argument("--consol-mode", choices=("window", "candle"), default="window")
    ap.add_argument("--sl-mode", choices=("literal", "protective"), default="literal")
    ap.add_argument("--with-impulse", action="store_true", help="breakout must match the impulse direction")
    ap.add_argument("--consol-mult", type=float, default=0.5, help="consolidation range < this x ATR (spec: 0.5)")
    ap.add_argument("--impulse-mult", type=float, default=2.0, help="impulse range > this x ATR (spec: 2.0)")
    ap.add_argument("--sweep", action="store_true", help="EXPLORATORY: loosen the tightness threshold to see where trades appear")
    ap.add_argument("--commission", type=float, default=0.0, help="per share, per side")
    ap.add_argument("--slippage", type=float, default=0.0, help="per share, against stop/time-stop exits")
    ap.add_argument("--compare", action="store_true", help="run the spec's readings side by side")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(HERE), "backtest_output"))
    a = ap.parse_args()

    data = {}
    paths = list(a.csv) + (sorted(glob.glob(os.path.join(a.csv_dir, "*.csv"))) if a.csv_dir else [])
    for pth in paths:
        data[os.path.basename(pth).split("_")[0].split(".")[0].upper()] = load_csv(pth, a.tz)
    if a.fetch:
        syms = [s.strip().upper() for s in a.fetch.split(",") if s.strip()]
        with ThreadPoolExecutor(6) as ex:
            got = list(ex.map(lambda s: (s, fetch_yahoo_15m(s, a.fetch_tz)), syms))
        for s, df in got:
            save_csv(df, os.path.join(DATA_DIR, f"{s}_15m.csv"))
            data[s] = df
        print(f"Downloaded {len(got)} symbols to {DATA_DIR}")
    if not data:
        print("No data: pass --csv, --csv-dir or --fetch.")
        return 1
    data = {s: session_filter(df, a.session_start, a.session_end) for s, df in data.items()}
    data = {s: df for s, df in data.items() if len(df) > 100}
    span = f"{min(df.index[0] for df in data.values()).date()} to {max(df.index[-1] for df in data.values()).date()}"
    print(f"{len(data)} symbols, {sum(len(d) for d in data.values()):,} bars, {span}\n")

    base = Params(commission_per_share=a.commission, slippage_per_share=a.slippage, initial_account=a.account,
                  consol_mult=a.consol_mult, impulse_mult=a.impulse_mult)
    if a.sweep:
        print("EXPLORATORY sweep (stops on the losing side). Loosening thresholds departs from the spec and is fitted to "
              "this sample: treat as a map of where trades exist, not as a result.\n")
        print(f"{'consol':>7}{'mode':>8}{'impulse':>8}{'trades':>7}{'win%':>7}{'PF':>7}{'net P&L':>11}{'maxDD%':>8}{'avg R':>7}{'avg planned R:R':>17}")
        for cm in (0.5, 0.75, 1.0, 1.5, 2.0):
            for mode in ("window", "candle"):
                prm = replace(base, consol_mult=cm, consol_mode=mode, sl_mode="protective")
                s, _, _, _ = line("", run_all(data, prm), prm)
                f = lambda x, fmt: (fmt % x) if x is not None else "n/a"
                print(f"{cm:>7}{mode:>8}{prm.impulse_mult:>8}{s['trades']:>7}{f(s['win_rate_pct'], '%.1f'):>7}{f(s['profit_factor'], '%.2f'):>7}"
                      f"{s['net_pnl']:>11,.0f}{s['max_drawdown_pct']:>8.2f}{f(s.get('expectancy_r'), '%.2f'):>7}{f(s.get('avg_planned_rr'), '%.2f'):>17}")
        return 0
    if a.compare:
        print(f"{'variant':34}{'trades':>7}{'win%':>7}{'PF':>7}{'net P&L':>11}{'maxDD%':>8}{'avg R':>7}   rejected (top reasons)")
        for name, kw in VARIANTS.items():
            prm = replace(base, **kw)
            s, counts, _, _ = line(name, run_all(data, prm), prm)
            top = ", ".join(f"{k}:{v}" for k, v in list(counts.items())[:3])
            f = lambda x, fmt: (fmt % x) if x is not None else "n/a"
            print(f"{name:34}{s['trades']:>7}{f(s['win_rate_pct'], '%.1f'):>7}{f(s['profit_factor'], '%.2f'):>7}"
                  f"{s['net_pnl']:>11,.0f}{s['max_drawdown_pct']:>8.2f}{f(s.get('expectancy_r'), '%.2f'):>7}   {top}")
        return 0

    prm = replace(base, consol_mode=a.consol_mode, sl_mode=a.sl_mode, require_impulse_direction=a.with_impulse)
    results = run_all(data, prm)
    s, counts, trades, eq = line("run", results, prm)
    summary = {"params": prm.__dict__, "period": span, "symbols": list(data), "pooled": s, "rejected_by_reason": counts,
               "per_symbol": {r.symbol: summarize(r.trades, r.equity, prm.initial_account) for r in results}}
    paths = write_outputs(a.out, results, trades, eq, summary)

    print(f"Rules: consolidation={prm.consol_mode}, stops={prm.sl_mode}, with-impulse-only={prm.require_impulse_direction}")
    for k in ("trades", "wins", "losses", "win_rate_pct", "profit_factor", "net_pnl", "total_return_pct", "max_drawdown",
              "max_drawdown_pct", "expectancy", "expectancy_r", "avg_planned_rr", "longest_losing_streak"):
        if k in s:
            print(f"  {k:24} {s[k]}")
    if s.get("exit_reasons"):
        print(f"  exit reasons             {s['exit_reasons']}")
    print(f"  rejected setups          {counts}")
    print("\nFiles:", *paths.values(), sep="\n  ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
