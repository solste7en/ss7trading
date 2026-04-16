"""
recovery.py — Assignment recovery tracking via LIFO matching.

For each assigned option trade, traces subsequent equity trades that "walk back"
the forced position.  PUT assignments (forced buy) recover via Sell / Sell Short;
CALL assignments (forced sell) recover via Buy.  When a ticker has multiple
assignments, recovery trades fill the most recent unfilled assignment first.
"""
import logging

from core.db import (
    get_assigned_trades_for_ticker,
    get_income_trade_ids_filtered,
    get_recovery_equity_trades,
)

log = logging.getLogger(__name__)

PUT_RECOVERY_ACTIONS = ("Sell", "Sell Short")
CALL_RECOVERY_ACTIONS = ("Buy", "Buy to Cover")


def compute_recovery(ticker: str) -> dict:
    """Compute recovery progress for all assigned income trades of *ticker*.

    Returns ``{"ticker": ..., "assignments": [...]}``.
    Each assignment entry contains matched recovery trades, progress, and P&L.
    """
    ticker = ticker.upper()
    trades = get_assigned_trades_for_ticker(ticker)
    if not trades:
        return {"ticker": ticker, "assignments": []}

    put_assignments = []
    call_assignments = []

    for t in trades:
        # Fully-exercised spreads: short assigned + long exercised nets out on
        # the stock side, so there is nothing to recover. Skip them entirely.
        if t.get("is_fully_exercised"):
            continue
        legs = t.get("legs", [])
        short_leg = next((l for l in legs if l["direction"] == "short"), None)
        if not short_leg:
            continue

        strike = short_leg["strike"]
        option_type = short_leg["leg_type"]     # PUT or CALL
        contracts = short_leg.get("open_qty") or 1
        assigned_qty = contracts * 100

        entry = {
            "trade_id": t["id"],
            "strike": strike,
            "option_type": option_type,
            "assignment_date": t["close_date"],
            "assignment_stock_price": t.get("assignment_stock_price"),
            "assigned_qty": assigned_qty,
            "dismissed_qty": t.get("recovery_dismissed_qty") or 0,
            "recovery_trades": [],
            "recovered_qty": 0,
            "_remaining": assigned_qty,
        }
        if option_type == "PUT":
            put_assignments.append(entry)
        else:
            call_assignments.append(entry)

    result_assignments = []

    if put_assignments:
        result_assignments.extend(
            _match_recovery(ticker, put_assignments, PUT_RECOVERY_ACTIONS, "put")
        )
    if call_assignments:
        result_assignments.extend(
            _match_recovery(ticker, call_assignments, CALL_RECOVERY_ACTIONS, "call")
        )

    result_assignments.sort(key=lambda a: a["assignment_date"], reverse=True)

    return {"ticker": ticker, "assignments": result_assignments}


def _match_recovery(ticker, assignments, actions, direction):
    """LIFO-match equity trades to assignments.

    *assignments* must be sorted by assignment_date ASC (earliest first).
    We process recovery trades chronologically; for each trade we fill the
    most recent assignment (by date) that still has remaining shares and whose
    assignment_date <= trade_date.
    """
    if not assignments:
        return []

    earliest_date = min(a["assignment_date"] for a in assignments)
    eq_trades = get_recovery_equity_trades(ticker, earliest_date, actions)

    # Sort assignments by date DESC for LIFO priority
    assignments_desc = sorted(assignments, key=lambda a: a["assignment_date"], reverse=True)

    for et in eq_trades:
        qty = abs(et.get("quantity") or 0)
        if qty == 0:
            continue
        trade_date = et["trade_date"]
        price = abs(et.get("price") or 0)

        remaining_to_fill = qty
        for a in assignments_desc:
            if a["assignment_date"] > trade_date:
                continue
            if a["_remaining"] <= 0:
                continue

            fill = min(a["_remaining"], remaining_to_fill)
            strike = a["strike"]
            asgn_price = a.get("assignment_stock_price")

            if direction == "put":
                pnl_per_share = price - strike
                true_pnl_per_share = (price - asgn_price) if asgn_price is not None else pnl_per_share
            else:
                pnl_per_share = strike - price
                true_pnl_per_share = (asgn_price - price) if asgn_price is not None else pnl_per_share

            a["recovery_trades"].append({
                "date": trade_date,
                "action": et["action"],
                "qty": fill,
                "price": round(price, 4),
                "pnl_per_share": round(pnl_per_share, 4),
                "pnl": round(pnl_per_share * fill, 2),
                "true_pnl_per_share": round(true_pnl_per_share, 4),
                "true_pnl": round(true_pnl_per_share * fill, 2),
            })
            a["_remaining"] -= fill
            a["recovered_qty"] += fill
            remaining_to_fill -= fill

            if remaining_to_fill <= 0:
                break

    # Finalize output
    for a in assignments:
        effective_target = a["assigned_qty"] - a["dismissed_qty"]
        a["remaining_qty"] = max(0, effective_target - a["recovered_qty"])
        a["recovery_pnl"] = round(sum(rt["pnl"] for rt in a["recovery_trades"]), 2)
        a["true_recovery_pnl"] = round(sum(rt["true_pnl"] for rt in a["recovery_trades"]), 2)
        a["is_complete"] = a["recovered_qty"] >= effective_target
        del a["_remaining"]

    return assignments


