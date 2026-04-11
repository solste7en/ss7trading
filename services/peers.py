"""External peer data via yfinance with SQLite caching."""

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

from core.config import DB_PATH

log = logging.getLogger(__name__)

_CACHE_TTL_HOURS = 24

# Curated sector/industry -> ETF lookup
ETF_MAP = {
    "Technology": {"broad": ["XLK", "VGT"], "sub": {
        "Semiconductors": ["SMH", "SOXX"],
        "Software—Infrastructure": ["IGV"],
        "Software—Application": ["IGV"],
        "Information Technology Services": ["XLK"],
        "Consumer Electronics": ["XLK"],
        "Electronic Components": ["SMH"],
        "Semiconductor Equipment & Materials": ["SMH", "SOXX"],
        "Scientific & Technical Instruments": ["XLK"],
        "Solar": ["TAN"],
        "Communication Equipment": ["XLC"],
    }},
    "Healthcare": {"broad": ["XLV", "VHT"], "sub": {
        "Biotechnology": ["XBI", "IBB"],
        "Drug Manufacturers—General": ["XLV"],
        "Drug Manufacturers—Specialty & Generic": ["XBI"],
        "Medical Devices": ["IHI"],
        "Diagnostics & Research": ["XLV"],
        "Health Information Services": ["IHI"],
        "Medical Instruments & Supplies": ["IHI"],
    }},
    "Financial Services": {"broad": ["XLF", "VFH"], "sub": {
        "Banks—Diversified": ["KBE"],
        "Banks—Regional": ["KRE"],
        "Insurance—Diversified": ["KIE"],
        "Capital Markets": ["XLF"],
        "Credit Services": ["XLF"],
        "Asset Management": ["XLF"],
        "Insurance—Property & Casualty": ["KIE"],
    }},
    "Energy": {"broad": ["XLE", "VDE"], "sub": {
        "Oil & Gas E&P": ["XOP"],
        "Oil & Gas Integrated": ["XLE"],
        "Oil & Gas Midstream": ["AMLP"],
        "Oil & Gas Equipment & Services": ["OIH"],
        "Uranium": ["URA"],
    }},
    "Consumer Cyclical": {"broad": ["XLY", "VCR"], "sub": {
        "Internet Retail": ["IBUY"],
        "Auto Manufacturers": ["CARZ"],
        "Restaurants": ["XLY"],
        "Apparel Retail": ["XRT"],
        "Home Improvement Retail": ["XHB"],
        "Specialty Retail": ["XRT"],
        "Leisure": ["PEJ"],
    }},
    "Consumer Defensive": {"broad": ["XLP", "VDC"], "sub": {
        "Household & Personal Products": ["XLP"],
        "Beverages—Non-Alcoholic": ["XLP"],
        "Packaged Foods": ["XLP"],
        "Discount Stores": ["XRT"],
        "Grocery Stores": ["XLP"],
    }},
    "Industrials": {"broad": ["XLI", "VIS"], "sub": {
        "Aerospace & Defense": ["ITA"],
        "Railroads": ["XLI"],
        "Specialty Industrial Machinery": ["XLI"],
        "Building Products & Equipment": ["XHB"],
        "Trucking": ["XLI"],
        "Waste Management": ["XLI"],
    }},
    "Communication Services": {"broad": ["XLC", "VOX"], "sub": {
        "Internet Content & Information": ["XLC"],
        "Entertainment": ["XLC"],
        "Telecom Services": ["XLC"],
        "Electronic Gaming & Multimedia": ["ESPO"],
        "Advertising Agencies": ["XLC"],
    }},
    "Real Estate": {"broad": ["XLRE", "VNQ"], "sub": {
        "REIT—Residential": ["REZ"],
        "REIT—Industrial": ["XLRE"],
        "REIT—Retail": ["XLRE"],
        "REIT—Office": ["XLRE"],
        "REIT—Healthcare Facilities": ["REZ"],
        "REIT—Diversified": ["VNQ"],
        "Real Estate Services": ["VNQ"],
    }},
    "Utilities": {"broad": ["XLU", "VPU"], "sub": {
        "Utilities—Regulated Electric": ["XLU"],
        "Utilities—Renewable": ["ICLN"],
        "Utilities—Diversified": ["XLU"],
    }},
    "Basic Materials": {"broad": ["XLB", "VAW"], "sub": {
        "Gold": ["GDX"],
        "Silver": ["SIL"],
        "Copper": ["COPX"],
        "Steel": ["SLX"],
        "Specialty Chemicals": ["XLB"],
        "Agricultural Inputs": ["MOO"],
        "Lumber & Wood Production": ["WOOD"],
    }},
}

_NORMALIZE_FIELDS = [
    "sector", "industry", "marketCap", "trailingPE", "forwardPE",
    "priceToBook", "revenueGrowth", "profitMargins", "operatingMargins",
    "returnOnEquity", "beta", "dividendYield", "fiftyTwoWeekHigh",
    "fiftyTwoWeekLow", "currentPrice", "shortName", "longName",
    "fiftyDayAverage", "twoHundredDayAverage",
]


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _connection():
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


