# Changelog

All notable changes to ss7trading are documented here.

---

## [0.1.1] — 2026-03-31

Dashboard restructure and new features: Overview tab, Ladder trade tab, and codebase refactoring.

### New: Overview tab (default landing page)

- Top 10 most-traded tickers displayed as cards with trade count split (equity / option)
- Each card shows the last 10 equity trades with Prev/Next pagination
- **Custom ticker lookup** card inline in the grid — enter any ticker to view its trade history
- Toggle to include or exclude options trades (default: include)
- Ladder shortcut button on each card to jump to the Ladder tab pre-filled

### New: Ladder Trade tab

- Submit grouped limit orders at staggered prices in one action
- **Quick Fill** helpers: Even Split, Scale Up, Scale Down — auto-generate rungs from total qty, start/end price, and rung count
- Manually add/remove rungs with per-row qty and price inputs
- Live summary bar (total rungs, total shares, estimated value)
- Preview → Confirm flow; per-rung success/failure results displayed after submission
- **Recent trades sidebar** showing last 20 trades (equity + options) for the selected ticker, with Prev/Next pagination and option type / strike / expiry columns

### New: `POST /api/order/ladder`

- Accepts a `rungs` array, places individual limit orders per rung via Schwab API
- Returns per-rung results so partial failures are visible

### New: `GET /api/top-tickers`

- Returns top N tickers by trade count with equity/option split and last N equity trades
- Uses `ROW_NUMBER()` window function for efficient single-query fetching

### Codebase refactoring

- **Extracted inline HTML**: Moved ~900-line `DASHBOARD_HTML` Python string to `templates/dashboard.html`, `static/css/style.css`, and `static/js/dashboard.js` — proper IDE support, syntax highlighting, and separation of concerns
- **Database access layer** (`db.py`): Extracted reusable query functions (`get_transactions`, `get_realized_gains`, `get_top_tickers`) out of route handlers
- `app.py` now uses `render_template()` instead of `render_template_string()`

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

**Trade tab**
- Stock/ETF order entry: Buy, Sell, Sell Short, Buy to Cover
- Single-leg option order entry: Sell to Open, Buy to Open, Buy to Close, Sell to Close for PUT and CALL
- Order types: Limit, Market, Stop, Stop Limit (equity); Limit, Market (options)
- Duration: Day or GTC (Good Till Cancelled)
- Session: Normal, Extended Hours (pre + post), Pre-Market only, Post-Market only
- Preview → Confirm flow before any order is submitted

**Open Orders tab**
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
