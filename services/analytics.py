"""Portfolio analytics — performance, exposure, concentration, consolidation."""

from collections import defaultdict


def compute_performance_series(history):
    """Build chart-ready series from balance snapshots (newest-first from DB).

    Returns {dates, equity, daily_pnl, drawdown_pct, cumulative_return_pct}.
    """
    if not history:
        return {"dates": [], "equity": [], "daily_pnl": [],
                "drawdown_pct": [], "cumulative_return_pct": []}

    rows = list(reversed(history))
    dates = [r["as_of_date"] for r in rows]
    equity = [r.get("liquidation_value") or r.get("equity") or 0 for r in rows]

    first = equity[0] if equity else 1
    if first == 0:
        first = 1

    daily_pnl = [0.0]
    for i in range(1, len(equity)):
        daily_pnl.append(round(equity[i] - equity[i - 1], 2))

    cum_ret = [round((e - first) / first * 100, 2) for e in equity]

    peak = equity[0]
    drawdown = []
    for e in equity:
        if e > peak:
            peak = e
        dd = round((e - peak) / peak * 100, 2) if peak else 0
        drawdown.append(dd)

    return {
        "dates": dates,
        "equity": equity,
        "daily_pnl": daily_pnl,
        "drawdown_pct": drawdown,
        "cumulative_return_pct": cum_ret,
    }


def compute_exposure(positions, sector_map):
    """Group positions by sector/industry.

    positions: list of cleaned position dicts (from clean_positions).
    sector_map: {SYMBOL: {sector, industry, ...}} from peers service.

    Returns {sectors: [{name, industry, market_value, pct, tickers}], total_value}.
    """
    by_sector = defaultdict(lambda: {"market_value": 0, "tickers": [], "industries": defaultdict(float)})
    total = 0
    for p in positions:
        if p.get("asset_type") not in ("EQUITY", "ETF"):
            continue
        mv = abs(p.get("market_value") or 0)
        sym = p["symbol"]
        info = sector_map.get(sym, {})
        sector = info.get("sector") or "Unknown"
        industry = info.get("industry") or "Unknown"
        by_sector[sector]["market_value"] += mv
        by_sector[sector]["tickers"].append(sym)
        by_sector[sector]["industries"][industry] += mv
        total += mv

    sectors = []
    for name, data in sorted(by_sector.items(), key=lambda x: -x[1]["market_value"]):
        industries = [
            {"name": ind, "market_value": round(val, 2)}
            for ind, val in sorted(data["industries"].items(), key=lambda x: -x[1])
        ]
        sectors.append({
            "name": name,
            "market_value": round(data["market_value"], 2),
            "pct": round(data["market_value"] / total * 100, 2) if total else 0,
            "tickers": sorted(set(data["tickers"])),
            "industries": industries,
        })

    return {"sectors": sectors, "total_value": round(total, 2)}


def compute_concentration(positions):
    """Top holdings + HHI concentration index.

    HHI ranges 0–10000: < 1500 = diversified, 1500–2500 = moderate, > 2500 = concentrated.
    """
    holdings = []
    total = 0
    for p in positions:
        if p.get("asset_type") not in ("EQUITY", "ETF"):
            continue
        mv = abs(p.get("market_value") or 0)
        holdings.append({"symbol": p["symbol"], "market_value": mv,
                         "unrealized_pl": p.get("unrealized_pl"),
                         "day_pl": p.get("day_pl")})
        total += mv

    holdings.sort(key=lambda h: -h["market_value"])
    for h in holdings:
        h["pct"] = round(h["market_value"] / total * 100, 2) if total else 0

    hhi = sum(h["pct"] ** 2 for h in holdings)
    hhi = round(hhi, 1)

    label = "Diversified" if hhi < 1500 else ("Moderate" if hhi < 2500 else "Concentrated")

    return {
        "holdings": holdings[:20],
        "total_value": round(total, 2),
        "hhi": hhi,
        "hhi_label": label,
        "total_positions": len(holdings),
    }