def sum_recovery_pnl_filtered(
    ticker="", status="", strategy="", outcome="", date_from="", date_to=""
):
    """Sum recovery P&L (vs strike) and true recovery P&L (vs assignment price)
    for assigned income trades matching the given filters.

    Returns ``{"recovery_pnl": float, "true_recovery_pnl": float}``.
    """
    rows = get_income_trade_ids_filtered(ticker, status, strategy, outcome, date_from, date_to)
    if not rows:
        return {"recovery_pnl": 0.0, "true_recovery_pnl": 0.0}
    by_under = {}
    for r in rows:
        if r["underlying"]:
            by_under.setdefault(r["underlying"].upper(), []).append(r["id"])
    total_rec = 0.0
    total_true = 0.0
    for und, tids in by_under.items():
        data = compute_recovery(und)
        tid_set = set(tids)
        for a in data.get("assignments", []):
            if a["trade_id"] in tid_set:
                total_rec += a.get("recovery_pnl") or 0
                total_true += a.get("true_recovery_pnl") or 0
    return {"recovery_pnl": round(total_rec, 2), "true_recovery_pnl": round(total_true, 2)}


def attach_recovery_summaries(trades: list) -> None:
    """Mutate trade dicts in place with recovery_recovered, recovery_target, recovery_pnl for assigned rows."""
    assigned = [
        t for t in trades
        if t.get("status") == "assigned" and not t.get("is_fully_exercised")
    ]
    if not assigned:
        for t in trades:
            t.setdefault("recovery_recovered", None)
            t.setdefault("recovery_target", None)
            t.setdefault("recovery_pnl", None)
            t.setdefault("true_recovery_pnl", None)
        return
    by_under = {}
    for t in assigned:
        u = (t.get("underlying") or "").upper()
        if u:
            by_under.setdefault(u, []).append(t["id"])
    cache = {}
    for und in by_under:
        cache[und] = compute_recovery(und)
    tid_to_a = {}
    for und, tids in by_under.items():
        for a in cache[und].get("assignments", []):
            if a["trade_id"] in tids:
                tid_to_a[a["trade_id"]] = a
    for t in trades:
        if t.get("status") != "assigned" or t.get("is_fully_exercised"):
            t["recovery_recovered"] = None
            t["recovery_target"] = None
            t["recovery_pnl"] = None
            t["true_recovery_pnl"] = None
            continue
        a = tid_to_a.get(t["id"])
        if not a:
            t["recovery_recovered"] = None
            t["recovery_target"] = None
            t["recovery_pnl"] = None
            t["true_recovery_pnl"] = None
            continue
        eff = int(a["assigned_qty"]) - int(a.get("dismissed_qty") or 0)
        rec = int(round(a["recovered_qty"]))
        t["recovery_recovered"] = rec
        t["recovery_target"] = eff
        t["recovery_pnl"] = a["recovery_pnl"]
        t["true_recovery_pnl"] = a["true_recovery_pnl"]