def _ensure_peer_cache_table(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS peer_cache (
            symbol      TEXT PRIMARY KEY,
            data_json   TEXT NOT NULL,
            fetched_at  TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_peer_cache_fetched ON peer_cache(fetched_at);
    """)
    conn.commit()


def get_cached_peer_data(symbol, max_age_hours=_CACHE_TTL_HOURS):
    cutoff = (datetime.utcnow() - timedelta(hours=max_age_hours)).strftime("%Y-%m-%d %H:%M:%S")
    with _connection() as conn:
        _ensure_peer_cache_table(conn)
        cur = conn.cursor()
        cur.execute(
            "SELECT data_json FROM peer_cache WHERE symbol = ? AND fetched_at > ?",
            (symbol.upper(), cutoff),
        )
        row = cur.fetchone()
        if row:
            return json.loads(row["data_json"])
    return None


def get_cached_peer_data_batch(symbols, max_age_hours=_CACHE_TTL_HOURS):
    if not symbols:
        return {}
    symbols = [s.upper() for s in symbols]
    cutoff = (datetime.utcnow() - timedelta(hours=max_age_hours)).strftime("%Y-%m-%d %H:%M:%S")
    ph = ",".join("?" for _ in symbols)
    with _connection() as conn:
        _ensure_peer_cache_table(conn)
        cur = conn.cursor()
        cur.execute(
            f"SELECT symbol, data_json FROM peer_cache WHERE symbol IN ({ph}) AND fetched_at > ?",
            symbols + [cutoff],
        )
        return {row["symbol"]: json.loads(row["data_json"]) for row in cur.fetchall()}


def set_cached_peer_data(symbol, data):
    with _connection() as conn:
        _ensure_peer_cache_table(conn)
        conn.execute(
            "INSERT OR REPLACE INTO peer_cache (symbol, data_json, fetched_at) VALUES (?, ?, datetime('now'))",
            (symbol.upper(), json.dumps(data)),
        )
        conn.commit()


def _normalize_info(raw):
    """Extract the fields we care about from yfinance's info dict."""
    out = {}
    for f in _NORMALIZE_FIELDS:
        out[f] = raw.get(f)
    price = out.get("currentPrice")
    high52 = out.get("fiftyTwoWeekHigh")
    low52 = out.get("fiftyTwoWeekLow")
    if price and high52 and high52 > 0:
        out["pct_from_52w_high"] = round((price - high52) / high52 * 100, 2)
    if price and low52 and low52 > 0:
        out["pct_from_52w_low"] = round((price - low52) / low52 * 100, 2)
    return out


def get_ticker_info(symbol):
    """Fetch ticker fundamentals via yfinance, with cache."""
    symbol = symbol.upper().strip()
    cached = get_cached_peer_data(symbol)
    if cached:
        return cached
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        raw = ticker.info or {}
        data = _normalize_info(raw)
        set_cached_peer_data(symbol, data)
        return data
    except Exception as e:
        log.warning("yfinance fetch failed for %s: %s", symbol, e)
        return {}


def get_ticker_info_batch(symbols):
    """Fetch info for multiple symbols, using cache where available."""
    if not symbols:
        return {}
    symbols = list({s.upper().strip() for s in symbols if s})
    cached = get_cached_peer_data_batch(symbols)
    missing = [s for s in symbols if s not in cached]
    result = dict(cached)

    if missing:
        try:
            import yfinance as yf
            tickers = yf.Tickers(" ".join(missing))
            for sym in missing:
                try:
                    raw = tickers.tickers[sym].info or {}
                    data = _normalize_info(raw)
                    set_cached_peer_data(sym, data)
                    result[sym] = data
                except Exception as e:
                    log.warning("yfinance batch fetch failed for %s: %s", sym, e)
                    result[sym] = {}
        except Exception as e:
            log.warning("yfinance batch init failed: %s", e)
            for sym in missing:
                result[sym] = {}

    return result


def get_etf_alternatives(sector, industry=None):
    """Return relevant ETFs for a given sector/industry."""
    sector_data = ETF_MAP.get(sector, {})
    if not sector_data:
        return []
    broad = sector_data.get("broad", [])
    sub_map = sector_data.get("sub", {})
    specific = sub_map.get(industry, []) if industry else []
    seen = set()
    etfs = []
    for sym in specific + broad:
        if sym not in seen:
            etfs.append(sym)
            seen.add(sym)
    return etfs


def get_peers(symbol, all_info=None):
    """Find peer tickers from the same sector/industry in the portfolio."""
    info = (all_info or {}).get(symbol.upper(), {})
    sector = info.get("sector")
    industry = info.get("industry")
    if not sector:
        return {"sector": None, "industry": None, "peers": [], "etfs": []}
    peers = []
    for sym, sym_info in (all_info or {}).items():
        if sym == symbol.upper():
            continue
        if sym_info.get("industry") == industry:
            peers.append({"symbol": sym, "match": "industry"})
        elif sym_info.get("sector") == sector:
            peers.append({"symbol": sym, "match": "sector"})
    etfs = get_etf_alternatives(sector, industry)
    return {"sector": sector, "industry": industry, "peers": peers, "etfs": etfs}
