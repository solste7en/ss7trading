"""Tests for db.py — filter builders, pagination, and position-unwind logic."""
import os
import sqlite3
import sys
from contextlib import contextmanager

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.db import _income_trades_where

# ── get_income_weekly_timeseries ─────────────────────────────────────────────


class TestIncomeWeeklyTimeseries:
    @pytest.fixture
    def income_ts_db(self, tmp_path, monkeypatch):
        import core.db as db_mod

        monkeypatch.setattr(db_mod, "DB_PATH", str(tmp_path / "income_ts.db"))
        with db_mod._connection() as conn:
            db_mod._ensure_income_tables(conn)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO income_trades (
                    underlying, strategy, open_date, close_date, status, days_held,
                    net_premium, close_cost, fees, net_pnl, net_pnl_pct, is_win, is_perfect_win,
                    dedup_key
                ) VALUES
                ('NVDA','put_spread','2026-01-02','2026-01-08','closed',6,400,0,0,100,0,1,0,'u1'),
                ('NVDA','put_spread','2026-01-20','2026-01-22','closed',2,300,0,0,-50,0,0,0,'u2')
                """
            )
            conn.commit()
        return tmp_path

    def test_fills_week_axis_and_cumulative(self, income_ts_db):
        from core.db import get_income_weekly_timeseries

        out = get_income_weekly_timeseries(date_from="2026-01-06", date_to="2026-01-26")
        assert out["week_starts"]
        assert len(out["week_starts"]) == len(out["weekly_sum_net"])
        assert len(out["cumulative_sum_net"]) == len(out["weekly_sum_net"])
        i0 = out["week_starts"].index("2026-01-05")
        i1 = out["week_starts"].index("2026-01-19")
        assert out["weekly_option_pnl"][i0] == 100.0
        assert out["weekly_recovery_pnl"][i0] == 0.0
        assert out["weekly_sum_net"][i0] == 100.0
        assert out["weekly_option_pnl"][i1] == -50.0
        assert out["weekly_recovery_pnl"][i1] == 0.0
        assert out["weekly_sum_net"][i1] == -50.0
        assert out["cumulative_sum_net"][-1] == 50.0

    def test_open_trade_zero_realized_pnl(self, tmp_path, monkeypatch):
        import core.db as db_mod

        monkeypatch.setattr(db_mod, "DB_PATH", str(tmp_path / "income_open.db"))
        with db_mod._connection() as conn:
            db_mod._ensure_income_tables(conn)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO income_trades (
                    underlying, strategy, open_date, close_date, status, days_held,
                    net_premium, close_cost, fees, net_pnl, net_pnl_pct, is_win, is_perfect_win,
                    dedup_key
                ) VALUES ('AAPL','naked_put','2026-02-02',NULL,'open',0,250,0,0,NULL,0,0,0,'o1')
                """
            )
            conn.commit()
        from core.db import get_income_weekly_timeseries

        out = get_income_weekly_timeseries(date_from="2026-02-01", date_to="2026-02-28")
        i = out["week_starts"].index("2026-02-02")
        assert out["weekly_option_pnl"][i] == 0.0
        assert out["weekly_recovery_pnl"][i] == 0.0
        assert out["weekly_sum_net"][i] == 0.0
        assert out["cumulative_sum_net"][i] == 0.0


# ── _income_trades_where ──────────────────────────────────────────────────────

