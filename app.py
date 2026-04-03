"""
app.py — ss7trading dashboard
Run: python app.py
Visit: http://127.0.0.1:5050
"""
import datetime
import re
import threading
import time
import traceback
from pathlib import Path
from flask import Flask, jsonify, render_template, request
import schwab
from auth import get_client
from db import (
    get_transactions, get_realized_gains, get_top_tickers, suggest_position_unwind,
    get_watchlists, create_watchlist, delete_watchlist,
    get_watchlist_symbols, add_watchlist_symbol, remove_watchlist_symbol,
    get_income_trades, get_income_stats,
)
from sync_trades import parse_schwab_transaction
from income_sync import run_sync as run_income_sync

# ── Schwab order API rate-limiter ──────────────────────────────────────────────
# Schwab enforces a burst limit on order writes (place + cancel).
# A 600 ms gap between successive calls stays well within the allowed rate
# and adds only ~0.6 s per rung — imperceptible for typical ladder sizes.
# All order-placement and cancel calls route through _throttled_order_call()
# so any future batch strategy automatically inherits this behaviour.
#
# Tune ORDER_INTERVAL_S here if Schwab tightens or relaxes the limit.
_ORDER_INTERVAL_S: float = 0.6        # min seconds between successive order API calls
_order_lock                = threading.Lock()
_last_order_ts: float      = 0.0      # epoch-seconds of the most recent call


def _throttled_order_call(fn, *args, **kwargs):
    """
    Generic throttle wrapper for any Schwab order-mutating API call.
    Acquires the shared lock, sleeps only as long as needed to honour
    _ORDER_INTERVAL_S since the previous call, then fires fn(*args, **kwargs).

    Usage:
        resp = _throttled_order_call(client.place_order, account_hash, order)
        resp = _throttled_order_call(client.cancel_order, order_id, account_hash)
    """
    global _last_order_ts
    with _order_lock:
        wait_for = _ORDER_INTERVAL_S - (time.monotonic() - _last_order_ts)
        if wait_for > 0:
            time.sleep(wait_for)
        _last_order_ts = time.monotonic()
    return fn(*args, **kwargs)

BASE_DIR = Path(__file__).parent

app = Flask(__name__)

@app.errorhandler(Exception)
def handle_exception(e):
    return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500

# ── helpers ────────────────────────────────────────────────────────────────────

_ASSET_TYPE_MAP = {
    "COLLECTIVE_INVESTMENT": "ETF",
}

_OCC_RE = re.compile(
    r'^(?P<under>.{6})(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})[CP](?P<strike>\d{8})$'
)

def _parse_occ(symbol):
    """Extract (expiry_iso, strike_float) from a 21-char OCC symbol.
    e.g. 'NVDA  260410P00170000' → ('2026-04-10', 170.0)
    Returns (None, None) if the symbol doesn't match.
    """
    m = _OCC_RE.match(symbol.replace(' ', ' ').ljust(21)[:21])
    if not m:
        return None, None
    expiry = f"20{m.group('yy')}-{m.group('mm')}-{m.group('dd')}"
    strike = int(m.group('strike')) / 1000.0
    return expiry, strike


def _clean_positions(accounts_data):
    """Parse the Schwab account response into a flat list of position dicts."""
    positions = []
    for acct in accounts_data:
        acct_info = acct.get("securitiesAccount", {})
        acct_number = acct_info.get("accountNumber", "")
        for pos in acct_info.get("positions", []):
            instrument         = pos.get("instrument", {})
            raw_type           = instrument.get("assetType", "")
            asset_type         = _ASSET_TYPE_MAP.get(raw_type, raw_type)
            symbol             = instrument.get("symbol", "")
            description        = instrument.get("description", symbol)
            put_call           = instrument.get("putCall")          # "PUT" | "CALL" | None
            underlying_symbol  = instrument.get("underlyingSymbol") # e.g. "NVDA" for options

            qty        = pos.get("longQuantity", 0) - pos.get("shortQuantity", 0)
            avg_price  = pos.get("averagePrice")
            mkt_value  = pos.get("marketValue")
            day_pl     = pos.get("currentDayProfitLoss")
            day_pl_pct = pos.get("currentDayProfitLossPercentage")

            # Unrealized P&L: long positions use longOpenProfitLoss,
            # short positions use shortOpenProfitLoss (previously always None for shorts).
            if qty >= 0:
                unrealized_pl = pos.get("longOpenProfitLoss")
            else:
                unrealized_pl = pos.get("shortOpenProfitLoss")

            # Current price: per-share for equity/ETF, per-share for options (÷100)
            current_price = None
            if qty and mkt_value is not None:
                if asset_type == "OPTION":
                    current_price = mkt_value / (abs(qty) * 100)
                else:
                    current_price = mkt_value / abs(qty)

            # Parse expiry and strike from OCC symbol for options
            option_expiry, option_strike = (None, None)
            if asset_type == "OPTION":
                option_expiry, option_strike = _parse_occ(symbol)

            positions.append({
                "account":            acct_number[-4:],
                "symbol":             symbol,
                "description":        description,
                "asset_type":         asset_type,
                "put_call":           put_call,
                "underlying_symbol":  underlying_symbol,
                "option_expiry":      option_expiry,   # ISO "2026-04-10" or None
                "option_strike":      option_strike,   # float e.g. 170.0 or None
                "quantity":           qty,
                "avg_price":          avg_price,
                "current_price":      current_price,
                "market_value":       mkt_value,
                "unrealized_pl":      unrealized_pl,
                "day_pl":             day_pl,
                "day_pl_pct":         day_pl_pct,
            })
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


