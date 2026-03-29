"""
sync_trades.py — Schwab API → trades.db sync
---------------------------------------------
Pulls new transactions from the Schwab API and inserts them into trades.db,
deduplicating against what's already there.

Designed to be called by cron. The script decides internally whether to run
based on market hours / day of week, so cron can fire it frequently without waste.

Cron schedule (add via: crontab -e):
  # Every 10 min on weekdays 9:00 AM–5:00 PM ET
  */10 9-16 * * 1-5  cd /path/to/schwab_app && python3.11 sync_trades.py
  # Once daily on weekends at 9:00 AM ET (catches assignments/exercises)
  0 9 * * 0,6        cd /path/to/schwab_app && python3.11 sync_trades.py --force

Usage:
  python3.11 sync_trades.py            # respects market-hours window
  python3.11 sync_trades.py --force    # runs regardless of time (useful for weekends/testing)
  python3.11 sync_trades.py --days 7   # look back N days (default: 2)
"""

import sys
import re
import sqlite3
import logging
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from auth import get_client
import schwab

# ── config ────────────────────────────────────────────────────────────────────

BASE_DIR  = Path(__file__).parent
DB_PATH   = BASE_DIR.parent / "trades.db"      # sibling of schwab_app/
LOG_PATH  = BASE_DIR / "sync.log"
ET        = ZoneInfo("America/New_York")

MARKET_OPEN  = 9    # 9:00 AM ET (start checking slightly before open)
MARKET_CLOSE = 17   # 5:00 PM ET (catch late settlement)

# ── logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ── market hours gate ─────────────────────────────────────────────────────────

def should_run(force: bool) -> bool:
    if force:
        return True
    now = datetime.now(ET)
    weekday = now.weekday()          # 0=Mon, 6=Sun
    hour    = now.hour
    if weekday < 5:                  # Mon–Fri
        return MARKET_OPEN <= hour < MARKET_CLOSE
    else:                            # Sat–Sun: only if --force
        return False

# ── helpers ───────────────────────────────────────────────────────────────────

def clean_money(val):
    if val is None: return None
    try: return float(str(val).replace("$", "").replace(",", ""))
    except: return None

def parse_option_symbol(symbol: str):
    """Parse 'NVDA 03/27/2026 177.50 P' → option fields dict, or None."""
    m = re.match(r"^(\S+)\s+(\d{2}/\d{2}/\d{4})\s+([\d.]+)\s+([CP])$", symbol.strip())
    if m:
        return {
            "underlying":    m.group(1),
            "option_expiry": datetime.strptime(m.group(2), "%m/%d/%Y").strftime("%Y-%m-%d"),
            "option_strike": float(m.group(3)),
            "option_type":   "CALL" if m.group(4) == "C" else "PUT",
        }
    return None

OPTION_ACTIONS   = {"Buy to Open","Sell to Open","Buy to Close","Sell to Close",
                    "Expired","Assigned","Exchange or Exercise"}
EQUITY_ACTIONS   = {"Buy","Sell","Sell Short","Stock Split","Reverse Split",
                    "Security Transfer","Journaled Shares"}
INCOME_ACTIONS   = {"Cash Dividend","Qualified Dividend","Special Qual Div",
                    "Rev Pr Yr Cash Div","Bank Interest","Credit Interest",
                    "Cash In Lieu","ADR Mgmt Fee","Foreign Tax Paid","Margin Interest"}
TRANSFER_ACTIONS = {"MoneyLink Transfer","Journal","Other"}

def classify_action(action: str) -> str:
    if action in OPTION_ACTIONS:   return "option"
    if action in EQUITY_ACTIONS:   return "equity"
    if action in INCOME_ACTIONS:   return "income"
    if action in TRANSFER_ACTIONS: return "transfer"
    return "other"

# ── Schwab API → normalised row ───────────────────────────────────────────────

