"""
sync_trades.py — Schwab API → trades.db sync
---------------------------------------------
Pulls new transactions from the Schwab API and inserts them into trades.db,
deduplicating against what's already there.

Designed to be called by cron. The script decides internally whether to run
based on market hours / day of week, so cron can fire it frequently without waste.

Cron schedule (add via: crontab -e):
  # Every 10 min on weekdays 9:00 AM–5:00 PM ET
  */10 9-16 * * 1-5  cd /path/to/schwab_app && python3 sync_trades.py
  # Once daily on weekends at 9:00 AM ET (catches assignments/exercises)
  0 9 * * 0,6        cd /path/to/schwab_app && python3 sync_trades.py --force

Usage:
  python3 sync_trades.py            # respects market-hours window
  python3 sync_trades.py --force    # runs regardless of time (useful for weekends/testing)
  python3 sync_trades.py --days 7   # look back N days (default: 2)
  python3 sync_trades.py --backfill # enrich existing DB rows that have empty descriptions
"""

import sys
import re
import sqlite3
import logging
import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from auth import get_client
from authlib.integrations.base_client.errors import OAuthError
from migrate_db import get_pending_migrations
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
    if val is None:
        return None
    try:
        return float(str(val).replace("$", "").replace(",", ""))
    except (ValueError, TypeError):
        return None

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
TRANSFER_ACTIONS = {"MoneyLink Transfer","Journal","Other","SMA Adjustment"}

def classify_action(action: str) -> str:
    if action in OPTION_ACTIONS:   return "option"
    if action in EQUITY_ACTIONS:   return "equity"
    if action in INCOME_ACTIONS:   return "income"
    if action in TRANSFER_ACTIONS: return "transfer"
    return "other"

# ── Company name → ticker resolution ─────────────────────────────────────────

# Static map for common names the Schwab API returns for dividends/journals.
# The API description field uses full company names without ticker symbols.
COMPANY_TICKER_MAP = {
    "NVIDIA CORP":                      "NVDA",
    "APPLE INC":                        "AAPL",
    "MICROSOFT CORP":                   "MSFT",
    "ALPHABET INC CLASS CLASS A":       "GOOGL",
    "ALPHABET INC CL A":               "GOOGL",
    "META PLATFORMS INC CLASS CLASS A": "META",
    "META PLATFORMS INC CL A":         "META",
    "AMAZON COM INC":                   "AMZN",
    "AMAZON.COM INC":                   "AMZN",
    "TESLA INC":                        "TSLA",
    "PROSHARES ULTRAPRO QQQ":           "TQQQ",
    "PROSHARES ULTRAPRO QQQ ETF":       "TQQQ",
    "DISNEY WALT CO":                   "DIS",
    "WALT DISNEY CO":                   "DIS",
    "SOUTHWEST AIRLS CO":               "LUV",
    "SOUTHWEST AIRLINES CO":            "LUV",
    "NIO INC F":                        "NIO",
    "NIO INC":                          "NIO",
    "STATE STREET SPDR S&P 500 TRUST ETF": "SPY",
    "SPDR S&P 500 ETF TRUST":          "SPY",
    "NIKOLA CORP":                      "NKLA",
}

def resolve_ticker_from_description(desc: str, db_conn: sqlite3.Connection | None = None) -> str | None:
    """
    Try to resolve a ticker symbol from a company description string.
    First checks the static map, then falls back to DB lookup if a connection is provided.
    """
    if not desc:
        return None

    desc_upper = desc.strip().upper()

    # Strip common suffixes for matching
    for suffix in (" XXXBANKRUPTCY", " XXXDELISTED", " XXXMERGER"):
        desc_upper = desc_upper.split(suffix)[0]

    if desc_upper in COMPANY_TICKER_MAP:
        return COMPANY_TICKER_MAP[desc_upper]

    # Fuzzy: try prefix matching (API sometimes appends extra qualifiers)
    for company, ticker in COMPANY_TICKER_MAP.items():
        if desc_upper.startswith(company) or company.startswith(desc_upper):
            return ticker

    # DB fallback: find the most recent trade whose description matches
    if db_conn:
        try:
            cur = db_conn.cursor()
            cur.execute("""
                SELECT underlying FROM transactions
                WHERE description = ? AND underlying IS NOT NULL AND underlying != ''
                ORDER BY trade_date DESC LIMIT 1
            """, (desc,))
            row = cur.fetchone()
            if row:
                return row[0]
            # Try prefix match in DB
            cur.execute("""
                SELECT underlying FROM transactions
                WHERE description LIKE ? AND underlying IS NOT NULL AND underlying != ''
                ORDER BY trade_date DESC LIMIT 1
            """, (desc + "%",))
            row = cur.fetchone()
            if row:
                return row[0]
        except Exception:
            pass

    return None

