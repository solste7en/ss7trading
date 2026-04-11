# Changelog

All notable changes to ss7trading are documented here.

---

## [0.3.0] — 2026-04-11

Major feature release: **Analytics** and **Consolidation Intelligence** tabs. Portfolio-level performance tracking, sector exposure analysis, and an intelligent consolidation engine for overlapping/underwater positions with peer comparison, ETF alternatives, tax-loss harvesting, and options strategy suggestions.

### New: 📊 Analytics tab (Insight group)

Portfolio analytics powered by balance snapshots and external fundamentals from Yahoo Finance (`yfinance`).

- **Equity curve** — line chart of account value over time from daily balance snapshots
- **Daily P&L** — bar chart showing day-over-day value changes with positive/negative coloring
- **Drawdown** — area chart tracking peak-to-trough decline percentage
- **KPI cards** — current value, total return %, last day P&L, max drawdown, data points
- **Sector exposure** — doughnut chart + table showing sector breakdown with market values, weights, and constituent tickers; powered by yfinance sector/industry classification
- **Concentration** — HHI (Herfindahl index) score with "Diversified / Moderate / Concentrated" label; top-15 holdings by portfolio weight as a horizontal bar chart
- **Income performance** — monthly P&L chart + strategy breakdown table (count, P&L, win rate per strategy type)

### New: 🔍 Consolidate tab (Insight group)

Consolidation intelligence engine for managing overlapping and underwater positions.

- **Underwater positions list** — all equity positions with negative unrealized P&L, sorted by loss magnitude; each row has an "Analyze" button for deep-dive analysis
- **Sector overlap detector** — groups holdings by sector/industry to find duplicate exposure (e.g., "you hold 3 semiconductor stocks"); groups with 2+ tickers shown as color-coded cards
- **Peer comparison** — composite scoring algorithm ranks tickers within each overlap group on: revenue growth, profit margins, ROE, 52-week momentum, and position P/L; top scorer marked "keep", others "consolidate"
- **Consolidation detail panel** — click any ticker to see full fundamentals, peer comparison table, ETF alternatives, and tax-loss swap candidates
- **ETF alternatives** — curated mapping of ~30 sector/industry combinations to well-known ETFs (SMH, XLK, XBI, etc.); fetches live ETF info from yfinance
- **Tax-loss harvest candidates** — suggests similar-but-not-identical stocks and sector ETFs for harvesting; includes IRS wash sale rule warning
- **Underwater position strategies** — when analyzing an underwater position: covered calls near cost basis (with annualized yield and recovery timeline), OTM covered calls, sell-and-harvest tax loss comparison, and ETF swap suggestions

### Backend

- **`blueprints/analytics.py`** — 7 new API routes: `/api/analytics/performance`, `/exposure`, `/concentration`, `/income-summary`, `/consolidation`, `/consolidation/<symbol>`, `/underwater-strategies/<symbol>`
- **`services/analytics.py`** — pure logic: `compute_performance_series`, `compute_exposure`, `compute_concentration`, `find_overlap_groups`, `score_consolidation_candidates`, `suggest_tax_loss_swaps`
- **`services/peers.py`** — yfinance integration with 24-hour SQLite cache (`peer_cache` table); batch fetching; curated ETF mapping; peer discovery within portfolio
- **`services/options.py`** — new `suggest_underwater_strategies` for cost-basis-aware covered calls and tax-loss sell/swap economics

### Frontend

- **`static/js/analytics.js`** — ES module with Chart.js performance charts, sector doughnut, concentration bar chart, consolidation panel, peer comparison tables, underwater strategies cards
- **`templates/dashboard.html`** — two new tab buttons (📊 Analytics, 🔍 Consolidate) in the Insight group with panel containers
- **`static/js/main.js`**, **`state.js`**, **`tabs.js`** — wired up analytics module imports, state initialization, tab switching, and refresh handling
- **`static/css/style.css`** — analytics-specific styles: KPI rows, chart grid, exposure layout, overlap cards, detail panel, fundamentals grid, peer table, ETF cards, strategy cards, wash sale warning

### Dependencies

- **`yfinance`** added to `requirements.txt` — free Yahoo Finance API for sector, industry, fundamentals, and peer data

### Testing

