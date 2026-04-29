"""Order cleaning and building."""

import datetime

from services.options_parsing import parse_option_symbol


def _trim_iso(s):
    if not s:
        return ""
    return s[:19].replace("T", " ")


def clean_orders(orders_data):
    """Flatten Schwab order objects into simple dicts for the UI.

    Adds per-leg detail (parsed OSI for options), an ``underlyings`` list,
    and ``release_time`` / ``cancel_time`` fields used by the orders UI to
    surface order expiry and option DTE.
    """
    result = []
    for order in (orders_data or []):
        legs = order.get("orderLegCollection", []) or []
        first = legs[0] if legs else {}
        first_instr = first.get("instrument", {}) or {}
        symbol = first_instr.get("symbol", "")
        asset_type = first_instr.get("assetType", "")
        instruction = first.get("instruction", "")
        underlying = symbol.split()[0] if " " in symbol else symbol

        legs_detail = []
        underlyings = []
        seen_under = set()
        for leg in legs:
            instr = leg.get("instrument", {}) or {}
            sym = instr.get("symbol", "")
            atype = instr.get("assetType", "")
            entry = {
                "symbol": sym,
                "asset_type": atype,
                "instruction": leg.get("instruction", ""),
                "quantity": leg.get("quantity", 0),
            }
            leg_under = sym.split()[0] if " " in sym else sym
            if atype == "OPTION":
                parsed = parse_option_symbol(sym)
                if parsed:
                    entry["option_type"] = parsed["option_type"]
                    entry["strike"] = parsed["option_strike"]
                    entry["expiration_date"] = parsed["option_expiry"]
                    leg_under = parsed["underlying"]
            if leg_under and leg_under not in seen_under:
                seen_under.add(leg_under)
                underlyings.append(leg_under)
            legs_detail.append(entry)

        result.append({
            "order_id": str(order.get("orderId", "")),
            "status": order.get("status", ""),
            "order_type": order.get("orderType", ""),
            "session": order.get("session", ""),
            "duration": order.get("duration", ""),
            "symbol": symbol,
            "underlying": underlying,
            "underlyings": underlyings,
            "asset_type": asset_type,
            "instruction": instruction,
            "quantity": order.get("quantity", 0),
            "filled_quantity": order.get("filledQuantity", 0),
            "remaining_quantity": order.get("remainingQuantity", 0),
            "price": order.get("price"),
            "stop_price": order.get("stopPrice"),
            "entered_time": _trim_iso(order.get("enteredTime")),
            "close_time": _trim_iso(order.get("closeTime")),
            "release_time": _trim_iso(order.get("releaseTime")),
            "cancel_time": _trim_iso(order.get("cancelTime")),
            "cancelable": order.get("cancelable", False),
            "editable": order.get("editable", False),
            "legs": len(legs),
            "legs_detail": legs_detail,
        })
    return result


