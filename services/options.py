"""Option chain cleaning and strategy suggestion engine."""

import datetime
import re

# Income-strategy suggester tuning. These defaults target the "theta sweet spot"
# (21-45 DTE) while still offering a shorter-dated option for users who want it.
_DTE_TARGETS = (14, 30, 45)
_MIN_DTE = 7
_DELTA_TARGETS = (0.30, 0.20, 0.15)
_PCT_OTM_TARGETS = (0.03, 0.05, 0.08)
_MAX_SUGGESTIONS = 12


def clean_option_map(exp_date_map):
    """Flatten Schwab's nested {expiryDate: {strike: [contracts]}} into a simple list."""
    result = {}
    for exp_key, strikes in (exp_date_map or {}).items():
        exp_date = exp_key.split(":")[0]
        contracts = []
        for _strike_key, chain_items in strikes.items():
            for c in chain_items:
                contracts.append({
                    "strike": c.get("strikePrice"),
                    "bid": c.get("bid"),
                    "ask": c.get("ask"),
                    "last": c.get("last"),
                    "volume": c.get("totalVolume", 0),
                    "oi": c.get("openInterest", 0),
                    "iv": c.get("volatility"),
                    "delta": c.get("delta"),
                    "symbol": c.get("symbol", ""),
                    "itm": c.get("inTheMoney", False),
                    "description": c.get("description", ""),
                })
        contracts.sort(key=lambda x: x["strike"] or 0)
        result[exp_date] = contracts
    return result


def _select_expirations(all_exps, targets=_DTE_TARGETS, min_dte=_MIN_DTE,
                        today=None):
    """Pick one expiry closest to each target DTE (deduped), skipping <min_dte.

    Falls back to whatever is available when the chain is sparse, so we always
    return at least one expiry when the input is non-empty. Output is sorted
    chronologically.
    """
    if not all_exps:
        return []
    today = today or datetime.date.today()

    dated = []
    for e in all_exps:
        try:
            d = datetime.date.fromisoformat(e)
        except (TypeError, ValueError):
            continue
        dated.append((e, (d - today).days))

    if not dated:
        return []

    eligible = [(e, dte) for e, dte in dated if dte >= min_dte]
    pool = eligible or dated

    picked = set()
    for target in targets:
        best = min(pool, key=lambda x: abs(x[1] - target))
        picked.add(best[0])

    return sorted(picked)


def _pick_short_strikes(contracts, last, side, n=3,
                        delta_targets=_DELTA_TARGETS,
                        pct_targets=_PCT_OTM_TARGETS):
    """Pick up to *n* short-leg candidates from *contracts*.

    *side* is ``"put"`` or ``"call"``. Prefers delta-targeted selection when
    Schwab populated delta on the contracts; otherwise falls back to
    percent-OTM-targeted selection. Always OTM, always bid > 0.05. Prefers
    liquid contracts (oi or volume > 0) but relaxes the filter per-bucket if
    nothing liquid matches.
    """
    if not contracts or not last:
        return []

    if side == "put":
        otm = [c for c in contracts if c.get("strike") and c["strike"] < last]
    else:
        otm = [c for c in contracts if c.get("strike") and c["strike"] > last]

    otm = [c for c in otm if (c.get("bid") or 0) > 0.05]
    if not otm:
        return []

    liquid = [c for c in otm if (c.get("oi") or 0) > 0 or (c.get("volume") or 0) > 0]

    has_delta = any(
        c.get("delta") is not None and abs(c.get("delta") or 0) > 0.001
        for c in otm
    )

    if has_delta:
        def _dist(c, target):
            d = c.get("delta")
            if d is None:
                return float("inf")
            return abs(abs(d) - target)
        targets = delta_targets
        dist_fn = _dist
    else:
        def _dist(c, target):
            strike = c.get("strike") or 0
            pct = abs(strike - last) / last if last else float("inf")
            return abs(pct - target)
        targets = pct_targets
        dist_fn = _dist

    picked_syms = set()
    result = []
    for target in targets[:n]:
        pool = liquid or otm
        ranked = sorted(pool, key=lambda c: dist_fn(c, target))
        if liquid and ranked and dist_fn(ranked[0], target) == float("inf"):
            ranked = sorted(otm, key=lambda c: dist_fn(c, target))
        for c in ranked:
            sym = c.get("symbol") or (c.get("strike"),)
            if sym in picked_syms:
                continue
            picked_syms.add(sym)
            result.append(c)
            break

    return result


