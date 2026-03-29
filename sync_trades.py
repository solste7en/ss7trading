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

MARKET_OPEN  = 9    # 9:00 AM ET  (market opens 9:30, but 9 AM gives a small buffer)
MARKET_CLOSE = 17   # 5:00 PM ET  (covers through market close at 4 PM + buffer)

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
    """
    Parse option symbol in either format:
    - Schwab CSV:  'NVDA 03/27/2026 177.50 P'
    - Schwab API (OCC 21-char): 'NVDA  260327P00177500'
    Returns option fields dict, or None if not an option.
    """
    s = symbol.strip()

    # CSV format: 'NVDA 03/27/2026 177.50 P'
    m = re.match(r"^(\S+)\s+(\d{2}/\d{2}/\d{4})\s+([\d.]+)\s+([CP])$", s)
    if m:
        return {
            "underlying":    m.group(1),
            "option_expiry": datetime.strptime(m.group(2), "%m/%d/%Y").strftime("%Y-%m-%d"),
            "option_strike": float(m.group(3)),
            "option_type":   "CALL" if m.group(4) == "C" else "PUT",
        }

    # OCC API format: 'NVDA  260327C00190000'
    # underlying (1-6 chars, space-padded) + YYMMDD + C/P + 8-digit strike*1000
    m = re.match(r"^([A-Z/]+)\s+(\d{6})([CP])(\d{8})$", s)
    if m:
        strike = int(m.group(4)) / 1000.0
        try:
            expiry = datetime.strptime("20" + m.group(2), "%Y%m%d").strftime("%Y-%m-%d")
        except ValueError:
            return None
        return {
            "underlying":    m.group(1),
            "option_expiry": expiry,
            "option_strike": strike,
            "option_type":   "CALL" if m.group(3) == "C" else "PUT",
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
    # Use execution time as trade_date — tradeDate in the API is T+1 settlement,
    # but the CSV "Date" column records the execution date (same as "time" field).
    tx_date  = tx.get("time") or tx.get("tradeDate") or tx.get("settlementDate") or ""
    if tx_date:
        # Schwab returns ISO8601 e.g. "2026-03-28T08:43:16+0000"
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

    # ── find the non-cash instrument leg in transferItems ─────────────────────
    transfer_items = tx.get("transferItems", [])
    trade_item = None
    for ti in transfer_items:
        asset_type = ti.get("instrument", {}).get("assetType", "")
        if asset_type not in ("CURRENCY", ""):
            trade_item = ti
            break
    # fallback: first item if all are currency (e.g. cash-only events)
    if trade_item is None and transfer_items:
        trade_item = transfer_items[0]

    instrument = (trade_item or {}).get("instrument", {}) if trade_item else {}
    asset_type = instrument.get("assetType", "")
    # If only a currency leg exists, treat as a symbol-less cash event
    is_currency_only = (asset_type == "CURRENCY")
    symbol     = "" if is_currency_only else instrument.get("symbol", "")
    desc       = "" if is_currency_only else (instrument.get("description", "") or symbol)

    # qty: positive = bought, negative = sold
    qty        = (trade_item or {}).get("amount") if trade_item else None
    price      = (trade_item or {}).get("price") if trade_item else None
    # positionEffect lives on the transferItem (OPENING / CLOSING)
    pos_effect = (trade_item or {}).get("positionEffect", "") if trade_item else ""
    fees_obj   = tx.get("fees", {}) or {}
    fees       = fees_obj.get("commission") or fees_obj.get("optRegFee")
    net_amount = tx.get("netAmount")

    # For TRADE types, derive action from asset type + positionEffect + sign of qty
    if tx_type == "TRADE":
        is_buy = (qty is not None and qty > 0)
        if asset_type == "OPTION":
            if is_buy:
                action = "Buy to Open" if pos_effect == "OPENING" else "Buy to Close"
            else:
                action = "Sell to Open" if pos_effect == "OPENING" else "Sell to Close"
        elif asset_type == "EQUITY":
            sub_account = tx.get("subAccount", "")
            if is_buy:
                action = "Buy"
            elif sub_account == "SHORT":
                action = "Sell Short"
            else:
                action = "Sell"
        else:
            action = "Buy" if is_buy else "Sell"

    # For RECEIVE_AND_DELIVER: detect expired options (option instrument + $0 net)
    if tx_type == "RECEIVE_AND_DELIVER" and asset_type == "OPTION":
        if net_amount == 0 or net_amount is None:
            action = "Expired"
        # non-zero RECEIVE_AND_DELIVER options are assignments or exercises
        # keep "Journaled Shares" for equity transfers

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

    opt = parse_option_symbol(symbol)
    if opt:
        # Normalize symbol to CSV format so DB is consistent regardless of source
        # e.g. OCC 'LUV   260327P00037000' → 'LUV 03/27/2026 37.00 P'
        exp_fmt   = datetime.strptime(opt["option_expiry"], "%Y-%m-%d").strftime("%m/%d/%Y")
        type_char = "C" if opt["option_type"] == "CALL" else "P"
        symbol    = f"{opt['underlying']} {exp_fmt} {opt['option_strike']:.2f} {type_char}"
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
    )
    resp.raise_for_status()
    raw_txs = resp.json()
    log.info("Fetched %d raw transactions from Schwab API", len(raw_txs))

    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()

    # Build existing dedup structures (last lookback_days + buffer)
    buffer_date = (datetime.now() - timedelta(days=lookback_days + 3)).strftime("%Y-%m-%d")
    cur.execute("""
        SELECT trade_date, action, symbol, amount
        FROM transactions WHERE trade_date >= ?
    """, (buffer_date,))
    existing_rows = cur.fetchall()
    existing_exact = set(existing_rows)  # exact (date, action, symbol, amount) matches

    # Fuzzy match index: (action, symbol) → list of (date, amount) for ±2-day / ~same-amount checks
    from collections import defaultdict
    fuzzy_index = defaultdict(list)
    for d, a, s, amt in existing_rows:
        fuzzy_index[(a, s)].append((d, amt))

    # Sell / Sell Short aliases — the API and CSV sometimes disagree on which to use
    SELL_ALIASES = {"Sell", "Sell Short"}

    def is_duplicate(row: dict) -> bool:
        """Return True if this row is already represented in the DB."""
        key = dedup_key(row)
        if key in existing_exact:
            return True
        # Fuzzy check: same action+symbol, date within ±2 days, amount within 2%
        # Also check Sell ↔ Sell Short aliases since API/CSV may disagree
        action_keys = [(row["action"], row["symbol"])]
        if row["action"] in SELL_ALIASES:
            for alt in SELL_ALIASES - {row["action"]}:
                action_keys.append((alt, row["symbol"]))
        candidates = []
        for ak in action_keys:
            candidates.extend(fuzzy_index.get(ak, []))
        if not candidates:
            return False
        try:
            row_dt = datetime.strptime(row["trade_date"], "%Y-%m-%d")
            row_amt = float(row["amount"]) if row["amount"] is not None else None
        except (ValueError, TypeError):
            return False
        for cand_date, cand_amt in candidates:
            try:
                cand_dt = datetime.strptime(cand_date, "%Y-%m-%d")
                day_diff = abs((row_dt - cand_dt).days)
                if day_diff > 2:
                    continue
                # Amount check: exact match OR within 2% (for partial-fill aggregation)
                if row_amt is None or cand_amt is None:
                    if day_diff == 0:
                        return True
                    continue
                if abs(row_amt) < 0.01:  # zero-amount events (expirations)
                    if abs(float(cand_amt)) < 0.01 and day_diff <= 2:
                        return True
                    continue
                pct_diff = abs(row_amt - float(cand_amt)) / max(abs(row_amt), 0.01)
                if pct_diff < 0.02:
                    return True
            except (ValueError, TypeError):
                continue
        return False

    inserted = skipped = errors = 0
    for raw in raw_txs:
        try:
            row = parse_schwab_transaction(raw)
            if row is None:
                skipped += 1
                continue
            if is_duplicate(row):
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
            existing_exact.add(dedup_key(row))
            fuzzy_index[(row["action"], row["symbol"])].append((row["trade_date"], row["amount"]))
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

