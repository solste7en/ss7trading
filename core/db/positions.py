"""Custom position lists, symbol assignments, and equity-trade aggregates.

Backs the Positions tab's drag-organize lists ("Trade a lot", "Shorting",
"Old / dead", "Other") and the auto-classification logic that decides which
list a newly-seen symbol belongs in.
"""

from collections import defaultdict

from core.db._conn import _connection

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
