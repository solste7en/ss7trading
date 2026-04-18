"""
db.py — Database access layer for ss7trading.
Provides reusable query functions for the trades.db SQLite database.
"""
import math
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta

from core.config import DB_PATH


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _connection():
    """Context manager that guarantees the connection is closed even on exception."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


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

    with _connection() as conn:
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

    with _connection() as conn:
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

        last_imported_at = None
        try:
            cur.execute("SELECT MAX(imported_at) FROM realized_gains")
            row = cur.fetchone()
            if row and row[0]:
                last_imported_at = row[0]
        except sqlite3.OperationalError:
            pass

    return {
        "data": rows,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": math.ceil(total / limit) if total else 0,
        "last_imported_at": last_imported_at,
    }


def get_top_tickers(top_n=10, recent_n=5):
    """
    Return the top_n most-traded tickers with their last recent_n executed
    *equity-only* trades (options excluded for clarity on the overview page).
    Uses a window function to avoid N+1 queries.
    """
    with _connection() as conn:
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
            "note": ("All recent trades are exits"
                     " — no accumulation to unwind"),
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


# ── Positions tab: custom lists & assignments ───────────────────────────────

# Stable ids for default lists (seeded once).
POSITION_LIST_ACTIVE_ID = 1
POSITION_LIST_SHORT_ID = 2
POSITION_LIST_STALE_ID = 3
POSITION_LIST_OTHER_ID = 4
POSITION_TOP_ACTIVE_K = 10

_EQUITY_TRADE_ACTIONS_SQL = "'Buy','Sell','Sell Short','Buy to Cover'"


def _ensure_position_list_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS position_lists (
            id           INTEGER PRIMARY KEY,
            name         TEXT    NOT NULL,
            sort_order   INTEGER NOT NULL,
            is_system    INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS position_symbol_list (
            symbol       TEXT    PRIMARY KEY,
            list_id      INTEGER NOT NULL REFERENCES position_lists(id)
        );
    """)
    conn.commit()
    _ensure_position_symbol_sort_index_column(conn)


def _ensure_position_symbol_sort_index_column(conn):
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(position_symbol_list)")
    cols = [r[1] for r in cur.fetchall()]
    if "sort_index" not in cols:
        cur.execute(
            "ALTER TABLE position_symbol_list ADD COLUMN sort_index INTEGER NOT NULL DEFAULT 0"
        )
        conn.commit()


def _next_symbol_sort_index(conn, list_id: int) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COALESCE(MAX(sort_index), 0) + 1
        FROM position_symbol_list
        WHERE list_id = ?
        """,
        (int(list_id),),
    )
    return int(cur.fetchone()[0])


def _seed_default_position_lists(conn):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM position_lists")
    if cur.fetchone()[0]:
        return
    defaults = [
        (POSITION_LIST_ACTIVE_ID, "Trade a lot", 1, 1),
        (POSITION_LIST_SHORT_ID, "Shorting", 2, 1),
        (POSITION_LIST_STALE_ID, "Old / dead", 3, 1),
        (POSITION_LIST_OTHER_ID, "Other", 4, 1),
    ]
    cur.executemany(
        "INSERT INTO position_lists (id, name, sort_order, is_system) VALUES (?,?,?,?)",
        defaults,
    )
    conn.commit()


def get_position_lists():
    with _connection() as conn:
        _ensure_position_list_tables(conn)
        _seed_default_position_lists(conn)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, name, sort_order, is_system
            FROM position_lists
            ORDER BY sort_order ASC, id ASC
            """
        )
        return [dict(r) for r in cur.fetchall()]


def create_position_list(name: str):
    name = (name or "").strip()
    if not name:
        raise ValueError("List name is required")
    with _connection() as conn:
        _ensure_position_list_tables(conn)
        _seed_default_position_lists(conn)
        cur = conn.cursor()
        cur.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM position_lists")
        sort_order = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO position_lists (name, sort_order, is_system) VALUES (?, ?, 0)",
            (name, sort_order),
        )
        conn.commit()
        new_id = cur.lastrowid
    return {"id": new_id, "name": name, "sort_order": sort_order, "is_system": 0}


def rename_position_list(list_id: int, name: str):
    name = (name or "").strip()
    if not name:
        raise ValueError("List name is required")
    with _connection() as conn:
        _ensure_position_list_tables(conn)
        cur = conn.cursor()
        cur.execute("UPDATE position_lists SET name = ? WHERE id = ?", (name, list_id))
        if cur.rowcount == 0:
            raise LookupError("List not found")
        conn.commit()


def delete_position_list(list_id: int):
    """Delete a user list; reassign symbols to Other. System lists cannot be removed."""
    with _connection() as conn:
        _ensure_position_list_tables(conn)
        cur = conn.cursor()
        cur.execute(
            "SELECT is_system FROM position_lists WHERE id = ?",
            (list_id,),
        )
        row = cur.fetchone()
        if not row:
            raise LookupError("List not found")
        if row["is_system"]:
            raise ValueError("Cannot delete a built-in list")
        cur.execute(
            "UPDATE position_symbol_list SET list_id = ? WHERE list_id = ?",
            (POSITION_LIST_OTHER_ID, list_id),
        )
        cur.execute("DELETE FROM position_lists WHERE id = ?", (list_id,))
        conn.commit()


def get_all_position_assignments() -> dict:
    with _connection() as conn:
        _ensure_position_list_tables(conn)
        _seed_default_position_lists(conn)
        cur = conn.cursor()
        cur.execute("SELECT symbol, list_id FROM position_symbol_list")
        return {r["symbol"]: r["list_id"] for r in cur.fetchall()}


