"""Option chain cleaning and strategy suggestion engine."""

import datetime
import re


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

    usable_exps = [e for e in exps if (calls_map.get(e) or puts_map.get(e))][:2]

    for expiry in usable_exps:
        calls = calls_map.get(expiry, [])
        puts = puts_map.get(expiry, [])
        days_to_exp = max(1, (datetime.date.fromisoformat(expiry) - datetime.date.today()).days)

        if eq_qty > 0:
            coverable = abs(eq_qty) // 100
            if coverable < 1:
                continue

            # Covered Call
            otm_calls = [c for c in calls
                         if c["strike"] and c["strike"] > last
                         and c["bid"] and c["bid"] > 0.05]
            otm_calls.sort(key=lambda c: c["strike"])
            for c in otm_calls[:3]:
                premium = c["bid"]
                total_prem = premium * coverable * 100
                ann_yield = (premium / last) * (365 / days_to_exp)
                occ = c.get("symbol", "")
                suggestions.append({
                    "id": f"cc_{expiry}_{c['strike']}",
                    "strategy": "naked",
                    "title": "Covered Call",
                    "description": f"Sell {coverable} CALL @ ${c['strike']:.2f}, {expiry}",
                    "detail": f"Premium ~${premium:.2f}/sh (${total_prem:,.0f} total) · {ann_yield:.0%} ann. yield",
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
                })

            # Protective Collar
            otm_puts = [p for p in puts
                        if p["strike"] and p["strike"] < last
                        and p["ask"] and p["ask"] > 0]
            if otm_calls and otm_puts:
                otm_puts.sort(key=lambda p: -p["strike"])
                best_collar = None
                best_net = 999
                for cc in otm_calls[:4]:
                    for pp in otm_puts[:4]:
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
                        "detail": f"Net {'credit' if net < 0 else 'debit'}: ${abs(net):.2f}/sh (${abs(net_total):,.0f} total) · Protection below ${pp['strike']:.2f}",
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
                    })

            # Bear Call Spread
            if len(otm_calls) >= 2:
                sell_c = otm_calls[0]
                buy_c = otm_calls[min(2, len(otm_calls) - 1)]
                if sell_c["bid"] and buy_c["ask"] and sell_c["bid"] > buy_c["ask"]:
                    net_cr = sell_c["bid"] - buy_c["ask"]
                    width = buy_c["strike"] - sell_c["strike"]
                    suggestions.append({
                        "id": f"spread_{expiry}_{sell_c['strike']}_{buy_c['strike']}",
                        "strategy": "vertical",
                        "title": "Call Credit Spread",
                        "description": f"Sell {coverable} CALL @ ${sell_c['strike']:.2f} + Buy {coverable} CALL @ ${buy_c['strike']:.2f}, {expiry}",
                        "detail": f"Net credit: ${net_cr:.2f}/sh · Max risk: ${width - net_cr:.2f}/sh",
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
                    })

        elif eq_qty < 0:
            coverable = abs(eq_qty) // 100
            if coverable < 1:
                continue

            # Cash-Secured Put
            otm_puts = [p for p in puts
                        if p["strike"] and p["strike"] < last
                        and p["bid"] and p["bid"] > 0.05]
            otm_puts.sort(key=lambda p: -p["strike"])
            for p in otm_puts[:3]:
                premium = p["bid"]
                total_prem = premium * coverable * 100
                ann_yield = (premium / last) * (365 / days_to_exp)
                suggestions.append({
                    "id": f"csp_{expiry}_{p['strike']}",
                    "strategy": "naked",
                    "title": "Cash-Secured Put",
                    "description": f"Sell {coverable} PUT @ ${p['strike']:.2f}, {expiry}",
                    "detail": f"Premium ~${premium:.2f}/sh (${total_prem:,.0f} total) · {ann_yield:.0%} ann. yield",
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
                })

            # Short Collar
            otm_calls = [c for c in calls
                         if c["strike"] and c["strike"] > last
                         and c["ask"] and c["ask"] > 0]
            if otm_puts and otm_calls:
                otm_calls.sort(key=lambda c: c["strike"])
                best_collar = None
                best_net = 999
                for pp in otm_puts[:4]:
                    for cc in otm_calls[:4]:
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
                        "detail": f"Net {'debit' if net > 0 else 'credit'}: ${abs(net):.2f}/sh · Upside protection above ${cc['strike']:.2f}",
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
                    })

            # Put Credit Spread
            if len(otm_puts) >= 2:
                sell_p = otm_puts[0]
                buy_p = otm_puts[min(2, len(otm_puts) - 1)]
                if sell_p["bid"] and buy_p["ask"] and sell_p["bid"] > buy_p["ask"]:
                    net_cr = sell_p["bid"] - buy_p["ask"]
                    width = sell_p["strike"] - buy_p["strike"]
                    suggestions.append({
                        "id": f"pspread_{expiry}_{sell_p['strike']}_{buy_p['strike']}",
                        "strategy": "vertical",
                        "title": "Put Credit Spread",
                        "description": f"Sell {coverable} PUT @ ${sell_p['strike']:.2f} + Buy {coverable} PUT @ ${buy_p['strike']:.2f}, {expiry}",
                        "detail": f"Net credit: ${net_cr:.2f}/sh · Max risk: ${width - net_cr:.2f}/sh",
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
                    })

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
