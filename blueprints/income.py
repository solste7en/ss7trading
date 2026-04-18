"""Income P&L and recovery routes."""

import logging
import threading

from flask import Blueprint, jsonify, request

from core.db import (
    dismiss_recovery,
    get_income_stats,
    get_income_trade_ids_filtered,
    get_income_trades,
    get_income_weekly_timeseries,
)
from core.income_range import resolve_income_date_range
from services.income_sync import run_sync as run_income_sync
from services.recovery import (
    attach_recovery_summaries,
    compute_recovery,
    recovery_summary_by_ticker,
    sum_recovery_pnl_filtered,
)

log = logging.getLogger(__name__)
bp = Blueprint("income", __name__)

_income_sync_lock = threading.Lock()


def _income_date_bounds_from_request():
    preset = request.args.get("range", "all").strip()
    df = request.args.get("date_from", "").strip()
    dte = request.args.get("date_to", "").strip()
    a, b = resolve_income_date_range(preset, df, dte)
    return (a or ""), (b or "")


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
        date_from, date_to = _income_date_bounds_from_request()
        data = get_income_trades(
            page, limit, ticker, status, strategy, outcome, sort_by, sort_dir, date_from, date_to
        )
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
        date_from, date_to = _income_date_bounds_from_request()
        stats = get_income_stats(ticker, status, strategy, outcome, date_from, date_to)
        rec_totals = sum_recovery_pnl_filtered(ticker, status, strategy, outcome, date_from, date_to)
        stats["total_recovery_pnl"] = rec_totals["recovery_pnl"]
        stats["total_true_recovery_pnl"] = rec_totals["true_recovery_pnl"]
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


@bp.route("/api/income/timeseries")
def api_income_timeseries():
    """Weekly premium volume + cumulative realized option P&L for the chart.

    Uses the same ``range`` / ``date_from`` / ``date_to`` / filters as stats."""
    try:
        ticker = request.args.get("ticker", "").strip().upper()
        status = request.args.get("status", "").strip()
        strategy = request.args.get("strategy", "").strip()
        outcome = request.args.get("outcome", "").strip()
        date_from, date_to = _income_date_bounds_from_request()
        data = get_income_weekly_timeseries(
            ticker, status, strategy, outcome, date_from, date_to
        )
        return jsonify(data)
    except Exception as e:
        log.exception("API error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/income/recovery-summary")
def api_income_recovery_summary():
    """Per-ticker recovery progress summary, filtered by the same date window
    used elsewhere in the income P&L tab."""
    try:
        ticker = request.args.get("ticker", "").strip().upper()
        strategy = request.args.get("strategy", "").strip()
        outcome = request.args.get("outcome", "").strip()
        date_from, date_to = _income_date_bounds_from_request()

        rows = get_income_trade_ids_filtered(ticker, "assigned", strategy, outcome,
                                             date_from, date_to)
        ticker_to_tids: dict = {}
        for r in rows:
            u = (r.get("underlying") or "").upper()
            if u:
                ticker_to_tids.setdefault(u, []).append(r["id"])

        summary = recovery_summary_by_ticker(ticker_to_tids)
        return jsonify({"summary": summary})
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
