"""Tests for core.income_range preset resolution."""

from core.income_range import resolve_income_date_range


def test_all_dates():
    assert resolve_income_date_range("all") == (None, None)
    assert resolve_income_date_range("") == (None, None)


def test_custom_sorts_bounds():
    assert resolve_income_date_range("custom", "2025-03-01", "2025-01-01") == (
        "2025-01-01",
        "2025-03-01",
    )


def test_custom_incomplete_falls_back_to_all():
    assert resolve_income_date_range("custom", "2025-01-01", "") == (None, None)
    assert resolve_income_date_range("custom", "", "2025-12-31") == (None, None)
