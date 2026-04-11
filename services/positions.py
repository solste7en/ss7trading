"""Position data cleaning and helpers."""

import re

from db import get_position_lists

_ASSET_TYPE_MAP = {
    "COLLECTIVE_INVESTMENT": "ETF",
}

_OCC_RE = re.compile(
    r'^(?P<under>.{6})(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})[CP](?P<strike>\d{8})$'
)


def parse_occ(symbol):
    """Extract (expiry_iso, strike_float) from a 21-char OCC symbol."""
    m = _OCC_RE.match(symbol.replace(' ', ' ').ljust(21)[:21])
    if not m:
        return None, None
    expiry = f"20{m.group('yy')}-{m.group('mm')}-{m.group('dd')}"
    strike = int(m.group('strike')) / 1000.0
    return expiry, strike


def clean_positions(accounts_data):
    """Parse the Schwab account response into a flat list of position dicts."""
    positions = []
    for acct in accounts_data:
        acct_info = acct.get("securitiesAccount", {})
        acct_number = acct_info.get("accountNumber", "")
        for pos in acct_info.get("positions", []):
            instrument = pos.get("instrument", {})
            raw_type = instrument.get("assetType", "")
            asset_type = _ASSET_TYPE_MAP.get(raw_type, raw_type)
            symbol = instrument.get("symbol", "")
            description = instrument.get("description", symbol)
            put_call = instrument.get("putCall")
            underlying_symbol = instrument.get("underlyingSymbol")

            qty = pos.get("longQuantity", 0) - pos.get("shortQuantity", 0)
            avg_price = pos.get("averagePrice")
            mkt_value = pos.get("marketValue")
            day_pl = pos.get("currentDayProfitLoss")
            day_pl_pct = pos.get("currentDayProfitLossPercentage")

            if qty >= 0:
                unrealized_pl = pos.get("longOpenProfitLoss")
            else:
                unrealized_pl = pos.get("shortOpenProfitLoss")

            current_price = None
            if qty and mkt_value is not None:
                if asset_type == "OPTION":
                    current_price = mkt_value / (abs(qty) * 100)
                else:
                    current_price = abs(float(mkt_value)) / abs(qty)

            if asset_type in ("EQUITY", "ETF") and qty < 0 and avg_price is not None:
                avg_price = -abs(float(avg_price))

            option_expiry, option_strike = (None, None)
            if asset_type == "OPTION":
                option_expiry, option_strike = parse_occ(symbol)

            positions.append({
                "account": acct_number[-4:],
                "symbol": symbol,
                "description": description,
                "asset_type": asset_type,
                "put_call": put_call,
                "underlying_symbol": underlying_symbol,
                "option_expiry": option_expiry,
                "option_strike": option_strike,
                "quantity": qty,
                "avg_price": avg_price,
                "current_price": current_price,
                "market_value": mkt_value,
                "unrealized_pl": unrealized_pl,
                "day_pl": day_pl,
                "day_pl_pct": day_pl_pct,
            })
    order = {"EQUITY": 0, "ETF": 1, "OPTION": 2, "CASH_EQUIVALENT": 3}
    positions.sort(key=lambda p: (order.get(p["asset_type"], 9), p["symbol"]))
    return positions


def position_list_ids():
    return {row["id"] for row in get_position_lists()}