def parse_schwab_transaction(tx: dict) -> dict | None:
    """
    Convert a raw Schwab transaction dict (from get_transactions)
    into the shape expected by our transactions table.
    Returns None if the transaction should be skipped.
    """
    tx_type  = tx.get("type", "")
    tx_date  = tx.get("tradeDate") or tx.get("settlementDate") or ""
    if tx_date:
        # Schwab returns ISO8601 e.g. "2026-03-27T00:00:00+0000"
        tx_date = tx_date[:10]

    # Map Schwab API type → Schwab CSV action names we already use
    TYPE_MAP = {
        "TRADE":             None,   # resolved per instrument below
        "RECEIVE_AND_DELIVER": "Journaled Shares",
        "DIVIDEND_OR_INTEREST": None,
        "ACH_RECEIPT":       "MoneyLink Transfer",
        "ACH_DISBURSEMENT":  "MoneyLink Transfer",
        "CASH_RECEIPT":      "Journal",
        "CASH_DISBURSEMENT": "Journal",
        "ELECTRONIC_FUND":   "MoneyLink Transfer",
        "WIRE_OUT":          "MoneyLink Transfer",
        "WIRE_IN":           "MoneyLink Transfer",
        "JOURNAL":           "Journal",
        "MEMORANDUM":        "Other",
        "MARGIN_CALL":       "Other",
        "CORRECTION":        "Other",
        "SMA_ADJUSTMENT":    "Other",
    }

    action = TYPE_MAP.get(tx_type)

    # For TRADE types, derive action from instruction + positionEffect
    if tx_type == "TRADE":
        instr  = tx.get("orderLegCollection", [{}])[0].get("instruction", "")
        effect = tx.get("orderLegCollection", [{}])[0].get("positionEffect", "")
        asset  = (tx.get("transactionItem", {})
                    .get("instrument", {})
                    .get("assetType", ""))

        if asset == "OPTION":
            mapping = {
                ("BUY",  "OPENING"):  "Buy to Open",
                ("BUY",  "CLOSING"):  "Buy to Close",
                ("SELL", "OPENING"):  "Sell to Open",
                ("SELL", "CLOSING"):  "Sell to Close",
            }
            action = mapping.get((instr, effect), tx_type)
        elif asset == "EQUITY":
            if instr == "BUY":
                action = "Buy"
            elif instr == "SELL_SHORT":
                action = "Sell Short"
            else:
                action = "Sell"

    # For dividend/interest, get sub-type
    if tx_type == "DIVIDEND_OR_INTEREST":
        sub = tx.get("description", "").lower()
        if "qualified" in sub:
            action = "Qualified Dividend"
        elif "ordinary" in sub or "dividend" in sub:
            action = "Cash Dividend"
        elif "interest" in sub:
            action = "Bank Interest"
        else:
            action = "Cash Dividend"

    if not action:
        log.debug("Skipping unmapped transaction type: %s", tx_type)
        return None

    item       = tx.get("transactionItem", {})
    instrument = item.get("instrument", {})
    symbol     = instrument.get("symbol", "")
    desc       = instrument.get("description", symbol)
    qty        = item.get("amount")          # shares/contracts
    price      = item.get("price")
    fees       = tx.get("fees", {}).get("commission") or tx.get("fees", {}).get("optRegFee")
    net_amount = tx.get("netAmount")

    opt = parse_option_symbol(symbol)
    if opt:
        underlying = opt["underlying"]
        is_option  = 1
        opt_type   = opt["option_type"]
        opt_strike = opt["option_strike"]
        opt_expiry = opt["option_expiry"]
    else:
        underlying = symbol or None
        is_option  = 0
        opt_type   = opt_strike = opt_expiry = None

    return {
        "trade_date":    tx_date,
        "action":        action,
        "category":      classify_action(action),
        "symbol":        symbol,
        "underlying":    underlying,
        "description":   desc,
        "quantity":      clean_money(qty),
        "price":         clean_money(price),
        "fees":          clean_money(fees),
        "amount":        clean_money(net_amount),
        "is_option":     is_option,
        "option_type":   opt_type,
        "option_strike": opt_strike,
        "option_expiry": opt_expiry,
    }

# ── dedup key (same logic as import_schwab_transactions.py) ──────────────────

def dedup_key(row: dict) -> tuple:
    return (row["trade_date"], row["action"], row["symbol"], row["amount"])

# ── assignment tagger (mirrors historical import logic) ───────────────────────