@app.route("/api/quotes/list/<int:list_id>")
def api_quotes_for_list(list_id):
    """Returns live quotes for all symbols in a watchlist."""
    try:
        symbols = get_watchlist_symbols(list_id)
        if not symbols:
            return jsonify([])
        client = get_client()
        resp = client.get_quotes(symbols)
        resp.raise_for_status()
        return jsonify(_clean_quotes(resp.json()))
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/watchlists", methods=["GET", "POST"])
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


@app.route("/api/watchlists/<int:list_id>", methods=["DELETE"])
def api_watchlist_delete(list_id):
    try:
        delete_watchlist(list_id)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/watchlists/<int:list_id>/symbols", methods=["GET", "POST"])
def api_watchlist_symbols(list_id):
    if request.method == "GET":
        return jsonify(get_watchlist_symbols(list_id))
    data = request.get_json() or {}
    sym = (data.get("symbol") or "").strip().upper()
    if not sym:
        return jsonify({"error": "symbol required"}), 400
    add_watchlist_symbol(list_id, sym)
    return jsonify({"ok": True})


@app.route("/api/watchlists/<int:list_id>/symbols/<symbol>", methods=["DELETE"])
def api_watchlist_symbol_delete(list_id, symbol):
    remove_watchlist_symbol(list_id, symbol.upper())
    return jsonify({"ok": True})


# ── Trade History & Realized G/L endpoints (delegated to db.py) ────────────────

@app.route("/api/transactions")
def api_transactions():
    try:
        page     = max(1, int(request.args.get("page", 1)))
        limit    = min(100, max(10, int(request.args.get("limit", 25))))
        category = request.args.get("category", "").strip()
        ticker   = request.args.get("ticker", "").strip().upper()
        search   = request.args.get("search", "").strip()
        return jsonify(get_transactions(page, limit, category, ticker, search))
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/realized_gains")
def api_realized_gains():
    try:
        page   = max(1, int(request.args.get("page", 1)))
        limit  = min(100, max(10, int(request.args.get("limit", 25))))
        ticker = request.args.get("ticker", "").strip().upper()
        term   = request.args.get("term", "").strip()
        return jsonify(get_realized_gains(page, limit, ticker, term))
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


# ── Top Tickers endpoint ──────────────────────────────────────────────────────

@app.route("/api/top-tickers")
def api_top_tickers():
    """Return the 10 most-traded tickers with their last 5 executed trades."""
    try:
        return jsonify(get_top_tickers(top_n=10, recent_n=10))
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


# ── Live transaction cross-reference endpoint ─────────────────────────────────

@app.route("/api/transactions/live")
def api_transactions_live():
    """Fetch recent transactions for a ticker directly from Schwab API and
    compare against the local DB. Returns both API and DB rows so the UI
    can highlight any gaps or mismatches."""
    try:
        ticker = request.args.get("ticker", "").strip().upper()
        days   = max(7, min(365, int(request.args.get("days", 180))))
        if not ticker:
            return jsonify({"error": "ticker is required"}), 400

        client    = get_client()
        resp      = client.get_account_numbers()
        resp.raise_for_status()
        acct_hash = resp.json()[0]["hashValue"]

        end_dt   = datetime.datetime.now(datetime.timezone.utc)
        start_dt = end_dt - datetime.timedelta(days=days)

        resp = client.get_transactions(
            account_hash=acct_hash,
            start_date=start_dt,
            end_date=end_dt,
        )
        resp.raise_for_status()
        raw_txs = resp.json() or []

        # Parse and filter for this ticker
        api_rows = []
        for raw in raw_txs:
            try:
                parsed = parse_schwab_transaction(raw)
                if parsed and parsed.get("underlying") == ticker:
                    api_rows.append(parsed)
            except Exception:
                pass

        api_rows.sort(key=lambda r: r.get("trade_date", ""), reverse=True)

        # Pull matching DB rows for the same date window
        from db import get_transactions as _get_tx
        db_result = _get_tx(
            page=1, limit=500,
            ticker=ticker,
            category="",
        )
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
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