def get_symbol_sort_map_for_symbols(symbols: list) -> dict:
    """symbol -> sort_index for rows in position_symbol_list."""
    if not symbols:
        return {}
    symbols = sorted({(s or "").strip().upper() for s in symbols if s and str(s).strip()})
    if not symbols:
        return {}
    placeholders = ",".join("?" * len(symbols))
    with _connection() as conn:
        _ensure_position_list_tables(conn)
        cur = conn.cursor()
        cur.execute(
            f"SELECT symbol, sort_index FROM position_symbol_list WHERE symbol IN ({placeholders})",
            symbols,
        )
        return {r["symbol"]: int(r["sort_index"]) for r in cur.fetchall()}


def upsert_position_assignments(assignments: dict):
    """assignments: symbol -> list_id (int). New rows get next sort_index; list changes re-append."""
    if not assignments:
        return
    with _connection() as conn:
        _ensure_position_list_tables(conn)
        _seed_default_position_lists(conn)
        cur = conn.cursor()
        for sym, lid in assignments.items():
            s = (sym or "").strip().upper()
            if not s:
                continue
            lid = int(lid)
            cur.execute(
                "SELECT list_id FROM position_symbol_list WHERE symbol = ?",
                (s,),
            )
            row = cur.fetchone()
            if row is None:
                si = _next_symbol_sort_index(conn, lid)
                cur.execute(
                    """
                    INSERT INTO position_symbol_list (symbol, list_id, sort_index)
                    VALUES (?, ?, ?)
                    """,
                    (s, lid, si),
                )
            elif int(row["list_id"]) != lid:
                si = _next_symbol_sort_index(conn, lid)
                cur.execute(
                    """
                    UPDATE position_symbol_list
                    SET list_id = ?, sort_index = ?
                    WHERE symbol = ?
                    """,
                    (lid, si, s),
                )
        conn.commit()


def reorder_position_lists(ordered_ids: list) -> None:
    """Set sort_order from ordered_ids (must be a permutation of all list ids)."""
    ordered_ids = [int(x) for x in ordered_ids]
    with _connection() as conn:
        _ensure_position_list_tables(conn)
        _seed_default_position_lists(conn)
        cur = conn.cursor()
        cur.execute("SELECT id FROM position_lists")
        all_ids = {int(r["id"]) for r in cur.fetchall()}
    if len(ordered_ids) != len(all_ids) or set(ordered_ids) != all_ids:
        raise ValueError("order must list every position list id exactly once")
    with _connection() as conn:
        cur = conn.cursor()
        for i, lid in enumerate(ordered_ids):
            cur.execute(
                "UPDATE position_lists SET sort_order = ? WHERE id = ?",
                (i + 1, lid),
            )
        conn.commit()


def reorder_symbols_within_list(list_id: int, ordered_symbols: list) -> dict:
    """
    Persist manual order for symbols on *list_id*.
    Returns { symbol: sort_index } for symbols in that list after update.
    """
    list_id = int(list_id)
    ordered_symbols = [(s or "").strip().upper() for s in ordered_symbols if s and str(s).strip()]
    with _connection() as conn:
        _ensure_position_list_tables(conn)
        _seed_default_position_lists(conn)
        cur = conn.cursor()
        cur.execute(
            "SELECT symbol FROM position_symbol_list WHERE list_id = ?",
            (list_id,),
        )
        db_syms = {r["symbol"] for r in cur.fetchall()}
    want = list(dict.fromkeys(ordered_symbols))  # unique, preserve order
    if set(want) != db_syms:
        raise ValueError("ordered symbols must match exactly the symbols on this list")
    out = {}
    with _connection() as conn:
        cur = conn.cursor()
        for i, sym in enumerate(want):
            si = (i + 1) * 10
            cur.execute(
                """
                UPDATE position_symbol_list
                SET sort_index = ?
                WHERE symbol = ? AND list_id = ?
                """,
                (si, sym, list_id),
            )
            out[sym] = si
        conn.commit()
    return out


def get_equity_trade_counts_365d(symbols: list) -> dict:
    if not symbols:
        return {}
    symbols = [s.strip().upper() for s in symbols if s and str(s).strip()]
    if not symbols:
        return {}
    placeholders = ",".join("?" * len(symbols))
    sql = f"""
        SELECT underlying, COUNT(*) AS cnt
        FROM transactions
        WHERE category = 'equity'
          AND action IN ({_EQUITY_TRADE_ACTIONS_SQL})
          AND trade_date >= date('now', '-365 days')
          AND underlying IN ({placeholders})
        GROUP BY underlying
    """
    with _connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, symbols)
        return {r["underlying"]: r["cnt"] for r in cur.fetchall()}


def get_equity_share_volume_365d_batch(symbols: list) -> dict:
    """
    Per underlying: sum of ABS(quantity) on equity transactions in the last 365 days
    (share volume). Symbols with no rows are omitted; callers treat missing as 0.
    """
    if not symbols:
        return {}
    symbols = [s.strip().upper() for s in symbols if s and str(s).strip()]
    if not symbols:
        return {}
    placeholders = ",".join("?" * len(symbols))
    sql = f"""
        SELECT underlying, SUM(ABS(quantity)) AS vol
        FROM transactions
        WHERE category = 'equity'
          AND action IN ({_EQUITY_TRADE_ACTIONS_SQL})
          AND trade_date >= date('now', '-365 days')
          AND underlying IN ({placeholders})
        GROUP BY underlying
    """
    with _connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, symbols)
        return {r["underlying"]: float(r["vol"] or 0) for r in cur.fetchall()}


def _compute_top_active_symbols(non_short_symbols: list, counts: dict, k: int) -> set:
    ranked = [(s, counts.get(s, 0)) for s in non_short_symbols if counts.get(s, 0) > 0]
    ranked.sort(key=lambda x: -x[1])
    return {s for s, _ in ranked[:k]}


