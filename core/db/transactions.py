"""Transaction history and realized-gains queries."""

import math
import sqlite3

# Look up ``_connection`` on the package at call time so tests that do
# ``monkeypatch.setattr(core.db, "_connection", mock)`` redirect connections.
import core.db as _db_pkg


def _connection():
    return _db_pkg._connection()


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
