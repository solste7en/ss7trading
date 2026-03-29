"""
app.py — ss7trading dashboard
Run: python app.py
Visit: http://127.0.0.1:5050
"""
import datetime
import json
import math
import sqlite3
import traceback
from pathlib import Path
from flask import Flask, jsonify, render_template_string, request
import schwab
from auth import get_client

BASE_DIR = Path(__file__).parent
DB_PATH  = BASE_DIR.parent / "trades.db"

app = Flask(__name__)

@app.errorhandler(Exception)
def handle_exception(e):
    return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500

# ── helpers ────────────────────────────────────────────────────────────────────

def _clean_positions(accounts_data):
    """Parse the Schwab account response into a flat list of position dicts."""
    positions = []
    for acct in accounts_data:
        acct_info = acct.get("securitiesAccount", {})
        acct_number = acct_info.get("accountNumber", "")
        for pos in acct_info.get("positions", []):
            instrument  = pos.get("instrument", {})
            asset_type  = instrument.get("assetType", "")
            symbol      = instrument.get("symbol", "")
            description = instrument.get("description", symbol)
            qty         = pos.get("longQuantity", 0) - pos.get("shortQuantity", 0)
            avg_price   = pos.get("averagePrice")
            mkt_value   = pos.get("marketValue")
            cost_basis  = pos.get("longOpenProfitLoss") # unrealized P&L vs cost
            day_pl      = pos.get("currentDayProfitLoss")
            day_pl_pct  = pos.get("currentDayProfitLossPercentage")

            positions.append({
                "account":     acct_number[-4:],   # last 4 digits only
                "symbol":      symbol,
                "description": description,
                "asset_type":  asset_type,
                "quantity":    qty,
                "avg_price":   avg_price,
                "market_value": mkt_value,
                "unrealized_pl": cost_basis,
                "day_pl":      day_pl,
                "day_pl_pct":  day_pl_pct,
            })
    # Sort: equities first, then options, then cash
    order = {"EQUITY": 0, "ETF": 1, "OPTION": 2, "CASH_EQUIVALENT": 3}
    positions.sort(key=lambda p: (order.get(p["asset_type"], 9), p["symbol"]))
    return positions


def _clean_quotes(quotes_data):
    """Parse the Schwab quote response into a flat list."""
    result = []
    for symbol, data in quotes_data.items():
        q = data.get("quote", {})
        ref = data.get("reference", {})
        result.append({
            "symbol":       symbol,
            "description":  ref.get("description", symbol),
            "last":         q.get("lastPrice"),
            "bid":          q.get("bidPrice"),
            "ask":          q.get("askPrice"),
            "change":       q.get("netChange"),
            "change_pct":   q.get("netPercentChange"),
            "volume":       q.get("totalVolume"),
            "52w_high":     q.get("52WeekHigh"),
            "52w_low":      q.get("52WeekLow"),
        })
    result.sort(key=lambda x: x["symbol"])
    return result


# ── API routes ─────────────────────────────────────────────────────────────────


@app.route("/api/test")
def api_test():
    """Quick connectivity test — returns raw account numbers from Schwab."""
    try:
        client = get_client()
        resp = client.get_account_numbers()
        resp.raise_for_status()
        return jsonify({"status": "ok", "data": resp.json()})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/positions")
def api_positions():
    try:
        client = get_client()
        resp = client.get_accounts(fields=[schwab.client.Client.Account.Fields.POSITIONS])
        resp.raise_for_status()
        return jsonify(_clean_positions(resp.json()))
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/quotes")
def api_quotes():
    """Returns quotes for all symbols currently held."""
    try:
        client = get_client()
        resp = client.get_accounts(fields=[schwab.client.Client.Account.Fields.POSITIONS])
        resp.raise_for_status()
        positions = _clean_positions(resp.json())

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
        return jsonify(_clean_quotes(resp.json()))
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/quote/<symbol>")
def api_quote_single(symbol):
    """Quote a single symbol."""
    try:
        client = get_client()
        resp = client.get_quotes([symbol.upper()])
        resp.raise_for_status()
        data = _clean_quotes(resp.json())
        return jsonify(data[0] if data else {})
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


# ── Trade History & Realized G/L endpoints ────────────────────────────────────

@app.route("/api/transactions")
def api_transactions():
    try:
        page     = max(1, int(request.args.get("page", 1)))
        limit    = min(100, max(10, int(request.args.get("limit", 25))))
        category = request.args.get("category", "").strip()
        ticker   = request.args.get("ticker", "").strip().upper()
        search   = request.args.get("search", "").strip()
        offset   = (page - 1) * limit

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur  = conn.cursor()

        where, params = [], []
        if category:
            where.append("category = ?"); params.append(category)
        if ticker:
            where.append("underlying = ?"); params.append(ticker)
        if search:
            where.append("(symbol LIKE ? OR action LIKE ? OR underlying LIKE ?)")
            params += [f"%{search}%", f"%{search}%", f"%{search}%"]

        clause = ("WHERE " + " AND ".join(where)) if where else ""

        cur.execute(f"SELECT COUNT(*) FROM transactions {clause}", params)
        total = cur.fetchone()[0]

        cur.execute(f"""
            SELECT trade_date, action, category, symbol, underlying,
                   quantity, price, fees, amount,
                   is_option, option_type, option_strike, option_expiry,
                   is_from_option_event, linked_option_action
            FROM transactions {clause}
            ORDER BY trade_date DESC, id DESC
            LIMIT ? OFFSET ?
        """, params + [limit, offset])

        rows = [dict(r) for r in cur.fetchall()]
        conn.close()

        return jsonify({
            "data":  rows,
            "total": total,
            "page":  page,
            "limit": limit,
            "pages": math.ceil(total / limit),
        })
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/realized_gains")
def api_realized_gains():
    try:
        page   = max(1, int(request.args.get("page", 1)))
        limit  = min(100, max(10, int(request.args.get("limit", 25))))
        ticker = request.args.get("ticker", "").strip().upper()
        term   = request.args.get("term", "").strip()   # "lt", "st", or ""
        offset = (page - 1) * limit

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur  = conn.cursor()

        where, params = [], []
        if ticker:
            where.append("underlying = ?"); params.append(ticker)
        if term == "lt":
            where.append("lt_gl_amt IS NOT NULL")
        elif term == "st":
            where.append("st_gl_amt IS NOT NULL")

        clause = ("WHERE " + " AND ".join(where)) if where else ""

        cur.execute(f"SELECT COUNT(*) FROM realized_gains {clause}", params)
        total = cur.fetchone()[0]

        cur.execute(f"""
            SELECT symbol, underlying, name, closed_date, quantity,
                   closing_price, cb_method, proceeds, cost_basis,
                   total_gl_amt, total_gl_pct,
                   lt_gl_amt, lt_gl_pct, st_gl_amt, st_gl_pct,
                   wash_sale, disallowed_loss,
                   is_option, option_type, option_strike, option_expiry
            FROM realized_gains {clause}
            ORDER BY closed_date DESC, id DESC
            LIMIT ? OFFSET ?
        """, params + [limit, offset])

        rows = [dict(r) for r in cur.fetchall()]
        conn.close()

        return jsonify({
            "data":  rows,
            "total": total,
            "page":  page,
            "limit": limit,
            "pages": math.ceil(total / limit),
        })
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


# ── Order helpers ──────────────────────────────────────────────────────────────

def _get_account_hash():
    """Return the hashValue of the first linked account (needed for order placement)."""
    client = get_client()
    resp = client.get_account_numbers()
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise ValueError("No accounts found in get_account_numbers()")
    return data[0]["hashValue"]


