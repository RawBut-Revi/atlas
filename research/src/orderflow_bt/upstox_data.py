"""
Upstox historical 1-minute candles (public endpoint, no token needed).

The API serves at most one calendar month of 1-minute bars per request, so a long range is
stitched from monthly chunks. Each candle is [timestamp, open, high, low, close, volume, oi];
oi is 0 for cash equities. Timestamps are returned as IST with an offset; we store naive IST wall time.
There is NO buy/sell split, bid/ask or tick data in this feed: order-flow measures built on it are proxies.
"""
import os
import time
from calendar import monthrange
from datetime import date, timedelta
from typing import List, Optional, Tuple
from urllib.parse import quote

import pandas as pd
import requests

URL = "https://api.upstox.com/v3/historical-candle/{key}/minutes/1/{to}/{frm}"
COLS = ["Open", "High", "Low", "Close", "Volume"]


def month_chunks(start: date, end: date) -> List[Tuple[date, date]]:
    """Calendar-month (from, to) pairs covering [start, end]."""
    out, cur = [], date(start.year, start.month, 1)
    while cur <= end:
        last = date(cur.year, cur.month, monthrange(cur.year, cur.month)[1])
        out.append((max(cur, start), min(last, end)))
        cur = last + timedelta(days=1)
    return out


def parse_candles(candles: list) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame(columns=COLS)
    df = pd.DataFrame(candles, columns=["ts", "Open", "High", "Low", "Close", "Volume", "oi"])
    idx = pd.to_datetime(df["ts"]).dt.tz_localize(None)              # wall-clock IST
    df = df.set_index(pd.DatetimeIndex(idx))[COLS]
    for c in COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna()
    ok = (df["High"] >= df["Low"]) & (df["Low"] > 0) & (df["Volume"] >= 0) \
        & (df["High"] >= df[["Open", "Close"]].max(axis=1) - 1e-9) & (df["Low"] <= df[["Open", "Close"]].min(axis=1) + 1e-9)
    df = df[ok]
    return df[~df.index.duplicated(keep="first")].sort_index()


def fetch_range(instrument_key: str, start: date, end: date, pause: float = 0.25,
                session: Optional[requests.Session] = None) -> pd.DataFrame:
    s = session or requests.Session()
    frames = []
    for frm, to in month_chunks(start, end):
        url = URL.format(key=quote(instrument_key, safe=""), to=to.isoformat(), frm=frm.isoformat())
        for attempt in range(3):
            try:
                r = s.get(url, headers={"Accept": "application/json"}, timeout=30)
                body = r.json()
                if r.status_code == 200 and body.get("status") == "success":
                    frames.append(parse_candles(body["data"]["candles"]))
                    break
                if r.status_code == 429:
                    time.sleep(2 * (attempt + 1))
                else:
                    raise RuntimeError(f"Upstox {r.status_code}: {str(body)[:150]}")
            except requests.RequestException:
                time.sleep(1.5 * (attempt + 1))
        else:
            raise RuntimeError(f"Upstox request failed for {frm}..{to}")
        time.sleep(pause)
    return clean(pd.concat(frames)) if frames else pd.DataFrame(columns=COLS)


def load_or_fetch(symbol: str, instrument_key: str, start: date, end: date, cache_dir: str,
                  refresh: bool = False) -> pd.DataFrame:
    path = os.path.join(cache_dir, f"{symbol}_1m.csv")
    if not refresh and os.path.exists(path):
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if len(df) and df.index[0].date() <= start + timedelta(days=5) and df.index[-1].date() >= end - timedelta(days=5):
            return df.loc[str(start):str(end)]
    df = fetch_range(instrument_key, start, end)
    os.makedirs(cache_dir, exist_ok=True)
    df.rename_axis("timestamp").to_csv(path)
    return df
