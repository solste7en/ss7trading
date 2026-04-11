"""Normalize Schwab account / balance payloads for the Balance tab API."""


def _mask_account_number(raw: str | None) -> str:
    if not raw:
        return "—"
    s = str(raw).strip()
    if len(s) <= 4:
        return "****" + s
    return "****" + s[-4:]


def _clean_balance_block(block: dict | None) -> dict:
    """Keep only JSON-safe scalars (Schwab balance objects are usually flat numbers)."""
    if not block or not isinstance(block, dict):
        return {}
    out = {}
    for k, v in block.items():
        if v is None or isinstance(v, (bool, int, float, str)):
            out[k] = v
    return out


def clean_accounts_balance(accounts_data: list | None) -> dict:
    """
    Build a minimal, display-safe structure from Client.get_accounts().json().

    Returns:
        {
          "accounts": [{ "account_display", "type", "round_trips", "is_day_trader",
                         "current_balances", "projected_balances", "initial_balances" }, ...],
          "aggregated_balance": { ... } | null
        }
    """
    accounts_data = accounts_data or []
    accounts_out = []
    aggregated = None

    for item in accounts_data:
        if not isinstance(item, dict):
            continue
        ab = item.get("aggregatedBalance")
        if isinstance(ab, dict) and ab and aggregated is None:
            aggregated = _clean_balance_block(ab)
        sa = item.get("securitiesAccount")
        if not isinstance(sa, dict):
            continue
        acct_num = sa.get("accountNumber")
        accounts_out.append({
            "account_display": _mask_account_number(acct_num),
            "type": sa.get("type") or "",
            "round_trips": sa.get("roundTrips"),
            "is_day_trader": sa.get("isDayTrader"),
            "current_balances": _clean_balance_block(sa.get("currentBalances")),
            "projected_balances": _clean_balance_block(sa.get("projectedBalances")),
            "initial_balances": _clean_balance_block(sa.get("initialBalances")),
        })

    return {
        "accounts": accounts_out,
        "aggregated_balance": aggregated,
    }
