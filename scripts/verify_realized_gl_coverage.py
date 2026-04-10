#!/usr/bin/env python3
"""
verify_realized_gl_coverage.py — Realized G/L vs Schwab API / transactions

Implements the verification playbook:
  1) Schema + row counts / date ranges for realized_gains
  2) Optional --api: fetch transactions, scan JSON for tax/realized-related keys,
     print a sample CLOSING (or Sell to Close) TRADE if found
  3) Coverage: realized_gains rows without same-day symbol match in transactions
     (crude + closing-action filter)
  4) Cross-reference: stratified sample of realized_gains vs transactions (proceeds)

Run from repo root (schwab_app):
  ./venv/bin/python scripts/verify_realized_gl_coverage.py
  ./venv/bin/python scripts/verify_realized_gl_coverage.py --api --days 60
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Closing-side actions that can correspond to a realized-gains row
# Narrow tokens only — avoid matching "st" inside "instrument", etc.
_TAX_LIKE = re.compile(
    r"(?i)(costbasis|realized|washsale|wash_sale|gain.?loss|longterm|shortterm|"
    r"taxlot|holdingperiod|8949|disallowed|qualifieddividend|lt_gl|st_gl)"
)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def print_schema_and_stats(conn: sqlite3.Connection) -> None:
    print("=== 1. realized_gains schema (sqlite_master) ===")
    cur = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='realized_gains'"
    )
    row = cur.fetchone()
    if not row or not row[0]:
        print("ERROR: table realized_gains not found")
        return
    print(row[0])

    print("\n=== 2. realized_gains counts / date range ===")
    cur = conn.execute(
        """
        SELECT COUNT(*) AS n,
               MIN(closed_date) AS dmin,
               MAX(closed_date) AS dmax,
               SUM(wash_sale) AS wash_rows,
               SUM(CASE WHEN disallowed_loss IS NOT NULL AND disallowed_loss != 0
                        THEN 1 ELSE 0 END) AS disallowed_rows
        FROM realized_gains
        """
    )
    r = dict(cur.fetchone())
    print(json.dumps(r, indent=2))

    print("\n=== transactions (equity+option) date range ===")
    cur = conn.execute(
        """
        SELECT COUNT(*) AS n, MIN(trade_date) AS dmin, MAX(trade_date) AS dmax
        FROM transactions
        WHERE category IN ('equity', 'option')
        """
    )
    print(json.dumps(dict(cur.fetchone()), indent=2))


def _flatten_keys(obj, prefix="") -> list[str]:
    keys: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            keys.append(p)
            keys.extend(_flatten_keys(v, p))
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:20]):  # cap list depth explosion
            p = f"{prefix}[{i}]"
            keys.extend(_flatten_keys(v, p))
    return keys


def _interesting_keys(flat_keys: list[str]) -> list[str]:
    return sorted({k for k in flat_keys if _TAX_LIKE.search(k)})


def scan_api(days: int) -> None:
    sys.path.insert(0, str(BASE_DIR))
    from auth import get_client  # noqa: E402

    print(f"\n=== 3. API sample (last {days} days) — key scan ===")
    client = get_client()
    resp = client.get_account_numbers()
    resp.raise_for_status()
    acct_hash = resp.json()[0]["hashValue"]

    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days)
    resp = client.get_transactions(
        account_hash=acct_hash,
        start_date=start_dt,
        end_date=end_dt,
    )
    resp.raise_for_status()
    raw_txs = resp.json() or []
    print(f"Fetched {len(raw_txs)} raw transactions")

    all_interesting: set[str] = set()
    closing_sample = None
    for raw in raw_txs:
        flat = _flatten_keys(raw)
        all_interesting.update(_interesting_keys(flat))
        if raw.get("type") != "TRADE":
            continue
        for ti in raw.get("transferItems") or []:
            if ti.get("positionEffect") == "CLOSING":
                closing_sample = raw
                break
        if closing_sample:
            break

    if not closing_sample:
        # Fallback: first equity Sell (parsed would be closing long)
        for raw in raw_txs:
            if raw.get("type") != "TRADE":
                continue
            for ti in raw.get("transferItems") or []:
                inst = ti.get("instrument") or {}
                if inst.get("assetType") == "EQUITY" and (ti.get("amount") or 0) < 0:
                    closing_sample = raw
                    break
            if closing_sample:
                break

    print(
        "\nJSON paths matching tax-lot / realized-G-L-like names "
        "(excluding routine trade economics such as transferItems[].cost):"
    )
    if all_interesting:
        for k in sorted(all_interesting):
            print(f"  {k}")
    else:
        print("  (none — API payload has no obvious LT/ST, wash sale, or cost-basis-report fields)")
    print(
        "\nNote: transferItems[].cost on TRADE objects is cash effect / trade economics, "
        "not the portal Realized G/L breakdown (LT vs ST, wash sale, disallowed loss)."
    )

    if closing_sample:
        print("\n--- Sample closing-related TRADE (full JSON) ---")
        print(json.dumps(closing_sample, indent=2, default=str))
    else:
        print("\n(No CLOSING positionEffect TRADE found in window; no equity sell fallback.)")


def _tx_proceeds_proxy(qty: float | None, price: float | None, amount: float | None,
                       is_option: int) -> float | None:
    if amount is not None:
        return abs(float(amount))
    if qty is not None and price is not None:
        m = 100.0 if is_option else 1.0
        return abs(float(qty) * float(price) * m)
    return None


def _proceeds_close(a: float | None, b: float | None, rel: float = 0.02, floor: float = 1.0) -> bool:
    if a is None or b is None:
        return False
    if a < floor and b < floor:
        return True
    return abs(a - b) <= max(floor, rel * max(a, b))


def print_coverage(conn: sqlite3.Connection) -> None:
    print("\n=== 4. Coverage: realized_gains without same-day transaction row ===")
    cur = conn.execute(
        """
        SELECT COUNT(*) FROM realized_gains rg
        WHERE NOT EXISTS (
            SELECT 1 FROM transactions t
            WHERE t.symbol = rg.symbol AND t.trade_date = rg.closed_date
        )
        """
    )
    crude_missing = cur.fetchone()[0]
    cur = conn.execute("SELECT COUNT(*) FROM realized_gains")
    total_rg = cur.fetchone()[0]
    print(f"  No same-day rows for (symbol, closed_date): {crude_missing} / {total_rg}")

    cur = conn.execute(
        """
        SELECT COUNT(*) FROM realized_gains rg
        WHERE NOT EXISTS (
            SELECT 1 FROM transactions t
            WHERE t.symbol = rg.symbol
              AND t.trade_date = rg.closed_date
              AND t.action IN (
                'Sell','Buy to Cover','Sell to Close','Buy to Close',
                'Assigned','Expired','Exchange or Exercise'
              )
        )
        """
    )
    closing_missing = cur.fetchone()[0]
    print(
        f"  No same-day closing-action row for (symbol, closed_date): "
        f"{closing_missing} / {total_rg}"
    )


def print_cross_ref(conn: sqlite3.Connection, sample_size: int) -> None:
    print(f"\n=== 5. Stratified sample (n≈{sample_size}) vs transactions ===")

    # Buckets: equity / option / wash_sale / disallowed
    buckets = [
        ("equity_plain", "is_option = 0 AND (wash_sale IS NULL OR wash_sale = 0) "
         "AND (disallowed_loss IS NULL OR disallowed_loss = 0)"),
        ("option_plain", "is_option = 1 AND (wash_sale IS NULL OR wash_sale = 0) "
         "AND (disallowed_loss IS NULL OR disallowed_loss = 0)"),
        ("wash_sale", "wash_sale IS NOT NULL AND wash_sale != 0"),
        ("disallowed", "disallowed_loss IS NOT NULL AND disallowed_loss != 0"),
    ]
    per_bucket = max(1, sample_size // len(buckets))
    rows: list[sqlite3.Row] = []
    for name, where in buckets:
        cur = conn.execute(
            f"""
            SELECT id, symbol, underlying, closed_date, quantity, proceeds, cost_basis,
                   total_gl_amt, wash_sale, disallowed_loss, is_option
            FROM realized_gains
            WHERE {where}
            ORDER BY RANDOM()
            LIMIT ?
            """,
            (per_bucket,),
        )
        got = cur.fetchall()
        print(f"  bucket {name}: picked {len(got)} rows")
        rows.extend(got)

    match = mismatch = ambiguous = missing = 0
    details: list[dict] = []

    for rg in rows:
        cur = conn.execute(
            """
            SELECT id, action, quantity, price, amount, is_option
            FROM transactions
            WHERE symbol = ? AND trade_date = ?
              AND action IN (
                'Sell', 'Buy to Cover', 'Sell to Close', 'Buy to Close',
                'Assigned', 'Expired', 'Exchange or Exercise'
              )
            """,
            (rg["symbol"], rg["closed_date"]),
        )
        cands = cur.fetchall()

        if len(cands) == 0:
            missing += 1
            st = "missing"
        elif len(cands) > 1:
            ambiguous += 1
            st = "ambiguous"
        else:
            t = dict(cands[0])
            proxy = _tx_proceeds_proxy(
                t.get("quantity"), t.get("price"), t.get("amount"), t.get("is_option") or 0
            )
            rg_p = rg["proceeds"]
            if rg_p is not None and proxy is not None and _proceeds_close(float(rg_p), proxy):
                match += 1
                st = "match"
            elif rg_p is not None and proxy is not None:
                mismatch += 1
                st = "mismatch"
            else:
                ambiguous += 1
                st = "ambiguous_null"
            t["_proxy_proceeds"] = proxy
            details.append({
                "rg_id": rg["id"],
                "symbol": rg["symbol"],
                "closed_date": rg["closed_date"],
                "rg_proceeds": rg_p,
                "status": st,
                "tx": t,
            })
            continue

        details.append({
            "rg_id": rg["id"],
            "symbol": rg["symbol"],
            "closed_date": rg["closed_date"],
            "rg_proceeds": rg["proceeds"],
            "status": st,
            "tx": None,
        })

    print(
        f"  Summary: match={match} mismatch={mismatch} ambiguous={ambiguous} missing={missing} "
        f"(sample rows={len(rows)})"
    )
    print("\n  Per-row (first 15):")
    for d in details[:15]:
        print(f"    {json.dumps(d, default=str)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Realized G/L verification vs API/transactions")
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to trades.db (default: parent of schwab_app per config.py)",
    )
    parser.add_argument("--api", action="store_true", help="Fetch Schwab transactions and scan JSON")
    parser.add_argument("--days", type=int, default=60, help="API lookback days (default 60)")
    parser.add_argument("--sample", type=int, default=48, help="Cross-ref sample size (default 48)")
    args = parser.parse_args()

    if args.db:
        db_path = args.db
    else:
        sys.path.insert(0, str(BASE_DIR))
        from config import DB_PATH  # noqa: E402

        db_path = DB_PATH

    if not db_path.exists():
        print(f"ERROR: database not found: {db_path}", file=sys.stderr)
        return 1

    conn = _connect(db_path)
    try:
        print_schema_and_stats(conn)
        print_coverage(conn)
        print_cross_ref(conn, args.sample)
    finally:
        conn.close()

    if args.api:
        try:
            scan_api(args.days)
        except Exception as e:
            print(f"\nAPI scan failed: {e}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