# ── Ladder suggestion endpoint ─────────────────────────────────────────────────

@app.route("/api/ladder-suggest")
def api_ladder_suggest():
    """Analyse recent equity trades and suggest position-unwind ladder rungs."""
    try:
        ticker = request.args.get("ticker", "").strip().upper()
        if not ticker:
            return jsonify({"error": "ticker is required"}), 400
        window_size   = max(2, min(20, int(request.args.get("window_size", 5))))
        sell_pct      = max(1, min(100, int(request.args.get("sell_pct", 25)))) / 100.0
        premium_cents = max(1, min(99, int(request.args.get("premium_cents", 77))))
        min_streak    = max(1, min(100, int(request.args.get("min_streak", 10))))
        max_rungs     = max(1, min(20, int(request.args.get("max_rungs", 5))))
        return jsonify(suggest_position_unwind(
            ticker, window_size, sell_pct, premium_cents, min_streak, max_rungs))
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


# ── Option chain endpoints ─────────────────────────────────────────────────────

@app.route("/api/option-expirations/<symbol>")
def api_option_expirations(symbol):
    """Return available option expiration dates for a symbol."""
    try:
        client = get_client()
        resp = client.get_option_expiration_chain(symbol.upper())
        resp.raise_for_status()
        raw = resp.json() or {}
        expirations = []
        for item in raw.get("expirationList", []):
            date_str = item.get("expirationDate", "")
            if date_str:
                expirations.append(date_str[:10])
        return jsonify({"symbol": symbol.upper(), "expirations": sorted(expirations)})
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


def _clean_option_map(exp_date_map):
    """Flatten Schwab's nested {expiryDate: {strike: [contracts]}} into a simple list."""
    result = {}
    for exp_key, strikes in (exp_date_map or {}).items():
        exp_date = exp_key.split(":")[0]
        contracts = []
        for strike_key, chain_items in strikes.items():
            for c in chain_items:
                contracts.append({
                    "strike":      c.get("strikePrice"),
                    "bid":         c.get("bid"),
                    "ask":         c.get("ask"),
                    "last":        c.get("last"),
                    "volume":      c.get("totalVolume", 0),
                    "oi":          c.get("openInterest", 0),
                    "iv":          c.get("volatility"),
                    "delta":       c.get("delta"),
                    "symbol":      c.get("symbol", ""),
                    "itm":         c.get("inTheMoney", False),
                    "description": c.get("description", ""),
                })
        contracts.sort(key=lambda x: x["strike"] or 0)
        result[exp_date] = contracts
    return result


