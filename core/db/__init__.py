"""Database access layer for ss7trading.

Split into focused modules by domain. This package re-exports every name
that the wider codebase (and tests) import from ``core.db`` so existing
``from core.db import X`` statements keep working.

``DB_PATH`` is re-exported here so tests that do
``monkeypatch.setattr(core.db, "DB_PATH", tmp_path)`` continue to redirect
new connections.
"""

from core.config import DB_PATH

# Connection helpers (private but imported by services and tests).
from core.db._conn import _bootstrap_pragmas, _connect, _connection
from core.db.balance import (
    _BAL_METRIC_COLS,
    _BAL_SCHWAB_KEYS,
    _BALANCE_SNAPSHOT_COLS,
    _ensure_balance_table,
    get_balance_history,
    get_balance_snapshot_status,
    save_balance_snapshot,
)
from core.db.income import (
    _ensure_income_tables,
    _income_attach_legs,
    _income_efficiency_score,
    _income_trades_where,
    _income_week_monday,
    clear_income_trades,
    dismiss_recovery,
    get_assigned_trades_for_ticker,
    get_income_stats,
    get_income_sync_time,
    get_income_trade_ids_filtered,
    get_income_trades,
    get_income_weekly_timeseries,
    get_recovery_equity_trades,
    set_income_sync_time,
    upsert_income_trade,
)
from core.db.positions import (
    POSITION_LIST_ACTIVE_ID,
    POSITION_LIST_OTHER_ID,
    POSITION_LIST_SHORT_ID,
    POSITION_LIST_STALE_ID,
    POSITION_TOP_ACTIVE_K,
    _compute_top_active_symbols,
    _ensure_position_list_tables,
    _ensure_position_symbol_sort_index_column,
    _net_qty_sign,
    _next_symbol_sort_index,
    _seed_default_position_lists,
    create_position_list,
    delete_position_list,
    get_all_position_assignments,
    get_equity_share_volume_365d_batch,
    get_equity_trade_counts_365d,
    get_position_lists,
    get_recent_equity_trade_metrics_batch,
    get_symbol_sort_map_for_symbols,
    rename_position_list,
    reorder_position_lists,
    reorder_symbols_within_list,
    resolve_position_assignments,
    upsert_position_assignments,
)
from core.db.trade_meta import (
    _ensure_trades_sync_meta,
    get_most_traded_ticker,
    get_trade_sync_time,
    set_trade_sync_time,
)
from core.db.transactions import (
    get_realized_gains,
    get_top_tickers,
    get_transactions,
)
from core.db.unwind import (
    _pack_params,
    _summarise_exits,
    suggest_position_unwind,
)
from core.db.watchlists import (
    _ensure_watchlist_tables,
    add_watchlist_symbol,
    create_watchlist,
    delete_watchlist,
    get_watchlist_symbols,
    get_watchlist_symbols_batch,
    get_watchlists,
    remove_watchlist_symbol,
)

__all__ = [
    "DB_PATH",
    "_connect", "_connection", "_bootstrap_pragmas",
    "get_transactions", "get_realized_gains", "get_top_tickers",
    "suggest_position_unwind", "_summarise_exits", "_pack_params",
    "POSITION_LIST_ACTIVE_ID", "POSITION_LIST_SHORT_ID",
    "POSITION_LIST_STALE_ID", "POSITION_LIST_OTHER_ID", "POSITION_TOP_ACTIVE_K",
    "_ensure_position_list_tables", "_ensure_position_symbol_sort_index_column",
    "_next_symbol_sort_index", "_seed_default_position_lists",
    "get_position_lists", "create_position_list", "rename_position_list",
    "delete_position_list", "get_all_position_assignments",
    "get_symbol_sort_map_for_symbols", "upsert_position_assignments",
    "reorder_position_lists", "reorder_symbols_within_list",
    "get_equity_trade_counts_365d", "get_equity_share_volume_365d_batch",
    "_compute_top_active_symbols", "resolve_position_assignments",
    "_net_qty_sign", "get_recent_equity_trade_metrics_batch",
    "_ensure_watchlist_tables", "get_watchlists", "create_watchlist",
    "delete_watchlist", "get_watchlist_symbols", "get_watchlist_symbols_batch",
    "add_watchlist_symbol", "remove_watchlist_symbol",
    "_ensure_income_tables", "clear_income_trades", "upsert_income_trade",
    "set_income_sync_time", "get_income_sync_time",
    "_income_trades_where", "get_income_trade_ids_filtered",
    "_income_attach_legs", "_income_efficiency_score",
    "get_income_trades", "get_income_stats",
    "_income_week_monday", "get_income_weekly_timeseries",
    "dismiss_recovery", "get_assigned_trades_for_ticker",
    "get_recovery_equity_trades",
    "_ensure_trades_sync_meta", "get_trade_sync_time", "set_trade_sync_time",
    "get_most_traded_ticker",
    "_BALANCE_SNAPSHOT_COLS", "_BAL_METRIC_COLS", "_BAL_SCHWAB_KEYS",
    "_ensure_balance_table", "save_balance_snapshot",
    "get_balance_history", "get_balance_snapshot_status",
]