def _clean_orders(orders_data):
    """Flatten Schwab order objects into simple dicts for the UI."""
    result = []
    for order in (orders_data or []):
        legs      = order.get("orderLegCollection", [])
        first     = legs[0] if legs else {}
        instr     = first.get("instrument", {})
        symbol    = instr.get("symbol", "")
        asset_type= instr.get("assetType", "")
        instruction = first.get("instruction", "")
        underlying  = symbol.split()[0] if " " in symbol else symbol
        entered     = (order.get("enteredTime") or "")[:19].replace("T", " ")
        close       = (order.get("closeTime")   or "")[:19].replace("T", " ")
        result.append({
            "order_id":           str(order.get("orderId", "")),
            "status":             order.get("status", ""),
            "order_type":         order.get("orderType", ""),
            "session":            order.get("session", ""),
            "duration":           order.get("duration", ""),
            "symbol":             symbol,
            "underlying":         underlying,
            "asset_type":         asset_type,
            "instruction":        instruction,
            "quantity":           order.get("quantity", 0),
            "filled_quantity":    order.get("filledQuantity", 0),
            "remaining_quantity": order.get("remainingQuantity", 0),
            "price":              order.get("price"),
            "stop_price":         order.get("stopPrice"),
            "entered_time":       entered,
            "close_time":         close,
            "cancelable":         order.get("cancelable", False),
            "editable":           order.get("editable", False),
            "legs":               len(legs),
        })
    return result


def _build_equity_order(data):
    """Build an equity/ETF order using schwab-py convenience functions."""
    from schwab.orders import equities as EQ
    from schwab.orders.options import OrderBuilder
    from schwab.orders.common import (Duration, Session, OrderType,
                                       EquityInstruction, OrderStrategyType)

    action     = data["action"]          # buy | sell | sell_short | buy_to_cover
    symbol     = data["symbol"].strip().upper()
    qty        = int(data["quantity"])
    order_type = data["order_type"]      # market | limit | stop | stop_limit
    price      = data.get("price")
    stop_price = data.get("stop_price")
    duration   = data.get("duration", "day")    # day | gtc
    session    = data.get("session", "normal")  # normal | am | pm | seamless

    dur_map = {"day": Duration.DAY, "gtc": Duration.GOOD_TILL_CANCEL}
    ses_map = {
        "normal":   Session.NORMAL,
        "am":       Session.AM,
        "pm":       Session.PM,
        "seamless": Session.SEAMLESS,
    }

    if order_type == "market":
        fn = {
            "buy":          EQ.equity_buy_market,
            "sell":         EQ.equity_sell_market,
            "sell_short":   EQ.equity_sell_short_market,
            "buy_to_cover": EQ.equity_buy_to_cover_market,
        }[action]
        order = fn(symbol, qty)

    elif order_type == "limit":
        fn = {
            "buy":          EQ.equity_buy_limit,
            "sell":         EQ.equity_sell_limit,
            "sell_short":   EQ.equity_sell_short_limit,
            "buy_to_cover": EQ.equity_buy_to_cover_limit,
        }[action]
        order = fn(symbol, qty, float(price))

    else:
        # STOP or STOP_LIMIT — use OrderBuilder directly
        inst_map = {
            "buy":          EquityInstruction.BUY,
            "sell":         EquityInstruction.SELL,
            "sell_short":   EquityInstruction.SELL_SHORT,
            "buy_to_cover": EquityInstruction.BUY_TO_COVER,
        }
        ot_map = {"stop": OrderType.STOP, "stop_limit": OrderType.STOP_LIMIT}
        order = (OrderBuilder()
                 .set_order_type(ot_map[order_type])
                 .set_order_strategy_type(OrderStrategyType.SINGLE)
                 .add_equity_leg(inst_map[action], symbol, qty))
        if stop_price is not None:
            order.set_stop_price(float(stop_price))
        if price is not None and order_type == "stop_limit":
            order.set_price(float(price))

    order.set_duration(dur_map.get(duration, Duration.DAY))
    order.set_session(ses_map.get(session, Session.NORMAL))
    return order


def _build_option_order(data):
    """Build a single-leg option order using schwab-py convenience functions."""
    from schwab.orders import options as OP
    from schwab.orders.common import Duration, Session

    underlying  = data["underlying"].strip().upper()
    option_type = data["option_type"].upper()    # PUT | CALL
    action      = data["action"]                 # buy_to_open | sell_to_open | buy_to_close | sell_to_close
    expiry_str  = data["expiry"]                 # "YYYY-MM-DD"
    strike      = data["strike"]
    qty         = int(data["contracts"])
    order_type  = data["order_type"]             # market | limit
    price       = data.get("price")
    duration    = data.get("duration", "day")    # day | gtc
    session     = data.get("session", "normal")  # normal | am | pm | seamless

    expiry = datetime.date.fromisoformat(expiry_str)
    symbol = OP.OptionSymbol(underlying, expiry, option_type[0], str(strike)).build()

    dur_map = {"day": Duration.DAY, "gtc": Duration.GOOD_TILL_CANCEL}
    ses_map = {
        "normal":   Session.NORMAL,
        "am":       Session.AM,
        "pm":       Session.PM,
        "seamless": Session.SEAMLESS,
    }

    fn_map = {
        ("buy_to_open",   "limit"):  OP.option_buy_to_open_limit,
        ("buy_to_open",   "market"): OP.option_buy_to_open_market,
        ("sell_to_open",  "limit"):  OP.option_sell_to_open_limit,
        ("sell_to_open",  "market"): OP.option_sell_to_open_market,
        ("buy_to_close",  "limit"):  OP.option_buy_to_close_limit,
        ("buy_to_close",  "market"): OP.option_buy_to_close_market,
        ("sell_to_close", "limit"):  OP.option_sell_to_close_limit,
        ("sell_to_close", "market"): OP.option_sell_to_close_market,
    }
    fn = fn_map.get((action, order_type))
    if fn is None:
        raise ValueError(f"Unsupported action/order_type: {action}/{order_type}")

    order = fn(symbol, qty, float(price)) if order_type == "limit" else fn(symbol, qty)
    order.set_duration(dur_map.get(duration, Duration.DAY))
    order.set_session(ses_map.get(session, Session.NORMAL))
    return order


# ── Order API routes ────────────────────────────────────────────────────────────

@app.route("/api/orders")
def api_orders():
    """Return all open/working orders across linked accounts."""
    try:
        client = get_client()
        now    = datetime.datetime.now(datetime.timezone.utc)
        resp   = client.get_orders_for_all_linked_accounts(
            from_entered_datetime=now - datetime.timedelta(days=90),
            to_entered_datetime=now + datetime.timedelta(days=1),
        )
        resp.raise_for_status()
        raw = resp.json() or []
        # Keep only open/pending statuses
        open_statuses = {
            "WORKING", "QUEUED", "PENDING_ACTIVATION", "ACCEPTED",
            "AWAITING_PARENT_ORDER", "AWAITING_CONDITION",
            "NEW", "PENDING_ACKNOWLEDGEMENT",
        }
        orders = [o for o in raw if o.get("status", "") in open_statuses]
        return jsonify(_clean_orders(orders))
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/order", methods=["POST"])
def api_place_order():
    """Place an equity or single-leg option order."""
    try:
        data       = request.json or {}
        trade_type = data.get("trade_type")

        if trade_type == "equity":
            order = _build_equity_order(data)
        elif trade_type == "option":
            order = _build_option_order(data)
        else:
            return jsonify({"error": f"Unknown trade_type: {trade_type!r}"}), 400

        account_hash = _get_account_hash()
        resp = get_client().place_order(account_hash, order)
        resp.raise_for_status()

        # Schwab echoes the new order ID in the Location header
        location = resp.headers.get("Location", "")
        order_id = location.rstrip("/").split("/")[-1] if location else "—"
        return jsonify({"status": "ok", "order_id": order_id})

    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/order/<order_id>", methods=["DELETE"])
