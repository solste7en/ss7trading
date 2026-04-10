"""Shared fixtures for the schwab_app test suite."""
import sqlite3
import pytest


TRANSACTIONS_SCHEMA = """
CREATE TABLE transactions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date    TEXT,
    action        TEXT,
    category      TEXT,
    symbol        TEXT,
    underlying    TEXT,
    description   TEXT,
    quantity      REAL,
    price         REAL,
    fees          REAL,
    amount        REAL,
    is_option     INTEGER DEFAULT 0,
    option_type   TEXT,
    option_strike REAL,
    option_expiry TEXT,
    imported_at   TEXT DEFAULT (datetime('now')),
    is_from_option_event INTEGER DEFAULT 0,
    linked_option_id      INTEGER,
    linked_option_action  TEXT,
    activity_id   INTEGER
);
CREATE INDEX idx_tx_date       ON transactions(trade_date);
CREATE INDEX idx_tx_underlying ON transactions(underlying);
CREATE INDEX idx_tx_category   ON transactions(category);
CREATE INDEX idx_tx_activity_id ON transactions(activity_id);
"""


@pytest.fixture
def mem_db():
    """In-memory SQLite with the transactions schema."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(TRANSACTIONS_SCHEMA)
    return conn


@pytest.fixture
def sample_parsed_row():
    """A minimal valid parsed-row dict matching the transactions table shape."""
    return {
        "trade_date":    "2026-04-01",
        "action":        "Buy",
        "category":      "equity",
        "symbol":        "NVDA",
        "underlying":    "NVDA",
        "description":   "NVIDIA CORP",
        "quantity":      100.0,
        "price":         175.50,
        "fees":          0.01,
        "amount":        -17550.0,
        "is_option":     0,
        "option_type":   None,
        "option_strike": None,
        "option_expiry": None,
        "activity_id":   12345678,
    }


def _make_raw_trade_equity(symbol="NVDA", qty=100, price=175.50, net=-17550.0,
                           date="2026-04-01T10:30:00Z", activity_id=12345678):
    """Build a minimal Schwab TRADE equity JSON blob."""
    return {
        "type": "TRADE",
        "description": "Buy trade",
        "activityId": activity_id,
        "time": date,
        "tradeDate": date,
        "netAmount": net,
        "transferItems": [{
            "instrument": {"assetType": "EQUITY", "symbol": symbol, "description": f"{symbol} INC"},
            "amount": qty,
            "price": price,
            "positionEffect": "OPENING",
        }],
    }


def _make_raw_trade_option(underlying="NVDA", occ_symbol="NVDA  260410P00170000",
                           qty=-1, price=2.50, net=250.0,
                           date="2026-04-01T10:30:00Z", activity_id=99999):
    """Build a minimal Schwab TRADE option JSON blob."""
    return {
        "type": "TRADE",
        "description": "Sell to open",
        "activityId": activity_id,
        "time": date,
        "tradeDate": date,
        "netAmount": net,
        "transferItems": [{
            "instrument": {
                "assetType": "OPTION",
                "symbol": occ_symbol,
                "description": f"{underlying} Put",
                "underlyingSymbol": underlying,
            },
            "amount": qty,
            "price": price,
            "positionEffect": "OPENING",
        }],
    }


def _make_raw_dividend(desc="NVIDIA CORP", net=12.50, date="2026-04-01T00:00:00Z",
                       activity_id=55555, qualified=True):
    """Build a minimal Schwab DIVIDEND_OR_INTEREST JSON blob."""
    return {
        "type": "DIVIDEND_OR_INTEREST",
        "description": "Qualified Dividend" if qualified else "Cash Dividend",
        "qualifiedDividend": qualified,
        "activityId": activity_id,
        "time": date,
        "tradeDate": date,
        "netAmount": net,
        "transferItems": [{
            "instrument": {"assetType": "CURRENCY", "symbol": ""},
            "amount": net,
        }],
    }


def _make_raw_sma():
    """Build an SMA_ADJUSTMENT that should be skipped."""
    return {
        "type": "SMA_ADJUSTMENT",
        "description": "SMA Adjustment",
        "activityId": 11111,
        "time": "2026-04-01T00:00:00Z",
        "netAmount": 0,
        "transferItems": [],
    }


def _make_raw_journal(desc="NIO INC F", net=-0.50, date="2026-04-01T00:00:00Z",
                      activity_id=77777):
    """Build a JOURNAL transaction (e.g. ADR fee)."""
    return {
        "type": "JOURNAL",
        "description": desc,
        "activityId": activity_id,
        "time": date,
        "tradeDate": date,
        "netAmount": net,
        "transferItems": [{
            "instrument": {"assetType": "CURRENCY"},
            "amount": net,
        }],
    }
