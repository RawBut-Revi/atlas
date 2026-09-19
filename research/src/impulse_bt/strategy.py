"""
Impulse + Consolidation + Breakout: rule engine and trade simulator (15-minute bars).

One pass over the bars, one state machine, no look-ahead: every decision on bar i uses bars <= i.

  SCAN     an impulse candle: (high-low) > impulse_mult x ATR(14) measured on the PRIOR bar
  CONSOL   5-10 tight candles after it (see Params.consol_mode), inside one session
           volume profile of those candles -> POC / VAH / VAL
  signal   first candle after >= min_consol candles that CLOSES above VAH (long) or below VAL (short)
  PENDING  limit order at the breakout candle's low (long) / high (short); day order, expires
  POSITION 1% risk sizing, stop and target exits, max one position at a time

Everything the spec leaves open is a Params field with the default documented on the field.
Setups that fail any rule are not traded and are logged in `rejected` with the reason.
"""
import math
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from impulse_bt.indicators import atr_wilder
from impulse_bt.volume_profile import volume_profile


@dataclass(frozen=True)
class Params:
    # -- rules stated in the spec --
    atr_len: int = 14
    impulse_mult: float = 2.0            # impulse: range > 2 x ATR
    consol_mult: float = 0.5             # consolidation: range < 0.5 x ATR
    min_consol: int = 5
    max_consol: int = 10
    va_pct: float = 0.70
    risk_pct: float = 0.01               # 1% of account per trade
    block_open_min: int = 30             # no entries in the first 30 min of a session
    block_close_min: int = 30            # ... or the last 30 min
    max_positions: int = 1               # fixed by construction; kept for the record
    # -- spec is ambiguous: choices made here --
    consol_mode: str = "window"          # "window": (max high - min low) of the window < 0.5 ATR (literal reading)
                                         # "candle": every candle's own range < 0.5 ATR
    sl_mode: str = "literal"             # "literal": long SL = consolidation swing HIGH, short SL = swing LOW (as written)
                                         # "protective": long SL = swing LOW, short SL = swing HIGH
    require_impulse_direction: bool = False   # True: breakout must go the same way as the impulse candle
    n_bins: int = 24
    # -- spec is silent: assumptions --
    entry_expiry_bars: int = 4           # a limit order lives at most this many bars ...
    day_orders: bool = True              # ... and never past the session it was placed in
    max_hold_bars: int = 130             # time stop: 5 sessions of 26 bars
    flatten_at_close: bool = False       # True: close any position on the last bar of each session
    same_session: bool = True            # impulse + consolidation + breakout must sit in one session
    max_leverage: float = 1.0            # notional <= equity x this (the 1% formula alone can size >100% of equity)
    commission_per_share: float = 0.0
    slippage_per_share: float = 0.0      # applied against stop / time-stop exits; limit fills get none
    initial_account: float = 100_000.0


@dataclass
class Result:
    symbol: str
    params: Params
    trades: pd.DataFrame
    rejected: pd.DataFrame
    equity: pd.Series                    # mark-to-market equity at each bar's close
    n_bars: int
    n_sessions: int


def _session_arrays(times: pd.DatetimeIndex, p: Params):
    day = times.normalize()
    codes = pd.factorize(day)[0]
    minute = (times.hour * 60 + times.minute).to_numpy()
    bar_min = int(np.median(np.diff(times.values).astype("timedelta64[m]").astype(int))) if len(times) > 1 else 15
    df = pd.DataFrame({"code": codes, "minute": minute})
    first = df.groupby("code")["minute"].transform("min").to_numpy()
    last = df.groupby("code")["minute"].transform("max").to_numpy()
    blocked = (minute < first + p.block_open_min) | (minute >= last + bar_min - p.block_close_min)
    is_last = minute == last
    return codes, blocked, is_last


