"""
income_sync.py — Sync option income trades from Schwab API into income_trades DB.

Fetches all 2026 transactions from the Schwab API, identifies option income
trades (short options and spreads), matches opening/closing legs using FIFO,
groups legs into trades, calculates P&L, and stores results.
"""
import datetime
import hashlib
import logging
from collections import defaultdict

from auth import get_client
from db import (
    clear_income_trades,
    set_income_sync_time,
    upsert_income_trade,
)
from sync_trades import parse_schwab_transaction

log = logging.getLogger(__name__)

START_DATE = datetime.date(datetime.date.today().year, 1, 1)
PERFECT_WIN_THRESHOLD = 0.03


def run_sync():
    """Full re-sync: fetch from Schwab API, process, store."""
    client = get_client()

    # 1. Get account hash
    resp = client.get_account_numbers()
    resp.raise_for_status()
    acct_hash = resp.json()[0]["hashValue"]

    end_dt = datetime.datetime.now(datetime.UTC)
    start_dt = datetime.datetime(START_DATE.year, 1, 1, tzinfo=datetime.UTC)

    resp = client.get_transactions(
        account_hash=acct_hash,
        start_date=start_dt,
        end_date=end_dt,
    )
    resp.raise_for_status()
    raw_txs = resp.json() or []
    log.info("Fetched %d raw transactions from Schwab API", len(raw_txs))

    # 3a. Build an equity-buy lookup from all TRADE events.
    #     When a short option is assigned, Schwab generates TWO events:
    #       (a) RECEIVE_AND_DELIVER for the option closing  → netAmount = null/0  → parsed as "Expired"
    #       (b) A TRADE equity Buy/Sell at the strike price → this is the assignment signal
    #     We collect every equity TRADE buy/sell so we can reclassify option "Expired" events
    #     that coincide with an equity purchase at the option's strike price.
    equity_trade_lookup: dict[tuple, str] = {}   # (underlying, date, strike) → "Buy"/"Sell"
    for raw in raw_txs:
        if raw.get("type") != "TRADE":
            continue
        try:
            eq = parse_schwab_transaction(raw)
        except Exception:
            continue
        if not eq or eq.get("is_option"):
            continue
        action_eq = eq.get("action", "")
        if action_eq not in ("Buy", "Sell", "Sell Short"):
            continue
        und = eq.get("underlying") or eq.get("symbol")
        price_eq = eq.get("price")
        date_eq = eq.get("trade_date", "")
        if und and price_eq and date_eq:
            key = (und, date_eq, round(float(price_eq), 2))
            equity_trade_lookup[key] = action_eq
    log.info("Built equity-trade lookup with %d entries", len(equity_trade_lookup))

    def _is_assigned_via_equity(underlying, expiry, strike) -> bool:
        """Check if an equity TRADE at `strike` happened within 5 days of `expiry`."""
        try:
            exp_dt = datetime.date.fromisoformat(expiry)
        except (ValueError, TypeError):
            return False
        for delta in range(0, 6):   # expiry day through +5 calendar days
            chk = (exp_dt + datetime.timedelta(days=delta)).isoformat()
            if (underlying, chk, round(float(strike), 2)) in equity_trade_lookup:
                log.info(
                    "Assignment detected for %s %s %.4g (equity trade on %s)",
                    underlying, strike, expiry, chk,
                )
                return True
        return False

    # 3b. Parse into normalised rows, keep only option trades
    option_rows = []
    for raw in raw_txs:
        try:
            parsed = parse_schwab_transaction(raw)
        except Exception:
            continue
        if not parsed:
            continue
        if not parsed.get("is_option"):
            continue
        if parsed.get("trade_date", "") < START_DATE.isoformat():
            continue
        # Reclassify "Expired" events that have a matching equity TRADE at the strike price:
        # these are assignments, not true expirations.
        if parsed.get("action") == "Expired":
            und = parsed.get("underlying")
            expiry = parsed.get("option_expiry")
            strike = parsed.get("option_strike")
            if und and expiry and strike is not None:
                if _is_assigned_via_equity(und, expiry, strike):
                    parsed = dict(parsed)   # copy so we don't mutate the original
                    parsed["action"] = "Assigned"
        option_rows.append(parsed)

    option_rows.sort(key=lambda r: (r["trade_date"], r.get("activity_id") or 0))
    log.info("Parsed %d option transactions for 2026", len(option_rows))

    # 4. FIFO matching: build matched legs
    matched_legs = _match_legs(option_rows)
    log.info("Matched %d leg records", len(matched_legs))

    # 5. Group legs into trades (spreads vs naked)
    trades = _group_into_trades(matched_legs)
    log.info("Grouped into %d income trades", len(trades))

    # 6. For assignments, look up stock prices
    _fill_assignment_prices(client, trades)

    # 7. Calculate P&L and win/loss
    _calculate_pnl(trades)

    # 8. Write to DB
    clear_income_trades()
    for trade in trades:
        legs_data = trade.pop("_legs")
        upsert_income_trade(trade, legs_data)

    set_income_sync_time()
    log.info("Sync complete: %d trades written", len(trades))
    return {"trades_synced": len(trades)}