def resolve_position_assignments(underlyings: list, short_equity_symbols: list) -> dict:
    """
    Return symbol -> list_id for every underlying in *underlyings*.
    Insert inferred rows for symbols not yet in position_symbol_list.
    """
    underlyings = sorted({(u or "").strip().upper() for u in underlyings if u and str(u).strip()})
    shorts = {(s or "").strip().upper() for s in (short_equity_symbols or []) if s}

    with _connection() as conn:
        _ensure_position_list_tables(conn)
        _seed_default_position_lists(conn)
        cur = conn.cursor()
        cur.execute("SELECT symbol, list_id FROM position_symbol_list")
        existing = {r["symbol"]: r["list_id"] for r in cur.fetchall()}

    counts = get_equity_trade_counts_365d(underlyings)
    non_short = [u for u in underlyings if u not in shorts]
    top_active = _compute_top_active_symbols(non_short, counts, POSITION_TOP_ACTIVE_K)

    to_insert = {}
    for u in underlyings:
        if u in existing:
            continue
        if u in shorts:
            lid = POSITION_LIST_SHORT_ID
        elif u in top_active:
            lid = POSITION_LIST_ACTIVE_ID
        elif counts.get(u, 0) < 3:
            lid = POSITION_LIST_STALE_ID
        else:
            lid = POSITION_LIST_OTHER_ID
        to_insert[u] = lid

    if to_insert:
        upsert_position_assignments(to_insert)
        existing.update(to_insert)

    missing = [u for u in underlyings if u not in existing]
    if missing:
        fix = {u: POSITION_LIST_OTHER_ID for u in missing}
        upsert_position_assignments(fix)
        existing.update(fix)

    assignments = {u: existing[u] for u in underlyings}
    symbol_sort = get_symbol_sort_map_for_symbols(underlyings)
    volume_365d = get_equity_share_volume_365d_batch(underlyings)
    return {
        "assignments": assignments,
        "symbol_sort": symbol_sort,
        "volume_365d": volume_365d,
    }


def _net_qty_sign(action: str) -> int:
    """Return +1 for buy-side actions, -1 for sell-side actions."""
    if action in ("Buy", "Buy to Cover"):
        return 1
    if action in ("Sell", "Sell Short"):
        return -1
    return 1


def get_recent_equity_trade_metrics_batch(
    symbols: list,
    days: int = 365,
    last_n: int = 10,
    min_count: int = 10,
) -> dict:
    """
    Per symbol (if at least min_count fills in window):
      n, avg_price (share-weighted unsigned mean), net_shares (action-derived).
    """
    if not symbols:
        return {}
    symbols = sorted({(s or "").strip().upper() for s in symbols if s and str(s).strip()})
    if not symbols:
        return {}
    placeholders = ",".join("?" * len(symbols))
    mod = f"-{int(days)} days"
    sql = f"""
        WITH ranked AS (
            SELECT underlying, action, price, quantity,
                ROW_NUMBER() OVER (
                    PARTITION BY underlying ORDER BY trade_date DESC, id DESC
                ) AS rn
            FROM transactions
            WHERE category = 'equity'
              AND action IN ({_EQUITY_TRADE_ACTIONS_SQL})
              AND trade_date >= date('now', ?)
              AND underlying IN ({placeholders})
        )
        SELECT underlying, action, price, quantity
        FROM ranked
        WHERE rn <= ?
    """
    params = [mod] + symbols + [last_n]

    by_sym = defaultdict(list)
    with _connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        for r in cur.fetchall():
            by_sym[r["underlying"]].append(dict(r))

    out = {}
    for s in symbols:
        rows = by_sym.get(s, [])
        if len(rows) < min_count:
            out[s] = None
        else:
            total_shares = 0.0
            weighted_sum = 0.0
            net_shares = 0
            for x in rows:
                q = abs(float(x["quantity"]))
                p = float(x["price"])
                weighted_sum += q * p
                total_shares += q
                net_shares += _net_qty_sign(x["action"]) * int(q)
            out[s] = {
                "n": len(rows),
                "avg_price": weighted_sum / total_shares if total_shares else 0.0,
                "net_shares": net_shares,
            }
    return out


# ── Watchlists ──────────────────────────────────────────────────────────────

