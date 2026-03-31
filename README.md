# ss7trading — Schwab API Dashboard

A personal trading dashboard and order management tool built on the Schwab API. Tracks positions and trade history, syncs transactions to a local SQLite database, and supports order entry — including ladder orders — directly from the browser.

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
| **Positions** | Live positions with market value, unrealized P&L, and day P&L |
| **Quote Lookup** | Live quote for any symbol + quotes for all held positions |
| **Trade History** | Paginated transactions from `trades.db`, filterable by ticker, category, and keyword |
| **Realized G/L** | Closed-position gain/loss, filterable by ticker and short/long term |
| **Trade** | Place equity/ETF or single-leg option orders with preview confirmation |
| **Ladder** | Submit grouped limit orders at staggered prices; quick-fill helpers (even split, scale up/down); recent trades sidebar with pagination |
| **Open Orders** | All working/queued orders with sortable columns, filters, and cancel buttons |

---

## Ladder orders

The **Ladder** tab lets you submit multiple limit orders at different price levels in one action:

1. Enter ticker, action (Buy/Sell/Sell Short/Buy to Cover), duration, session
2. Use **Quick Fill** to auto-generate rungs (even split, scale up, scale down)
3. Or manually add/remove rungs with custom qty and price
4. **Preview** → **Confirm** submits all orders; per-rung success/failure is shown

The sidebar shows recent trades (equity + options with strike/expiry) for the selected ticker, paginated 20 per page.

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
| `/api/order` | POST | Place equity or option order |
| `/api/order/ladder` | POST | Place a ladder of limit orders |
| `/api/order/<id>` | DELETE | Cancel an order |
| `/api/test` | GET | Connectivity test |

---

## Files

| File | Purpose |
|------|---------|
| `app.py` | Flask app — API routes and dashboard serving |
| `db.py` | Database access layer (transactions, gains, top tickers) |
| `auth.py` | OAuth setup (run once for first-time login) |
| `config.py` | Loads credentials from `.env` |
| `sync_trades.py` | Schwab API → `trades.db` sync script |
| `templates/dashboard.html` | Dashboard HTML template |
| `static/css/style.css` | Dashboard styles |
| `static/js/dashboard.js` | Dashboard client-side logic |
| `requirements.txt` | Python dependencies |
| `../trades.db` | SQLite database (transactions and realized G/L) |