def dry_run(lookback_days: int = 7):
    """
    Fetch transactions from Schwab and show what WOULD be inserted,
    without writing anything to the database. Good for testing.
    """
    log.info("=== DRY RUN (lookback=%d days) — nothing will be written ===", lookback_days)

    client   = get_client()
    resp     = client.get_account_numbers()
    resp.raise_for_status()
    accounts  = resp.json()
    acct_hash = accounts[0]["hashValue"]

    end_dt   = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=lookback_days)

    resp = client.get_transactions(
        account_hash=acct_hash,
        start_date=start_dt,
        end_date=end_dt,
    )
    resp.raise_for_status()
    raw_txs = resp.json()
    log.info("Fetched %d raw transactions from Schwab", len(raw_txs))

    # Print raw structure of first TRADE transaction for debugging
    import json
    trade_sample = next((t for t in raw_txs if t.get("type") == "TRADE"), raw_txs[0] if raw_txs else None)
    if trade_sample:
        log.info("--- Sample TRADE transaction (raw) ---")
        log.info(json.dumps(trade_sample, indent=2, default=str))
        log.info("--------------------------------------")

    # Load existing dedup structures from DB
    conn        = sqlite3.connect(DB_PATH)
    cur         = conn.cursor()
    buffer_date = (datetime.now() - timedelta(days=lookback_days + 3)).strftime("%Y-%m-%d")
    cur.execute("SELECT trade_date, action, symbol, amount FROM transactions WHERE trade_date >= ?",
                (buffer_date,))
    existing_rows = cur.fetchall()
    conn.close()
    existing_exact = set(existing_rows)

    from collections import defaultdict
    fuzzy_index = defaultdict(list)
    for d, a, s, amt in existing_rows:
        fuzzy_index[(a, s)].append((d, amt))

    DR_SELL_ALIASES = {"Sell", "Sell Short"}

    def dr_is_duplicate(row: dict) -> bool:
        if dedup_key(row) in existing_exact:
            return True
        action_keys = [(row["action"], row["symbol"])]
        if row["action"] in DR_SELL_ALIASES:
            for alt in DR_SELL_ALIASES - {row["action"]}:
                action_keys.append((alt, row["symbol"]))
        candidates = []
        for ak in action_keys:
            candidates.extend(fuzzy_index.get(ak, []))
        if not candidates:
            return False
        try:
            row_dt  = datetime.strptime(row["trade_date"], "%Y-%m-%d")
            row_amt = float(row["amount"]) if row["amount"] is not None else None
        except (ValueError, TypeError):
            return False
        for cand_date, cand_amt in candidates:
            try:
                cand_dt  = datetime.strptime(cand_date, "%Y-%m-%d")
                day_diff = abs((row_dt - cand_dt).days)
                if day_diff > 2:
                    continue
                if row_amt is None or cand_amt is None:
                    if day_diff == 0:
                        return True
                    continue
                if abs(row_amt) < 0.01:
                    if abs(float(cand_amt)) < 0.01 and day_diff <= 2:
                        return True
                    continue
                pct_diff = abs(row_amt - float(cand_amt)) / max(abs(row_amt), 0.01)
                if pct_diff < 0.02:
                    return True
            except (ValueError, TypeError):
                continue
        return False

    new_rows = already_in_db = parse_errors = 0
    for raw in raw_txs:
        try:
            row = parse_schwab_transaction(raw)
            if row is None:
                continue
            if dr_is_duplicate(row):
                already_in_db += 1
                log.info("  = EXISTING  %s | %s | %s | $%s",
                         row["trade_date"], row["action"], row["symbol"], row["amount"])
            else:
                new_rows += 1
                log.info("  + NEW       %s | %s | %s | qty=%s | $%s",
                         row["trade_date"], row["action"], row["symbol"],
                         row["quantity"], row["amount"])
        except Exception as e:
            parse_errors += 1
            log.error("  ! PARSE ERR %s | raw=%s", e, raw)

    log.info("=== DRY RUN SUMMARY: %d new | %d already in DB | %d errors ===",
             new_rows, already_in_db, parse_errors)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force",   action="store_true",
                        help="Run regardless of market hours")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be inserted without writing to DB")
    parser.add_argument("--days",    type=int, default=2,
                        help="How many days back to look (default: 2)")
    args = parser.parse_args()

    if args.dry_run:
        dry_run(lookback_days=args.days)
        sys.exit(0)

    if not should_run(args.force):
        now = datetime.now(ET)
        log.info("Outside market window (%s %s) — skipping. Use --force to override.",
                 now.strftime("%A"), now.strftime("%H:%M ET"))
        sys.exit(0)

    sync(lookback_days=args.days)
