"""Unit tests for the income strategy suggester in services/options.py."""

import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.options import (
    _find_long_leg_by_width,
    _pick_short_strikes,
    _select_expirations,
    suggest_strategies,
)


def _d(days):
    return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()


# ── _select_expirations ───────────────────────────────────────────────────


class TestSelectExpirations:
    def test_empty_input(self):
        assert _select_expirations([]) == []

    def test_nvda_like_chain_skips_under_7_dte(self):
        # Daily (1-4 DTE) + weekly spread. Realistic NVDA-style chain.
        exps = [_d(1), _d(2), _d(3), _d(4), _d(7), _d(11),
                _d(14), _d(18), _d(25), _d(32), _d(46), _d(60)]
        picked = _select_expirations(exps)
        today = datetime.date.today()
        dtes = sorted(
            (datetime.date.fromisoformat(e) - today).days for e in picked
        )
        assert len(picked) == 3
        assert all(d >= 7 for d in dtes)
        # Closest to [14, 30, 45] → 14, 32, 46.
        assert dtes == [14, 32, 46]

    def test_dedupes_when_targets_collapse(self):
        # Only 20 and 60 DTE available — both 14 and 30 collapse onto 20.
        exps = [_d(20), _d(60)]
        picked = _select_expirations(exps)
        assert sorted(picked) == sorted(set(picked))
        assert _d(20) in picked

    def test_falls_back_when_chain_is_all_short_dated(self):
        # Chain is entirely under min_dte — we still need to return something.
        exps = [_d(1), _d(3), _d(5)]
        picked = _select_expirations(exps)
        assert len(picked) >= 1
        assert all(e in exps for e in picked)

    def test_custom_targets_and_min_dte(self):
        exps = [_d(5), _d(10), _d(20), _d(45)]
        picked = _select_expirations(exps, targets=(10, 20), min_dte=7)
        assert sorted(picked) == sorted([_d(10), _d(20)])

    def test_malformed_date_is_ignored(self):
        exps = ["not-a-date", _d(30), _d(45)]
        picked = _select_expirations(exps)
        for e in picked:
            # Valid iso date only.
            datetime.date.fromisoformat(e)


# ── _pick_short_strikes ───────────────────────────────────────────────────