def api_cancel_order(order_id):
    """Cancel a specific open order by ID."""
    try:
        account_hash = _get_account_hash()
        resp = get_client().cancel_order(order_id, account_hash)
        resp.raise_for_status()
        return jsonify({"status": "cancelled", "order_id": order_id})
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


# ── Dashboard UI ───────────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ss7trading · Dashboard</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #0f1117; color: #e2e8f0; font-size: 13px; }
.topbar { background: #1a1d2e; border-bottom: 1px solid #2d3148;
          padding: 14px 24px; display: flex; align-items: center; gap: 16px; }
.topbar h1 { font-size: 17px; font-weight: 600; }
.topbar .sub { color: #64748b; font-size: 12px; }
.refresh-btn { margin-left: auto; background: #312e81; color: #a5b4fc;
               border: 1px solid #4338ca; border-radius: 6px; padding: 6px 14px;
               cursor: pointer; font-size: 12px; }
.refresh-btn:hover { background: #3730a3; }
.tabs { display: flex; gap: 2px; padding: 12px 24px 0; border-bottom: 1px solid #1e2235; }
.tab { padding: 8px 18px; border-radius: 6px 6px 0 0; cursor: pointer;
       color: #64748b; font-size: 12px; font-weight: 500; }
.tab.active { background: #1a1d2e; color: #e2e8f0; border: 1px solid #2d3148; border-bottom: none; }
.panel { display: none; padding: 20px 24px; }
.panel.active { display: block; }

/* Filters bar */
.filters { display: flex; gap: 8px; margin-bottom: 14px; flex-wrap: wrap; align-items: center; }
.filters input, .filters select {
  background: #1a1d2e; border: 1px solid #2d3148; border-radius: 6px;
  color: #e2e8f0; padding: 6px 10px; font-size: 12px; outline: none; }
.filters input { width: 150px; }
.filters input:focus, .filters select:focus { border-color: #6366f1; }
.filters .count { color: #64748b; font-size: 12px; margin-left: auto; }

/* Quote search */
.quote-search { display: flex; gap: 8px; margin-bottom: 16px; }
.quote-search input { background: #1a1d2e; border: 1px solid #2d3148;
  border-radius: 6px; color: #e2e8f0; padding: 8px 12px; font-size: 13px;
  width: 200px; outline: none; text-transform: uppercase; }
.quote-search input:focus { border-color: #6366f1; }
.quote-search button { background: #312e81; color: #a5b4fc;
  border: 1px solid #4338ca; border-radius: 6px; padding: 8px 16px;
  cursor: pointer; font-size: 12px; }
.quote-card { background: #1a1d2e; border: 1px solid #2d3148; border-radius: 8px;
  padding: 16px 20px; display: inline-grid; min-width: 260px;
  grid-template-columns: 1fr 1fr; gap: 8px 24px; margin-bottom: 12px; }
.quote-card .sym { font-size: 22px; font-weight: 700; grid-column: 1/-1; }
.quote-card .last { font-size: 28px; font-weight: 700; grid-column: 1/-1; }
.quote-card label { color: #64748b; font-size: 11px; text-transform: uppercase; }
.quote-card .val { color: #e2e8f0; }

/* Tables */
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th { background: #1a1d2e; color: #94a3b8; font-weight: 500; font-size: 11px;
     text-transform: uppercase; letter-spacing: 0.05em; padding: 10px 12px;
     text-align: left; border-bottom: 1px solid #2d3148; white-space: nowrap; }
td { padding: 9px 12px; border-bottom: 1px solid #1e2235; white-space: nowrap; }
tr:hover td { background: #1a1d2e; }
.pos { color: #34d399; font-weight: 500; }
.neg { color: #f87171; font-weight: 500; }

/* Badges */
.badge { display: inline-block; padding: 2px 7px; border-radius: 4px; font-size: 11px; font-weight: 500; }
.badge-EQUITY,.badge-equity   { background: #1e3a5f; color: #7dd3fc; }
.badge-ETF,.badge-etf         { background: #1e3a5f; color: #93c5fd; }
.badge-OPTION,.badge-option   { background: #312e81; color: #a5b4fc; }
.badge-CASH_EQUIVALENT        { background: #1c2a1c; color: #86efac; }
.badge-income                 { background: #14532d; color: #86efac; }
.badge-transfer               { background: #3b2f1c; color: #fbbf24; }
.badge-PUT  { background: #4c1d1d; color: #fca5a5; }
.badge-CALL { background: #14532d; color: #86efac; }
.badge-ws   { background: #44270a; color: #fb923c; font-size: 10px; }

/* Pagination */
.pagination { display: flex; align-items: center; gap: 6px; margin-top: 14px;
              justify-content: center; flex-wrap: wrap; }
.pg-btn { background: #1a1d2e; border: 1px solid #2d3148; border-radius: 5px;
          color: #94a3b8; padding: 5px 11px; cursor: pointer; font-size: 12px; }
.pg-btn:hover:not(:disabled) { border-color: #6366f1; color: #a5b4fc; }
.pg-btn.active { background: #312e81; border-color: #6366f1; color: #a5b4fc; }
.pg-btn:disabled { opacity: 0.35; cursor: default; }
.pg-info { color: #64748b; font-size: 12px; }

.loading { color: #64748b; padding: 40px; text-align: center; }
.error   { color: #f87171; padding: 20px; }

/* ── Trade tab ── */
.trade-toggle { display: flex; gap: 8px; margin-bottom: 20px; }
.toggle-btn { background: #1a1d2e; border: 1px solid #2d3148; border-radius: 6px;
  color: #64748b; padding: 8px 20px; cursor: pointer; font-size: 13px; font-weight: 500; }
.toggle-btn.active { background: #312e81; border-color: #6366f1; color: #a5b4fc; }
.toggle-btn:hover:not(.active) { border-color: #4338ca; color: #e2e8f0; }

.trade-form { background: #131621; border: 1px solid #1e2235; border-radius: 10px; padding: 20px 24px; max-width: 720px; }
.form-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 14px; margin-bottom: 20px; }
.form-group label { display: block; font-size: 11px; color: #64748b; text-transform: uppercase;
  letter-spacing: 0.05em; margin-bottom: 5px; }
.form-group input, .form-group select {
  width: 100%; background: #1a1d2e; border: 1px solid #2d3148; border-radius: 6px;
  color: #e2e8f0; padding: 8px 10px; font-size: 13px; outline: none; }
.form-group input:focus, .form-group select:focus { border-color: #6366f1; }
.form-group input[type=text] { text-transform: uppercase; }

.preview-btn { background: #312e81; color: #a5b4fc; border: 1px solid #4338ca;
  border-radius: 6px; padding: 9px 22px; cursor: pointer; font-size: 13px; font-weight: 500; }
.preview-btn:hover { background: #3730a3; }

.preview-box { background: #1a1d2e; border: 1px solid #4338ca; border-radius: 8px;
  padding: 18px 20px; max-width: 560px; margin-top: 16px; }
.preview-title { font-size: 13px; font-weight: 600; color: #fbbf24; margin-bottom: 10px; }
.preview-summary { font-size: 14px; color: #e2e8f0; margin-bottom: 16px; line-height: 1.6; }
.preview-actions { display: flex; gap: 10px; }
.cancel-btn { background: #1e2235; border: 1px solid #2d3148; border-radius: 6px;
  color: #94a3b8; padding: 8px 18px; cursor: pointer; font-size: 12px; }
.cancel-btn:hover { border-color: #475569; }
.confirm-btn { background: #065f46; border: 1px solid #059669; border-radius: 6px;
  color: #6ee7b7; padding: 8px 18px; cursor: pointer; font-size: 13px; font-weight: 600; }
.confirm-btn:hover { background: #047857; }

.success-box { background: #052e16; border: 1px solid #16a34a; border-radius: 8px;
  padding: 14px 18px; color: #86efac; font-size: 13px; max-width: 520px; margin-top: 16px; }

/* ── Open Orders tab ── */
.badge-status-WORKING    { background: #1a3a5f; color: #7dd3fc; }
.badge-status-QUEUED     { background: #2d2f1c; color: #fde68a; }
.badge-status-PENDING_ACTIVATION { background: #312e81; color: #a5b4fc; }
.badge-status-ACCEPTED   { background: #14532d; color: #86efac; }
.badge-status-NEW        { background: #1e2235; color: #94a3b8; }
.cancel-order-btn { background: #4c1d1d; border: 1px solid #7f1d1d; border-radius: 5px;
  color: #fca5a5; padding: 3px 10px; cursor: pointer; font-size: 11px; }
.cancel-order-btn:hover { background: #7f1d1d; }
</style>
</head>
<body>

<div class="topbar">
  <div>
    <h1>📈 ss7trading</h1>
    <div class="sub" id="lastUpdated">Loading…</div>
  </div>
  <button class="refresh-btn" onclick="refreshCurrent()">↻ Refresh</button>
</div>

<div class="tabs">
  <div class="tab active"  onclick="switchTab('positions')">Positions</div>
  <div class="tab"         onclick="switchTab('quotes')">Quote Lookup</div>
  <div class="tab"         onclick="switchTab('history')">Trade History</div>
  <div class="tab"         onclick="switchTab('gains')">Realized G/L</div>
  <div class="tab"         onclick="switchTab('trade')">⚡ Trade</div>
  <div class="tab"         onclick="switchTab('orders')">Open Orders</div>
</div>

<!-- ── Positions ── -->
<div class="panel active" id="tab-positions">
  <div class="loading" id="pos-loading">Loading positions…</div>
  <div id="pos-error" class="error" style="display:none"></div>
  <div class="table-wrap" id="pos-table" style="display:none">
    <table><thead><tr>
      <th>Symbol</th><th>Type</th><th>Qty</th><th>Avg Price</th>
      <th>Mkt Value</th><th>Unrealized P&L</th><th>Day P&L</th><th>Day %</th>
    </tr></thead><tbody id="pos-tbody"></tbody></table>
  </div>
</div>

<!-- ── Quote Lookup ── -->
<div class="panel" id="tab-quotes">
  <div class="quote-search">
    <input type="text" id="quoteInput" placeholder="NVDA" maxlength="10"
           onkeydown="if(event.key==='Enter') fetchQuote()">
    <button onclick="fetchQuote()">Get Quote</button>
  </div>
  <div id="quote-result"></div>
  <hr style="border-color:#1e2235; margin:20px 0">
  <div style="color:#64748b;font-size:12px;margin-bottom:12px;">Live quotes for all held positions:</div>
  <div class="loading" id="q-loading">Loading quotes…</div>
  <div id="q-error" class="error" style="display:none"></div>
  <div class="table-wrap">
    <table style="display:none" id="q-table"><thead><tr>
      <th>Symbol</th><th>Last</th><th>Bid</th><th>Ask</th>
      <th>Change</th><th>Change %</th><th>Volume</th><th>52W High</th><th>52W Low</th>
    </tr></thead><tbody id="q-tbody"></tbody></table>
  </div>
</div>

<!-- ── Trade History ── -->
<div class="panel" id="tab-history">
  <div class="filters">
    <input type="text" id="h-ticker"   placeholder="Ticker (e.g. NVDA)" oninput="debounce(loadHistory)">
    <input type="text" id="h-search"   placeholder="Search symbol/action…" oninput="debounce(loadHistory)">
    <select id="h-category" onchange="loadHistory()">
      <option value="">All categories</option>
      <option value="option">Options</option>
      <option value="equity">Equity</option>
      <option value="income">Income</option>
      <option value="transfer">Transfer</option>
    </select>
    <select id="h-limit" onchange="loadHistory()">
      <option value="25">25 / page</option>
      <option value="50">50 / page</option>
      <option value="100">100 / page</option>
    </select>
    <span class="count" id="h-count"></span>
  </div>
  <div class="loading" id="h-loading">Loading…</div>
  <div id="h-error" class="error" style="display:none"></div>
  <div class="table-wrap" id="h-table" style="display:none">
    <table><thead><tr>
      <th>Date</th><th>Action</th><th>Category</th><th>Ticker</th>
      <th>Symbol</th><th>Opt Type</th><th>Strike</th><th>Expiry</th>
      <th>Qty</th><th>Price</th><th>Fees</th><th>Amount</th><th>Source</th>
    </tr></thead><tbody id="h-tbody"></tbody></table>
  </div>
  <div class="pagination" id="h-pagination"></div>
</div>

<!-- ── Realized G/L ── -->
<div class="panel" id="tab-gains">
  <div class="filters">
    <input type="text" id="g-ticker" placeholder="Ticker (e.g. NVDA)" oninput="debounce(loadGains)">
    <select id="g-term" onchange="loadGains()">
      <option value="">All (LT + ST)</option>
      <option value="lt">Long Term only</option>
      <option value="st">Short Term only</option>
    </select>
    <select id="g-limit" onchange="loadGains()">
      <option value="25">25 / page</option>
      <option value="50">50 / page</option>
      <option value="100">100 / page</option>
    </select>
    <span class="count" id="g-count"></span>
  </div>
  <div class="loading" id="g-loading">Loading…</div>
  <div id="g-error" class="error" style="display:none"></div>
  <div class="table-wrap" id="g-table" style="display:none">
    <table><thead><tr>
      <th>Closed</th><th>Symbol</th><th>Ticker</th><th>Type</th>
      <th>Qty</th><th>Proceeds</th><th>Cost Basis</th>
      <th>Total G/L</th><th>Total G/L %</th>
      <th>LT G/L</th><th>ST G/L</th>
      <th>Wash Sale</th><th>Disallowed</th>
    </tr></thead><tbody id="g-tbody"></tbody></table>
  </div>
  <div class="pagination" id="g-pagination"></div>
</div>

<!-- ── Trade ── -->
<div class="panel" id="tab-trade">
  <div class="trade-toggle">
    <button class="toggle-btn active" id="btn-equity" onclick="setTradeMode('equity')">📈 Stock / ETF</button>
    <button class="toggle-btn"        id="btn-option" onclick="setTradeMode('option')">🎯 Option (Single Leg)</button>
  </div>

  <!-- Equity / ETF form -->
  <div id="form-equity" class="trade-form">
    <div class="form-grid">
      <div class="form-group">
        <label>Ticker</label>
        <input type="text" id="eq-ticker" placeholder="NVDA" maxlength="10">
      </div>
      <div class="form-group">
        <label>Action</label>
        <select id="eq-action">
          <option value="buy">Buy</option>
          <option value="sell">Sell</option>
          <option value="sell_short">Sell Short</option>
          <option value="buy_to_cover">Buy to Cover</option>
        </select>
      </div>
      <div class="form-group">
        <label>Quantity (shares)</label>
        <input type="number" id="eq-qty" placeholder="100" min="1" step="1">
      </div>
      <div class="form-group">
        <label>Order Type</label>
        <select id="eq-order-type" onchange="updateEqFields()">
          <option value="limit" selected>Limit</option>
          <option value="market">Market</option>
          <option value="stop">Stop</option>
          <option value="stop_limit">Stop Limit</option>
        </select>
      </div>
      <div class="form-group" id="eq-price-group">
        <label>Limit Price ($)</label>
        <input type="number" id="eq-price" placeholder="0.00" step="0.01" min="0">
      </div>
      <div class="form-group" id="eq-stop-group" style="display:none">
        <label>Stop Price ($)</label>
        <input type="number" id="eq-stop" placeholder="0.00" step="0.01" min="0">
      </div>
      <div class="form-group">
        <label>Duration</label>
        <select id="eq-duration">
          <option value="day">Day</option>
          <option value="gtc">GTC (Good Till Cancelled)</option>
        </select>
      </div>
      <div class="form-group">
        <label>Session</label>
        <select id="eq-session">
          <option value="normal">Normal (Market Hours)</option>
          <option value="seamless">Extended Hours (Pre + Post)</option>
          <option value="am">Pre-Market Only</option>
          <option value="pm">Post-Market Only</option>
        </select>
      </div>
    </div>
    <button class="preview-btn" onclick="previewOrder('equity')">Preview Order →</button>
  </div>

  <!-- Option form -->
  <div id="form-option" class="trade-form" style="display:none">
    <div class="form-grid">
      <div class="form-group">
        <label>Underlying Ticker</label>
        <input type="text" id="opt-underlying" placeholder="NVDA" maxlength="10">
      </div>
      <div class="form-group">
        <label>Option Type</label>
        <select id="opt-type">
          <option value="PUT">PUT</option>
          <option value="CALL">CALL</option>
        </select>
      </div>
      <div class="form-group">
        <label>Action</label>
        <select id="opt-action">
          <option value="sell_to_open">Sell to Open (short)</option>
          <option value="buy_to_open">Buy to Open (long)</option>
          <option value="buy_to_close">Buy to Close</option>
          <option value="sell_to_close">Sell to Close</option>
        </select>
      </div>
      <div class="form-group">
        <label>Expiration Date</label>
        <input type="date" id="opt-expiry">
      </div>
      <div class="form-group">
        <label>Strike Price ($)</label>
        <input type="number" id="opt-strike" placeholder="170.00" step="0.50" min="0">
      </div>
      <div class="form-group">
        <label>Contracts</label>
        <input type="number" id="opt-contracts" placeholder="1" min="1" step="1" value="1">
      </div>
      <div class="form-group">
        <label>Order Type</label>
        <select id="opt-order-type" onchange="updateOptFields()">
          <option value="limit" selected>Limit</option>
          <option value="market">Market</option>
        </select>
      </div>
      <div class="form-group" id="opt-price-group">
        <label>Limit Price (per share)</label>
        <input type="number" id="opt-price" placeholder="0.00" step="0.01" min="0">
      </div>
      <div class="form-group">
        <label>Duration</label>
        <select id="opt-duration">
          <option value="day">Day</option>
          <option value="gtc">GTC (Good Till Cancelled)</option>
        </select>
      </div>
      <div class="form-group">
        <label>Session</label>
        <select id="opt-session">
          <option value="normal">Normal (Market Hours)</option>
          <option value="seamless">Extended Hours (Pre + Post)</option>
          <option value="am">Pre-Market Only</option>
          <option value="pm">Post-Market Only</option>
        </select>
      </div>
    </div>
    <button class="preview-btn" onclick="previewOrder('option')">Preview Order →</button>
  </div>

  <div id="trade-result"></div>
</div>

<!-- ── Open Orders ── -->
<div class="panel" id="tab-orders">
  <div class="filters">
    <input type="text" id="ord-ticker" placeholder="Filter by ticker…" oninput="filterOrders()" style="width:160px">
    <select id="ord-type" onchange="filterOrders()">
      <option value="">All order types</option>
      <option value="MARKET">Market</option>
      <option value="LIMIT">Limit</option>
      <option value="STOP">Stop</option>
      <option value="STOP_LIMIT">Stop Limit</option>
    </select>
    <select id="ord-status" onchange="filterOrders()">
      <option value="">All statuses</option>
      <option value="WORKING">Working</option>
      <option value="QUEUED">Queued</option>
      <option value="PENDING_ACTIVATION">Pending Activation</option>
      <option value="ACCEPTED">Accepted</option>
    </select>
    <button class="refresh-btn" style="margin-left:0" onclick="loadOrders()">↻ Refresh</button>
    <span class="count" id="ord-count"></span>
  </div>
  <div class="loading" id="ord-loading" style="display:none">Loading open orders…</div>
  <div id="ord-error" class="error" style="display:none"></div>
  <div class="table-wrap" id="ord-table" style="display:none">
    <table>
      <thead><tr>
        <th onclick="sortOrders('order_id')">Order ID ↕</th>
        <th onclick="sortOrders('status')">Status ↕</th>
        <th onclick="sortOrders('order_type')">Type ↕</th>
        <th onclick="sortOrders('underlying')">Ticker ↕</th>
        <th>Symbol</th>
        <th onclick="sortOrders('instruction')">Side ↕</th>
        <th onclick="sortOrders('quantity')">Qty ↕</th>
        <th onclick="sortOrders('filled_quantity')">Filled ↕</th>
        <th onclick="sortOrders('price')">Price ↕</th>
        <th onclick="sortOrders('entered_time')">Entered ↕</th>
        <th>Action</th>
      </tr></thead>
      <tbody id="ord-tbody"></tbody>
    </table>
  </div>
  <div id="ord-empty" style="display:none;color:#64748b;padding:32px;text-align:center">No open orders found.</div>
</div>

<script>
const fmt   = (v,d=2) => v==null ? '—' : Number(v).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
const fmtD  = (v,d=2) => v==null ? '—' : (v>=0?'+':'') + fmt(v,d);
const cls   = (v)     => v==null ? '' : (v>=0?'pos':'neg');
const esc   = (s)     => String(s||'').replace(/</g,'&lt;');
let _debTimer = null;
function debounce(fn) { clearTimeout(_debTimer); _debTimer = setTimeout(fn, 400); }

// ── tab state ──────────────────────────────────────────────────────
const TAB_NAMES = ['positions','quotes','history','gains','trade','orders'];
let currentTab = 'positions';

function switchTab(name) {
  currentTab = name;
  document.querySelectorAll('.tab').forEach((t,i) =>
    t.classList.toggle('active', TAB_NAMES[i] === name));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  if (name === 'history' && !historyState.loaded) loadHistory();
  if (name === 'gains'   && !gainsState.loaded)   loadGains();
  if (name === 'orders'  && !ordersState.loaded)  loadOrders();
}

function refreshCurrent() {
  document.getElementById('lastUpdated').textContent = 'Updated ' + new Date().toLocaleTimeString();
  if (currentTab === 'positions') loadPositions();
  if (currentTab === 'quotes')    loadQuotes();
  if (currentTab === 'history')   loadHistory();
  if (currentTab === 'gains')     loadGains();
  if (currentTab === 'orders')    loadOrders();
}

// ── Positions ─────────────────────────────────────────────────────
async function loadPositions() {
  try {
    const data = await fetch('/api/positions').then(r=>r.json());
    document.getElementById('pos-tbody').innerHTML = data.map(p => `<tr>
      <td><b>${esc(p.symbol)}</b></td>
      <td><span class="badge badge-${p.asset_type}">${p.asset_type}</span></td>
      <td>${fmt(p.quantity,0)}</td>
      <td>${p.avg_price!=null?'$'+fmt(p.avg_price,4):'—'}</td>
      <td>${p.market_value!=null?'$'+fmt(p.market_value):'—'}</td>
      <td class="${cls(p.unrealized_pl)}">${p.unrealized_pl!=null?'$'+fmtD(p.unrealized_pl):'—'}</td>
      <td class="${cls(p.day_pl)}">${p.day_pl!=null?'$'+fmtD(p.day_pl):'—'}</td>
      <td class="${cls(p.day_pl_pct)}">${p.day_pl_pct!=null?fmtD(p.day_pl_pct)+'%':'—'}</td>
    </tr>`).join('');
    document.getElementById('pos-loading').style.display='none';
    document.getElementById('pos-table').style.display='block';
  } catch(e) {
    document.getElementById('pos-loading').style.display='none';
    document.getElementById('pos-error').style.display='block';
    document.getElementById('pos-error').textContent='Error: '+e.message;
  }
}

// ── Quotes ────────────────────────────────────────────────────────
async function loadQuotes() {
  try {
    const data = await fetch('/api/quotes').then(r=>r.json());
    document.getElementById('q-tbody').innerHTML = data.map(q=>`<tr>
      <td><b>${esc(q.symbol)}</b></td><td>$${fmt(q.last)}</td>
      <td>${q.bid!=null?'$'+fmt(q.bid):'—'}</td>
      <td>${q.ask!=null?'$'+fmt(q.ask):'—'}</td>
      <td class="${cls(q.change)}">${q.change!=null?'$'+fmtD(q.change):'—'}</td>
      <td class="${cls(q.change_pct)}">${q.change_pct!=null?fmtD(q.change_pct)+'%':'—'}</td>
      <td>${q.volume!=null?Number(q.volume).toLocaleString():'—'}</td>
      <td>${q['52w_high']!=null?'$'+fmt(q['52w_high']):'—'}</td>
      <td>${q['52w_low']!=null?'$'+fmt(q['52w_low']):'—'}</td>
    </tr>`).join('');
    document.getElementById('q-loading').style.display='none';
    document.getElementById('q-table').style.display='table';
  } catch(e) {
    document.getElementById('q-loading').style.display='none';
    document.getElementById('q-error').style.display='block';
    document.getElementById('q-error').textContent='Error: '+e.message;
  }
}

async function fetchQuote() {
  const sym = document.getElementById('quoteInput').value.trim().toUpperCase();
  if (!sym) return;
  const div = document.getElementById('quote-result');
  div.innerHTML = '<div class="loading">Loading '+sym+'…</div>';
  try {
    const q = await fetch('/api/quote/'+sym).then(r=>r.json());
    if (!q.symbol) { div.innerHTML='<div class="error">No data for '+sym+'</div>'; return; }
    div.innerHTML = `<div class="quote-card">
      <div class="sym">${esc(q.symbol)}</div>
      <div class="last ${cls(q.change)}">$${fmt(q.last)}</div>
      <div><label>Bid</label><div class="val">$${fmt(q.bid)}</div></div>
      <div><label>Ask</label><div class="val">$${fmt(q.ask)}</div></div>
      <div><label>Change</label><div class="val ${cls(q.change)}">${q.change!=null?'$'+fmtD(q.change):'—'}</div></div>
      <div><label>Change %</label><div class="val ${cls(q.change_pct)}">${q.change_pct!=null?fmtD(q.change_pct)+'%':'—'}</div></div>
      <div><label>Volume</label><div class="val">${q.volume!=null?Number(q.volume).toLocaleString():'—'}</div></div>
      <div><label>52W High</label><div class="val">$${fmt(q['52w_high'])}</div></div>
      <div><label>52W Low</label><div class="val">$${fmt(q['52w_low'])}</div></div>
    </div>`;
  } catch(e) { div.innerHTML='<div class="error">Error: '+e.message+'</div>'; }
}

// ── Trade History (paginated) ─────────────────────────────────────
const historyState = { page:1, loaded:false };

async function loadHistory(resetPage=true) {
  if (resetPage) historyState.page = 1;
  const ticker   = document.getElementById('h-ticker').value.trim();
  const search   = document.getElementById('h-search').value.trim();
  const category = document.getElementById('h-category').value;
  const limit    = document.getElementById('h-limit').value;

  document.getElementById('h-loading').style.display='block';
  document.getElementById('h-table').style.display='none';
  document.getElementById('h-error').style.display='none';

  try {
    const params = new URLSearchParams({
      page: historyState.page, limit, ticker, search, category
    });
    const res  = await fetch('/api/transactions?'+params).then(r=>r.json());
    if (res.error) throw new Error(res.error);
    historyState.loaded = true;

    document.getElementById('h-count').textContent =
      `${res.total.toLocaleString()} total transactions`;

    document.getElementById('h-tbody').innerHTML = res.data.map(r => {
      const asgn = r.is_from_option_event
        ? `<span class="badge badge-OPTION" title="${r.linked_option_action||''}">asgn</span>` : '';
      return `<tr>
        <td>${r.trade_date}</td>
        <td>${esc(r.action)}</td>
        <td><span class="badge badge-${r.category}">${r.category}</span></td>
        <td><b>${esc(r.underlying||'')}</b></td>
        <td style="color:#94a3b8;max-width:220px;overflow:hidden;text-overflow:ellipsis">${esc(r.symbol)}</td>
        <td>${r.option_type?`<span class="badge badge-${r.option_type}">${r.option_type}</span>`:'—'}</td>
        <td>${r.option_strike!=null?'$'+fmt(r.option_strike):'—'}</td>
        <td>${r.option_expiry||'—'}</td>
        <td>${r.quantity!=null?fmt(r.quantity,0):'—'}</td>
        <td>${r.price!=null?'$'+fmt(r.price,4):'—'}</td>
        <td>${r.fees!=null?'$'+fmt(r.fees):'—'}</td>
        <td class="${cls(r.amount)}">${r.amount!=null?(r.amount>=0?'+':'')+'$'+fmt(Math.abs(r.amount)):'—'}</td>
        <td>${asgn}</td>
      </tr>`;
    }).join('');

    document.getElementById('h-loading').style.display='none';
    document.getElementById('h-table').style.display='block';
    renderPagination('h-pagination', res, loadHistory);
  } catch(e) {
    document.getElementById('h-loading').style.display='none';
    document.getElementById('h-error').style.display='block';
    document.getElementById('h-error').textContent='Error: '+e.message;
  }
}

// ── Realized G/L (paginated) ──────────────────────────────────────
const gainsState = { page:1, loaded:false };

async function loadGains(resetPage=true) {
  if (resetPage) gainsState.page = 1;
  const ticker = document.getElementById('g-ticker').value.trim();
  const term   = document.getElementById('g-term').value;
  const limit  = document.getElementById('g-limit').value;

  document.getElementById('g-loading').style.display='block';
  document.getElementById('g-table').style.display='none';
  document.getElementById('g-error').style.display='none';

  try {
    const params = new URLSearchParams({
      page: gainsState.page, limit, ticker, term
    });
    const res = await fetch('/api/realized_gains?'+params).then(r=>r.json());
    if (res.error) throw new Error(res.error);
    gainsState.loaded = true;

    document.getElementById('g-count').textContent =
      `${res.total.toLocaleString()} total closed positions`;

    document.getElementById('g-tbody').innerHTML = res.data.map(r => {
      const wsTag = r.wash_sale ? '<span class="badge badge-ws">WS</span>' : '—';
      return `<tr>
        <td>${r.closed_date}</td>
        <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;color:#94a3b8"
            title="${esc(r.symbol)}">${esc(r.symbol)}</td>
        <td><b>${esc(r.underlying||'')}</b></td>
        <td>${r.is_option?`<span class="badge badge-${r.option_type||'option'}">${r.option_type||'OPT'}</span>`
                         :'<span class="badge badge-equity">equity</span>'}</td>
        <td>${r.quantity!=null?fmt(r.quantity,0):'—'}</td>
        <td>${r.proceeds!=null?'$'+fmt(r.proceeds):'—'}</td>
        <td>${r.cost_basis!=null?'$'+fmt(r.cost_basis):'—'}</td>
        <td class="${cls(r.total_gl_amt)}">${r.total_gl_amt!=null?'$'+fmtD(r.total_gl_amt):'—'}</td>
        <td class="${cls(r.total_gl_pct)}">${r.total_gl_pct!=null?fmtD(r.total_gl_pct,1)+'%':'—'}</td>
        <td class="${cls(r.lt_gl_amt)}">${r.lt_gl_amt!=null?'$'+fmtD(r.lt_gl_amt):'—'}</td>
        <td class="${cls(r.st_gl_amt)}">${r.st_gl_amt!=null?'$'+fmtD(r.st_gl_amt):'—'}</td>
        <td>${wsTag}</td>
        <td class="neg">${r.disallowed_loss!=null&&r.disallowed_loss!=0?'$'+fmt(Math.abs(r.disallowed_loss)):'—'}</td>
      </tr>`;
    }).join('');

    document.getElementById('g-loading').style.display='none';
    document.getElementById('g-table').style.display='block';
    renderPagination('g-pagination', res, loadGains);
  } catch(e) {
    document.getElementById('g-loading').style.display='none';
    document.getElementById('g-error').style.display='block';
    document.getElementById('g-error').textContent='Error: '+e.message;
  }
}

// ── Pagination helper ─────────────────────────────────────────────
function renderPagination(containerId, res, loadFn) {
  const state = loadFn === loadHistory ? historyState : gainsState;
  const { page, pages, total, limit } = res;
  const start = (page-1)*limit+1, end = Math.min(page*limit, total);
  const el = document.getElementById(containerId);
  if (pages <= 1) { el.innerHTML=''; return; }

  // Show up to 7 page buttons around current page
  let btns = '';
  const addBtn = (p, label, active, disabled) =>
    `<button class="pg-btn${active?' active':''}" ${disabled?'disabled':''} onclick="
      ${loadFn===loadHistory?'historyState':'gainsState'}.page=${p};
      ${loadFn===loadHistory?'loadHistory':'loadGains'}(false)">${label}</button>`;

  btns += addBtn(page-1,'‹ Prev', false, page===1);
  const lo=Math.max(1,page-3), hi=Math.min(pages,page+3);
  if (lo>1) btns += addBtn(1,'1',false,false) + (lo>2?'<span class="pg-info">…</span>':'');
  for (let p=lo;p<=hi;p++) btns += addBtn(p,p,p===page,false);
  if (hi<pages) btns += (hi<pages-1?'<span class="pg-info">…</span>':'') + addBtn(pages,pages,false,false);
  btns += addBtn(page+1,'Next ›',false,page===pages);

  el.innerHTML = `<span class="pg-info">${start}–${end} of ${total.toLocaleString()}</span>` + btns;
}

// ── Trade form ─────────────────────────────────────────────────────
let tradeMode = 'equity';
let _pendingOrder = null;

function setTradeMode(mode) {
  tradeMode = mode;
  document.getElementById('btn-equity').classList.toggle('active', mode === 'equity');
  document.getElementById('btn-option').classList.toggle('active', mode === 'option');
  document.getElementById('form-equity').style.display = mode === 'equity' ? '' : 'none';
  document.getElementById('form-option').style.display = mode === 'option' ? '' : 'none';
  document.getElementById('trade-result').innerHTML = '';
}

function updateEqFields() {
  const ot = document.getElementById('eq-order-type').value;
  document.getElementById('eq-price-group').style.display = ['limit','stop_limit'].includes(ot) ? '' : 'none';
  document.getElementById('eq-stop-group').style.display  = ['stop','stop_limit'].includes(ot)  ? '' : 'none';
}

function updateOptFields() {
  const ot = document.getElementById('opt-order-type').value;
  document.getElementById('opt-price-group').style.display = ot === 'limit' ? '' : 'none';
}

function previewOrder(type) {
  let payload, summary;

  if (type === 'equity') {
    const ticker = document.getElementById('eq-ticker').value.trim().toUpperCase();
    const action = document.getElementById('eq-action').value;
    const qty    = document.getElementById('eq-qty').value;
    const ot     = document.getElementById('eq-order-type').value;
    const price  = document.getElementById('eq-price').value;
    const stop   = document.getElementById('eq-stop').value;

    if (!ticker) { showTradeError('Ticker is required.'); return; }
    if (!qty || qty <= 0) { showTradeError('Quantity must be a positive number.'); return; }
    if (['limit','stop_limit'].includes(ot) && !price) { showTradeError('Limit price is required.'); return; }
    if (['stop','stop_limit'].includes(ot) && !stop)   { showTradeError('Stop price is required.'); return; }

    const dur = document.getElementById('eq-duration').value;
    const ses = document.getElementById('eq-session').value;

    payload = { trade_type:'equity', symbol:ticker, action, quantity:qty, order_type:ot, duration:dur, session:ses };
    if (price && ['limit','stop_limit'].includes(ot)) payload.price = price;
    if (stop  && ['stop','stop_limit'].includes(ot))  payload.stop_price = stop;

    const aLabel = { buy:'Buy', sell:'Sell', sell_short:'Sell Short', buy_to_cover:'Buy to Cover' }[action];
    const pLabel = ot === 'market' ? 'at Market' : ot === 'stop' ? `Stop @$${stop}` :
                   ot === 'stop_limit' ? `Stop $${stop} / Limit $${price}` : `@$${price}`;
    const durLabel = dur === 'gtc' ? 'GTC' : 'Day';
    const sesLabel = {normal:'Normal', seamless:'Extended Hrs', am:'Pre-Market', pm:'Post-Market'}[ses];
    summary = `<b>${aLabel}</b> ${qty} shares of <b>${ticker}</b> — ${ot.replace('_',' ').toUpperCase()} ${pLabel} · ${durLabel} · ${sesLabel}`;

  } else {
    const underlying = document.getElementById('opt-underlying').value.trim().toUpperCase();
    const optType    = document.getElementById('opt-type').value;
    const action     = document.getElementById('opt-action').value;
    const expiry     = document.getElementById('opt-expiry').value;
    const strike     = document.getElementById('opt-strike').value;
    const contracts  = document.getElementById('opt-contracts').value;
    const ot         = document.getElementById('opt-order-type').value;
    const price      = document.getElementById('opt-price').value;

    if (!underlying)      { showTradeError('Underlying ticker is required.'); return; }
    if (!expiry)          { showTradeError('Expiration date is required.'); return; }
    if (!strike || strike <= 0) { showTradeError('Strike price is required.'); return; }
    if (!contracts || contracts < 1) { showTradeError('Contract count must be ≥ 1.'); return; }
    if (ot === 'limit' && !price) { showTradeError('Limit price is required.'); return; }

    const dur = document.getElementById('opt-duration').value;
    const ses = document.getElementById('opt-session').value;

    payload = { trade_type:'option', underlying, option_type:optType, action,
                expiry, strike, contracts, order_type:ot, duration:dur, session:ses };
    if (price && ot === 'limit') payload.price = price;

    const aLabel = { buy_to_open:'Buy to Open', sell_to_open:'Sell to Open',
                     buy_to_close:'Buy to Close', sell_to_close:'Sell to Close' }[action];
    const pLabel = ot === 'market' ? 'at Market' : `@$${price}/share ($${(price*100).toFixed(0)}/contract)`;
    const badge  = optType === 'PUT'
      ? '<span style="background:#4c1d1d;color:#fca5a5;padding:1px 6px;border-radius:3px;font-size:11px">PUT</span>'
      : '<span style="background:#14532d;color:#86efac;padding:1px 6px;border-radius:3px;font-size:11px">CALL</span>';
    const durLabel = dur === 'gtc' ? 'GTC' : 'Day';
    const sesLabel = {normal:'Normal', seamless:'Extended Hrs', am:'Pre-Market', pm:'Post-Market'}[ses];
    summary = `<b>${aLabel}</b> ${contracts} contract(s) — <b>${underlying}</b> ${expiry} $${strike} ${badge}<br>
               Order: ${ot.toUpperCase()} ${pLabel} · ${durLabel} · ${sesLabel}`;
  }

  _pendingOrder = payload;
  const div = document.getElementById('trade-result');
  div.innerHTML =
    '<div class="preview-box">' +
      '<div class="preview-title">⚠️ Review Order Before Submitting</div>' +
      '<div class="preview-summary">' + summary + '</div>' +
      '<div style="font-size:11px;color:#64748b;margin-bottom:14px">' +
        'Session: Normal (Day order) &middot; Please verify all details before confirming.' +
      '</div>' +
      '<div class="preview-actions">' +
        '<button class="cancel-btn" onclick="clearTradeResult()">✕ Cancel</button>' +
        '<button class="confirm-btn" onclick="submitPendingOrder()">✓ Confirm &amp; Submit</button>' +
      '</div>' +
    '</div>';
}

async function submitPendingOrder() {
  if (!_pendingOrder) return;
  const payload = _pendingOrder;
  _pendingOrder = null;
  const div = document.getElementById('trade-result');
  div.innerHTML = '<div class="loading">Submitting order to Schwab…</div>';
  try {
    const res = await fetch('/api/order', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    }).then(r => r.json());

    if (res.error) throw new Error(res.error);
    div.innerHTML =
      '<div class="success-box">✅ Order submitted successfully!<br>' +
      '<b>Order ID: ' + esc(res.order_id) + '</b><br>' +
      '<small style="color:#6ee7b7;opacity:0.7">Switch to the "Open Orders" tab to track status.</small>' +
      '</div>';
    ordersState.loaded = false; // force refresh on next Open Orders visit
  } catch(e) {
    div.innerHTML = '<div class="error" style="margin-top:0">❌ Order failed: ' + esc(e.message) + '</div>';
  }
}

function clearTradeResult() {
  document.getElementById('trade-result').innerHTML = '';
  _pendingOrder = null;
}

function showTradeError(msg) {
  document.getElementById('trade-result').innerHTML =
    '<div class="error" style="margin-top:12px;padding:10px 14px;border-radius:6px">' + msg + '</div>';
}

// ── Open Orders ────────────────────────────────────────────────────
let _allOrders = [];
let _ordSortCol = 'entered_time', _ordSortDir = -1;
const ordersState = { loaded: false };

async function loadOrders() {
  document.getElementById('ord-loading').style.display='block';
  document.getElementById('ord-table').style.display='none';
  document.getElementById('ord-empty').style.display='none';
  document.getElementById('ord-error').style.display='none';
  try {
    const raw = await fetch('/api/orders').then(r => r.json());
    if (raw.error) throw new Error(raw.error);
    _allOrders = raw;
    ordersState.loaded = true;
    document.getElementById('ord-loading').style.display='none';
    filterOrders();
  } catch(e) {
    document.getElementById('ord-loading').style.display='none';
    document.getElementById('ord-error').style.display='block';
    document.getElementById('ord-error').textContent = 'Error: ' + e.message;
  }
}

function sortOrders(col) {
  if (_ordSortCol === col) _ordSortDir *= -1;
  else { _ordSortCol = col; _ordSortDir = -1; }
  filterOrders();
}

function filterOrders() {
  const ticker = document.getElementById('ord-ticker').value.trim().toUpperCase();
  const type   = document.getElementById('ord-type').value;
  const status = document.getElementById('ord-status').value;

  let rows = _allOrders.filter(o => {
    if (ticker && !o.underlying.includes(ticker) && !o.symbol.includes(ticker)) return false;
    if (type   && o.order_type !== type)   return false;
    if (status && o.status !== status)     return false;
    return true;
  });

  rows.sort((a, b) => {
    let av = a[_ordSortCol], bv = b[_ordSortCol];
    if (av === null || av === undefined) av = '';
    if (bv === null || bv === undefined) bv = '';
    if (av < bv) return  _ordSortDir;
    if (av > bv) return -_ordSortDir;
    return 0;
  });

  document.getElementById('ord-count').textContent = rows.length + ' open order' + (rows.length !== 1 ? 's' : '');

  if (rows.length === 0) {
    document.getElementById('ord-table').style.display='none';
    document.getElementById('ord-empty').style.display='block';
    return;
  }
  document.getElementById('ord-empty').style.display='none';
  document.getElementById('ord-table').style.display='block';

  document.getElementById('ord-tbody').innerHTML = rows.map(o => {
    const sideClass = (o.instruction||'').includes('SELL') ? 'badge-PUT' : 'badge-equity';
    const priceStr  = o.price != null ? '$' + fmt(o.price, 4)
                    : o.stop_price != null ? 'Stop $' + fmt(o.stop_price) : '—';
    const cancelBtn = o.cancelable
      ? '<button class="cancel-order-btn" onclick="cancelOrder(' + esc(o.order_id) + ')">Cancel</button>'
      : '—';
    return '<tr>' +
      '<td style="color:#475569;font-size:11px;font-family:monospace">' + esc(o.order_id) + '</td>' +
      '<td><span class="badge badge-status-' + o.status + '">' + o.status + '</span></td>' +
      '<td style="color:#94a3b8">' + o.order_type + '</td>' +
      '<td><b>' + esc(o.underlying) + '</b></td>' +
      '<td style="color:#64748b;max-width:200px;overflow:hidden;text-overflow:ellipsis" title="' + esc(o.symbol) + '">' + esc(o.symbol) + '</td>' +
      '<td><span class="badge ' + sideClass + '">' + esc(o.instruction) + '</span></td>' +
      '<td>' + fmt(o.quantity, 0) + '</td>' +
      '<td>' + fmt(o.filled_quantity, 0) + '</td>' +
      '<td>' + priceStr + '</td>' +
      '<td style="color:#64748b;font-size:12px">' + o.entered_time + '</td>' +
      '<td>' + cancelBtn + '</td>' +
    '</tr>';
  }).join('');
}

async function cancelOrder(orderId) {
  if (!confirm('Cancel order ' + orderId + '?')) return;
  try {
    const res = await fetch('/api/order/' + orderId, { method: 'DELETE' }).then(r => r.json());
    if (res.error) throw new Error(res.error);
    await loadOrders();
  } catch(e) {
    alert('Failed to cancel order: ' + e.message);
  }
}

// ── Init ──────────────────────────────────────────────────────────
document.getElementById('lastUpdated').textContent = 'Updated ' + new Date().toLocaleTimeString();
loadPositions();
loadQuotes();
</script>
</body>
</html>"""

@app.route("/")
def dashboard():
    return render_template_string(DASHBOARD_HTML)


if __name__ == "__main__":
    print()
    print("=" * 50)
    print("  ss7trading dashboard")
    print("  http://127.0.0.1:5050")
    print("=" * 50)
    print()
    app.run(host="127.0.0.1", port=5050, debug=True, use_reloader=True)
