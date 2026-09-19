"""
Garvit's 5-concept order-flow system: backtest on Upstox 1-minute NSE data
==========================================================================
    python run_orderflow_backtest.py                                   # 8 liquid NSE stocks, Mar-Sep 2026
    python run_orderflow_backtest.py --symbols RELIANCE,SBIN --start 2026-06-01 --end 2026-09-18
    python run_orderflow_backtest.py --gex gex.csv --gex-eps 50        # add Concept 5 (CSV: date,net_gex)

Runs the strict system, each concept dropped in turn, single/paired concepts, and a random-entry control,
all with real NSE intraday costs. Read orderflow_bt/features.py first: order flow here is a PROXY built from
candles (Upstox has no tick, bid/ask or buy/sell volume), and GEX is skipped unless you supply a file.

Outputs (in --out): trade_log.csv (every trade, every variant), summary.json, equity_curve.csv/.png, setup_*.png
"""
import argparse
import json
import os
import sys
from datetime import date

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

from orderflow_bt.features import FeatureConfig, build_features
from orderflow_bt.plotting import plot_day
from orderflow_bt.runner import VARIANTS, run_all, run_control
from orderflow_bt.upstox_data import load_or_fetch
from trading.universe import NSE_UNIVERSE

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_SYMBOLS = "RELIANCE,HDFCBANK,ICICIBANK,INFY,TCS,SBIN,ITC,AXISBANK"