class TestPickShortStrikes:
    def _put_chain_with_delta(self):
        # last ≈ 100. Deltas mimic typical OTM put curve.
        return [
            {"strike": 98, "bid": 1.80, "ask": 1.82, "delta": -0.42,
             "oi": 500, "volume": 100, "symbol": "X_P98"},
            {"strike": 95, "bid": 1.20, "ask": 1.22, "delta": -0.30,
             "oi": 500, "volume": 100, "symbol": "X_P95"},
            {"strike": 90, "bid": 0.70, "ask": 0.72, "delta": -0.20,
             "oi": 500, "volume": 100, "symbol": "X_P90"},
            {"strike": 85, "bid": 0.35, "ask": 0.37, "delta": -0.12,
             "oi": 500, "volume": 100, "symbol": "X_P85"},
            {"strike": 80, "bid": 0.15, "ask": 0.17, "delta": -0.06,
             "oi": 500, "volume": 100, "symbol": "X_P80"},
        ]

    def test_delta_targeted_when_deltas_present(self):
        chain = self._put_chain_with_delta()
        picks = _pick_short_strikes(chain, last=100, side="put")
        assert len(picks) == 3
        strikes = [p["strike"] for p in picks]
        # Targets ~0.30/0.20/0.15 → 95, 90, 85.
        assert strikes == [95, 90, 85]

    def test_pct_otm_fallback_when_delta_missing(self):
        chain = []
        for s in (97, 95, 92, 88, 80):
            chain.append({
                "strike": s, "bid": 1.0, "ask": 1.1, "delta": None,
                "oi": 100, "volume": 10,
                "symbol": f"X_P{s}",
            })
        picks = _pick_short_strikes(chain, last=100, side="put")
        assert len(picks) == 3
        strikes = [p["strike"] for p in picks]
        # Targets 3%/5%/8% below 100 → ideals 97/95/92 → nearest = 97, 95, 92.
        assert strikes == [97, 95, 92]

    def test_only_otm_contracts_returned_for_puts(self):
        chain = [
            {"strike": 105, "bid": 6.0, "ask": 6.1, "delta": -0.60,
             "oi": 100, "volume": 10, "symbol": "X_P105"},  # ITM for puts
            {"strike": 95, "bid": 1.2, "ask": 1.3, "delta": -0.30,
             "oi": 100, "volume": 10, "symbol": "X_P95"},
        ]
        picks = _pick_short_strikes(chain, last=100, side="put")
        assert all(p["strike"] < 100 for p in picks)

    def test_only_otm_contracts_returned_for_calls(self):
        chain = [
            {"strike": 95, "bid": 6.0, "ask": 6.1, "delta": 0.60,
             "oi": 100, "volume": 10, "symbol": "X_C95"},  # ITM for calls
            {"strike": 105, "bid": 1.2, "ask": 1.3, "delta": 0.30,
             "oi": 100, "volume": 10, "symbol": "X_C105"},
        ]
        picks = _pick_short_strikes(chain, last=100, side="call")
        assert all(c["strike"] > 100 for c in picks)

    def test_filters_crumb_bids(self):
        chain = [
            {"strike": 95, "bid": 0.03, "ask": 0.04, "delta": -0.25,
             "oi": 100, "volume": 10, "symbol": "X_P95"},
            {"strike": 90, "bid": 0.70, "ask": 0.72, "delta": -0.20,
             "oi": 100, "volume": 10, "symbol": "X_P90"},
        ]
        picks = _pick_short_strikes(chain, last=100, side="put")
        strikes = [p["strike"] for p in picks]
        assert 95 not in strikes

    def test_prefers_liquid_contracts(self):
        # Two strikes equally close to delta target 0.30 — one illiquid.
        chain = [
            {"strike": 95, "bid": 1.20, "ask": 1.22, "delta": -0.30,
             "oi": 0, "volume": 0, "symbol": "X_P95_illiq"},
            {"strike": 94, "bid": 1.10, "ask": 1.12, "delta": -0.29,
             "oi": 500, "volume": 50, "symbol": "X_P94_liq"},
            {"strike": 90, "bid": 0.70, "ask": 0.72, "delta": -0.20,
             "oi": 500, "volume": 50, "symbol": "X_P90"},
            {"strike": 85, "bid": 0.35, "ask": 0.37, "delta": -0.12,
             "oi": 500, "volume": 50, "symbol": "X_P85"},
        ]
        picks = _pick_short_strikes(chain, last=100, side="put")
        # The illiquid 95 should be skipped in favor of the liquid 94 at the
        # 0.30 target.
        strikes = [p["strike"] for p in picks]
        assert 95 not in strikes
        assert 94 in strikes

    def test_empty_inputs(self):
        assert _pick_short_strikes([], last=100, side="put") == []
        assert _pick_short_strikes([{"strike": 95, "bid": 1.0}], last=None,
                                   side="put") == []


# ── _find_long_leg_by_width ───────────────────────────────────────────────


class TestFindLongLegByWidth:
    def test_put_picks_closest_to_short_minus_width(self):
        chain = [
            {"strike": s, "ask": 0.5, "bid": 0.4}
            for s in (70, 75, 80, 85, 90, 95, 100)
        ]
        # Short @ 95, target width $5 → ideal long strike 90.
        leg = _find_long_leg_by_width(chain, 95, 5.0, "put")
        assert leg is not None
        assert leg["strike"] == 90

    def test_call_picks_closest_to_short_plus_width(self):
        chain = [
            {"strike": s, "ask": 0.5, "bid": 0.4}
            for s in (100, 105, 110, 115, 120)
        ]
        leg = _find_long_leg_by_width(chain, 105, 5.0, "call")
        assert leg is not None
        assert leg["strike"] == 110

    def test_returns_none_when_no_candidates(self):
        assert _find_long_leg_by_width([], 100, 5.0, "put") is None

    def test_requires_ask(self):
        chain = [{"strike": 90, "ask": 0.0, "bid": 0.4}]
        assert _find_long_leg_by_width(chain, 95, 5.0, "put") is None


# ── suggest_strategies (end-to-end) ───────────────────────────────────────


