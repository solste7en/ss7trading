"""Watchlist tables and symbol management."""

from core.db._conn import _connection


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


def get_watchlist_symbols_batch(list_ids):
    """Return ``{list_id: [symbol, ...]}`` for the given list_ids in one query."""
    ids = [int(i) for i in list_ids]
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    with _connection() as conn:
        _ensure_watchlist_tables(conn)
        cur = conn.cursor()
        cur.execute(
            f"SELECT watchlist_id, symbol FROM watchlist_symbols "
            f"WHERE watchlist_id IN ({placeholders}) ORDER BY watchlist_id, symbol",
            ids,
        )
        out = {i: [] for i in ids}
        for row in cur.fetchall():
            out[row["watchlist_id"]].append(row["symbol"])
        return out


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
