"""Position-related API routes."""

import logging

import schwab
from flask import Blueprint, jsonify, request

from auth import get_client
from db import (
    create_position_list,
    delete_position_list,
    get_position_lists,
    get_recent_equity_trade_metrics_batch,
    rename_position_list,
    reorder_position_lists,
    reorder_symbols_within_list,
    resolve_position_assignments,
    upsert_position_assignments,
)
from services.positions import clean_positions, position_list_ids

log = logging.getLogger(__name__)
bp = Blueprint("positions", __name__)


@bp.route("/api/test")
def api_test():
    """Quick connectivity test."""
    try:
        client = get_client()
        resp = client.get_account_numbers()
        resp.raise_for_status()
        return jsonify({"status": "ok", "data": resp.json()})
    except Exception as e:
        log.exception("API error")
        return jsonify({"status": "error", "error": str(e)}), 500


@bp.route("/api/positions")
def api_positions():
    try:
        client = get_client()
        resp = client.get_accounts(fields=[schwab.client.Client.Account.Fields.POSITIONS])
        resp.raise_for_status()
        return jsonify(clean_positions(resp.json()))
    except Exception as e:
        log.exception("API error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/position-lists", methods=["GET", "POST"])
def api_position_lists():
    if request.method == "GET":
        return jsonify({"lists": get_position_lists()})
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    try:
        return jsonify(create_position_list(name))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/api/position-lists/<int:list_id>", methods=["PATCH", "DELETE"])
def api_position_list_item(list_id):
    if request.method == "PATCH":
        data = request.get_json() or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name required"}), 400
        try:
            rename_position_list(list_id, name)
            return jsonify({"ok": True})
        except LookupError:
            return jsonify({"error": "list not found"}), 404
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    try:
        delete_position_list(list_id)
        return jsonify({"ok": True})
    except LookupError:
        return jsonify({"error": "list not found"}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/api/position-assignments/resolve", methods=["POST"])
def api_position_assignments_resolve():
    """Infer and persist missing symbol->list rows."""
    data = request.get_json() or {}
    underlyings = data.get("underlyings") or []
    short_equity = data.get("short_equity") or []
    if not isinstance(underlyings, list):
        return jsonify({"error": "underlyings must be a list"}), 400
    if not isinstance(short_equity, list):
        return jsonify({"error": "short_equity must be a list"}), 400
    try:
        result = resolve_position_assignments(underlyings, short_equity)
        return jsonify(result)
    except Exception as e:
        log.exception("position assignments resolve")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/position-lists/reorder", methods=["PUT"])
def api_position_lists_reorder():
    data = request.get_json() or {}
    order = data.get("order") or data.get("ids")
    if not isinstance(order, list):
        return jsonify({"error": "order must be a list of list ids"}), 400
    try:
        reorder_position_lists(order)
        return jsonify({"ok": True, "lists": get_position_lists()})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/api/position-assignments/order", methods=["PUT"])
def api_position_assignments_order():
    data = request.get_json() or {}
    list_id = data.get("list_id")
    symbols = data.get("symbols")
    if list_id is None or not isinstance(symbols, list):
        return jsonify({"error": "list_id and symbols required"}), 400
    try:
        updated = reorder_symbols_within_list(int(list_id), symbols)
        return jsonify({"ok": True, "symbol_sort": updated})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/api/position-assignments", methods=["PUT"])
def api_position_assignments_put():
    data = request.get_json() or {}
    if not isinstance(data, dict):
        return jsonify({"error": "expected JSON object symbol -> list_id"}), 400
    valid_ids = position_list_ids()
    clean = {}
    for k, v in data.items():
        sym = (k or "").strip().upper()
        if not sym:
            continue
        try:
            lid = int(v)
        except (TypeError, ValueError):
            return jsonify({"error": f"invalid list_id for {sym}"}), 400
        if lid not in valid_ids:
            return jsonify({"error": f"unknown list_id {lid}"}), 400
        clean[sym] = lid
    if not clean:
        return jsonify({"error": "no assignments"}), 400
    try:
        upsert_position_assignments(clean)
        return jsonify({"ok": True})
    except Exception as e:
        log.exception("position assignments put")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/position-recent-metrics", methods=["POST"])
def api_position_recent_metrics():
    data = request.get_json() or {}
    symbols = data.get("symbols") or []
    if not isinstance(symbols, list):
        return jsonify({"error": "symbols must be a list"}), 400
    try:
        metrics = get_recent_equity_trade_metrics_batch(symbols)
        return jsonify({"metrics": metrics})
    except Exception as e:
        log.exception("position recent metrics")
        return jsonify({"error": str(e)}), 500