def _nvda_like_chain(last=200.0):
    """Return a chain_data dict mirroring what the Schwab client produces for
    a heavily-traded name: daily 1-4 DTE expirations plus weeklies/monthlies."""
    dtes = [1, 4, 7, 14, 32, 46]

    expirations = [_d(d) for d in dtes]

    def _put_row(strike, delta):
        return {
            "strike": strike,
            "bid": max(0.05, round((last - strike) * 0.04, 2) + 0.10),
            "ask": max(0.06, round((last - strike) * 0.04, 2) + 0.12),
            "delta": delta,
            "oi": 500,
            "volume": 100,
            "symbol": f"NVDA_P{strike}",
            "last": None,
            "iv": 0.45,
        }

    def _call_row(strike, delta):
        return {
            "strike": strike,
            "bid": max(0.05, round((strike - last) * 0.04, 2) + 0.10),
            "ask": max(0.06, round((strike - last) * 0.04, 2) + 0.12),
            "delta": delta,
            "oi": 500,
            "volume": 100,
            "symbol": f"NVDA_C{strike}",
            "last": None,
            "iv": 0.45,
        }

    puts_map = {}
    calls_map = {}
    for e in expirations:
        puts_map[e] = [
            _put_row(198, -0.42),
            _put_row(195, -0.30),
            _put_row(190, -0.20),
            _put_row(185, -0.12),
            _put_row(180, -0.06),
        ]
        calls_map[e] = [
            _call_row(202, 0.42),
            _call_row(205, 0.30),
            _call_row(210, 0.20),
            _call_row(215, 0.12),
            _call_row(220, 0.06),
        ]
    return {"calls": calls_map, "puts": puts_map, "expirations": expirations}


class TestSuggestStrategiesEndToEnd:
    def test_short_equity_csp_flow_excludes_ultra_short_dte(self):
        # User is short 1100 NVDA (matches the screenshot).
        positions = [{
            "symbol": "NVDA", "asset_type": "EQUITY",
            "quantity": -1100, "avg_price": -200.0, "market_value": -220000,
            "unrealized_pl": 0, "day_pl": 0,
        }]
        quote = {"last": 200.0}
        chain = _nvda_like_chain()

        out = suggest_strategies(positions, quote, chain, "NVDA")
        # No suggestion should be under our min DTE threshold.
        assert all(s.get("days_to_expiry", 999) >= 7 for s in out)
        # Total should respect the cap.
        assert len(out) <= 12
        # We should have at least one CSP (Cash-Secured Put).
        titles = {s["title"] for s in out}
        assert "Cash-Secured Put" in titles
        # Suggestions with annualized_yield should be sorted desc.
        ys = [s["annualized_yield"] for s in out if s.get("annualized_yield")]
        assert ys == sorted(ys, reverse=True)
        # Detail strings include DTE hint.
        assert all(" DTE" in s.get("detail", "") for s in out if s.get("days_to_expiry"))

    def test_long_equity_covered_call_flow(self):
        positions = [{
            "symbol": "NVDA", "asset_type": "EQUITY",
            "quantity": 200, "avg_price": 180.0, "market_value": 40000,
            "unrealized_pl": 4000, "day_pl": 0,
        }]
        quote = {"last": 200.0}
        chain = _nvda_like_chain()

        out = suggest_strategies(positions, quote, chain, "NVDA")
        assert all(s.get("days_to_expiry", 999) >= 7 for s in out)
        titles = {s["title"] for s in out}
        assert "Covered Call" in titles
        # Credit spread, if present, must have non-zero width.
        for s in out:
            if s["title"] == "Call Credit Spread":
                sell = next(l for l in s["legs"] if "SELL" in l["instruction"])
                buy = next(l for l in s["legs"] if "BUY" in l["instruction"])
                assert buy["strike"] > sell["strike"]

    def test_no_equity_yields_no_income_suggestions(self):
        quote = {"last": 200.0}
        chain = _nvda_like_chain()
        out = suggest_strategies([], quote, chain, "NVDA")
        # With no equity position and no existing options, nothing to suggest.
        assert out == []

    def test_empty_chain_returns_no_suggestions(self):
        positions = [{
            "symbol": "NVDA", "asset_type": "EQUITY",
            "quantity": -1100, "avg_price": -200.0, "market_value": -220000,
            "unrealized_pl": 0, "day_pl": 0,
        }]
        quote = {"last": 200.0}
        chain = {"calls": {}, "puts": {}, "expirations": []}
        out = suggest_strategies(positions, quote, chain, "NVDA")
        assert out == []

    def test_multiple_expirations_represented(self):
        # If the DTE bucketer is working, suggestions should be spread across
        # 2-3 distinct expirations, not all crammed onto one.
        positions = [{
            "symbol": "NVDA", "asset_type": "EQUITY",
            "quantity": -1100, "avg_price": -200.0, "market_value": -220000,
            "unrealized_pl": 0, "day_pl": 0,
        }]
        quote = {"last": 200.0}
        chain = _nvda_like_chain()

        out = suggest_strategies(positions, quote, chain, "NVDA")
        expiries = set()
        for s in out:
            for leg in s.get("legs", []):
                if leg.get("expiry"):
                    expiries.add(leg["expiry"])
        assert len(expiries) >= 2
