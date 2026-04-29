"""Last-sync timestamps and the most-traded ticker shortcut."""

from datetime import datetime

from core.db._conn import _connection


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