# ── Fee extraction ────────────────────────────────────────────────────────────

def extract_fees(tx: dict) -> float | None:
    """
    Sum all fee items from transferItems.
    The Schwab API stores fees as separate CURRENCY transferItems with a feeType field.
    """
    total = 0.0
    found_any = False
    for ti in tx.get("transferItems", []):
        if ti.get("feeType"):
            amt = ti.get("cost") or ti.get("amount") or 0.0
            if amt:
                total += abs(float(amt))
                found_any = True
    return total if found_any else None

# ── Schwab API → normalised row ───────────────────────────────────────────────

def parse_schwab_transaction(tx: dict) -> dict | None:
    """
    Convert a raw Schwab transaction dict (from get_transactions)
    into the shape expected by our transactions table.
    Returns None if the transaction should be skipped.
    """
    tx_type     = tx.get("type", "")
    tx_desc     = tx.get("description", "")
    activity_id = tx.get("activityId") or tx.get("transactionId")

    # Use execution time as trade_date — tradeDate in the API is T+1 settlement,
    # but the CSV "Date" column records the execution date (same as "time" field).
    tx_date  = tx.get("time") or tx.get("tradeDate") or tx.get("settlementDate") or ""
    if tx_date:
        tx_date = tx_date[:10]

    # Map Schwab API type → Schwab CSV action names we already use
    TYPE_MAP = {
        "TRADE":              None,   # resolved per instrument below
        "RECEIVE_AND_DELIVER": None,  # resolved below (expired, assigned, journaled)
        "DIVIDEND_OR_INTEREST": None, # resolved below (qualified, ordinary, interest)
        "ACH_RECEIPT":        "MoneyLink Transfer",
        "ACH_DISBURSEMENT":   "MoneyLink Transfer",
        "CASH_RECEIPT":       "MoneyLink Transfer",
        "CASH_DISBURSEMENT":  "MoneyLink Transfer",
        "ELECTRONIC_FUND":    "MoneyLink Transfer",
        "WIRE_OUT":           "MoneyLink Transfer",
        "WIRE_IN":            "MoneyLink Transfer",
        "JOURNAL":            None,   # resolved below (ADR fee, foreign tax, generic journal)
        "SMA_ADJUSTMENT":     "SMA Adjustment",
        "MEMORANDUM":         "Other",
        "MARGIN_CALL":        "Other",
        "CORRECTION":         "Other",
    }

    action = TYPE_MAP.get(tx_type)

    # Skip SMA_ADJUSTMENT entirely — internal margin bookkeeping, not real cash flow
    if tx_type == "SMA_ADJUSTMENT":
        log.debug("Skipping SMA_ADJUSTMENT: %s (net=%s)", tx_desc, tx.get("netAmount"))
        return None

    # ── find the non-cash instrument leg in transferItems ─────────────────────
    transfer_items = tx.get("transferItems", [])
    trade_item = None
    for ti in transfer_items:
        asset_type = ti.get("instrument", {}).get("assetType", "")
        if asset_type not in ("CURRENCY", "") and not ti.get("feeType"):
            trade_item = ti
            break
    # fallback: first non-fee item if all are currency (e.g. cash-only events)
    if trade_item is None:
        for ti in transfer_items:
            if not ti.get("feeType"):
                trade_item = ti
                break

    instrument = (trade_item or {}).get("instrument", {}) if trade_item else {}
    asset_type = instrument.get("assetType", "")
    is_currency_only = (asset_type == "CURRENCY" or asset_type == "")
    symbol     = "" if is_currency_only else instrument.get("symbol", "")
    desc       = "" if is_currency_only else (instrument.get("description", "") or symbol)

    qty        = (trade_item or {}).get("amount") if trade_item else None
    price      = (trade_item or {}).get("price") if trade_item else None
    pos_effect = (trade_item or {}).get("positionEffect", "") if trade_item else ""
    fees       = extract_fees(tx)
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

    # For RECEIVE_AND_DELIVER: detect expired/assigned options vs equity transfers
    if tx_type == "RECEIVE_AND_DELIVER":
        if asset_type == "OPTION":
            if net_amount == 0 or net_amount is None:
                action = "Expired"
            else:
                # Non-zero R&D on options → assignment or exercise
                action = "Assigned"
                if "exercise" in tx_desc.lower():
                    action = "Exchange or Exercise"
        else:
            action = "Journaled Shares"
            if "security transfer" in tx_desc.lower():
                action = "Security Transfer"

    # For dividend/interest, detect sub-type from API description and qualifiedDividend flag
    if tx_type == "DIVIDEND_OR_INTEREST":
        sub = tx_desc.lower()
        is_qualified = tx.get("qualifiedDividend", False)
        if "interest" in sub:
            action = "Credit Interest"
        elif "cash in lieu" in sub:
            action = "Cash In Lieu"
        elif "margin interest" in sub:
            action = "Margin Interest"
        elif "foreign tax" in sub:
            action = "Foreign Tax Paid"
        elif is_qualified:
            action = "Qualified Dividend"
        elif "qualified" in sub:
            action = "Qualified Dividend"
        elif "special" in sub:
            action = "Special Qual Div"
        elif "rev pr yr" in sub or "prior year" in sub:
            action = "Rev Pr Yr Cash Div"
        else:
            action = "Cash Dividend"
        # Use top-level description (company name) since transferItems only has CURRENCY
        if is_currency_only and tx_desc:
            desc = tx_desc

    # For JOURNAL: detect ADR fees, foreign tax, etc. from description
    if tx_type == "JOURNAL":
        sub = tx_desc.lower()
        if "adr" in sub or "mgmt fee" in sub:
            action = "ADR Mgmt Fee"
        elif "foreign tax" in sub:
            action = "Foreign Tax Paid"
        elif "margin interest" in sub:
            action = "Margin Interest"
        elif net_amount is not None and net_amount < 0 and abs(net_amount) < 50:
            # Small negative JOURNAL with a company-name description → likely ADR fee
            # (the API doesn't label these explicitly; it just gives the company name)
            desc_up = tx_desc.upper()
            has_company_keyword = any(
                kw in desc_up for kw in (" INC", " CORP", " ETF", " CO ", " LTD")
            ) or desc_up.endswith(" F")  # trailing F = foreign/ADR marker
            looks_like_company = (
                bool(tx_desc)
                and not any(kw in sub for kw in ("tfr ", "transfer", "journal", "wire"))
                and has_company_keyword
            )
            action = "ADR Mgmt Fee" if looks_like_company else "Journal"
        else:
            action = "Journal"
        if is_currency_only and tx_desc:
            desc = tx_desc

    # For cash transfers, use the API description (contains bank name, account holder)
    if tx_type in ("CASH_DISBURSEMENT", "CASH_RECEIPT", "ACH_RECEIPT",
                    "ACH_DISBURSEMENT", "ELECTRONIC_FUND", "WIRE_IN", "WIRE_OUT"):
        if is_currency_only and tx_desc:
            desc = tx_desc

    # For "Other" / "MEMORANDUM" / "CORRECTION", store description for context
    if tx_type in ("MEMORANDUM", "CORRECTION", "MARGIN_CALL"):
        if is_currency_only and tx_desc:
            desc = tx_desc

    if not action:
        log.debug("Skipping unmapped transaction type: %s", tx_type)
        return None

    opt = parse_option_symbol(symbol)
    if opt:
        # Normalize symbol to CSV format so DB is consistent regardless of source
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

    # For RECEIVE_AND_DELIVER with rich descriptions, preserve the API description
    if tx_type == "RECEIVE_AND_DELIVER" and tx_desc and not desc:
        desc = tx_desc

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
        "activity_id":   int(activity_id) if activity_id is not None else None,
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

