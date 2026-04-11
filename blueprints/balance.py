"""Account balance API (Schwab get_accounts, balances by default — no positions-only filter)."""

import logging

from flask import Blueprint, jsonify

from core.auth import get_client
from core.db import get_balance_history, get_balance_snapshot_status, save_balance_snapshot
from services.accounts import clean_accounts_balance

log = logging.getLogger(__name__)
bp = Blueprint("balance", __name__)


@bp.route("/api/account-balances")
def api_account_balances():
    """Return cleaned per-account balance snapshots (current / projected / initial)."""
    try:
        client = get_client()
        resp = client.get_accounts()
        resp.raise_for_status()
        raw = resp.json() or []
        return jsonify(clean_accounts_balance(raw if isinstance(raw, list) else [raw]))
    except Exception as e:
        log.exception("API error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/account-balances/snapshot", methods=["POST"])
def api_balance_snapshot():
    """
    Fetch live balances from Schwab and persist key metrics to balance_snapshots.
    INSERT OR IGNORE means only the first call of the day actually writes rows;
    subsequent calls return already_saved=True without touching the DB.
    """
    try:
        client = get_client()
        resp = client.get_accounts()
        resp.raise_for_status()
        raw = resp.json() or []
        cleaned = clean_accounts_balance(raw if isinstance(raw, list) else [raw])
        result = save_balance_snapshot(cleaned["accounts"], cleaned["aggregated_balance"])
        return jsonify(result)
    except Exception as e:
        log.exception("Snapshot error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/account-balances/snapshot/status")
def api_balance_snapshot_status():
    """Return whether today's snapshot has been saved and the saved_at timestamp."""
    try:
        saved_at = get_balance_snapshot_status()
        return jsonify({"saved_at": saved_at, "already_saved": saved_at is not None})
    except Exception as e:
        log.exception("Snapshot status error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/account-balances/history")
def api_balance_history():
    """Return historical daily balance snapshots (aggregate row, newest first)."""
    try:
        return jsonify({"history": get_balance_history()})
    except Exception as e:
        log.exception("Balance history error")
        return jsonify({"error": str(e)}), 500
