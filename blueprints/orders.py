"""Order placement and management routes."""

import datetime
import logging

from flask import Blueprint, jsonify, request

from core.auth import get_client
from services.orders import build_equity_order, build_multi_leg_order, build_option_order, clean_orders
from services.schwab_client import get_account_hash, throttled_order_call

log = logging.getLogger(__name__)
bp = Blueprint("orders", __name__)


@bp.route("/api/orders")
def api_orders():
    """Return all open/working orders across linked accounts."""
    try:
        client = get_client()
        now = datetime.datetime.now(datetime.UTC)
        resp = client.get_orders_for_all_linked_accounts(
            from_entered_datetime=now - datetime.timedelta(days=90),
            to_entered_datetime=now + datetime.timedelta(days=1),
        )
        resp.raise_for_status()
        raw = resp.json() or []
        open_statuses = {
            "WORKING", "QUEUED", "PENDING_ACTIVATION", "ACCEPTED",
            "AWAITING_PARENT_ORDER", "AWAITING_CONDITION",
            "NEW", "PENDING_ACKNOWLEDGEMENT",
        }
        orders = [o for o in raw if o.get("status", "") in open_statuses]
        return jsonify(clean_orders(orders))
    except Exception as e:
        log.exception("API error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/order", methods=["POST"])
def api_place_order():
    """Place an equity or single-leg option order."""
    try:
        data = request.json or {}
        trade_type = data.get("trade_type")

        if trade_type == "equity":
            order = build_equity_order(data)
        elif trade_type == "option":
            order = build_option_order(data)
        else:
            return jsonify({"error": f"Unknown trade_type: {trade_type!r}"}), 400

        account_hash = get_account_hash()
        client = get_client()
        resp = throttled_order_call(client.place_order, account_hash, order)
        resp.raise_for_status()

        location = resp.headers.get("Location", "")
        order_id = location.rstrip("/").split("/")[-1] if location else "\u2014"
        return jsonify({"status": "ok", "order_id": order_id})

    except Exception as e:
        log.exception("API error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/order/ladder", methods=["POST"])
def api_place_ladder():
    """Place a ladder of limit orders -- one per rung."""
    try:
        data = request.json or {}
        trade_type = data.get("trade_type", "equity")
        rungs = data.get("rungs", [])

        if not rungs:
            return jsonify({"error": "No rungs provided"}), 400

        account_hash = get_account_hash()
        client = get_client()
        results = []

        for i, rung in enumerate(rungs, 1):
            try:
                rung_data = {**data, "quantity": rung["quantity"], "price": rung["price"], "order_type": "limit"}

                if trade_type == "equity":
                    order = build_equity_order(rung_data)
                elif trade_type == "option":
                    order = build_option_order(rung_data)
                else:
                    results.append({"rung": i, "qty": rung["quantity"], "price": rung["price"],
                                    "status": "error", "error": f"Unknown trade_type: {trade_type!r}"})
                    continue

                resp = throttled_order_call(client.place_order, account_hash, order)
                resp.raise_for_status()

                location = resp.headers.get("Location", "")
                order_id = location.rstrip("/").split("/")[-1] if location else "\u2014"
                results.append({"rung": i, "qty": rung["quantity"], "price": rung["price"],
                                "status": "ok", "order_id": order_id})

            except Exception as rung_err:
                results.append({"rung": i, "qty": rung["quantity"], "price": rung["price"],
                                "status": "error", "error": str(rung_err)})

        return jsonify({"results": results})

    except Exception as e:
        log.exception("API error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/order/<order_id>", methods=["DELETE"])
def api_cancel_order(order_id):
    """Cancel a specific open order by ID."""
    try:
        account_hash = get_account_hash()
        client = get_client()
        resp = throttled_order_call(client.cancel_order, order_id, account_hash)
        resp.raise_for_status()
        return jsonify({"status": "cancelled", "order_id": order_id})
    except Exception as e:
        log.exception("API error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/order/strategy", methods=["POST"])
def api_place_strategy_order():
    """Place a multi-leg strategy order."""
    try:
        data = request.json or {}
        order = build_multi_leg_order(data)
        account_hash = get_account_hash()
        client = get_client()
        resp = throttled_order_call(client.place_order, account_hash, order)
        resp.raise_for_status()
        location = resp.headers.get("Location", "")
        order_id = location.rstrip("/").split("/")[-1] if location else "\u2014"
        return jsonify({"status": "ok", "order_id": order_id})
    except Exception as e:
        log.exception("API error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/order/strategy-ladder", methods=["POST"])
def api_place_strategy_ladder():
    """Place multiple independent strategy orders (price ladder)."""
    try:
        data = request.json or {}
        orders = data.get("orders") or []

        if not orders:
            return jsonify({"error": "No orders provided"}), 400
        if len(orders) > 7:
            return jsonify({"error": "Maximum 7 ladder rungs"}), 400

        account_hash = get_account_hash()
        client = get_client()
        results = []

        for i, order_data in enumerate(orders, 1):
            qty = order_data.get("_rung_qty")
            price = order_data.get("_rung_price")
            body = {k: v for k, v in order_data.items() if not str(k).startswith("_")}
            try:
                order = build_multi_leg_order(body)
                resp = throttled_order_call(client.place_order, account_hash, order)
                resp.raise_for_status()
                location = resp.headers.get("Location", "")
                order_id = location.rstrip("/").split("/")[-1] if location else "\u2014"
                results.append({
                    "rung": i, "qty": qty, "price": price,
                    "status": "ok", "order_id": order_id,
                })
            except Exception as rung_err:
                results.append({
                    "rung": i, "qty": qty, "price": price,
                    "status": "error", "error": str(rung_err),
                })

        return jsonify({"results": results})

    except Exception as e:
        log.exception("API error")
        return jsonify({"error": str(e)}), 500