def _find_long_leg_by_width(contracts, short_strike, target_width, side):
    """Find the long-leg contract closest to short_strike ± target_width.

    *side* is ``"put"`` (long leg strike < short strike) or ``"call"`` (long
    leg strike > short strike). Requires ask > 0. Returns None if nothing
    qualifies.
    """
    if side == "put":
        candidates = [c for c in contracts
                      if c.get("strike") and c["strike"] < short_strike
                      and (c.get("ask") or 0) > 0]
        ideal = short_strike - target_width
    else:
        candidates = [c for c in contracts
                      if c.get("strike") and c["strike"] > short_strike
                      and (c.get("ask") or 0) > 0]
        ideal = short_strike + target_width

    if not candidates:
        return None
    return min(candidates, key=lambda c: abs(c["strike"] - ideal))


def suggest_strategies(positions, quote, chain_data, ticker):
    """Analyse positions + option chain and return strategy suggestions."""
    suggestions = []
    last = quote.get("last")
    if last is None:
        return suggestions

    eq_pos = [p for p in positions
              if p["asset_type"] in ("EQUITY", "ETF") and p["symbol"] == ticker]
    re_opt = ticker + " "
    opt_pos = [p for p in positions
               if p["asset_type"] == "OPTION" and p["symbol"].startswith(re_opt)]
    eq_qty = sum(p["quantity"] for p in eq_pos)
    avg_price = None
    if eq_pos and eq_pos[0].get("avg_price") is not None:
        avg_price = eq_pos[0]["avg_price"]

    exps = sorted(chain_data.get("expirations", []))
    calls_map = chain_data.get("calls", {})
    puts_map = chain_data.get("puts", {})

    exps_with_data = [e for e in exps if (calls_map.get(e) or puts_map.get(e))]
    usable_exps = _select_expirations(exps_with_data)

    for expiry in usable_exps:
        calls = calls_map.get(expiry, [])
        puts = puts_map.get(expiry, [])
        days_to_exp = max(1, (datetime.date.fromisoformat(expiry) - datetime.date.today()).days)

        if eq_qty > 0:
            coverable = abs(eq_qty) // 100
            if coverable < 1:
                continue

            # Covered Call — delta/pct-targeted short strikes
            cc_candidates = _pick_short_strikes(calls, last, "call")
            for c in cc_candidates:
                premium = c["bid"]
                total_prem = premium * coverable * 100
                ann_yield = (premium / last) * (365 / days_to_exp)
                occ = c.get("symbol", "")
                suggestions.append({
                    "id": f"cc_{expiry}_{c['strike']}",
                    "strategy": "naked",
                    "title": "Covered Call",
                    "description": f"Sell {coverable} CALL @ ${c['strike']:.2f}, {expiry}",
                    "detail": (f"Premium ~${premium:.2f}/sh (${total_prem:,.0f} total) · "
                               f"{ann_yield:.0%} ann. yield · {days_to_exp} DTE"),
                    "legs": [{
                        "type": "option", "instruction": "SELL_TO_OPEN",
                        "option_type": "CALL", "strike": c["strike"],
                        "expiry": expiry, "quantity": coverable,
                        "symbol": occ, "est_premium": premium,
                    }],
                    "order_type": "LIMIT",
                    "price": premium,
                    "net_credit": total_prem,
                    "max_profit": (c["strike"] - last) * abs(eq_qty) + total_prem if avg_price else None,
                    "breakeven": round(last - premium, 2),
                    "annualized_yield": round(ann_yield, 4),
                    "days_to_expiry": days_to_exp,
                })

            # Protective Collar
            pp_candidates = _pick_short_strikes(puts, last, "put")
            if cc_candidates and pp_candidates:
                best_collar = None
                best_net = 999
                for cc in cc_candidates:
                    for pp in pp_candidates:
                        if not pp.get("ask"):
                            continue
                        net = pp["ask"] - cc["bid"]
                        if abs(net) < best_net:
                            best_net = abs(net)
                            best_collar = (cc, pp, net)
                if best_collar:
                    cc, pp, net = best_collar
                    net_total = net * coverable * 100
                    ot = "NET_DEBIT" if net > 0.005 else ("NET_CREDIT" if net < -0.005 else "NET_ZERO")
                    suggestions.append({
                        "id": f"collar_{expiry}_{cc['strike']}_{pp['strike']}",
                        "strategy": "collar",
                        "title": "Protective Collar",
                        "description": f"Sell {coverable} CALL @ ${cc['strike']:.2f} + Buy {coverable} PUT @ ${pp['strike']:.2f}, {expiry}",
                        "detail": (f"Net {'credit' if net < 0 else 'debit'}: ${abs(net):.2f}/sh "
                                   f"(${abs(net_total):,.0f} total) · "
                                   f"Protection below ${pp['strike']:.2f} · {days_to_exp} DTE"),
                        "legs": [
                            {"type": "option", "instruction": "SELL_TO_OPEN",
                             "option_type": "CALL", "strike": cc["strike"],
                             "expiry": expiry, "quantity": coverable,
                             "symbol": cc.get("symbol", ""), "est_premium": cc["bid"]},
                            {"type": "option", "instruction": "BUY_TO_OPEN",
                             "option_type": "PUT", "strike": pp["strike"],
                             "expiry": expiry, "quantity": coverable,
                             "symbol": pp.get("symbol", ""), "est_premium": pp["ask"]},
                        ],
                        "order_type": ot,
                        "price": round(abs(net), 2),
                        "net_credit": round(-net_total, 2),
                        "breakeven": round(last + net, 2),
                        "days_to_expiry": days_to_exp,
                    })

            # Call Credit Spread — fixed-width, not arbitrary-index
            if cc_candidates:
                target_width = max(2.5, round(last * 0.025))
                sell_c = cc_candidates[0]
                buy_c = _find_long_leg_by_width(calls, sell_c["strike"], target_width, "call")
                if buy_c and sell_c["bid"] and buy_c["ask"] and sell_c["bid"] > buy_c["ask"]:
                    net_cr = sell_c["bid"] - buy_c["ask"]
                    width = buy_c["strike"] - sell_c["strike"]
                    ann_yield = (net_cr / last) * (365 / days_to_exp) if last else 0
                    suggestions.append({
                        "id": f"spread_{expiry}_{sell_c['strike']}_{buy_c['strike']}",
                        "strategy": "vertical",
                        "title": "Call Credit Spread",
                        "description": f"Sell {coverable} CALL @ ${sell_c['strike']:.2f} + Buy {coverable} CALL @ ${buy_c['strike']:.2f}, {expiry}",
                        "detail": (f"Net credit: ${net_cr:.2f}/sh · "
                                   f"Width ${width:.2f} · Max risk: ${width - net_cr:.2f}/sh · "
                                   f"{days_to_exp} DTE"),
                        "legs": [
                            {"type": "option", "instruction": "SELL_TO_OPEN",
                             "option_type": "CALL", "strike": sell_c["strike"],
                             "expiry": expiry, "quantity": coverable,
                             "symbol": sell_c.get("symbol", ""), "est_premium": sell_c["bid"]},
                            {"type": "option", "instruction": "BUY_TO_OPEN",
                             "option_type": "CALL", "strike": buy_c["strike"],
                             "expiry": expiry, "quantity": coverable,
                             "symbol": buy_c.get("symbol", ""), "est_premium": buy_c["ask"]},
                        ],
                        "order_type": "NET_CREDIT",
                        "price": round(net_cr, 2),
                        "net_credit": round(net_cr * coverable * 100, 2),
                        "max_loss": round((width - net_cr) * coverable * 100, 2),
                        "annualized_yield": round(ann_yield, 4),
                        "days_to_expiry": days_to_exp,
                    })

        elif eq_qty < 0:
            coverable = abs(eq_qty) // 100
            if coverable < 1:
                continue

            # Cash-Secured Put — delta/pct-targeted short strikes
            csp_candidates = _pick_short_strikes(puts, last, "put")
            for p in csp_candidates:
                premium = p["bid"]
                total_prem = premium * coverable * 100
                ann_yield = (premium / last) * (365 / days_to_exp)
                suggestions.append({
                    "id": f"csp_{expiry}_{p['strike']}",
                    "strategy": "naked",
                    "title": "Cash-Secured Put",
                    "description": f"Sell {coverable} PUT @ ${p['strike']:.2f}, {expiry}",
                    "detail": (f"Premium ~${premium:.2f}/sh (${total_prem:,.0f} total) · "
                               f"{ann_yield:.0%} ann. yield · {days_to_exp} DTE"),
                    "legs": [{
                        "type": "option", "instruction": "SELL_TO_OPEN",
                        "option_type": "PUT", "strike": p["strike"],
                        "expiry": expiry, "quantity": coverable,
                        "symbol": p.get("symbol", ""), "est_premium": premium,
                    }],
                    "order_type": "LIMIT",
                    "price": premium,
                    "net_credit": total_prem,
                    "breakeven": round(p["strike"] - premium, 2),
                    "annualized_yield": round(ann_yield, 4),
                    "days_to_expiry": days_to_exp,
                })

            # Short Collar
            cc_candidates = _pick_short_strikes(calls, last, "call")
            if csp_candidates and cc_candidates:
                best_collar = None
                best_net = 999
                for pp in csp_candidates:
                    for cc in cc_candidates:
                        if not cc.get("ask"):
                            continue
                        net = cc["ask"] - pp["bid"]
                        if abs(net) < best_net:
                            best_net = abs(net)
                            best_collar = (pp, cc, net)
                if best_collar:
                    pp, cc, net = best_collar
                    net_total = net * coverable * 100
                    ot = "NET_DEBIT" if net > 0.005 else ("NET_CREDIT" if net < -0.005 else "NET_ZERO")
                    suggestions.append({
                        "id": f"scollar_{expiry}_{pp['strike']}_{cc['strike']}",
                        "strategy": "collar",
                        "title": "Short Collar",
                        "description": f"Sell {coverable} PUT @ ${pp['strike']:.2f} + Buy {coverable} CALL @ ${cc['strike']:.2f}, {expiry}",
                        "detail": (f"Net {'debit' if net > 0 else 'credit'}: ${abs(net):.2f}/sh · "
                                   f"Upside protection above ${cc['strike']:.2f} · {days_to_exp} DTE"),
                        "legs": [
                            {"type": "option", "instruction": "SELL_TO_OPEN",
                             "option_type": "PUT", "strike": pp["strike"],
                             "expiry": expiry, "quantity": coverable,
                             "symbol": pp.get("symbol", ""), "est_premium": pp["bid"]},
                            {"type": "option", "instruction": "BUY_TO_OPEN",
                             "option_type": "CALL", "strike": cc["strike"],
                             "expiry": expiry, "quantity": coverable,
                             "symbol": cc.get("symbol", ""), "est_premium": cc["ask"]},
                        ],
                        "order_type": ot,
                        "price": round(abs(net), 2),
                        "net_credit": round(-net_total, 2),
                        "days_to_expiry": days_to_exp,
                    })

            # Put Credit Spread — fixed-width, not arbitrary-index
            if csp_candidates:
                target_width = max(2.5, round(last * 0.025))
                sell_p = csp_candidates[0]
                buy_p = _find_long_leg_by_width(puts, sell_p["strike"], target_width, "put")
                if buy_p and sell_p["bid"] and buy_p["ask"] and sell_p["bid"] > buy_p["ask"]:
                    net_cr = sell_p["bid"] - buy_p["ask"]
                    width = sell_p["strike"] - buy_p["strike"]
                    ann_yield = (net_cr / last) * (365 / days_to_exp) if last else 0
                    suggestions.append({
                        "id": f"pspread_{expiry}_{sell_p['strike']}_{buy_p['strike']}",
                        "strategy": "vertical",
                        "title": "Put Credit Spread",
                        "description": f"Sell {coverable} PUT @ ${sell_p['strike']:.2f} + Buy {coverable} PUT @ ${buy_p['strike']:.2f}, {expiry}",
                        "detail": (f"Net credit: ${net_cr:.2f}/sh · "
                                   f"Width ${width:.2f} · Max risk: ${width - net_cr:.2f}/sh · "
                                   f"{days_to_exp} DTE"),
                        "legs": [
                            {"type": "option", "instruction": "SELL_TO_OPEN",
                             "option_type": "PUT", "strike": sell_p["strike"],
                             "expiry": expiry, "quantity": coverable,
                             "symbol": sell_p.get("symbol", ""), "est_premium": sell_p["bid"]},
                            {"type": "option", "instruction": "BUY_TO_OPEN",
                             "option_type": "PUT", "strike": buy_p["strike"],
                             "expiry": expiry, "quantity": coverable,
                             "symbol": buy_p.get("symbol", ""), "est_premium": buy_p["ask"]},
                        ],
                        "order_type": "NET_CREDIT",
                        "price": round(net_cr, 2),
                        "net_credit": round(net_cr * coverable * 100, 2),
                        "max_loss": round((width - net_cr) * coverable * 100, 2),
                        "annualized_yield": round(ann_yield, 4),
                        "days_to_expiry": days_to_exp,
                    })

    # Sort by annualized yield (desc) so the highest-efficiency ideas rise to
    # the top and short DTE can no longer dominate by being listed first.
    # Suggestions without a yield (e.g. collars) keep their insertion order
    # relative to yielded suggestions but sink below them.
    suggestions.sort(key=lambda s: -(s.get("annualized_yield") or 0))
    if len(suggestions) > _MAX_SUGGESTIONS:
        suggestions = suggestions[:_MAX_SUGGESTIONS]

    # Existing short option positions — suggest roll / close
    for p in opt_pos:
        if p["quantity"] < 0:
            desc = p.get("description", "")
            m = desc and re.match(
                r'.*?(\d{2}/\d{2}/\d{4})\s+\$([0-9.]+)\s+(Put|Call)', desc, re.IGNORECASE)
            if m:
                suggestions.append({
                    "id": f"close_{p['symbol'][:20]}",
                    "strategy": "naked",
                    "title": f"Close Short {m.group(3).upper()}",
                    "description": f"Buy to close {abs(p['quantity'])} {m.group(3).upper()} @ ${m.group(2)}, exp {m.group(1)}",
                    "detail": f"Current value: {p.get('current_price', '?')}/sh",
                    "legs": [],
                    "order_type": "LIMIT",
                    "price": p.get("current_price"),
                    "is_close": True,
                })

    return suggestions