# ── shared dedup logic ────────────────────────────────────────────────────────

SELL_ALIASES = {"Sell", "Sell Short"}

def build_dedup_structures(cur: sqlite3.Cursor, buffer_date: str):
    """Load existing rows from DB and build exact + fuzzy dedup indexes."""
    cur.execute("""
        SELECT trade_date, action, symbol, amount, activity_id
        FROM transactions WHERE trade_date >= ?
    """, (buffer_date,))
    existing_rows = cur.fetchall()
    exact_set = set((d, a, s, amt) for d, a, s, amt, _ in existing_rows)
    activity_id_set = set(aid for _, _, _, _, aid in existing_rows if aid is not None)
    fuzzy_idx = defaultdict(list)
    for d, a, s, amt, _ in existing_rows:
        fuzzy_idx[(a, s)].append((d, amt))
    return exact_set, activity_id_set, fuzzy_idx


def is_duplicate(row: dict, exact_set: set, activity_id_set: set, fuzzy_idx: dict) -> bool:
    """Return True if this row is already represented in the DB."""
    # Primary gate: activityId uniquely identifies a Schwab transaction.
    # If found in the set, it's a confirmed duplicate — return immediately.
    aid = row.get("activity_id")
    if aid is not None and aid in activity_id_set:
        return True

    key = dedup_key(row)
    if key in exact_set:
        return True

    # Fuzzy match ONLY for income/transfer — these can have minor rounding differences
    # between exports. Never fuzzy-match equity/option trades: same-day trades at
    # similar prices (e.g. multiple Sell Short lots of the same stock) are distinct.
    if row.get("category") not in ("income", "transfer"):
        return False

    action_keys = [(row["action"], row["symbol"])]
    if row["action"] in SELL_ALIASES:
        for alt in SELL_ALIASES - {row["action"]}:
            action_keys.append((alt, row["symbol"]))
    candidates = []
    for ak in action_keys:
        candidates.extend(fuzzy_idx.get(ak, []))
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


