"""OHLCV loading: CSV in, regular-session 15-minute bars out (naive exchange-local timestamps)."""
import os
from typing import Optional

import pandas as pd
import requests

COLS = ["open", "high", "low", "close", "volume"]
TIME_NAMES = ("timestamp", "datetime", "date", "time", "date_time", "dt")


def clean(df: pd.DataFrame) -> pd.DataFrame:
    """Sorted, de-duplicated, numeric, and free of impossible candles."""
    df = df.copy()
    for c in COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=COLS)
    ok = (df["high"] >= df["low"]) & (df["high"] >= df[["open", "close"]].max(axis=1) - 1e-9) \
        & (df["low"] <= df[["open", "close"]].min(axis=1) + 1e-9) & (df["low"] > 0) & (df["volume"] >= 0)
    df = df[ok]
    df = df[~df.index.duplicated(keep="first")].sort_index()
    return df


def load_csv(path: str, tz: Optional[str] = None) -> pd.DataFrame:
    """Reads a CSV with a timestamp column plus open/high/low/close/volume (any case).
    Timezone-aware stamps are converted to `tz` (if given) and made naive, so 09:30 means 09:30 local."""
    raw = pd.read_csv(path)
    raw.columns = [str(c).strip().lower().replace(" ", "_") for c in raw.columns]
    tcol = next((c for c in TIME_NAMES if c in raw.columns), None)
    if tcol is None:
        raise ValueError(f"{path}: no timestamp column (looked for {TIME_NAMES})")
    missing = [c for c in COLS if c not in raw.columns]
    if missing:
        raise ValueError(f"{path}: missing columns {missing}")
    idx = pd.to_datetime(raw[tcol], errors="coerce", utc=False)
    if getattr(idx.dt, "tz", None) is not None:
        idx = idx.dt.tz_convert(tz).dt.tz_localize(None) if tz else idx.dt.tz_localize(None)
    raw = raw.set_index(pd.DatetimeIndex(idx)).loc[:, COLS]
    return clean(raw[raw.index.notna()])


def session_filter(df: pd.DataFrame, start: str = "09:30", end: str = "16:00") -> pd.DataFrame:
    """Keeps bars that START in [start, end). Drops pre/post-market bars."""
    t = df.index.time
    s = pd.Timestamp(start).time()
    e = pd.Timestamp(end).time()
    return df[[(x >= s and x < e) for x in t]]


def fetch_yahoo_15m(symbol: str, tz: str = "America/New_York") -> pd.DataFrame:
    """Last ~60 days of 15-minute bars (Yahoo's limit), as naive exchange-local time."""
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}", timeout=25,
                     params={"interval": "15m", "range": "60d"}, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    idx = pd.to_datetime(res["timestamp"], unit="s", utc=True).tz_convert(tz).tz_localize(None)
    df = pd.DataFrame({c: q[c] for c in COLS}, index=pd.DatetimeIndex(idx))
    return clean(df)


def save_csv(df: pd.DataFrame, path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    df.rename_axis("timestamp").to_csv(path)
