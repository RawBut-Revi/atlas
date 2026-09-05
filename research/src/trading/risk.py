"""
Project Atlas — Risk Management for Intraday Trading

Handles:
  - Position sizing based on capital and risk per trade
  - Daily loss limit enforcement
  - Trading hours validation
"""

from dataclasses import dataclass, field
from datetime import datetime, time
import pytz

IST = pytz.timezone("Asia/Kolkata")

# Market hours
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 15)  # Stop new trades 15 min before close
SQUARE_OFF = time(15, 25)    # Force close all positions


@dataclass
class TradeRecord:
    symbol: str
    direction: str  # BUY or SELL
    entry_price: float
    exit_price: float
    qty: int
    pnl: float
    entry_time: str
    exit_time: str
    status: str  # "OPEN", "WIN", "LOSS", "SQUARE_OFF"


@dataclass
class RiskManager:
    """Manages universal position sizing, daily loss tracking, and multi-asset trade validation."""

    capital: float = 150000.0          # Updated demat capital ₹1,50,000
    risk_per_trade_pct: float = 1.5   # Risk 1.5% of capital per trade (₹2,250 max loss)
    max_daily_loss_pct: float = 4.0   # Stop trading if daily loss > 4% (₹6,000)
    max_positions: int = 8            # Max simultaneous positions across all assets
    mis_leverage: float = 5.0         # Upstox MIS leverage
    trades_today: list = field(default_factory=list)
    open_positions: list = field(default_factory=list)

    @property
    def buying_power(self) -> float:
        return self.capital * self.mis_leverage

    @property
    def risk_per_trade(self) -> float:
        return self.capital * self.risk_per_trade_pct / 100.0  # ₹2,250.00

    @property
    def daily_pnl(self) -> float:
        return sum(t.pnl for t in self.trades_today if t.status != "OPEN")

    @property
    def daily_loss_limit(self) -> float:
        return self.capital * self.max_daily_loss_pct / 100.0

    def calculate_position_size(
        self,
        entry_price: float,
        stop_loss: float,
        asset_type: str = "EQUITY",
        lot_multiplier: int = 1,
        max_risk_budget: float = None,
    ) -> tuple[int, float, str]:
        """
        Universal Position Sizing & Contract Multiplier Cap Engine.
        Supports dynamic RL risk scaling via optional max_risk_budget.
        Returns: (allowed_units_or_lots, estimated_max_loss_in_rupees, status_message)
        """
        sl_distance = abs(entry_price - stop_loss)
        if sl_distance <= 0:
            return 0, 0.0, "Invalid SL distance"

        single_unit_risk = sl_distance * lot_multiplier
        if single_unit_risk <= 0:
            return 0, 0.0, "Invalid single unit risk"

        risk_budget = max_risk_budget if max_risk_budget is not None else self.risk_per_trade

        # ─── Hard Risk Cap: If 1 Lot risks more than allowed budget, REJECT! ───
        if single_unit_risk > risk_budget:
            return (
                0,
                single_unit_risk,
                f"REJECTED: 1 Lot risks ₹{single_unit_risk:,.0f} > Max Budget ₹{risk_budget:,.0f}",
            )

        allowed_units = int(risk_budget / single_unit_risk)
        allowed_units = max(1, allowed_units)

        if asset_type == "EQUITY":
            max_by_margin = int((self.buying_power * 0.3) / entry_price)  # Max 30% margin on single stock
            allowed_units = min(allowed_units, max_by_margin, 250)
        elif asset_type == "CURRENCY":
            allowed_units = min(allowed_units, 10)  # Max 10 lots FX
        elif asset_type == "COMMODITY":
            allowed_units = min(allowed_units, 4)   # Max 4 lots MCX Mini

        total_risk = round(allowed_units * single_unit_risk, 2)
        return allowed_units, total_risk, "APPROVED"

    def calculate_qty(self, entry_price: float, stop_loss: float) -> int:
        """Helper for equity position sizing."""
        qty, _, _ = self.calculate_position_size(entry_price, stop_loss, "EQUITY", 1)
        return max(1, qty)

    def can_trade(self) -> tuple[bool, str]:
        """Check if we're allowed to take a new trade."""
        now = datetime.now(IST).time()

        # Market hours check
        if now < MARKET_OPEN:
            return False, f"Market not open yet (opens {MARKET_OPEN})"
        if now > MARKET_CLOSE:
            return False, f"Too late for new trades (cutoff {MARKET_CLOSE})"

        # Weekend check
        weekday = datetime.now(IST).weekday()
        if weekday >= 5:
            return False, "Market closed (weekend)"

        # Daily loss limit
        if abs(self.daily_pnl) >= self.daily_loss_limit and self.daily_pnl < 0:
            return False, f"Daily loss limit reached: ₹{abs(self.daily_pnl):.0f}"

        # Max positions
        if len(self.open_positions) >= self.max_positions:
            return False, f"Max {self.max_positions} simultaneous positions"

        return True, "OK"

    def should_square_off(self) -> bool:
        """Check if it's time to force-close all positions."""
        now = datetime.now(IST).time()
        return now >= SQUARE_OFF

    def record_trade(self, trade: TradeRecord):
        """Record a completed trade."""
        self.trades_today.append(trade)

    def reset_daily(self):
        """Reset at start of new trading day."""
        self.trades_today = []
        self.open_positions = []

    def get_daily_summary(self) -> dict:
        """Get today's trading performance summary."""
        closed = [t for t in self.trades_today if t.status != "OPEN"]
        wins = [t for t in closed if t.pnl > 0]
        losses = [t for t in closed if t.pnl <= 0]

        return {
            "total_trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / max(len(closed), 1) * 100, 1),
            "total_pnl": round(sum(t.pnl for t in closed), 2),
            "capital": self.capital,
            "risk_per_trade": round(self.risk_per_trade, 2),
            "buying_power": round(self.buying_power, 2),
            "open_positions": len(self.open_positions),
        }