def suggest_underwater_strategies(positions, quote, chain_data, ticker, peer_data=None):
    """Generate strategy suggestions specifically for underwater equity positions.

    Focuses on:
    - Covered calls near cost basis to generate income while waiting for recovery
    - Comparison of "hold + sell calls" vs "sell now" tax-loss economics
    - ETF swap suggestions from peer_data
    """
    suggestions = []
    last = quote.get("last")
    if last is None:
        return suggestions

    eq_pos = [p for p in positions
              if p["asset_type"] in ("EQUITY", "ETF") and p["symbol"] == ticker]
    if not eq_pos:
        return suggestions

    eq_qty = sum(p["quantity"] for p in eq_pos)
    if eq_qty <= 0:
        return suggestions

    avg_price = eq_pos[0].get("avg_price")
    if avg_price is None or last >= avg_price:
        return suggestions

    unrealized_pl = sum(p.get("unrealized_pl") or 0 for p in eq_pos)
    coverable = abs(eq_qty) // 100
    if coverable < 1:
        return suggestions

    exps = sorted(chain_data.get("expirations", []))
    calls_map = chain_data.get("calls", {})
    usable_exps = [e for e in exps if calls_map.get(e)][:3]

    for expiry in usable_exps:
        calls = calls_map.get(expiry, [])
        days_to_exp = max(1, (datetime.date.fromisoformat(expiry) - datetime.date.today()).days)

        near_cost_calls = [c for c in calls
                           if c["strike"] and c["bid"] and c["bid"] > 0.05
                           and abs(c["strike"] - avg_price) / avg_price < 0.10]
        near_cost_calls.sort(key=lambda c: abs(c["strike"] - avg_price))

        for c in near_cost_calls[:2]:
            premium = c["bid"]
            total_prem = premium * coverable * 100
            ann_yield = (premium / last) * (365 / days_to_exp)
            months_to_breakeven = abs(unrealized_pl) / total_prem if total_prem > 0 else float("inf")

            suggestions.append({
                "id": f"uw_cc_cost_{expiry}_{c['strike']}",
                "strategy": "underwater_covered_call",
                "title": f"CC Near Cost Basis @ ${c['strike']:.2f}",
                "description": (f"Sell {coverable} CALL @ ${c['strike']:.2f}, {expiry} "
                                f"(your avg cost: ${avg_price:.2f})"),
                "detail": (f"Premium: ${premium:.2f}/sh (${total_prem:,.0f} total) · "
                           f"{ann_yield:.0%} ann. yield · "
                           f"~{months_to_breakeven:.0f} cycles to recover ${abs(unrealized_pl):,.0f} loss"),
                "premium_per_share": premium,
                "total_premium": total_prem,
                "annualized_yield": round(ann_yield, 4),
                "months_to_recover": round(months_to_breakeven, 1),
                "days_to_expiry": days_to_exp,
            })

        otm_calls = [c for c in calls
                     if c["strike"] and c["strike"] > last
                     and c["bid"] and c["bid"] > 0.05]
        otm_calls.sort(key=lambda c: c["strike"])

        for c in otm_calls[:2]:
            premium = c["bid"]
            total_prem = premium * coverable * 100
            ann_yield = (premium / last) * (365 / days_to_exp)
            upside_to_strike = (c["strike"] - last) / last

            suggestions.append({
                "id": f"uw_cc_otm_{expiry}_{c['strike']}",
                "strategy": "underwater_covered_call_otm",
                "title": f"CC OTM @ ${c['strike']:.2f}",
                "description": (f"Sell {coverable} CALL @ ${c['strike']:.2f}, {expiry} "
                                f"({upside_to_strike:.1%} above current)"),
                "detail": (f"Premium: ${premium:.2f}/sh (${total_prem:,.0f} total) · "
                           f"{ann_yield:.0%} ann. yield · "
                           f"Allows recovery up to ${c['strike']:.2f}"),
                "premium_per_share": premium,
                "total_premium": total_prem,
                "annualized_yield": round(ann_yield, 4),
                "days_to_expiry": days_to_exp,
            })

    tax_loss = abs(unrealized_pl)
    tax_savings_st = round(tax_loss * 0.37, 2)
    tax_savings_lt = round(tax_loss * 0.20, 2)
    suggestions.append({
        "id": f"uw_sell_{ticker}",
        "strategy": "sell_and_harvest",
        "title": "Sell & Harvest Tax Loss",
        "description": f"Sell all {eq_qty} shares of {ticker} at ~${last:.2f}",
        "detail": (f"Realize ${tax_loss:,.0f} loss · "
                   f"Est. tax savings: ${tax_savings_st:,.0f} (short-term) "
                   f"or ${tax_savings_lt:,.0f} (long-term)"),
        "tax_loss": tax_loss,
        "tax_savings_short_term": tax_savings_st,
        "tax_savings_long_term": tax_savings_lt,
    })

    if peer_data:
        etfs = peer_data.get("etfs", [])
        if etfs:
            suggestions.append({
                "id": f"uw_etf_swap_{ticker}",
                "strategy": "etf_swap",
                "title": f"Swap to {etfs[0]} (Sector ETF)",
                "description": (f"Sell {ticker}, buy {etfs[0]} to maintain "
                                f"{peer_data.get('sector', '')} exposure"),
                "detail": (f"Harvest ${tax_loss:,.0f} tax loss while keeping sector exposure. "
                           f"ETF options: {', '.join(etfs[:3])}"),
                "etfs": etfs[:5],
                "tax_loss": tax_loss,
            })

    return suggestions
