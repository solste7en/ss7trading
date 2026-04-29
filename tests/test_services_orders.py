"""Tests for services/orders.py: clean_orders enrichment.

Parser-level tests live in tests/test_options_parsing.py and tests/test_sync_trades.py.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.orders import clean_orders


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