def build_equity_order(data):
    """Build an equity/ETF order using schwab-py convenience functions."""
    from schwab.orders import equities as EQ
    from schwab.orders.common import (
        Duration,
        EquityInstruction,
        OrderStrategyType,
        OrderType,
        Session,
    )
    from schwab.orders.options import OrderBuilder

    action = data["action"]
    symbol = data["symbol"].strip().upper()
    qty = int(data["quantity"])
    order_type = data["order_type"]
    price = data.get("price")
    stop_price = data.get("stop_price")
    duration = data.get("duration", "day")
    session = data.get("session", "normal")

    dur_map = {"day": Duration.DAY, "gtc": Duration.GOOD_TILL_CANCEL}
    ses_map = {
        "normal": Session.NORMAL,
        "am": Session.AM,
        "pm": Session.PM,
        "seamless": Session.SEAMLESS,
    }

    if order_type == "market":
        fn = {
            "buy": EQ.equity_buy_market,
            "sell": EQ.equity_sell_market,
            "sell_short": EQ.equity_sell_short_market,
            "buy_to_cover": EQ.equity_buy_to_cover_market,
        }[action]
        order = fn(symbol, qty)

    elif order_type == "limit":
        fn = {
            "buy": EQ.equity_buy_limit,
            "sell": EQ.equity_sell_limit,
            "sell_short": EQ.equity_sell_short_limit,
            "buy_to_cover": EQ.equity_buy_to_cover_limit,
        }[action]
        order = fn(symbol, qty, float(price))

    else:
        inst_map = {
            "buy": EquityInstruction.BUY,
            "sell": EquityInstruction.SELL,
            "sell_short": EquityInstruction.SELL_SHORT,
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


def build_option_order(data):
    """Build a single-leg option order using schwab-py convenience functions."""
    from schwab.orders import options as OP
    from schwab.orders.common import Duration, Session

    underlying = data["underlying"].strip().upper()
    option_type = data["option_type"].upper()
    action = data["action"]
    expiry_str = data["expiry"]
    strike = data["strike"]
    qty = int(data["contracts"])
    order_type = data["order_type"]
    price = data.get("price")
    duration = data.get("duration", "day")
    session = data.get("session", "normal")

    expiry = datetime.date.fromisoformat(expiry_str)
    symbol = OP.OptionSymbol(underlying, expiry, option_type[0], str(strike)).build()

    dur_map = {"day": Duration.DAY, "gtc": Duration.GOOD_TILL_CANCEL}
    ses_map = {
        "normal": Session.NORMAL,
        "am": Session.AM,
        "pm": Session.PM,
        "seamless": Session.SEAMLESS,
    }

    fn_map = {
        ("buy_to_open", "limit"): OP.option_buy_to_open_limit,
        ("buy_to_open", "market"): OP.option_buy_to_open_market,
        ("sell_to_open", "limit"): OP.option_sell_to_open_limit,
        ("sell_to_open", "market"): OP.option_sell_to_open_market,
        ("buy_to_close", "limit"): OP.option_buy_to_close_limit,
        ("buy_to_close", "market"): OP.option_buy_to_close_market,
        ("sell_to_close", "limit"): OP.option_sell_to_close_limit,
        ("sell_to_close", "market"): OP.option_sell_to_close_market,
    }
    fn = fn_map.get((action, order_type))
    if fn is None:
        raise ValueError(f"Unsupported action/order_type: {action}/{order_type}")

    order = fn(symbol, qty, float(price)) if order_type == "limit" else fn(symbol, qty)
    order.set_duration(dur_map.get(duration, Duration.DAY))
    order.set_session(ses_map.get(session, Session.NORMAL))
    return order


def build_multi_leg_order(data):
    """Build a multi-leg order (spread, collar, covered, bundle) using OrderBuilder."""
    from schwab.orders import options as OP  # noqa: F401
    from schwab.orders.common import (
        ComplexOrderStrategyType,
        Duration,
        EquityInstruction,
        OptionInstruction,
        OrderStrategyType,
        OrderType,
        Session,
    )
    from schwab.orders.generic import OrderBuilder

    strategy = data.get("strategy", "")
    legs = data.get("legs", [])
    order_type = data.get("order_type", "NET_CREDIT")
    price = data.get("price")
    duration = data.get("duration", "day")
    session = data.get("session", "normal")

    if not legs:
        raise ValueError("No legs provided")

    ot_map = {
        "NET_CREDIT": OrderType.NET_CREDIT,
        "NET_DEBIT": OrderType.NET_DEBIT,
        "NET_ZERO": OrderType.NET_ZERO,
        "LIMIT": OrderType.LIMIT,
        "MARKET": OrderType.MARKET,
    }
    cos_map = {
        "vertical": ComplexOrderStrategyType.VERTICAL,
        "collar": ComplexOrderStrategyType.COLLAR_SYNTHETIC,
        "covered": ComplexOrderStrategyType.COVERED,
        "bundle": ComplexOrderStrategyType.NONE,
        "naked": ComplexOrderStrategyType.NONE,
    }
    opt_inst = {
        "BUY_TO_OPEN": OptionInstruction.BUY_TO_OPEN,
        "SELL_TO_OPEN": OptionInstruction.SELL_TO_OPEN,
        "BUY_TO_CLOSE": OptionInstruction.BUY_TO_CLOSE,
        "SELL_TO_CLOSE": OptionInstruction.SELL_TO_CLOSE,
    }
    eq_inst = {
        "BUY": EquityInstruction.BUY,
        "SELL": EquityInstruction.SELL,
        "SELL_SHORT": EquityInstruction.SELL_SHORT,
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
        "normal": Session.NORMAL,
        "am": Session.AM,
        "pm": Session.PM,
        "seamless": Session.SEAMLESS,
    }
    builder.set_duration(dur_map.get(duration, Duration.DAY))
    builder.set_session(ses_map.get(session, Session.NORMAL))
    return builder
