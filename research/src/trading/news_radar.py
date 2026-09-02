"""
Project Atlas — Real-Time News & Market Panic Radar
Monitors live financial news feeds for sector-specific government policy changes,
regulatory actions, and black swan events. Automatically triggers defensive
actions (exit positions, block new entries) in affected sectors.

Sources: Economic Times, MoneyControl, LiveMint RSS feeds (free, no API key).
"""

import os
import json
import time
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional
import requests

# ─── Sector-to-Stock Mapping ────────────────────────────────────────────────

SECTOR_STOCKS = {
    "BANKING": ["SBIN", "HDFCBANK", "ICICIBANK", "KOTAKBANK", "AXISBANK", "BANKBARODA", "PNB", "INDUSINDBK"],
    "IT": ["TCS", "INFY", "WIPRO", "HCLTECH", "TECHM", "LTIM", "MPHASIS", "COFORGE"],
    "PHARMA": ["SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "APOLLOHOSP", "BIOCON"],
    "AUTO": ["TATAMOTORS", "M&M", "MARUTI", "HEROMOTOCO", "BAJAJ-AUTO", "EICHERMOT", "ASHOKLEY"],
    "METAL": ["TATASTEEL", "JSWSTEEL", "HINDALCO", "VEDL", "NATIONALUM", "COALINDIA", "NMDC"],
    "OIL_GAS": ["RELIANCE", "ONGC", "BPCL", "IOC", "GAIL", "PETRONET"],
    "REALTY": ["DLF", "GODREJPROP", "OBEROIRLTY", "PRESTIGE", "BRIGADE"],
    "FMCG": ["ITC", "HINDUNILVR", "NESTLEIND", "BRITANNIA", "DABUR", "MARICO", "COLPAL"],
    "INFRA": ["LT", "ADANIENT", "ADANIPORTS", "ULTRACEMCO", "AMBUJACEM", "ACC"],
    "DEFENCE": ["BEL", "HAL", "BDL", "COCHINSHIP"],
    "TELECOM": ["BHARTIARTL", "IDEA"],
    "POWER": ["NTPC", "POWERGRID", "TATAPOWER", "ADANIGREEN", "NHPC"],
    "TEXTILE": ["TRENT", "PAGEIND", "RAYMOND"],
    "COMMODITY_COPPER": ["COPPER", "COPPERM"],
    "COMMODITY_CRUDE": ["CRUDEOILM"],
    "COMMODITY_GOLD": ["GOLDM", "SILVERMIC"],
    "CURRENCY_INR": ["USDINR", "EURINR", "GBPINR", "JPYINR"],
}

# Reverse lookup: stock → sector
STOCK_TO_SECTOR = {}
for sector, stocks in SECTOR_STOCKS.items():
    for sym in stocks:
        STOCK_TO_SECTOR[sym] = sector

# ─── Panic Keywords & Severity ──────────────────────────────────────────────

# Keywords mapped to (severity_score, affected_sector_keywords)
PANIC_KEYWORDS = {
    # Government Policy / Regulatory (HIGH severity)
    r"ban(?:ned|ning|s)?": (9, None),
    r"prohibit(?:ion|ed|ing|s)?": (9, None),
    r"export ban(?:s)?": (10, ["METAL", "PHARMA", "FMCG"]),
    r"import dut(?:y|ies)": (8, ["METAL", "AUTO", "OIL_GAS"]),
    r"windfall tax(?:es)?": (9, ["OIL_GAS"]),
    r"price cap(?:s)?": (8, None),
    r"price ceiling": (8, None),
    r"retrospective tax(?:es)?": (9, None),
    r"sebi order(?:s)?": (7, None),
    r"sebi action(?:s)?": (7, None),
    r"rbi circular(?:s)?": (7, ["BANKING", "CURRENCY_INR"]),
    r"rbi policy": (6, ["BANKING", "CURRENCY_INR"]),
    r"rate hike(?:s)?": (6, ["BANKING", "REALTY"]),
    r"rate cut(?:s)?": (5, ["BANKING", "REALTY"]),
    r"regulatory action(?:s)?": (8, None),
    r"crackdown(?:s)?": (8, None),
    r"penalt(?:y|ies)": (6, None),
    r"fine(?:s)? imposed": (6, None),
    r"fraud(?:s|ulent)?": (9, None),
    r"scam(?:s)?": (9, None),
    r"default(?:s|ed|ing)?": (8, ["BANKING"]),
    r"npa(?:s)?": (7, ["BANKING"]),
    r"moratorium(?:s)?": (8, ["BANKING"]),
    r"gst hike(?:s)?": (7, ["FMCG", "AUTO"]),
    r"tax hike(?:s)?": (7, None),
    r"surcharge(?:s)?": (6, None),
    r"cess": (5, None),
    r"demonetization": (10, ["BANKING", "FMCG"]),
    r"lockdown(?:s)?": (10, None),
    r"curfew(?:s)?": (9, None),
    r"emergency": (10, None),
    r"war(?:s)?": (10, None),
    r"missile(?:s)?": (10, ["DEFENCE"]),
    r"sanction(?:s)?": (9, ["OIL_GAS", "IT"]),
    r"tariff(?:s)?": (7, ["IT", "METAL", "AUTO"]),
    r"trade war(?:s)?": (8, None),

    # Market Crash / Sentiment (MEDIUM-HIGH)
    r"circuit breaker(?:s)?": (9, None),
    r"lower circuit(?:s)?": (8, None),
    r"upper circuit(?:s)?": (5, None),
    r"market crash(?:es|ed)?": (9, None),
    r"panic selling": (9, None),
    r"blood bath|bloodbath": (8, None),
    r"black monday": (10, None),
    r"flash crash(?:es)?": (10, None),
    r"margin call(?:s)?": (8, None),
    r"fii selling": (7, None),
    r"fpi outflow(?:s)?": (7, None),

    # Sector-Specific Triggers
    r"drug recall(?:s)?": (8, ["PHARMA"]),
    r"fda warning(?:s)?": (7, ["PHARMA"]),
    r"emission norm(?:s)?": (6, ["AUTO"]),
    r"steel dut(?:y|ies)": (7, ["METAL"]),
    r"crude oil surge(?:s)?": (6, ["OIL_GAS", "AUTO"]),
    r"rupee (?:crash(?:es|ed)?|fall(?:s|ing)?|plunge(?:s|d)?|drops?)": (8, ["CURRENCY_INR", "IT"]),
    r"gold surge(?:s)?": (5, ["COMMODITY_GOLD"]),
    r"copper (?:crash(?:es|ed)?|fall(?:s|ing)?|plunge(?:s|d)?)": (7, ["COMMODITY_COPPER", "METAL"]),
    r"inflation spike(?:s)?": (7, ["BANKING", "FMCG"]),
    r"recession(?:ary)?": (8, None),
    r"gdp contraction": (7, None),
    r"unemployment spike": (6, None),
    r"downgrade(?:s|d)?": (7, None),
    r"rating downgrade(?:s)?": (8, None),
}

# ─── News Feed Sources (Free RSS, No API Key) ──────────────────────────────

NEWS_FEEDS = [
    {
        "name": "Economic Times - Markets",
        "url": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
        "type": "rss",
    },
    {
        "name": "Economic Times - Economy",
        "url": "https://economictimes.indiatimes.com/news/economy/rssfeeds/1373380680.cms",
        "type": "rss",
    },
    {
        "name": "LiveMint - Markets",
        "url": "https://www.livemint.com/rss/markets",
        "type": "rss",
    },
    {
        "name": "MoneyControl - News",
        "url": "https://www.moneycontrol.com/rss/latestnews.xml",
        "type": "rss",
    },
]

NEWS_CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "news_cache.json")
PANIC_STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "panic_state.json")


