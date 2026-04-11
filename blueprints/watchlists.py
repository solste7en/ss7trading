"""Watchlist API routes."""

import logging

from flask import Blueprint, jsonify, request

from core.db import (
    add_watchlist_symbol,
    create_watchlist,
    delete_watchlist,
    get_watchlist_symbols,
    get_watchlists,
    remove_watchlist_symbol,
)

log = logging.getLogger(__name__)
bp = Blueprint("watchlists", __name__)


@bp.route("/api/watchlists", methods=["GET", "POST"])
def api_watchlists():
    if request.method == "GET":
        return jsonify(get_watchlists())
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    try:
        return jsonify(create_watchlist(name))
    except Exception as e:
        return jsonify({"error": str(e)}), 409


@bp.route("/api/watchlists/<int:list_id>", methods=["DELETE"])
def api_watchlist_delete(list_id):
    try:
        delete_watchlist(list_id)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/watchlists/<int:list_id>/symbols", methods=["GET", "POST"])
def api_watchlist_symbols(list_id):
    if request.method == "GET":
        return jsonify(get_watchlist_symbols(list_id))
    data = request.get_json() or {}
    sym = (data.get("symbol") or "").strip().upper()
    if not sym:
        return jsonify({"error": "symbol required"}), 400
    add_watchlist_symbol(list_id, sym)
    return jsonify({"ok": True})


@bp.route("/api/watchlists/<int:list_id>/symbols/<symbol>", methods=["DELETE"])
def api_watchlist_symbol_delete(list_id, symbol):
    remove_watchlist_symbol(list_id, symbol.upper())
    return jsonify({"ok": True})