# ── FIFO Matching ─────────────────────────────────────────────────────────────

# Closing transactions only pair with open lots of the matching *direction*.
# Buy to Close / Sell to Close are explicit.  Expiration and exercise are not
# direction-specific in the API label — a long that expires OTM is still
# "Expired" and must close a *long* lot, not only shorts (the old bug showed
# BTO legs as perpetually "open").  Assignment almost always closes a short.
_CLOSE_MATCH_DIRECTIONS: dict[str, tuple[str, ...]] = {
    "Buy to Close":           ("short",),
    "Sell to Close":          ("long",),
    "Expired":                ("short", "long"),
    "Assigned":               ("short",),
    "Exchange or Exercise":   ("long", "short"),
}


def _match_legs(option_rows):
    """Match option opens with their closes using FIFO per position key."""
    # Pre-process: suppress spurious 'Expired' events for contracts that also have an 'Assigned'
    # event. Schwab generates BOTH an Expired (net_amount=0, the Saturday after expiry) AND an
    # Assigned (net_amount≠0, the following Monday) for in-the-money options at expiry. If we
    # let the Expired event run first it consumes the open STO lot, leaving Assigned with nothing
    # to match against. The Assigned event is the authoritative close, so drop the Expired twin.
    assigned_keys: set = set()
    for row in option_rows:
        if row["action"] in ("Assigned", "Exchange or Exercise"):
            key = (row["underlying"], row["option_type"],
                   row["option_strike"], row["option_expiry"])
            # Accept even if qty=0/None — quantity field can be absent for RECEIVE_AND_DELIVER
            assigned_keys.add(key)
            log.info("Pre-scan: adding assigned key %s (qty=%s)", key, row.get("quantity"))


    filtered_rows = []
    for row in option_rows:
        if row["action"] == "Expired":
            key = (row["underlying"], row["option_type"],
                   row["option_strike"], row["option_expiry"])
            if key in assigned_keys:
                log.debug("Suppressing spurious Expired for %s %s %.4g %s "
                          "(assignment exists)", *key)
                continue
        filtered_rows.append(row)

    # Position key → list of open lots: {action, qty_remaining, price, date, activity_id, fees}
    open_lots = defaultdict(list)
    matched = []

    for row in filtered_rows:
        action = row["action"]
        underlying = row["underlying"]
        opt_type = row["option_type"]
        strike = row["option_strike"]
        expiry = row["option_expiry"]
        qty = abs(row["quantity"] or 0)
        # Assigned / Exchange or Exercise events may have qty=0 if the Schwab API omits
        # the `amount` field on the option transferItem.  Default to 1 (one contract).
        if qty == 0 and action in ("Assigned", "Exchange or Exercise"):
            qty = 1
        price = abs(row["price"] or 0)
        fees = abs(row["fees"] or 0)
        date = row["trade_date"]
        aid = row.get("activity_id")

        if qty == 0:
            continue

        key = (underlying, opt_type, strike, expiry)

        if action == "Sell to Open":
            open_lots[key].append({
                "direction": "short", "qty_remaining": qty,
                "price": price, "date": date, "activity_id": aid,
                "fees": fees, "action": action,
            })
        elif action == "Buy to Open":
            open_lots[key].append({
                "direction": "long", "qty_remaining": qty,
                "price": price, "date": date, "activity_id": aid,
                "fees": fees, "action": action,
            })
        elif action in _CLOSE_MATCH_DIRECTIONS:
            allowed_dirs = _CLOSE_MATCH_DIRECTIONS[action]
            remaining = qty
            lots = open_lots[key]
            for lot in lots:
                if lot["direction"] not in allowed_dirs or lot["qty_remaining"] <= 0:
                    continue
                fill = min(lot["qty_remaining"], remaining)
                lot["qty_remaining"] -= fill
                remaining -= fill

                matched.append({
                    "underlying": underlying,
                    "leg_type": opt_type,
                    "strike": strike,
                    "expiry": expiry,
                    "direction": lot["direction"],
                    "open_action": lot["action"],
                    "open_qty": fill,
                    "open_price": lot["price"],
                    "open_date": lot["date"],
                    "close_action": action,
                    "close_qty": fill,
                    "close_price": price,
                    "close_date": date,
                    "open_fees": lot["fees"],
                    "close_fees": fees * (fill / qty) if qty else 0,
                    "schwab_open_activity_id": lot["activity_id"],
                    "schwab_close_activity_id": aid,
                })
                if remaining <= 0:
                    break

    # Add still-open lots as open legs
    for key, lots in open_lots.items():
        underlying, opt_type, strike, expiry = key
        for lot in lots:
            if lot["qty_remaining"] > 0:
                matched.append({
                    "underlying": underlying,
                    "leg_type": opt_type,
                    "strike": strike,
                    "expiry": expiry,
                    "direction": lot["direction"],
                    "open_action": lot["action"],
                    "open_qty": lot["qty_remaining"],
                    "open_price": lot["price"],
                    "open_date": lot["date"],
                    "close_action": None,
                    "close_qty": None,
                    "close_price": None,
                    "close_date": None,
                    "open_fees": lot["fees"],
                    "close_fees": 0,
                    "schwab_open_activity_id": lot["activity_id"],
                    "schwab_close_activity_id": None,
                })

    return matched