def find_overlap_groups(positions, sector_map):
    """Cluster positions by sector+industry to find overlapping holdings."""
    groups = defaultdict(list)
    for p in positions:
        if p.get("asset_type") not in ("EQUITY", "ETF"):
            continue
        sym = p["symbol"]
        info = sector_map.get(sym, {})
        sector = info.get("sector")
        industry = info.get("industry")
        if not sector:
            continue
        key = f"{sector}/{industry}" if industry else sector
        groups[key].append({
            "symbol": sym,
            "market_value": abs(p.get("market_value") or 0),
            "unrealized_pl": p.get("unrealized_pl"),
            "unrealized_pl_pct": _calc_pl_pct(p),
            "avg_price": p.get("avg_price"),
            "current_price": p.get("current_price"),
            "quantity": p.get("quantity"),
        })

    result = []
    for key, tickers in sorted(groups.items(), key=lambda x: -len(x[1])):
        if len(tickers) < 2:
            continue
        total_value = sum(t["market_value"] for t in tickers)
        result.append({
            "group": key,
            "count": len(tickers),
            "total_value": round(total_value, 2),
            "tickers": sorted(tickers, key=lambda t: -t["market_value"]),
        })

    return result


def score_consolidation_candidates(group_tickers, fundamentals):
    """Rank tickers within an overlap group on a composite score.

    Higher score = stronger "keep" candidate.
    Returns sorted list with scores and reasoning.
    """
    scored = []
    for t in group_tickers:
        sym = t["symbol"]
        info = fundamentals.get(sym, {})
        score = 0
        reasons = []

        revenue_growth = info.get("revenueGrowth")
        if revenue_growth is not None:
            growth_pts = min(max(revenue_growth * 100, -20), 40)
            score += growth_pts
            reasons.append(f"Revenue growth: {revenue_growth:.1%}")

        profit_margin = info.get("profitMargins")
        if profit_margin is not None:
            margin_pts = min(max(profit_margin * 50, -10), 25)
            score += margin_pts
            reasons.append(f"Profit margin: {profit_margin:.1%}")

        roe = info.get("returnOnEquity")
        if roe is not None:
            roe_pts = min(max(roe * 30, -10), 20)
            score += roe_pts
            reasons.append(f"ROE: {roe:.1%}")

        pct_high = info.get("pct_from_52w_high")
        if pct_high is not None:
            momentum_pts = max(min(pct_high + 20, 15), -15)
            score += momentum_pts
            reasons.append(f"From 52w high: {pct_high:+.1f}%")

        pl_pct = t.get("unrealized_pl_pct")
        if pl_pct is not None:
            position_pts = max(min(pl_pct / 2, 10), -10)
            score += position_pts
            reasons.append(f"Position P/L: {pl_pct:+.1f}%")

        scored.append({
            **t,
            "score": round(score, 1),
            "reasoning": reasons,
            "fundamentals": {
                "market_cap": info.get("marketCap"),
                "pe_ratio": info.get("trailingPE"),
                "forward_pe": info.get("forwardPE"),
                "revenue_growth": revenue_growth,
                "profit_margin": profit_margin,
                "roe": roe,
                "beta": info.get("beta"),
                "dividend_yield": info.get("dividendYield"),
                "pct_from_52w_high": pct_high,
            },
        })

    scored.sort(key=lambda x: -x["score"])
    if scored:
        scored[0]["recommendation"] = "keep"
        for s in scored[1:]:
            s["recommendation"] = "consolidate"

    return scored


def suggest_tax_loss_swaps(symbol, all_info, position_info=None):
    """Find peers suitable for tax-loss harvesting (same sector, different company)."""
    info = all_info.get(symbol.upper(), {})
    sector = info.get("sector")
    industry = info.get("industry")
    if not sector:
        return {"swaps": [], "wash_sale_warning": True}

    from services.peers import get_etf_alternatives
    swaps = []

    for sym, sym_info in all_info.items():
        if sym == symbol.upper():
            continue
        if sym_info.get("industry") == industry:
            swaps.append({
                "symbol": sym,
                "match": "industry",
                "name": sym_info.get("shortName") or sym_info.get("longName") or sym,
                "market_cap": sym_info.get("marketCap"),
            })

    etfs = get_etf_alternatives(sector, industry)
    for etf in etfs:
        swaps.append({
            "symbol": etf,
            "match": "etf",
            "name": f"{sector} ETF",
            "market_cap": None,
        })

    return {
        "swaps": swaps[:10],
        "wash_sale_warning": True,
        "wash_sale_note": (
            "IRS wash sale rule: you cannot claim a tax loss if you buy a "
            "'substantially identical' security within 30 days before or after the sale. "
            "ETFs in the same sector are generally considered different enough, but "
            "consult your tax advisor."
        ),
    }


def _calc_pl_pct(position):
    """Calculate unrealized P/L % from position data."""
    avg = position.get("avg_price")
    cur = position.get("current_price")
    if avg and cur and avg != 0:
        return round((cur - abs(avg)) / abs(avg) * 100, 2)
    return None
