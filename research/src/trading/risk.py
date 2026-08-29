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
    """Manages position sizing, daily loss tracking, and trade validation."""

    capital: float = 10000.0
    risk_per_trade_pct: float = 2.0   # Risk 2% of capital per trade
    max_daily_loss_pct: float = 3.0   # Stop trading if daily loss > 3%
    max_positions: int = 3            # Max simultaneous positions
    mis_leverage: float = 5.0         # Upstox MIS leverage
    trades_today: list = field(default_factory=list)
    open_positions: list = field(default_factory=list)

    @property
    def buying_power(self) -> float:
        return self.capital * self.mis_leverage

    @property
    def risk_per_trade(self) -> float:
        return self.capital * self.risk_per_trade_pct / 100

    @property
    def daily_pnl(self) -> float:
        return sum(t.pnl for t in self.trades_today if t.status != "OPEN")

    @property
    def daily_loss_limit(self) -> float:
        return self.capital * self.max_daily_loss_pct / 100

    def calculate_qty(self, entry_price: float, stop_loss: float) -> int:
        """
        Calculate position size: risk amount / stop-loss distance.
        E.g., ₹200 risk / ₹5 SL = 40 shares.
        """
        sl_distance = abs(entry_price - stop_loss)
        if sl_distance <= 0:
            return 0
        qty = int(self.risk_per_trade / sl_distance)
        # Check we don't exceed buying power
        max_qty = int(self.buying_power / entry_price)
        return min(qty, max_qty, 500)  # Cap at 500 shares for safety

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
