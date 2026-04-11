"""Smoke tests for Flask blueprint routes using test_client.

Routes that call the Schwab API are tested with mocked auth.get_client().
Routes that only talk to the DB are tested with monkeypatched db functions.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch

import pytest

from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _mock_schwab_client():
    """Return a MagicMock that quacks like schwab.client.Client."""
    mock = MagicMock()
    mock.get_account_numbers.return_value = _ok_resp([{"hashValue": "abc123"}])
    mock.get_accounts.return_value = _ok_resp([{
        "securitiesAccount": {
            "accountNumber": "12341337",
            "positions": [{
                "instrument": {"assetType": "EQUITY", "symbol": "NVDA", "description": "NVIDIA"},
                "longQuantity": 100, "shortQuantity": 0,
                "averagePrice": 170.0, "marketValue": 18000.0,
                "longOpenProfitLoss": 1000.0,
                "currentDayProfitLoss": 50.0, "currentDayProfitLossPercentage": 0.28,
            }],
        }
    }])
    mock.get_quotes.return_value = _ok_resp({
        "NVDA": {
            "quote": {"lastPrice": 180.0, "bidPrice": 179.95, "askPrice": 180.05,
                      "netChange": 2.5, "netPercentChange": 1.4, "totalVolume": 50000000,
                      "52WeekHigh": 200.0, "52WeekLow": 120.0},
            "reference": {"description": "NVIDIA Corp"},
        }
    })
    mock.get_orders_for_all_linked_accounts.return_value = _ok_resp([])
    mock.get_option_expiration_chain.return_value = _ok_resp({"expirationList": []})
    mock.get_option_chain.return_value = _ok_resp({
        "underlying": {"symbol": "NVDA", "last": 180.0},
        "callExpDateMap": {}, "putExpDateMap": {},
    })
    return mock


def _ok_resp(json_data):
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    resp.ok = True
    resp.status_code = 200
    return resp


class TestPositionsBlueprint:
    @patch("blueprints.positions.get_client")
    def test_api_test(self, mock_gc, client):
        mock_gc.return_value = _mock_schwab_client()
        r = client.get("/api/test")
        assert r.status_code == 200
        assert r.get_json()["status"] == "ok"

    @patch("blueprints.positions.get_client")
    def test_api_positions(self, mock_gc, client):
        mock_gc.return_value = _mock_schwab_client()
        r = client.get("/api/positions")
        assert r.status_code == 200
        data = r.get_json()
        assert isinstance(data, list)
        assert data[0]["symbol"] == "NVDA"

    @patch("blueprints.positions.get_position_lists", return_value=[])
    def test_api_position_lists_get(self, _mock, client):
        r = client.get("/api/position-lists")
        assert r.status_code == 200
        assert "lists" in r.get_json()

    def test_api_position_lists_post_empty_name(self, client):
        r = client.post("/api/position-lists", json={"name": ""})
        assert r.status_code == 400


class TestQuotesBlueprint:
    @patch("blueprints.quotes.get_client")
    def test_api_quote_single(self, mock_gc, client):
        mock_gc.return_value = _mock_schwab_client()
        r = client.get("/api/quote/NVDA")
        assert r.status_code == 200
        data = r.get_json()
        assert data["symbol"] == "NVDA"
        assert data["last"] == 180.0

    @patch("blueprints.quotes.get_watchlist_symbols", return_value=[])
    def test_api_quotes_empty_list(self, _mock, client):
        r = client.get("/api/quotes/list/1")
        assert r.status_code == 200
        assert r.get_json() == []


class TestWatchlistsBlueprint:
    @patch("blueprints.watchlists.get_watchlists", return_value=[])
    def test_api_watchlists_get(self, _mock, client):
        r = client.get("/api/watchlists")
        assert r.status_code == 200
        assert r.get_json() == []

    def test_api_watchlists_post_no_name(self, client):
        r = client.post("/api/watchlists", json={})
        assert r.status_code == 400


class TestTransactionsBlueprint:
    @patch("blueprints.transactions.get_transactions", return_value={"data": [], "page": 1, "pages": 0, "total": 0})
    def test_api_transactions(self, _mock, client):
        r = client.get("/api/transactions")
        assert r.status_code == 200
        assert r.get_json()["total"] == 0

    @patch("blueprints.transactions.get_realized_gains", return_value={"data": [], "page": 1, "pages": 0, "total": 0})
    def test_api_realized_gains(self, _mock, client):
        r = client.get("/api/realized_gains")
        assert r.status_code == 200

    @patch("blueprints.transactions.get_top_tickers", return_value={"tickers": []})
    def test_api_top_tickers(self, _mock, client):
        r = client.get("/api/top-tickers")
        assert r.status_code == 200


class TestOrdersBlueprint:
    @patch("blueprints.orders.get_client")
    def test_api_orders(self, mock_gc, client):
        mock_gc.return_value = _mock_schwab_client()
        r = client.get("/api/orders")
        assert r.status_code == 200
        assert r.get_json() == []

    def test_api_place_order_unknown_type(self, client):
        r = client.post("/api/order", json={"trade_type": "unknown"})
        assert r.status_code == 400

    def test_api_place_ladder_no_rungs(self, client):
        r = client.post("/api/order/ladder", json={"rungs": []})
        assert r.status_code == 400

    def test_api_strategy_ladder_empty(self, client):
        r = client.post("/api/order/strategy-ladder", json={"orders": []})
        assert r.status_code == 400

    def test_api_strategy_ladder_too_many(self, client):
        r = client.post("/api/order/strategy-ladder", json={"orders": [{}] * 8})
        assert r.status_code == 400
        assert "7" in r.get_json().get("error", "")


class TestOptionsBlueprint:
    @patch("blueprints.options.get_client")
    def test_api_option_expirations(self, mock_gc, client):
        mock_gc.return_value = _mock_schwab_client()
        r = client.get("/api/option-expirations/NVDA")
        assert r.status_code == 200
        data = r.get_json()
        assert data["symbol"] == "NVDA"
        assert "expirations" in data

    def test_api_option_chain_no_symbol(self, client):
        r = client.get("/api/option-chain")
        assert r.status_code == 400

    def test_api_ladder_suggest_no_ticker(self, client):
        r = client.get("/api/ladder-suggest")
        assert r.status_code == 400

    def test_api_strategy_suggest_no_ticker(self, client):
        r = client.get("/api/strategy-suggest")
        assert r.status_code == 400


class TestSyncBlueprint:
    @patch("blueprints.sync.get_trade_sync_time", return_value="2026-04-10T12:00:00")
    @patch("blueprints.sync.get_most_traded_ticker", return_value="NVDA")
    def test_api_trades_last_sync(self, _m1, _m2, client):
        r = client.get("/api/trades/last-sync")
        assert r.status_code == 200
        data = r.get_json()
        assert data["most_traded_ticker"] == "NVDA"


class TestIncomeBlueprint:
    @patch("blueprints.income.get_income_stats", return_value={"total_premium": 1000})
    @patch("blueprints.income.sum_recovery_pnl_filtered", return_value=0.0)
    def test_api_income_stats(self, _m1, _m2, client):
        r = client.get("/api/income/stats")
        assert r.status_code == 200
        assert "total_premium" in r.get_json()

    def test_api_income_recovery_no_ticker(self, client):
        r = client.get("/api/income/recovery")
        assert r.status_code == 400

    def test_api_income_recovery_dismiss_negative_qty(self, client):
        r = client.post("/api/income/recovery/1/dismiss", json={"qty": -1})
        assert r.status_code == 400


class TestDashboardRoute:
    def test_dashboard_renders(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert b"dashboard" in r.data.lower()
