"""
Garvit's 5-concept system as a backtesting.py Strategy.

The concepts are computed in features.py (point-in-time). This class only COMBINES them and manages
the trade. Setup (all required in the strict variant):
  LONG : Imbalance UP  (open above yesterday's POC + 1 sigma)        Concept 4
         + pullback into an HVN zone                                 Concept 1
         + absorption (proxy) at a lower low that held               Concept 2
         + bullish delta divergence and positive bar delta           Concept 3
         + negative gamma (only when a GEX file is supplied)         Concept 5
  SHORT: the mirror image (Imbalance DOWN, rally into HVN, buyers absorbed, bearish divergence, delta < 0).
Balance days (open inside yesterday's value area) take no trade at all.

Exits : stop = the FARTHER of 0.5 x ATR(14) and the absorption level (minus a tick);
        target = the nearer of the next HVN and 2R. Flat by eod_minute.
Risk  : risk_pct of equity / stop distance, capped by leverage. Max 2 trades a day, first 2 hours only.
"""
import math
from collections import Counter

import numpy as np
from backtesting import Strategy

TICK = 0.05


def _floor_tick(x: float) -> float:
    """Round DOWN to the tick. The epsilon stops float noise (100.6 / 0.05 = 2011.9999...) losing a whole tick."""
    return round(math.floor(x / TICK + 1e-9) * TICK, 2)


def _ceil_tick(x: float) -> float:
    return round(math.ceil(x / TICK - 1e-9) * TICK, 2)


class OrderFlowStrategy(Strategy):
    # -- which concepts must agree (set per variant via bt.run(...)) --
    req_hvn = True
    req_absorb = True
    req_delta = True
    req_imb = True
    # -- risk and session rules from the spec --
    risk_pct = 0.0075              # spec: 0.5-1% per trade
    leverage = 5.0                 # MIS 5x (Upstox), same as the live bot
    max_trades_day = 2
    window_min = 120.0             # only the first 2 hours after the open
    atr_mult = 0.5
    rr = 2.0
    eod_minute = 15 * 60 + 10      # flat before the 15:15 feed gap
    # -- Concept 5: GEX (needs a gex column; "ignore" when no data is available) --
    gex_mode = "ignore"            # "ignore" | "require"
    gex_eps = 0.0                  # |net GEX| <= eps counts as "near 0": skip
    pos_gamma = "skip"             # positive gamma: "skip" (setups need negative gamma) or "half" (trade at 50% size)
    # -- random-entry control (same window, exits and sizing, no order-flow logic) --
    random_p = 0.0
    seed = 0

    def init(self):
        self._day = None
        self._n = 0
        self.rejects = Counter()
        self._rng = np.random.default_rng(self.seed)

    def next(self):
        ts = self.data.index[-1]
        if ts.date() != self._day:
            self._day, self._n = ts.date(), 0
        mod = ts.hour * 60 + ts.minute
        if self.position:
            if mod >= self.eod_minute:
                self.position.close()
            return
        if self._n >= self.max_trades_day or self.data.minute[-1] >= self.window_min or self.orders:
            return
        if math.isnan(self.data.poc[-1]):
            return                                        # first session: no prior day to profile
        atr = self.data.atr[-1]
        if math.isnan(atr):
            return

        long_ok = short_ok = True
        if self.req_imb:                                  # Concept 4: trade only in the direction of an imbalance
            imb = self.data.imb[-1]
            long_ok, short_ok = imb == 1, imb == -1
        if self.req_hvn:                                  # Concept 1
            long_ok &= bool(self.data.hvn_long[-1])
            short_ok &= bool(self.data.hvn_short[-1])
        if self.req_absorb:                               # Concept 2 (proxy)
            long_ok &= bool(self.data.abs_long[-1])
            short_ok &= bool(self.data.abs_short[-1])
        if self.req_delta:                                # Concept 3 (proxy)
            long_ok &= bool(self.data.div_bull[-1]) and self.data.delta[-1] > 0
            short_ok &= bool(self.data.div_bear[-1]) and self.data.delta[-1] < 0
        if self.random_p > 0:                             # control: random direction, random timing
            if self._rng.random() >= self.random_p:
                return
            direction = "LONG" if self._rng.random() < 0.5 else "SHORT"
        else:
            if not (long_ok or short_ok):
                return
            if long_ok and short_ok:
                self.rejects["ambiguous_both_directions"] += 1
                return
            direction = "LONG" if long_ok else "SHORT"

        size_mult = 1.0
        if self.gex_mode == "require":                    # Concept 5, read once at the open
            g = self.data.gex[-1]
            if math.isnan(g):
                self.rejects["no_gex_reading"] += 1
                return
            if abs(g) <= self.gex_eps:
                self.rejects["gex_near_zero"] += 1
                return
            if g > 0:
                if self.pos_gamma == "skip":
                    self.rejects["positive_gamma"] += 1
                    return
                size_mult = 0.5

        entry = float(self.data.Close[-1])
        long = direction == "LONG"
        lvl = self.data.abs_lvl_long[-1] if long else self.data.abs_lvl_short[-1]
        if long:
            stop = entry - self.atr_mult * atr
            if not math.isnan(lvl):
                stop = min(stop, lvl - TICK)                        # farther of the two
            risk = entry - stop
            tp = entry + self.rr * risk
            nh = self.data.next_hvn_up[-1]
            if not math.isnan(nh) and nh > entry:
                tp = min(tp, nh)                                    # next HVN or 2R, whichever comes first
            stop, tp = _floor_tick(stop), _floor_tick(tp)
            valid = stop < entry < tp
        else:
            stop = entry + self.atr_mult * atr
            if not math.isnan(lvl):
                stop = max(stop, lvl + TICK)
            risk = stop - entry
            tp = entry - self.rr * risk
            nh = self.data.next_hvn_dn[-1]
            if not math.isnan(nh) and nh < entry:
                tp = max(tp, nh)
            stop, tp = _ceil_tick(stop), _ceil_tick(tp)
            valid = tp < entry < stop
        if not valid or risk <= 0:
            self.rejects["invalid_stop_target_geometry"] += 1
            return

        equity = self.equity
        units = math.floor(min(equity * self.risk_pct * size_mult / risk, 0.98 * equity * self.leverage / entry))
        if units < 1:
            self.rejects["size_below_one_share"] += 1
            return
        planned_rr = abs(tp - entry) / abs(entry - stop)
        tag = f"rr={planned_rr:.3f}|risk={risk:.4f}|hvn={self.data.hvn_lvl[-1]:.2f}|abs={lvl:.2f}|imb={int(self.data.imb[-1])}|gex={self.data.gex[-1]:.3g}"
        order = self.buy if long else self.sell
        order(size=units, sl=stop, tp=tp, tag=tag)
        self._n += 1