def run_backtest(df: pd.DataFrame, params: Params = Params(), symbol: str = "") -> Result:
    p = params
    o, h, l, c, v = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume"))
    times = df.index
    n = len(df)
    atr = atr_wilder(h, l, c, p.atr_len)
    day, blocked, is_last_bar = _session_arrays(times, p)

    trades: List[Dict] = []
    rejected: List[Dict] = []
    equity = p.initial_account
    eq_curve = np.full(n, np.nan)

    state = "SCAN"
    imp_idx, imp_dir = -1, ""
    order: Optional[Dict] = None
    pos: Optional[Dict] = None

    def reject(i, reason, **info):
        rejected.append({"time": times[i], "symbol": symbol, "reason": reason, **info})

    def is_impulse(i):
        a = atr[i - 1] if i > 0 else np.nan
        return (not math.isnan(a)) and (h[i] - l[i]) > p.impulse_mult * a and c[i] != o[i]

    def close_position(i, price, reason, is_stop):
        nonlocal equity, pos, state
        d = 1 if pos["direction"] == "LONG" else -1
        px = price - d * p.slippage_per_share if is_stop else price          # slippage always hurts
        gross = (px - pos["entry"]) * d * pos["qty"]
        fees = 2 * p.commission_per_share * pos["qty"]
        pnl = gross - fees
        equity += pnl
        trades.append({**pos["info"], "exit_time": times[i], "exit": round(px, 4), "exit_reason": reason,
                       "gross_pnl": round(gross, 2), "fees": round(fees, 2), "pnl": round(pnl, 2),
                       "r_multiple": round(pnl / pos["risk_amt"], 3) if pos["risk_amt"] > 0 else 0.0,
                       "bars_held": i - pos["fill_bar"], "equity_after": round(equity, 2)})
        pos, state = None, "SCAN"

    for i in range(n):
        # ---------- 1. pending order: expiry, cancellation, fill ----------
        if state == "PENDING":
            long = order["direction"] == "LONG"
            if p.day_orders and day[i] != day[order["bar"]]:
                reject(i, "order_expired_end_of_session", **order["info_short"]); order, state = None, "SCAN"
            elif i - order["bar"] > p.entry_expiry_bars:
                reject(i, "order_expired_bars", **order["info_short"]); order, state = None, "SCAN"
            elif not blocked[i]:
                touched = l[i] <= order["entry"] if long else h[i] >= order["entry"]
                if touched:
                    risk_ps = abs(order["entry"] - order["sl"])
                    q_risk = math.floor(equity * p.risk_pct / risk_ps)
                    q_cap = math.floor(equity * p.max_leverage / order["entry"])
                    qty = min(q_risk, q_cap)
                    if qty < 1:
                        reject(i, "qty_below_one_share", **order["info_short"]); order, state = None, "SCAN"
                    else:
                        fill = min(o[i], order["entry"]) if long else max(o[i], order["entry"])
                        pos = {"direction": order["direction"], "entry": fill, "sl": order["sl"], "tp": order["tp"],
                               "qty": qty, "fill_bar": i, "risk_amt": qty * risk_ps,
                               "info": {**order["info"], "fill_time": times[i], "entry": round(fill, 4), "qty": qty,
                                        "risk_amt": round(qty * risk_ps, 2), "size_capped": q_cap < q_risk}}
                        order, state = None, "POSITION"
                        # Same-bar exits after the fill: only the stop counts (we cannot know the order inside a
                        # bar, so the conservative assumption is that any favourable move came before the fill).
                        if (l[i] <= pos["sl"]) if long else (h[i] >= pos["sl"]):
                            close_position(i, min(o[i], pos["sl"]) if long else max(o[i], pos["sl"]), "stop_loss", True)
                elif (h[i] >= order["tp"]) if long else (l[i] <= order["tp"]):
                    reject(i, "target_reached_before_fill", **order["info_short"]); order, state = None, "SCAN"

        # ---------- 2. open position: stop / target / time stop ----------
        elif state == "POSITION" and i > pos["fill_bar"]:
            long = pos["direction"] == "LONG"
            sl, tp = pos["sl"], pos["tp"]
            if (o[i] <= sl) if long else (o[i] >= sl):
                close_position(i, o[i], "stop_loss_gap", True)
            elif (l[i] <= sl) if long else (h[i] >= sl):
                close_position(i, sl, "stop_loss", True)                     # both hit in one bar -> stop first
            elif (o[i] >= tp) if long else (o[i] <= tp):
                close_position(i, o[i], "take_profit_gap", False)
            elif (h[i] >= tp) if long else (l[i] <= tp):
                close_position(i, tp, "take_profit", False)
            elif i - pos["fill_bar"] >= p.max_hold_bars:
                close_position(i, c[i], "time_stop", True)
            elif p.flatten_at_close and is_last_bar[i]:
                close_position(i, c[i], "session_end_flatten", True)

        # ---------- 3. scan for impulse -> consolidation -> breakout (flat only) ----------
        if state == "SCAN":
            if is_impulse(i):
                imp_idx, imp_dir, state = i, ("UP" if c[i] > o[i] else "DOWN"), "CONSOL"
        elif state == "CONSOL":
            a, b = imp_idx + 1, i - 1                      # consolidation window = a..b (before bar i)
            if p.same_session and day[i] != day[imp_idx]:
                reject(i, "consolidation_crossed_session", impulse_time=times[imp_idx]); state = "SCAN"
                if is_impulse(i):
                    imp_idx, imp_dir, state = i, ("UP" if c[i] > o[i] else "DOWN"), "CONSOL"
            elif is_impulse(i):                            # a fresh impulse restarts the pattern
                imp_idx, imp_dir = i, ("UP" if c[i] > o[i] else "DOWN")
            else:
                m = b - a + 1
                if m >= 1:
                    if p.consol_mode == "window":
                        tight = (h[a:b + 1].max() - l[a:b + 1].min()) < p.consol_mult * atr[b]
                    else:
                        tight = all((h[t] - l[t]) < p.consol_mult * atr[t - 1] for t in range(a, b + 1))
                    if not tight:
                        reject(i, "consolidation_not_tight", impulse_time=times[imp_idx], candles=m); state = "SCAN"
                if state == "CONSOL" and m >= p.min_consol:
                    prof = volume_profile(h[a:b + 1], l[a:b + 1], v[a:b + 1], p.n_bins, p.va_pct)
                    if prof is None:
                        reject(i, "no_volume_in_consolidation"); state = "SCAN"
                    else:
                        direction = "LONG" if c[i] > prof["vah"] else ("SHORT" if c[i] < prof["val"] else None)
                        if direction:
                            order = _make_order(p, symbol, h, l, atr, times, blocked, reject,
                                                i, a, b, m, direction, prof, imp_idx, imp_dir)
                            state = "PENDING" if order else "SCAN"
                        elif m >= p.max_consol:
                            reject(i, "no_breakout_within_max_consolidation", impulse_time=times[imp_idx]); state = "SCAN"

        # ---------- 4. mark-to-market equity ----------
        if pos is not None:
            d = 1 if pos["direction"] == "LONG" else -1
            eq_curve[i] = equity + (c[i] - pos["entry"]) * d * pos["qty"]
        else:
            eq_curve[i] = equity

    if pos is not None:                                    # never leave a trade open at the end of the data
        close_position(n - 1, c[n - 1], "end_of_data", False)
        eq_curve[n - 1] = equity
    if state == "PENDING" and order is not None:
        reject(n - 1, "order_unfilled_end_of_data", **order["info_short"])

    tr = pd.DataFrame(trades)
    rj = pd.DataFrame(rejected)
    return Result(symbol=symbol, params=p, trades=tr, rejected=rj,
                  equity=pd.Series(eq_curve, index=times, name="equity"), n_bars=n,
                  n_sessions=int(len(set(day))))


