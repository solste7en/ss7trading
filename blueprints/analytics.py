"""Portfolio analytics API — performance, exposure, concentration, consolidation."""

import logging

import schwab
from flask import Blueprint, jsonify, request

from core.auth import get_client
from core.db import (
    get_all_position_assignments,
    get_balance_history,
    get_income_stats,
    get_income_trades,
    get_position_lists,
    get_watchlist_symbols_batch,
    get_watchlists,
)
from services.analytics import (
    compute_concentration,
    compute_exposure,
    compute_performance_series,
    find_overlap_groups,
    score_consolidation_candidates,
    suggest_tax_loss_swaps,
)
from services.options import suggest_underwater_strategies
from services.peers import get_etf_alternatives, get_peers, get_ticker_info, get_ticker_info_batch
from services.positions import clean_positions
from services.quotes import clean_quotes

log = logging.getLogger(__name__)
bp = Blueprint("analytics", __name__)


def _fetch_positions():
    """Shared helper: fetch + clean current positions from Schwab."""
    client = get_client()
    resp = client.get_accounts(fields=[schwab.client.Client.Account.Fields.POSITIONS])
    resp.raise_for_status()
    return clean_positions(resp.json())


def _equity_symbols(positions):
    return list({p["symbol"] for p in positions if p.get("asset_type") in ("EQUITY", "ETF")})


@bp.route("/api/analytics/performance")
def api_analytics_performance():
    """Balance history formatted for charting."""
    try:
        limit = request.args.get("days", 90, type=int)
        history = get_balance_history(limit_days=limit)
        series = compute_performance_series(history)
        return jsonify(series)
    except Exception as e:
        log.exception("Analytics performance error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/analytics/exposure")
def api_analytics_exposure():
    """Sector/industry breakdown of current positions."""
    try:
        positions = _fetch_positions()
        symbols = _equity_symbols(positions)
        sector_map = get_ticker_info_batch(symbols)
        result = compute_exposure(positions, sector_map)
        return jsonify(result)
    except Exception as e:
        log.exception("Analytics exposure error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/analytics/concentration")
def api_analytics_concentration():
    """Top holdings and HHI concentration score."""
    try:
        positions = _fetch_positions()
        result = compute_concentration(positions)
        return jsonify(result)
    except Exception as e:
        log.exception("Analytics concentration error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/analytics/income-summary")
def api_analytics_income_summary():
    """Income P&L stats reformatted for charts."""
    try:
        stats = get_income_stats()
        trades_data = get_income_trades(page=1, limit=1000)
        trades = trades_data.get("data", [])

        monthly = {}
        strategy_breakdown = {}
        for t in trades:
            if t.get("status") == "open":
                continue
            date = t.get("close_date") or t.get("open_date") or ""
            month = date[:7] if len(date) >= 7 else "unknown"
            pnl = t.get("net_pnl") or 0
            monthly[month] = round(monthly.get(month, 0) + pnl, 2)

            strat = t.get("strategy") or "unknown"
            if strat not in strategy_breakdown:
                strategy_breakdown[strat] = {"count": 0, "pnl": 0, "wins": 0}
            strategy_breakdown[strat]["count"] += 1
            strategy_breakdown[strat]["pnl"] = round(strategy_breakdown[strat]["pnl"] + pnl, 2)
            if t.get("is_win"):
                strategy_breakdown[strat]["wins"] += 1

        for _strat, data in strategy_breakdown.items():
            data["win_rate"] = round(data["wins"] / data["count"] * 100, 1) if data["count"] else 0

        months_sorted = sorted(monthly.keys())
        return jsonify({
            "stats": stats,
            "monthly_pnl": {"months": months_sorted, "values": [monthly[m] for m in months_sorted]},
            "strategy_breakdown": strategy_breakdown,
        })
    except Exception as e:
        log.exception("Analytics income summary error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/analytics/consolidation-lists")