# ── Spread Grouping ───────────────────────────────────────────────────────────

def _group_into_trades(matched_legs):
    """Group matched legs into income trades.
    Spread = STO + BTO on same date, same underlying, same expiry, same option type.
    Collar = STO + BTO on same date, same underlying, same expiry, different option type.
    Naked = lone STO with no companion BTO."""
    # Group by (underlying, open_date, expiry)
    groups = defaultdict(list)
    for leg in matched_legs:
        gkey = (leg["underlying"], leg["open_date"], leg["expiry"])
        groups[gkey].append(leg)

    trades = []
    for gkey, legs in groups.items():
        underlying, open_date, expiry = gkey
        short_legs = [l for l in legs if l["direction"] == "short"]
        long_legs = [l for l in legs if l["direction"] == "long"]

        # Exclude groups with no short legs (standalone long options)
        if not short_legs:
            continue

        used_longs = set()

        for sl in short_legs:
            # Try to find a matching long leg for a spread
            partner = None
            for i, ll in enumerate(long_legs):
                if i in used_longs:
                    continue
                if ll["leg_type"] == sl["leg_type"] and ll["open_qty"] == sl["open_qty"]:
                    # Same type = vertical spread
                    partner = ll
                    used_longs.add(i)
                    break

            if partner is None:
                for i, ll in enumerate(long_legs):
                    if i in used_longs:
                        continue
                    if ll["leg_type"] != sl["leg_type"] and ll["open_qty"] == sl["open_qty"]:
                        # Different type = collar
                        partner = ll
                        used_longs.add(i)
                        break

            trade_legs = [sl]
            if partner:
                trade_legs.append(partner)

            strategy = _classify_strategy(trade_legs)
            status = _determine_status(trade_legs)
            close_date = _get_close_date(trade_legs)
            days_held = None
            if close_date and open_date:
                try:
                    d0 = datetime.date.fromisoformat(open_date)
                    d1 = datetime.date.fromisoformat(close_date)
                    days_held = (d1 - d0).days
                except (ValueError, TypeError):
                    pass

            total_fees = sum((l.get("open_fees") or 0) + (l.get("close_fees") or 0)
                             for l in trade_legs)

            dedup = _make_dedup_key(underlying, open_date, trade_legs)

            trade = {
                "underlying": underlying,
                "strategy": strategy,
                "open_date": open_date,
                "close_date": close_date,
                "status": status,
                "days_held": days_held,
                "fees": round(total_fees, 2),
                "dedup_key": dedup,
                "_legs": [{
                    "leg_type": l["leg_type"],
                    "strike": l["strike"],
                    "expiry": l["expiry"],
                    "direction": l["direction"],
                    "open_action": l["open_action"],
                    "open_qty": l["open_qty"],
                    "open_price": l["open_price"],
                    "open_date": l["open_date"],
                    "close_action": l["close_action"],
                    "close_qty": l["close_qty"],
                    "close_price": l["close_price"],
                    "close_date": l["close_date"],
                    "schwab_open_activity_id": l.get("schwab_open_activity_id"),
                    "schwab_close_activity_id": l.get("schwab_close_activity_id"),
                } for l in trade_legs],
            }
            trades.append(trade)

    return trades