@dataclass
class NewsAlert:
    headline: str
    source: str
    timestamp: str
    severity: int           # 1-10 (10 = maximum panic)
    affected_sectors: list  # ["BANKING", "IT", ...]
    affected_stocks: list   # ["SBIN", "HDFCBANK", ...]
    panic_keywords_found: list
    action: str             # "EXIT_POSITIONS", "BLOCK_ENTRIES", "MONITOR", "NONE"

    def to_dict(self) -> dict:
        return {
            "headline": self.headline,
            "source": self.source,
            "timestamp": self.timestamp,
            "severity": self.severity,
            "affected_sectors": self.affected_sectors,
            "affected_stocks": self.affected_stocks,
            "panic_keywords": self.panic_keywords_found,
            "action": self.action,
        }


def fetch_rss_headlines(feed_url: str, feed_name: str, max_items: int = 15) -> list[dict]:
    """Fetches latest headlines from an RSS feed."""
    headlines = []
    try:
        resp = requests.get(feed_url, timeout=8, headers={
            "User-Agent": "Mozilla/5.0 (Linux; Android 14) ProjectAtlas/1.0"
        })
        if resp.status_code != 200:
            return []

        root = ET.fromstring(resp.content)

        # Standard RSS 2.0
        for item in root.findall(".//item")[:max_items]:
            title = item.find("title")
            pub_date = item.find("pubDate")
            desc = item.find("description")
            headlines.append({
                "title": title.text.strip() if title is not None and title.text else "",
                "source": feed_name,
                "date": pub_date.text.strip() if pub_date is not None and pub_date.text else "",
                "description": desc.text.strip() if desc is not None and desc.text else "",
            })
    except Exception:
        pass
    return headlines


