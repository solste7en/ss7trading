"""Quote data cleaning."""


def clean_quotes(quotes_data):
    """Parse the Schwab quote response into a flat list."""
    result = []
    for symbol, data in quotes_data.items():
        q = data.get("quote", {})
        ref = data.get("reference", {})
        result.append({
            "symbol": symbol,
            "description": ref.get("description", symbol),
            "last": q.get("lastPrice"),
            "bid": q.get("bidPrice"),
            "ask": q.get("askPrice"),
            "change": q.get("netChange"),
            "change_pct": q.get("netPercentChange"),
            "volume": q.get("totalVolume"),
            "52w_high": q.get("52WeekHigh"),
            "52w_low": q.get("52WeekLow"),
        })
    result.sort(key=lambda x: x["symbol"])
    return result
