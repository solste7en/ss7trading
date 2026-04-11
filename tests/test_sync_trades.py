"""Tests for sync_trades.py — parsing, dedup, and market-hours logic."""
import os
import sys
from collections import defaultdict
from datetime import datetime
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.sync_trades import (
    build_dedup_structures,
    classify_action,
    clean_money,
    dedup_key,
    extract_fees,
    is_duplicate,
    parse_option_symbol,
    parse_schwab_transaction,
    record_in_dedup,
    should_run,
)
from tests.conftest import (
    _make_raw_dividend,
    _make_raw_journal,
    _make_raw_sma,
    _make_raw_trade_equity,
    _make_raw_trade_option,
)

# ── clean_money ───────────────────────────────────────────────────────────────

class TestCleanMoney:
    def test_none(self):
        assert clean_money(None) is None

    def test_float_passthrough(self):
        assert clean_money(123.45) == 123.45

    def test_string_plain(self):
        assert clean_money("100.50") == 100.50

    def test_string_with_dollar_and_commas(self):
        assert clean_money("$1,234.56") == 1234.56

    def test_negative(self):
        assert clean_money("-500.00") == -500.00

    def test_negative_dollar(self):
        assert clean_money("-$1,000") == -1000.0

    def test_bad_string(self):
        assert clean_money("not_a_number") is None

    def test_empty_string(self):
        assert clean_money("") is None

    def test_zero(self):
        assert clean_money(0) == 0.0

    def test_integer(self):
        assert clean_money(42) == 42.0


# ── parse_option_symbol ───────────────────────────────────────────────────────

class TestParseOptionSymbol:
    def test_csv_put(self):
        result = parse_option_symbol("NVDA 03/27/2026 177.50 P")
        assert result is not None
        assert result["underlying"] == "NVDA"
        assert result["option_expiry"] == "2026-03-27"
        assert result["option_strike"] == 177.50
        assert result["option_type"] == "PUT"

    def test_csv_call(self):
        result = parse_option_symbol("AAPL 01/15/2027 200.00 C")
        assert result is not None
        assert result["underlying"] == "AAPL"
        assert result["option_type"] == "CALL"
        assert result["option_strike"] == 200.0

    def test_occ_put(self):
        result = parse_option_symbol("NVDA  260327P00177500")
        assert result is not None
        assert result["underlying"] == "NVDA"
        assert result["option_expiry"] == "2026-03-27"
        assert result["option_strike"] == 177.5
        assert result["option_type"] == "PUT"

    def test_occ_call(self):
        result = parse_option_symbol("AAPL  270115C00200000")
        assert result is not None
        assert result["underlying"] == "AAPL"
        assert result["option_type"] == "CALL"

    def test_plain_equity_returns_none(self):
        assert parse_option_symbol("NVDA") is None

    def test_empty_string(self):
        assert parse_option_symbol("") is None

    def test_garbage(self):
        assert parse_option_symbol("not an option at all") is None

    def test_whitespace_padding(self):
        result = parse_option_symbol("  NVDA 03/27/2026 177.50 P  ")
        assert result is not None
        assert result["underlying"] == "NVDA"


# ── classify_action ───────────────────────────────────────────────────────────

class TestClassifyAction:
    def test_option_actions(self):
        for a in ("Buy to Open", "Sell to Open", "Buy to Close", "Sell to Close",
                   "Expired", "Assigned", "Exchange or Exercise"):
            assert classify_action(a) == "option"

    def test_equity_actions(self):
        for a in ("Buy", "Sell", "Sell Short", "Stock Split", "Reverse Split"):
            assert classify_action(a) == "equity"

    def test_income_actions(self):
        for a in ("Cash Dividend", "Qualified Dividend", "Credit Interest"):
            assert classify_action(a) == "income"

    def test_transfer_actions(self):
        for a in ("MoneyLink Transfer", "Journal"):
            assert classify_action(a) == "transfer"

    def test_unknown_returns_other(self):
        assert classify_action("SomeRandomAction") == "other"
        assert classify_action("") == "other"


# ── extract_fees ──────────────────────────────────────────────────────────────

