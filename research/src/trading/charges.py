"""
Project Atlas — Transaction Cost Modeling (TCM) & Brokerage Engine
Accurately computes all statutory taxes, brokerages, and exchange fees for Indian markets.

Covers:
  1. Brokerage (Upstox / Discount Broker: ₹20/order or 0.05%)
  2. STT / CTT (Securities / Commodities Transaction Tax)
  3. Exchange Transaction Charges (NSE / MCX)
  4. GST (18% on Brokerage + Exchange Charges + SEBI Fees)
  5. SEBI Turnover Charges (₹10 / Crore)
  6. Stamp Duty (State Govt Rates on Buy Orders)
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class TradeCharges:
    gross_pnl: float              # P&L before any charges
    net_pnl: float                # True Take-Home P&L after all charges
    total_charges: float          # Sum of all taxes & fees
    brokerage: float              # Broker fee (Entry + Exit)
    stt: float                    # Securities / Commodities Transaction Tax
    exchange_charges: float       # NSE / MCX Transaction fee
    gst: float                    # 18% GST
    sebi_charges: float           # ₹10 per crore
    stamp_duty: float             # State Stamp duty
    buy_turnover: float
    sell_turnover: float
    total_turnover: float
    breakeven_points: float       # Price movement required to cover all costs

    def to_dict(self) -> dict:
        return {
            "gross_pnl": round(self.gross_pnl, 2),
            "net_pnl": round(self.net_pnl, 2),
            "total_charges": round(self.total_charges, 2),
            "brokerage": round(self.brokerage, 2),
            "stt": round(self.stt, 2),
            "exchange_charges": round(self.exchange_charges, 2),
            "gst": round(self.gst, 2),
            "sebi_charges": round(self.sebi_charges, 2),
            "stamp_duty": round(self.stamp_duty, 2),
            "total_turnover": round(self.total_turnover, 2),
            "breakeven_points": round(self.breakeven_points, 4),
        }


def calculate_trade_charges(
    symbol: str,
    asset_type: str,          # "EQUITY", "CURRENCY", "COMMODITY"
    direction: str,           # "BUY" or "SELL"
    entry_price: float,
    exit_price: float,
    qty_or_lots: int = 1,
    broker_flat_fee: float = 20.0,
) -> TradeCharges:
    """
    Computes exact real-world transaction charges and net P&L for a closed trade.
    """
    asset_type = asset_type.upper()
    direction = direction.upper()

    # Determine contract multiplier for turnover
    multiplier = 1.0
    if asset_type == "CURRENCY":
        # USDINR / EURINR / GBPINR = 1000, JPYINR = 100,000
        multiplier = 100000.0 if "JPY" in symbol else 1000.0
    elif asset_type == "COMMODITY":
        if "CRUDE" in symbol:
            multiplier = 10.0      # 10 barrels per mini lot
        elif "NATGAS" in symbol or "GAS" in symbol:
            multiplier = 250.0     # 250 mmBtu per mini lot
        elif "SILVER" in symbol:
            multiplier = 1.0       # 1 kg per micro lot
        elif "GOLD" in symbol:
            multiplier = 100.0     # 100 grams
        elif "COPPER" in symbol:
            multiplier = 2500.0    # 2,500 kg

    units = qty_or_lots * multiplier

    if direction == "BUY":
        buy_price = entry_price
        sell_price = exit_price
        gross_pnl = (sell_price - buy_price) * units
    else:  # SELL (Short)
        sell_price = entry_price
        buy_price = exit_price
        gross_pnl = (sell_price - buy_price) * units

    buy_turnover = buy_price * units
    sell_turnover = sell_price * units
    total_turnover = buy_turnover + sell_turnover

    # ─── 1. Equity Intraday (NSE) ─────────────────────────────────
    if asset_type == "EQUITY":
        # Brokerage: ₹20 per order or 0.05% of turnover (whichever is lower)
        buy_brokerage = min(broker_flat_fee, buy_turnover * 0.0005)
        sell_brokerage = min(broker_flat_fee, sell_turnover * 0.0005)
        brokerage = buy_brokerage + sell_brokerage

        # STT: 0.025% on Sell side only for intraday
        stt = sell_turnover * 0.00025

        # Exchange txn charge: 0.00345% of total turnover
        exchange_charges = total_turnover * 0.0000345

        # Stamp duty: 0.003% on Buy side only
        stamp_duty = buy_turnover * 0.00003

        # SEBI Turnover fees: ₹10 per crore (0.0001%)
        sebi_charges = total_turnover * 0.000001

        # GST: 18% on (Brokerage + Exchange + SEBI)
        gst = (brokerage + exchange_charges + sebi_charges) * 0.18

    # ─── 2. Currency Derivatives (NSE CDS) ────────────────────────
    elif asset_type == "CURRENCY":
        # Brokerage: Flat ₹20 per leg
        brokerage = broker_flat_fee * 2.0

        # STT: 0% (Zero STT on Currency Futures!)
        stt = 0.0

        # Exchange txn charge: 0.0009% of total turnover
        exchange_charges = total_turnover * 0.000009

        # Stamp duty: 0.0001% on Buy side
        stamp_duty = buy_turnover * 0.000001

        # SEBI fees: ₹10 per crore
        sebi_charges = total_turnover * 0.000001

        # GST: 18% on (Brokerage + Exchange + SEBI)
        gst = (brokerage + exchange_charges + sebi_charges) * 0.18

    # ─── 3. Commodity Futures (MCX) ───────────────────────────────
    elif asset_type == "COMMODITY":
        # Brokerage: Flat ₹20 per leg
        brokerage = broker_flat_fee * 2.0

        # CTT (Commodity Transaction Tax): 0.01% on Sell side
        stt = sell_turnover * 0.0001

        # MCX Exchange txn charge: 0.0021% of total turnover
        exchange_charges = total_turnover * 0.000021

        # Stamp duty: 0.002% on Buy side
        stamp_duty = buy_turnover * 0.00002

        # SEBI fees: ₹10 per crore
        sebi_charges = total_turnover * 0.000001

        # GST: 18% on (Brokerage + Exchange + SEBI)
        gst = (brokerage + exchange_charges + sebi_charges) * 0.18

    else:
        brokerage = broker_flat_fee * 2.0
        stt = 0.0
        exchange_charges = 0.0
        stamp_duty = 0.0
        sebi_charges = 0.0
        gst = brokerage * 0.18

    total_charges = brokerage + stt + exchange_charges + gst + sebi_charges + stamp_duty
    net_pnl = gross_pnl - total_charges
    breakeven_points = total_charges / max(units, 1.0)

    return TradeCharges(
        gross_pnl=gross_pnl,
        net_pnl=net_pnl,
        total_charges=total_charges,
        brokerage=brokerage,
        stt=stt,
        exchange_charges=exchange_charges,
        gst=gst,
        sebi_charges=sebi_charges,
        stamp_duty=stamp_duty,
        buy_turnover=buy_turnover,
        sell_turnover=sell_turnover,
        total_turnover=total_turnover,
        breakeven_points=breakeven_points,
    )


if __name__ == "__main__":
    print("=== Testing Real-World Transaction Charges Engine ===")
    
    # 1. Equity Example (SBIN 18 qty @ 1047.5 entered and exited at 1047.5)
    c1 = calculate_trade_charges("SBIN", "EQUITY", "BUY", 1047.5, 1047.5, qty_or_lots=18)
    print(f"Equity Scratch Trade (SBIN 18x): Gross=₹{c1.gross_pnl:.2f}, Charges=₹{c1.total_charges:.2f}, Net=₹{c1.net_pnl:.2f}")
    
    # 2. Currency Example (USDINR 1 lot @ 95.44 sold, exited at 95.15)
    c2 = calculate_trade_charges("USDINR", "CURRENCY", "SELL", 95.44, 95.15, qty_or_lots=1)
    print(f"Currency Trade (USDINR 1 lot): Gross=₹{c2.gross_pnl:.2f}, Charges=₹{c2.total_charges:.2f}, Net=₹{c2.net_pnl:.2f}")

    # 3. Commodity Example (CRUDEOILM 1 lot @ 7242.85, exited at 7276.85)
    c3 = calculate_trade_charges("CRUDEOILM", "COMMODITY", "BUY", 7242.85, 7276.85, qty_or_lots=1)
    print(f"Commodity Trade (CRUDEOILM 1 lot): Gross=₹{c3.gross_pnl:.2f}, Charges=₹{c3.total_charges:.2f}, Net=₹{c3.net_pnl:.2f}")