def _classify_strategy(legs):
    short_legs = [l for l in legs if l["direction"] == "short"]
    long_legs = [l for l in legs if l["direction"] == "long"]

    if len(legs) == 1 and short_legs:
        return "naked_put" if short_legs[0]["leg_type"] == "PUT" else "naked_call"

    if len(legs) == 2 and short_legs and long_legs:
        if short_legs[0]["leg_type"] == long_legs[0]["leg_type"]:
            return "put_spread" if short_legs[0]["leg_type"] == "PUT" else "call_spread"
        else:
            return "collar"

    return "other"


def _determine_status(legs):
    close_actions = [l["close_action"] for l in legs if l["close_action"]]
    if not close_actions:
        return "open"
    if any(a in ("Assigned", "Exchange or Exercise") for a in close_actions):
        return "assigned"
    if all(a == "Expired" for a in close_actions):
        return "expired"
    return "closed"


def _get_close_date(legs):
    dates = [l["close_date"] for l in legs if l["close_date"]]
    return max(dates) if dates else None


def _make_dedup_key(underlying, open_date, legs):
    parts = [underlying, open_date]
    for l in sorted(legs, key=lambda x: (x["leg_type"], x["strike"])):
        parts.append(f"{l['direction']}_{l['leg_type']}_{l['strike']}_{l['expiry']}")
    raw = "|".join(str(p) for p in parts)
    return hashlib.md5(raw.encode()).hexdigest()


# ── Assignment Price Lookup ───────────────────────────────────────────────────

def _fill_assignment_prices(client, trades):
    """For assigned trades, look up the stock closing price on assignment date."""
    lookups_needed = {}
    for trade in trades:
        if trade["status"] != "assigned":
            continue
        close_date = trade.get("close_date")
        underlying = trade["underlying"]
        if close_date and underlying:
            lookups_needed.setdefault(underlying, set()).add(close_date)

    price_cache = {}
    for ticker, dates in lookups_needed.items():
        for date_str in dates:
            cache_key = (ticker, date_str)
            if cache_key in price_cache:
                continue
            try:
                dt = datetime.date.fromisoformat(date_str)
                start = datetime.datetime(dt.year, dt.month, dt.day,
                                          tzinfo=datetime.UTC)
                end = start + datetime.timedelta(days=3)
                resp = client.get_price_history_every_day(
                    ticker, start_datetime=start, end_datetime=end)
                resp.raise_for_status()
                candles = resp.json().get("candles", [])
                if candles:
                    price_cache[cache_key] = candles[0].get("close")
                    log.info("Assignment price for %s on %s: $%.2f",
                             ticker, date_str, candles[0].get("close", 0))
            except Exception as e:
                log.warning("Failed to get price for %s on %s: %s", ticker, date_str, e)

    for trade in trades:
        if trade["status"] == "assigned" and trade.get("close_date"):
            price = price_cache.get((trade["underlying"], trade["close_date"]))
            if price is not None:
                trade["assignment_stock_price"] = price


# ── P&L Calculation ──────────────────────────────────────────────────────────

