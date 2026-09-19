"""
Project Atlas - Upstox Delivery Broker (live)
=============================================
Same three-method interface as PaperDeliveryBroker (get_holdings, get_funds, place_order).

VERIFICATION STATUS: written from Upstox's documented v2 REST API and tested only against a
mocked HTTP session. It has NOT been run against a real account: response field names
(long-term-holdings, funds) and the order endpoint are unconfirmed until the first live read
succeeds. The order endpoint is a constant (ORDER_URL) so it can be switched to v3 if Upstox
retires v2. Treat the first live order as a supervised test.

Safety, enforced here independently of the caller:
  - orders are refused unless the broker was built with allow_orders=True
  - hard per-order value cap (MAX_ORDER_VALUE) and a limit-price sanity band vs the market price
  - CNC/delivery LIMIT orders only, day validity, tagged for audit
  - a submitted order that is not yet filled is returned as PENDING with its cash still
    reserved, so the runner never spends the same money twice
  - an expired token fails fast with instructions rather than partway through a cycle
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import requests

from trading.charges import calculate_delivery_buy_charges
from trading.universe import NSE_UNIVERSE

BASE = "https://api.upstox.com"
PROFILE_URL = BASE + "/v2/user/profile"
FUNDS_URL = BASE + "/v2/user/get-funds-and-margin"
HOLDINGS_URL = BASE + "/v2/portfolio/long-term-holdings"
ORDER_URL = BASE + "/v2/order/place"
ORDER_DETAILS_URL = BASE + "/v2/order/details"

MAX_ORDER_VALUE = 25000.0        # Rs per order; a deliberate ceiling, raise it consciously
MAX_LIMIT_DEVIATION_PCT = 3.0    # limit price may not stray further than this from the market
FILL_POLL_SECONDS = 8.0
ENV_TOKEN = "UPSTOX_ACCESS_TOKEN"
IST = timezone(timedelta(hours=5, minutes=30))
TOKEN_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "engine", ".upstox_token")

REAUTH_HELP = "Upstox token is expired or invalid (tokens expire daily at ~03:30 IST). Log in again with `engine\\atlas.exe auth`, or set UPSTOX_ACCESS_TOKEN."


class UpstoxError(Exception):
    pass


def load_token(path: str = TOKEN_FILE) -> str:
    tok = os.environ.get(ENV_TOKEN)
    if tok:
        return tok.strip()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)["access_token"]
    except (OSError, KeyError, ValueError):
        raise UpstoxError(f"no Upstox token found (env {ENV_TOKEN} or {path}). " + REAUTH_HELP)


class UpstoxDeliveryBroker:
    def __init__(self, allow_orders: bool = False, token: Optional[str] = None,
                 session: Optional[requests.Session] = None, max_order_value: float = MAX_ORDER_VALUE,
                 sleep=time.sleep):
        self.allow_orders = allow_orders
        self.max_order_value = max_order_value
        self._sleep = sleep
        self.s = session or requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token or load_token()}", "Accept": "application/json"})
        self._check_token()

    def _get(self, url: str, params: Optional[Dict] = None) -> Dict:
        try:
            r = self.s.get(url, params=params, timeout=20)
        except requests.RequestException as e:
            raise UpstoxError(f"network error: {e}")
        if r.status_code == 401:
            raise UpstoxError(REAUTH_HELP)
        if r.status_code != 200:
            raise UpstoxError(f"Upstox HTTP {r.status_code}: {r.text[:200]}")
        body = r.json()
        if body.get("status") != "success":
            raise UpstoxError(f"Upstox error: {str(body)[:200]}")
        return body["data"]

    def _check_token(self) -> None:
        self._get(PROFILE_URL)

    def get_holdings(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for h in self._get(HOLDINGS_URL):
            sym = h.get("trading_symbol") or h.get("tradingsymbol")
            qty = int(h.get("quantity") or 0)
            if sym and qty > 0:
                out[sym] = out.get(sym, 0) + qty
        return out

    def get_funds(self) -> float:
        d = self._get(FUNDS_URL, {"segment": "SEC"})
        return round(float(d["equity"]["available_margin"]), 2)

    def place_order(self, symbol: str, qty: int, limit_price: float, ref_price: float) -> Dict:
        if not self.allow_orders:
            raise UpstoxError("orders are disabled on this broker instance")
        key = NSE_UNIVERSE.get(symbol)
        if not key:
            return {"symbol": symbol, "status": "REJECTED", "reason": "no instrument key"}
        if qty < 1 or limit_price <= 0 or ref_price <= 0:
            return {"symbol": symbol, "status": "REJECTED", "reason": "invalid qty/price"}
        if qty * limit_price > self.max_order_value:
            return {"symbol": symbol, "status": "REJECTED",
                    "reason": f"order Rs{qty * limit_price:,.0f} exceeds hard cap Rs{self.max_order_value:,.0f}"}
        if abs(limit_price / ref_price - 1.0) * 100.0 > MAX_LIMIT_DEVIATION_PCT:
            return {"symbol": symbol, "status": "REJECTED", "reason": "limit price too far from market"}

        est = calculate_delivery_buy_charges(limit_price, qty)
        reserve = round(est["value"] + est["total"], 2)
        payload = {"quantity": qty, "product": "D", "validity": "DAY", "price": limit_price,
                   "tag": "atlas-drip", "instrument_token": key, "order_type": "LIMIT",
                   "transaction_type": "BUY", "disclosed_quantity": 0, "trigger_price": 0, "is_amo": False}
        try:
            r = self.s.post(ORDER_URL, json=payload, timeout=20)
        except requests.RequestException as e:
            # Outcome unknown: the order may have reached Upstox. Reserve the cash, never assume it failed.
            return {"symbol": symbol, "status": "PENDING", "reason": f"network error, outcome unknown: {e}",
                    "cash_used": reserve}
        if r.status_code == 401:
            raise UpstoxError(REAUTH_HELP)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code != 200 or body.get("status") != "success":
            return {"symbol": symbol, "status": "REJECTED", "reason": f"HTTP {r.status_code}: {str(body)[:160]}"}

        order_id = body["data"]["order_id"]
        deadline = time.time() + FILL_POLL_SECONDS
        while True:
            try:
                d = self._get(ORDER_DETAILS_URL, {"order_id": order_id})
            except UpstoxError:
                d = {}
            status = (d.get("status") or "").lower()
            if status == "complete":
                px = float(d.get("average_price") or limit_price)
                c = calculate_delivery_buy_charges(px, qty)
                return {"symbol": symbol, "status": "FILLED", "qty": qty, "fill_price": px, "order_id": order_id,
                        "charges": round(c["total"], 2), "cash_used": round(c["value"] + c["total"], 2)}
            if status in ("rejected", "cancelled"):
                return {"symbol": symbol, "status": "REJECTED", "order_id": order_id,
                        "reason": d.get("status_message") or status}
            if time.time() >= deadline:
                return {"symbol": symbol, "status": "PENDING", "order_id": order_id, "cash_used": reserve,
                        "reason": "not filled yet: cash stays reserved until you reconcile"}
            self._sleep(1.0)
