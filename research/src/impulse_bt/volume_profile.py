"""
Volume profile of a set of candles.

Each candle's volume is spread uniformly over the price bins it overlaps (a candle with no range
puts all its volume in one bin). Definitions:
  POC = centre of the highest-volume bin (ties -> the bin closest to the middle of the range).
  Value area = the contiguous bins around the POC holding va_pct (default 70%) of the volume,
               grown one bin at a time toward the side whose next bin has more volume (ties -> up).
  VAH / VAL  = top edge / bottom edge of that value area.
"""
from typing import Dict, Optional

import numpy as np


def volume_profile(high, low, volume, n_bins: int = 24, va_pct: float = 0.70) -> Optional[Dict]:
    h, l, v = (np.asarray(x, dtype=float) for x in (high, low, volume))
    lo, hi, total = float(l.min()), float(h.max()), float(v.sum())
    if total <= 0:
        return None
    if hi - lo <= 1e-12:                                   # one price level
        return {"poc": lo, "vah": lo, "val": lo, "edges": np.array([lo, hi]), "volumes": np.array([total]), "total": total}

    edges = np.linspace(lo, hi, n_bins + 1)
    step = (hi - lo) / n_bins
    vols = np.zeros(n_bins)
    for hh, ll, vv in zip(h, l, v):
        if hh - ll <= 1e-12:
            vols[min(int((ll - lo) / step), n_bins - 1)] += vv
        else:
            overlap = np.clip(np.minimum(edges[1:], hh) - np.maximum(edges[:-1], ll), 0.0, None)
            vols += vv * overlap / (hh - ll)

    centres = (edges[:-1] + edges[1:]) / 2.0
    top = np.flatnonzero(vols >= vols.max() - 1e-12)
    poc_i = int(top[np.argmin(np.abs(centres[top] - (lo + hi) / 2.0))])

    a = b = poc_i
    acc, target = vols[poc_i], va_pct * total
    while acc < target - 1e-12 and (a > 0 or b < n_bins - 1):
        up = vols[b + 1] if b < n_bins - 1 else -1.0
        dn = vols[a - 1] if a > 0 else -1.0
        if up >= dn:
            b += 1
            acc += vols[b]
        else:
            a -= 1
            acc += vols[a]
    return {"poc": float(centres[poc_i]), "vah": float(edges[b + 1]), "val": float(edges[a]),
            "edges": edges, "volumes": vols, "total": total}