def api_analytics_consolidation_lists():
    """Return position lists + watchlists for the consolidation list selector."""
    try:
        pos_lists = get_position_lists()
        watchlists = get_watchlists()

        positions = _fetch_positions()
        assignments = get_all_position_assignments()

        symbols_by_list = {}
        for p_list in pos_lists:
            symbols_by_list[p_list["id"]] = []

        for p in positions:
            if p.get("asset_type") not in ("EQUITY", "ETF"):
                continue
            sym = p["symbol"]
            lid = assignments.get(sym)
            if lid and lid in symbols_by_list:
                symbols_by_list[lid].append({
                    "symbol": sym,
                    "market_value": round(abs(p.get("market_value") or 0), 2),
                    "unrealized_pl": p.get("unrealized_pl"),
                    "avg_price": p.get("avg_price"),
                    "current_price": p.get("current_price"),
                    "quantity": p.get("quantity"),
                    "asset_type": p.get("asset_type"),
                })

        result_lists = []
        for pl in pos_lists:
            tickers = symbols_by_list.get(pl["id"], [])
            result_lists.append({
                "id": f"pos_{pl['id']}",
                "name": pl["name"],
                "type": "position",
                "tickers": tickers,
            })

        pos_by_sym = {}
        for p in positions:
            if p.get("asset_type") in ("EQUITY", "ETF"):
                pos_by_sym[p["symbol"]] = p

        wl_symbols_by_id = get_watchlist_symbols_batch([wl["id"] for wl in watchlists])
        for wl in watchlists:
            syms = wl_symbols_by_id.get(wl["id"], [])
            wl_tickers = []
            for s in syms:
                held = pos_by_sym.get(s)
                if held:
                    wl_tickers.append({
                        "symbol": s,
                        "market_value": round(abs(held.get("market_value") or 0), 2),
                        "unrealized_pl": held.get("unrealized_pl"),
                        "avg_price": held.get("avg_price"),
                        "current_price": held.get("current_price"),
                        "quantity": held.get("quantity"),
                        "asset_type": held.get("asset_type"),
                    })
                else:
                    wl_tickers.append({
                        "symbol": s,
                        "market_value": 0,
                        "unrealized_pl": None,
                        "avg_price": None,
                        "current_price": None,
                        "quantity": 0,
                        "asset_type": None,
                    })
            result_lists.append({
                "id": f"wl_{wl['id']}",
                "name": wl["name"],
                "type": "watchlist",
                "tickers": wl_tickers,
            })

        return jsonify({"lists": result_lists})
    except Exception as e:
        log.exception("Consolidation lists error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/analytics/consolidation")
def api_analytics_consolidation():
    """Overlap groups, rankings, and ETF alternatives."""
    try:
        positions = _fetch_positions()
        symbols = _equity_symbols(positions)
        sector_map = get_ticker_info_batch(symbols)
        groups = find_overlap_groups(positions, sector_map)

        for g in groups:
            g["scored"] = score_consolidation_candidates(g["tickers"], sector_map)
            parts = g["group"].split("/")
            sector = parts[0]
            industry = parts[1] if len(parts) > 1 else None
            g["etf_alternatives"] = get_etf_alternatives(sector, industry)

        underwater = []
        for p in positions:
            if p.get("asset_type") not in ("EQUITY", "ETF"):
                continue
            upl = p.get("unrealized_pl")
            if upl is not None and upl < 0:
                sym = p["symbol"]
                info = sector_map.get(sym, {})
                underwater.append({
                    "symbol": sym,
                    "unrealized_pl": round(upl, 2),
                    "market_value": round(abs(p.get("market_value") or 0), 2),
                    "avg_price": p.get("avg_price"),
                    "current_price": p.get("current_price"),
                    "quantity": p.get("quantity"),
                    "sector": info.get("sector"),
                    "industry": info.get("industry"),
                })
        underwater.sort(key=lambda x: x["unrealized_pl"])

        return jsonify({
            "groups": groups,
            "underwater": underwater,
            "total_positions": len(symbols),
        })
    except Exception as e:
        log.exception("Analytics consolidation error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/analytics/consolidation/<symbol>")
