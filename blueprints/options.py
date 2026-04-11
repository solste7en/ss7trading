"""Option chain and strategy suggestion routes."""

import datetime
import logging

import schwab
from flask import Blueprint, jsonify, request

from auth import get_client
from db import suggest_position_unwind
from services.options import clean_option_map, suggest_strategies
from services.positions import clean_positions
from services.quotes import clean_quotes

log = logging.getLogger(__name__)
bp = Blueprint("options", __name__)


@bp.route("/api/ladder-suggest")
def api_ladder_suggest():
    """Analyse recent equity trades and suggest position-unwind ladder rungs."""
    try:
        ticker = request.args.get("ticker", "").strip().upper()
        if not ticker:
            return jsonify({"error": "ticker is required"}), 400
        window_size = max(2, min(20, int(request.args.get("window_size", 5))))
        sell_pct = max(1, min(100, int(request.args.get("sell_pct", 25)))) / 100.0
        premium_cents = max(1, min(99, int(request.args.get("premium_cents", 77))))
        min_streak = max(1, min(100, int(request.args.get("min_streak", 10))))
        max_rungs = max(1, min(20, int(request.args.get("max_rungs", 5))))
        return jsonify(suggest_position_unwind(
            ticker, window_size, sell_pct, premium_cents, min_streak, max_rungs))
    except Exception as e:
        log.exception("API error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/option-expirations/<symbol>")
def api_option_expirations(symbol):
    """Return available option expiration dates for a symbol."""
    try:
        client = get_client()
        resp = client.get_option_expiration_chain(symbol.upper())
        resp.raise_for_status()
        raw = resp.json() or {}
        expirations = []
        for item in raw.get("expirationList", []):
            date_str = item.get("expirationDate", "")
            if date_str:
                expirations.append(date_str[:10])
        return jsonify({"symbol": symbol.upper(), "expirations": sorted(expirations)})
    except Exception as e:
        log.exception("API error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/option-chain")
def api_option_chain():
    """Return the option chain for a symbol, simplified for the UI."""
    try:
        symbol = request.args.get("symbol", "").strip().upper()
        if not symbol:
            return jsonify({"error": "symbol is required"}), 400
        strike_count = min(100, max(5, int(request.args.get("strike_count", 15))))
        contract_type = request.args.get("contract_type", "ALL").upper()

        ct_map = {"ALL": None, "CALL": "CALL", "PUT": "PUT"}
        ct = ct_map.get(contract_type)

        client = get_client()
        kwargs = dict(
            symbol=symbol,
            strike_count=strike_count,
            include_underlying_quote=True,
        )
        if ct:
            kwargs["contract_type"] = ct

        from_date = request.args.get("from_date")
        to_date = request.args.get("to_date")
        if from_date:
            kwargs["from_date"] = datetime.date.fromisoformat(from_date)
        if to_date:
            kwargs["to_date"] = datetime.date.fromisoformat(to_date)

        resp = client.get_option_chain(**kwargs)
        resp.raise_for_status()
        raw = resp.json() or {}

        underlying = raw.get("underlying", {})
        underlying_clean = {
            "symbol": underlying.get("symbol", symbol),
            "last": underlying.get("last"),
            "bid": underlying.get("bid"),
            "ask": underlying.get("ask"),
            "change": underlying.get("change"),
            "change_pct": underlying.get("percentChange"),
            "volume": underlying.get("totalVolume"),
        }

        calls = clean_option_map(raw.get("callExpDateMap"))
        puts = clean_option_map(raw.get("putExpDateMap"))
        all_exps = sorted(set(list(calls.keys()) + list(puts.keys())))

        return jsonify({
            "symbol": symbol,
            "underlying": underlying_clean,
            "expirations": all_exps,
            "calls": calls,
            "puts": puts,
        })
    except Exception as e:
        log.exception("API error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/strategy-suggest")
def api_strategy_suggest():
    """Analyse positions and option chain, return strategy suggestions."""
    try:
        ticker = request.args.get("ticker", "").strip().upper()
        if not ticker:
            return jsonify({"error": "ticker is required"}), 400

        client = get_client()

        resp = client.get_accounts(fields=[schwab.client.Client.Account.Fields.POSITIONS])
        resp.raise_for_status()
        positions = clean_positions(resp.json())

        resp = client.get_quotes([ticker])
        resp.raise_for_status()
        quotes = clean_quotes(resp.json())
        quote = quotes[0] if quotes else {}

        try:
            resp = client.get_option_chain(
                symbol=ticker, strike_count=20, include_underlying_quote=True)
            resp.raise_for_status()
            raw = resp.json() or {}
            chain_data = {
                "calls": clean_option_map(raw.get("callExpDateMap")),
                "puts": clean_option_map(raw.get("putExpDateMap")),
                "expirations": sorted(set(
                    list(clean_option_map(raw.get("callExpDateMap")).keys()) +
                    list(clean_option_map(raw.get("putExpDateMap")).keys())
                )),
            }
        except Exception:
            chain_data = {"calls": {}, "puts": {}, "expirations": []}

        re_opt = ticker + " "
        eq_pos = [p for p in positions
                  if p["asset_type"] in ("EQUITY", "ETF") and p["symbol"] == ticker]
        opt_pos = [p for p in positions
                   if p["asset_type"] == "OPTION" and p["symbol"].startswith(re_opt)]

        eq_qty = sum(p["quantity"] for p in eq_pos)

        suggestions = suggest_strategies(positions, quote, chain_data, ticker)

        return jsonify({
            "ticker": ticker,
            "equity_qty": eq_qty,
            "quote": quote,
            "positions": eq_pos + opt_pos,
            "expirations": chain_data["expirations"],
            "suggestions": suggestions,
        })
    except Exception as e:
        log.exception("API error")
        return jsonify({"error": str(e)}), 500