def record_in_dedup(row: dict, exact_set: set, activity_id_set: set, fuzzy_idx: dict):
    """Add a newly inserted row to the dedup structures so subsequent rows see it."""
    exact_set.add(dedup_key(row))
    if row.get("activity_id") is not None:
        activity_id_set.add(row["activity_id"])
    fuzzy_idx[(row["action"], row["symbol"])].append((row["trade_date"], row["amount"]))

# ── ticker resolution pass ────────────────────────────────────────────────────

def enrich_ticker(row: dict, db_conn: sqlite3.Connection | None = None):
    """
    For income/transfer rows that have a description but no symbol/underlying,
    try to resolve the ticker from the company name.
    """
    if row.get("underlying") or row.get("symbol"):
        return
    desc = row.get("description", "")
    if not desc:
        return
    ticker = resolve_ticker_from_description(desc, db_conn)
    if ticker:
        row["symbol"]     = ticker
        row["underlying"] = ticker

def schwab_get_account_numbers(client):
    """
    First API call; triggers OAuth refresh if the access token expired.
    Schwab often returns refresh_token_authentication_error when the refresh
    token is expired, revoked, or was issued for different app credentials.
    """
    try:
        return client.get_account_numbers()
    except OAuthError as e:
        log.error(
            "OAuth refresh failed (%s). Schwab rejected the refresh token — "
            "it may be expired, revoked, or not match SCHWAB_APP_KEY / SCHWAB_APP_SECRET.",
            e,
        )
        log.error(
            "Fix: run `python auth.py` in this directory to log in again and write a new token.json. "
            "Ensure .env keys match the same app in the Schwab Developer Portal as when the token was created."
        )
        sys.exit(1)


# ── schema guard ──────────────────────────────────────────────────────────────

