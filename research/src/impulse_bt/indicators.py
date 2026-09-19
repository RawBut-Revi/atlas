"""Indicators implemented directly in numpy so the backtest has no numba/pandas-ta dependency."""
import numpy as np


def true_range(high, low, close) -> np.ndarray:
    h, l, c = (np.asarray(x, dtype=float) for x in (high, low, close))
    tr = h - l
    if len(h) > 1:
        pc = c[:-1]
        tr[1:] = np.maximum.reduce([h[1:] - l[1:], np.abs(h[1:] - pc), np.abs(l[1:] - pc)])
    return tr


def atr_wilder(high, low, close, n: int = 14) -> np.ndarray:
    """Wilder ATR: seeded with the simple mean of the first n true ranges, then
    ATR[t] = (ATR[t-1] * (n-1) + TR[t]) / n. NaN until n bars exist. atr[t] uses bars up to and including t."""
    tr = true_range(high, low, close)
    out = np.full(len(tr), np.nan)
    if len(tr) < n:
        return out
    out[n - 1] = tr[:n].mean()
    for t in range(n, len(tr)):
        out[t] = (out[t - 1] * (n - 1) + tr[t]) / n
    return out
