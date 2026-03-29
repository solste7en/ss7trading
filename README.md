# ss7trading — Schwab API Dashboard

## First-time setup (do this once)

### 1. Install dependencies
```bash
cd schwab_app
pip3 install -r requirements.txt
```

### 2. Create your .env file
```bash
cp .env.example .env
```
Open `.env` and paste in your **App Key** and **Secret** from the Schwab developer portal.

### 3. Run the one-time OAuth login
```bash
python3 auth.py
```
- A browser window opens → log in to Schwab → approve the app
- Token is saved to `token.json` — **don't share or commit this file**
- You only need to do this once (tokens auto-refresh)

### 4. Start the dashboard
```bash
python3 app.py
```
Open **http://127.0.0.1:5050** in your browser.

---

## Daily use
Just run `python3 app.py` — no login needed.

## What's available
| URL | Description |
|-----|-------------|
| `http://127.0.0.1:5050` | Dashboard — positions + quotes |
| `/api/positions` | Raw JSON — all account positions |
| `/api/quotes` | Raw JSON — live quotes for all held tickers |
| `/api/quote/NVDA` | Raw JSON — single ticker quote |

## Files
| File | Purpose |
|------|---------|
| `auth.py` | OAuth setup (run once) |
| `app.py` | Flask web app |
| `config.py` | Loads credentials from .env |
| `.env` | Your API credentials (never commit) |
| `token.json` | OAuth token (auto-created, never commit) |
