"""
db.py — Database access layer for ss7trading.
Provides reusable query functions for the trades.db SQLite database.
"""
import math
import sqlite3
from datetime import datetime
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
    conn = _connect()
    _ensure_watchlist_tables(conn)
    cur = conn.cursor()
    cur.execute("""
        SELECT w.id, w.name, COUNT(ws.id) AS symbol_count
        FROM watchlists w
        LEFT JOIN watchlist_symbols ws ON ws.watchlist_id = w.id
        GROUP BY w.id
        ORDER BY w.name
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def create_watchlist(name):
    conn = _connect()
    _ensure_watchlist_tables(conn)
    cur = conn.cursor()
    cur.execute("INSERT INTO watchlists (name) VALUES (?)", (name,))
    conn.commit()
    list_id = cur.lastrowid
    conn.close()
    return {"id": list_id, "name": name, "symbol_count": 0}


def delete_watchlist(list_id):
    conn = _connect()
    _ensure_watchlist_tables(conn)
    conn.execute("DELETE FROM watchlists WHERE id = ?", (list_id,))
    conn.commit()
    conn.close()


def get_watchlist_symbols(list_id):
    conn = _connect()
    _ensure_watchlist_tables(conn)
    cur = conn.cursor()
    cur.execute(
        "SELECT symbol FROM watchlist_symbols WHERE watchlist_id = ? ORDER BY symbol",
        (list_id,),
    )
    syms = [r["symbol"] for r in cur.fetchall()]
    conn.close()
    return syms


def add_watchlist_symbol(list_id, symbol):
    conn = _connect()
    _ensure_watchlist_tables(conn)
    conn.execute(
        "INSERT OR IGNORE INTO watchlist_symbols (watchlist_id, symbol) VALUES (?, ?)",
        (list_id, symbol.upper()),
    )
    conn.commit()
    conn.close()


def remove_watchlist_symbol(list_id, symbol):
    conn = _connect()
    _ensure_watchlist_tables(conn)
    conn.execute(
        "DELETE FROM watchlist_symbols WHERE watchlist_id = ? AND symbol = ?",
        (list_id, symbol.upper()),
    )
    conn.commit()
    conn.close()


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
    conn.commit()


def clear_income_trades():
    conn = _connect()
    _ensure_income_tables(conn)
    conn.executescript("DELETE FROM income_trade_legs; DELETE FROM income_trades;")
    conn.commit()
    conn.close()


def upsert_income_trade(trade, legs):
    """Insert or replace an income trade with its legs.
    `trade` is a dict, `legs` is a list of dicts."""
    conn = _connect()
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
                assignment_stock_price=?, synced_at=datetime('now')
            WHERE id=?
        """, (trade["underlying"], trade["strategy"], trade["open_date"],
              trade.get("close_date"), trade["status"], trade.get("days_held"),
              trade.get("net_premium"), trade.get("close_cost", 0),
              trade.get("fees", 0), trade.get("net_pnl"),
              trade.get("net_pnl_pct"), trade.get("is_win", 0),
              trade.get("is_perfect_win", 0),
              trade.get("assignment_stock_price"), trade_id))
    else:
        cur.execute("""
            INSERT INTO income_trades
                (underlying, strategy, open_date, close_date, status, days_held,
                 net_premium, close_cost, fees, net_pnl, net_pnl_pct,
                 is_win, is_perfect_win, assignment_stock_price, dedup_key)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (trade["underlying"], trade["strategy"], trade["open_date"],
              trade.get("close_date"), trade["status"], trade.get("days_held"),
              trade.get("net_premium"), trade.get("close_cost", 0),
              trade.get("fees", 0), trade.get("net_pnl"),
              trade.get("net_pnl_pct"), trade.get("is_win", 0),
              trade.get("is_perfect_win", 0),
              trade.get("assignment_stock_price"), trade["dedup_key"]))
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
    conn.close()
    return trade_id


def set_income_sync_time():
    conn = _connect()
    _ensure_income_tables(conn)
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute("""
        INSERT INTO income_sync_meta (id, last_synced) VALUES (1, ?)
        ON CONFLICT(id) DO UPDATE SET last_synced = excluded.last_synced
    """, (now,))
    conn.commit()
    conn.close()


def get_income_sync_time():
    conn = _connect()
    _ensure_income_tables(conn)
    cur = conn.cursor()
    cur.execute("SELECT last_synced FROM income_sync_meta WHERE id=1")
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def get_income_trades(page=1, limit=25, ticker="", status="", strategy="", outcome=""):
    offset = (page - 1) * limit
    where, params = [], []
    if ticker:
        where.append("t.underlying = ?"); params.append(ticker.upper())
    if status:
        where.append("t.status = ?"); params.append(status)
    if strategy:
        where.append("t.strategy LIKE ?"); params.append(f"%{strategy}%")
    if outcome == "win":
        where.append("t.is_win = 1")
    elif outcome == "perfect":
        where.append("t.is_perfect_win = 1")
    elif outcome == "assigned":
        where.append("t.status = 'assigned'")
    elif outcome == "open":
        where.append("t.status = 'open'")
    elif outcome == "closed":
        where.append("t.status = 'closed'")

    clause = ("WHERE " + " AND ".join(where)) if where else ""

    conn = _connect()
    _ensure_income_tables(conn)
    cur = conn.cursor()

    cur.execute(f"SELECT COUNT(*) FROM income_trades t {clause}", params)
    total = cur.fetchone()[0]

    cur.execute(f"""
        SELECT t.* FROM income_trades t {clause}
        ORDER BY t.open_date DESC, t.id DESC
        LIMIT ? OFFSET ?
    """, params + [limit, offset])
    trades = [dict(r) for r in cur.fetchall()]

    if trades:
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
    else:
        for t in trades:
            t["legs"] = []

    conn.close()
    return {
        "data": trades,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": math.ceil(total / limit) if total else 0,
    }


def get_income_stats(ticker=""):
    where, params = [], []
    if ticker:
        where.append("underlying = ?"); params.append(ticker.upper())
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    conn = _connect()
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
    conn.close()

    closed = row["closed_trades"] or 0
    row["win_rate"] = round(100 * (row["win_count"] or 0) / closed, 1) if closed else 0
    row["perfect_win_rate"] = round(100 * (row["perfect_win_count"] or 0) / closed, 1) if closed else 0
    row["avg_pnl_per_trade"] = round((row["total_pnl"] or 0) / closed, 2) if closed else 0
    row["total_pnl"] = round(row["total_pnl"] or 0, 2)
    row["total_premium_collected"] = round(row["total_premium_collected"] or 0, 2)
    row["open_premium"] = round(row["open_premium"] or 0, 2)
    row["last_synced"] = get_income_sync_time()
    return row