def _ensure_watchlist_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS watchlists (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT    NOT NULL UNIQUE,
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS watchlist_symbols (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            watchlist_id INTEGER NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
            symbol       TEXT    NOT NULL,
            added_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(watchlist_id, symbol)
        );
    """)
    conn.commit()


def get_watchlists():
    with _connection() as conn:
        _ensure_watchlist_tables(conn)
        cur = conn.cursor()
        cur.execute("""
            SELECT w.id, w.name, COUNT(ws.id) AS symbol_count
            FROM watchlists w
            LEFT JOIN watchlist_symbols ws ON ws.watchlist_id = w.id
            GROUP BY w.id
            ORDER BY w.name
        """)
        return [dict(r) for r in cur.fetchall()]


def create_watchlist(name):
    with _connection() as conn:
        _ensure_watchlist_tables(conn)
        cur = conn.cursor()
        cur.execute("INSERT INTO watchlists (name) VALUES (?)", (name,))
        conn.commit()
        return {"id": cur.lastrowid, "name": name, "symbol_count": 0}


def delete_watchlist(list_id):
    with _connection() as conn:
        _ensure_watchlist_tables(conn)
        conn.execute("DELETE FROM watchlists WHERE id = ?", (list_id,))
        conn.commit()


def get_watchlist_symbols(list_id):
    with _connection() as conn:
        _ensure_watchlist_tables(conn)
        cur = conn.cursor()
        cur.execute(
            "SELECT symbol FROM watchlist_symbols WHERE watchlist_id = ? ORDER BY symbol",
            (list_id,),
        )
        return [r["symbol"] for r in cur.fetchall()]


def add_watchlist_symbol(list_id, symbol):
    with _connection() as conn:
        _ensure_watchlist_tables(conn)
        conn.execute(
            "INSERT OR IGNORE INTO watchlist_symbols (watchlist_id, symbol) VALUES (?, ?)",
            (list_id, symbol.upper()),
        )
        conn.commit()


def remove_watchlist_symbol(list_id, symbol):
    with _connection() as conn:
        _ensure_watchlist_tables(conn)
        conn.execute(
            "DELETE FROM watchlist_symbols WHERE watchlist_id = ? AND symbol = ?",
            (list_id, symbol.upper()),
        )
        conn.commit()


# ── Income Performance Tracking ──────────────────────────────────────────────

def _ensure_income_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS income_trades (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            underlying            TEXT    NOT NULL,
            strategy              TEXT    NOT NULL,
            open_date             TEXT    NOT NULL,
            close_date            TEXT,
            status                TEXT    NOT NULL DEFAULT 'open',
            days_held             INTEGER,
            net_premium           REAL,
            close_cost            REAL    DEFAULT 0,
            fees                  REAL    DEFAULT 0,
            net_pnl               REAL,
            net_pnl_pct           REAL,
            is_win                INTEGER DEFAULT 0,
            is_perfect_win        INTEGER DEFAULT 0,
            assignment_stock_price REAL,
            is_early_assignment   INTEGER DEFAULT 0,
            is_fully_exercised    INTEGER DEFAULT 0,
            recovery_dismissed_qty INTEGER DEFAULT 0,
            dedup_key             TEXT    UNIQUE,
            synced_at             TEXT    DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_it_underlying ON income_trades(underlying);
        CREATE INDEX IF NOT EXISTS idx_it_status     ON income_trades(status);

        CREATE TABLE IF NOT EXISTS income_trade_legs (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id                INTEGER NOT NULL REFERENCES income_trades(id) ON DELETE CASCADE,
            leg_type                TEXT    NOT NULL,
            strike                  REAL    NOT NULL,
            expiry                  TEXT    NOT NULL,
            direction               TEXT    NOT NULL,
            open_action             TEXT,
            open_qty                INTEGER,
            open_price              REAL,
            open_date               TEXT,
            close_action            TEXT,
            close_qty               INTEGER,
            close_price             REAL,
            close_date              TEXT,
            leg_pnl                 REAL,
            schwab_open_activity_id INTEGER,
            schwab_close_activity_id INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_itl_trade ON income_trade_legs(trade_id);

        CREATE TABLE IF NOT EXISTS income_sync_meta (
            id          INTEGER PRIMARY KEY CHECK (id = 1),
            last_synced TEXT
        );
    """)
    # Migrate: add recovery_dismissed_qty if missing (tables created before v0.2.1)
    try:
        conn.execute("SELECT recovery_dismissed_qty FROM income_trades LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE income_trades ADD COLUMN recovery_dismissed_qty INTEGER DEFAULT 0")
    # Migrate: add is_early_assignment if missing
    try:
        conn.execute("SELECT is_early_assignment FROM income_trades LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE income_trades ADD COLUMN is_early_assignment INTEGER DEFAULT 0")
    # Migrate: add is_fully_exercised if missing (spread where short was assigned
    # and long was exercised same day → net-zero stock, no recovery needed).
    try:
        conn.execute("SELECT is_fully_exercised FROM income_trades LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE income_trades ADD COLUMN is_fully_exercised INTEGER DEFAULT 0")
    conn.commit()


def clear_income_trades():
    with _connection() as conn:
        _ensure_income_tables(conn)
        conn.executescript("DELETE FROM income_trade_legs; DELETE FROM income_trades;")
        conn.commit()


