"""
Per-asset entry windows. New entries are only allowed inside the window; open positions are
always managed and squared off regardless.

Why: paper trades were opened in the first seconds of a session (EURINR/USDINR at 09:00:10) on
stale prior-session data, and late entries had no time to work before the forced square-off
but still paid full round-trip charges.
"""
from datetime import time as dt_time

ENTRY_WINDOWS = {
    # 10:00 skips the opening volatility trap; 15:00 leaves 15 min before the 15:15 square-off.
    "EQUITY": (dt_time(10, 0), dt_time(15, 0)),
    # First 10 min of FX/MCX are stale-data prints; stop entering ~45 min / 30 min before square-off.
    "CURRENCY": (dt_time(9, 10), dt_time(16, 0)),
    "COMMODITY": (dt_time(9, 10), dt_time(22, 45)),
}


def entries_allowed(asset_type: str, now_time: dt_time) -> bool:
    """True if a new `asset_type` entry may be opened at `now_time` (IST wall-clock time)."""
    window = ENTRY_WINDOWS.get(asset_type)
    if window is None:
        return True
    start, end = window
    return start <= now_time < end
