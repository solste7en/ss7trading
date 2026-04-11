"""Trade sync routes."""

import datetime
import logging
import threading

from flask import Blueprint, jsonify

from db import get_most_traded_ticker, get_trade_sync_time, set_trade_sync_time
from migrate_db import get_pending_migrations
from sync_trades import sync as run_trade_sync

log = logging.getLogger(__name__)
bp = Blueprint("sync", __name__)

_trades_sync_lock = threading.Lock()


@bp.route("/api/trades/last-sync")
def api_trades_last_sync():
    """Return the last trade sync timestamp and most-traded ticker."""
    try:
        return jsonify({
            "last_synced": get_trade_sync_time(),
            "most_traded_ticker": get_most_traded_ticker(),
        })
    except Exception as e:
        log.exception("API error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/trades/sync", methods=["POST"])
def api_trades_sync():
    """Trigger a trade sync from Schwab API."""
    if not _trades_sync_lock.acquire(blocking=False):
        return jsonify({"error": "Sync already in progress"}), 409

    try:
        pending = get_pending_migrations()
        if pending:
            desc = "; ".join(f"[{v}] {d}" for v, d in pending)
            return jsonify({
                "error": f"DB schema is out of date — run `python migrate_db.py` first. "
                         f"Pending: {desc}"
            }), 400

        last_synced = get_trade_sync_time()
        if last_synced:
            from datetime import datetime as _dt
            last_dt = _dt.fromisoformat(last_synced)
            days_since = (datetime.datetime.utcnow() - last_dt).days
            lookback = min(365, max(2, days_since + 3))
        else:
            lookback = 7

        result = run_trade_sync(lookback_days=lookback, check_schema=False)
        set_trade_sync_time()

        return jsonify({
            "ok": True,
            "lookback_days": lookback,
            "last_synced": get_trade_sync_time(),
            "most_traded_ticker": get_most_traded_ticker(),
            **result,
        })
    except Exception as e:
        log.exception("API error")
        return jsonify({"error": str(e)}), 500
    finally:
        _trades_sync_lock.release()
