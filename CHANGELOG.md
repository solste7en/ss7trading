# Changelog

All notable changes to ss7trading are documented here.

---

## [0.1.0] — 2026-03-29

Initial release. Full pipeline from Schwab API → local database → browser dashboard, with live order entry.

### Dashboard (`app.py`)

**Positions tab**
- Live positions pulled from Schwab API with market value, unrealized P&L, and day P&L / day P&L %

**Quote Lookup tab**
- Single-symbol quote lookup with full bid/ask, change, volume, and 52-week range
- Bulk quotes for all currently held positions displayed in a table

**Trade History tab**
- Paginated transaction history from `trades.db` (2,800+ rows from Jan 2024 onward)
- Filterable by ticker, free-text search, and category (equity / option / income / transfer)
- Assignment tagging: equity rows resulting from option assignments are flagged with source link

**Realized G/L tab**
- Paginated closed-position gain/loss from `trades.db`
- Filterable by ticker and term (long-term / short-term)
- Wash sale and disallowed loss columns included

**⚡ Trade tab** *(new)*
- Stock/ETF order entry: Buy, Sell, Sell Short, Buy to Cover
- Single-leg option order entry: Sell to Open, Buy to Open, Buy to Close, Sell to Close for PUT and CALL
- Order types: Limit, Market, Stop, Stop Limit (equity); Limit, Market (options)
- Duration: Day or GTC (Good Till Cancelled)
- Session: Normal, Extended Hours (pre + post), Pre-Market only, Post-Market only
- Preview → Confirm flow before any order is submitted
- Uses schwab-py convenience functions (`equity_buy_limit`, `option_sell_to_open_limit`, etc.) for reliable order construction

**Open Orders tab** *(new)*
- Displays all working/queued orders across linked accounts
- Sortable by any column (order ID, status, type, ticker, side, qty, price, time entered)
- Filterable by ticker text, order type (Market/Limit/Stop/Stop Limit), and status
- Cancel button shown per order when `cancelable: true` is returned by Schwab

### API routes added
- `GET /api/orders` — fetch open orders, filtered to active statuses
- `POST /api/order` — place an equity or single-leg option order
- `DELETE /api/order/<id>` — cancel an open order by ID

### Transaction sync (`sync_trades.py`)

- Pulls transactions from the Schwab API for a configurable lookback window (default: 2 days)
- Maps Schwab API transaction types to human-readable action names consistent with CSV export format
- Parses both CSV-format and OCC-format option symbols; normalises to CSV format in the DB
- Two-stage deduplication: exact match on (date, action, symbol, amount) plus fuzzy match for ±2-day / ±2% amount variance to handle Sell/Sell Short aliases and partial-fill aggregation
- Tags equity rows that resulted from option assignment or exercise with `is_from_option_event` and `linked_option_action`
- Market-hours gate: no-op outside 9 AM–5 PM ET on weekdays unless `--force` is passed
- `--dry-run` mode prints what would be inserted without touching the database
- `--days N` flag controls the lookback window
- Logs all activity to `sync.log` and stdout

### Infrastructure
- SQLite database (`trades.db`) with `transactions` and `realized_gains` tables
- Historical data imported from Schwab CSV exports (Jan 2024 – Mar 2026)
- OAuth token management via `schwab-py` — token stored in `token.json`, auto-refreshes on every run
- Flask development server with `use_reloader=True` for automatic code reload during development
