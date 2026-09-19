"""
Project Atlas - Dividend Events & Live Prices (Yahoo Finance chart API, no auth)
================================================================================
Real ex-dividend dates and per-share amounts for NSE stocks, plus the last traded price.
Same public endpoint the currency module already uses, so it works on Termux too.
Fetchers return [] / None on any failure: callers must treat that as "unknown", never as zero.
"""
import time
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

import requests

IST = timezone(timedelta(hours=5, minutes=30))
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def yahoo_ticker(symbol: str) -> str:
    return symbol.replace("&", "%26") + ".NS"


def parse_dividend_events(chart_json: Dict) -> List[Dict]:
    """[{'ex_date': 'YYYY-MM-DD', 'dps': float}] oldest first, from a Yahoo chart response."""
    try:
        divs = chart_json["chart"]["result"][0].get("events", {}).get("dividends", {})
    except (KeyError, IndexError, TypeError):
        return []
    out = [{"ex_date": datetime.fromtimestamp(v["date"], tz=IST).strftime("%Y-%m-%d"),
            "dps": float(v["amount"])} for v in divs.values()
           if v.get("amount") and float(v["amount"]) > 0]
    return sorted(out, key=lambda e: e["ex_date"])


def _chart(symbol: str, years: int) -> Optional[Dict]:
    end = int(time.time())
    try:
        r = requests.get(CHART_URL.format(ticker=yahoo_ticker(symbol)), headers=HEADERS, timeout=15,
                         params={"period1": end - years * 366 * 86400, "period2": end,
                                 "interval": "1d", "events": "div"})
        return r.json() if r.status_code == 200 else None
    except (requests.RequestException, ValueError):
        return None


def parse_snapshot(chart_json: Dict) -> Optional[Dict]:
    """{'price', 'events', 'trend_pct'} from one chart response, or None if there is no usable price.
    trend_pct = live price vs its 200-day average, in % (None if under 100 daily closes)."""
    try:
        res = chart_json["chart"]["result"][0]
        price = float(res["meta"]["regularMarketPrice"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if price <= 0:
        return None
    closes = [c for c in res.get("indicators", {}).get("quote", [{}])[0].get("close", []) if c]
    trend = None
    if len(closes) >= 100:
        window = closes[-200:]
        trend = round((price / (sum(window) / len(window)) - 1.0) * 100.0, 2)
    return {"price": price, "events": parse_dividend_events(chart_json), "trend_pct": trend}


def fetch_snapshot(symbol: str, years: int = 2) -> Optional[Dict]:
    data = _chart(symbol, years)
    return parse_snapshot(data) if data else None


def trailing_12m_dps(events: List[Dict], asof: date) -> float:
    start = (asof - timedelta(days=365)).isoformat()
    return round(sum(e["dps"] for e in events if start < e["ex_date"] <= asof.isoformat()), 4)
