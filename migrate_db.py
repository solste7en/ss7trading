"""
migrate_db.py — trades.db schema migration manager
----------------------------------------------------
Tracks applied migrations in a `schema_migrations` table and applies any
pending ones in version order.

Usage:
  python migrate_db.py          # apply all pending migrations (safe to re-run)
  python migrate_db.py --check  # report pending migrations without applying

Import API (used by sync_trades.py):
  from migrate_db import get_pending_migrations, apply_migrations
"""

import argparse
import sqlite3
import sys
from pathlib import Path

from config import DB_PATH

# ── migration registry ────────────────────────────────────────────────────────
# Each entry: (version_str, description, list_of_sql_statements)
# Keep sorted by version. Use zero-padded integers so ordering is unambiguous.
# Always add new migrations at the END — never edit existing entries.

MIGRATIONS: list[tuple[str, str, list[str]]] = [
    (
        "001",
        "Add activity_id column to transactions for Schwab dedup",
        [
            "ALTER TABLE transactions ADD COLUMN activity_id INTEGER",
            "CREATE INDEX IF NOT EXISTS idx_tx_activity_id ON transactions(activity_id)",
        ],
    ),
    # ── future migrations go here ──────────────────────────────────────────────
    # (
    #     "002",
    #     "Short description of what changes",
    #     [
    #         "ALTER TABLE ...",
    #     ],
    # ),
]

# ── internal helpers ──────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        sys.exit(
            f"ERROR: Database not found at {DB_PATH}\n"
            "Make sure trades.db exists before running migrations."
        )
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     TEXT PRIMARY KEY,
            description TEXT,
            applied_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def _applied_versions(conn: sqlite3.Connection) -> set[str]:
    cur = conn.execute("SELECT version FROM schema_migrations")
    return {row[0] for row in cur.fetchall()}


# ── public API ────────────────────────────────────────────────────────────────

def get_pending_migrations(conn: sqlite3.Connection | None = None) -> list[tuple[str, str]]:
    """
    Return a list of (version, description) for migrations that have not yet
    been applied to the database.  Pass an open connection or one will be
    opened and closed internally.
    """
    close_after = conn is None
    if conn is None:
        conn = _connect()
    try:
        _ensure_migrations_table(conn)
        applied = _applied_versions(conn)
        return [
            (ver, desc)
            for ver, desc, _ in MIGRATIONS
            if ver not in applied
        ]
    finally:
        if close_after:
            conn.close()


def apply_migrations(conn: sqlite3.Connection | None = None, verbose: bool = True) -> int:
    """
    Apply all pending migrations in version order.  Returns the number of
    migrations that were applied.
    """
    close_after = conn is None
    if conn is None:
        conn = _connect()
    try:
        _ensure_migrations_table(conn)
        applied = _applied_versions(conn)
        count = 0
        for ver, desc, statements in MIGRATIONS:
            if ver in applied:
                continue
            if verbose:
                print(f"  Applying migration {ver}: {desc} ...", end=" ", flush=True)
            try:
                for sql in statements:
                    conn.execute(sql)
                conn.execute(
                    "INSERT INTO schema_migrations (version, description) VALUES (?, ?)",
                    (ver, desc),
                )
                conn.commit()
                count += 1
                if verbose:
                    print("OK")
            except Exception as exc:
                conn.rollback()
                print(f"FAILED\n  ERROR: {exc}")
                sys.exit(1)
        return count
    finally:
        if close_after:
            conn.close()


# ── CLI entry point ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply pending schema migrations to trades.db"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report pending migrations without applying them (exits 1 if any are pending)",
    )
    args = parser.parse_args()

    conn = _connect()
    pending = get_pending_migrations(conn)

    if not pending:
        print("trades.db schema is up to date — no migrations needed.")
        conn.close()
        sys.exit(0)

    if args.check:
        print(f"trades.db has {len(pending)} pending migration(s):")
        for ver, desc in pending:
            print(f"  [{ver}] {desc}")
        print("\nRun `python migrate_db.py` to apply them.")
        conn.close()
        sys.exit(1)

    print(f"Applying {len(pending)} pending migration(s) to {DB_PATH}:")
    applied = apply_migrations(conn, verbose=True)
    conn.close()
    print(f"\nDone — {applied} migration(s) applied. trades.db is now up to date.")


if __name__ == "__main__":
    main()
