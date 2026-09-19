"""Performance statistics. All P&L is net of the fees configured in Params."""
from typing import Dict, Optional

import numpy as np
import pandas as pd


def max_drawdown(equity: pd.Series):
    """(max drawdown in currency, as a fraction of the running peak) from an equity curve."""
    eq = equity.dropna().to_numpy(dtype=float)
    if len(eq) == 0:
        return 0.0, 0.0
    peak = np.maximum.accumulate(eq)
    dd = peak - eq
    return float(dd.max()), float((dd / peak).max())


def summarize(trades: pd.DataFrame, equity: pd.Series, initial: float) -> Dict:
    out: Dict = {"trades": 0, "initial_account": initial}
    dd, dd_pct = max_drawdown(equity)
    out.update({"max_drawdown": round(dd, 2), "max_drawdown_pct": round(dd_pct * 100, 2)})
    if trades is None or len(trades) == 0:
        out.update({"win_rate_pct": None, "profit_factor": None, "net_pnl": 0.0, "total_return_pct": 0.0,
                    "expectancy": None, "expectancy_r": None})
        return out
    pnl = trades["pnl"].to_numpy(dtype=float)
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    gl = -losses.sum()
    streak = best = 0
    for x in pnl:
        streak = streak + 1 if x <= 0 else 0
        best = max(best, streak)
    out.update({
        "trades": int(len(pnl)), "wins": int(len(wins)), "losses": int(len(losses)),
        "win_rate_pct": round(100.0 * len(wins) / len(pnl), 2),
        "profit_factor": round(float(wins.sum() / gl), 3) if gl > 0 else (None if wins.sum() == 0 else float("inf")),
        "net_pnl": round(float(pnl.sum()), 2), "total_return_pct": round(100.0 * pnl.sum() / initial, 2),
        "avg_win": round(float(wins.mean()), 2) if len(wins) else 0.0,
        "avg_loss": round(float(losses.mean()), 2) if len(losses) else 0.0,
        "expectancy": round(float(pnl.mean()), 2),
        "expectancy_r": round(float(trades["r_multiple"].mean()), 3),
        "avg_planned_rr": round(float(trades["planned_rr"].mean()), 3),
        "avg_bars_held": round(float(trades["bars_held"].mean()), 1),
        "longest_losing_streak": best,
        "long_trades": int((trades["direction"] == "LONG").sum()),
        "short_trades": int((trades["direction"] == "SHORT").sum()),
        "exit_reasons": trades["exit_reason"].value_counts().to_dict(),
    })
    return out


def pool(results, initial: Optional[float] = None):
    """Pools independent per-symbol accounts (each sized on its own equity): trades sorted by exit time and a
    realised equity curve = initial + cumulative P&L. Only a fixed-size aggregate, NOT one shared account."""
    frames = [r.trades for r in results if len(r.trades)]
    initial = initial if initial is not None else results[0].params.initial_account
    if not frames:
        return pd.DataFrame(), pd.Series(dtype=float)
    t = pd.concat(frames, ignore_index=True).sort_values("exit_time").reset_index(drop=True)
    eq = pd.Series(initial + t["pnl"].cumsum().to_numpy(), index=pd.DatetimeIndex(t["exit_time"]), name="equity")
    return t, pd.concat([pd.Series([initial], index=[t["exit_time"].iloc[0]]), eq])
