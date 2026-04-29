"""SQLite connection helpers and PRAGMA tuning.

Exposes ``_connect`` (returns a configured ``sqlite3.Connection``) and
``_connection`` (context manager). DB-level PRAGMAs (journal_mode=WAL) are
applied once per process; per-connection PRAGMAs are applied on every
connect.

``DB_PATH`` is read from the ``core.db`` package namespace at call time so
tests can ``monkeypatch.setattr(core.db, "DB_PATH", tmp_path)`` and have
subsequent connections honor the new path.
"""

import sqlite3
from contextlib import contextmanager

_PRAGMAS_BOOTSTRAPPED = False


def _bootstrap_pragmas(conn: sqlite3.Connection) -> None:
    global _PRAGMAS_BOOTSTRAPPED
    if not _PRAGMAS_BOOTSTRAPPED:
        conn.execute("PRAGMA journal_mode=WAL")
        _PRAGMAS_BOOTSTRAPPED = True
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-20000")
    conn.execute("PRAGMA foreign_keys=ON")


def _connect():
    from core.db import DB_PATH  # late-bound so monkeypatched DB_PATH works
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _bootstrap_pragmas(conn)
    return conn


@contextmanager
def _connection():
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()
