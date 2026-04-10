"""Tests for recovery.py — LIFO matching and summary attachment."""
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from recovery import _match_recovery, attach_recovery_summaries


def _assignment(trade_id, strike, option_type="PUT", date="2026-03-15",
                qty=100, dismissed=0):
    return {
        "trade_id": trade_id,
        "strike": strike,
        "option_type": option_type,
        "assignment_date": date,
        "assigned_qty": qty,
        "dismissed_qty": dismissed,
        "recovery_trades": [],
        "recovered_qty": 0,
        "_remaining": qty,
    }


def _equity_trades(entries):
    """Build a list of equity trade dicts from (date, action, qty, price) tuples."""
    return [
        {"trade_date": d, "action": a, "quantity": q, "price": p}
        for d, a, q, p in entries
    ]


# ── _match_recovery ───────────────────────────────────────────────────────────

class TestMatchRecovery:
    def test_single_assignment_full_recovery(self, monkeypatch):
        """100 shares assigned, 100 shares sold → complete."""
        import recovery as rec_mod
        assignments = [_assignment(1, strike=170.0, qty=100)]
        eq_trades = _equity_trades([
            ("2026-03-16", "Sell", 100, 172.0),
        ])
        monkeypatch.setattr(rec_mod, "get_recovery_equity_trades",
                            lambda *a, **kw: eq_trades)

        result = _match_recovery("NVDA", assignments, ("Sell", "Sell Short"), "put")
        assert len(result) == 1
        a = result[0]
        assert a["recovered_qty"] == 100
        assert a["is_complete"] is True
        assert a["remaining_qty"] == 0
        assert a["recovery_pnl"] == 200.0  # (172 - 170) * 100

    def test_lifo_fills_most_recent_first(self, monkeypatch):
        """Two assignments — recovery trade fills the more recent one first."""
        import recovery as rec_mod
        assignments = [
            _assignment(1, strike=170.0, qty=100, date="2026-03-10"),
            _assignment(2, strike=175.0, qty=100, date="2026-03-15"),
        ]
        eq_trades = _equity_trades([
            ("2026-03-16", "Sell", 100, 176.0),
        ])
        monkeypatch.setattr(rec_mod, "get_recovery_equity_trades",
                            lambda *a, **kw: eq_trades)

        result = _match_recovery("NVDA", assignments, ("Sell", "Sell Short"), "put")
        by_id = {a["trade_id"]: a for a in result}
        assert by_id[2]["recovered_qty"] == 100  # newer assignment filled first
        assert by_id[1]["recovered_qty"] == 0

    def test_partial_fill(self, monkeypatch):
        import recovery as rec_mod
        assignments = [_assignment(1, strike=170.0, qty=200)]
        eq_trades = _equity_trades([
            ("2026-03-16", "Sell", 50, 172.0),
        ])
        monkeypatch.setattr(rec_mod, "get_recovery_equity_trades",
                            lambda *a, **kw: eq_trades)

        result = _match_recovery("NVDA", assignments, ("Sell", "Sell Short"), "put")
        assert result[0]["recovered_qty"] == 50
        assert result[0]["remaining_qty"] == 150
        assert result[0]["is_complete"] is False

    def test_dismissed_qty_reduces_target(self, monkeypatch):
        import recovery as rec_mod
        assignments = [_assignment(1, strike=170.0, qty=100, dismissed=50)]
        eq_trades = _equity_trades([
            ("2026-03-16", "Sell", 50, 172.0),
        ])
        monkeypatch.setattr(rec_mod, "get_recovery_equity_trades",
                            lambda *a, **kw: eq_trades)

        result = _match_recovery("NVDA", assignments, ("Sell", "Sell Short"), "put")
        assert result[0]["is_complete"] is True  # effective target = 100 - 50 = 50

    def test_call_recovery_direction(self, monkeypatch):
        import recovery as rec_mod
        assignments = [_assignment(1, strike=200.0, option_type="CALL", qty=100)]
        eq_trades = _equity_trades([
            ("2026-03-16", "Buy", 100, 195.0),
        ])
        monkeypatch.setattr(rec_mod, "get_recovery_equity_trades",
                            lambda *a, **kw: eq_trades)

        result = _match_recovery("NVDA", assignments, ("Buy", "Buy to Cover"), "call")
        assert result[0]["recovery_pnl"] == 500.0  # (200 - 195) * 100

    def test_empty_assignments(self, monkeypatch):
        import recovery as rec_mod
        monkeypatch.setattr(rec_mod, "get_recovery_equity_trades",
                            lambda *a, **kw: [])
        result = _match_recovery("NVDA", [], ("Sell",), "put")
        assert result == []


# ── attach_recovery_summaries ─────────────────────────────────────────────────

class TestAttachRecoverySummaries:
    def test_non_assigned_get_none_fields(self):
        trades = [
            {"id": 1, "status": "expired", "underlying": "NVDA"},
            {"id": 2, "status": "closed", "underlying": "NVDA"},
        ]
        attach_recovery_summaries(trades)
        for t in trades:
            assert t["recovery_recovered"] is None
            assert t["recovery_target"] is None
            assert t["recovery_pnl"] is None

    def test_empty_list(self):
        attach_recovery_summaries([])  # should not raise
