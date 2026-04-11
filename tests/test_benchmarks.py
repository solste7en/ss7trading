"""Performance benchmarks for critical code paths.

Run with:  pytest tests/test_benchmarks.py --benchmark-only -v
"""
import os
import random
import sqlite3
import sys
from contextlib import contextmanager

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.sync_trades import (
    build_dedup_structures,
    is_duplicate,
    parse_schwab_transaction,
    record_in_dedup,
)
from tests.conftest import (
    TRANSACTIONS_SCHEMA,
    _make_raw_dividend,
    _make_raw_journal,
    _make_raw_trade_equity,
    _make_raw_trade_option,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

_TICKERS = ["NVDA", "AAPL", "MSFT", "TSLA", "AMZN", "GOOGL", "META", "TQQQ", "SPY", "NIO"]
_ACTIONS = ["Buy", "Sell", "Sell Short", "Buy to Open", "Sell to Open",
            "Cash Dividend", "MoneyLink Transfer"]


def _seed_db(conn, n_rows):
    """Insert n_rows realistic transactions into an in-memory DB."""
    cur = conn.cursor()
    for i in range(n_rows):
        ticker = _TICKERS[i % len(_TICKERS)]
        action = _ACTIONS[i % len(_ACTIONS)]
        from services.sync_trades import classify_action
        cat = classify_action(action)
        price = round(100 + random.random() * 200, 2)
        qty = random.choice([10, 50, 100, -10, -50, -100])
        amt = round(price * qty, 2)
        date_day = 1 + (i % 28)
        date_month = 1 + (i % 12)
        cur.execute("""
            INSERT INTO transactions
            (trade_date, action, category, symbol, underlying,
             quantity, price, amount, activity_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (f"2026-{date_month:02d}-{date_day:02d}", action, cat,
              ticker, ticker, qty, price, amt, 100000 + i))
    conn.commit()


def _make_sample_raw_txs(n):
    """Generate n raw Schwab API JSON blobs for parsing benchmarks."""
    txs = []
    for i in range(n):
        kind = i % 4
        if kind == 0:
            txs.append(_make_raw_trade_equity(
                symbol=_TICKERS[i % len(_TICKERS)],
                qty=100, price=round(150 + random.random() * 50, 2),
                activity_id=200000 + i,
                date=f"2026-{1 + i % 12:02d}-{1 + i % 28:02d}T10:30:00Z"))
        elif kind == 1:
            txs.append(_make_raw_trade_option(
                underlying=_TICKERS[i % len(_TICKERS)],
                activity_id=200000 + i,
                date=f"2026-{1 + i % 12:02d}-{1 + i % 28:02d}T10:30:00Z"))
        elif kind == 2:
            txs.append(_make_raw_dividend(
                activity_id=200000 + i,
                net=round(5 + random.random() * 20, 2),
                date=f"2026-{1 + i % 12:02d}-{1 + i % 28:02d}T00:00:00Z"))
        else:
            txs.append(_make_raw_journal(
                activity_id=200000 + i,
                net=round(-0.5 - random.random() * 10, 2),
                date=f"2026-{1 + i % 12:02d}-{1 + i % 28:02d}T00:00:00Z"))
    return txs


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def sample_txs_1000():
    random.seed(42)
    return _make_sample_raw_txs(1000)


@pytest.fixture(scope="module")
def db_10k():
    random.seed(42)
    conn = sqlite3.connect(":memory:")
    conn.executescript(TRANSACTIONS_SCHEMA)
    _seed_db(conn, 10_000)
    return conn


@pytest.fixture(scope="module")
def db_50k():
    random.seed(42)
    conn = sqlite3.connect(":memory:")
    conn.executescript(TRANSACTIONS_SCHEMA)
    _seed_db(conn, 50_000)
    return conn


# ── Benchmark 1: parse_schwab_transaction throughput ──────────────────────────

def test_bench_parse_1000_transactions(benchmark, sample_txs_1000):
    """Parse 1000 sample transactions and measure throughput."""
    def run():
        for tx in sample_txs_1000:
            parse_schwab_transaction(tx)

    benchmark(run)


# ── Benchmark 2: is_duplicate against 10K-row dedup index ────────────────────

def test_bench_is_duplicate_10k(benchmark, db_10k):
    """Check 200 rows against a 10K-row dedup index (includes fuzzy path)."""
    random.seed(99)
    cur = db_10k.cursor()
    exact_set, aid_set, fuzzy_idx = build_dedup_structures(cur, "2025-01-01")

    probe_rows = []
    for i in range(200):
        ticker = _TICKERS[i % len(_TICKERS)]
        cat = "income" if i % 3 == 0 else "equity"
        action = "Cash Dividend" if cat == "income" else "Buy"
        probe_rows.append({
            "trade_date": f"2026-{1 + i % 12:02d}-{1 + i % 28:02d}",
            "action": action,
            "symbol": ticker,
            "amount": round(100 + random.random() * 500, 2),
            "category": cat,
            "activity_id": None,
        })

    def run():
        for row in probe_rows:
            is_duplicate(row, exact_set, aid_set, fuzzy_idx)

    benchmark(run)


# ── Benchmark 3: build_dedup_structures on 10K and 50K rows ──────────────────

def test_bench_build_dedup_10k(benchmark, db_10k):
    """Build the complete dedup index from 10K DB rows."""
    cur = db_10k.cursor()
    benchmark(build_dedup_structures, cur, "2025-01-01")


def test_bench_build_dedup_50k(benchmark, db_50k):
    """Build the complete dedup index from 50K DB rows."""
    cur = db_50k.cursor()
    benchmark(build_dedup_structures, cur, "2025-01-01")


# ── Benchmark 4: suggest_position_unwind with 200 trades ─────────────────────

def test_bench_suggest_unwind_200(benchmark, monkeypatch):
    """Ladder generation on a 200-trade history."""
    import core.db as db_mod
    random.seed(42)

    conn = sqlite3.connect(":memory:")
    conn.executescript(TRANSACTIONS_SCHEMA)
    cur = conn.cursor()
    for i in range(200):
        price = round(170 + random.random() * 10, 2)
        cur.execute("""
            INSERT INTO transactions
            (trade_date, action, category, symbol, underlying, quantity, price, amount)
            VALUES (?, 'Buy', 'equity', 'NVDA', 'NVDA', 100, ?, ?)
        """, (f"2026-{1 + i % 12:02d}-{1 + i % 28:02d}", price, -price * 100))
    conn.commit()

    @contextmanager
    def _mock_conn():
        conn.row_factory = sqlite3.Row
        yield conn
    monkeypatch.setattr(db_mod, "_connection", _mock_conn)

    from core.db import suggest_position_unwind
    benchmark(suggest_position_unwind, "NVDA", window_size=5, sell_pct=0.25,
              min_streak=10, max_rungs=5)


# ── Benchmark 5: _match_recovery with 20 assignments + 500 equity trades ─────

def test_bench_match_recovery(benchmark, monkeypatch):
    """LIFO matching cost: 20 assignments against 500 equity trades."""
    import services.recovery as rec_mod
    random.seed(42)

    assignments = []
    for i in range(20):
        assignments.append({
            "trade_id": i + 1,
            "strike": round(165 + i * 0.5, 2),
            "option_type": "PUT",
            "assignment_date": f"2026-{1 + i % 6:02d}-{10 + i % 15:02d}",
            "assigned_qty": 100,
            "dismissed_qty": 0,
            "recovery_trades": [],
            "recovered_qty": 0,
            "_remaining": 100,
        })

    eq_trades = []
    for i in range(500):
        eq_trades.append({
            "trade_date": f"2026-{1 + i % 12:02d}-{1 + i % 28:02d}",
            "action": "Sell",
            "quantity": random.choice([10, 20, 50, 100]),
            "price": round(168 + random.random() * 10, 2),
        })

    monkeypatch.setattr(rec_mod, "get_recovery_equity_trades",
                        lambda *a, **kw: eq_trades)

    import copy

    def run():
        fresh = copy.deepcopy(assignments)
        rec_mod._match_recovery("NVDA", fresh, ("Sell", "Sell Short"), "put")

    benchmark(run)


# ── Benchmark 6: Full sync pipeline (mocked API, real SQLite) ────────────────

def test_bench_sync_pipeline_500(benchmark):
    """End-to-end parse-dedup-insert with 500 transactions (in-memory SQLite)."""
    random.seed(42)
    raw_txs = _make_sample_raw_txs(500)

    def run():
        conn = sqlite3.connect(":memory:")
        conn.executescript(TRANSACTIONS_SCHEMA)
        cur = conn.cursor()
        exact_set, aid_set, fuzzy_idx = build_dedup_structures(cur, "2025-01-01")

        inserted = 0
        for tx in raw_txs:
            row = parse_schwab_transaction(tx)
            if row is None:
                continue
            if is_duplicate(row, exact_set, aid_set, fuzzy_idx):
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
            record_in_dedup(row, exact_set, aid_set, fuzzy_idx)
            inserted += 1
        conn.commit()
        conn.close()
        return inserted

    benchmark(run)