def _make_order(p, symbol, h, l, atr, times, blocked, reject, i, a, b, m, direction, prof, imp_idx, imp_dir):
    """Validates a breakout signal (candle i, consolidation a..b) against every entry rule.
    Returns the pending-order dict, or None after logging why the setup was rejected."""
    long = direction == "LONG"
    swing_low, swing_high = float(l[a:b + 1].min()), float(h[a:b + 1].max())
    entry = float(l[i]) if long else float(h[i])
    if p.sl_mode == "protective":
        sl = swing_low if long else swing_high
    else:                                                  # literal: swing high for longs, swing low for shorts
        sl = swing_high if long else swing_low
    tp = prof["poc"]
    short = {"signal_time": times[i], "direction": direction, "entry": round(entry, 4), "sl": round(sl, 4),
             "tp": round(tp, 4), "poc": round(tp, 4), "vah": round(prof["vah"], 4), "val": round(prof["val"], 4)}

    if p.require_impulse_direction and ((direction == "LONG") != (imp_dir == "UP")):
        reject(i, "breakout_against_impulse_direction", **short); return None
    if blocked[i]:
        reject(i, "signal_bar_in_blocked_window", **short); return None
    valid = (sl < entry < tp) if long else (tp < entry < sl)
    if not valid:
        need = "SL < entry < TP" if long else "TP < entry < SL"
        reject(i, "invalid_geometry", need=need, **short); return None

    info = {"symbol": symbol, "direction": direction, "impulse_time": times[imp_idx], "impulse_dir": imp_dir,
            "impulse_range_atr": round((h[imp_idx] - l[imp_idx]) / atr[imp_idx - 1], 2),
            "consol_start": times[a], "consol_end": times[b], "consol_candles": m,
            "signal_time": times[i], "poc": round(tp, 4), "vah": round(prof["vah"], 4), "val": round(prof["val"], 4),
            "limit_entry": round(entry, 4), "stop_loss": round(sl, 4), "take_profit": round(tp, 4),
            "planned_rr": round(abs(tp - entry) / abs(entry - sl), 3),
            "entry_reason": f"{imp_dir} impulse {(h[imp_idx]-l[imp_idx])/atr[imp_idx-1]:.1f}xATR, {m}-candle consolidation, "
                            f"close {'above VAH' if long else 'below VAL'} ({prof['vah'] if long else prof['val']:.4f})"}
    return {"direction": direction, "entry": entry, "sl": sl, "tp": tp, "bar": i, "info": info, "info_short": short}