def api_analytics_consolidation_detail(symbol):
    """Deep dive on one ticker: peer comparison, options strategies, ETF swaps."""
    try:
        symbol = symbol.upper().strip()
        positions = _fetch_positions()
        symbols = _equity_symbols(positions)
        sector_map = get_ticker_info_batch(symbols)

        peer_data = get_peers(symbol, sector_map)

        target_pos = None
        for p in positions:
            if p["symbol"] == symbol and p.get("asset_type") in ("EQUITY", "ETF"):
                target_pos = p
                break

        # Single batch call for target + peers + ETF alternatives. ETF list
        # depends on target sector/industry, so resolve target_info from the
        # portfolio sector_map first when possible to avoid an extra round trip.
        target_info = sector_map.get(symbol) or get_ticker_info(symbol)
        sector = target_info.get("sector")
        industry = target_info.get("industry")
        etfs = get_etf_alternatives(sector, industry) if sector else []

        peer_symbols = [pr["symbol"] for pr in peer_data.get("peers", [])]
        batch_symbols = list({symbol, *peer_symbols, *etfs})
        batch_info = get_ticker_info_batch(batch_symbols) if batch_symbols else {}
        target_info = batch_info.get(symbol) or target_info
        peer_fundamentals = {sym: batch_info.get(sym, {}) for sym in peer_symbols}
        peer_fundamentals[symbol] = target_info
        etf_info = {e: batch_info.get(e, {}) for e in etfs}

        all_tickers = [{"symbol": symbol, "market_value": abs((target_pos or {}).get("market_value") or 0),
                        "unrealized_pl": (target_pos or {}).get("unrealized_pl"),
                        "unrealized_pl_pct": _calc_detail_pl_pct(target_pos),
                        "avg_price": (target_pos or {}).get("avg_price"),
                        "current_price": (target_pos or {}).get("current_price"),
                        "quantity": (target_pos or {}).get("quantity")}]
        for pr in peer_data.get("peers", []):
            psym = pr["symbol"]
            pp = next((p for p in positions if p["symbol"] == psym), None)
            all_tickers.append({
                "symbol": psym,
                "market_value": abs((pp or {}).get("market_value") or 0),
                "unrealized_pl": (pp or {}).get("unrealized_pl"),
                "unrealized_pl_pct": _calc_detail_pl_pct(pp) if pp else None,
                "avg_price": (pp or {}).get("avg_price"),
                "current_price": (pp or {}).get("current_price"),
                "quantity": (pp or {}).get("quantity"),
                "in_portfolio": pp is not None,
            })

        scored = score_consolidation_candidates(all_tickers, peer_fundamentals)
        tax_swaps = suggest_tax_loss_swaps(symbol, {**sector_map, **peer_fundamentals}, target_pos)

        return jsonify({
            "symbol": symbol,
            "info": target_info,
            "position": {
                "quantity": (target_pos or {}).get("quantity"),
                "avg_price": (target_pos or {}).get("avg_price"),
                "current_price": (target_pos or {}).get("current_price"),
                "market_value": abs((target_pos or {}).get("market_value") or 0),
                "unrealized_pl": (target_pos or {}).get("unrealized_pl"),
            } if target_pos else None,
            "peer_data": peer_data,
            "scored_peers": scored,
            "tax_loss_swaps": tax_swaps,
            "etf_alternatives": [
                {"symbol": e, "info": etf_info.get(e, {})} for e in etfs
            ],
        })
    except Exception as e:
        log.exception("Analytics consolidation detail error")
        return jsonify({"error": str(e)}), 500


@bp.route("/api/analytics/underwater-strategies/<symbol>")
def api_analytics_underwater_strategies(symbol):
    """Options strategies for an underwater position."""
    try:
        symbol = symbol.upper().strip()
        positions = _fetch_positions()
        symbols = _equity_symbols(positions)
        sector_map = get_ticker_info_batch(symbols)
        peer_data = get_peers(symbol, sector_map)

        client = get_client()
        quote_resp = client.get_quotes([symbol])
        quote_resp.raise_for_status()
        quotes = clean_quotes(quote_resp.json())
        quote = quotes[0] if quotes else {}

        chain_data = {"expirations": [], "calls": {}, "puts": {}}
        try:
            from services.options import clean_option_map
            exp_resp = client.get_option_expiration_chain(symbol)
            exp_resp.raise_for_status()
            exps_raw = exp_resp.json().get("expirationList", [])
            expirations = sorted({
                e.get("expirationDate") for e in exps_raw
                if e.get("expirationDate")
            })
            chain_data["expirations"] = expirations

            for exp in expirations[:3]:
                chain_resp = client.get_option_chain(
                    symbol, from_date=exp, to_date=exp,
                    contract_type=schwab.client.Client.Options.ContractType.CALL,
                )
                chain_resp.raise_for_status()
                raw_chain = chain_resp.json()
                calls = clean_option_map(raw_chain.get("callExpDateMap", {}))
                chain_data["calls"].update(calls)
        except Exception as e:
            log.warning("Chain fetch failed for %s: %s", symbol, e)

        strategies = suggest_underwater_strategies(
            positions, quote, chain_data, symbol, peer_data
        )
        return jsonify({"symbol": symbol, "strategies": strategies})
    except Exception as e:
        log.exception("Underwater strategies error")
        return jsonify({"error": str(e)}), 500


def _calc_detail_pl_pct(position):
    if not position:
        return None
    avg = position.get("avg_price")
    cur = position.get("current_price")
    if avg and cur and avg != 0:
        return round((cur - abs(avg)) / abs(avg) * 100, 2)
    return None
