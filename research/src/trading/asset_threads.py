"""
Project Atlas — Phase 3: Multi-Threaded Asset Execution Architecture
=====================================================================
Dedicated concurrent threads for independent market scanning and lifecycle management:
  1. EquityThread: NSE Equities (09:15 – 15:15 IST, 180s interval, own HMM instance)
  2. CurrencyThread: NSE Currency CDS (09:00 – 16:45 IST, 90s interval, own HMM instance)
  3. CommodityThread: MCX Commodities (09:00 – 23:15 IST, 120s interval, own HMM instance)

Each thread:
  - Inherits threading.Thread with daemon=True
  - Holds isolated GaussianMarketHMM to prevent cross-asset state contamination
  - Operates independently: a delay or crash in equity scans never blocks MCX position monitoring
  - Synchronizes shared state atomically through TradingDaemon.state_lock
"""

import threading
import time
from datetime import datetime, time as dt_time
import pytz

from trading.neural_markov import GaussianMarketHMM

IST = pytz.timezone("Asia/Kolkata")

# Market hours
CURRENCY_OPEN = dt_time(9, 0)
CURRENCY_SQUARE_OFF = dt_time(16, 45)
CURRENCY_CLOSE = dt_time(17, 0)

MARKET_OPEN = dt_time(9, 15)
MARKET_CHOP_START = dt_time(11, 0)
MARKET_AFTERNOON_START = dt_time(13, 30)
MARKET_CLOSE = dt_time(15, 15)
EQUITY_SQUARE_OFF = dt_time(15, 25)

MCX_OPEN = dt_time(9, 0)
MCX_SQUARE_OFF = dt_time(23, 15)
MCX_CLOSE = dt_time(23, 30)


class EquityThread(threading.Thread):
    """
    Dedicated thread managing NSE Equity lifecycle:
      - 09:15 AM: Early Gap Scanner (181 stocks)
      - 09:15 – 11:00 AM: Morning Momentum Kill Zone
      - 11:00 – 13:30 PM: Mid-Day Chop Pause (monitoring only, no new entries)
      - 13:30 – 15:15 PM: Afternoon Breakout Kill Zone
      - 15:15 – 15:25 PM: Clean Intraday MIS Square-Off
    """

    def __init__(self, daemon_ref, scan_interval: int = 180):
        super().__init__(daemon=True, name="EquityThread")
        self.daemon_ref = daemon_ref
        self.scan_interval = scan_interval
        self.hmm = GaussianMarketHMM()
        self.gap_scanned_today = False
        self.equity_square_off_done = False

    def run(self):
        print("[EquityThread] Started dedicated NSE Equity worker (180s cycle, isolated HMM).")
        while self.daemon_ref.is_running:
            try:
                now_ist = datetime.now(IST)
                now_time = now_ist.time()
                weekday = now_ist.weekday()
                is_weekday = weekday < 5
                time_str = now_ist.strftime("%H:%M:%S")

                # Weekend check
                if not is_weekday:
                    time.sleep(30)
                    continue

                # Pre-market daily reset
                if now_time < dt_time(9, 0):
                    self.gap_scanned_today = False
                    self.equity_square_off_done = False
                    time.sleep(15)
                    continue

                # 09:00 – 09:15: Pre-market equity standby
                if dt_time(9, 0) <= now_time < MARKET_OPEN:
                    time.sleep(10)
                    continue

                # 09:15 – 11:00: Morning Momentum Kill Zone
                elif MARKET_OPEN <= now_time < MARKET_CHOP_START:
                    print(f"\n[{time_str}] [EquityThread] ⚡ MORNING MOMENTUM KILL ZONE (09:15-11:00)")
                    if not self.gap_scanned_today and now_time >= dt_time(9, 15):
                        self.daemon_ref.run_gap_scan()
                        self.gap_scanned_today = True

                    self.daemon_ref.run_scan_cycle()
                    state = self.daemon_ref.load_state()
                    self.daemon_ref.manage_open_positions(state, asset_filter="EQUITY")
                    time.sleep(self.scan_interval)

                # 11:00 – 13:30: Mid-Day Chop Pause (strictly no new entries)
                elif MARKET_CHOP_START <= now_time < MARKET_AFTERNOON_START:
                    state = self.daemon_ref.load_state()
                    open_eq = len([p for p in state.get("open_positions", []) if p.get("asset_type") == "EQUITY"])
                    print(f"[{time_str}] [EquityThread] ⏸️ MID-DAY CHOP PAUSE (11:00-13:30) | Monitoring {open_eq} open equities")
                    self.daemon_ref.manage_open_positions(state, asset_filter="EQUITY")
                    time.sleep(self.scan_interval)

                # 13:30 – 15:15: Afternoon Breakout Kill Zone
                elif MARKET_AFTERNOON_START <= now_time <= MARKET_CLOSE:
                    print(f"\n[{time_str}] [EquityThread] ⚡ AFTERNOON BREAKOUT KILL ZONE (13:30-15:15)")
                    self.daemon_ref.run_scan_cycle()
                    state = self.daemon_ref.load_state()
                    self.daemon_ref.manage_open_positions(state, asset_filter="EQUITY")
                    time.sleep(self.scan_interval)

                # 15:15 – 15:25: Equity Square-off
                elif MARKET_CLOSE < now_time <= EQUITY_SQUARE_OFF:
                    if not self.equity_square_off_done:
                        print(f"\n[{time_str}] [EquityThread] >> 15:15 PM: SQUARING OFF ALL EQUITY POSITIONS")
                        state = self.daemon_ref.load_state()
                        equity_positions = [p for p in state.get("open_positions", []) if p.get("asset_type") == "EQUITY"]
                        remaining = [p for p in state.get("open_positions", []) if p.get("asset_type") != "EQUITY"]

                        for p in equity_positions:
                            self.daemon_ref._close_position(state, p, "SQUARE_OFF")

                        state["open_positions"] = remaining
                        self.daemon_ref.save_state(state)
                        self.equity_square_off_done = True
                        print(f"[{time_str}] [EquityThread] Equity square-off complete. Currency & MCX continue.")
                    time.sleep(30)

                else:
                    # Off-market hours for equity
                    time.sleep(30)

            except Exception as e:
                print(f"[EquityThread] Error: {e}")
                time.sleep(5)


