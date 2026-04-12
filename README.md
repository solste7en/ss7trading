# ss7trading — Schwab API Dashboard

A personal trading dashboard and order management tool built on the Schwab API. Tracks positions and trade history, syncs transactions to a local SQLite database, and supports order entry — including ladder orders and multi-leg options strategies — directly from the browser.

**Current version: 0.3.0**

---

## First-time setup

### 1. Create a virtual environment (macOS/Linux)
```bash
python3 -m venv venv
source venv/bin/activate
```

> **macOS users:** Homebrew Python requires a virtual environment. If you skip this step, `pip3 install` will fail with an "externally-managed-environment" error.

### 2. Install dependencies
```bash
pip3 install -r requirements.txt
```

### 3. Create your `.env` file
```bash
cp .env.example .env
```
Fill in your **App Key** and **App Secret** from the [Schwab developer portal](https://developer.schwab.com).

### 4. Run the one-time OAuth login
```bash
python3 -m core.auth
```

### 5. Start the dashboard
```bash
python3 app.py
```
Open **http://127.0.0.1:5050** in your browser.

> **Never commit `token.json` or `.env`** — both are in `.gitignore`.

---

## Daily use

```bash
source venv/bin/activate
python3 app.py
```

The server runs at `http://127.0.0.1:5050`. Press `Ctrl+C` to stop.

---

## Dashboard tabs

| Tab | Description |
|-----|-------------|
| **Overview** | Top 10 most-traded tickers with recent equity trades, paginated; custom ticker lookup with options toggle |
| **Positions** | Live positions with market value, current price, unrealized P&L, and day P&L; sortable columns; ETF badge; options grouped under underlying with expand/collapse; PUT/CALL type and expiry columns |
| **📊 Analytics** | Portfolio performance charts (equity curve, daily P&L, drawdown), sector/industry exposure, HHI concentration score, income strategy breakdown; powered by balance snapshots + yfinance |
| **🔍 Consolidate** | Underwater positions list; sector overlap detection; peer comparison with composite scoring; ETF alternatives; tax-loss harvest candidates; covered call strategies for underwater positions |
| **Quote** | Live quote for any symbol + quotes for held positions or custom watchlists (saved to DB) |
| **Trade History** | Paginated transactions from `trades.db`, filterable by ticker, category, and keyword; **Sync from Schwab** with last-sync time and results modal |
| **📊 Income P&L** | Option income strategy performance tracker: clickable KPI cards, paginated trade table with expandable leg detail, filters by ticker/status/strategy/outcome; "Sync from Schwab" rebuilds from API |
| **Realized G/L** | Closed-position gain/loss from **Schwab portal Realized G/L CSV** (not the API); banner explains refresh workflow; last import time from `MAX(imported_at)` in `realized_gains` |
| **Trade** | Place equity/ETF or single-leg option orders; live quote card, TradingView chart, option chain browser (click-to-fill), and current holdings panel |
| **Ladder** | Submit grouped limit orders at staggered prices; quick-fill helpers (even split, scale up/down); position unwind suggestion engine; two-column layout with independent scroll on wide screens — form on the left; **Holdings**, **Open Orders**, then **Recent Trades** on the right |
| **💰 Income** | Multi-leg options strategies: naked option, vertical spread, collar, equity+option bundle; optional 2–7 rung **price ladder** (per-rung qty/limit or net); two-column layout (scrollable form + chain and suggestions on the left; holdings, open orders, recent option trades on the right); suggestion cards below the chain; live P&L preview; paginated option chain |
| **Open Orders** | All working/queued orders with sortable columns, filters, and cancel buttons |

---

## Trade tab

The **Trade** tab supports both equity and single-leg option order entry in a two-column layout:

- **Left column** — order form (equity or option, switchable); order type, duration, session, preview/confirm flow
- **Right column** — live quote card (bid/ask, change, volume, 52W range); **TradingView Advanced Chart** (auto-loaded on ticker entry); **option chain browser** (select expiration, click any row to fill strike/expiry/type); **current holdings panel** (see open stock and option positions for the ticker before placing the trade)

## Ladder orders

The **Ladder** tab lets you submit multiple limit orders at different price levels in one action:

1. Enter ticker, action (Buy/Sell/Sell Short/Buy to Cover), duration, session
2. On wide screens the **left column** (form, unwind suggestion, rungs) and **right column** scroll independently. The right column lists **Holdings**, then **Open Orders**, then **Recent Trades** (equity + optional options), each paginated where applicable.
3. Use **Quick Fill** to auto-generate rungs (even split, scale up, scale down)
4. Or manually add/remove rungs with custom qty and price
5. **Preview** → **Confirm** submits all orders; per-rung success/failure is shown

## 💰 Income tab (multi-leg options strategies)

The **Income** tab is designed for generating income using options against existing equity positions, exiting at better prices, and creating costless downside protection via collars.

**Four strategy modes:**

- **Naked Option** — sell or buy a single option leg (covered call, cash-secured put, etc.)
- **Spread** — two same-type legs at different strikes for a net credit or debit (call/put credit spreads)
- **Collar** — sell one option + buy the opposite type in a single order (long or short position collars; targets NET_ZERO for costless structures)
- **Equity + Option Bundle** — combine an equity trade and option trade into one composite order

**Workflow:**

1. Enter a ticker — the right column lists **Holdings**, then **Open Orders**, then **Recent Option Trades** (top to bottom). On wide screens the left column (form + chain + suggestions) and the right column scroll independently. The **suggestion engine** runs in the background; ranked cards appear at the **bottom of the left column** (under the option chain) so you can review holdings and working orders without losing the form above.
2. Click any suggestion card to pre-fill the form with recommended strikes, expiry, and quantity
3. Browse the inline **option chain** (paginated, 20 strikes/page, centered on ATM) and use **strike dropdowns** — selecting a strike auto-fills the net credit/debit from bid/ask
4. The **P&L preview panel** updates live as you adjust parameters — shows max profit, max loss, breakeven(s), net credit/debit, and **Max score (perfect, ÷DTE)** (hypothetical efficiency if max profit is realized by expiration)
5. **Preview** → **Confirm** submits via `POST /api/order/strategy`

**Optional price ladder (naked, spread, and collar only):** Check **Enable price ladder** under the ticker / expiration row, choose **Steps** (2–7, default 3), then enter **contracts** and **limit or net price** per rung. The usual single-row quantity and price fields are hidden while the ladder is on so values stay consistent. Auto-priced quotes from the chain fill **rung 1** (and the hidden single-row fields) until you edit that rung. Each rung is submitted as its own multi-leg order via `POST /api/order/strategy-ladder` (same payload shape as a single strategy order, repeated per rung). **Equity + Option (bundle)** mode hides the ladder.

## 📊 Income P&L tab (income strategy performance tracker)

Tracks the full history of option income trades from the current calendar year onward (since January 1 of the running year), grouped by strategy and linked to their open/close legs.

**How it works:**

1. Click **Sync from Schwab** to fetch all option transactions for the configured year window fresh from the Schwab API
2. The sync engine builds a FIFO-matched ledger: each STO/BTO opening leg is paired with its BTC/STC/Expired/Assigned closing leg
3. Matched legs are grouped into trades by strategy type (naked, spread, collar)
4. Assignment detection: the Schwab API marks assigned options as "Expired"; the sync cross-references equity TRADE events at the strike price to correctly reclassify them
5. P&L is calculated: net premium collected, close cost (BTC price or assignment intrinsic value), fees, net P&L, and win/perfect-win classification

**KPI cards** (5 are clickable — click to filter the table; click again to deselect):

- **Total Net P&L** — aggregate net P&L across all filtered trades
- **Win Rate** — percentage of closed trades with net P&L > 0
- **Perfect Win Rate** — percentage where the short option expired nearly worthless (close cost < 3% of original premium)
- **Closed Trades** / **Open Trades** / **Assigned** — trade counts by status

**Trade table:**

- Expandable rows — click any row to show individual leg detail (strike, expiry, direction, open/close action and price, dates)
- Filters: ticker, status (Open / Closed / Expired / Assigned), strategy (Naked / Spread / Collar), page size
- Strategy, status, and outcome badges with distinct colors
- **Score** (before Outcome): for closed trades, `net P&L ÷ max(1, days held) ÷ short-leg strike × 100` (negative on losses). Open trades show —. This is a simple time- and strike-scaled heuristic, not risk-adjusted notional.

On the **💰 Income** tab, the **P&amp;L Preview** panel includes **Max score (perfect, ÷DTE)** for naked, spread, and collar (not bundle): it uses max profit (or net credit) ÷ calendar days to selected expiration ÷ short strike × 100, as an upper bound if the trade expired perfectly—ignoring fees and path risk. With **price ladder** enabled, the same idea is shown for **rung 1** under the ladder preview table.

---

## Transaction sync

```bash
source venv/bin/activate
python3 sync_trades.py              # default 2-day lookback, market hours only
python3 sync_trades.py --force      # ignore market hours gate
python3 sync_trades.py --days 7     # look back 7 days
python3 sync_trades.py --dry-run    # preview without writing
```

**Cron setup** (edit with `crontab -e`):
```cron
*/10 9-16 * * 1-5  cd /path/to/schwab_app && source venv/bin/activate && python3 -m services.sync_trades
0 9 * * 0,6        cd /path/to/schwab_app && source venv/bin/activate && python3 -m services.sync_trades --force
```

---

## API endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/positions` | GET | Live positions from Schwab |
| `/api/quotes` | GET | Quotes for all held tickers |
| `/api/quote/<SYMBOL>` | GET | Single symbol quote |
| `/api/transactions` | GET | Paginated trade history from DB |
| `/api/realized_gains` | GET | Paginated realized G/L from DB |
| `/api/top-tickers` | GET | Top 10 tickers with recent equity trades |
| `/api/orders` | GET | Open/working orders from Schwab |
| `/api/order` | POST | Place equity or single-leg option order |
| `/api/order/ladder` | POST | Place a ladder of limit orders |
| `/api/order/strategy` | POST | Place a multi-leg strategy order (spread, collar, bundle) |
| `/api/order/strategy-ladder` | POST | Place multiple independent strategy orders (`orders` array, max 7); Income tab ladder |
| `/api/order/<id>` | DELETE | Cancel an order |
| `/api/option-expirations/<symbol>` | GET | Available option expiration dates for a symbol |
| `/api/option-chain` | GET | Calls and puts grouped by expiration and strike |
| `/api/strategy-suggest` | GET | Suggestion engine: position + chain analysis for a ticker |
| `/api/watchlists` | GET/POST | List all watchlists / create a new one |
| `/api/watchlists/<id>` | DELETE | Delete a watchlist |
| `/api/watchlists/<id>/symbols` | GET/POST | List symbols in a watchlist / add a symbol |
| `/api/watchlists/<id>/symbols/<sym>` | DELETE | Remove a symbol from a watchlist |
| `/api/quotes/list/<id>` | GET | Live quotes for all symbols in a watchlist |
| `/api/income/sync` | POST | Trigger full re-sync of income trades from Schwab API |
| `/api/income/trades` | GET | Paginated income trades (filters: ticker, status, strategy, outcome) |
| `/api/income/stats` | GET | Aggregate KPI stats for income trades |
| `/api/test` | GET | Connectivity test |
| `/api/trades/last-sync` | GET | Last trade sync time and most-traded ticker |
| `/api/trades/sync` | POST | Run trade sync from Schwab (dynamic lookback; rejects if DB migrations pending) |

---

## Database migrations

If the app reports that the schema is out of date, apply migrations once:

```bash
source venv/bin/activate
python3 -m core.migrate_db
```

Migrations are idempotent — safe to re-run. They are recorded in the `schema_migrations` table in `trades.db`. The migration runner also bootstraps the base `transactions` schema if it does not yet exist, so this is also the correct command to run on a fresh database.

---

## Testing and benchmarks

Install dev/test dependencies (included in `requirements.txt`):

```bash
source venv/bin/activate
pip3 install -r requirements.txt
```

### Unit tests (pytest)

Run the full suite from the project directory (`schwab_app/`):

```bash
python3 -m pytest tests/ -v
```

Quick run without benchmark timing noise:

```bash
python3 -m pytest tests/ --benchmark-disable -q
```

Configuration lives in `pytest.ini`. Tests use in-memory SQLite where possible so they do not require your real `trades.db` or live Schwab credentials.

### Verification (Realized G/L vs Schwab API)

The **Schwab Trader API** does not expose a Realized Gain/Loss export equivalent to **Accounts → Realized Gain/Loss** on the website. Long/short-term splits, wash sales, disallowed loss, and cost-basis method come from Schwab’s tax engine and are not available on transaction endpoints in a drop-in form. This app keeps **Realized G/L** as **portal CSV data** in `realized_gains` (re-import when you need updates); **Trade History** and **Income P&L** continue to use API-backed `transactions` where appropriate.

To reproduce the analysis (schema snapshot, coverage vs `transactions`, optional live JSON key scan), run from `schwab_app/`:

```bash
./venv/bin/python scripts/verify_realized_gl_coverage.py
./venv/bin/python scripts/verify_realized_gl_coverage.py --api --days 60   # needs token.json + .env
```

### Performance benchmarks (pytest-benchmark)

Benchmarks measure parsing, deduplication index builds, income matching, and a mocked sync-style pipeline:

```bash
python3 -m pytest tests/test_benchmarks.py --benchmark-only -v
```

To run benchmarks together with the rest of the tests (slower):

```bash
python3 -m pytest tests/ --benchmark-enable -v
```

Results print as a table (mean time, rounds, iterations) in the terminal.

---

## Frontend static assets

The dashboard uses no JavaScript bundler: Flask serves [templates/dashboard.html](templates/dashboard.html) with a single script tag.

- **[static/js/dashboard.js](static/js/dashboard.js)** — All client logic lives in one file so every `onclick="…"` handler in the template can call a **global** function. Near the top, shared helpers include **`fetchJson`** (wraps `fetch`, parses JSON, throws on `error` in the body or non-OK HTTP) and **`ladderResultTableHtml`** (shared table markup for equity ladder and Income strategy-ladder submission results).

- **[static/css/style.css](static/css/style.css)** — One stylesheet. A **`:root`** block defines **CSS custom properties** (`--color-*`) for the dark theme; rules reference `var(--color-…)` so palette tweaks stay centralized.

**If this grows further**, two optional directions (not required today): (1) split into several scripts loaded in a **fixed order** in the template (e.g. utils first, then feature files), still exposing functions on `window` for `onclick`; or (2) add a small **esbuild** (or similar) pipeline that bundles ES modules into one output file and assigns **`window.fnName = …`** for each handler the HTML needs.

---

## Project structure

```
schwab_app/
├── app.py                  # Flask entry point — registers all blueprints
├── requirements.txt        # Python dependencies
├── pyproject.toml          # Ruff + coverage config
├── pytest.ini              # Test config
│
├── core/                   # Infrastructure layer
│   ├── auth.py             # Schwab OAuth (run once: python -m core.auth)
│   ├── config.py           # Loads credentials from .env; defines DB_PATH
│   ├── db.py               # SQLite access layer (all query functions)
│   └── migrate_db.py       # Versioned schema migrations (python -m core.migrate_db)
│
├── services/               # Business logic layer
│   ├── accounts.py         # Account balance formatting
│   ├── analytics.py        # Performance series, exposure, concentration, consolidation scoring
│   ├── income_sync.py      # Option income sync: FIFO matching, strategy grouping, P&L
│   ├── options.py          # Option strategy suggestions, ladder, underwater strategies
│   ├── orders.py           # Order building helpers
│   ├── peers.py            # yfinance peer data with SQLite cache
│   ├── positions.py        # Position cleaning and list management
│   ├── quotes.py           # Quote formatting
│   ├── recovery.py         # Assignment recovery tracking (LIFO equity matching)
│   ├── schwab_client.py    # Schwab API client helpers
│   └── sync_trades.py      # Schwab API → trades.db sync
│
├── blueprints/             # Flask route handlers (one file per feature area)
│   ├── analytics.py        # /api/analytics/…
│   ├── balance.py          # /api/account-balances/…
│   ├── income.py           # /api/income/…
│   ├── options.py          # /api/option-*/…
│   ├── orders.py           # /api/order/…
│   ├── positions.py        # /api/positions, /api/position-lists
│   ├── quotes.py           # /api/quote/…, /api/quotes/…
│   ├── sync.py             # /api/trades/sync, /api/trades/last-sync
│   ├── transactions.py     # /api/transactions, /api/realized_gains, /api/top-tickers
│   └── watchlists.py       # /api/watchlists/…
│
├── templates/
│   └── dashboard.html      # Single-page dashboard template
│
├── static/
│   ├── css/style.css       # Dark theme (CSS custom properties + component rules)
│   └── js/                 # ES modules (analytics.js, overview.js, main.js, …)
│
├── tests/                  # pytest suite (~195 tests)
│   ├── conftest.py
│   ├── test_analytics.py
│   ├── test_app_helpers.py
│   ├── test_benchmarks.py
│   ├── test_db.py
│   ├── test_income_sync.py
│   ├── test_recovery.py
│   ├── test_routes.py
│   └── test_sync_trades.py
│
└── scripts/
    └── verify_realized_gl_coverage.py   # Audit realized_gains vs API

../trades.db                # SQLite database — one level above project root
```
