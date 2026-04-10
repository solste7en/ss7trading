# ss7trading — Schwab API Dashboard

A personal trading dashboard and order management tool built on the Schwab API. Tracks positions and trade history, syncs transactions to a local SQLite database, and supports order entry — including ladder orders and multi-leg options strategies — directly from the browser.

**Current version: 0.2.1**

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
python3 auth.py
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
| **Quote** | Live quote for any symbol + quotes for held positions or custom watchlists (saved to DB) |
| **Trade History** | Paginated transactions from `trades.db`, filterable by ticker, category, and keyword; **Sync from Schwab** with last-sync time and results modal |
| **Realized G/L** | Closed-position gain/loss, filterable by ticker and short/long term |
| **📊 Income P&L** | Option income strategy performance tracker: clickable KPI cards, paginated trade table with expandable leg detail, filters by ticker/status/strategy/outcome; "Sync from Schwab" rebuilds from API |
| **Trade** | Place equity/ETF or single-leg option orders; live quote card, TradingView chart, option chain browser (click-to-fill), and current holdings panel |
| **Ladder** | Submit grouped limit orders at staggered prices; quick-fill helpers (even split, scale up/down); position unwind suggestion engine; current holdings panel + recent trades/open orders sidebar |
| **💰 Income** | Multi-leg options strategies: naked option, vertical spread, collar, equity+option bundle; suggestion engine with clickable cards; live P&L preview; paginated option chain (20 strikes/page) with strike dropdowns auto-filled from bid/ask |
| **Open Orders** | All working/queued orders with sortable columns, filters, and cancel buttons |

---

## Trade tab

The **Trade** tab supports both equity and single-leg option order entry in a two-column layout:

- **Left column** — order form (equity or option, switchable); order type, duration, session, preview/confirm flow
- **Right column** — live quote card (bid/ask, change, volume, 52W range); **TradingView Advanced Chart** (auto-loaded on ticker entry); **option chain browser** (select expiration, click any row to fill strike/expiry/type); **current holdings panel** (see open stock and option positions for the ticker before placing the trade)

## Ladder orders

The **Ladder** tab lets you submit multiple limit orders at different price levels in one action:

1. Enter ticker, action (Buy/Sell/Sell Short/Buy to Cover), duration, session
2. The **holdings panel** at the top of the sidebar shows your current position for the ticker
3. Use **Quick Fill** to auto-generate rungs (even split, scale up, scale down)
4. Or manually add/remove rungs with custom qty and price
5. **Preview** → **Confirm** submits all orders; per-rung success/failure is shown

The sidebar also shows recent trades (equity + options with strike/expiry) for the selected ticker, paginated 20 per page, and all open orders for that ticker.

## 💰 Income tab (multi-leg options strategies)

The **Income** tab is designed for generating income using options against existing equity positions, exiting at better prices, and creating costless downside protection via collars.

**Four strategy modes:**

- **Naked Option** — sell or buy a single option leg (covered call, cash-secured put, etc.)
- **Spread** — two same-type legs at different strikes for a net credit or debit (call/put credit spreads)
- **Collar** — sell one option + buy the opposite type in a single order (long or short position collars; targets NET_ZERO for costless structures)
- **Equity + Option Bundle** — combine an equity trade and option trade into one composite order

**Workflow:**

1. Enter a ticker — the **suggestion engine** immediately analyses your current position and the option chain, returning ranked strategy cards (covered calls with annualized yield, collar candidates, spread structures)
2. Click any suggestion card to pre-fill the form with recommended strikes, expiry, and quantity
3. Browse the inline **option chain** (paginated, 20 strikes/page, centered on ATM) and use **strike dropdowns** — selecting a strike auto-fills the net credit/debit from bid/ask
4. The **P&L preview panel** updates live as you adjust parameters — shows max profit, max loss, breakeven(s), and net credit/debit
5. **Preview** → **Confirm** submits via `POST /api/order/strategy`

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
*/10 9-16 * * 1-5  cd /path/to/ss7trading && source venv/bin/activate && python3 sync_trades.py
0 9 * * 0,6        cd /path/to/ss7trading && source venv/bin/activate && python3 sync_trades.py --force
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

If `sync_trades.py` or the app reports that the schema is out of date (for example missing `activity_id` on `transactions`), apply migrations once:

```bash
source venv/bin/activate
python3 migrate_db.py
```

Migrations are recorded in the `schema_migrations` table in `trades.db`.

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

## Files

| File | Purpose |
|------|---------|
| `app.py` | Flask app — API routes and dashboard serving |
| `db.py` | Database access layer (transactions, gains, watchlists, income trades) |
| `auth.py` | OAuth setup (run once for first-time login) |
| `config.py` | Loads credentials from `.env` |
| `sync_trades.py` | Schwab API → `trades.db` sync script |
| `migrate_db.py` | Versioned SQLite schema migrations for `trades.db` |
| `income_sync.py` | Income trade sync: FIFO matching, strategy grouping, P&L calculation |
| `recovery.py` | Assignment recovery tracking (LIFO equity matching) |
| `pytest.ini` | Pytest configuration |
| `tests/` | Unit tests and performance benchmarks |
| `templates/dashboard.html` | Dashboard HTML template |
| `static/css/style.css` | Dashboard styles |
| `static/js/dashboard.js` | Dashboard client-side logic |
| `requirements.txt` | Python dependencies |
| `../trades.db` | SQLite database (transactions, realized G/L, income trades) |