class CurrencyThread(threading.Thread):
    """
    Dedicated thread managing NSE CDS Currency Futures (USDINR, EURINR, GBPINR, JPYINR):
      - 09:00 – 16:45 IST active trading window
      - 90s scan cycle (fast FX monitoring)
      - 16:45 – 17:00 IST currency intraday square-off
      - Independent from equity scans
    """

    def __init__(self, daemon_ref, scan_interval: int = 90):
        super().__init__(daemon=True, name="CurrencyThread")
        self.daemon_ref = daemon_ref
        self.scan_interval = scan_interval
        self.hmm = GaussianMarketHMM()
        self.currency_square_off_done = False

    def run(self):
        print(f"[CurrencyThread] Started dedicated FX worker ({self.scan_interval}s cycle, isolated HMM).")
        while self.daemon_ref.is_running:
            try:
                now_ist = datetime.now(IST)
                now_time = now_ist.time()
                weekday = now_ist.weekday()
                is_weekday = weekday < 5
                time_str = now_ist.strftime("%H:%M:%S")

                # Weekend check
                if not is_weekday:
                    time.sleep(30)
                    continue

                # Pre-market daily reset
                if now_time < CURRENCY_OPEN:
                    self.currency_square_off_done = False
                    time.sleep(15)
                    continue

                # 09:00 – 16:45: Active FX Trading Window
                if CURRENCY_OPEN <= now_time <= CURRENCY_SQUARE_OFF:
                    self.daemon_ref.run_currency_scan()
                    state = self.daemon_ref.load_state()
                    self.daemon_ref.manage_open_positions(state, asset_filter="CURRENCY")
                    time.sleep(self.scan_interval)

                # 16:45 – 17:00: Currency Square-off
                elif CURRENCY_SQUARE_OFF < now_time <= CURRENCY_CLOSE:
                    if not self.currency_square_off_done:
                        print(f"\n[{time_str}] [CurrencyThread] >> 16:45 PM: SQUARING OFF ALL CURRENCY POSITIONS")
                        state = self.daemon_ref.load_state()
                        fx_positions = [p for p in state.get("open_positions", []) if p.get("asset_type") == "CURRENCY"]
                        remaining = [p for p in state.get("open_positions", []) if p.get("asset_type") != "CURRENCY"]

                        for p in fx_positions:
                            self.daemon_ref._close_position(state, p, "SQUARE_OFF")

                        state["open_positions"] = remaining
                        self.daemon_ref.save_state(state)
                        self.currency_square_off_done = True
                        print(f"[{time_str}] [CurrencyThread] Currency square-off complete.")
                    time.sleep(30)

                else:
                    # Off-market hours for currency
                    time.sleep(30)

            except Exception as e:
                print(f"[CurrencyThread] Error: {e}")
                time.sleep(5)