- **`tests/test_analytics.py`** — 33 new tests covering all analytics service functions (performance series, exposure, concentration, overlap groups, consolidation scoring, tax-loss swaps, underwater strategies) plus 5 route smoke tests for new API endpoints
- Full suite: **196 tests passing**, all linting clean

---

## [0.2.2] — 2026-04-11

Positions and dashboard polish: clearer short-side metrics, share-weighted recent fills, exposure charts, and more consistent trade quantity display.

### Positions tab

- **10-fill average** — share-weighted mean of the last 10 equity fills (365-day window); dropped the earlier signed average that did not match execution prices
- **10F Net** — net share change over those same 10 fills (action-based sign, robust to inconsistent stored quantity signs)
- **vs recent** — compares absolute market price to the weighted fill anchor, with short- vs long-aware coloring
- **Short rows** — positive market quote from `abs(marketValue)/abs(qty)`; equity/ETF average price shown negative when quantity is short (Schwab convention)
- **Bottom charts** — dual doughnut charts (long vs short exposure) with top-N bucketing, “Others” breakdown, summary totals, long/short comparison bar, Chart.js-based tooltips and mini-chart for “Others”

### Trade History and lists

- **Quantity column** — display normalizes sign from **action** (e.g. Sell / Sell Short show negative qty) when the database row has the wrong sign, across the main history table and other trade lists

### Dependencies

- **Chart.js 4** (CDN) for position exposure doughnut charts

---

## [0.2.1] — 2026-04-10

Maintenance and quality release: schema migrations, safer database access, Trade History sync in the UI, expanded automated tests, performance benchmarks, and an Income P&L leg-matching fix for spreads.

### Database and configuration

- **`DB_PATH`** is defined once in `config.py` and imported by `db.py`, `sync_trades.py`, and `migrate_db.py`
- **`migrate_db.py`** — versioned migrations with a `schema_migrations` table; initial migration adds `activity_id` on `transactions` for Schwab deduplication
- **`db.py`** — `_connection()` context manager so connections are always closed; public **`get_income_trade_ids_filtered()`** for recovery aggregation without reaching into private helpers
- **`recovery.py`** — uses the new public DB helper instead of private `_connect` / `_ensure_income_tables` / `_income_trades_where`

### Trade History (dashboard)

- **Sync from Schwab** on the Trade History tab (toolbar), with last-sync display and a results modal (fetched / inserted / skipped / errors, lookback days, most-traded ticker)
- **`GET /api/trades/last-sync`**, **`POST /api/trades/sync`** — dynamic lookback from last sync + buffer; rejects sync when pending migrations exist

### `sync_trades.py` and `income_sync.py`

- Shared **`_fetch_and_prepare()`** for `sync()` and `dry_run()`; named constants for dedup fuzzy thresholds and assignment tagging; module-level **`TYPE_MAP`**; **`tag_assignments()`** wrapped in try/except so tagging failures do not lose successful inserts
- **Backfill** — include `trade_date` in the initial query to avoid per-row N+1 selects
- **`income_sync` — `START_DATE`** uses the current calendar year (January 1) instead of a hard-coded year

### Income P&L: long-leg close display (FIFO)

- **`_match_legs`** — **`Expired`** now closes **long** `Buy to Open` lots as well as short lots (previously only short-side closes matched, so long legs showed as perpetually “open” in the UI)
- **`Exchange or Exercise`** can close long lots (exercise) as well as short where applicable
- Dashboard leg row labels: **expired** / **exercised** for human-readable close states

### Testing and benchmarks

- **`pytest`** + **`pytest-benchmark`** in `requirements.txt`; **`pytest.ini`** at repo root
- **`tests/`** — coverage for `sync_trades`, `app` helpers, `db`, `recovery`, `income_sync`, plus **`tests/test_benchmarks.py`** for hot paths (parse throughput, dedup index build, `_match_recovery`, `suggest_position_unwind`, sync-style pipeline)

---

## [0.2.0] — 2026-04-04

Major feature release. Introduces the **Income P&L tab** — a full option income strategy performance tracker backed by a new SQLite schema and sync engine. Also enriches the Positions tab with option grouping and expiry display, and upgrades the Income tab with a paginated option chain and dynamic strike dropdowns.

### New: Income P&L tab (Records group)

A new **📊 Income P&L** tab added to the Records group (**Trade History | Realized G/L | Income P&L**) for tracking the performance of all option income strategies written since 2026-01-01.