def fmt(v, spec):
    return "n/a" if v is None else format(v, spec)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    ap.add_argument("--start", default="2026-03-01")
    ap.add_argument("--end", default="2026-09-18")
    ap.add_argument("--cash", type=float, default=150_000.0, help="per-symbol account (Atlas capital)")
    ap.add_argument("--refresh", action="store_true", help="re-download from Upstox instead of using the cache")
    ap.add_argument("--gex", help="CSV with columns date,net_gex (Concept 5). Without it GEX is not evaluated.")
    ap.add_argument("--gex-eps", type=float, default=0.0, help="|net GEX| <= this is 'near zero': skip")
    ap.add_argument("--pos-gamma", choices=("skip", "half"), default="skip")
    ap.add_argument("--plots", type=int, default=2, help="number of trade charts to draw")
    ap.add_argument("--out", default=os.path.join(ROOT, "backtest_output", "orderflow"))
    a = ap.parse_args()

    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    cache = os.path.join(ROOT, "nse_1min")
    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    gex = None
    extra = {}
    if a.gex:
        g = pd.read_csv(a.gex)
        gex = pd.Series(g["net_gex"].to_numpy(dtype=float), index=pd.to_datetime(g["date"]))
        extra = dict(gex_mode="require", gex_eps=a.gex_eps, pos_gamma=a.pos_gamma)

    feats = {}
    for s in syms:
        raw = load_or_fetch(s, NSE_UNIVERSE[s], start, end, cache, a.refresh)
        feats[s] = build_features(raw, FeatureConfig(), gex)
    bars = sum(len(f) for f in feats.values())
    days = max(f.index.normalize().nunique() for f in feats.values())
    print(f"{len(feats)} stocks, {bars:,} one-minute bars, {days} sessions, {a.start} to {a.end}")
    print(f"Concept 5 (GEX): {'evaluated from ' + a.gex if gex is not None else 'NOT evaluated (no GEX data supplied)'}\n")

    res = run_all(feats, a.cash, extra)
    ctl = run_control(feats, a.cash, extra)

    hdr = f"{'variant':32}{'trades':>7}{'wins':>6}{'win%':>7}{'PF':>7}{'net Rs':>10}{'gross Rs':>10}{'fees Rs':>9}{'avg R':>7}{'maxDD%':>8}{'1st half':>10}{'2nd half':>10}"
    print(hdr)
    for name, r in res.items():
        s = r["summary"]
        if s["trades"] == 0:
            print(f"{name:32}{0:>7}   (no setups)   rejected: {r['rejects'] or '-'}")
            continue
        fh, sh = s["first_half"], s["second_half"]
        print(f"{name:32}{s['trades']:>7}{s['wins']:>6}{fmt(s['win_rate_pct'], '.1f'):>7}{fmt(s['profit_factor'], '.2f'):>7}"
              f"{s['net_pnl']:>10,.0f}{s['gross_pnl']:>10,.0f}{s['fees_total']:>9,.0f}{fmt(s['expectancy_r'], '.2f'):>7}"
              f"{s['max_drawdown_pct']:>8.1f}{fh['net_pnl']:>10,.0f}{sh['net_pnl']:>10,.0f}")
    print("\nHow much to believe each row (95% interval on win rate; t-stat of GROSS per-trade P&L, |t| < 2 is noise):")
    for name, r in res.items():
        s_ = r["summary"]
        if s_["trades"]:
            print(f"  {name:32} win rate {s_['win_rate_pct']:>5.1f}%  95% CI {s_['win_rate_ci95'][0]:>5.1f}-{s_['win_rate_ci95'][1]:<5.1f}  gross t = {fmt(s_.get('gross_t'), '.2f')}")
    live = [c for c in ctl if c["trades"]]
    if live:
        pf = [c["profit_factor"] for c in live if c["profit_factor"] is not None]
        print(f"{'R  random entries (5 seeds)':32}{np.mean([c['trades'] for c in live]):>7.0f}{np.mean([c['wins'] for c in live]):>6.0f}"
              f"{np.mean([c['win_rate_pct'] for c in live]):>7.1f}{np.mean(pf):>7.2f}{np.mean([c['net_pnl'] for c in live]):>10,.0f}"
              f"{np.mean([c['gross_pnl'] for c in live]):>10,.0f}{np.mean([c['fees_total'] for c in live]):>9,.0f}"
              f"{np.mean([c['expectancy_r'] for c in live]):>7.2f}{np.mean([c['max_drawdown_pct'] for c in live]):>8.1f}")
        print(f"{'   control range (min..max net Rs)':32} {min(c['net_pnl'] for c in live):,.0f} .. {max(c['net_pnl'] for c in live):,.0f}")

    # Primary variant for the equity curve and charts: most trades among the four-concept family (A-E). Picked by
    # trade count, never by performance.
    family = {k: v for k, v in res.items() if k[0] in "ABCDE" and v["summary"]["trades"] > 0}
    primary = max(family, key=lambda k: family[k]["summary"]["trades"]) if family else None

    os.makedirs(a.out, exist_ok=True)
    logs = []
    for name, r in res.items():
        if len(r["trades"]):
            logs.append(r["trades"].assign(variant=name))
    if logs:
        pd.concat(logs, ignore_index=True).drop(columns=["tag"]).to_csv(os.path.join(a.out, "trade_log.csv"), index=False)
    summary = {"period": f"{a.start}..{a.end}", "symbols": syms, "bars": bars, "sessions": days, "gex": bool(a.gex),
               "primary_variant": primary, "variants": {k: {"summary": v["summary"], "rejects": v["rejects"]} for k, v in res.items()},
               "random_control": ctl}
    with open(os.path.join(a.out, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, default=str)

    if primary:
        eq = res[primary]["equity"]
        eq.rename_axis("time").to_csv(os.path.join(a.out, "equity_curve.csv"))
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(2, 1, figsize=(11, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
        ax[0].plot(eq.index, eq.values, lw=1.3); ax[0].axhline(a.cash * len(syms), color="grey", lw=0.6, ls="--")
        ax[0].set_title(f"Equity curve, closed trades: {primary.strip()} ({len(syms)} pooled fixed-size Rs{a.cash:,.0f} accounts)")
        dd = (eq.cummax() - eq) / eq.cummax() * 100
        ax[1].fill_between(dd.index, -dd.values, 0, color="tab:red", alpha=0.4); ax[1].set_ylabel("Drawdown %")
        fig.tight_layout(); fig.savefig(os.path.join(a.out, "equity_curve.png"), dpi=110); plt.close(fig)

    drawn = 0
    if primary and a.plots:
        tr = res[primary]["trades"]
        for _, t in tr.head(a.plots).iterrows():                       # the first trades chronologically: not cherry-picked
            day = pd.Timestamp(t["entry_time"]).normalize()
            sub = tr[(tr["symbol"] == t["symbol"]) & (pd.to_datetime(tr["entry_time"]).dt.normalize() == day)]
            if plot_day(feats[t["symbol"]], day, os.path.join(a.out, f"setup_{t['symbol']}_{day.date()}.png"), t["symbol"], sub):
                drawn += 1
    if drawn == 0:                                                       # strict system found nothing: show a day where concepts overlap
        for s, f in feats.items():
            hit = f[(f["hvn_long"] + f["hvn_short"] > 0) & ((f["abs_long"] + f["abs_short"]) > 0) & (f["minute"] < 120)]
            if len(hit) and plot_day(f, hit.index[0].normalize(), os.path.join(a.out, f"example_{s}_{hit.index[0].date()}.png"), s):
                drawn += 1
                break

    print(f"\nprimary variant for chart/equity (most trades in A-E): {primary or 'none: no four-concept variant produced a trade'}")
    print(f"files in {a.out}: trade_log.csv, summary.json{', equity_curve.csv/.png' if primary else ''}, {drawn} chart(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