@app.route("/api/option-chain")
def api_option_chain():
    """Return the option chain for a symbol, simplified for the UI."""
    try:
        symbol       = request.args.get("symbol", "").strip().upper()
        if not symbol:
            return jsonify({"error": "symbol is required"}), 400
        strike_count = min(100, max(5, int(request.args.get("strike_count", 15))))
        contract_type = request.args.get("contract_type", "ALL").upper()

        ct_map = {"ALL": None, "CALL": "CALL", "PUT": "PUT"}
        ct = ct_map.get(contract_type)

        client = get_client()
        kwargs = dict(
            symbol=symbol,
            strike_count=strike_count,
            include_underlying_quote=True,
        )
        if ct:
            kwargs["contract_type"] = ct

        from_date = request.args.get("from_date")
        to_date   = request.args.get("to_date")
        if from_date:
            kwargs["from_date"] = datetime.date.fromisoformat(from_date)
        if to_date:
            kwargs["to_date"] = datetime.date.fromisoformat(to_date)

        resp = client.get_option_chain(**kwargs)
        resp.raise_for_status()
        raw = resp.json() or {}

        underlying = raw.get("underlying", {})
        underlying_clean = {
            "symbol": underlying.get("symbol", symbol),
            "last":   underlying.get("last"),
            "bid":    underlying.get("bid"),
            "ask":    underlying.get("ask"),
            "change": underlying.get("change"),
            "change_pct": underlying.get("percentChange"),
            "volume": underlying.get("totalVolume"),
        }

        calls = _clean_option_map(raw.get("callExpDateMap"))
        puts  = _clean_option_map(raw.get("putExpDateMap"))
        all_exps = sorted(set(list(calls.keys()) + list(puts.keys())))

        return jsonify({
            "symbol":      symbol,
            "underlying":  underlying_clean,
            "expirations": all_exps,
            "calls":       calls,
            "puts":        puts,
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

    action     = data["action"]
    symbol     = data["symbol"].strip().upper()
    qty        = int(data["quantity"])
    order_type = data["order_type"]
    price      = data.get("price")
    stop_price = data.get("stop_price")
    duration   = data.get("duration", "day")
    session    = data.get("session", "normal")

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
    option_type = data["option_type"].upper()
    action      = data["action"]
    expiry_str  = data["expiry"]
    strike      = data["strike"]
    qty         = int(data["contracts"])
    order_type  = data["order_type"]
    price       = data.get("price")
    duration    = data.get("duration", "day")
    session     = data.get("session", "normal")

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


def _build_multi_leg_order(data):
    """Build a multi-leg order (spread, collar, covered, bundle) using OrderBuilder."""
    from schwab.orders.generic import OrderBuilder
    from schwab.orders import options as OP
    from schwab.orders.common import (
        Duration, Session, OrderType, OrderStrategyType,
        ComplexOrderStrategyType, OptionInstruction, EquityInstruction,
    )

    strategy   = data.get("strategy", "")
    legs       = data.get("legs", [])
    order_type = data.get("order_type", "NET_CREDIT")
    price      = data.get("price")
    duration   = data.get("duration", "day")
    session    = data.get("session", "normal")

    if not legs:
        raise ValueError("No legs provided")

    ot_map = {
        "NET_CREDIT": OrderType.NET_CREDIT,
        "NET_DEBIT":  OrderType.NET_DEBIT,
        "NET_ZERO":   OrderType.NET_ZERO,
        "LIMIT":      OrderType.LIMIT,
        "MARKET":     OrderType.MARKET,
    }
    cos_map = {
        "vertical": ComplexOrderStrategyType.VERTICAL,
        "collar":   ComplexOrderStrategyType.COLLAR_SYNTHETIC,
        "covered":  ComplexOrderStrategyType.COVERED,
        "bundle":   ComplexOrderStrategyType.NONE,
        "naked":    ComplexOrderStrategyType.NONE,
    }
    opt_inst = {
        "BUY_TO_OPEN":   OptionInstruction.BUY_TO_OPEN,
        "SELL_TO_OPEN":  OptionInstruction.SELL_TO_OPEN,
        "BUY_TO_CLOSE":  OptionInstruction.BUY_TO_CLOSE,
        "SELL_TO_CLOSE": OptionInstruction.SELL_TO_CLOSE,
    }
    eq_inst = {
        "BUY":          EquityInstruction.BUY,
        "SELL":         EquityInstruction.SELL,
        "SELL_SHORT":   EquityInstruction.SELL_SHORT,
        "BUY_TO_COVER": EquityInstruction.BUY_TO_COVER,
    }

    builder = OrderBuilder()
    builder.set_order_type(ot_map.get(order_type, OrderType.NET_CREDIT))
    builder.set_order_strategy_type(OrderStrategyType.SINGLE)

    if strategy in cos_map:
        builder.set_complex_order_strategy_type(cos_map[strategy])

    for leg in legs:
        sym = leg["symbol"]
        qty = int(leg["quantity"])
        inst = leg["instruction"]
        if leg["type"] == "option":
            builder.add_option_leg(opt_inst[inst], sym, qty)
        elif leg["type"] == "equity":
            builder.add_equity_leg(eq_inst[inst], sym, qty)
        else:
            raise ValueError(f"Unknown leg type: {leg['type']!r}")

    if price is not None and order_type not in ("MARKET", "NET_ZERO"):
        builder.set_price(float(price))

    dur_map = {"day": Duration.DAY, "gtc": Duration.GOOD_TILL_CANCEL}
    ses_map = {
        "normal":   Session.NORMAL,
        "am":       Session.AM,
        "pm":       Session.PM,
        "seamless": Session.SEAMLESS,
    }
    builder.set_duration(dur_map.get(duration, Duration.DAY))
    builder.set_session(ses_map.get(session, Session.NORMAL))
    return builder


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
        client = get_client()
        resp = _throttled_order_call(client.place_order, account_hash, order)
        resp.raise_for_status()

        location = resp.headers.get("Location", "")
        order_id = location.rstrip("/").split("/")[-1] if location else "—"
        return jsonify({"status": "ok", "order_id": order_id})

    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/order/ladder", methods=["POST"])
def api_place_ladder():
    """Place a ladder of limit orders — one per rung."""
    try:
        data       = request.json or {}
        trade_type = data.get("trade_type", "equity")
        rungs      = data.get("rungs", [])

        if not rungs:
            return jsonify({"error": "No rungs provided"}), 400

        account_hash = _get_account_hash()
        client = get_client()
        results = []

        for i, rung in enumerate(rungs, 1):
            try:
                rung_data = {**data, "quantity": rung["quantity"], "price": rung["price"], "order_type": "limit"}

                if trade_type == "equity":
                    order = _build_equity_order(rung_data)
                elif trade_type == "option":
                    order = _build_option_order(rung_data)
                else:
                    results.append({"rung": i, "qty": rung["quantity"], "price": rung["price"],
                                    "status": "error", "error": f"Unknown trade_type: {trade_type!r}"})
                    continue

                resp = _throttled_order_call(client.place_order, account_hash, order)
                resp.raise_for_status()

                location = resp.headers.get("Location", "")
                order_id = location.rstrip("/").split("/")[-1] if location else "—"
                results.append({"rung": i, "qty": rung["quantity"], "price": rung["price"],
                                "status": "ok", "order_id": order_id})

            except Exception as rung_err:
                results.append({"rung": i, "qty": rung["quantity"], "price": rung["price"],
                                "status": "error", "error": str(rung_err)})

        return jsonify({"results": results})

    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/order/<order_id>", methods=["DELETE"])
def api_cancel_order(order_id):
    """Cancel a specific open order by ID."""
    try:
        account_hash = _get_account_hash()
        client = get_client()
        resp = _throttled_order_call(client.cancel_order, order_id, account_hash)
        resp.raise_for_status()
        return jsonify({"status": "cancelled", "order_id": order_id})
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


# ── Strategy order placement ────────────────────────────────────────────────────

@app.route("/api/order/strategy", methods=["POST"])
def api_place_strategy_order():
    """Place a multi-leg strategy order (spread, collar, covered, bundle)."""
    try:
        data = request.json or {}
        order = _build_multi_leg_order(data)
        account_hash = _get_account_hash()
        client = get_client()
        resp = _throttled_order_call(client.place_order, account_hash, order)
        resp.raise_for_status()
        location = resp.headers.get("Location", "")
        order_id = location.rstrip("/").split("/")[-1] if location else "—"
        return jsonify({"status": "ok", "order_id": order_id})
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


# ── Strategy suggestion engine ──────────────────────────────────────────────────

def _suggest_strategies(positions, quote, chain_data, ticker):
    """Analyse positions + option chain and return strategy suggestions."""
    from schwab.orders import options as OP

    suggestions = []
    last = quote.get("last")
    if last is None:
        return suggestions

    eq_pos = [p for p in positions
              if p["asset_type"] in ("EQUITY", "ETF") and p["symbol"] == ticker]
    re_opt = ticker + " "
    opt_pos = [p for p in positions
               if p["asset_type"] == "OPTION" and p["symbol"].startswith(re_opt)]
    eq_qty = sum(p["quantity"] for p in eq_pos)
    avg_price = None
    if eq_pos and eq_pos[0].get("avg_price") is not None:
        avg_price = eq_pos[0]["avg_price"]

    exps = sorted(chain_data.get("expirations", []))
    calls_map = chain_data.get("calls", {})
    puts_map  = chain_data.get("puts", {})

    # Pick the 2 nearest expirations with data
    usable_exps = [e for e in exps if (calls_map.get(e) or puts_map.get(e))][:2]

    for expiry in usable_exps:
        calls = calls_map.get(expiry, [])
        puts  = puts_map.get(expiry, [])
        days_to_exp = max(1, (datetime.date.fromisoformat(expiry) - datetime.date.today()).days)

        if eq_qty > 0:
            # ── LONG POSITION strategies ────────────────────────────
            coverable = abs(eq_qty) // 100
            if coverable < 1:
                continue

            # 1. Covered Call — sell OTM calls
            otm_calls = [c for c in calls
                         if c["strike"] and c["strike"] > last
                         and c["bid"] and c["bid"] > 0.05]
            otm_calls.sort(key=lambda c: c["strike"])
            for c in otm_calls[:3]:
                premium = c["bid"]
                total_prem = premium * coverable * 100
                ann_yield = (premium / last) * (365 / days_to_exp)
                occ = c.get("symbol", "")
                suggestions.append({
                    "id": f"cc_{expiry}_{c['strike']}",
                    "strategy": "naked",
                    "title": "Covered Call",
                    "description": f"Sell {coverable} CALL @ ${c['strike']:.2f}, {expiry}",
                    "detail": f"Premium ~${premium:.2f}/sh (${total_prem:,.0f} total) · {ann_yield:.0%} ann. yield",
                    "legs": [{
                        "type": "option", "instruction": "SELL_TO_OPEN",
                        "option_type": "CALL", "strike": c["strike"],
                        "expiry": expiry, "quantity": coverable,
                        "symbol": occ, "est_premium": premium,
                    }],
                    "order_type": "LIMIT",
                    "price": premium,
                    "net_credit": total_prem,
                    "max_profit": (c["strike"] - last) * abs(eq_qty) + total_prem if avg_price else None,
                    "breakeven": round(last - premium, 2),
                    "annualized_yield": round(ann_yield, 4),
                })

            # 2. Protective Collar — sell OTM call + buy OTM put
            otm_puts = [p for p in puts
                        if p["strike"] and p["strike"] < last
                        and p["ask"] and p["ask"] > 0]
            if otm_calls and otm_puts:
                otm_puts.sort(key=lambda p: -p["strike"])
                # Try to find near-zero-cost collar
                best_collar = None
                best_net = 999
                for cc in otm_calls[:4]:
                    for pp in otm_puts[:4]:
                        net = pp["ask"] - cc["bid"]
                        if abs(net) < best_net:
                            best_net = abs(net)
                            best_collar = (cc, pp, net)
                if best_collar:
                    cc, pp, net = best_collar
                    net_total = net * coverable * 100
                    ot = "NET_DEBIT" if net > 0.005 else ("NET_CREDIT" if net < -0.005 else "NET_ZERO")
                    suggestions.append({
                        "id": f"collar_{expiry}_{cc['strike']}_{pp['strike']}",
                        "strategy": "collar",
                        "title": "Protective Collar",
                        "description": f"Sell {coverable} CALL @ ${cc['strike']:.2f} + Buy {coverable} PUT @ ${pp['strike']:.2f}, {expiry}",
                        "detail": f"Net {'credit' if net < 0 else 'debit'}: ${abs(net):.2f}/sh (${abs(net_total):,.0f} total) · Protection below ${pp['strike']:.2f}",
                        "legs": [
                            {"type": "option", "instruction": "SELL_TO_OPEN",
                             "option_type": "CALL", "strike": cc["strike"],
                             "expiry": expiry, "quantity": coverable,
                             "symbol": cc.get("symbol", ""), "est_premium": cc["bid"]},
                            {"type": "option", "instruction": "BUY_TO_OPEN",
                             "option_type": "PUT", "strike": pp["strike"],
                             "expiry": expiry, "quantity": coverable,
                             "symbol": pp.get("symbol", ""), "est_premium": pp["ask"]},
                        ],
                        "order_type": ot,
                        "price": round(abs(net), 2),
                        "net_credit": round(-net_total, 2),
                        "breakeven": round(last + net, 2),
                    })

            # 3. Bear Call Spread (credit) — sell lower call, buy higher call
            if len(otm_calls) >= 2:
                sell_c = otm_calls[0]
                buy_c  = otm_calls[min(2, len(otm_calls)-1)]
                if sell_c["bid"] and buy_c["ask"] and sell_c["bid"] > buy_c["ask"]:
                    net_cr = sell_c["bid"] - buy_c["ask"]
                    width  = buy_c["strike"] - sell_c["strike"]
                    suggestions.append({
                        "id": f"spread_{expiry}_{sell_c['strike']}_{buy_c['strike']}",
                        "strategy": "vertical",
                        "title": "Call Credit Spread",
                        "description": f"Sell {coverable} CALL @ ${sell_c['strike']:.2f} + Buy {coverable} CALL @ ${buy_c['strike']:.2f}, {expiry}",
                        "detail": f"Net credit: ${net_cr:.2f}/sh · Max risk: ${width - net_cr:.2f}/sh",
                        "legs": [
                            {"type": "option", "instruction": "SELL_TO_OPEN",
                             "option_type": "CALL", "strike": sell_c["strike"],
                             "expiry": expiry, "quantity": coverable,
                             "symbol": sell_c.get("symbol", ""), "est_premium": sell_c["bid"]},
                            {"type": "option", "instruction": "BUY_TO_OPEN",
                             "option_type": "CALL", "strike": buy_c["strike"],
                             "expiry": expiry, "quantity": coverable,
                             "symbol": buy_c.get("symbol", ""), "est_premium": buy_c["ask"]},
                        ],
                        "order_type": "NET_CREDIT",
                        "price": round(net_cr, 2),
                        "net_credit": round(net_cr * coverable * 100, 2),
                        "max_loss": round((width - net_cr) * coverable * 100, 2),
                    })

        elif eq_qty < 0:
            # ── SHORT POSITION strategies ───────────────────────────
            coverable = abs(eq_qty) // 100
            if coverable < 1:
                continue

            # 1. Cash-Secured Put — sell OTM puts
            otm_puts = [p for p in puts
                        if p["strike"] and p["strike"] < last
                        and p["bid"] and p["bid"] > 0.05]
            otm_puts.sort(key=lambda p: -p["strike"])
            for p in otm_puts[:3]:
                premium = p["bid"]
                total_prem = premium * coverable * 100
                ann_yield = (premium / last) * (365 / days_to_exp)
                suggestions.append({
                    "id": f"csp_{expiry}_{p['strike']}",
                    "strategy": "naked",
                    "title": "Cash-Secured Put",
                    "description": f"Sell {coverable} PUT @ ${p['strike']:.2f}, {expiry}",
                    "detail": f"Premium ~${premium:.2f}/sh (${total_prem:,.0f} total) · {ann_yield:.0%} ann. yield",
                    "legs": [{
                        "type": "option", "instruction": "SELL_TO_OPEN",
                        "option_type": "PUT", "strike": p["strike"],
                        "expiry": expiry, "quantity": coverable,
                        "symbol": p.get("symbol", ""), "est_premium": premium,
                    }],
                    "order_type": "LIMIT",
                    "price": premium,
                    "net_credit": total_prem,
                    "breakeven": round(p["strike"] - premium, 2),
                    "annualized_yield": round(ann_yield, 4),
                })

            # 2. Short Collar — sell put + buy call (protection for short)
            otm_calls = [c for c in calls
                         if c["strike"] and c["strike"] > last
                         and c["ask"] and c["ask"] > 0]
            if otm_puts and otm_calls:
                otm_calls.sort(key=lambda c: c["strike"])
                best_collar = None
                best_net = 999
                for pp in otm_puts[:4]:
                    for cc in otm_calls[:4]:
                        net = cc["ask"] - pp["bid"]
                        if abs(net) < best_net:
                            best_net = abs(net)
                            best_collar = (pp, cc, net)
                if best_collar:
                    pp, cc, net = best_collar
                    net_total = net * coverable * 100
                    ot = "NET_DEBIT" if net > 0.005 else ("NET_CREDIT" if net < -0.005 else "NET_ZERO")
                    suggestions.append({
                        "id": f"scollar_{expiry}_{pp['strike']}_{cc['strike']}",
                        "strategy": "collar",
                        "title": "Short Collar",
                        "description": f"Sell {coverable} PUT @ ${pp['strike']:.2f} + Buy {coverable} CALL @ ${cc['strike']:.2f}, {expiry}",
                        "detail": f"Net {'debit' if net > 0 else 'credit'}: ${abs(net):.2f}/sh · Upside protection above ${cc['strike']:.2f}",
                        "legs": [
                            {"type": "option", "instruction": "SELL_TO_OPEN",
                             "option_type": "PUT", "strike": pp["strike"],
                             "expiry": expiry, "quantity": coverable,
                             "symbol": pp.get("symbol", ""), "est_premium": pp["bid"]},
                            {"type": "option", "instruction": "BUY_TO_OPEN",
                             "option_type": "CALL", "strike": cc["strike"],
                             "expiry": expiry, "quantity": coverable,
                             "symbol": cc.get("symbol", ""), "est_premium": cc["ask"]},
                        ],
                        "order_type": ot,
                        "price": round(abs(net), 2),
                        "net_credit": round(-net_total, 2),
                    })

            # 3. Put Credit Spread — sell higher put, buy lower put
            if len(otm_puts) >= 2:
                sell_p = otm_puts[0]
                buy_p  = otm_puts[min(2, len(otm_puts)-1)]
                if sell_p["bid"] and buy_p["ask"] and sell_p["bid"] > buy_p["ask"]:
                    net_cr = sell_p["bid"] - buy_p["ask"]
                    width  = sell_p["strike"] - buy_p["strike"]
                    suggestions.append({
                        "id": f"pspread_{expiry}_{sell_p['strike']}_{buy_p['strike']}",
                        "strategy": "vertical",
                        "title": "Put Credit Spread",
                        "description": f"Sell {coverable} PUT @ ${sell_p['strike']:.2f} + Buy {coverable} PUT @ ${buy_p['strike']:.2f}, {expiry}",
                        "detail": f"Net credit: ${net_cr:.2f}/sh · Max risk: ${width - net_cr:.2f}/sh",
                        "legs": [
                            {"type": "option", "instruction": "SELL_TO_OPEN",
                             "option_type": "PUT", "strike": sell_p["strike"],
                             "expiry": expiry, "quantity": coverable,
                             "symbol": sell_p.get("symbol", ""), "est_premium": sell_p["bid"]},
                            {"type": "option", "instruction": "BUY_TO_OPEN",
                             "option_type": "PUT", "strike": buy_p["strike"],
                             "expiry": expiry, "quantity": coverable,
                             "symbol": buy_p.get("symbol", ""), "est_premium": buy_p["ask"]},
                        ],
                        "order_type": "NET_CREDIT",
                        "price": round(net_cr, 2),
                        "net_credit": round(net_cr * coverable * 100, 2),
                        "max_loss": round((width - net_cr) * coverable * 100, 2),
                    })

    # Existing short option positions — suggest roll / close
    for p in opt_pos:
        if p["quantity"] < 0:
            parsed = None
            desc = p.get("description", "")
            m = desc and re.match(
                r'.*?(\d{2}/\d{2}/\d{4})\s+\$([0-9.]+)\s+(Put|Call)', desc, re.IGNORECASE)
            if m:
                suggestions.append({
                    "id": f"close_{p['symbol'][:20]}",
                    "strategy": "naked",
                    "title": f"Close Short {m.group(3).upper()}",
                    "description": f"Buy to close {abs(p['quantity'])} {m.group(3).upper()} @ ${m.group(2)}, exp {m.group(1)}",
                    "detail": f"Current value: {p.get('current_price', '?')}/sh",
                    "legs": [],
                    "order_type": "LIMIT",
                    "price": p.get("current_price"),
                    "is_close": True,
                })

    return suggestions


@app.route("/api/strategy-suggest")
def api_strategy_suggest():
    """Analyse positions and option chain, return strategy suggestions."""
    try:
        ticker = request.args.get("ticker", "").strip().upper()
        if not ticker:
            return jsonify({"error": "ticker is required"}), 400

        client = get_client()

        # Positions
        resp = client.get_accounts(fields=[schwab.client.Client.Account.Fields.POSITIONS])
        resp.raise_for_status()
        positions = _clean_positions(resp.json())

        # Quote
        resp = client.get_quotes([ticker])
        resp.raise_for_status()
        quotes = _clean_quotes(resp.json())
        quote = quotes[0] if quotes else {}

        # Option chain — nearest strikes
        try:
            resp = client.get_option_chain(
                symbol=ticker, strike_count=20, include_underlying_quote=True)
            resp.raise_for_status()
            raw = resp.json() or {}
            chain_data = {
                "calls":       _clean_option_map(raw.get("callExpDateMap")),
                "puts":        _clean_option_map(raw.get("putExpDateMap")),
                "expirations": sorted(set(
                    list(_clean_option_map(raw.get("callExpDateMap")).keys()) +
                    list(_clean_option_map(raw.get("putExpDateMap")).keys())
                )),
            }
        except Exception:
            chain_data = {"calls": {}, "puts": {}, "expirations": []}

        # Filter positions for this ticker
        re_opt = ticker + " "
        eq_pos = [p for p in positions
                  if p["asset_type"] in ("EQUITY", "ETF") and p["symbol"] == ticker]
        opt_pos = [p for p in positions
                   if p["asset_type"] == "OPTION" and p["symbol"].startswith(re_opt)]

        eq_qty = sum(p["quantity"] for p in eq_pos)

        suggestions = _suggest_strategies(positions, quote, chain_data, ticker)

        return jsonify({
            "ticker":      ticker,
            "equity_qty":  eq_qty,
            "quote":       quote,
            "positions":   eq_pos + opt_pos,
            "expirations": chain_data["expirations"],
            "suggestions": suggestions,
        })
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


# ── Income Performance Tracking ────────────────────────────────────────────────

_income_sync_lock = threading.Lock()

@app.route("/api/income/sync", methods=["POST"])
def api_income_sync():
    """Trigger a full re-sync of income trades from Schwab API."""
    if not _income_sync_lock.acquire(blocking=False):
        return jsonify({"error": "Sync already in progress"}), 409
    try:
        result = run_income_sync()
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500
    finally:
        _income_sync_lock.release()


@app.route("/api/income/trades")
def api_income_trades():
    """Paginated income trades with optional filters."""
    try:
        page     = max(1, int(request.args.get("page", 1)))
        limit    = min(100, max(10, int(request.args.get("limit", 25))))
        ticker   = request.args.get("ticker", "").strip().upper()
        status   = request.args.get("status", "").strip()
        strategy = request.args.get("strategy", "").strip()
        outcome  = request.args.get("outcome", "").strip()
        return jsonify(get_income_trades(page, limit, ticker, status, strategy, outcome))
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/income/stats")
def api_income_stats():
    """Aggregate KPI stats for income trades."""
    try:
        ticker = request.args.get("ticker", "").strip().upper()
        return jsonify(get_income_stats(ticker))
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


# ── Dashboard UI ───────────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    return render_template("dashboard.html")


if __name__ == "__main__":
    print()
    print("=" * 50)
    print("  ss7trading dashboard")
    print("  http://127.0.0.1:5050")
    print("=" * 50)
    print()
    app.run(host="127.0.0.1", port=5050, debug=True, use_reloader=True)
