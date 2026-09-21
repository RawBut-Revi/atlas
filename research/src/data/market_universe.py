"""
Project Atlas - Market Universes
================================
Which stocks each market contains, plus the per-market assumptions the study needs (benchmark index,
transaction cost, liquidity floor in local currency).

India uses EVERY regular-series equity on NSE (about 2,300; the trading bot's 181-stock list was
curated for intraday liquidity and is far too narrow for an investing scan). World markets use their
main index constituents from Wikipedia (large, liquid, comparable across countries) because "every
listed stock in the world" is tens of thousands of mostly illiquid names and no free source lists
them reliably.

Every loader returns [{"symbol": display, "ticker": yahoo_ticker, "name": str}] and caches the list
for 7 days (falling back to a stale cache if the site is down).

BIASES to keep in mind: all lists are TODAY'S constituents, so every historical test is
survivorship-flattered, and the broader the universe (India-all), the worse it is: delisted losers
are missing entirely.
"""
import io
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import requests

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKET_DIR = os.path.join(HERE, "data", "markets")
HEADERS = {"User-Agent": "Mozilla/5.0 (atlas research)"}
LIST_TTL_DAYS = 7.0


@dataclass(frozen=True)
class MarketSpec:
    key: str
    name: str
    currency: str
    benchmark: str                 # Yahoo index ticker
    cost_per_side: float           # all-in assumption: taxes + spread/slippage, per side
    min_turnover: float            # liquidity floor: 20-day average traded value, in local currency
    turnover_scale: float = 1.0    # multiply price*volume by this to get local currency (LSE quotes pence)
    loader_name: str = ""


# Liquidity floors are about US$1.2M/day (Rs 10 Cr in India). Costs are stated assumptions, not
# measured: India 0.25% (STT + charges + slippage), UK 0.35% (0.5% stamp duty on buys averaged),
# HK 0.25% (stamp duty both sides), Korea 0.25% (sale tax), Europe 0.20% (France FTT), US/Canada 0.10%.
MARKETS: Dict[str, MarketSpec] = {m.key: m for m in [
    MarketSpec("india_all", "India - all NSE equities", "INR", "^NSEI", 0.0025, 1.0e8, 1.0, "load_india_all"),
    MarketSpec("us_sp500", "USA - S&P 500", "USD", "^GSPC", 0.0010, 1.2e6, 1.0, "load_sp500"),
    MarketSpec("uk_ftse100", "UK - FTSE 100", "GBP", "^FTSE", 0.0035, 0.9e6, 0.01, "load_ftse100"),
    MarketSpec("eu_dax_cac", "Europe - DAX 40 + CAC 40", "EUR", "^GDAXI", 0.0020, 1.1e6, 1.0, "load_dax_cac"),
    MarketSpec("hk_hsi", "Hong Kong - Hang Seng", "HKD", "^HSI", 0.0025, 9.4e6, 1.0, "load_hang_seng"),
    MarketSpec("au_asx200", "Australia - S&P/ASX 200", "AUD", "^AXJO", 0.0015, 1.8e6, 1.0, "load_asx200"),
    MarketSpec("ca_tsx60", "Canada - S&P/TSX 60", "CAD", "^GSPTSE", 0.0010, 1.6e6, 1.0, "load_tsx60"),
    MarketSpec("kr_kospi200", "South Korea - KOSPI 200", "KRW", "^KS11", 0.0025, 1.6e9, 1.0, "load_kospi200"),
]}


# ── pure parsers (DataFrame in, list out): unit-tested without the network ─────────────────────

def _entry(symbol: str, ticker: str, name: str = "") -> Dict:
    return {"symbol": symbol, "ticker": ticker, "name": name}


def _clean(v) -> Optional[str]:
    s = "" if v is None else str(v).replace("\xa0", " ").strip()
    return None if s in ("", "nan", "NaN", "None") else s


def parse_nse_equity_csv(text: str) -> List[Dict]:
    """NSE EQUITY_L.csv: regular-series ('EQ') stocks only. BE/BZ are trade-to-trade / surveillance
    segments (no intraday, ASM/GSM style restrictions) and are excluded on purpose."""
    import csv
    rows = csv.DictReader(io.StringIO(text))
    out = []
    for r in rows:
        r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
        if r.get("SERIES") != "EQ" or not r.get("SYMBOL"):
            continue
        sym = r["SYMBOL"]
        out.append(_entry(sym, sym + ".NS", r.get("NAME OF COMPANY", "")))
    return out


def parse_sp500(df) -> List[Dict]:
    out = []
    for sym, name in zip(df["Symbol"], df["Security"]):
        s = _clean(sym)
        if s:
            out.append(_entry(s, s.replace(".", "-"), _clean(name) or ""))
    return out


def parse_ftse100(df) -> List[Dict]:
    out = []
    for sym, name in zip(df["Ticker"], df["Company"]):
        s = _clean(sym)
        if s:
            out.append(_entry(s, s.replace(".", "-") + ".L", _clean(name) or ""))
    return out


