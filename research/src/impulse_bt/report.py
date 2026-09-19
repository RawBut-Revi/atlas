"""Journal and chart output."""
import json
import os
from typing import List

import pandas as pd


def write_outputs(outdir: str, results: List, pooled_trades: pd.DataFrame, pooled_equity: pd.Series, summary: dict) -> dict:
    os.makedirs(outdir, exist_ok=True)
    paths = {}
    cols_first = ["symbol", "direction", "signal_time", "fill_time", "exit_time", "entry", "exit", "qty", "pnl",
                  "r_multiple", "exit_reason", "entry_reason"]
    if len(pooled_trades):
        rest = [c for c in pooled_trades.columns if c not in cols_first]
        pooled_trades[cols_first + rest].to_csv(os.path.join(outdir, "trade_journal.csv"), index=False)
        paths["journal"] = os.path.join(outdir, "trade_journal.csv")
    rej = [r.rejected for r in results if len(r.rejected)]
    if rej:
        pd.concat(rej, ignore_index=True).sort_values("time").to_csv(os.path.join(outdir, "rejected_setups.csv"), index=False)
        paths["rejected"] = os.path.join(outdir, "rejected_setups.csv")
    if len(pooled_equity):
        pooled_equity.rename_axis("time").to_csv(os.path.join(outdir, "equity_curve.csv"))
        paths["equity_csv"] = os.path.join(outdir, "equity_curve.csv")
    with open(os.path.join(outdir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, default=str)
    paths["summary"] = os.path.join(outdir, "summary.json")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        if len(pooled_equity) > 1:
            fig, ax = plt.subplots(2, 1, figsize=(11, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
            eq = pooled_equity
            ax[0].plot(eq.index, eq.values, lw=1.4)
            ax[0].axhline(eq.iloc[0], color="grey", lw=0.6, ls="--")
            ax[0].set_ylabel("Equity"); ax[0].set_title("Equity curve (closed trades, pooled fixed-size accounts)")
            dd = (eq.cummax() - eq) / eq.cummax() * 100
            ax[1].fill_between(dd.index, -dd.values, 0, color="tab:red", alpha=0.4)
            ax[1].set_ylabel("Drawdown %")
            fig.tight_layout()
            fig.savefig(os.path.join(outdir, "equity_curve.png"), dpi=110)
            plt.close(fig)
            paths["equity_png"] = os.path.join(outdir, "equity_curve.png")
    except ImportError:
        pass
    return paths