def upsert_income_trade(trade, legs):
    """Insert or replace an income trade with its legs.
    `trade` is a dict, `legs` is a list of dicts."""
    with _connection() as conn:
        _ensure_income_tables(conn)
        cur = conn.cursor()

        cur.execute("SELECT id FROM income_trades WHERE dedup_key = ?",
                    (trade["dedup_key"],))
        existing = cur.fetchone()
        if existing:
            trade_id = existing[0]
            cur.execute("DELETE FROM income_trade_legs WHERE trade_id = ?", (trade_id,))
            cur.execute("""
                UPDATE income_trades SET
                    underlying=?, strategy=?, open_date=?, close_date=?,
                    status=?, days_held=?, net_premium=?, close_cost=?, fees=?,
                    net_pnl=?, net_pnl_pct=?, is_win=?, is_perfect_win=?,
                    assignment_stock_price=?, is_early_assignment=?,
                    is_fully_exercised=?,
                    synced_at=datetime('now')
                WHERE id=?
            """, (trade["underlying"], trade["strategy"], trade["open_date"],
                  trade.get("close_date"), trade["status"], trade.get("days_held"),
                  trade.get("net_premium"), trade.get("close_cost", 0),
                  trade.get("fees", 0), trade.get("net_pnl"),
                  trade.get("net_pnl_pct"), trade.get("is_win", 0),
                  trade.get("is_perfect_win", 0),
                  trade.get("assignment_stock_price"),
                  trade.get("is_early_assignment", 0),
                  trade.get("is_fully_exercised", 0), trade_id))
        else:
            cur.execute("""
                INSERT INTO income_trades
                    (underlying, strategy, open_date, close_date, status, days_held,
                     net_premium, close_cost, fees, net_pnl, net_pnl_pct,
                     is_win, is_perfect_win, assignment_stock_price,
                     is_early_assignment, is_fully_exercised, dedup_key)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (trade["underlying"], trade["strategy"], trade["open_date"],
                  trade.get("close_date"), trade["status"], trade.get("days_held"),
                  trade.get("net_premium"), trade.get("close_cost", 0),
                  trade.get("fees", 0), trade.get("net_pnl"),
                  trade.get("net_pnl_pct"), trade.get("is_win", 0),
                  trade.get("is_perfect_win", 0),
                  trade.get("assignment_stock_price"),
                  trade.get("is_early_assignment", 0),
                  trade.get("is_fully_exercised", 0), trade["dedup_key"]))
            trade_id = cur.lastrowid

        for leg in legs:
            cur.execute("""
                INSERT INTO income_trade_legs
                    (trade_id, leg_type, strike, expiry, direction,
                     open_action, open_qty, open_price, open_date,
                     close_action, close_qty, close_price, close_date,
                     leg_pnl, schwab_open_activity_id, schwab_close_activity_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (trade_id, leg["leg_type"], leg["strike"], leg["expiry"],
                  leg["direction"], leg.get("open_action"), leg.get("open_qty"),
                  leg.get("open_price"), leg.get("open_date"),
                  leg.get("close_action"), leg.get("close_qty"),
                  leg.get("close_price"), leg.get("close_date"),
                  leg.get("leg_pnl"),
                  leg.get("schwab_open_activity_id"),
                  leg.get("schwab_close_activity_id")))

        conn.commit()
        return trade_id


def set_income_sync_time():
    with _connection() as conn:
        _ensure_income_tables(conn)
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute("""
            INSERT INTO income_sync_meta (id, last_synced) VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET last_synced = excluded.last_synced
        """, (now,))
        conn.commit()


def get_income_sync_time():
    with _connection() as conn:
        _ensure_income_tables(conn)
        cur = conn.cursor()
        cur.execute("SELECT last_synced FROM income_sync_meta WHERE id=1")
        row = cur.fetchone()
        return row[0] if row else None


# ── Trade History Sync Meta ───────────────────────────────────────────────────

def _ensure_trades_sync_meta(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades_sync_meta (
            id          INTEGER PRIMARY KEY CHECK (id = 1),
            last_synced TEXT
        )
    """)
    conn.commit()


def get_trade_sync_time() -> str | None:
    """Return the ISO timestamp of the last trades sync, or None if never synced."""
    with _connection() as conn:
        _ensure_trades_sync_meta(conn)
        cur = conn.cursor()
        cur.execute("SELECT last_synced FROM trades_sync_meta WHERE id=1")
        row = cur.fetchone()
        return row[0] if row else None


def set_trade_sync_time() -> None:
    """Record the current UTC time as the last successful trade sync timestamp."""
    with _connection() as conn:
        _ensure_trades_sync_meta(conn)
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute("""
            INSERT INTO trades_sync_meta (id, last_synced) VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET last_synced = excluded.last_synced
        """, (now,))
        conn.commit()


def get_most_traded_ticker() -> str | None:
    """Return the underlying symbol with the most total transactions in the DB."""
    with _connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT underlying, COUNT(*) AS cnt
            FROM transactions
            WHERE underlying IS NOT NULL AND underlying != ''
            GROUP BY underlying
            ORDER BY cnt DESC
            LIMIT 1
        """)
        row = cur.fetchone()
        return row["underlying"] if row else None


def _income_trades_where(
    ticker="",
    status="",
    strategy="",
    outcome="",
    table_alias="t",
    date_from="",
    date_to="",
):
    """Build WHERE fragments for income_trades filters. Returns (list of SQL fragments, params)."""
    p = f"{table_alias}." if table_alias else ""
    where, params = [], []
    if ticker:
        where.append(f"{p}underlying = ?"); params.append(ticker.upper())
    if status:
        where.append(f"{p}status = ?"); params.append(status)
    if strategy:
        where.append(f"{p}strategy LIKE ?"); params.append(f"%{strategy}%")
    if outcome == "win":
        where.append(f"{p}is_win = 1")
    elif outcome == "perfect":
        where.append(f"{p}is_perfect_win = 1")
    elif outcome == "assigned":
        where.append(f"{p}status = 'assigned'")
    elif outcome == "open":
        where.append(f"{p}status = 'open'")
    elif outcome == "closed":
        # Must match get_income_stats closed_trades: SUM(status != 'open'), not status='closed' only.
        where.append(f"{p}status != 'open'")
    df = (date_from or "").strip()
    dt_ = (date_to or "").strip()
    if df or dt_:
        eff = f"COALESCE(NULLIF(TRIM({p}close_date), ''), {p}open_date)"
        where.append(f"{eff} >= ? AND {eff} <= ?")
        params.append(df or "1970-01-01")
        params.append(dt_ or "9999-12-31")
    return where, params


def get_income_trade_ids_filtered(
    ticker="", status="", strategy="", outcome="", date_from="", date_to=""
):
    """Return list of {id, underlying} dicts for income_trades matching the given filters."""
    wfrag, params = _income_trades_where(ticker, status, strategy, outcome, "", date_from, date_to)
    clause = ("WHERE " + " AND ".join(wfrag)) if wfrag else ""
    with _connection() as conn:
        _ensure_income_tables(conn)
        cur = conn.cursor()
        cur.execute(f"SELECT id, underlying FROM income_trades {clause}", params)
        return [dict(r) for r in cur.fetchall()]


def _income_attach_legs(cur, trades):
    if not trades:
        return
    trade_ids = [t["id"] for t in trades]
    ph = ",".join("?" for _ in trade_ids)
    cur.execute(f"""
        SELECT * FROM income_trade_legs WHERE trade_id IN ({ph})
        ORDER BY trade_id, id
    """, trade_ids)
    legs_by_trade = {}
    for leg in cur.fetchall():
        leg_dict = dict(leg)
        tid = leg_dict["trade_id"]
        legs_by_trade.setdefault(tid, []).append(leg_dict)
    for t in trades:
        t["legs"] = legs_by_trade.get(t["id"], [])


def _income_efficiency_score(trade: dict) -> float | None:
    """Same formula as static/js/income.js _incomeEfficiencyScore.

    score = net_pnl / sqrt(days_held + 1) * 100 / strike

    sqrt normalises time sublinearly (Sharpe-like) so short-duration
    trades aren't disproportionately inflated vs longer ones.
    """
    import math
    if trade.get("status") == "open":
        return None
    net = trade.get("net_pnl")
    if net is None:
        return None
    legs = trade.get("legs") or []
    strike = None
    for leg in legs:
        if leg.get("direction") == "short":
            k = leg.get("strike")
            if k is not None:
                kf = float(k)
                if kf > 0:
                    strike = kf
                    break
    if strike is None and legs:
        k = legs[0].get("strike")
        if k is not None:
            kf = float(k)
            if kf > 0:
                strike = kf
    if strike is None:
        return None
    d = trade.get("days_held")
    d = int(d) if d is not None and d >= 0 else 0
    return (float(net) / math.sqrt(d + 1)) * (100.0 / strike)


def get_income_trades(
    page=1,
    limit=25,
    ticker="",
    status="",
    strategy="",
    outcome="",
    sort_by="open_date",
    sort_dir="desc",
    date_from="",
    date_to="",
):
    offset = (page - 1) * limit
    wfrag, params = _income_trades_where(ticker, status, strategy, outcome, "t", date_from, date_to)
    clause = ("WHERE " + " AND ".join(wfrag)) if wfrag else ""
    sb = (sort_by or "open_date").lower()
    rev = (sort_dir or "desc").lower() != "asc"

    sort_map = {
        "open_date": "t.open_date",
        "close_date": "t.close_date",
        "underlying": "t.underlying",
        "strategy": "t.strategy",
        "net_pnl": "t.net_pnl",
        "net_pnl_pct": "t.net_pnl_pct",
        "days_held": "t.days_held",
        "status": "t.status",
        "net_premium": "t.net_premium",
    }

    with _connection() as conn:
        _ensure_income_tables(conn)
        cur = conn.cursor()

        cur.execute(f"SELECT COUNT(*) FROM income_trades t {clause}", params)
        total = cur.fetchone()[0]

        if sb in ("recovery", "recovery_pnl"):
            from services.recovery import attach_recovery_summaries
            cur.execute(f"SELECT t.* FROM income_trades t {clause} ORDER BY t.id DESC", params)
            trades = [dict(r) for r in cur.fetchall()]
            _income_attach_legs(cur, trades)
            attach_recovery_summaries(trades)

            def _rec_frac(t):
                tgt = t.get("recovery_target")
                if not tgt:
                    return -1.0
                return (t.get("recovery_recovered") or 0) / max(tgt, 1)

            if sb == "recovery":
                trades.sort(key=_rec_frac, reverse=rev)
            else:
                def _pnl_key(t):
                    v = t.get("true_recovery_pnl")
                    if v is None:
                        return float("-inf") if rev else float("inf")
                    return v
                trades.sort(key=_pnl_key, reverse=rev)
            trades = trades[offset:offset + limit]
        elif sb == "score":
            cur.execute(f"SELECT t.* FROM income_trades t {clause} ORDER BY t.id DESC", params)
            trades = [dict(r) for r in cur.fetchall()]
            _income_attach_legs(cur, trades)

            def _score_key(t):
                v = _income_efficiency_score(t)
                if v is None:
                    return float("-inf") if rev else float("inf")
                return v

            trades.sort(key=_score_key, reverse=rev)
            trades = trades[offset:offset + limit]
        else:
            order_col = sort_map.get(sb, "t.open_date")
            order_dir = "ASC" if not rev else "DESC"
            cur.execute(f"""
                SELECT t.* FROM income_trades t {clause}
                ORDER BY {order_col} {order_dir}, t.id DESC
                LIMIT ? OFFSET ?
            """, params + [limit, offset])
            trades = [dict(r) for r in cur.fetchall()]
            _income_attach_legs(cur, trades)

    return {
        "data": trades,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": math.ceil(total / limit) if total else 0,
    }


def get_income_stats(ticker="", status="", strategy="", outcome="", date_from="", date_to=""):
    wfrag, params = _income_trades_where(ticker, status, strategy, outcome, "", date_from, date_to)
    clause = ("WHERE " + " AND ".join(wfrag)) if wfrag else ""

    with _connection() as conn:
        _ensure_income_tables(conn)
        cur = conn.cursor()

        cur.execute(f"""
            SELECT
                COUNT(*)                                          AS total_trades,
                SUM(CASE WHEN status != 'open' THEN 1 ELSE 0 END) AS closed_trades,
                SUM(CASE WHEN status  = 'open' THEN 1 ELSE 0 END) AS open_trades,
                SUM(CASE WHEN is_win = 1 THEN 1 ELSE 0 END)       AS win_count,
                SUM(CASE WHEN status != 'open' AND is_win = 0 THEN 1 ELSE 0 END) AS loss_count,
                SUM(CASE WHEN is_perfect_win = 1 THEN 1 ELSE 0 END) AS perfect_win_count,
                SUM(CASE WHEN status = 'assigned' THEN 1 ELSE 0 END) AS assigned_count,
                COALESCE(SUM(CASE WHEN status != 'open' THEN net_pnl ELSE 0 END), 0) AS total_pnl,
                COALESCE(SUM(net_premium), 0)                      AS total_premium_collected,
                COALESCE(SUM(CASE WHEN status = 'open' THEN net_premium ELSE 0 END), 0) AS open_premium
            FROM income_trades {clause}
        """, params)
        row = dict(cur.fetchone())

    closed = row["closed_trades"] or 0
    row["win_rate"] = round(100 * (row["win_count"] or 0) / closed, 1) if closed else 0
    row["perfect_win_rate"] = round(100 * (row["perfect_win_count"] or 0) / closed, 1) if closed else 0
    row["avg_pnl_per_trade"] = round((row["total_pnl"] or 0) / closed, 2) if closed else 0
    row["total_pnl"] = round(row["total_pnl"] or 0, 2)
    row["total_premium_collected"] = round(row["total_premium_collected"] or 0, 2)
    row["open_premium"] = round(row["open_premium"] or 0, 2)
    row["last_synced"] = get_income_sync_time()
    return row


def _income_week_monday(eff: str) -> date:
    """Return the Monday (ISO week start) of the calendar week containing *eff*."""
    d = date.fromisoformat(eff[:10])
    return d - timedelta(days=d.weekday())


def get_income_weekly_timeseries(
    ticker="",
    status="",
    strategy="",
    outcome="",
    date_from="",
    date_to="",
):
    """Weekly buckets aligned with Income KPI **Sum Net P&L** (option + recovery).

    Each trade is placed in the Monday week of its effective date
    (``COALESCE(NULLIF(TRIM(close_date), ''), open_date)``) — same as
    ``_income_trades_where``.

    Per trade, the week receives the same components the dashboard uses for
    **Sum Net P&L**:

    * **Option leg:** ``net_pnl`` when ``status != 'open'`` (matches
      ``get_income_stats`` ``total_pnl``); open trades contribute ``0``.
    * **Recovery:** ``true_recovery_pnl`` after ``attach_recovery_summaries``
      (assigned, non–fully-exercised rows only; others ``0``).

    Returns::

        week_starts: ISO Mondays, ascending
        weekly_sum_net: bar height = sum(option + recovery) in that week
        weekly_option_pnl: option-only portion (same week)
        weekly_recovery_pnl: true recovery portion (same week)
        cumulative_sum_net: running sum of ``weekly_sum_net`` (line)
    """
    from services.recovery import attach_recovery_summaries

    wfrag, params = _income_trades_where(ticker, status, strategy, outcome, "t", date_from, date_to)
    clause = ("WHERE " + " AND ".join(wfrag)) if wfrag else ""

    with _connection() as conn:
        _ensure_income_tables(conn)
        cur = conn.cursor()
        cur.execute(f"SELECT t.* FROM income_trades t {clause}", params)
        trades = [dict(r) for r in cur.fetchall()]
        _income_attach_legs(cur, trades)

    attach_recovery_summaries(trades)

    opt_by_week: dict[date, float] = defaultdict(float)
    rec_by_week: dict[date, float] = defaultdict(float)

    for t in trades:
        eff = (t.get("close_date") or "").strip()
        if not eff:
            eff = (t.get("open_date") or "").strip()
        if not eff or len(eff) < 10:
            continue
        try:
            wk = _income_week_monday(eff)
        except ValueError:
            continue
        st = (t.get("status") or "").strip()
        opt = float(t.get("net_pnl") or 0) if st != "open" else 0.0
        rec = float(t.get("true_recovery_pnl") or 0)
        opt_by_week[wk] += opt
        rec_by_week[wk] += rec

    week_keys = set(opt_by_week) | set(rec_by_week)

    df_s = (date_from or "").strip()
    dt_s = (date_to or "").strip()

    if df_s and dt_s:
        try:
            d0 = date.fromisoformat(df_s[:10])
            d1 = date.fromisoformat(dt_s[:10])
        except ValueError:
            d0, d1 = None, None
        if d0 and d1 and d0 > d1:
            d0, d1 = d1, d0
        if d0 and d1:
            axis_start = _income_week_monday(d0.isoformat())
            axis_end = _income_week_monday(d1.isoformat())
            weeks = []
            cur_w = axis_start
            while cur_w <= axis_end:
                weeks.append(cur_w)
                cur_w += timedelta(days=7)
        else:
            weeks = sorted(week_keys)
    else:
        weeks = sorted(week_keys)

    raw_weekly = [opt_by_week.get(w, 0.0) + rec_by_week.get(w, 0.0) for w in weeks]
    weekly_option = [round(opt_by_week.get(w, 0.0), 2) for w in weeks]
    weekly_recovery = [round(rec_by_week.get(w, 0.0), 2) for w in weeks]
    weekly_sum_net = [round(x, 2) for x in raw_weekly]

    cum = []
    running = 0.0
    for x in raw_weekly:
        running += x
        cum.append(round(running, 2))

    return {
        "week_starts": [w.isoformat() for w in weeks],
        "weekly_sum_net": weekly_sum_net,
        "weekly_option_pnl": weekly_option,
        "weekly_recovery_pnl": weekly_recovery,
        "cumulative_sum_net": cum,
        "date_from": df_s or None,
        "date_to": dt_s or None,
    }


def dismiss_recovery(trade_id, qty):
    """Set recovery_dismissed_qty on an assigned income trade."""
    with _connection() as conn:
        _ensure_income_tables(conn)
        conn.execute(
            "UPDATE income_trades SET recovery_dismissed_qty = ? WHERE id = ? AND status = 'assigned'",
            (qty, trade_id),
        )
        conn.commit()


def get_assigned_trades_for_ticker(ticker):
    """Return all assigned income trades + legs for a given ticker."""
    with _connection() as conn:
        _ensure_income_tables(conn)
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM income_trades
            WHERE underlying = ? AND status = 'assigned'
            ORDER BY close_date ASC
        """, (ticker.upper(),))
        trades = [dict(r) for r in cur.fetchall()]
        if trades:
            ids = [t["id"] for t in trades]
            ph = ",".join("?" for _ in ids)
            cur.execute(f"""
                SELECT * FROM income_trade_legs WHERE trade_id IN ({ph})
                ORDER BY trade_id, id
            """, ids)
            legs_map = {}
            for leg in cur.fetchall():
                ld = dict(leg)
                legs_map.setdefault(ld["trade_id"], []).append(ld)
            for t in trades:
                t["legs"] = legs_map.get(t["id"], [])
        return trades


def get_recovery_equity_trades(ticker, min_date, actions):
    """Return equity transactions for a ticker from min_date onwards, filtered by actions."""
    with _connection() as conn:
        cur = conn.cursor()
        ph = ",".join("?" for _ in actions)
        cur.execute(f"""
            SELECT trade_date, action, quantity, price, amount
            FROM transactions
            WHERE underlying = ? AND is_option = 0 AND category = 'equity'
              AND trade_date >= ?
              AND action IN ({ph})
              AND (is_from_option_event IS NULL OR is_from_option_event = 0)
            ORDER BY trade_date ASC, ROWID ASC
        """, [ticker.upper(), min_date] + list(actions))
        return [dict(r) for r in cur.fetchall()]


# ── Balance Snapshots ─────────────────────────────────────────────────────────

# Maps Schwab camelCase balance keys → snake_case DB column names (matches KEY_METRICS in balance.js)
_BALANCE_SNAPSHOT_COLS = [
    ("liquidationValue",        "liquidation_value"),
    ("equity",                  "equity"),
    ("cashBalance",             "cash_balance"),
    ("buyingPower",             "buying_power"),
    ("availableFunds",          "available_funds"),
    ("cashAvailableForTrading", "cash_available_for_trading"),
    ("optionBuyingPower",       "option_buying_power"),
    ("dayTradingBuyingPower",   "day_trading_buying_power"),
    ("maintenanceRequirement",  "maintenance_requirement"),
    ("longMarketValue",         "long_market_value"),
    ("shortMarketValue",        "short_market_value"),
    ("longOptionMarketValue",   "long_option_market_value"),
    ("shortOptionMarketValue",  "short_option_market_value"),
    ("unsettledCash",           "unsettled_cash"),
    ("marginBalance",           "margin_balance"),
]
_BAL_METRIC_COLS = [col for _, col in _BALANCE_SNAPSHOT_COLS]
_BAL_SCHWAB_KEYS = [k for k, _ in _BALANCE_SNAPSHOT_COLS]


def _ensure_balance_table(conn):
    metric_ddl = "\n    ".join(f"{col}  REAL," for col in _BAL_METRIC_COLS)
    conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS balance_snapshots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            as_of_date      TEXT    NOT NULL,
            account_display TEXT    NOT NULL,
            account_type    TEXT,
            {metric_ddl}
            saved_at        TEXT    DEFAULT (datetime('now')),
            UNIQUE(as_of_date, account_display)
        );
        CREATE INDEX IF NOT EXISTS idx_bal_snap_date ON balance_snapshots(as_of_date);
    """)


