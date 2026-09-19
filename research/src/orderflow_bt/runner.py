"""
Runs the strategy variants on every symbol and pools the results.

The variant list is fixed BEFORE looking at any results: the strict five-concept system, each concept
dropped in turn (leave-one-out), a few single/paired concepts, and a random-entry control that uses the
same window, exits, sizing and costs. Choosing the best-looking subset afterwards would be data dredging;
the control shows how much of any result random entries would also produce.
"""
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from backtesting import Backtest

from impulse_bt.metrics import summarize
from orderflow_bt.costs import nse_intraday_commission
from orderflow_bt.strategy import OrderFlowStrategy

LEVERAGE = 5.0
CONTROL_SEEDS = (0, 1, 2, 3, 4)
CONTROL_P = 0.02                                   # per-bar entry probability inside the window (max 2 trades a day)

ALL = dict(req_hvn=True, req_absorb=True, req_delta=True, req_imb=True)
VARIANTS: Dict[str, dict] = {
    "A  all four concepts (strict)":   dict(ALL),
    "B  drop HVN":                     {**ALL, "req_hvn": False},
    "C  drop absorption":              {**ALL, "req_absorb": False},
    "D  drop delta":                   {**ALL, "req_delta": False},
    "E  drop imbalance":               {**ALL, "req_imb": False},
    "F  HVN only":                     dict(req_hvn=True, req_absorb=False, req_delta=False, req_imb=False),
    "G  absorption only":              dict(req_hvn=False, req_absorb=True, req_delta=False, req_imb=False),
    "H  delta divergence only":        dict(req_hvn=False, req_absorb=False, req_delta=True, req_imb=False),
    "I  imbalance + HVN":              dict(req_hvn=True, req_absorb=False, req_delta=False, req_imb=True),
}


def enrich(trades: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """backtesting.py trade table -> journal with direction, planned R:R, R multiple and exit reason."""
    if trades is None or len(trades) == 0:
        return pd.DataFrame()
    t = pd.DataFrame({
        "symbol": symbol,
        "direction": np.where(trades["Size"] > 0, "LONG", "SHORT"),
        "entry_time": trades["EntryTime"], "exit_time": trades["ExitTime"],
        "entry": trades["EntryPrice"].round(4), "exit": trades["ExitPrice"].round(4), "size": trades["Size"].abs(),
        "stop_loss": trades["SL"], "take_profit": trades["TP"], "pnl": trades["PnL"].round(2),
        "bars_held": trades["ExitBar"] - trades["EntryBar"], "tag": trades["Tag"].astype(str)})
    t["fees"] = trades["Commission"].round(2) if "Commission" in trades.columns else np.nan
    t["planned_rr"] = t["tag"].str.extract(r"rr=([0-9.]+)")[0].astype(float)
    planned_risk = t["tag"].str.extract(r"risk=([0-9.]+)")[0].astype(float)          # per-share risk at signal time
    t["r_multiple"] = (t["pnl"] / (planned_risk * t["size"])).replace([np.inf, -np.inf], np.nan).round(3)
    t["exit_reason"] = np.select([(t["exit"] - t["stop_loss"]).abs() < 1e-6, (t["exit"] - t["take_profit"]).abs() < 1e-6],
                                 ["stop", "target"], "eod_or_other")
    t["exit_reason"] = t["exit_reason"].where(t["exit_reason"] != "stop", "stop_loss")
    return t


def run_symbol(feat: pd.DataFrame, variant: dict, symbol: str, cash: float = 150_000.0, **extra):
    """One backtesting.py run. Returns (stats, journal, rejects)."""
    bt = Backtest(feat, OrderFlowStrategy, cash=cash, commission=nse_intraday_commission, margin=1.0 / LEVERAGE,
                  exclusive_orders=True, trade_on_close=False, finalize_trades=True)
    stats = bt.run(**variant, **extra)
    return stats, enrich(stats["_trades"], symbol), dict(stats._strategy.rejects)


def pool(journals: List[pd.DataFrame], cash: float):
    """Independent per-symbol accounts pooled: trades by exit time, equity = cash + cumulative P&L."""
    frames = [j for j in journals if len(j)]
    if not frames:
        return pd.DataFrame(), pd.Series(dtype=float)
    t = pd.concat(frames, ignore_index=True).sort_values("exit_time").reset_index(drop=True)
    eq = pd.Series(cash + t["pnl"].cumsum().to_numpy(), index=pd.DatetimeIndex(t["exit_time"]), name="equity")
    return t, pd.concat([pd.Series([cash], index=[t["exit_time"].iloc[0]]), eq])


def wilson_ci(wins: int, n: int, z: float = 1.96):
    """95% Wilson interval for a win rate. With few trades it is very wide: 4 wins in 5 could be a 40% or a 96% system."""
    if n == 0:
        return None
    p = wins / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return round(float(centre - half) * 100, 1), round(float(centre + half) * 100, 1)


def summarize_variant(journals, cash):
    """`cash` is the TOTAL capital across the pooled per-symbol accounts (drawdown is measured against it)."""
    trades, eq = pool(journals, cash)
    s = summarize(trades, eq, cash)
    if len(trades):
        g = (trades["pnl"] + trades["fees"]).to_numpy(dtype=float)
        s["gross_t"] = round(float(g.mean() / (g.std(ddof=1) / np.sqrt(len(g)))), 2) if len(g) >= 10 and g.std(ddof=1) > 0 else None
        s["win_rate_ci95"] = wilson_ci(int((trades["pnl"] > 0).sum()), len(trades))
        s["fees_total"] = round(float(trades["fees"].sum()), 2)
        s["gross_pnl"] = round(float(trades["pnl"].sum() + trades["fees"].sum()), 2)
        s["avg_fee_per_trade"] = round(float(trades["fees"].mean()), 2)
        mid = trades["exit_time"].sort_values().iloc[len(trades) // 2]
        for name, part in (("first_half", trades[trades["exit_time"] < mid]), ("second_half", trades[trades["exit_time"] >= mid])):
            s[name] = {"trades": int(len(part)), "net_pnl": round(float(part["pnl"].sum()), 2),
                       "win_rate_pct": round(float((part["pnl"] > 0).mean() * 100), 1) if len(part) else None}
    return s, trades, eq


def run_all(features: Dict[str, pd.DataFrame], cash: float = 150_000.0, extra: Optional[dict] = None, variants=None):
    """{variant: (summary, trades, equity, rejects)} plus the control statistics."""
    extra = extra or {}
    out = {}
    for name, kw in (variants or VARIANTS).items():
        journals, rej = [], {}
        for sym, f in features.items():
            _, j, r = run_symbol(f, kw, sym, cash, **extra)
            journals.append(j)
            for k, v in r.items():
                rej[k] = rej.get(k, 0) + v
        s, t, eq = summarize_variant(journals, cash * len(features))
        out[name] = {"summary": s, "trades": t, "equity": eq, "rejects": rej}
    return out


def run_control(features: Dict[str, pd.DataFrame], cash: float = 150_000.0, extra: Optional[dict] = None,
                seeds=CONTROL_SEEDS, p: float = CONTROL_P):
    """Random-entry control across several seeds: what unskilled entries earn with the same exits, sizing and costs."""
    extra = extra or {}
    runs = []
    for sd in seeds:
        journals = [run_symbol(f, dict(req_hvn=False, req_absorb=False, req_delta=False, req_imb=False), sym, cash,
                               random_p=p, seed=sd, **extra)[1] for sym, f in features.items()]
        s, _, _ = summarize_variant(journals, cash * len(features))
        runs.append(s)
    return runs