def analyze_headline_for_panic(headline: str, description: str = "") -> tuple[int, list, list, list]:
    """
    Analyzes a single headline for panic-inducing keywords.
    Uses word-boundary regex matching to avoid false positives (e.g. 'ban' in 'bank').
    Returns: (max_severity, affected_sectors, affected_stocks, keywords_found)
    """
    text = (headline + " " + description).lower()
    max_severity = 0
    sectors_hit = set()
    keywords_found = []

    for keyword, (severity, sector_hints) in PANIC_KEYWORDS.items():
        # Use word boundary regex to avoid partial matches ('ban' != 'bank')
        pattern = r'\b(?:' + keyword + r')\b'
        if re.search(pattern, text):
            keywords_found.append(keyword)
            max_severity = max(max_severity, severity)
            if sector_hints:
                sectors_hit.update(sector_hints)

    # If no specific sector identified but severity is high, it's market-wide
    if max_severity >= 8 and not sectors_hit:
        sectors_hit = set(SECTOR_STOCKS.keys())

    # Also try to identify sector from headline content
    sector_keyword_map = {
        "bank": "BANKING", "nifty bank": "BANKING", "rbi": "BANKING",
        "it sector": "IT", "software": "IT", "tech": "IT",
        "pharma": "PHARMA", "drug": "PHARMA", "fda": "PHARMA",
        "auto": "AUTO", "vehicle": "AUTO", "ev ": "AUTO",
        "metal": "METAL", "steel": "METAL", "iron": "METAL", "aluminium": "METAL",
        "oil": "OIL_GAS", "crude": "OIL_GAS", "petrol": "OIL_GAS", "diesel": "OIL_GAS",
        "real estate": "REALTY", "housing": "REALTY", "property": "REALTY",
        "fmcg": "FMCG", "consumer": "FMCG",
        "infra": "INFRA", "cement": "INFRA", "construction": "INFRA",
        "defence": "DEFENCE", "military": "DEFENCE",
        "telecom": "TELECOM", "5g": "TELECOM", "spectrum": "TELECOM",
        "power": "POWER", "electricity": "POWER", "renewable": "POWER",
        "textile": "TEXTILE", "apparel": "TEXTILE",
        "copper": "COMMODITY_COPPER",
        "gold": "COMMODITY_GOLD", "silver": "COMMODITY_GOLD",
        "rupee": "CURRENCY_INR", "forex": "CURRENCY_INR", "dollar": "CURRENCY_INR",
    }
    for kw, sector in sector_keyword_map.items():
        pattern = r'\b' + re.escape(kw) + r'\b'
        if re.search(pattern, text) and max_severity > 0:
            sectors_hit.add(sector)

    # Map sectors to affected stocks
    affected_stocks = []
    for sector in sectors_hit:
        affected_stocks.extend(SECTOR_STOCKS.get(sector, []))

    return max_severity, list(sectors_hit), list(set(affected_stocks)), keywords_found