class TestIncomeTradesWhere:
    def test_empty_filters(self):
        where, params = _income_trades_where()
        assert where == []
        assert params == []

    def test_ticker_filter(self):
        where, params = _income_trades_where(ticker="nvda")
        assert len(where) == 1
        assert "underlying" in where[0]
        assert params == ["NVDA"]

    def test_status_filter(self):
        where, params = _income_trades_where(status="open")
        assert len(where) == 1
        assert "status" in where[0]
        assert params == ["open"]

    def test_strategy_filter(self):
        where, params = _income_trades_where(strategy="spread")
        assert len(where) == 1
        assert "LIKE" in where[0]
        assert params == ["%spread%"]

    def test_outcome_win(self):
        where, params = _income_trades_where(outcome="win")
        assert len(where) == 1
        assert "is_win" in where[0]
        assert params == []

    def test_outcome_perfect(self):
        where, params = _income_trades_where(outcome="perfect")
        assert any("is_perfect_win" in w for w in where)

    def test_outcome_assigned(self):
        where, params = _income_trades_where(outcome="assigned")
        assert any("assigned" in w for w in where)

    def test_outcome_closed(self):
        where, params = _income_trades_where(outcome="closed")
        assert any("open" in w for w in where)

    def test_combined(self):
        where, params = _income_trades_where(ticker="nvda", status="closed", strategy="naked")
        assert len(where) == 3
        assert len(params) == 3

    def test_table_alias(self):
        where, params = _income_trades_where(ticker="AAPL", table_alias="x")
        assert "x.underlying" in where[0]

    def test_no_alias(self):
        where, params = _income_trades_where(ticker="AAPL", table_alias="")
        assert "underlying" in where[0]
        assert not where[0].startswith(".")


# ── suggest_position_unwind (integration with in-memory DB) ───────────────────

class TestSuggestPositionUnwind:
    """Test the ladder-suggestion algorithm with real DB rows."""

    @pytest.fixture
    def populated_db(self, mem_db, monkeypatch):
        """Insert a streak of Buy trades for NVDA and point db to mem_db."""
        import core.db as db_mod
        cur = mem_db.cursor()
        for i in range(15):
            cur.execute("""
                INSERT INTO transactions
                (trade_date, action, category, symbol, underlying, quantity, price, amount)
                VALUES (?, 'Buy', 'equity', 'NVDA', 'NVDA', 100, ?, ?)
            """, (f"2026-03-{10+i:02d}", 170.0 + i * 0.5, -(170.0 + i * 0.5) * 100))
        mem_db.commit()

        @contextmanager
        def _mock_connection():
            mem_db.row_factory = sqlite3.Row
            yield mem_db
        monkeypatch.setattr(db_mod, "_connection", _mock_connection)
        return mem_db

    def test_generates_rungs(self, populated_db):
        from core.db import suggest_position_unwind
        result = suggest_position_unwind("NVDA", window_size=5, sell_pct=0.25,
                                         min_streak=10, max_rungs=3)
        assert result["direction"] == "Buy"
        assert result["unwind_action"] == "sell"
        assert result["streak_count"] == 15
        assert len(result["rungs"]) > 0
        assert len(result["rungs"]) <= 3
        for rung in result["rungs"]:
            assert "qty" in rung
            assert "price" in rung
            assert rung["price"] > 0

    def test_min_streak_not_met(self, populated_db):
        from core.db import suggest_position_unwind
        result = suggest_position_unwind("NVDA", min_streak=100)
        assert result["streak_count"] == 15
        assert len(result["rungs"]) == 0
        assert "Only" in result.get("note", "")


# ── Pagination ────────────────────────────────────────────────────────────────

class TestPagination:
    @pytest.fixture
    def tx_db(self, mem_db, monkeypatch):
        import core.db as db_mod
        cur = mem_db.cursor()
        for i in range(60):
            cur.execute("""
                INSERT INTO transactions
                (trade_date, action, category, symbol, underlying, quantity, price, amount)
                VALUES (?, 'Buy', 'equity', 'NVDA', 'NVDA', 10, 170.0, -1700.0)
            """, (f"2026-01-{(i % 28) + 1:02d}",))
        mem_db.commit()

        @contextmanager
        def _mock_connection():
            mem_db.row_factory = sqlite3.Row
            yield mem_db
        monkeypatch.setattr(db_mod, "_connection", _mock_connection)
        return mem_db

    def test_page_1_returns_25(self, tx_db):
        from core.db import get_transactions
        result = get_transactions(page=1, limit=25)
        assert len(result["data"]) == 25
        assert result["total"] == 60
        assert result["pages"] == 3

    def test_page_3_returns_remaining(self, tx_db):
        from core.db import get_transactions
        result = get_transactions(page=3, limit=25)
        assert len(result["data"]) == 10

    def test_category_filter(self, tx_db):
        from core.db import get_transactions
        result = get_transactions(category="option")
        assert result["total"] == 0
