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
    def test_api_positions(self, mock_gc, client):
        """Verify clean_positions response shape — not a status smoke test."""
        mock_gc.return_value = _mock_schwab_client()
        r = client.get("/api/positions")
        assert r.status_code == 200
        data = r.get_json()
        assert isinstance(data, list)
        assert data[0]["symbol"] == "NVDA"

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

    @patch("blueprints.quotes.get_client")
    def test_api_quotes_symbols(self, mock_gc, client):
        mock_gc.return_value = _mock_schwab_client()
        r = client.get("/api/quotes/symbols?symbols=NVDA")
        assert r.status_code == 200
        data = r.get_json()
        assert isinstance(data, list)
        assert data[0]["symbol"] == "NVDA"

    def test_api_quotes_symbols_empty(self, client):
        r = client.get("/api/quotes/symbols")
        assert r.status_code == 200
        assert r.get_json() == []

    def test_api_quotes_symbols_too_many(self, client):
        big = ",".join(f"T{i}" for i in range(60))
        r = client.get("/api/quotes/symbols?symbols=" + big)
        assert r.status_code == 400

    def test_api_earnings_too_many(self, client):
        big = ",".join(f"T{i}" for i in range(60))
        r = client.get("/api/earnings?symbols=" + big)
        assert r.status_code == 400


class TestWatchlistsBlueprint:
    def test_api_watchlists_post_no_name(self, client):
        r = client.post("/api/watchlists", json={})
        assert r.status_code == 400


class TestOrdersBlueprint:
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
    @patch(
        "blueprints.income.sum_recovery_pnl_filtered",
        return_value={"recovery_pnl": 0.0, "true_recovery_pnl": 0.0},
    )
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


_BALANCE_ACCOUNTS_PAYLOAD = [{
    "securitiesAccount": {
        "accountNumber": "9999888777",
        "type": "MARGIN",
        "roundTrips": 0,
        "isDayTrader": False,
        "currentBalances": {
            "cashBalance": 1000.0,
            "liquidationValue": 50000.0,
            "cashAvailableForTrading": 800.0,
        },
        "projectedBalances": {"cashBalance": 950.0},
    },
}]


class TestBalanceBlueprint:
    @patch("blueprints.balance.get_client")
    def test_api_account_balances(self, mock_gc, client):
        mock_gc.return_value.get_accounts.return_value = _ok_resp(_BALANCE_ACCOUNTS_PAYLOAD)
        r = client.get("/api/account-balances")
        assert r.status_code == 200
        data = r.get_json()
        assert "accounts" in data
        assert "aggregated_balance" in data
        assert len(data["accounts"]) == 1
        acct = data["accounts"][0]
        assert acct["account_display"] == "****8777"
        assert acct["type"] == "MARGIN"
        assert acct["current_balances"]["liquidationValue"] == 50000.0
        assert acct["projected_balances"]["cashBalance"] == 950.0

    @patch("blueprints.balance.get_client")
    def test_snapshot_saves_and_returns_date(self, mock_gc, client, tmp_path, monkeypatch):
        import core.db as db_module
        test_db = tmp_path / "test_bal.db"
        monkeypatch.setattr(db_module, "DB_PATH", test_db)
        mock_gc.return_value.get_accounts.return_value = _ok_resp(_BALANCE_ACCOUNTS_PAYLOAD)
        r = client.post("/api/account-balances/snapshot")
        assert r.status_code == 200
        data = r.get_json()
        assert "as_of_date" in data
        assert data["rows_written"] >= 1
        assert data["already_saved"] is False

    @patch("blueprints.balance.get_client")
    def test_snapshot_once_per_day(self, mock_gc, client, tmp_path, monkeypatch):
        import core.db as db_module
        test_db = tmp_path / "test_bal2.db"
        monkeypatch.setattr(db_module, "DB_PATH", test_db)
        mock_gc.return_value.get_accounts.return_value = _ok_resp(_BALANCE_ACCOUNTS_PAYLOAD)
        r1 = client.post("/api/account-balances/snapshot")
        r2 = client.post("/api/account-balances/snapshot")
        assert r1.get_json()["already_saved"] is False
        assert r2.get_json()["already_saved"] is True

    @patch("blueprints.balance.get_balance_snapshot_status", return_value=None)
    def test_snapshot_status_not_saved(self, _mock, client):
        r = client.get("/api/account-balances/snapshot/status")
        assert r.status_code == 200
        data = r.get_json()
        assert data["already_saved"] is False
        assert data["saved_at"] is None

    def test_balance_history_empty(self, client, tmp_path, monkeypatch):
        import core.db as db_module
        monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "hist.db")
        r = client.get("/api/account-balances/history")
        assert r.status_code == 200
        assert r.get_json()["history"] == []


class TestDashboardRoute:
    def test_dashboard_renders(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert b"dashboard" in r.data.lower()


# Replaces a batch of "GET endpoint and assert 200" smoke tests. Catches the
# same class of regression (blueprint failed to register, route renamed) by
# introspecting the live Flask app's URL map.
@pytest.mark.parametrize("rule", [
    "/api/test",
    "/api/positions",
    "/api/position-lists",
    "/api/quote/<symbol>",
    "/api/quotes/list/<int:list_id>",
    "/api/quotes/symbols",
    "/api/earnings",
    "/api/earnings/<symbol>",
    "/api/watchlists",
    "/api/transactions",
    "/api/realized_gains",
    "/api/top-tickers",
    "/api/orders",
    "/api/order",
    "/api/order/ladder",
    "/api/order/strategy-ladder",
    "/api/option-expirations/<symbol>",
    "/api/option-chain",
    "/api/ladder-suggest",
    "/api/strategy-suggest",
    "/api/trades/last-sync",
    "/api/income/sync",
    "/api/income/trades",
    "/api/income/stats",
    "/api/income/recovery",
    "/api/income/timeseries",
    "/api/account-balances",
    "/api/account-balances/snapshot",
    "/api/account-balances/history",
    "/api/analytics/performance",
    "/api/analytics/exposure",
    "/api/analytics/concentration",
    "/api/analytics/consolidation",
])
def test_blueprint_route_registered(rule):
    """Every expected route must appear in the live url_map."""
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert rule in rules, f"Route {rule} not registered (blueprint missing?)"