def scan_news_feeds() -> list[NewsAlert]:
    """
    Scans all RSS news feeds and returns panic alerts sorted by severity.
    """
    all_alerts = []

    for feed in NEWS_FEEDS:
        headlines = fetch_rss_headlines(feed["url"], feed["name"])
        for h in headlines:
            severity, sectors, stocks, keywords = analyze_headline_for_panic(
                h["title"], h.get("description", "")
            )
            if severity >= 5:  # Only report medium+ severity
                if severity >= 8:
                    action = "EXIT_POSITIONS"
                elif severity >= 6:
                    action = "BLOCK_ENTRIES"
                else:
                    action = "MONITOR"

                alert = NewsAlert(
                    headline=h["title"],
                    source=h["source"],
                    timestamp=h.get("date", datetime.now().strftime("%Y-%m-%d %H:%M")),
                    severity=severity,
                    affected_sectors=sectors,
                    affected_stocks=stocks,
                    panic_keywords_found=keywords,
                    action=action,
                )
                all_alerts.append(alert)

    all_alerts.sort(key=lambda a: a.severity, reverse=True)

    # Cache results
    try:
        cache = [a.to_dict() for a in all_alerts[:20]]
        with open(NEWS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"scanned_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "alerts": cache}, f, indent=2)
    except Exception:
        pass

    return all_alerts


def get_blocked_sectors() -> set:
    """Returns set of sectors currently blocked due to panic news."""
    if not os.path.exists(PANIC_STATE_FILE):
        return set()
    try:
        with open(PANIC_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Only respect blocks from today
        blocked_date = data.get("date", "")
        if blocked_date != datetime.now().strftime("%Y-%m-%d"):
            return set()
        return set(data.get("blocked_sectors", []))
    except Exception:
        return set()


def get_blocked_stocks() -> set:
    """Returns set of individual stocks blocked due to panic news."""
    blocked_sectors = get_blocked_sectors()
    blocked_stocks = set()
    for sector in blocked_sectors:
        blocked_stocks.update(SECTOR_STOCKS.get(sector, []))
    return blocked_stocks


def update_panic_state(alerts: list[NewsAlert]):
    """Saves current panic blocks to disk for the daemon to read."""
    blocked_sectors = set()
    for a in alerts:
        if a.action in ("EXIT_POSITIONS", "BLOCK_ENTRIES"):
            blocked_sectors.update(a.affected_sectors)

    state = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "blocked_sectors": list(blocked_sectors),
        "alerts": [a.to_dict() for a in alerts[:10] if a.severity >= 6],
    }
    try:
        with open(PANIC_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass


def is_stock_blocked_by_news(symbol: str) -> tuple[bool, str]:
    """
    Check if a specific stock is blocked due to panic news.
    Returns: (is_blocked, reason_string)
    """
    blocked_stocks = get_blocked_stocks()
    if symbol in blocked_stocks:
        sector = STOCK_TO_SECTOR.get(symbol, "UNKNOWN")
        return True, f"Blocked by News Panic Radar: Sector {sector} under threat"
    return False, ""


def should_exit_position(symbol: str) -> tuple[bool, str]:
    """
    Check if an open position should be force-exited due to severe panic news (severity >= 8).
    """
    if not os.path.exists(PANIC_STATE_FILE):
        return False, ""
    try:
        with open(PANIC_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("date") != datetime.now().strftime("%Y-%m-%d"):
            return False, ""
        for alert in data.get("alerts", []):
            if alert.get("action") == "EXIT_POSITIONS" and symbol in alert.get("affected_stocks", []):
                return True, f"EMERGENCY EXIT: {alert['headline'][:80]}"
    except Exception:
        pass
    return False, ""