class TestExtractFees:
    def test_no_transfer_items(self):
        assert extract_fees({}) is None

    def test_no_fee_items(self):
        tx = {"transferItems": [{"instrument": {"assetType": "EQUITY"}, "amount": 100}]}
        assert extract_fees(tx) is None

    def test_single_fee(self):
        tx = {"transferItems": [
            {"feeType": "COMMISSION", "cost": 6.95},
            {"instrument": {"assetType": "EQUITY"}, "amount": 100},
        ]}
        assert extract_fees(tx) == 6.95

    def test_multiple_fees(self):
        tx = {"transferItems": [
            {"feeType": "COMMISSION", "cost": 0.65},
            {"feeType": "SEC_FEE", "amount": 0.02},
        ]}
        assert extract_fees(tx) == pytest.approx(0.67)

    def test_negative_fee_amounts_use_abs(self):
        tx = {"transferItems": [{"feeType": "FEE", "cost": -1.50}]}
        assert extract_fees(tx) == 1.50


# ── dedup_key ─────────────────────────────────────────────────────────────────

class TestDedupKey:
    def test_tuple_shape(self, sample_parsed_row):
        key = dedup_key(sample_parsed_row)
        assert key == ("2026-04-01", "Buy", "NVDA", -17550.0)

    def test_different_rows_differ(self, sample_parsed_row):
        row2 = {**sample_parsed_row, "action": "Sell"}
        assert dedup_key(sample_parsed_row) != dedup_key(row2)


# ── is_duplicate ──────────────────────────────────────────────────────────────

class TestIsDuplicate:
    def test_activity_id_match(self, sample_parsed_row):
        activity_id_set = {12345678}
        assert is_duplicate(sample_parsed_row, set(), activity_id_set, {}) is True

    def test_activity_id_none_skips(self, sample_parsed_row):
        row = {**sample_parsed_row, "activity_id": None}
        assert is_duplicate(row, set(), {99999}, {}) is False

    def test_exact_key_match(self, sample_parsed_row):
        exact_set = {dedup_key(sample_parsed_row)}
        assert is_duplicate(sample_parsed_row, exact_set, set(), {}) is True

    def test_no_match(self, sample_parsed_row):
        assert is_duplicate(sample_parsed_row, set(), set(), {}) is False

    def test_fuzzy_income_within_tolerance(self):
        row = {
            "trade_date": "2026-04-01", "action": "Cash Dividend",
            "symbol": "NVDA", "amount": 12.50, "category": "income",
            "activity_id": None,
        }
        fuzzy_idx = defaultdict(list)
        fuzzy_idx[("Cash Dividend", "NVDA")].append(("2026-04-02", 12.48))
        assert is_duplicate(row, set(), set(), fuzzy_idx) is True

    def test_fuzzy_rejected_for_equity(self):
        row = {
            "trade_date": "2026-04-01", "action": "Buy",
            "symbol": "NVDA", "amount": -17550.0, "category": "equity",
            "activity_id": None,
        }
        fuzzy_idx = defaultdict(list)
        fuzzy_idx[("Buy", "NVDA")].append(("2026-04-01", -17550.0))
        assert is_duplicate(row, set(), set(), fuzzy_idx) is False

    def test_fuzzy_date_out_of_range(self):
        row = {
            "trade_date": "2026-04-01", "action": "Cash Dividend",
            "symbol": "NVDA", "amount": 12.50, "category": "income",
            "activity_id": None,
        }
        fuzzy_idx = defaultdict(list)
        fuzzy_idx[("Cash Dividend", "NVDA")].append(("2026-04-10", 12.50))
        assert is_duplicate(row, set(), set(), fuzzy_idx) is False

    def test_fuzzy_sell_aliases(self):
        row = {
            "trade_date": "2026-04-01", "action": "Sell",
            "symbol": "NVDA", "amount": 500.0, "category": "income",
            "activity_id": None,
        }
        fuzzy_idx = defaultdict(list)
        fuzzy_idx[("Sell Short", "NVDA")].append(("2026-04-01", 500.0))
        assert is_duplicate(row, set(), set(), fuzzy_idx) is True


# ── build_dedup_structures ────────────────────────────────────────────────────

class TestBuildDedupStructures:
    def test_builds_from_db(self, mem_db):
        cur = mem_db.cursor()
        cur.execute("""
            INSERT INTO transactions (trade_date, action, symbol, amount, activity_id)
            VALUES ('2026-04-01', 'Buy', 'NVDA', -17550.0, 12345)
        """)
        mem_db.commit()
        exact, aids, fuzzy = build_dedup_structures(cur, "2026-03-01")
        assert ("2026-04-01", "Buy", "NVDA", -17550.0) in exact
        assert 12345 in aids
        assert ("Buy", "NVDA") in fuzzy

    def test_respects_buffer_date(self, mem_db):
        cur = mem_db.cursor()
        cur.execute("""
            INSERT INTO transactions (trade_date, action, symbol, amount, activity_id)
            VALUES ('2026-01-01', 'Buy', 'NVDA', -100.0, 111)
        """)
        mem_db.commit()
        exact, aids, fuzzy = build_dedup_structures(cur, "2026-03-01")
        assert len(exact) == 0
        assert 111 not in aids


