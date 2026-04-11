"""Income P&L and recovery routes."""

import logging
import threading

from flask import Blueprint, jsonify, request

from db import dismiss_recovery, get_income_stats, get_income_trades
from income_sync import run_sync as run_income_sync
from recovery import attach_recovery_summaries, compute_recovery, sum_recovery_pnl_filtered

log = logging.getLogger(__name__)
bp = Blueprint("income", __name__)

_income_sync_lock = threading.Lock()


@bp.route("/api/income/sync", methods=["POST"])
def api_income_sync():
    """Trigger a full re-sync of income trades from Schwab API."""
    if not _income_sync_lock.acquire(blocking=False):
        return jsonify({"error": "Sync already in progress"}), 409
    try:
        result = run_income_sync()
        return jsonify({"ok": True, **result})
    except Exception as e:
        log.exception("API error")
        return jsonify({"error": str(e)}), 500
    finally:
        _income_sync_lock.release()


@bp.route("/api/income/trades")
def api_income_trades():
    """Paginated income trades with optional filters."""
    try:
        page = max(1, int(request.args.get("page", 1)))
        limit = min(100, max(10, int(request.args.get("limit", 25))))
        ticker = request.args.get("ticker", "").strip().upper()
        status = request.args.get("status", "").strip()
        strategy = request.args.get("strategy", "").strip()
        outcome = request.args.get("outcome", "").strip()
        sort_by = request.args.get("sort_by", "open_date").strip()
        sort_dir = request.args.get("sort_dir", "desc").strip()
        data = get_income_trades(page, limit, ticker, status, strategy, outcome, sort_by, sort_dir)
        attach_recovery_summaries(data["data"])
        return jsonify(data)
    except Exception as e:
        log.exception("API error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/income/stats")
def api_income_stats():
    """Aggregate KPI stats for income trades."""
    try:
        ticker = request.args.get("ticker", "").strip().upper()
        status = request.args.get("status", "").strip()
        strategy = request.args.get("strategy", "").strip()
        outcome = request.args.get("outcome", "").strip()
        stats = get_income_stats(ticker, status, strategy, outcome)
        stats["total_recovery_pnl"] = sum_recovery_pnl_filtered(ticker, status, strategy, outcome)
        return jsonify(stats)
    except Exception as e:
        log.exception("API error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/income/recovery")
def api_income_recovery():
    """Recovery progress for all assigned trades of a ticker."""
    try:
        ticker = request.args.get("ticker", "").strip().upper()
        if not ticker:
            return jsonify({"error": "ticker is required"}), 400
        return jsonify(compute_recovery(ticker))
    except Exception as e:
        log.exception("API error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/income/recovery/<int:trade_id>/dismiss", methods=["POST"])
def api_income_recovery_dismiss(trade_id):
    """Write off remaining unrecovered shares for an assigned trade."""
    try:
        body = request.get_json(force=True) or {}
        qty = int(body.get("qty", 0))
        if qty < 0:
            return jsonify({"error": "qty must be >= 0"}), 400
        dismiss_recovery(trade_id, qty)
        return jsonify({"ok": True, "trade_id": trade_id, "dismissed_qty": qty})
    except Exception as e:
        log.exception("API error")
        return jsonify({"error": str(e)}), 500
