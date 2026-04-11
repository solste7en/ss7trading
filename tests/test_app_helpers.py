"""Tests for pure helper functions (now in services/)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import core.db as db_module
from core.db import get_balance_history, get_balance_snapshot_status, save_balance_snapshot
from services.accounts import clean_accounts_balance
from services.orders import clean_orders as _clean_orders
from services.positions import clean_positions as _clean_positions
from services.positions import parse_occ as _parse_occ
from services.quotes import clean_quotes as _clean_quotes

# ── _parse_occ ────────────────────────────────────────────────────────────────

class TestParseOcc:
    def test_valid_put(self):
        expiry, strike = _parse_occ("NVDA  260410P00170000")
        assert expiry == "2026-04-10"
        assert strike == 170.0

    def test_valid_call(self):
        expiry, strike = _parse_occ("AAPL  270115C00200000")
        assert expiry == "2027-01-15"
        assert strike == 200.0

    def test_fractional_strike(self):
        expiry, strike = _parse_occ("NVDA  260327P00177500")
        assert strike == 177.5

    def test_invalid_returns_none(self):
        expiry, strike = _parse_occ("NVDA")
        assert expiry is None
        assert strike is None

    def test_empty_string(self):
        expiry, strike = _parse_occ("")
        assert expiry is None

    def test_short_symbol(self):
        expiry, strike = _parse_occ("NIO   260410P00005000")
        assert strike == 5.0
        assert expiry == "2026-04-10"


# ── _clean_positions ──────────────────────────────────────────────────────────

class TestCleanPositions:
    def _make_account(self, positions):
        return [{"securitiesAccount": {
            "accountNumber": "12341337",
            "positions": positions,
        }}]

    def test_equity_position(self):
        data = self._make_account([{
            "instrument": {"assetType": "EQUITY", "symbol": "NVDA", "description": "NVIDIA"},
            "longQuantity": 100, "shortQuantity": 0,
            "averagePrice": 170.0, "marketValue": 18000.0,
            "longOpenProfitLoss": 1000.0,
            "currentDayProfitLoss": 50.0, "currentDayProfitLossPercentage": 0.28,
        }])
        result = _clean_positions(data)
        assert len(result) == 1
        p = result[0]
        assert p["symbol"] == "NVDA"
        assert p["asset_type"] == "EQUITY"
        assert p["quantity"] == 100
        assert p["avg_price"] == 170.0
        assert p["unrealized_pl"] == 1000.0

    def test_short_position_uses_short_pl(self):
        data = self._make_account([{
            "instrument": {"assetType": "EQUITY", "symbol": "TQQQ", "description": "TQQQ"},
            "longQuantity": 0, "shortQuantity": 50,
            "averagePrice": 45.0, "marketValue": -2250.0,
            "shortOpenProfitLoss": 100.0,
            "currentDayProfitLoss": -10.0, "currentDayProfitLossPercentage": -0.4,
        }])
        result = _clean_positions(data)
        assert result[0]["quantity"] == -50
        assert result[0]["unrealized_pl"] == 100.0

    def test_option_position_has_expiry_strike(self):
        data = self._make_account([{
            "instrument": {
                "assetType": "OPTION",
                "symbol": "NVDA  260410P00170000",
                "description": "NVDA Put",
                "putCall": "PUT",
                "underlyingSymbol": "NVDA",
            },
            "longQuantity": 0, "shortQuantity": 1,
            "averagePrice": 2.50, "marketValue": -250.0,
            "shortOpenProfitLoss": 50.0,
            "currentDayProfitLoss": 5.0, "currentDayProfitLossPercentage": 2.0,
        }])
        result = _clean_positions(data)
        p = result[0]
        assert p["asset_type"] == "OPTION"
        assert p["option_expiry"] == "2026-04-10"
        assert p["option_strike"] == 170.0
        assert p["put_call"] == "PUT"

    def test_sorting_equity_before_option(self):
        data = self._make_account([
            {"instrument": {"assetType": "OPTION", "symbol": "NVDA  260410P00170000",
                            "putCall": "PUT", "underlyingSymbol": "NVDA"},
             "longQuantity": 1, "shortQuantity": 0,
             "marketValue": 250.0,
             "currentDayProfitLoss": 0, "currentDayProfitLossPercentage": 0},
            {"instrument": {"assetType": "EQUITY", "symbol": "AAPL", "description": "Apple"},
             "longQuantity": 10, "shortQuantity": 0,
             "marketValue": 1800.0,
             "currentDayProfitLoss": 0, "currentDayProfitLossPercentage": 0},
        ])
        result = _clean_positions(data)
        assert result[0]["asset_type"] == "EQUITY"
        assert result[1]["asset_type"] == "OPTION"


# ── _clean_quotes ─────────────────────────────────────────────────────────────

class TestCleanQuotes:
    def test_basic_quote(self):
        data = {
            "NVDA": {
                "quote": {
                    "lastPrice": 180.0, "bidPrice": 179.95, "askPrice": 180.05,
                    "netChange": 2.5, "netPercentChange": 1.4, "totalVolume": 50000000,
                    "52WeekHigh": 200.0, "52WeekLow": 120.0,
                },
                "reference": {"description": "NVIDIA Corp"},
            }
        }
        result = _clean_quotes(data)
        assert len(result) == 1
        q = result[0]
        assert q["symbol"] == "NVDA"
        assert q["last"] == 180.0
        assert q["bid"] == 179.95
        assert q["description"] == "NVIDIA Corp"

    def test_multiple_sorted(self):
        data = {
            "NVDA": {"quote": {"lastPrice": 180.0}, "reference": {}},
            "AAPL": {"quote": {"lastPrice": 150.0}, "reference": {}},
        }
        result = _clean_quotes(data)
        assert result[0]["symbol"] == "AAPL"
        assert result[1]["symbol"] == "NVDA"


# ── _clean_orders ─────────────────────────────────────────────────────────────

class TestCleanOrders:
    def test_basic_order(self):
        orders = [{
            "orderId": 123456,
            "status": "WORKING",
            "orderType": "LIMIT",
            "session": "NORMAL",
            "duration": "DAY",
            "quantity": 100,
            "filledQuantity": 0,
            "remainingQuantity": 100,
            "price": 175.50,
            "cancelable": True,
            "editable": False,
            "enteredTime": "2026-04-07T14:30:00Z",
            "orderLegCollection": [{
                "instrument": {"symbol": "NVDA", "assetType": "EQUITY"},
                "instruction": "BUY",
            }],
        }]
        result = _clean_orders(orders)
        assert len(result) == 1
        o = result[0]
        assert o["order_id"] == "123456"
        assert o["status"] == "WORKING"
        assert o["symbol"] == "NVDA"
        assert o["price"] == 175.50
        assert o["cancelable"] is True

    def test_empty_orders(self):
        assert _clean_orders([]) == []
        assert _clean_orders(None) == []


class TestCleanAccountsBalance:
    def test_masks_account_and_flattens(self):
        out = clean_accounts_balance([{
            "securitiesAccount": {
                "accountNumber": "12345678",
                "type": "CASH",
                "currentBalances": {"cashBalance": 100.5, "nested": {"x": 1}},
            },
        }])
        assert out["accounts"][0]["account_display"] == "****5678"
        assert out["accounts"][0]["current_balances"] == {"cashBalance": 100.5}
        assert out["aggregated_balance"] is None

    def test_aggregated_balance(self):
        out = clean_accounts_balance([{
            "aggregatedBalance": {"liquidationValue": 1.0},
            "securitiesAccount": {"accountNumber": "1", "type": "MARGIN"},
        }])
        assert out["aggregated_balance"] == {"liquidationValue": 1.0}


class TestBalanceSnapshots:
    _ACCOUNTS = [{
        "account_display": "****1337",
        "type": "MARGIN",
        "round_trips": 0,
        "is_day_trader": False,
        "current_balances": {
            "liquidationValue": 100000.0,
            "cashBalance": 5000.0,
            "buyingPower": 200000.0,
        },
        "projected_balances": {},
        "initial_balances": {},
    }]

    def test_save_and_retrieve(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "snap.db")
        result = save_balance_snapshot(self._ACCOUNTS, None, as_of_date="2026-04-11")
        assert result["rows_written"] >= 1
        assert result["already_saved"] is False
        assert result["as_of_date"] == "2026-04-11"

        history = get_balance_history()
        assert len(history) == 1
        assert history[0]["liquidation_value"] == 100000.0
        assert history[0]["cash_balance"] == 5000.0

    def test_once_per_day_guard(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "snap2.db")
        r1 = save_balance_snapshot(self._ACCOUNTS, None, as_of_date="2026-04-11")
        r2 = save_balance_snapshot(self._ACCOUNTS, None, as_of_date="2026-04-11")
        assert r1["already_saved"] is False
        assert r2["already_saved"] is True

    def test_different_days_both_saved(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "snap3.db")
        save_balance_snapshot(self._ACCOUNTS, None, as_of_date="2026-04-10")
        save_balance_snapshot(self._ACCOUNTS, None, as_of_date="2026-04-11")
        history = get_balance_history()
        dates = [r["as_of_date"] for r in history]
        assert "2026-04-10" in dates
        assert "2026-04-11" in dates

    def test_snapshot_status(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "snap4.db")
        assert get_balance_snapshot_status("2026-04-11") is None
        save_balance_snapshot(self._ACCOUNTS, None, as_of_date="2026-04-11")
        assert get_balance_snapshot_status("2026-04-11") is not None