# ── record_in_dedup ───────────────────────────────────────────────────────────

class TestRecordInDedup:
    def test_updates_structures(self, sample_parsed_row):
        exact = set()
        aids = set()
        fuzzy = defaultdict(list)
        record_in_dedup(sample_parsed_row, exact, aids, fuzzy)
        assert dedup_key(sample_parsed_row) in exact
        assert 12345678 in aids


# ── parse_schwab_transaction ──────────────────────────────────────────────────

class TestParseSchwabTransaction:
    def test_equity_buy(self):
        raw = _make_raw_trade_equity(symbol="NVDA", qty=100, price=175.50, net=-17550.0)
        result = parse_schwab_transaction(raw)
        assert result is not None
        assert result["action"] == "Buy"
        assert result["category"] == "equity"
        assert result["underlying"] == "NVDA"
        assert result["symbol"] == "NVDA"
        assert result["is_option"] == 0
        assert result["activity_id"] == 12345678

    def test_equity_sell(self):
        raw = _make_raw_trade_equity(qty=-50, price=180.0, net=9000.0)
        result = parse_schwab_transaction(raw)
        assert result is not None
        assert result["action"] == "Sell"

    def test_option_sell_to_open(self):
        raw = _make_raw_trade_option(qty=-1, price=2.50, net=250.0)
        result = parse_schwab_transaction(raw)
        assert result is not None
        assert result["action"] == "Sell to Open"
        assert result["category"] == "option"
        assert result["is_option"] == 1
        assert result["option_type"] == "PUT"
        assert result["option_strike"] == 170.0
        assert result["underlying"] == "NVDA"

    def test_sma_adjustment_skipped(self):
        raw = _make_raw_sma()
        assert parse_schwab_transaction(raw) is None

    def test_dividend_qualified(self):
        raw = _make_raw_dividend(qualified=True, net=12.50)
        result = parse_schwab_transaction(raw)
        assert result is not None
        assert result["action"] == "Qualified Dividend"
        assert result["category"] == "income"
        assert result["amount"] == 12.50

    def test_journal_adr_fee(self):
        raw = _make_raw_journal(desc="NIO INC F", net=-0.50)
        result = parse_schwab_transaction(raw)
        assert result is not None
        assert result["action"] == "ADR Mgmt Fee"
        assert result["category"] == "income"

    def test_journal_generic(self):
        raw = _make_raw_journal(desc="Transfer something", net=-5000.0)
        result = parse_schwab_transaction(raw)
        assert result is not None
        assert result["action"] == "Journal"

    def test_trade_date_extracted(self):
        raw = _make_raw_trade_equity(date="2026-04-07T14:30:00Z")
        result = parse_schwab_transaction(raw)
        assert result["trade_date"] == "2026-04-07"

    def test_option_symbol_normalized_to_csv_format(self):
        raw = _make_raw_trade_option(occ_symbol="NVDA  260410P00170000")
        result = parse_schwab_transaction(raw)
        assert "04/10/2026" in result["symbol"]
        assert "170.00 P" in result["symbol"]


# ── should_run ────────────────────────────────────────────────────────────────

class TestShouldRun:
    def test_force_always_true(self):
        assert should_run(force=True) is True

    @patch("services.sync_trades.datetime")
    def test_weekday_in_hours(self, mock_dt):
        from zoneinfo import ZoneInfo
        ET = ZoneInfo("America/New_York")
        mock_dt.now.return_value = datetime(2026, 4, 6, 10, 0, tzinfo=ET)  # Monday 10 AM
        mock_dt.strptime = datetime.strptime
        assert should_run(force=False) is True

    @patch("services.sync_trades.datetime")
    def test_weekday_out_of_hours(self, mock_dt):
        from zoneinfo import ZoneInfo
        ET = ZoneInfo("America/New_York")
        mock_dt.now.return_value = datetime(2026, 4, 6, 20, 0, tzinfo=ET)  # Monday 8 PM
        mock_dt.strptime = datetime.strptime
        assert should_run(force=False) is False

    @patch("services.sync_trades.datetime")
    def test_weekend_no_force(self, mock_dt):
        from zoneinfo import ZoneInfo
        ET = ZoneInfo("America/New_York")
        mock_dt.now.return_value = datetime(2026, 4, 4, 10, 0, tzinfo=ET)  # Saturday
        mock_dt.strptime = datetime.strptime
        assert should_run(force=False) is False
