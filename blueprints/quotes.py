"""Quote-related API routes."""

import logging

import schwab
from flask import Blueprint, jsonify, request

from core.auth import get_client
from core.db import get_watchlist_symbols
from services.earnings import get_next_earnings
from services.positions import clean_positions
from services.quotes import clean_quotes

log = logging.getLogger(__name__)
bp = Blueprint("quotes", __name__)

_MAX_BATCH = 50


def _parse_symbols_param() -> list[str]:
    raw = request.args.get("symbols", "") or ""
    out = []
    seen = set()
    for tok in raw.split(","):
        s = tok.strip().upper()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


@bp.route("/api/quotes")
def api_quotes():
    """Returns quotes for all symbols currently held."""
    try:
        client = get_client()
        resp = client.get_accounts(fields=[schwab.client.Client.Account.Fields.POSITIONS])
        resp.raise_for_status()
        positions = clean_positions(resp.json())

        symbols = set()
        for p in positions:
            sym = p["symbol"]
            if p["asset_type"] == "OPTION":
                underlying = sym.split()[0] if " " in sym else sym
                symbols.add(underlying)
            elif p["asset_type"] not in ("CASH_EQUIVALENT",):
                symbols.add(sym)

        if not symbols:
            return jsonify([])

        resp = client.get_quotes(list(symbols))
        resp.raise_for_status()
        return jsonify(clean_quotes(resp.json()))
    except Exception as e:
        log.exception("API error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/quote/<symbol>")
def api_quote_single(symbol):
    """Quote a single symbol."""
    try:
        client = get_client()
        resp = client.get_quotes([symbol.upper()])
        resp.raise_for_status()
        data = clean_quotes(resp.json())
        return jsonify(data[0] if data else {})
    except Exception as e:
        log.exception("API error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/quotes/list/<int:list_id>")
def api_quotes_for_list(list_id):
    """Returns live quotes for all symbols in a watchlist."""
    try:
        symbols = get_watchlist_symbols(list_id)
        if not symbols:
            return jsonify([])
        client = get_client()
        resp = client.get_quotes(symbols)
        resp.raise_for_status()
        return jsonify(clean_quotes(resp.json()))
    except Exception as e:
        log.exception("API error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/quotes/symbols")
def api_quotes_symbols():
    """Generic batch quote endpoint: ``?symbols=AAPL,NVDA,...``."""
    symbols = _parse_symbols_param()
    if not symbols:
        return jsonify([])
    if len(symbols) > _MAX_BATCH:
        return jsonify({"error": f"Too many symbols (max {_MAX_BATCH})."}), 400
    try:
        client = get_client()
        resp = client.get_quotes(symbols)
        resp.raise_for_status()
        return jsonify(clean_quotes(resp.json()))
    except Exception as e:
        log.exception("API error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/earnings")
def api_earnings():
    """Next earnings date per symbol via yfinance, cached 24h."""
    symbols = _parse_symbols_param()
    if not symbols:
        return jsonify({})
    if len(symbols) > _MAX_BATCH:
        return jsonify({"error": f"Too many symbols (max {_MAX_BATCH})."}), 400
    try:
        return jsonify(get_next_earnings(symbols))
    except Exception as e:
        log.exception("API error")
        return jsonify({"error": str(e)}), 500
