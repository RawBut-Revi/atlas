"""
Project Atlas - Live Stock Profiles (replaces the hand-typed fundamentals table)
================================================================================
Builds a scoring-ready profile for any NSE stock from two public Yahoo endpoints:
  - chart (10y daily): dividend history, price CAGR, drawdown, liquidity
  - quoteSummary     : sector, EPS/book value, payout, debt, 4y net income
    (free cash flow is NOT used: Yahoo returns it for only a few Indian stocks and in mixed currencies)

Yahoo's coverage of Indian stocks is patchy (e.g. ROE and free cash flow are often blank), so
every field can be None. Missing values are never guessed here: the scorer handles them and
lowers confidence. Profiles are cached on disk and refreshed weekly by default.
"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import requests

from data.dividend_events import HEADERS, IST, parse_dividend_events, yahoo_ticker

SUMMARY_URL = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
MODULES = "defaultKeyStatistics,financialData,summaryDetail,assetProfile,incomeStatementHistory"


def _raw(d: Dict, k: str):
    v = d.get(k)
    return v.get("raw") if isinstance(v, dict) else v


def _cagr(new: float, old: float, years: float) -> Optional[float]:
    if years <= 0 or old is None or new is None or old <= 0:
        return None
    if new <= 0:
        return -100.0
    return round(((new / old) ** (1.0 / years) - 1.0) * 100.0, 2)


# ─── quoteSummary -> fundamentals ─────────────────────────────────────────────

def parse_quote_summary(js: Dict) -> Optional[Dict]:
    try:
        res = js["quoteSummary"]["result"][0]
    except (KeyError, IndexError, TypeError):
        return None
    fd, sd, ks, ap = (res.get(k) or {} for k in ("financialData", "summaryDetail", "defaultKeyStatistics", "assetProfile"))
    sector = ap.get("sector") or "UNKNOWN"
    eps, bv = _raw(ks, "trailingEps"), _raw(ks, "bookValue")

    roe = _raw(fd, "returnOnEquity")
    roe = roe * 100.0 if roe is not None else (eps / bv * 100.0 if eps and bv and eps > 0 and bv > 0 else None)
    payout = _raw(sd, "payoutRatio")
    de = _raw(fd, "debtToEquity")                       # Yahoo reports this in percent
    margin = _raw(fd, "profitMargins")
    mcap = _raw(sd, "marketCap")

    ni = sorted(((_raw(x, "endDate"), _raw(x, "netIncome")) for x in
                 (res.get("incomeStatementHistory") or {}).get("incomeStatementHistory", [])),
                key=lambda t: t[0] or 0)
    ni = [(d, v) for d, v in ni if d and v is not None]
    profit_cagr = _cagr(ni[-1][1], ni[0][1], len(ni) - 1) if len(ni) >= 3 else None

    return {
        "sector": sector, "eps_ttm": eps, "pe_ratio": _raw(sd, "trailingPE"),
        "pb_ratio": _raw(ks, "priceToBook"), "roe_pct": roe,
        "payout_ratio_pct": payout * 100.0 if payout is not None else None,
        "debt_to_equity": de / 100.0 if de is not None else None,
        "net_profit_margin_pct": margin * 100.0 if margin is not None else None,
        "profit_cagr_5y": profit_cagr,              # 3-year in practice: Yahoo gives 4 annual points
        "market_cap_cr": round(mcap / 1e7, 0) if mcap else None,
    }


# ─── chart -> price/dividend history stats ────────────────────────────────────

def parse_chart_history(js: Dict) -> Optional[Dict]:
    try:
        res = js["chart"]["result"][0]
        ts = res["timestamp"]
        q = res["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError):
        return None
    rows = [(t, c, v or 0) for t, c, v in zip(ts, q.get("close", []), q.get("volume", [])) if c]
    if len(rows) < 60:
        return None
    return {"ts": [r[0] for r in rows], "close": [r[1] for r in rows], "volume": [r[2] for r in rows],
            "events": parse_dividend_events(js)}


def _price_at(hist: Dict, when: datetime) -> Optional[float]:
    target = when.timestamp()
    if hist["ts"][0] > target + 5 * 86400:          # history does not reach that far back
        return None
    px = None
    for t, c in zip(hist["ts"], hist["close"]):
        if t <= target:
            px = c
        else:
            break
    return px


def history_stats(hist: Dict, asof: date) -> Dict:
    end = datetime(asof.year, asof.month, asof.day, tzinfo=IST)
    last = hist["close"][-1]
    p3, p5 = _price_at(hist, end - timedelta(days=3 * 365)), _price_at(hist, end - timedelta(days=5 * 365))

    peak, mdd = 0.0, 0.0
    for t, c in zip(hist["ts"], hist["close"]):
        if t < (end - timedelta(days=3 * 365)).timestamp():
            continue
        peak = max(peak, c)
        mdd = min(mdd, c / peak - 1.0)

    turn = [c * v for c, v in zip(hist["close"][-20:], hist["volume"][-20:])]

    # Dividends in consecutive trailing-12-month windows, newest last (robust to a partial year).
    windows = []
    for k in range(8, -1, -1):
        hi, lo = asof - timedelta(days=365 * k), asof - timedelta(days=365 * (k + 1))
        windows.append(round(sum(e["dps"] for e in hist["events"] if lo.isoformat() < e["ex_date"] <= hi.isoformat()), 4))
    while windows and windows[0] == 0:              # drop years before the first-ever dividend
        windows.pop(0)
    consecutive = 0
    for w in reversed(windows):
        if w > 0:
            consecutive += 1
        else:
            break
    n = min(5, len(windows) - 1)                    # CAGR from the newest window back up to 5 windows
    div_cagr = _cagr(windows[-1], windows[-1 - n], n) if n >= 3 and consecutive >= 3 else None

    return {
        "price_cagr_3y": _cagr(last, p3, 3.0), "price_cagr_5y": _cagr(last, p5, 5.0),
        "max_drawdown_3y_pct": round(mdd * 100.0, 1),
        "avg_turnover_cr": round(sum(turn) / len(turn) / 1e7, 2) if turn else 0.0,
        "dividend_history": windows, "years_consecutive_dividend": consecutive,
        "dividend_cagr_5y": div_cagr, "history_years": round((hist["ts"][-1] - hist["ts"][0]) / (365 * 86400), 1),
    }


def build_profile(symbol: str, hist: Optional[Dict], fundamentals: Optional[Dict], asof: date,
                  curated: Optional[Dict] = None, now: Optional[datetime] = None) -> Optional[Dict]:
    """Scoring-ready profile. None when there is no usable price history."""
    if not hist:
        return None
    price = hist["close"][-1]
    p = {"symbol": symbol, "current_price": round(price, 2), "fetched_at": (now or datetime.now(IST)).isoformat(timespec="seconds"),
         "fundamentals_ok": fundamentals is not None, "source": "yahoo"}
    p.update(history_stats(hist, asof))
    ttm = p["dividend_history"][-1] if p["dividend_history"] else 0.0
    p["dividend_yield_pct"] = round(ttm / price * 100.0, 2)
    p["sector"] = "UNKNOWN"
    if fundamentals:
        p.update({k: v for k, v in fundamentals.items()})
    # The curated table is the only source of Piotroski F-scores; keep them as the one fallback.
    if curated and curated.get("piotroski_f_score") is not None:
        p["piotroski_f_score"] = curated["piotroski_f_score"]
    return p


# ─── network + cache ──────────────────────────────────────────────────────────

class YahooSession:
    """One cookie/crumb pair shared by all workers."""

    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        self.s.get("https://fc.yahoo.com", timeout=10)
        self.crumb = self.s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=10).text

    def _get(self, url, params, tries=2):
        for i in range(tries):
            try:
                r = self.s.get(url, params=params, timeout=20)
                if r.status_code == 200:
                    return r.json()
                if r.status_code in (429, 500, 502, 503):
                    time.sleep(1.5 * (i + 1))
                else:
                    return None
            except (requests.RequestException, ValueError):
                time.sleep(1.0)
        return None

    def chart(self, symbol: str, years: int = 10) -> Optional[Dict]:
        end = int(time.time())
        return self._get(CHART_URL.format(ticker=yahoo_ticker(symbol)),
                         {"period1": end - years * 366 * 86400, "period2": end, "interval": "1d", "events": "div"})

    def summary(self, symbol: str) -> Optional[Dict]:
        return self._get(SUMMARY_URL.format(ticker=yahoo_ticker(symbol)), {"modules": MODULES, "crumb": self.crumb})


def refresh_profiles(symbols: List[str], cache_path: str, max_age_days: float = 7.0, workers: int = 6,
                     curated: Optional[Dict[str, Dict]] = None, session: Optional[YahooSession] = None,
                     now: Optional[datetime] = None) -> Dict:
    """Load cache, refetch only stale/missing symbols, write back. Returns {'profiles', 'errors', 'refreshed'}."""
    now = now or datetime.now(IST)
    cache: Dict[str, Dict] = {}
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as fh:
            cache = json.load(fh)

    def fresh(p):
        try:
            return (now - datetime.fromisoformat(p["fetched_at"])).total_seconds() < max_age_days * 86400
        except (KeyError, ValueError):
            return False

    todo = [s for s in symbols if not (s in cache and fresh(cache[s]) and cache[s].get("fundamentals_ok"))]
    errors, refreshed = {}, 0
    if todo:
        session = session or YahooSession()

        def work(sym):
            hist = parse_chart_history(session.chart(sym) or {})
            fund = parse_quote_summary(session.summary(sym) or {})
            return sym, build_profile(sym, hist, fund, now.date(), (curated or {}).get(sym), now)

        with ThreadPoolExecutor(workers) as ex:
            for sym, prof in ex.map(work, todo):
                if prof is None:
                    errors[sym] = "no price history"
                else:
                    cache[sym] = prof
                    refreshed += 1
        tmp = cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh)
        os.replace(tmp, cache_path)
    return {"profiles": {s: cache[s] for s in symbols if s in cache}, "errors": errors, "refreshed": refreshed}


def select_universe(profiles: Dict[str, Dict], min_years: int = 3, min_turnover_cr: float = 10.0,
                    min_yield_pct: float = 0.0) -> Dict[str, Dict]:
    """Dividend-paying, liquid stocks only: a reinvestment target must be buyable without moving the price."""
    return {s: p for s, p in profiles.items()
            if p.get("years_consecutive_dividend", 0) >= min_years
            and p.get("avg_turnover_cr", 0) >= min_turnover_cr
            and p.get("dividend_yield_pct", 0) >= min_yield_pct}
