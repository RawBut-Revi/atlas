"""
Paper-fill realism: adverse slippage on market fills and gap-through stop fills.

Before this module every paper exit filled exactly at the stop/target level, so paper P&L
was an upper bound. Resting targets are limit orders (no slippage); stops and market
square-offs fill at the worse of the level and the live price, plus slippage.
"""

# Adverse slippage per market fill, in basis points of price.
SLIPPAGE_BPS = {"EQUITY": 5.0, "CURRENCY": 1.5, "COMMODITY": 5.0}


def price_decimals(asset_type: str) -> int:
    """Quote precision. JPYINR trades near 0.61, so 2 decimals throws away most of the price."""
    return 4 if asset_type == "CURRENCY" else 2


def _slip(price: float, asset_type: str) -> float:
    return price * SLIPPAGE_BPS.get(asset_type, 0.0) / 10000.0


def entry_fill(direction: str, price: float, asset_type: str) -> float:
    """A BUY entry fills above the signal price, a SELL entry below it."""
    s = _slip(price, asset_type)
    fill = price + s if direction == "BUY" else price - s
    return round(fill, price_decimals(asset_type))


def signal_price(direction: str, fill: float, asset_type: str) -> float:
    """Undo `entry_fill`: the pre-slippage price behind an entry fill (used to build inverse twins)."""
    bps = SLIPPAGE_BPS.get(asset_type, 0.0) / 10000.0
    return fill / (1.0 + bps) if direction == "BUY" else fill / (1.0 - bps)


# Equity signals are built from daily candles, so signal entry = a stale close. If the live price
# has drifted further than this, the setup is gone and the signal is skipped, not chased.
MAX_ENTRY_DRIFT_PCT = 1.5


def rebase_levels(direction: str, signal_entry: float, live_price: float, levels: dict, asset_type: str):
    """
    Fill an entry at the live price instead of the signal's stale price, and shift every level
    (stop, targets) by the same amount so risk/reward distances are preserved.

    `levels` maps names (stop_loss, target_price, target_1, target_2, ...) to prices.
    Returns {"entry_price": fill, **shifted_levels}, or None if live price has drifted more than
    MAX_ENTRY_DRIFT_PCT from the signal. With no live price (<= 0) the signal price is used as-is.
    """
    decimals = price_decimals(asset_type)
    ref = live_price if live_price and live_price > 0 else signal_entry
    if abs(ref - signal_entry) / signal_entry * 100.0 > MAX_ENTRY_DRIFT_PCT:
        return None

    fill = entry_fill(direction, ref, asset_type)
    shift = fill - signal_entry
    out = {"entry_price": fill}
    for name, price in levels.items():
        out[name] = round(price + shift, decimals)
    return out


def exit_fill(direction: str, level: float, cur_price: float, asset_type: str, kind: str) -> float:
    """
    Fill price when closing a `direction` position.

    kind: "TARGET" -> resting limit order, fills at `level`.
          "STOP"   -> fills at the worse of `level` and `cur_price` (gap-through), plus slippage.
          "MARKET" -> fills at `cur_price` plus slippage (square-offs).
    Closing a BUY is a sell (fills lower); closing a SELL is a buy (fills higher).
    """
    decimals = price_decimals(asset_type)
    if kind == "TARGET":
        return round(level, decimals)

    base = cur_price
    if kind == "STOP":
        base = min(level, cur_price) if direction == "BUY" else max(level, cur_price)

    s = _slip(base, asset_type)
    fill = base - s if direction == "BUY" else base + s
    return round(fill, decimals)