class CommodityThread(threading.Thread):
    """
    Dedicated thread managing MCX Commodity Futures (Crude Oil, Gold, Silver, Copper Mini, NatGas):
      - 09:00 – 23:15 IST full trading window (includes US session peak 17:00 – 23:15)
      - 120s scan cycle
      - 23:15 – 23:30 IST final master square-off & daily EOD summary dispatch
      - Completely isolated from equity closures
    """

    def __init__(self, daemon_ref, scan_interval: int = 120):
        super().__init__(daemon=True, name="CommodityThread")
        self.daemon_ref = daemon_ref
        self.scan_interval = scan_interval
        self.hmm = GaussianMarketHMM()
        self.mcx_square_off_done = False
        self.daily_report_sent = False

    def run(self):
        print(f"[CommodityThread] Started dedicated MCX worker ({self.scan_interval}s cycle, isolated HMM).")
        while self.daemon_ref.is_running:
            try:
                now_ist = datetime.now(IST)
                now_time = now_ist.time()
                weekday = now_ist.weekday()
                is_weekday = weekday < 5
                time_str = now_ist.strftime("%H:%M:%S")

                # Weekend check
                if not is_weekday:
                    time.sleep(30)
                    continue

                # Pre-market daily reset
                if now_time < MCX_OPEN:
                    self.mcx_square_off_done = False
                    self.daily_report_sent = False
                    time.sleep(15)
                    continue

                # 09:00 – 23:15: Active MCX Trading Window
                if MCX_OPEN <= now_time <= MCX_SQUARE_OFF:
                    self.daemon_ref.run_commodity_scan()
                    state = self.daemon_ref.load_state()
                    self.daemon_ref.manage_open_positions(state, asset_filter="COMMODITY")
                    time.sleep(self.scan_interval)

                # 23:15 – 23:30: MCX Square-off & EOD Master Summary
                elif MCX_SQUARE_OFF < now_time <= MCX_CLOSE:
                    if not self.mcx_square_off_done:
                        print(f"\n[{time_str}] [CommodityThread] >> 23:15 PM: SQUARING OFF MCX COMMODITY POSITIONS")
                        state = self.daemon_ref.load_state()
                        mcx_positions = [p for p in state.get("open_positions", []) if p.get("asset_type") == "COMMODITY"]
                        remaining = [p for p in state.get("open_positions", []) if p.get("asset_type") != "COMMODITY"]

                        for p in mcx_positions:
                            self.daemon_ref._close_position(state, p, "SQUARE_OFF")

                        state["open_positions"] = remaining
                        self.daemon_ref.save_state(state)
                        self.mcx_square_off_done = True

                    if not self.daily_report_sent:
                        print(f"[{time_str}] [CommodityThread] Dispatching Daily EOD Summary Report...")
                        state = self.daemon_ref.load_state()
                        summary = self.daemon_ref.risk_manager.get_daily_summary()
                        summary["total_pnl"] = state.get("total_pnl", 0.0)
                        self.daemon_ref.notifier.notify_daily_summary(summary)
                        self.daily_report_sent = True
                        print(f"[{time_str}] [CommodityThread] Daily session concluded! Master report sent to Telegram.")
                    time.sleep(30)

                else:
                    # Off-market hours for MCX
                    time.sleep(30)

            except Exception as e:
                print(f"[CommodityThread] Error: {e}")
                time.sleep(5)