def parse_suffixed_tickers(df, ticker_col: str = "Ticker", name_col: str = "Company") -> List[Dict]:
    """DAX / CAC tables already carry the Yahoo exchange suffix (ADS.DE, AIR.PA)."""
    out = []
    for sym, name in zip(df[ticker_col], df[name_col]):
        s = _clean(sym)
        if s and "." in s:
            out.append(_entry(s.split(".")[0], s, _clean(name) or ""))
    return out


def parse_hang_seng(df) -> List[Dict]:
    out = []
    for sym, name in zip(df["Ticker"], df["Name"]):
        m = re.search(r"(\d+)", _clean(sym) or "")
        if m:
            code = str(int(m.group(1))).zfill(4)
            out.append(_entry(code, code + ".HK", _clean(name) or ""))
    return out


def parse_asx200(df) -> List[Dict]:
    out = []
    for sym, name in zip(df["Code"], df["Company"]):
        s = _clean(sym)
        if s:
            out.append(_entry(s, s + ".AX", _clean(name) or ""))
    return out


def parse_tsx60(df) -> List[Dict]:
    out = []
    for sym, name in zip(df["Symbol"], df["Company"]):
        s = _clean(sym)
        if s:
            out.append(_entry(s, s.replace(".", "-") + ".TO", _clean(name) or ""))
    return out


def parse_kospi200(df) -> List[Dict]:
    out = []
    for sym, name in zip(df["Symbol"], df["Company"]):
        s = _clean(sym)
        if s and s.isdigit():
            code = s.zfill(6)
            out.append(_entry(code, code + ".KS", _clean(name) or ""))
    return out


# ── network loaders ─────────────────────────────────────────────────────────────────────────────

def _get(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def _wiki_tables(url: str):
    import pandas as pd
    return pd.read_html(io.StringIO(_get(url)))


def _wiki_table_with(url: str, column: str):
    """The largest table on the page that has `column`."""
    cands = [t for t in _wiki_tables(url) if column in [str(c).strip() for c in t.columns]]
    if not cands:
        raise ValueError(f"no table with a '{column}' column at {url}")
    t = max(cands, key=len)
    t.columns = [str(c).strip() for c in t.columns]
    return t


def load_india_all() -> List[Dict]:
    return parse_nse_equity_csv(_get("https://archives.nseindia.com/content/equities/EQUITY_L.csv"))


def load_sp500() -> List[Dict]:
    return parse_sp500(_wiki_table_with("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", "Symbol"))


def load_ftse100() -> List[Dict]:
    return parse_ftse100(_wiki_table_with("https://en.wikipedia.org/wiki/FTSE_100_Index", "Ticker"))


def load_dax_cac() -> List[Dict]:
    dax = parse_suffixed_tickers(_wiki_table_with("https://en.wikipedia.org/wiki/DAX", "Ticker"))
    cac = parse_suffixed_tickers(_wiki_table_with("https://en.wikipedia.org/wiki/CAC_40", "Ticker"))
    seen, out = set(), []
    for e in dax + cac:
        if e["ticker"] not in seen:
            seen.add(e["ticker"])
            out.append(e)
    return out


def load_hang_seng() -> List[Dict]:
    return parse_hang_seng(_wiki_table_with("https://en.wikipedia.org/wiki/Hang_Seng_Index", "Ticker"))


def load_asx200() -> List[Dict]:
    return parse_asx200(_wiki_table_with("https://en.wikipedia.org/wiki/S%26P/ASX_200", "Code"))


def load_tsx60() -> List[Dict]:
    return parse_tsx60(_wiki_table_with("https://en.wikipedia.org/wiki/S%26P/TSX_60", "Symbol"))


def load_kospi200() -> List[Dict]:
    return parse_kospi200(_wiki_table_with("https://en.wikipedia.org/wiki/KOSPI_200", "Symbol"))


# ── cached access ───────────────────────────────────────────────────────────────────────────────

def universe(key: str, refresh: bool = False, market_dir: str = MARKET_DIR,
             loader: Optional[Callable[[], List[Dict]]] = None) -> List[Dict]:
    """Constituent list for a market, cached for LIST_TTL_DAYS; a stale cache is used if the fetch fails."""
    spec = MARKETS[key]
    os.makedirs(market_dir, exist_ok=True)
    path = os.path.join(market_dir, f"{key}_universe.json")
    cached = None
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                cached = json.load(fh)
        except (OSError, ValueError):
            cached = None
    fresh = cached and (time.time() - os.path.getmtime(path)) < LIST_TTL_DAYS * 86400
    if fresh and not refresh:
        return cached
    try:
        entries = (loader or globals()[spec.loader_name])()
        if len(entries) < 20:
            raise ValueError(f"only {len(entries)} symbols parsed for {key}: page format probably changed")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(entries, fh)
        os.replace(tmp, path)
        return entries
    except Exception as e:                        # noqa: BLE001 - fall back to any cache, else surface the reason
        if cached:
            print(f"[Universe] {key}: refresh failed ({type(e).__name__}: {e}); using stale cache of {len(cached)}")
            return cached
        raise