def tag_assignments(conn: sqlite3.Connection):
    cur = conn.cursor()
    cur.execute("""
        SELECT id, trade_date, underlying, option_strike, quantity, action
        FROM transactions
        WHERE action IN ('Assigned','Exchange or Exercise')
          AND option_strike IS NOT NULL
          AND trade_date >= date('now', '-7 days')
    """)
    for opt_id, date, underlying, strike, opt_qty, opt_action in cur.fetchall():
        abs_qty = abs(opt_qty) if opt_qty else None
        cur.execute("""
            SELECT id FROM transactions
            WHERE trade_date = ?
              AND underlying  = ?
              AND category    = 'equity'
              AND action IN ('Buy','Sell','Sell Short')
              AND ABS(price - ?) < 0.02
              AND ABS(quantity) = ABS(?) * 100
              AND (is_from_option_event IS NULL OR is_from_option_event = 0)
            LIMIT 1
        """, (date, underlying, strike, abs_qty))
        row = cur.fetchone()
        if row:
            cur.execute("""
                UPDATE transactions
                SET is_from_option_event = 1,
                    linked_option_id     = ?,
                    linked_option_action = ?
                WHERE id = ?
            """, (opt_id, opt_action, row[0]))
    conn.commit()

# ── main sync ─────────────────────────────────────────────────────────────────

def sync(lookback_days: int = 2):
    log.info("=== sync_trades.py starting (lookback=%d days) ===", lookback_days)

    client = get_client()

    # Get account hash (required for transaction endpoints)
    resp = client.get_account_numbers()
    resp.raise_for_status()
    accounts   = resp.json()
    acct_hash  = accounts[0]["hashValue"]
    log.info("Account: ...%s", accounts[0].get("accountNumber", "")[-4:])

    # Date window
    end_dt   = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=lookback_days)

    resp = client.get_transactions(
        account_hash=acct_hash,
        start_date=start_dt,
        end_date=end_dt,
        types=schwab.client.Client.Transaction.TransactionType.ALL,
    )
    resp.raise_for_status()
    raw_txs = resp.json()
    log.info("Fetched %d raw transactions from Schwab API", len(raw_txs))

    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()

    # Build existing dedup set (last lookback_days + buffer)
    buffer_date = (datetime.now() - timedelta(days=lookback_days + 1)).strftime("%Y-%m-%d")
    cur.execute("""
        SELECT trade_date, action, symbol, amount
        FROM transactions WHERE trade_date >= ?
    """, (buffer_date,))
    existing = set(cur.fetchall())

    inserted = skipped = errors = 0
    for raw in raw_txs:
        try:
            row = parse_schwab_transaction(raw)
            if row is None:
                skipped += 1
                continue
            key = dedup_key(row)
            if key in existing:
                skipped += 1
                continue
            cur.execute("""
                INSERT INTO transactions
                    (trade_date, action, category, symbol, underlying, description,
                     quantity, price, fees, amount,
                     is_option, option_type, option_strike, option_expiry)
                VALUES (:trade_date,:action,:category,:symbol,:underlying,:description,
                        :quantity,:price,:fees,:amount,
                        :is_option,:option_type,:option_strike,:option_expiry)
            """, row)
            existing.add(key)
            inserted += 1
            log.info("  + %s | %s | %s | qty=%s | $%s",
                     row["trade_date"], row["action"], row["symbol"],
                     row["quantity"], row["amount"])
        except Exception as e:
            log.error("  Error parsing transaction: %s | raw: %s", e, raw)
            errors += 1

    conn.commit()

    # Re-tag any new assignments/exercises
    if inserted > 0:
        tag_assignments(conn)
        log.info("Assignment tagging complete.")

    conn.close()
    log.info("Done — inserted: %d | skipped: %d | errors: %d", inserted, skipped, errors)
    return inserted, skipped, errors


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true",
                        help="Run regardless of market hours")
    parser.add_argument("--days", type=int, default=2,
                        help="How many days back to look (default: 2)")
    args = parser.parse_args()

    if not should_run(args.force):
        now = datetime.now(ET)
        log.info("Outside market window (%s %s) — skipping. Use --force to override.",
                 now.strftime("%A"), now.strftime("%H:%M ET"))
        sys.exit(0)

    sync(lookback_days=args.days)
