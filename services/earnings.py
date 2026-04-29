"""Upcoming earnings dates via yfinance, with SQLite-backed TTL cache.

TTLs:
  - Symbol returned a real date  → 7 days
  - Symbol returned no date (ETF, delisted, not yet announced) → 90 days
"""

import logging
import sqlite3
import time
from datetime import date

log = logging.getLogger(__name__)

NO_EARNINGS_TTL  = 90 * 24 * 3600   # 90 days for null results
HAS_EARNINGS_TTL =  7 * 24 * 3600   # 7 days for real dates

# Set to a file path in tests to avoid touching the real DB.
_DB_PATH_OVERRIDE: str | None = None


def _db_path() -> str:
    if _DB_PATH_OVERRIDE:
        return _DB_PATH_OVERRIDE
    from core.config import DB_PATH
    return DB_PATH


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS earnings_cache (
            symbol        TEXT PRIMARY KEY,
            earnings_date TEXT,
            fetched_at    REAL NOT NULL
        )
    """)
    conn.commit()
    return conn


def _now() -> float:
    return time.time()


def _today() -> date:
    return date.today()


def _ttl_for(earnings_date: str | None) -> float:
    return NO_EARNINGS_TTL if earnings_date is None else HAS_EARNINGS_TTL


def _next_future_earnings_date(ticker) -> str | None:
    """Pick the soonest earnings date >= today from a yfinance Ticker."""
    today = _today()

    try:
        df = ticker.get_earnings_dates(limit=8)
    except Exception as e:
        log.debug("get_earnings_dates failed: %s", e)
        df = None

    if df is not None and getattr(df, "empty", True) is False:
        candidates: list[date] = []
        for ts in df.index:
            try:
                d = ts.date() if hasattr(ts, "date") else date.fromisoformat(str(ts)[:10])
            except Exception:
                continue
            if d >= today:
                candidates.append(d)
        if candidates:
            return min(candidates).isoformat()

    # Fallback: ticker.calendar (older yfinance shape)
    try:
        cal = ticker.calendar
    except Exception:
        cal = None
    if cal is not None:
        raw = None
        if isinstance(cal, dict):
            raw = cal.get("Earnings Date")
        else:
            try:
                raw = cal.loc["Earnings Date"]
            except Exception:
                raw = None
        if raw is not None:
            seq = raw if isinstance(raw, (list, tuple)) else [raw]
            best = None
            for v in seq:
                try:
                    d = v.date() if hasattr(v, "date") else date.fromisoformat(str(v)[:10])
                except Exception:
                    continue
                if d >= today and (best is None or d < best):
                    best = d
            if best:
                return best.isoformat()

    return None


def get_next_earnings(symbols: list[str]) -> dict[str, str | None]:
    """Return ``{symbol: next_earnings_iso_date_or_None}`` for each symbol.

    Cached in SQLite with TTL: 7 days for real dates, 90 days for null
    results (ETFs, delisted, or not-yet-announced).
    """
    if not symbols:
        return {}

    cleaned = []
    seen = set()
    for s in symbols:
        if not s:
            continue
        u = s.upper().strip()
        if u and u not in seen:
            seen.add(u)
            cleaned.append(u)

    now = _now()
    out: dict[str, str | None] = {}
    missing: list[str] = []

    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT symbol, earnings_date, fetched_at FROM earnings_cache WHERE symbol IN ({})".format(
                ",".join("?" * len(cleaned))
            ),
            cleaned,
        ).fetchall()

        cached: dict[str, tuple[str | None, float]] = {
            row[0]: (row[1], row[2]) for row in rows
        }

        for sym in cleaned:
            if sym in cached:
                earnings_date, fetched_at = cached[sym]
                ttl = _ttl_for(earnings_date)
                if (now - fetched_at) < ttl:
                    out[sym] = earnings_date
                else:
                    missing.append(sym)
            else:
                missing.append(sym)

        if missing:
            fetched = _fetch_batch(missing)
            out.update(fetched)
            _upsert_many_conn(conn, missing, fetched, now)
    finally:
        conn.close()

    return out


def _fetch_batch(symbols: list[str]) -> dict[str, str | None]:
    """Fetch next earnings dates for *symbols* via yfinance in a single batch."""
    try:
        import yfinance as yf
    except Exception as e:
        log.warning("yfinance import failed: %s", e)
        return {s: None for s in symbols}

    result: dict[str, str | None] = {}
    try:
        tickers = yf.Tickers(" ".join(symbols))
    except Exception as e:
        log.warning("yfinance Tickers init failed: %s", e)
        return {s: None for s in symbols}

    for sym in symbols:
        try:
            ticker = tickers.tickers[sym]
            result[sym] = _next_future_earnings_date(ticker)
        except Exception as e:
            log.warning("earnings fetch failed for %s: %s", sym, e)
            result[sym] = None
    return result


def _upsert_many_conn(conn: sqlite3.Connection, symbols: list[str],
                      values: dict[str, str | None], now: float) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO earnings_cache (symbol, earnings_date, fetched_at) VALUES (?, ?, ?)",
        [(sym, values.get(sym), now) for sym in symbols],
    )
    conn.commit()


def clear_earnings_cache(symbol: str) -> None:
    """Remove a single symbol from the cache so it will be re-fetched."""
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM earnings_cache WHERE symbol = ?", (symbol.upper(),))
        conn.commit()
    finally:
        conn.close()


def _clear_cache_for_tests() -> None:
    """Test-only helper to reset the cache."""
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM earnings_cache")
        conn.commit()
    finally:
        conn.close()
