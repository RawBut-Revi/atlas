"""
NSE equity INTRADAY (MIS) costs as a backtesting.py commission callable.

backtesting.py calls commission(size, price) once when a trade opens and once when it closes, and
passes the TRADE's signed size both times (not each leg's own side), so the callable cannot know
which leg is the sell (STT applies to the sell only). Each call therefore returns HALF of the exact
round-trip charge at that price: the two calls sum to the round trip. Rates match trading/charges.py:
brokerage min(Rs20, 0.05% of value) per leg, STT 0.025% on the sell, exchange 0.00345%, stamp 0.003% on
the buy, SEBI Rs10/crore, GST 18% on brokerage + exchange + SEBI.
"""


def round_trip_charges(price: float, qty: float, flat_fee: float = 20.0) -> float:
    value = abs(qty) * price
    brokerage = min(flat_fee, value * 0.0005) * 2          # one buy leg + one sell leg
    stt = value * 0.00025                                  # sell side only
    exchange = value * 0.0000345 * 2
    stamp = value * 0.00003                                # buy side only
    sebi = value * 0.000001 * 2
    gst = (brokerage + exchange + sebi) * 0.18
    return brokerage + stt + exchange + stamp + sebi + gst


def nse_intraday_commission(size: float, price: float) -> float:
    return 0.5 * round_trip_charges(price, size)