def save_balance_snapshot(accounts, aggregated, as_of_date=None):
    """
    Persist one row per account (plus one '__agg__' aggregate row) to balance_snapshots.

    Uses INSERT OR IGNORE on the (as_of_date, account_display) UNIQUE constraint so the
    first write of the day succeeds and all subsequent calls silently skip.

    Returns:
        { "as_of_date": str, "rows_written": int, "already_saved": bool }
    """
    from datetime import date as _date
    today = as_of_date or _date.today().isoformat()

    rows_to_insert = []

    for a in accounts:
        cb = a.get("current_balances") or {}
        row = [today, a.get("account_display", "—"), a.get("type")]
        for schwab_key in _BAL_SCHWAB_KEYS:
            row.append(cb.get(schwab_key))
        rows_to_insert.append(row)

    # Aggregate row: prefer aggregated_balance from API, fall back to summing per-account
    agg_src = aggregated or {}
    merged: dict[str, float] = {}
    for a in accounts:
        for k, v in (a.get("current_balances") or {}).items():
            if isinstance(v, (int, float)):
                merged[k] = merged.get(k, 0) + v
    agg_row = [today, "__agg__", "AGGREGATED"]
    for schwab_key in _BAL_SCHWAB_KEYS:
        agg_row.append(agg_src.get(schwab_key) if schwab_key in agg_src else merged.get(schwab_key))
    rows_to_insert.append(agg_row)

    all_cols = ["as_of_date", "account_display", "account_type"] + _BAL_METRIC_COLS
    ph = ",".join("?" for _ in all_cols)
    sql = f"INSERT OR IGNORE INTO balance_snapshots ({','.join(all_cols)}) VALUES ({ph})"

    rows_written = 0
    with _connection() as conn:
        _ensure_balance_table(conn)
        for row in rows_to_insert:
            cur = conn.execute(sql, row)
            rows_written += cur.rowcount
        conn.commit()

    return {
        "as_of_date": today,
        "rows_written": rows_written,
        "already_saved": rows_written == 0,
    }


