"""Upcoming earnings dates via yfinance, with in-memory TTL cache."""

import logging
import time
from datetime import date

log = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 24 * 60 * 60

_cache: dict[str, tuple[float, str | None]] = {}


def _now() -> float:
    return time.time()


def _today() -> date:
    return date.today()


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

    Cached in-memory for 24 hours per symbol; failures (network, parsing,
    no upcoming date) cache as ``None`` so we don't retry tight loops.
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

    out: dict[str, str | None] = {}
    missing: list[str] = []
    now = _now()
    for sym in cleaned:
        cached = _cache.get(sym)
        if cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
            out[sym] = cached[1]
        else:
            missing.append(sym)

    if missing:
        try:
            import yfinance as yf
        except Exception as e:
            log.warning("yfinance import failed: %s", e)
            for sym in missing:
                _cache[sym] = (now, None)
                out[sym] = None
            return out

        for sym in missing:
            try:
                ticker = yf.Ticker(sym)
                next_date = _next_future_earnings_date(ticker)
            except Exception as e:
                log.warning("earnings fetch failed for %s: %s", sym, e)
                next_date = None
            _cache[sym] = (now, next_date)
            out[sym] = next_date

    return out


def _clear_cache_for_tests() -> None:
    """Test-only helper to reset the cache."""
    _cache.clear()
