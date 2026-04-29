"""Canonical option-symbol parsing.

Handles the formats Schwab returns across endpoints:
  - CSV exports:        ``NVDA 03/27/2026 177.50 P``
  - OSI/OCC API symbols: ``NVDA  260327P00177500`` (underlying space-padded
    to 6 chars, then ``YYMMDD``, ``C``/``P``, 8-digit strike scaled by 1000).

A single ``parse_option_symbol`` covers both. ``parse_occ_pair`` is a thin
compatibility shim returning ``(expiry, strike)`` for callers that only need
those two fields.
"""

import re
from datetime import datetime

_CSV_RE = re.compile(r"^(\S+)\s+(\d{2}/\d{2}/\d{4})\s+([\d.]+)\s+([CP])$")
_OSI_RE = re.compile(r"^([A-Z/]+)\s+(\d{6})([CP])(\d{8})$")


def parse_option_symbol(symbol):
    """Parse either CSV-export or OSI/OCC-API option symbols.

    Returns ``{underlying, option_expiry, option_strike, option_type}``
    (expiry as ISO ``YYYY-MM-DD``, strike as float, type as ``CALL``/``PUT``)
    or ``None`` if the input is not a recognized option symbol.
    """
    if not symbol:
        return None
    s = symbol.strip()

    m = _CSV_RE.match(s)
    if m:
        return {
            "underlying":    m.group(1),
            "option_expiry": datetime.strptime(m.group(2), "%m/%d/%Y").strftime("%Y-%m-%d"),
            "option_strike": float(m.group(3)),
            "option_type":   "CALL" if m.group(4) == "C" else "PUT",
        }

    m = _OSI_RE.match(s)
    if m:
        try:
            expiry = datetime.strptime("20" + m.group(2), "%Y%m%d").strftime("%Y-%m-%d")
        except ValueError:
            return None
        return {
            "underlying":    m.group(1),
            "option_expiry": expiry,
            "option_strike": int(m.group(4)) / 1000.0,
            "option_type":   "CALL" if m.group(3) == "C" else "PUT",
        }

    return None


def parse_occ_pair(symbol):
    """Compatibility shim: returns ``(option_expiry, option_strike)`` or
    ``(None, None)`` for non-options. Used by ``services.positions``.

    Pads the input with trailing spaces to tolerate symbols truncated by
    upstream data sources, matching the historical ``parse_occ`` behavior.
    """
    if not symbol:
        return None, None
    parsed = parse_option_symbol(symbol.ljust(21)[:21])
    if not parsed:
        return None, None
    return parsed["option_expiry"], parsed["option_strike"]
