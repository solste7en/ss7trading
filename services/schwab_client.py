"""Schwab API client helpers — account hash caching and order throttling."""

import threading
import time

from core.auth import get_client

_ORDER_INTERVAL_S: float = 0.6
_order_lock = threading.Lock()
_last_order_ts: float = 0.0

_account_hash_lock = threading.Lock()
_cached_account_hash: str | None = None
_account_hash_ts: float = 0.0
_ACCOUNT_HASH_TTL: float = 300.0  # 5 min


def get_account_hash() -> str:
    """Return the hashValue of the first linked account, cached for 5 minutes."""
    global _cached_account_hash, _account_hash_ts
    with _account_hash_lock:
        if _cached_account_hash and (time.monotonic() - _account_hash_ts) < _ACCOUNT_HASH_TTL:
            return _cached_account_hash
    client = get_client()
    resp = client.get_account_numbers()
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise ValueError("No accounts found in get_account_numbers()")
    h = data[0]["hashValue"]
    with _account_hash_lock:
        _cached_account_hash = h
        _account_hash_ts = time.monotonic()
    return h


def throttled_order_call(fn, *args, **kwargs):
    """Serialize order-mutating API calls with a minimum gap."""
    global _last_order_ts
    with _order_lock:
        wait_for = _ORDER_INTERVAL_S - (time.monotonic() - _last_order_ts)
        if wait_for > 0:
            time.sleep(wait_for)
        _last_order_ts = time.monotonic()
    return fn(*args, **kwargs)