def _calculate_pnl(trades):
    """Calculate net_premium, close_cost, net_pnl, win/loss for each trade."""
    for trade in trades:
        legs = trade["_legs"]
        total_premium = 0.0
        total_close_cost = 0.0

        for leg in legs:
            qty = leg.get("open_qty") or 0
            open_price = leg.get("open_price") or 0
            close_price = leg.get("close_price") or 0
            premium_per_share = open_price * 100 * qty

            if leg["direction"] == "short":
                total_premium += premium_per_share
                if leg.get("close_action") in ("Assigned", "Exchange or Exercise"):
                    stock_price = trade.get("assignment_stock_price")
                    if stock_price is not None:
                        if leg["leg_type"] == "PUT":
                            intrinsic = max(0, leg["strike"] - stock_price)
                        else:
                            intrinsic = max(0, stock_price - leg["strike"])
                        close_cost = intrinsic * 100 * qty
                    else:
                        close_cost = 0
                    total_close_cost += close_cost
                elif leg.get("close_action") == "Expired":
                    pass  # close cost = 0, full win
                elif leg.get("close_action") in ("Buy to Close",):
                    total_close_cost += close_price * 100 * qty
                # If still open, no close cost yet
            else:
                # Long leg (part of a spread): premium is a cost
                total_premium -= premium_per_share
                if leg.get("close_action") in ("Sell to Close",):
                    total_close_cost -= close_price * 100 * qty
                elif leg.get("close_action") == "Expired":
                    pass  # long leg expired worthless = loss of premium (already subtracted)
                elif leg.get("close_action") in ("Assigned", "Exchange or Exercise"):
                    stock_price = trade.get("assignment_stock_price")
                    if stock_price is not None:
                        if leg["leg_type"] == "PUT":
                            intrinsic = max(0, leg["strike"] - stock_price)
                        else:
                            intrinsic = max(0, stock_price - leg["strike"])
                        total_close_cost -= intrinsic * 100 * qty

            leg_pnl = _calc_leg_pnl(leg, trade.get("assignment_stock_price"))
            leg["leg_pnl"] = round(leg_pnl, 2)

        fees = trade.get("fees") or 0
        net_premium = round(total_premium, 2)
        close_cost = round(total_close_cost, 2)
        net_pnl = round(net_premium - close_cost - fees, 2)
        net_pnl_pct = round(100 * net_pnl / abs(net_premium), 1) if net_premium else 0

        trade["net_premium"] = net_premium
        trade["close_cost"] = close_cost
        trade["net_pnl"] = net_pnl if trade["status"] != "open" else None
        trade["net_pnl_pct"] = net_pnl_pct if trade["status"] != "open" else None

        if trade["status"] == "open":
            trade["is_win"] = 0
            trade["is_perfect_win"] = 0
        else:
            trade["is_win"] = 1 if net_pnl > 0 else 0
            is_perfect = (
                trade["status"] in ("expired", "closed")
                and close_cost < abs(net_premium) * PERFECT_WIN_THRESHOLD
                and net_pnl > 0
            )
            trade["is_perfect_win"] = 1 if is_perfect else 0


def _calc_leg_pnl(leg, assignment_stock_price=None):
    qty = leg.get("open_qty") or 0
    open_price = leg.get("open_price") or 0
    close_price = leg.get("close_price") or 0

    if leg["direction"] == "short":
        premium = open_price * 100 * qty
        if leg.get("close_action") in ("Assigned", "Exchange or Exercise"):
            if assignment_stock_price is not None:
                if leg["leg_type"] == "PUT":
                    intrinsic = max(0, leg["strike"] - assignment_stock_price)
                else:
                    intrinsic = max(0, assignment_stock_price - leg["strike"])
                return premium - intrinsic * 100 * qty
            return premium
        elif leg.get("close_action") == "Expired":
            return premium
        elif leg.get("close_action") in ("Buy to Close",):
            return premium - close_price * 100 * qty
        return 0  # still open
    else:
        cost = open_price * 100 * qty
        if leg.get("close_action") in ("Sell to Close",):
            return close_price * 100 * qty - cost
        elif leg.get("close_action") == "Expired":
            return -cost
        elif leg.get("close_action") in ("Assigned", "Exchange or Exercise"):
            if assignment_stock_price is not None:
                if leg["leg_type"] == "PUT":
                    intrinsic = max(0, leg["strike"] - assignment_stock_price)
                else:
                    intrinsic = max(0, assignment_stock_price - leg["strike"])
                return intrinsic * 100 * qty - cost
            return -cost
        return 0
