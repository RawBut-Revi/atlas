"""Chart of one session: price + prior-day volume profile with HVN zones + absorption + delta divergence + trades."""
import numpy as np
import pandas as pd

from orderflow_bt.features import FeatureConfig, session_profile
from impulse_bt.volume_profile import volume_profile


def plot_day(feat: pd.DataFrame, day, out_path: str, symbol: str = "", trades: pd.DataFrame = None,
             cfg: FeatureConfig = FeatureConfig(), window: int = 5) -> bool:
    """Writes a PNG. Returns False if the day has no prior session to profile."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    day = pd.Timestamp(day).normalize()
    dates = feat.index.normalize()
    today = feat[dates == day]
    prior_days = sorted(set(dates[dates < day]))
    if today.empty or not prior_days:
        return False
    prior = feat[dates == prior_days[-1]]
    prof = session_profile(prior["High"].to_numpy(), prior["Low"].to_numpy(), prior["Volume"].to_numpy(), cfg)
    raw = volume_profile(prior["High"].to_numpy(), prior["Low"].to_numpy(), prior["Volume"].to_numpy(), n_bins=cfg.n_bins)
    if prof is None or raw is None:
        return False

    x = np.arange(len(today))
    fig = plt.figure(figsize=(14, 9))
    gs = fig.add_gridspec(3, 2, width_ratios=[6, 1], height_ratios=[4, 1.6, 1.2], hspace=0.08, wspace=0.03)
    ax = fig.add_subplot(gs[0, 0]); axp = fig.add_subplot(gs[0, 1], sharey=ax)
    axd = fig.add_subplot(gs[1, 0], sharex=ax); axb = fig.add_subplot(gs[2, 0], sharex=ax)

    up = today["Close"].to_numpy() >= today["Open"].to_numpy()
    ax.vlines(x, today["Low"], today["High"], color=np.where(up, "#2a9d8f", "#d1495b"), lw=0.8)
    ax.vlines(x, today["Open"], today["Close"], color=np.where(up, "#2a9d8f", "#d1495b"), lw=2.4)
    for lo, hi, peak in prof["zones"]:                                   # Concept 1: HVN zones
        ax.axhspan(lo, hi, color="#f4a261", alpha=0.25)
        ax.axhline(peak, color="#e76f51", lw=0.8, ls=":")
    ax.axhline(prof["poc"], color="#264653", lw=1.2, label="prior POC")
    ax.axhspan(prof["va_lo"], prof["va_hi"], color="#8ecae6", alpha=0.18, label="prior value area (POC ± 1σ)")   # Concept 4
    ax.axvline(120, color="grey", lw=0.8, ls="--")                       # end of the 2-hour trading window
    for side, colr, mk in (("abs_long", "#2a9d8f", "^"), ("abs_short", "#d1495b", "v")):   # Concept 2 (proxy)
        for i in np.flatnonzero(today[side].to_numpy() == 1):
            lvl = today["abs_lvl_long" if side == "abs_long" else "abs_lvl_short"].iloc[i]
            ax.hlines(lvl, max(0, i - window + 1), i, color=colr, lw=3, alpha=0.8)
            ax.plot(i, lvl, mk, color=colr, ms=9, mec="k")
    if trades is not None and len(trades):
        for _, tr in trades.iterrows():
            xi = today.index.get_indexer([pd.Timestamp(tr["entry_time"])], method="nearest")[0]
            xo = today.index.get_indexer([pd.Timestamp(tr["exit_time"])], method="nearest")[0]
            ax.plot(xi, tr["entry"], "^" if tr["direction"] == "LONG" else "v", color="k", ms=10)
            ax.plot(xo, tr["exit"], "X", color="#6a4c93", ms=9)
            ax.hlines([tr["stop_loss"], tr["take_profit"]], xi, xo, colors=["#d1495b", "#2a9d8f"], lw=1, ls="--")
    lab = today.index[::30].strftime("%H:%M")
    ax.set_xticks(x[::30]); ax.set_xticklabels([]); ax.set_xlim(-2, len(today) + 2)
    ax.set_title(f"{symbol} {day.date()}  |  open {today['Open'].iloc[0]:.2f} "
                 f"({'IMBALANCE UP' if today['imb'].iloc[0] == 1 else 'IMBALANCE DOWN' if today['imb'].iloc[0] == -1 else 'BALANCE'})"
                 "  |  orange = prior-day HVN, blue = prior value area, ▲▼ = absorption (proxy)")
    ax.legend(loc="upper right", fontsize=8)
    centres = (raw["edges"][:-1] + raw["edges"][1:]) / 2
    axp.barh(centres, raw["volumes"], height=(raw["edges"][1] - raw["edges"][0]) * 0.9, color="#adb5bd")
    hvn_mask = np.zeros(len(centres), bool)
    for lo, hi, _ in prof["zones"]:
        hvn_mask |= (centres >= lo) & (centres <= hi)
    axp.barh(centres[hvn_mask], raw["volumes"][hvn_mask], height=(raw["edges"][1] - raw["edges"][0]) * 0.9, color="#e76f51")
    axp.set_title("prior-day\nvolume profile", fontsize=8); axp.tick_params(labelleft=False, labelbottom=False)

    axd.plot(x, today["cumdelta"], color="#264653", lw=1.2)                # Concept 3 (proxy delta)
    axd.fill_between(x, today["cumdelta"].min(), today["cumdelta"].max(),
                     where=today["div_bull"].to_numpy() == 1, color="#2a9d8f", alpha=0.25, label="bullish divergence")
    axd.fill_between(x, today["cumdelta"].min(), today["cumdelta"].max(),
                     where=today["div_bear"].to_numpy() == 1, color="#d1495b", alpha=0.25, label="bearish divergence")
    axd.set_ylabel("cum. delta\n(proxy)", fontsize=8); axd.legend(loc="upper left", fontsize=7); axd.tick_params(labelbottom=False)
    axb.bar(x, today["delta"], color=np.where(today["delta"] >= 0, "#2a9d8f", "#d1495b"), width=1.0)
    axb.set_ylabel("bar delta", fontsize=8); axb.set_xticks(x[::30]); axb.set_xticklabels(lab, fontsize=8)
    fig.savefig(out_path, dpi=105, bbox_inches="tight")
    plt.close(fig)
    return True
