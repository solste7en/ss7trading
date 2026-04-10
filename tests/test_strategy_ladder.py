"""Validation tests for POST /api/order/strategy-ladder."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import app


class TestStrategyLadderApi:
    def test_empty_orders_400(self):
        with app.test_client() as c:
            r = c.post("/api/order/strategy-ladder", json={"orders": []})
        assert r.status_code == 400
        data = r.get_json()
        assert "error" in data

    def test_too_many_rungs_400(self):
        with app.test_client() as c:
            r = c.post("/api/order/strategy-ladder", json={"orders": [{}] * 8})
        assert r.status_code == 400
        data = r.get_json()
        assert "7" in data.get("error", "")