**Data model (3 new SQLite tables):**

- `income_trades` — one row per identified income trade (underlying, strategy, open/close dates, net premium, close cost, net P&L, win/perfect-win flags, assignment stock price, dedup key)
- `income_trade_legs` — individual option legs linked to each trade (strike, expiry, direction, open/close action, prices, dates)
- `income_sync_meta` — stores the timestamp of the last successful sync

**Sync engine (`income_sync.py`):**

- "Sync from Schwab" button triggers `POST /api/income/sync`, which calls the Schwab API for all transactions since 2026-01-01 and rebuilds the tables from scratch (thread-safe; rejects concurrent sync requests with HTTP 409)
- **Assignment detection fix**: Schwab's API labels assigned options as `"Expired"` (with `netAmount = null`) and generates a separate equity TRADE at the strike price. The sync engine cross-references all equity TRADE events against each `"Expired"` option event — if an equity buy/sell at the exact strike price exists within 5 days of the option's expiry, the event is reclassified as `"Assigned"` before FIFO matching. Validated against 12/12 assignment events across NVDA, LUV, HUT, PRLB, SPOT, TSLA, PTON
- **FIFO matching**: opening legs (STO/BTO) are matched to closing legs (BTC/STC/Expired/Assigned) per position key `(underlying, type, strike, expiry)` using first-in-first-out order
- **Strategy grouping**: matched legs are grouped into trades — naked put/call (lone STO), vertical spread (STO + BTO same type, same expiry), collar (STO + BTO opposite type, same expiry); standalone long legs excluded
- **Assignment P&L**: for assigned legs, the closing cost is the intrinsic value at assignment — `max(0, K − S)` for short puts, `max(0, S − K)` for short calls — looked up from `get_price_history_every_day` for the stock closing price on the assignment date
- **Win / Perfect-win classification**: a trade is a win if `net_pnl > 0`; a "perfect win" if the short option expired with close cost < 3% of original sell premium (threshold configurable via `PERFECT_WIN_THRESHOLD`)

**New API endpoints:**

- `POST /api/income/sync` — triggers full re-sync; returns count of trades written
- `GET /api/income/trades` — paginated income trades with filters: `ticker`, `status`, `strategy`, `outcome` (win / perfect / assigned / open / closed), `page`, `limit`
- `GET /api/income/stats` — aggregate KPIs filtered by optional `ticker`

**UI:**

- Toolbar with ticker filter, status dropdown (All / Open / Closed / Expired / Assigned), strategy dropdown (All / Naked / Spread / Collar), page-size selector, sync button with last-sync timestamp
- Six KPI cards at the top: **Total Net P&L**, **Win Rate**, **Perfect Win Rate**, **Closed Trades**, **Open Trades**, **Assigned** — the five non-P&L cards are **clickable** to filter the table to that subset; clicking again deselects
- Trade table with expandable rows: click any row to reveal individual leg detail (strike, expiry, direction, open/close action, prices, dates)
- Strategy, status, and outcome badges with distinct colors per type
- Pagination shared with the existing generic `renderPagination` helper

---

### Positions tab: options grouping and expiry display

- **Options grouped under underlying equity**: option positions are hidden by default and can be expanded/collapsed by clicking the underlying equity row (triangle toggle)
- **PUT / CALL badge column** added for option rows
- **Expiration date column** for options: extracted from the 21-character OCC symbol (`NVDA  260410P00170000` → `Apr 10 '26`) and displayed in a separate column
- **Sort by expiry**: option rows within a group are sorted by expiration date
- **Unrealized P&L for short positions** now correctly reads `shortOpenProfitLoss` instead of always using the long field

---

### Income tab: paginated option chain and strike dropdowns

- **Option chain pagination**: chain loads 60 strikes, displays 20 at a time centred on the current price; Previous / Next buttons navigate pages; page label shows current position (e.g. `Page 2 / 3`)
- **Strike price dropdowns**: all six strike inputs across the four strategy forms (Naked, Spread, Collar, Bundle) replaced with `<select>` elements populated with the 20 currently visible strikes, each option showing the strike and its bid/ask; dropdowns update automatically when the chain page changes
- **Auto-fill net credit/debit**: selecting a strike immediately calculates and fills the net credit/debit field from the option's bid/ask (correct bid vs ask logic per leg direction)
- **Income tab emoji** 💰 added to the tab button

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
