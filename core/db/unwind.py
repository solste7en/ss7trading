"""Position-unwind ladder suggestion."""

import math

# Look up ``_connection`` on the package at call time so tests that do
# ``monkeypatch.setattr(core.db, "_connection", mock)`` redirect connections.
import core.db as _db_pkg


def _connection():
    return _db_pkg._connection()

# Actions that accumulate a position (and their unwind counterpart)
_ACCUM_UNWIND = {"Buy": "sell", "Sell Short": "buy_to_cover"}


_EXIT_TOLERANCE = 0.30   # exits up to 30 % of total volume are "noise"


def suggest_position_unwind(ticker, window_size=5, sell_pct=0.25,
                            premium_cents=77, min_streak=10, max_rungs=5):
    """Analyse recent equity trades for *ticker* and generate suggested
    ladder rungs to unwind the position.

    Uses a volume-based tolerance model: partial exits (up to 30 % of
    cumulative volume) are treated as already-filled rungs rather than
    breaking the streak.
    """
    with _connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT trade_date, action, quantity, price
            FROM transactions
            WHERE underlying = ? AND category = 'equity'
              AND action IN ('Buy', 'Sell', 'Sell Short', 'Buy to Cover')
            ORDER BY trade_date DESC, id DESC
        """, [ticker])
        all_trades = [dict(r) for r in cur.fetchall()]

    last_trade_price = all_trades[0]["price"] if all_trades else None

    if not all_trades:
        return {"streak_count": 0, "rungs": [],
                "note": "No equity trades found for this ticker"}

    first_action = all_trades[0]["action"]
    first_run = []
    for t in all_trades:
        if t["action"] == first_action:
            first_run.append(t)
        else:
            break
    first_run_vol = sum(abs(t["quantity"]) for t in first_run)

    second_action = None
    second_run_vol = 0.0
    for t in all_trades[len(first_run):]:
        if second_action is None:
            second_action = t["action"]
        if t["action"] == second_action:
            second_run_vol += abs(t["quantity"])
        else:
            break

    first_is_accum  = first_action in _ACCUM_UNWIND
    second_is_accum = second_action in _ACCUM_UNWIND if second_action else False

    combined = first_run_vol + second_run_vol
    first_run_is_noise = (combined > 0
                          and first_run_vol / combined <= _EXIT_TOLERANCE)

    if first_is_accum and second_is_accum and second_action != first_action \
            and first_run_is_noise:
        direction = second_action
    elif first_is_accum:
        direction = first_action
    elif second_is_accum:
        direction = second_action
    else:
        exit_action = first_action
        total_shares = sum(abs(t["quantity"]) for t in all_trades)
        return {
            "streak_count": len(all_trades),
            "direction": exit_action,
            "unwind_action": None,
            "total_shares": round(total_shares, 2),
            "overall_avg": 0,
            "last_trade_price": last_trade_price,
            "rungs": [],
            "exit_trades": [],
            "params": _pack_params(window_size, sell_pct, premium_cents,
                                   min_streak, max_rungs),
            "note": ("All recent trades are exits"
                     " — no accumulation to unwind"),
        }

    unwind_action = _ACCUM_UNWIND[direction]
    accum_actions = {direction}

    accum_trades = []
    exit_trades  = []
    accum_qty    = 0.0
    exit_qty     = 0.0

    for t in all_trades:
        q = abs(t["quantity"])
        if t["action"] in accum_actions:
            accum_qty += q
            accum_trades.append(t)
        else:
            if accum_qty > 0:
                total_so_far = accum_qty + exit_qty + q
                if (exit_qty + q) / total_so_far > _EXIT_TOLERANCE:
                    break
            exit_qty += q
            exit_trades.append(t)

    if not accum_trades:
        return {
            "streak_count": 0,
            "direction": direction,
            "unwind_action": unwind_action,
            "total_shares": 0,
            "overall_avg": 0,
            "last_trade_price": last_trade_price,
            "rungs": [],
            "exit_trades": _summarise_exits(exit_trades),
            "params": _pack_params(window_size, sell_pct, premium_cents,
                                   min_streak, max_rungs),
            "note": "No accumulation trades found within tolerance window",
        }

    if len(accum_trades) < min_streak:
        total_shares = sum(abs(t["quantity"]) for t in accum_trades)
        total_cost   = sum(abs(t["quantity"]) * t["price"] for t in accum_trades)
        overall_avg  = round(total_cost / total_shares, 4) if total_shares else 0
        return {
            "streak_count": len(accum_trades),
            "direction": direction,
            "unwind_action": unwind_action,
            "total_shares": round(total_shares, 2),
            "overall_avg": overall_avg,
            "last_trade_price": last_trade_price,
            "rungs": [],
            "exit_trades": _summarise_exits(exit_trades),
            "params": _pack_params(window_size, sell_pct, premium_cents,
                                   min_streak, max_rungs),
            "note": (f"Only {len(accum_trades)} {direction} trades found"
                     f" (minimum: {min_streak})"),
        }

    total_shares = sum(abs(t["quantity"]) for t in accum_trades)
    total_cost   = sum(abs(t["quantity"]) * t["price"] for t in accum_trades)
    overall_avg  = round(total_cost / total_shares, 4) if total_shares else 0

    if exit_qty > 0:
        exit_ratio = exit_qty / (accum_qty + exit_qty)
        rungs_used = max(1, round(max_rungs * exit_ratio))
        effective_max_rungs = max(1, max_rungs - rungs_used)
    else:
        effective_max_rungs = max_rungs

    is_long = (direction == "Buy")
    premium = premium_cents / 100.0

    rungs = []
    cursor = 0
    consumed = 0.0
    prev_price = None

    while cursor < len(accum_trades) and len(rungs) < effective_max_rungs:
        window = []
        idx = cursor
        first_remaining = abs(accum_trades[idx]["quantity"]) - consumed
        if first_remaining > 0:
            window.append({"qty": first_remaining,
                           "price": accum_trades[idx]["price"]})
        idx += 1
        while len(window) < window_size and idx < len(accum_trades):
            window.append({"qty": abs(accum_trades[idx]["quantity"]),
                           "price": accum_trades[idx]["price"]})
            idx += 1

        if not window:
            break

        win_shares = sum(w["qty"] for w in window)
        if win_shares <= 0:
            break
        win_cost = sum(w["qty"] * w["price"] for w in window)
        win_avg = win_cost / win_shares

        sell_qty = math.ceil(win_shares * sell_pct)
        if sell_qty <= 0:
            break

        if is_long:
            base = math.floor(win_avg)
            target = base + premium
            if target <= win_avg:
                target += 1.0
            if prev_price is not None:
                while target <= prev_price:
                    target += 1.0
        else:
            base = math.ceil(win_avg)
            target = base - premium
            if target >= win_avg:
                target -= 1.0
            if prev_price is not None:
                while target >= prev_price:
                    target -= 1.0

        target = round(target, 2)
        prev_price = target

        rungs.append({
            "qty": sell_qty,
            "price": target,
            "window_avg": round(win_avg, 4),
            "window_shares": round(win_shares, 2),
            "window_trades": len(window),
        })

        remaining = sell_qty
        while remaining > 0 and cursor < len(accum_trades):
            available = abs(accum_trades[cursor]["quantity"]) - consumed
            if available <= remaining:
                remaining -= available
                consumed = 0.0
                cursor += 1
            else:
                consumed += remaining
                remaining = 0

    return {
        "direction": direction,
        "unwind_action": unwind_action,
        "streak_count": len(accum_trades),
        "total_shares": round(total_shares, 2),
        "overall_avg": overall_avg,
        "last_trade_price": last_trade_price,
        "rungs": rungs,
        "exit_trades": _summarise_exits(exit_trades),
        "effective_max_rungs": effective_max_rungs,
        "params": _pack_params(window_size, sell_pct, premium_cents,
                               min_streak, max_rungs),
    }


def _summarise_exits(exit_trades):
    """Return a compact list of partial-exit trades for the UI."""
    if not exit_trades:
        return []
    return [{
        "date": t["trade_date"],
        "action": t["action"],
        "qty": abs(t["quantity"]),
        "price": t["price"],
    } for t in exit_trades]


def _pack_params(window_size, sell_pct, premium_cents, min_streak, max_rungs):
    return {
        "window_size": window_size,
        "sell_pct": sell_pct,
        "premium_cents": premium_cents,
        "min_streak": min_streak,
        "max_rungs": max_rungs,
    }