def get_balance_history(limit_days=90):
    """
    Return balance_snapshots newest-first, aggregate row only (account_display='__agg__').
    Includes all metric columns plus as_of_date and saved_at.
    """
    with _connection() as conn:
        _ensure_balance_table(conn)
        cur = conn.execute("""
            SELECT as_of_date, account_type,
                   liquidation_value, equity, cash_balance, buying_power,
                   available_funds, cash_available_for_trading, option_buying_power,
                   day_trading_buying_power, maintenance_requirement,
                   long_market_value, short_market_value,
                   long_option_market_value, short_option_market_value,
                   unsettled_cash, margin_balance, saved_at
            FROM balance_snapshots
            WHERE account_display = '__agg__'
            ORDER BY as_of_date DESC
            LIMIT ?
        """, (limit_days,))
        return [dict(r) for r in cur.fetchall()]


def get_balance_snapshot_status(as_of_date=None):
    """Return the saved_at timestamp for today's aggregate snapshot, or None if not yet saved."""
    from datetime import date as _date
    today = as_of_date or _date.today().isoformat()
    with _connection() as conn:
        _ensure_balance_table(conn)
        cur = conn.execute(
            "SELECT saved_at FROM balance_snapshots WHERE as_of_date = ? AND account_display = '__agg__'",
            (today,),
        )
        row = cur.fetchone()
        return row["saved_at"] if row else None
