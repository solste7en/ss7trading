"""Resolve Income P&L date-range presets (used by /api/income/*)."""

from __future__ import annotations

import datetime as dt
from typing import Optional, Tuple


def resolve_income_date_range(
    preset: str,
    date_from: str = "",
    date_to: str = "",
) -> Tuple[Optional[str], Optional[str]]:
    """Return inclusive (date_from, date_to) as YYYY-MM-DD, or (None, None) = no filter.

    ``custom`` uses ``date_from`` / ``date_to`` query params when both are set.
    """
    p = (preset or "all").strip().lower().replace("-", "_")
    today = dt.date.today()

    if p == "custom":
        df = (date_from or "").strip()
        dte = (date_to or "").strip()
        if df and dte:
            if df > dte:
                df, dte = dte, df
            return df, dte
        return None, None

    if p in ("", "all"):
        return None, None

    if p == "last_7_days":
        return (today - dt.timedelta(days=6)).isoformat(), today.isoformat()
    if p == "last_30_days":
        return (today - dt.timedelta(days=29)).isoformat(), today.isoformat()
    if p == "last_90_days":
        return (today - dt.timedelta(days=89)).isoformat(), today.isoformat()
    if p == "last_365_days":
        return (today - dt.timedelta(days=364)).isoformat(), today.isoformat()

    if p in ("mtd", "current_month"):
        return today.replace(day=1).isoformat(), today.isoformat()

    if p in ("prev_month", "previous_month"):
        first_this = today.replace(day=1)
        last_prev = first_this - dt.timedelta(days=1)
        start_prev = last_prev.replace(day=1)
        return start_prev.isoformat(), last_prev.isoformat()

    if p in ("last_6_months", "last6months"):
        return (today - dt.timedelta(days=182)).isoformat(), today.isoformat()

    if p in ("ytd", "current_year"):
        return today.replace(month=1, day=1).isoformat(), today.isoformat()

    if p in ("last_year", "previous_year", "last_calendar_year"):
        y = today.year - 1
        return dt.date(y, 1, 1).isoformat(), dt.date(y, 12, 31).isoformat()

    return None, None
