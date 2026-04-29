"""Balance-snapshot table and per-day persistence."""

from datetime import date as _date

from core.db._conn import _connection

# Maps Schwab camelCase balance keys → snake_case DB column names
# (matches KEY_METRICS in static/js/balance.js).
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
    today = as_of_date or _date.today().isoformat()
    with _connection() as conn:
        _ensure_balance_table(conn)
        cur = conn.execute(
            "SELECT saved_at FROM balance_snapshots WHERE as_of_date = ? AND account_display = '__agg__'",
            (today,),
        )
        row = cur.fetchone()
        return row["saved_at"] if row else None
