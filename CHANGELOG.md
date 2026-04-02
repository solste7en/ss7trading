# Changelog

All notable changes to ss7trading are documented here.

---

## [0.1.5] — 2026-04-02

Major feature release. Introduces the **Income tab** for advanced multi-leg options strategies, enriches the Positions tab, adds watchlist management to the Quote tab, and fixes several usability issues.

### New: Income tab (multi-leg options strategies)

A second strategy tab placed in the Trade group — **Trade | Ladder | Income | Open Orders** — focused on generating income, exiting positions at better prices, and providing downside protection using multi-leg option structures.

**Four strategy modes** (toggle buttons at the top of the left column):

- **Naked Option** — single-leg sell-to-open or buy-to-open; used for covered calls (long equity) and cash-secured puts (short equity)
- **Vertical Spread** — two same-type option legs, same expiry, different strikes; supports both call credit spreads and put credit spreads
- **Collar** — simultaneous sell one option + buy the opposite type; targets NET_ZERO for costless protection; works for both long positions (sell call + buy put) and short positions (sell put + buy call)
- **Equity + Option Bundle** — bundle an equity trade with an option trade as a single composite order

**Suggestion engine (right sidebar):**

- Automatically analyses current positions and option chain on ticker entry
- For **long positions** (e.g. LYFT): suggests covered calls (with annualized yield), protective collars (near-zero cost), and call credit spreads
- For **short positions** (e.g. NVDA): suggests cash-secured puts (with annualized yield), short collars, and put credit spreads
- Detects existing short option positions and suggests close strategies
- Each suggestion is a **clickable card** that pre-fills the strategy form with recommended strike, expiry, quantity, and estimated premium

**P&L preview (live, updates as you type):**

- Updates in real time as strikes and premiums are adjusted
- Naked: max profit, max loss (or "Unlimited"), breakeven
- Vertical spread: net credit/debit, max profit, max loss, breakeven
- Collar: net cost, protection floor, cap ceiling

**Option chain browser:**

- Same expiration-selector + full chain table as the Trade tab
- Click any call or put row to fill the active strategy form

**Right sidebar:**

- Current holdings panel (same as Trade/Ladder tabs)
- Recent option trades for the selected ticker, paginated 15 per page
- Open orders for the ticker with per-order cancel and Cancel All

**New backend API endpoints:**

- `POST /api/order/strategy` — place a multi-leg order (spread, collar, covered, bundle) using `OrderBuilder` with `VERTICAL`, `COLLAR_SYNTHETIC`, `COVERED`, and `NONE` complex order strategy types
- `GET /api/strategy-suggest?ticker=` — server-side suggestion engine: fetches positions, live quote, and option chain (2 nearest expirations); returns structured suggestions with pre-filled form values, estimated premiums, and annualized yields

---

### Positions tab enhancements

- **Current price column** (Mkt Price) added to the positions table; computed server-side as `market_value / abs(qty)` for equity/ETF and `market_value / (abs(qty) × 100)` for options
- **ETF badge**: `COLLECTIVE_INVESTMENT` asset type now mapped to `ETF` and displayed with a distinct teal badge (`#164e45` / `#5eead4`), distinct from Equity and Option
- **Sortable columns**: click any column header to sort by that field; second click reverses direction; sort arrows rendered inline; client-side sorting preserves backend ordering as the default

---

### Quote tab — watchlist management

- **All Positions** default watchlist shows live quotes for all currently held equity/ETF symbols (same as before, now a proper tab)
- **Custom watchlists** — create named lists, add/remove symbols, delete lists; persisted in SQLite (`watchlists` + `watchlist_symbols` tables via `db.py`)
- **Tabbed UI**: watchlist tabs rendered inline; active list highlighted; edit bar with symbol input and Delete List button shown only for custom lists
- **New API endpoints**:
  - `GET/POST /api/watchlists` — list all / create new
  - `DELETE /api/watchlists/<id>` — delete a list
  - `GET/POST /api/watchlists/<id>/symbols` — list symbols / add symbol
  - `DELETE /api/watchlists/<id>/symbols/<symbol>` — remove a symbol
  - `GET /api/quotes/list/<id>` — fetch live quotes for a watchlist

---

### Bug fixes

- **Tab highlight fix**: `switchTab()` now extracts the active tab name from each element's `onclick` attribute rather than relying on DOM index order, which was broken after prior tab reordering

---

## [0.1.2] — 2026-04-01

Trade tab and Ladder tab improvements: live TradingView chart, option chain browser, current holdings panel with color-coded table.

### Trade tab overhaul

- **Two-column layout**: left column holds the order form; right column holds a live quote card, TradingView chart, and option chain browser
- **Live quote card**: on ticker entry, fetches real-time bid/ask, last price, change/%, volume, and 52-week range via `GET /api/quote/<symbol>`; pre-fills the limit price field
- **TradingView Advanced Chart embed**: dynamically injects the TradingView widget using `createElement`/`appendChild` (fixes script execution vs. `innerHTML`); exchange auto-detected (AMEX for ETFs, NYSE for major names, NASDAQ default); `allow_symbol_change` enabled
- **Option chain browser**: expiration date selector via `GET /api/option-expirations/<symbol>`; chain table populated via `GET /api/option-chain`; click any cell to auto-fill the option form fields (strike, expiry, option type)
- **Option chain contrast**: headers use stronger blue/red backgrounds (`#0f2847` / `#3f1518`); cells have tinted call/put backgrounds; ITM rows highlighted; hover states per side; body text `#e2e8f0`

### Holdings panel (Trade + Ladder tabs)

- **Current holdings** displayed immediately when a ticker is entered, on both the Trade and Ladder tabs
- Fetches `GET /api/positions` and matches equity by exact symbol and options by OCC symbol prefix
- Rendered as a mini table with columns: **Type** · **Side** · **Qty** · **Detail** · **Mkt Value**
- Color-coded badges: `CALL` (green), `PUT` (red), `STOCK` (slate), `Long` (blue), `Short` (amber)
- Option descriptions parsed from Schwab format (`04/02/2026 $13 Put` → `Apr 2 '26  $13.00`)
- Large quantities formatted with commas (17,577 not 17577)
- Panel clears when ticker field is cleared

### Ladder tab layout

- Holdings panel moved from the left form column to the **top of the right sidebar**, above Recent Trades — keeps the form uncluttered and mirrors the Trade tab layout

### New API endpoints

- `GET /api/option-expirations/<symbol>` — returns available expiration dates for a symbol's option chain
- `GET /api/option-chain` — returns calls and puts for a symbol/expiry range, grouped by expiration and keyed by strike

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