def _assert_schema_current() -> None:
    """Exit with a clear message if trades.db has pending schema migrations."""
    pending = get_pending_migrations()
    if not pending:
        return
    log.error(
        "trades.db schema is out of date — %d pending migration(s):", len(pending)
    )
    for ver, desc in pending:
        log.error("  [%s] %s", ver, desc)
    log.error(
        "Run  python migrate_db.py  in the schwab_app directory to update the "
        "schema, then re-run this script."
    )
    sys.exit(1)


# ── main sync ─────────────────────────────────────────────────────────────────

def sync(lookback_days: int = 2):
    _assert_schema_current()
    log.info("=== sync_trades.py starting (lookback=%d days) ===", lookback_days)

    client = get_client()

    resp = schwab_get_account_numbers(client)
    resp.raise_for_status()
    accounts   = resp.json()
    acct_hash  = accounts[0]["hashValue"]
    log.info("Account: ...%s", accounts[0].get("accountNumber", "")[-4:])

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

    buffer_date = (datetime.now() - timedelta(days=lookback_days + 3)).strftime("%Y-%m-%d")
    exact_set, activity_id_set, fuzzy_idx = build_dedup_structures(cur, buffer_date)

    inserted = skipped = errors = 0
    for raw in raw_txs:
        try:
            row = parse_schwab_transaction(raw)
            if row is None:
                skipped += 1
                continue
            enrich_ticker(row, conn)
            if is_duplicate(row, exact_set, activity_id_set, fuzzy_idx):
                skipped += 1
                continue
            cur.execute("""
                INSERT INTO transactions
                    (trade_date, action, category, symbol, underlying, description,
                     quantity, price, fees, amount,
                     is_option, option_type, option_strike, option_expiry, activity_id)
                VALUES (:trade_date,:action,:category,:symbol,:underlying,:description,
                        :quantity,:price,:fees,:amount,
                        :is_option,:option_type,:option_strike,:option_expiry,:activity_id)
            """, row)
            record_in_dedup(row, exact_set, activity_id_set, fuzzy_idx)
            inserted += 1
            log.info("  + %s | %s | %s | qty=%s | $%s",
                     row["trade_date"], row["action"], row["symbol"],
                     row["quantity"], row["amount"])
        except Exception as e:
            log.error("  Error parsing transaction: %s | raw: %s", e, raw)
            errors += 1

    conn.commit()

    if inserted > 0:
        tag_assignments(conn)
        log.info("Assignment tagging complete.")

    conn.close()
    log.info("Done — inserted: %d | skipped: %d | errors: %d", inserted, skipped, errors)
    return inserted, skipped, errors


# ── dry run ───────────────────────────────────────────────────────────────────

def dry_run(lookback_days: int = 7):
    """
    Fetch transactions from Schwab and show what WOULD be inserted,
    without writing anything to the database. Good for testing.
    """
    _assert_schema_current()
    log.info("=== DRY RUN (lookback=%d days) — nothing will be written ===", lookback_days)

    client   = get_client()
    resp     = schwab_get_account_numbers(client)
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

    conn        = sqlite3.connect(DB_PATH)
    cur         = conn.cursor()
    buffer_date = (datetime.now() - timedelta(days=lookback_days + 3)).strftime("%Y-%m-%d")
    exact_set, activity_id_set, fuzzy_idx = build_dedup_structures(cur, buffer_date)

    new_rows = already_in_db = parse_errors = 0
    for raw in raw_txs:
        try:
            row = parse_schwab_transaction(raw)
            if row is None:
                continue
            enrich_ticker(row, conn)
            if is_duplicate(row, exact_set, activity_id_set, fuzzy_idx):
                already_in_db += 1
                log.info("  = EXISTING  %s | %s | %-8s | %s | $%s",
                         row["trade_date"], row["action"], row["category"],
                         row["symbol"] or row["description"][:30], row["amount"])
            else:
                new_rows += 1
                log.info("  + NEW       %s | %s | %-8s | %s | qty=%s | $%s",
                         row["trade_date"], row["action"], row["category"],
                         row["symbol"] or row["description"][:30],
                         row["quantity"], row["amount"])
        except Exception as e:
            parse_errors += 1
            log.error("  ! PARSE ERR %s | raw=%s", e, raw)

    conn.close()
    log.info("=== DRY RUN SUMMARY: %d new | %d already in DB | %d errors ===",
             new_rows, already_in_db, parse_errors)


