"""Unit tests for the analytics service layer and route smoke tests."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock, patch

import pytest

from app import app
from services.analytics import (
    compute_concentration,
    compute_exposure,
    compute_performance_series,
    find_overlap_groups,
    score_consolidation_candidates,
    suggest_tax_loss_swaps,
)
from services.options import suggest_underwater_strategies

# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def balance_history():
    return [
        {"as_of_date": "2026-04-03", "liquidation_value": 50500, "equity": 50500},
        {"as_of_date": "2026-04-02", "liquidation_value": 50200, "equity": 50200},
        {"as_of_date": "2026-04-01", "liquidation_value": 50000, "equity": 50000},
    ]


@pytest.fixture
def positions():
    return [
        {"symbol": "NVDA", "asset_type": "EQUITY", "market_value": 18000, "quantity": 100,
         "avg_price": 170.0, "current_price": 180.0, "unrealized_pl": 1000, "day_pl": 50},
        {"symbol": "AMD", "asset_type": "EQUITY", "market_value": 8000, "quantity": 50,
         "avg_price": 200.0, "current_price": 160.0, "unrealized_pl": -2000, "day_pl": -30},
        {"symbol": "INTC", "asset_type": "EQUITY", "market_value": 3000, "quantity": 100,
         "avg_price": 40.0, "current_price": 30.0, "unrealized_pl": -1000, "day_pl": -10},
        {"symbol": "AAPL", "asset_type": "EQUITY", "market_value": 12000, "quantity": 50,
         "avg_price": 220.0, "current_price": 240.0, "unrealized_pl": 1000, "day_pl": 25},
        {"symbol": "SPY", "asset_type": "ETF", "market_value": 5000, "quantity": 10,
         "avg_price": 490.0, "current_price": 500.0, "unrealized_pl": 100, "day_pl": 15},
        {"symbol": "XYZ  260410P00050000", "asset_type": "OPTION", "market_value": -200,
         "quantity": -1, "avg_price": 2.0, "current_price": 2.0, "unrealized_pl": 0, "day_pl": 0},
    ]


@pytest.fixture
def sector_map():
    return {
        "NVDA": {"sector": "Technology", "industry": "Semiconductors", "marketCap": 2e12,
                 "trailingPE": 40.0, "revenueGrowth": 0.6, "profitMargins": 0.55,
                 "returnOnEquity": 0.9, "pct_from_52w_high": -8.0},
        "AMD": {"sector": "Technology", "industry": "Semiconductors", "marketCap": 200e9,
                "trailingPE": 50.0, "revenueGrowth": 0.1, "profitMargins": 0.2,
                "returnOnEquity": 0.05, "pct_from_52w_high": -35.0},
        "INTC": {"sector": "Technology", "industry": "Semiconductors", "marketCap": 100e9,
                 "trailingPE": 25.0, "revenueGrowth": -0.1, "profitMargins": 0.05,
                 "returnOnEquity": 0.01, "pct_from_52w_high": -50.0},
        "AAPL": {"sector": "Technology", "industry": "Consumer Electronics", "marketCap": 3e12,
                 "trailingPE": 30.0, "revenueGrowth": 0.05, "profitMargins": 0.25,
                 "returnOnEquity": 1.5, "pct_from_52w_high": -5.0},
        "SPY": {"sector": None, "industry": None},
    }


# ── compute_performance_series ────────────────────────────────────────────

class TestPerformanceSeries:
    def test_empty_history(self):
        result = compute_performance_series([])
        assert result["dates"] == []
        assert result["equity"] == []

    def test_basic_series(self, balance_history):
        result = compute_performance_series(balance_history)
        assert result["dates"] == ["2026-04-01", "2026-04-02", "2026-04-03"]
        assert result["equity"] == [50000, 50200, 50500]
        assert len(result["daily_pnl"]) == 3
        assert result["daily_pnl"][0] == 0.0
        assert result["daily_pnl"][1] == 200.0
        assert result["daily_pnl"][2] == 300.0

    def test_cumulative_return(self, balance_history):
        result = compute_performance_series(balance_history)
        assert result["cumulative_return_pct"][0] == 0.0
        assert result["cumulative_return_pct"][-1] == pytest.approx(1.0, abs=0.1)

    def test_drawdown_no_drop(self, balance_history):
        result = compute_performance_series(balance_history)
        for dd in result["drawdown_pct"]:
            assert dd <= 0

    def test_drawdown_with_drop(self):
        history = [
            {"as_of_date": "2026-04-03", "liquidation_value": 48000},
            {"as_of_date": "2026-04-02", "liquidation_value": 50000},
            {"as_of_date": "2026-04-01", "liquidation_value": 49000},
        ]
        result = compute_performance_series(history)
        assert min(result["drawdown_pct"]) < 0


# ── compute_exposure ──────────────────────────────────────────────────────

class TestExposure:
    def test_groups_by_sector(self, positions, sector_map):
        result = compute_exposure(positions, sector_map)
        assert result["total_value"] > 0
        sectors = result["sectors"]
        tech = next(s for s in sectors if s["name"] == "Technology")
        assert "NVDA" in tech["tickers"]
        assert tech["pct"] > 0

    def test_excludes_options(self, positions, sector_map):
        result = compute_exposure(positions, sector_map)
        total = result["total_value"]
        assert total == 18000 + 8000 + 3000 + 12000 + 5000

    def test_unknown_sector_grouped(self, positions, sector_map):
        result = compute_exposure(positions, sector_map)
        names = [s["name"] for s in result["sectors"]]
        assert "Unknown" in names

    def test_empty_positions(self):
        assert compute_exposure([], {})["sectors"] == []


# ── compute_concentration ─────────────────────────────────────────────────

class TestConcentration:
    def test_hhi_calculated(self, positions):
        result = compute_concentration(positions)
        assert result["hhi"] > 0
        assert result["hhi_label"] in ("Diversified", "Moderate", "Concentrated")

    def test_top_holdings_sorted(self, positions):
        result = compute_concentration(positions)
        holdings = result["holdings"]
        for i in range(len(holdings) - 1):
            assert holdings[i]["market_value"] >= holdings[i + 1]["market_value"]

    def test_pct_sum_near_100(self, positions):
        result = compute_concentration(positions)
        total_pct = sum(h["pct"] for h in result["holdings"])
        assert 99 < total_pct < 101

    def test_empty_positions(self):
        result = compute_concentration([])
        assert result["hhi"] == 0

    def test_side_long_for_fixture_positions(self, positions):
        result = compute_concentration(positions)
        for h in result["holdings"]:
            assert h["side"] == "long"

    def test_side_short_for_negative_quantity(self):
        positions = [
            {
                "symbol": "TSLA",
                "asset_type": "EQUITY",
                "market_value": -4500,
                "quantity": -30,
                "avg_price": -150.0,
                "current_price": 150.0,
                "unrealized_pl": -200,
                "day_pl": 0,
            },
        ]
        result = compute_concentration(positions)
        assert len(result["holdings"]) == 1
        assert result["holdings"][0]["side"] == "short"
        assert result["holdings"][0]["market_value"] == 4500


# ── find_overlap_groups ───────────────────────────────────────────────────

class TestOverlapGroups:
    def test_finds_semiconductor_overlap(self, positions, sector_map):
        groups = find_overlap_groups(positions, sector_map)
        semi_group = next((g for g in groups if "Semiconductors" in g["group"]), None)
        assert semi_group is not None
        assert semi_group["count"] == 3
        syms = [t["symbol"] for t in semi_group["tickers"]]
        assert "NVDA" in syms
        assert "AMD" in syms
        assert "INTC" in syms

    def test_no_single_stock_groups(self, positions, sector_map):
        groups = find_overlap_groups(positions, sector_map)
        for g in groups:
            assert g["count"] >= 2

    def test_empty_sector_map(self, positions):
        groups = find_overlap_groups(positions, {})
        assert groups == []


# ── score_consolidation_candidates ────────────────────────────────────────

class TestConsolidationScoring:
    def test_ranks_tickers(self, sector_map):
        tickers = [
            {"symbol": "NVDA", "market_value": 18000, "unrealized_pl": 1000, "unrealized_pl_pct": 5.9},
            {"symbol": "AMD", "market_value": 8000, "unrealized_pl": -2000, "unrealized_pl_pct": -25.0},
            {"symbol": "INTC", "market_value": 3000, "unrealized_pl": -1000, "unrealized_pl_pct": -25.0},
        ]
        scored = score_consolidation_candidates(tickers, sector_map)
        assert scored[0]["recommendation"] == "keep"
        assert scored[0]["symbol"] == "NVDA"
        for s in scored[1:]:
            assert s["recommendation"] == "consolidate"

    def test_scores_decrease(self, sector_map):
        tickers = [
            {"symbol": "NVDA", "market_value": 18000, "unrealized_pl_pct": 5.9},
            {"symbol": "AMD", "market_value": 8000, "unrealized_pl_pct": -25.0},
        ]
        scored = score_consolidation_candidates(tickers, sector_map)
        assert scored[0]["score"] >= scored[1]["score"]

    def test_includes_fundamentals(self, sector_map):
        tickers = [{"symbol": "NVDA", "market_value": 18000, "unrealized_pl_pct": 5.9}]
        scored = score_consolidation_candidates(tickers, sector_map)
        assert "fundamentals" in scored[0]
        assert scored[0]["fundamentals"]["market_cap"] is not None

    def test_empty_tickers(self, sector_map):
        assert score_consolidation_candidates([], sector_map) == []


# ── suggest_tax_loss_swaps ────────────────────────────────────────────────

class TestTaxLossSwaps:
    def test_finds_industry_peers(self, sector_map):
        result = suggest_tax_loss_swaps("AMD", sector_map)
        swaps = result["swaps"]
        sym_list = [s["symbol"] for s in swaps]
        assert "NVDA" in sym_list or "INTC" in sym_list
        assert result["wash_sale_warning"] is True

    def test_includes_etf_alternatives(self, sector_map):
        result = suggest_tax_loss_swaps("AMD", sector_map)
        etf_swaps = [s for s in result["swaps"] if s["match"] == "etf"]
        assert len(etf_swaps) > 0

    def test_unknown_sector_returns_empty(self):
        result = suggest_tax_loss_swaps("XYZ", {"XYZ": {}})
        assert result["swaps"] == []


# ── suggest_underwater_strategies ─────────────────────────────────────────

class TestUnderwaterStrategies:
    @pytest.fixture
    def underwater_positions(self):
        return [
            {"symbol": "AMD", "asset_type": "EQUITY", "market_value": 8000,
             "quantity": 200, "avg_price": 200.0, "current_price": 160.0,
             "unrealized_pl": -8000},
        ]

    @pytest.fixture
    def chain_data(self):
        return {
            "expirations": ["2026-05-15"],
            "calls": {
                "2026-05-15": [
                    {"strike": 165, "bid": 3.50, "ask": 3.80, "last": 3.60, "volume": 1000,
                     "oi": 5000, "iv": 45, "delta": 0.4, "symbol": "AMD  260515C00165000",
                     "itm": False, "description": "AMD May 15 $165 Call"},
                    {"strike": 170, "bid": 2.80, "ask": 3.10, "last": 2.90, "volume": 800,
                     "oi": 4000, "iv": 42, "delta": 0.35, "symbol": "AMD  260515C00170000",
                     "itm": False, "description": "AMD May 15 $170 Call"},
                    {"strike": 200, "bid": 0.50, "ask": 0.65, "last": 0.55, "volume": 500,
                     "oi": 3000, "iv": 38, "delta": 0.1, "symbol": "AMD  260515C00200000",
                     "itm": False, "description": "AMD May 15 $200 Call"},
                ],
            },
            "puts": {},
        }

    def test_generates_covered_call_strategies(self, underwater_positions, chain_data):
        quote = {"last": 160.0}
        result = suggest_underwater_strategies(underwater_positions, quote, chain_data, "AMD")
        cc_strategies = [s for s in result if "covered_call" in s["strategy"]]
        assert len(cc_strategies) > 0

    def test_includes_tax_loss_option(self, underwater_positions, chain_data):
        quote = {"last": 160.0}
        result = suggest_underwater_strategies(underwater_positions, quote, chain_data, "AMD")
        harvest = [s for s in result if s["strategy"] == "sell_and_harvest"]
        assert len(harvest) == 1
        assert harvest[0]["tax_loss"] > 0

    def test_includes_etf_swap_with_peer_data(self, underwater_positions, chain_data):
        quote = {"last": 160.0}
        peer_data = {"sector": "Technology", "etfs": ["SMH", "SOXX"]}
        result = suggest_underwater_strategies(
            underwater_positions, quote, chain_data, "AMD", peer_data
        )
        etf = [s for s in result if s["strategy"] == "etf_swap"]
        assert len(etf) == 1
        assert "SMH" in etf[0]["etfs"]

    def test_no_suggestions_if_not_underwater(self):
        positions = [{"symbol": "NVDA", "asset_type": "EQUITY", "quantity": 200,
                      "avg_price": 170.0, "current_price": 180.0, "unrealized_pl": 2000,
                      "market_value": 36000}]
        result = suggest_underwater_strategies(positions, {"last": 180.0}, {}, "NVDA")
        assert result == []

    def test_no_suggestions_if_less_than_100_shares(self):
        positions = [{"symbol": "AMD", "asset_type": "EQUITY", "quantity": 50,
                      "avg_price": 200.0, "current_price": 160.0, "unrealized_pl": -2000,
                      "market_value": 8000}]
        result = suggest_underwater_strategies(positions, {"last": 160.0}, {}, "AMD")
        assert result == []


# ── Route smoke tests ─────────────────────────────────────────────────────


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _ok_resp(json_data):
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    resp.ok = True
    resp.status_code = 200
    return resp


class TestAnalyticsRoutes:
    def test_performance_empty(self, client, tmp_path, monkeypatch):
        import core.db as db_module
        monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test_an.db")
        r = client.get("/api/analytics/performance")
        assert r.status_code == 200
        data = r.get_json()
        assert "dates" in data
        assert "equity" in data

    @patch("blueprints.analytics.get_client")
    @patch("blueprints.analytics.get_ticker_info_batch", return_value={})
    def test_exposure_ok(self, _mock_batch, mock_gc, client):
        mock_client = MagicMock()
        mock_client.get_accounts.return_value = _ok_resp([{
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
        mock_gc.return_value = mock_client
        r = client.get("/api/analytics/exposure")
        assert r.status_code == 200
        data = r.get_json()
        assert "sectors" in data

    @patch("blueprints.analytics.get_client")
    def test_concentration_ok(self, mock_gc, client):
        mock_client = MagicMock()
        mock_client.get_accounts.return_value = _ok_resp([{
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
        mock_gc.return_value = mock_client
        r = client.get("/api/analytics/concentration")
        assert r.status_code == 200
        data = r.get_json()
        assert "hhi" in data
        assert "holdings" in data

    def test_income_summary_ok(self, client, tmp_path, monkeypatch):
        import core.db as db_module
        monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test_income.db")
        r = client.get("/api/analytics/income-summary")
        assert r.status_code == 200
        data = r.get_json()
        assert "stats" in data
        assert "monthly_pnl" in data

    @patch("blueprints.analytics.get_client")
    @patch("blueprints.analytics.get_ticker_info_batch", return_value={})
    def test_consolidation_ok(self, _mock_batch, mock_gc, client):
        mock_client = MagicMock()
        mock_client.get_accounts.return_value = _ok_resp([{
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
        mock_gc.return_value = mock_client
        r = client.get("/api/analytics/consolidation")
        assert r.status_code == 200
        data = r.get_json()
        assert "groups" in data
        assert "underwater" in data

    @patch("blueprints.analytics.get_client")
    @patch("blueprints.analytics.get_all_position_assignments", return_value={"NVDA": 1})
    @patch("blueprints.analytics.get_position_lists", return_value=[{"id": 1, "name": "Core", "sort_order": 1, "is_system": 0}])
    @patch("blueprints.analytics.get_watchlists", return_value=[{"id": 10, "name": "Watch", "symbol_count": 1}])
    @patch("blueprints.analytics.get_watchlist_symbols_batch", return_value={10: ["AAPL"]})
    def test_consolidation_lists_ok(self, _wl_syms, _wls, _pos_lists, _assign, mock_gc, client):
        mock_client = MagicMock()
        mock_client.get_accounts.return_value = _ok_resp([{
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
        mock_gc.return_value = mock_client
        r = client.get("/api/analytics/consolidation-lists")
        assert r.status_code == 200
        data = r.get_json()
        assert "lists" in data
        lists = data["lists"]
        pos_list = next(l for l in lists if l["type"] == "position")
        assert pos_list["name"] == "Core"
        assert any(t["symbol"] == "NVDA" for t in pos_list["tickers"])
        wl_list = next(l for l in lists if l["type"] == "watchlist")
        assert wl_list["name"] == "Watch"
        assert any(t["symbol"] == "AAPL" for t in wl_list["tickers"])
