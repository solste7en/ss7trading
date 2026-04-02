"""
db.py — Database access layer for ss7trading.
Provides reusable query functions for the trades.db SQLite database.
"""
import math
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "trades.db"


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_transactions(page=1, limit=25, category="", ticker="", search=""):
    """Paginated transaction history with optional filters."""
    offset = (page - 1) * limit

    where, params = [], []
    if category:
        where.append("category = ?"); params.append(category)
    if ticker:
        where.append("underlying = ?"); params.append(ticker)
    if search:
        where.append("(symbol LIKE ? OR action LIKE ? OR underlying LIKE ?)")
        params += [f"%{search}%", f"%{search}%", f"%{search}%"]

    clause = ("WHERE " + " AND ".join(where)) if where else ""

    conn = _connect()
    cur = conn.cursor()

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

    return {
        "data": rows,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": math.ceil(total / limit) if total else 0,
    }


def get_realized_gains(page=1, limit=25, ticker="", term=""):
    """Paginated realized gains with optional filters."""
    offset = (page - 1) * limit

    where, params = [], []
    if ticker:
        where.append("underlying = ?"); params.append(ticker)
    if term == "lt":
        where.append("lt_gl_amt IS NOT NULL")
    elif term == "st":
        where.append("st_gl_amt IS NOT NULL")

    clause = ("WHERE " + " AND ".join(where)) if where else ""

    conn = _connect()
    cur = conn.cursor()

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

    return {
        "data": rows,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": math.ceil(total / limit) if total else 0,
    }


def get_top_tickers(top_n=10, recent_n=5):
    """
    Return the top_n most-traded tickers with their last recent_n executed
    *equity-only* trades (options excluded for clarity on the overview page).
    Uses a window function to avoid N+1 queries.
    """
    conn = _connect()
    cur = conn.cursor()

    cur.execute("""
        SELECT underlying,
               COUNT(*) as cnt,
               SUM(CASE WHEN category = 'equity' THEN 1 ELSE 0 END) as equity_count,
               SUM(CASE WHEN category = 'option' THEN 1 ELSE 0 END) as option_count
        FROM transactions
        WHERE category IN ('equity', 'option') AND underlying IS NOT NULL
        GROUP BY underlying
        ORDER BY cnt DESC
        LIMIT ?
    """, [top_n])
    top = [
        (row["underlying"], row["cnt"], row["equity_count"], row["option_count"])
        for row in cur.fetchall()
    ]

    if not top:
        conn.close()
        return {"tickers": []}

    placeholders = ",".join("?" for _ in top)
    symbols = [t[0] for t in top]

    equity_actions = "'Buy','Sell','Sell Short','Buy to Cover'"
    cur.execute(f"""
        SELECT * FROM (
            SELECT underlying, trade_date, action, symbol, quantity, price, amount,
                   ROW_NUMBER() OVER (PARTITION BY underlying ORDER BY trade_date DESC, id DESC) as rn
            FROM transactions
            WHERE underlying IN ({placeholders})
              AND action IN ({equity_actions})
              AND category = 'equity'
        ) WHERE rn <= ?
        ORDER BY underlying, rn
    """, symbols + [recent_n])

    trades_by_ticker = {}
    for row in cur.fetchall():
        sym = row["underlying"]
        if sym not in trades_by_ticker:
            trades_by_ticker[sym] = []
        trades_by_ticker[sym].append({
            "trade_date": row["trade_date"],
            "action": row["action"],
            "symbol": row["symbol"],
            "quantity": row["quantity"],
            "price": row["price"],
            "amount": row["amount"],
        })

    conn.close()

    result = []
    for sym, cnt, eq_cnt, opt_cnt in top:
        result.append({
            "symbol": sym,
            "trade_count": cnt,
            "equity_count": eq_cnt,
            "option_count": opt_cnt,
            "recent_trades": trades_by_ticker.get(sym, []),
        })

    return {"tickers": result}


# ── Position-unwind ladder suggestion ─────────────────────────────────────────

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
    conn = _connect()
    cur = conn.cursor()
    cur.execute("""
        SELECT trade_date, action, quantity, price
        FROM transactions
        WHERE underlying = ? AND category = 'equity'
          AND action IN ('Buy', 'Sell', 'Sell Short', 'Buy to Cover')
        ORDER BY trade_date DESC, id DESC
    """, [ticker])
    all_trades = [dict(r) for r in cur.fetchall()]
    conn.close()

    last_trade_price = all_trades[0]["price"] if all_trades else None

    if not all_trades:
        return {"streak_count": 0, "rungs": [],
                "note": "No equity trades found for this ticker"}

    # --- Phase 1: determine dominant accumulation direction ------------------
    # Find the first consecutive run at the top.  Then find the next run of
    # a *different* action.  If the second run is accumulation and has much
    # more volume than the first, treat the first run as partial exits and
    # adopt the second run's direction.  This handles the common case of a
    # few recent Buys interrupting a long series of Sell Shorts (or vice
    # versa).
    first_action = all_trades[0]["action"]
    first_run = []
    for t in all_trades:
        if t["action"] == first_action:
            first_run.append(t)
        else:
            break
    first_run_vol = sum(abs(t["quantity"]) for t in first_run)

    # Find the second run (different action, consecutive from where first_run ended)
    second_action = None
    second_run_vol = 0.0
    for t in all_trades[len(first_run):]:
        if second_action is None:
            second_action = t["action"]
        if t["action"] == second_action:
            second_run_vol += abs(t["quantity"])
        else:
            break

    # Decide direction: if the first run is accumulation and the second run
    # is also accumulation (opposite), the second run's direction dominates
    # only if the first run's volume is within the exit tolerance relative
    # to total.  Otherwise the first run is genuinely the direction.
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
            "note": (f"All recent trades are exits"
                     f" — no accumulation to unwind"),
        }

    unwind_action = _ACCUM_UNWIND[direction]
    accum_actions = {direction}
    exit_actions  = {"Sell", "Buy to Cover", "Sell Short", "Buy"} - accum_actions

    # --- Phase 1b: volume-tolerance scan ------------------------------------
    # Walk newest → oldest.  Track cumulative accum vs exit volume.
    # Stop when exit share ratio exceeds _EXIT_TOLERANCE, but only enforce
    # the ratio check once we've seen at least one accumulation trade
    # (initial exits before any accum trades are always tolerated).
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

    # --- handle case where scan found only exits at the top ------------------
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

    # --- Phase 4: min_streak on accumulation trade count ---------------------
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

    # --- aggregate stats on accumulation trades ------------------------------
    total_shares = sum(abs(t["quantity"]) for t in accum_trades)
    total_cost   = sum(abs(t["quantity"]) * t["price"] for t in accum_trades)
    overall_avg  = round(total_cost / total_shares, 4) if total_shares else 0

    # --- Phase 3: adjust max_rungs proportionally for partial exits ----------
    if exit_qty > 0:
        exit_ratio = exit_qty / (accum_qty + exit_qty)
        rungs_used = max(1, round(max_rungs * exit_ratio))
        effective_max_rungs = max(1, max_rungs - rungs_used)
    else:
        effective_max_rungs = max_rungs

    is_long = (direction == "Buy")
    premium = premium_cents / 100.0

    # --- Phase 5: windowed rung generation (on accum_trades only) ------------
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
