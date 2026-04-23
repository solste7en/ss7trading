"""Tests for services/orders.py: OSI parsing and clean_orders enrichment."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.orders import clean_orders, parse_osi


class TestParseOsi:
    def test_put_basic(self):
        out = parse_osi("NVDA  260327P00177500")
        assert out == {
            "underlying": "NVDA",
            "option_type": "PUT",
            "strike": 177.5,
            "expiration_date": "2026-03-27",
        }

    def test_call_basic(self):
        out = parse_osi("AAPL  270115C00200000")
        assert out["option_type"] == "CALL"
        assert out["strike"] == 200.0
        assert out["expiration_date"] == "2027-01-15"

    def test_decimal_strike(self):
        out = parse_osi("SPY   240419P00450125")
        assert out["strike"] == 450.125

    def test_multi_digit_strike(self):
        out = parse_osi("BRK   261218C00050000")  # $50 call
        assert out["strike"] == 50.0

    def test_high_strike(self):
        out = parse_osi("NVDA  260327C01000000")  # $1000 strike
        assert out["strike"] == 1000.0

    def test_invalid_returns_none(self):
        assert parse_osi("NVDA") is None
        assert parse_osi("") is None
        assert parse_osi(None) is None
        assert parse_osi("not an option") is None

    def test_invalid_date_returns_none(self):
        assert parse_osi("NVDA  269999P00177500") is None


class TestCleanOrders:
    def test_empty_input(self):
        assert clean_orders([]) == []
        assert clean_orders(None) == []

    def test_single_equity_leg(self):
        raw = [{
            "orderId": 12345,
            "status": "WORKING",
            "orderType": "LIMIT",
            "session": "NORMAL",
            "duration": "GOOD_TILL_CANCEL",
            "quantity": 100,
            "filledQuantity": 0,
            "remainingQuantity": 100,
            "price": 150.5,
            "enteredTime": "2026-04-01T10:00:00+00:00",
            "releaseTime": "2026-04-01T10:00:00+00:00",
            "cancelTime": "2027-04-01T10:00:00+00:00",
            "cancelable": True,
            "editable": True,
            "orderLegCollection": [{
                "instruction": "BUY",
                "quantity": 100,
                "instrument": {"assetType": "EQUITY", "symbol": "NVDA"},
            }],
        }]
        out = clean_orders(raw)
        assert len(out) == 1
        o = out[0]
        assert o["order_id"] == "12345"
        assert o["underlying"] == "NVDA"
        assert o["underlyings"] == ["NVDA"]
        assert o["legs"] == 1
        assert o["release_time"] == "2026-04-01 10:00:00"
        assert o["cancel_time"] == "2027-04-01 10:00:00"
        assert len(o["legs_detail"]) == 1
        leg = o["legs_detail"][0]
        assert leg["symbol"] == "NVDA"
        assert leg["asset_type"] == "EQUITY"
        assert leg["instruction"] == "BUY"
        # Equity leg has no option_type / strike / expiration_date keys.
        assert "option_type" not in leg
        assert "strike" not in leg
        assert "expiration_date" not in leg

    def test_single_option_leg_parses_osi(self):
        raw = [{
            "orderId": 99,
            "status": "WORKING",
            "orderLegCollection": [{
                "instruction": "SELL_TO_OPEN",
                "quantity": 1,
                "instrument": {"assetType": "OPTION", "symbol": "NVDA  260327P00177500"},
            }],
        }]
        o = clean_orders(raw)[0]
        leg = o["legs_detail"][0]
        assert leg["asset_type"] == "OPTION"
        assert leg["option_type"] == "PUT"
        assert leg["strike"] == 177.5
        assert leg["expiration_date"] == "2026-03-27"
        assert o["underlying"] == "NVDA"
        assert o["underlyings"] == ["NVDA"]

    def test_multi_leg_spread_collects_underlyings(self):
        raw = [{
            "orderId": 1,
            "orderLegCollection": [
                {
                    "instruction": "SELL_TO_OPEN", "quantity": 1,
                    "instrument": {"assetType": "OPTION", "symbol": "NVDA  260327P00170000"},
                },
                {
                    "instruction": "BUY_TO_OPEN", "quantity": 1,
                    "instrument": {"assetType": "OPTION", "symbol": "NVDA  260327P00160000"},
                },
            ],
        }]
        o = clean_orders(raw)[0]
        assert o["legs"] == 2
        assert len(o["legs_detail"]) == 2
        # Same underlying across both legs → de-duplicated.
        assert o["underlyings"] == ["NVDA"]
        assert o["legs_detail"][0]["strike"] == 170.0
        assert o["legs_detail"][1]["strike"] == 160.0

    def test_missing_release_and_cancel_time_are_blank(self):
        raw = [{
            "orderId": 7,
            "orderLegCollection": [{
                "instruction": "BUY",
                "instrument": {"assetType": "EQUITY", "symbol": "AAPL"},
            }],
        }]
        o = clean_orders(raw)[0]
        assert o["release_time"] == ""
        assert o["cancel_time"] == ""
