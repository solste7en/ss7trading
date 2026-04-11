"""Transaction history and realized G/L routes."""

import datetime
import logging

from flask import Blueprint, jsonify, request

from auth import get_client
from db import get_realized_gains, get_top_tickers, get_transactions
from sync_trades import parse_schwab_transaction

log = logging.getLogger(__name__)
bp = Blueprint("transactions", __name__)


@bp.route("/api/transactions")
def api_transactions():
    try:
        page = max(1, int(request.args.get("page", 1)))
        limit = min(100, max(10, int(request.args.get("limit", 25))))
        category = request.args.get("category", "").strip()
        ticker = request.args.get("ticker", "").strip().upper()
        search = request.args.get("search", "").strip()
        return jsonify(get_transactions(page, limit, category, ticker, search))
    except Exception as e:
        log.exception("API error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/realized_gains")
def api_realized_gains():
    try:
        page = max(1, int(request.args.get("page", 1)))
        limit = min(100, max(10, int(request.args.get("limit", 25))))
        ticker = request.args.get("ticker", "").strip().upper()
        term = request.args.get("term", "").strip()
        return jsonify(get_realized_gains(page, limit, ticker, term))
    except Exception as e:
        log.exception("API error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/top-tickers")
def api_top_tickers():
    """Return the 10 most-traded tickers with their last 5 executed trades."""
    try:
        return jsonify(get_top_tickers(top_n=10, recent_n=10))
    except Exception as e:
        log.exception("API error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/transactions/live")
def api_transactions_live():
    """Fetch recent transactions from Schwab API and compare against local DB."""
    try:
        ticker = request.args.get("ticker", "").strip().upper()
        days = max(7, min(365, int(request.args.get("days", 180))))
        if not ticker:
            return jsonify({"error": "ticker is required"}), 400

        client = get_client()
        resp = client.get_account_numbers()
        resp.raise_for_status()
        acct_hash = resp.json()[0]["hashValue"]

        end_dt = datetime.datetime.now(datetime.UTC)
        start_dt = end_dt - datetime.timedelta(days=days)

        resp = client.get_transactions(
            account_hash=acct_hash,
            start_date=start_dt,
            end_date=end_dt,
        )
        resp.raise_for_status()
        raw_txs = resp.json() or []

        api_rows = []
        for raw in raw_txs:
            try:
                parsed = parse_schwab_transaction(raw)
                if parsed and parsed.get("underlying") == ticker:
                    api_rows.append(parsed)
            except Exception:
                pass

        api_rows.sort(key=lambda r: r.get("trade_date", ""), reverse=True)

        db_result = get_transactions(page=1, limit=500, ticker=ticker, category="")
        db_rows = [r for r in db_result["data"]
                   if r.get("trade_date", "") >= start_dt.strftime("%Y-%m-%d")]

        return jsonify({
            "ticker": ticker,
            "days": days,
            "api_count": len(api_rows),
            "db_count": len(db_rows),
            "api_rows": api_rows,
            "db_rows": db_rows,
        })
    except Exception as e:
        log.exception("API error")
        return jsonify({"error": str(e)}), 500
