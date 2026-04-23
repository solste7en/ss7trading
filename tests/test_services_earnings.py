"""Tests for services/earnings.py: SQLite-backed TTL caching and next-future-date selection."""
import os
import sys
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import services.earnings as earnings_mod
from services.earnings import (
    HAS_EARNINGS_TTL,
    NO_EARNINGS_TTL,
    _clear_cache_for_tests,
    clear_earnings_cache,
    get_next_earnings,
)


@pytest.fixture(autouse=True)
def _reset_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(earnings_mod, "_DB_PATH_OVERRIDE", str(tmp_path / "test.db"))
    _clear_cache_for_tests()
    yield
    _clear_cache_for_tests()


class _FakeIndex(list):
    @property
    def empty(self):
        return len(self) == 0


class _FakeDF:
    def __init__(self, dates):
        self.index = _FakeIndex([_FakeTs(d) for d in dates])

    @property
    def empty(self):
        return len(self.index) == 0


class _FakeTs:
    def __init__(self, d):
        self._d = d

    def date(self):
        return self._d


def _install_fake_yf(monkeypatch, ticker_factory):
    """Install a stub ``yfinance`` module exposing ``Ticker``."""
    fake_yf = MagicMock()
    fake_yf.Ticker = ticker_factory
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)


class TestNextFutureDateSelection:
    def test_picks_soonest_future_date(self, monkeypatch):
        today = date.today()
        future1 = today + timedelta(days=10)
        future2 = today + timedelta(days=3)
        past = today - timedelta(days=5)

        ticker = MagicMock()
        ticker.get_earnings_dates.return_value = _FakeDF([past, future1, future2])
        ticker.calendar = None

        _install_fake_yf(monkeypatch, lambda _sym: ticker)

        out = get_next_earnings(["AAPL"])
        assert out == {"AAPL": future2.isoformat()}

    def test_no_future_date_returns_none(self, monkeypatch):
        past = date.today() - timedelta(days=2)
        ticker = MagicMock()
        ticker.get_earnings_dates.return_value = _FakeDF([past])
        ticker.calendar = None

        _install_fake_yf(monkeypatch, lambda _sym: ticker)

        out = get_next_earnings(["NVDA"])
        assert out == {"NVDA": None}

    def test_yfinance_failure_returns_none(self, monkeypatch):
        def _factory(_sym):
            raise RuntimeError("network down")

        _install_fake_yf(monkeypatch, _factory)

        out = get_next_earnings(["TSLA"])
        assert out == {"TSLA": None}


class TestCaching:
    def test_second_call_does_not_hit_yfinance(self, monkeypatch):
        future = date.today() + timedelta(days=4)
        ticker = MagicMock()
        ticker.get_earnings_dates.return_value = _FakeDF([future])
        ticker.calendar = None

        call_count = {"n": 0}

        def factory(_sym):
            call_count["n"] += 1
            return ticker

        _install_fake_yf(monkeypatch, factory)

        first = get_next_earnings(["AMZN"])
        assert first == {"AMZN": future.isoformat()}
        assert call_count["n"] == 1

        second = get_next_earnings(["AMZN"])
        assert second == {"AMZN": future.isoformat()}
        assert call_count["n"] == 1, "Cache must serve the second call"

    def test_dedupes_and_uppercases_input(self, monkeypatch):
        future = date.today() + timedelta(days=2)
        ticker = MagicMock()
        ticker.get_earnings_dates.return_value = _FakeDF([future])
        ticker.calendar = None

        call_count = {"n": 0}

        def factory(_sym):
            call_count["n"] += 1
            return ticker

        _install_fake_yf(monkeypatch, factory)

        out = get_next_earnings(["aapl", "AAPL", " aapl ", ""])
        assert "AAPL" in out
        assert call_count["n"] == 1

    def test_has_date_uses_7day_ttl(self, monkeypatch):
        """A symbol with a real date refreshes after 7 days, not 90."""
        future = date.today() + timedelta(days=4)
        ticker = MagicMock()
        ticker.get_earnings_dates.return_value = _FakeDF([future])
        ticker.calendar = None

        call_count = {"n": 0}

        def factory(_sym):
            call_count["n"] += 1
            return ticker

        _install_fake_yf(monkeypatch, factory)

        get_next_earnings(["GOOG"])
        assert call_count["n"] == 1

        # Within 7-day TTL → no re-fetch
        monkeypatch.setattr(earnings_mod, "_now", lambda: __import__("time").time() + HAS_EARNINGS_TTL - 10)
        get_next_earnings(["GOOG"])
        assert call_count["n"] == 1

        # Past 7-day TTL → re-fetch
        monkeypatch.setattr(earnings_mod, "_now", lambda: __import__("time").time() + HAS_EARNINGS_TTL + 10)
        get_next_earnings(["GOOG"])
        assert call_count["n"] == 2

    def test_null_result_uses_90day_ttl(self, monkeypatch):
        """A null result (ETF/delisted) refreshes after 90 days, not 7."""
        ticker = MagicMock()
        ticker.get_earnings_dates.return_value = _FakeDF([])
        ticker.calendar = None

        call_count = {"n": 0}

        def factory(_sym):
            call_count["n"] += 1
            return ticker

        _install_fake_yf(monkeypatch, factory)

        get_next_earnings(["SPY"])
        assert call_count["n"] == 1

        # Past 7-day TTL but within 90-day TTL → still cached
        monkeypatch.setattr(earnings_mod, "_now", lambda: __import__("time").time() + HAS_EARNINGS_TTL + 10)
        get_next_earnings(["SPY"])
        assert call_count["n"] == 1, "Null result should be cached for 90 days"

        # Past 90-day TTL → re-fetch
        monkeypatch.setattr(earnings_mod, "_now", lambda: __import__("time").time() + NO_EARNINGS_TTL + 10)
        get_next_earnings(["SPY"])
        assert call_count["n"] == 2

    def test_clear_earnings_cache_forces_refetch(self, monkeypatch):
        """clear_earnings_cache() removes the entry so the next call re-fetches."""
        future = date.today() + timedelta(days=4)
        ticker = MagicMock()
        ticker.get_earnings_dates.return_value = _FakeDF([future])
        ticker.calendar = None

        call_count = {"n": 0}

        def factory(_sym):
            call_count["n"] += 1
            return ticker

        _install_fake_yf(monkeypatch, factory)

        get_next_earnings(["MSFT"])
        assert call_count["n"] == 1

        clear_earnings_cache("MSFT")

        get_next_earnings(["MSFT"])
        assert call_count["n"] == 2, "Cleared cache must trigger re-fetch"

    def test_empty_input(self):
        assert get_next_earnings([]) == {}