# ── backfill ──────────────────────────────────────────────────────────────────

def backfill_descriptions():
    """
    Enrich existing DB rows that have empty description/symbol fields.
    Targets income and transfer rows where the API description wasn't captured.
    """
    _assert_schema_current()
    log.info("=== Backfilling empty descriptions ===")

    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()

    cur.execute("""
        SELECT id, action, category, description, symbol, underlying, amount
        FROM transactions
        WHERE category IN ('income', 'transfer')
          AND (description IS NULL OR description = '')
          AND (underlying IS NULL OR underlying = '')
    """)
    empty_rows = cur.fetchall()
    log.info("Found %d rows with empty descriptions", len(empty_rows))

    if not empty_rows:
        conn.close()
        return

    # Fetch fresh data from API to match against DB rows
    client   = get_client()
    resp     = schwab_get_account_numbers(client)
    resp.raise_for_status()
    acct_hash = resp.json()[0]["hashValue"]

    end_dt   = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=365)

    resp = client.get_transactions(
        account_hash=acct_hash,
        start_date=start_dt,
        end_date=end_dt,
    )
    resp.raise_for_status()
    raw_txs = resp.json()
    log.info("Fetched %d API transactions for backfill matching", len(raw_txs))

    # Build lookup: (date, amount) → API description, for non-TRADE types
    api_desc_map = {}
    for raw in raw_txs:
        tx_type = raw.get("type", "")
        if tx_type == "TRADE":
            continue
        tx_desc = raw.get("description", "")
        tx_date = (raw.get("time") or raw.get("tradeDate") or "")[:10]
        net_amt = raw.get("netAmount")
        if tx_desc and tx_date:
            api_desc_map[(tx_date, net_amt)] = tx_desc
            # Also store with rounded amount for fuzzy matching
            if net_amt is not None:
                api_desc_map[(tx_date, round(float(net_amt), 2))] = tx_desc

    updated = 0
    for row_id, action, category, old_desc, old_sym, old_und, amount in empty_rows:
        # Try to get trade_date for this row
        cur.execute("SELECT trade_date FROM transactions WHERE id = ?", (row_id,))
        td = cur.fetchone()
        if not td:
            continue
        trade_date = td[0]

        # Look up API description
        api_desc = api_desc_map.get((trade_date, amount))
        if not api_desc and amount is not None:
            api_desc = api_desc_map.get((trade_date, round(float(amount), 2)))

        if not api_desc:
            continue

        # Resolve ticker
        ticker = resolve_ticker_from_description(api_desc, conn)

        updates = {}
        if api_desc:
            updates["description"] = api_desc
        if ticker:
            updates["symbol"]     = ticker
            updates["underlying"] = ticker

        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            vals = list(updates.values()) + [row_id]
            cur.execute(f"UPDATE transactions SET {set_clause} WHERE id = ?", vals)
            updated += 1
            log.info("  ~ Updated #%d: desc=%s, ticker=%s",
                     row_id, api_desc[:40], ticker or "—")

    conn.commit()
    conn.close()
    log.info("=== Backfill complete: %d/%d rows updated ===", updated, len(empty_rows))


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force",     action="store_true",
                        help="Run regardless of market hours")
    parser.add_argument("--dry-run",   action="store_true",
                        help="Show what would be inserted without writing to DB")
    parser.add_argument("--backfill",  action="store_true",
                        help="Enrich existing DB rows that have empty descriptions")
    parser.add_argument("--days",      type=int, default=2,
                        help="How many days back to look (default: 2)")
    args = parser.parse_args()

    if args.backfill:
        backfill_descriptions()
        sys.exit(0)

    if args.dry_run:
        dry_run(lookback_days=args.days)
        sys.exit(0)

    if not should_run(args.force):
        now = datetime.now(ET)
        log.info("Outside market window (%s %s) — skipping. Use --force to override.",
                 now.strftime("%A"), now.strftime("%H:%M ET"))
        sys.exit(0)

    sync(lookback_days=args.days)
