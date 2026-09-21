"""
Project Atlas - Market History Store
====================================
Daily price history for every stock of a market, one gzip-JSON file per stock under
data/markets/<market>/. One file per stock because India alone is ~2,300 stocks x 12 years:
loading that as one object would need gigabytes, while streaming stock-by-stock needs almost none.

Resumable: a stock whose file is younger than max_age_days is skipped, so an interrupted
download continues where it stopped. Failures are remembered in _failed.json (retried after
max_age_days) so delisted/renamed tickers are not hammered on every run.
"""
import gzip
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, Iterator, List, Optional, Tuple
from urllib.parse import quote

from data.market_universe import MARKET_DIR
from data.stock_profiles import CHART_URL, YahooSession, parse_chart_history

MAX_AGE_DAYS = 7.0


def _safe(ticker: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", ticker.replace("&", "_and_"))


def _dir(key: str, market_dir: str) -> str:
    d = os.path.join(market_dir, key)
    os.makedirs(d, exist_ok=True)
    return d


def _path(key: str, ticker: str, market_dir: str) -> str:
    return os.path.join(_dir(key, market_dir), _safe(ticker) + ".json.gz")


def save_history(key: str, ticker: str, hist: Dict, market_dir: str = MARKET_DIR) -> None:
    slim = {"ts": hist["ts"], "close": [round(c, 4) for c in hist["close"]],
            "volume": [int(v or 0) for v in hist["volume"]], "events": hist.get("events", [])}
    path = _path(key, ticker, market_dir)
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(slim, fh)
    os.replace(tmp, path)


def load_history(key: str, ticker: str, market_dir: str = MARKET_DIR) -> Optional[Dict]:
    path = _path(key, ticker, market_dir)
    if not os.path.exists(path):
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _is_fresh(path: str, max_age_days: float) -> bool:
    return os.path.exists(path) and (time.time() - os.path.getmtime(path)) < max_age_days * 86400


def fetch_one(session, ticker: str, years: int = 12, tries: int = 4) -> Tuple[Optional[Dict], str]:
    """(history, reason). Backs off on 429/5xx; a 404 means the ticker does not exist on Yahoo."""
    end = int(time.time())
    url = CHART_URL.format(ticker=quote(ticker, safe="^.-="))
    params = {"period1": end - years * 366 * 86400, "period2": end, "interval": "1d", "events": "div"}
    reason = "unknown"
    for i in range(tries):
        try:
            r = session.s.get(url, params=params, timeout=25)
            if r.status_code == 200:
                h = parse_chart_history(r.json())
                return (h, "ok") if h else (None, "too little history")
            if r.status_code == 404:
                return None, "not found"
            reason = f"HTTP {r.status_code}"
            time.sleep(2.0 * (i + 1))
        except Exception as e:                    # noqa: BLE001 - network hiccup: retry, then report
            reason = type(e).__name__
            time.sleep(1.5 * (i + 1))
    return None, reason


def fetch_market(key: str, entries: List[Dict], workers: int = 6, max_age_days: float = MAX_AGE_DAYS,
                 years: int = 12, market_dir: str = MARKET_DIR, session=None,
                 fetch: Optional[Callable] = None, progress: Optional[Callable[[int, int], None]] = None) -> Dict:
    """Downloads whatever is missing or stale. Returns {'total','fetched','skipped','failed'}."""
    failed_path = os.path.join(_dir(key, market_dir), "_failed.json")
    failed: Dict[str, Dict] = {}
    if os.path.exists(failed_path):
        try:
            with open(failed_path, "r", encoding="utf-8") as fh:
                failed = json.load(fh)
        except (OSError, ValueError):
            failed = {}

    now = time.time()
    todo = []
    for e in entries:
        t = e["ticker"]
        if _is_fresh(_path(key, t, market_dir), max_age_days):
            continue
        f = failed.get(t)
        if f and now - f["at"] < max_age_days * 86400:
            continue
        todo.append(t)

    counts = {"total": len(entries), "fetched": 0, "skipped": len(entries) - len(todo), "failed": 0}
    if not todo:
        return counts
    session = session or (None if fetch else YahooSession())
    fetch = fetch or (lambda t: fetch_one(session, t, years))
    lock, done = threading.Lock(), [0]

    def work(ticker: str):
        hist, reason = fetch(ticker)
        with lock:
            done[0] += 1
            if hist:
                save_history(key, ticker, hist, market_dir)
                failed.pop(ticker, None)
                counts["fetched"] += 1
            else:
                failed[ticker] = {"at": time.time(), "reason": reason}
                counts["failed"] += 1
            if progress and done[0] % 100 == 0:
                progress(done[0], len(todo))

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(work, todo))
    tmp = failed_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(failed, fh)
    os.replace(tmp, failed_path)
    return counts


def iter_histories(key: str, entries: List[Dict], market_dir: str = MARKET_DIR) -> Iterator[Tuple[str, Dict]]:
    """Streams (display_symbol, history) for every stock that has a stored file."""
    for e in entries:
        h = load_history(key, e["ticker"], market_dir)
        if h:
            yield e["symbol"], h


def stored_count(key: str, entries: List[Dict], market_dir: str = MARKET_DIR) -> int:
    return sum(1 for e in entries if os.path.exists(_path(key, e["ticker"], market_dir)))
