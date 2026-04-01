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


def suggest_position_unwind(ticker, window_size=5, sell_pct=0.25,
                            premium_cents=77, min_streak=10, max_rungs=5):
    """Analyse consecutive same-direction equity trades for *ticker* and
    generate suggested ladder rungs to unwind the position.

    Returns a dict with streak info, suggested rungs, and the parameters used.
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

    # most recent trade price as a fallback reference
    last_trade_price = all_trades[0]["price"] if all_trades else None

    if not all_trades:
        return {"streak_count": 0, "rungs": [],
                "note": "No equity trades found for this ticker"}

    # --- find the most recent streak (always the consecutive run from trade[0]) -
    # The streak direction is always the most recent trade's action — never
    # dig into history to find a longer one.
    direction = all_trades[0]["action"]
    streak = []
    for t in all_trades:
        if t["action"] == direction:
            streak.append(t)
        else:
            break   # first different action ends the streak

    unwind_action = _ACCUM_UNWIND.get(direction)   # None for exits (Sell, Buy to Cover)

    # --- handle exit streaks (Sell or Buy to Cover at the top) ---------------
    if unwind_action is None:
        # Most recent trades are already exits — show streak context and no rungs
        total_shares = sum(t["quantity"] for t in streak)
        return {
            "streak_count": len(streak),
            "direction": direction,
            "unwind_action": None,
            "total_shares": round(total_shares, 2),
            "overall_avg": 0,
            "last_trade_price": last_trade_price,
            "rungs": [],
            "params": _pack_params(window_size, sell_pct, premium_cents,
                                   min_streak, max_rungs),
            "note": (f"Most recent streak is {len(streak)} consecutive "
                     f"{direction}(s) — no accumulation to unwind"),
        }

    if len(streak) < min_streak:
        total_shares = sum(t["quantity"] for t in streak)
        total_cost   = sum(t["quantity"] * t["price"] for t in streak)
        overall_avg  = round(total_cost / total_shares, 4) if total_shares else 0
        return {
            "streak_count": len(streak),
            "direction": direction,
            "unwind_action": unwind_action,
            "total_shares": round(total_shares, 2),
            "overall_avg": overall_avg,
            "last_trade_price": last_trade_price,
            "rungs": [],
            "params": _pack_params(window_size, sell_pct, premium_cents,
                                   min_streak, max_rungs),
            "note": (f"Only {len(streak)} consecutive "
                     f"{direction} trades found (minimum: {min_streak})"),
        }

    # --- aggregate streak stats ---------------------------------------------
    total_shares = sum(t["quantity"] for t in streak)
    total_cost = sum(t["quantity"] * t["price"] for t in streak)
    overall_avg = round(total_cost / total_shares, 4) if total_shares else 0

    is_long = (direction == "Buy")
    premium = premium_cents / 100.0

    # --- windowed rung generation -------------------------------------------
    rungs = []
    cursor = 0        # current index into *streak*
    consumed = 0.0    # shares already consumed from streak[cursor]
    prev_price = None

    while cursor < len(streak) and len(rungs) < max_rungs:
        # build a window of up to *window_size* trade records
        window = []
        idx = cursor
        first_remaining = streak[idx]["quantity"] - consumed
        if first_remaining > 0:
            window.append({"qty": first_remaining,
                           "price": streak[idx]["price"]})
        idx += 1
        while len(window) < window_size and idx < len(streak):
            window.append({"qty": streak[idx]["quantity"],
                           "price": streak[idx]["price"]})
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

        # snap price: find the nearest $X.{premium_cents} above/below avg
        if is_long:
            base = math.floor(win_avg)
            target = base + premium
            if target <= win_avg:
                target += 1.0
            # monotonicity: each rung must be strictly higher than the last
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

        # consume sell_qty shares starting from the most-recent end
        remaining = sell_qty
        while remaining > 0 and cursor < len(streak):
            available = streak[cursor]["quantity"] - consumed
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
        "streak_count": len(streak),
        "total_shares": round(total_shares, 2),
        "overall_avg": overall_avg,
        "last_trade_price": last_trade_price,
        "rungs": rungs,
        "params": _pack_params(window_size, sell_pct, premium_cents,
                               min_streak, max_rungs),
    }


def _pack_params(window_size, sell_pct, premium_cents, min_streak, max_rungs):
    return {
        "window_size": window_size,
        "sell_pct": sell_pct,
        "premium_cents": premium_cents,
        "min_streak": min_streak,
        "max_rungs": max_rungs,
    }
