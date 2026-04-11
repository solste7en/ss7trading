"""Tests for income_sync.py — strategy classification, status, P&L, and dedup keys."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from income_sync import (
    _calc_leg_pnl,
    _classify_strategy,
    _determine_status,
    _get_close_date,
    _make_dedup_key,
    _match_legs,
)


def _leg(direction="short", leg_type="PUT", strike=170.0, expiry="2026-04-10",
         open_action="Sell to Open", close_action="Expired",
         open_qty=1, open_price=2.50, close_price=0.0, **kw):
    return {
        "direction": direction,
        "leg_type": leg_type,
        "strike": strike,
        "expiry": expiry,
        "open_action": open_action,
        "close_action": close_action,
        "open_qty": open_qty,
        "open_price": open_price,
        "close_price": close_price,
        "underlying": "NVDA",
        "open_date": "2026-04-01",
        "close_date": "2026-04-10",
        **kw,
    }


def _opt_row(action, underlying="NVDA", opt_type="PUT", strike=165.0,
             expiry="2026-04-02", trade_date="2026-03-18", qty=1, price=0.80,
             activity_id=1):
    return {
        "action": action,
        "underlying": underlying,
        "option_type": opt_type,
        "option_strike": strike,
        "option_expiry": expiry,
        "trade_date": trade_date,
        "quantity": qty,
        "price": price,
        "fees": 0,
        "activity_id": activity_id,
        "is_option": 1,
    }


# ── _match_legs (expiration / exercise close long lots) ───────────────────────

class TestMatchLegs:
    def test_long_put_expired_closes_bto_leg(self):
        """Long leg of a spread: Buy to Open then Expired must set close_action."""
        rows = [
            _opt_row("Buy to Open", strike=165.0, trade_date="2026-03-18", price=0.50, activity_id=1),
            _opt_row("Expired", strike=165.0, trade_date="2026-04-03", price=0, activity_id=2),
        ]
        matched = _match_legs(rows)
        long_leg = next(
            (m for m in matched if m["direction"] == "long" and m["strike"] == 165.0),
            None,
        )
        assert long_leg is not None
        assert long_leg["close_action"] == "Expired"
        assert long_leg["close_date"] == "2026-04-03"

    def test_short_put_still_closes_with_expired(self):
        rows = [
            _opt_row("Sell to Open", strike=177.5, trade_date="2026-03-18", price=2.50, activity_id=3),
            _opt_row("Expired", strike=177.5, trade_date="2026-04-02", price=0, activity_id=4),
        ]
        matched = _match_legs(rows)
        short_leg = next(m for m in matched if m["direction"] == "short")
        assert short_leg["close_action"] == "Expired"

    def test_spread_short_assigned_long_expired(self):
        """Put spread: suppress Expired on short when Assigned exists; long still expires."""
        rows = [
            _opt_row("Sell to Open", strike=177.5, trade_date="2026-03-18", price=2.50, activity_id=10),
            _opt_row("Buy to Open", strike=165.0, trade_date="2026-03-18", price=0.50, activity_id=11),
            _opt_row("Expired", strike=177.5, trade_date="2026-04-02", price=0, activity_id=12),
            _opt_row("Assigned", strike=177.5, trade_date="2026-04-03", price=0, activity_id=13),
            _opt_row("Expired", strike=165.0, trade_date="2026-04-03", price=0, activity_id=14),
        ]
        matched = _match_legs(rows)
        short_leg = next(m for m in matched if m["direction"] == "short" and m["strike"] == 177.5)
        long_leg = next(m for m in matched if m["direction"] == "long" and m["strike"] == 165.0)
        assert short_leg["close_action"] == "Assigned"
        assert long_leg["close_action"] == "Expired"

    def test_long_exercise_closes_bto(self):
        rows = [
            _opt_row("Buy to Open", strike=100.0, opt_type="CALL", expiry="2026-05-15",
                     trade_date="2026-04-01", price=3.0, activity_id=20),
            _opt_row("Exchange or Exercise", strike=100.0, opt_type="CALL", expiry="2026-05-15",
                     trade_date="2026-05-16", price=0, activity_id=21),
        ]
        matched = _match_legs(rows)
        leg = next(m for m in matched if m["direction"] == "long")
        assert leg["close_action"] == "Exchange or Exercise"


# ── _classify_strategy ────────────────────────────────────────────────────────

class TestClassifyStrategy:
    def test_naked_put(self):
        legs = [_leg(direction="short", leg_type="PUT")]
        assert _classify_strategy(legs) == "naked_put"

    def test_naked_call(self):
        legs = [_leg(direction="short", leg_type="CALL")]
        assert _classify_strategy(legs) == "naked_call"

    def test_put_spread(self):
        legs = [
            _leg(direction="short", leg_type="PUT", strike=170.0),
            _leg(direction="long", leg_type="PUT", strike=160.0, open_action="Buy to Open"),
        ]
        assert _classify_strategy(legs) == "put_spread"

    def test_call_spread(self):
        legs = [
            _leg(direction="short", leg_type="CALL", strike=200.0),
            _leg(direction="long", leg_type="CALL", strike=210.0, open_action="Buy to Open"),
        ]
        assert _classify_strategy(legs) == "call_spread"

    def test_collar(self):
        legs = [
            _leg(direction="short", leg_type="PUT", strike=170.0),
            _leg(direction="long", leg_type="CALL", strike=200.0, open_action="Buy to Open"),
        ]
        assert _classify_strategy(legs) == "collar"

    def test_other_three_legs(self):
        legs = [
            _leg(direction="short", leg_type="PUT"),
            _leg(direction="long", leg_type="PUT", open_action="Buy to Open"),
            _leg(direction="long", leg_type="CALL", open_action="Buy to Open"),
        ]
        assert _classify_strategy(legs) == "other"


# ── _determine_status ─────────────────────────────────────────────────────────

class TestDetermineStatus:
    def test_open(self):
        legs = [_leg(close_action=None)]
        assert _determine_status(legs) == "open"

    def test_expired(self):
        legs = [_leg(close_action="Expired")]
        assert _determine_status(legs) == "expired"

    def test_assigned(self):
        legs = [_leg(close_action="Assigned")]
        assert _determine_status(legs) == "assigned"

    def test_closed_buy_to_close(self):
        legs = [_leg(close_action="Buy to Close")]
        assert _determine_status(legs) == "closed"

    def test_mixed_assigned_takes_priority(self):
        legs = [
            _leg(close_action="Assigned"),
            _leg(close_action="Expired", direction="long", open_action="Buy to Open"),
        ]
        assert _determine_status(legs) == "assigned"


# ── _get_close_date ───────────────────────────────────────────────────────────

class TestGetCloseDate:
    def test_returns_latest(self):
        legs = [
            _leg(close_action="Expired"),
            {**_leg(close_action="Expired"), "close_date": "2026-04-12"},
        ]
        assert _get_close_date(legs) == "2026-04-12"

    def test_open_returns_none(self):
        legs = [{**_leg(), "close_date": None, "close_action": None}]
        assert _get_close_date(legs) is None


# ── _make_dedup_key ───────────────────────────────────────────────────────────

class TestMakeDedupKey:
    def test_deterministic(self):
        legs = [_leg()]
        k1 = _make_dedup_key("NVDA", "2026-04-01", legs)
        k2 = _make_dedup_key("NVDA", "2026-04-01", legs)
        assert k1 == k2
        assert isinstance(k1, str)
        assert len(k1) == 32  # MD5 hex digest

    def test_different_legs_different_key(self):
        legs_a = [_leg(strike=170.0)]
        legs_b = [_leg(strike=175.0)]
        assert _make_dedup_key("NVDA", "2026-04-01", legs_a) != \
               _make_dedup_key("NVDA", "2026-04-01", legs_b)


# ── _calc_leg_pnl ────────────────────────────────────────────────────────────

class TestCalcLegPnl:
    def test_short_expired_full_premium(self):
        leg = _leg(direction="short", open_price=2.50, open_qty=1, close_action="Expired")
        assert _calc_leg_pnl(leg) == 250.0  # 2.50 * 100 * 1

    def test_short_buy_to_close(self):
        leg = _leg(direction="short", open_price=2.50, open_qty=1,
                   close_action="Buy to Close", close_price=1.00)
        assert _calc_leg_pnl(leg) == 150.0  # (2.50 - 1.00) * 100

    def test_short_assigned_with_stock_price(self):
        leg = _leg(direction="short", leg_type="PUT", strike=170.0,
                   open_price=2.50, open_qty=1, close_action="Assigned")
        pnl = _calc_leg_pnl(leg, assignment_stock_price=165.0)
        assert pnl == -250.0  # premium 250 - intrinsic (170-165)*100 = 250 - 500

    def test_short_assigned_put_otm(self):
        leg = _leg(direction="short", leg_type="PUT", strike=170.0,
                   open_price=2.50, open_qty=1, close_action="Assigned")
        pnl = _calc_leg_pnl(leg, assignment_stock_price=175.0)
        assert pnl == 250.0  # intrinsic = 0

    def test_long_expired_loses_premium(self):
        leg = _leg(direction="long", open_price=1.50, open_qty=1,
                   close_action="Expired", open_action="Buy to Open")
        assert _calc_leg_pnl(leg) == -150.0  # lost the premium

    def test_long_sell_to_close(self):
        leg = _leg(direction="long", open_price=1.50, open_qty=1,
                   close_action="Sell to Close", close_price=3.00,
                   open_action="Buy to Open")
        assert _calc_leg_pnl(leg) == 150.0  # (3.00 - 1.50) * 100

    def test_still_open_returns_zero(self):
        leg = _leg(direction="short", open_price=2.50, open_qty=1, close_action=None)
        assert _calc_leg_pnl(leg) == 0
