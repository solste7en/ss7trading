# ss7trading — Schwab API Dashboard

A personal trading dashboard and order management tool built on the Schwab API. Tracks positions and trade history, syncs transactions to a local SQLite database, and supports order entry directly from the browser.

---

## First-time setup

### 1. Install dependencies
```bash
cd schwab_app
pip3 install -r requirements.txt
```

### 2. Create your `.env` file
```bash
cp .env.example .env
```
Open `.env` and fill in your **App Key** and **App Secret** from the [Schwab developer portal](https://developer.schwab.com).

### 3. Run the one-time OAuth login
```bash
python3 auth.py
```
A browser window opens → log in to Schwab → approve the app. The token is saved to `token.json`. You only need to do this once — tokens auto-refresh silently on every run.

> **Never commit `token.json` or `.env`** — both are already in `.gitignore`.

### 4. Start the dashboard
```bash
python3 app.py
```
Open **http://127.0.0.1:5050** in your browser.

---

## Daily use

```bash
python3 app.py        # start the dashboard (auto-reloads on file changes)
```

The server runs at `http://127.0.0.1:5050` and stays open in your terminal. Press `Ctrl+C` to stop it.

---

## Dashboard tabs

| Tab | Description |
|-----|-------------|
| **Positions** | Live positions with market value, unrealized P&L, and day P&L |
| **Quote Lookup** | Live quote for any symbol + quotes for all held positions |
| **Trade History** | Paginated transaction history from `trades.db`, filterable by ticker, category, and keyword |
| **Realized G/L** | Closed position gain/loss, filterable by ticker and short/long term |
| **⚡ Trade** | Place equity/ETF or single-leg option orders with preview confirmation |
| **Open Orders** | All working/queued orders with sortable columns, type/status filters, and cancel buttons |

---

## Placing trades

The **Trade** tab supports two modes:

**Stock / ETF**
- Actions: Buy, Sell, Sell Short, Buy to Cover
- Order types: Limit, Market, Stop, Stop Limit
- Duration: Day, GTC (Good Till Cancelled)
- Session: Normal (market hours), Extended Hours, Pre-Market, Post-Market

**Option (Single Leg)**
- Actions: Sell to Open, Buy to Open, Buy to Close, Sell to Close
- Option types: PUT, CALL
- Order types: Limit, Market
- Duration: Day, GTC
- Session: Normal, Extended Hours, Pre-Market, Post-Market

All orders go through a **Preview → Confirm** step before being submitted to Schwab.

---

## Transaction sync (`sync_trades.py`)

Pulls new transactions from the Schwab API and inserts them into `trades.db`, with exact and fuzzy deduplication to avoid double-counting across sources.

```bash
# Manual sync (respects market hours window by default)
python3 sync_trades.py

# Force a sync regardless of time (useful on weekends or for testing)
python3 sync_trades.py --force

# Look back further than the default 2 days
python3 sync_trades.py --days 7

# Dry run — shows what would be inserted without writing to the DB
python3 sync_trades.py --dry-run
```

**Recommended cron setup** (edit with `crontab -e`):
```cron
# Every 10 minutes on weekdays during market hours (9 AM–5 PM ET)
*/10 9-16 * * 1-5  cd /path/to/schwab_app && python3.11 sync_trades.py

# Once daily on weekends (catches assignments and exercises)
0 9 * * 0,6        cd /path/to/schwab_app && python3.11 sync_trades.py --force
```

Sync output is logged to `sync.log` and stdout.

---

## API endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/positions` | GET | All account positions (live from Schwab) |
| `/api/quotes` | GET | Live quotes for all currently held tickers |
| `/api/quote/<SYMBOL>` | GET | Live quote for a single symbol |
| `/api/transactions` | GET | Paginated trade history from `trades.db` |
| `/api/realized_gains` | GET | Paginated realized G/L from `trades.db` |
| `/api/orders` | GET | All open/working orders from Schwab |
| `/api/order` | POST | Place an equity or single-leg option order |
| `/api/order/<id>` | DELETE | Cancel an open order by ID |
| `/api/test` | GET | Connectivity test — returns raw account numbers |

---

## Files

| File | Purpose |
|------|---------|
| `app.py` | Flask web app — dashboard UI and all API routes |
| `auth.py` | OAuth setup (run once for first-time login) |
| `config.py` | Loads credentials from `.env` |
| `sync_trades.py` | Schwab API → `trades.db` transaction sync script |
| `requirements.txt` | Python dependencies |
| `.env` | API credentials (**never commit**) |
| `.env.example` | Credentials template |
| `token.json` | OAuth token, auto-created and auto-refreshed (**never commit**) |
| `sync.log` | Rolling log of all sync runs |
| `../trades.db` | SQLite database — transactions and realized G/L |
