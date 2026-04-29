"""Income trades, legs, recovery flags, weekly time series, and stats."""

import math
from collections import defaultdict
from datetime import date, datetime, timedelta

from core.db._conn import _connection


def _ensure_income_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS income_trades (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            underlying            TEXT    NOT NULL,
            strategy              TEXT    NOT NULL,
            open_date             TEXT    NOT NULL,
            close_date            TEXT,
            status                TEXT    NOT NULL DEFAULT 'open',
            days_held             INTEGER,
            net_premium           REAL,
            close_cost            REAL    DEFAULT 0,
            fees                  REAL    DEFAULT 0,
            net_pnl               REAL,
            net_pnl_pct           REAL,
            is_win                INTEGER DEFAULT 0,
            is_perfect_win        INTEGER DEFAULT 0,
            assignment_stock_price REAL,
            is_early_assignment   INTEGER DEFAULT 0,
            is_fully_exercised    INTEGER DEFAULT 0,
            recovery_dismissed_qty INTEGER DEFAULT 0,
            dedup_key             TEXT    UNIQUE,
            synced_at             TEXT    DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_it_underlying ON income_trades(underlying);
        CREATE INDEX IF NOT EXISTS idx_it_status     ON income_trades(status);

        CREATE TABLE IF NOT EXISTS income_trade_legs (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id                INTEGER NOT NULL REFERENCES income_trades(id) ON DELETE CASCADE,
            leg_type                TEXT    NOT NULL,
            strike                  REAL    NOT NULL,
            expiry                  TEXT    NOT NULL,
            direction               TEXT    NOT NULL,
            open_action             TEXT,
            open_qty                INTEGER,
            open_price              REAL,
            open_date               TEXT,
            close_action            TEXT,
            close_qty               INTEGER,
            close_price             REAL,
            close_date              TEXT,
            leg_pnl                 REAL,
            schwab_open_activity_id INTEGER,
            schwab_close_activity_id INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_itl_trade ON income_trade_legs(trade_id);

        CREATE TABLE IF NOT EXISTS income_sync_meta (
            id          INTEGER PRIMARY KEY CHECK (id = 1),
            last_synced TEXT
        );
    """)
    # Migrate: add recovery_dismissed_qty if missing (tables created before v0.2.1)
    try:
        conn.execute("SELECT recovery_dismissed_qty FROM income_trades LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE income_trades ADD COLUMN recovery_dismissed_qty INTEGER DEFAULT 0")
    # Migrate: add is_early_assignment if missing
    try:
        conn.execute("SELECT is_early_assignment FROM income_trades LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE income_trades ADD COLUMN is_early_assignment INTEGER DEFAULT 0")
    # Migrate: add is_fully_exercised if missing (spread where short was assigned
    # and long was exercised same day → net-zero stock, no recovery needed).
    try:
        conn.execute("SELECT is_fully_exercised FROM income_trades LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE income_trades ADD COLUMN is_fully_exercised INTEGER DEFAULT 0")
    conn.commit()


def clear_income_trades():
    with _connection() as conn:
        _ensure_income_tables(conn)
        conn.executescript("DELETE FROM income_trade_legs; DELETE FROM income_trades;")
        conn.commit()


def upsert_income_trade(trade, legs):
    """Insert or replace an income trade with its legs.
    `trade` is a dict, `legs` is a list of dicts."""
    with _connection() as conn:
        _ensure_income_tables(conn)
        cur = conn.cursor()

        cur.execute("SELECT id FROM income_trades WHERE dedup_key = ?",
                    (trade["dedup_key"],))
        existing = cur.fetchone()
        if existing:
            trade_id = existing[0]
            cur.execute("DELETE FROM income_trade_legs WHERE trade_id = ?", (trade_id,))
            cur.execute("""
                UPDATE income_trades SET
                    underlying=?, strategy=?, open_date=?, close_date=?,
                    status=?, days_held=?, net_premium=?, close_cost=?, fees=?,
                    net_pnl=?, net_pnl_pct=?, is_win=?, is_perfect_win=?,
                    assignment_stock_price=?, is_early_assignment=?,
                    is_fully_exercised=?,
                    synced_at=datetime('now')
                WHERE id=?
            """, (trade["underlying"], trade["strategy"], trade["open_date"],
                  trade.get("close_date"), trade["status"], trade.get("days_held"),
                  trade.get("net_premium"), trade.get("close_cost", 0),
                  trade.get("fees", 0), trade.get("net_pnl"),
                  trade.get("net_pnl_pct"), trade.get("is_win", 0),
                  trade.get("is_perfect_win", 0),
                  trade.get("assignment_stock_price"),
                  trade.get("is_early_assignment", 0),
                  trade.get("is_fully_exercised", 0), trade_id))
        else:
            cur.execute("""
                INSERT INTO income_trades
                    (underlying, strategy, open_date, close_date, status, days_held,
                     net_premium, close_cost, fees, net_pnl, net_pnl_pct,
                     is_win, is_perfect_win, assignment_stock_price,
                     is_early_assignment, is_fully_exercised, dedup_key)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (trade["underlying"], trade["strategy"], trade["open_date"],
                  trade.get("close_date"), trade["status"], trade.get("days_held"),
                  trade.get("net_premium"), trade.get("close_cost", 0),
                  trade.get("fees", 0), trade.get("net_pnl"),
                  trade.get("net_pnl_pct"), trade.get("is_win", 0),
                  trade.get("is_perfect_win", 0),
                  trade.get("assignment_stock_price"),
                  trade.get("is_early_assignment", 0),
                  trade.get("is_fully_exercised", 0), trade["dedup_key"]))
            trade_id = cur.lastrowid

        for leg in legs:
            cur.execute("""
                INSERT INTO income_trade_legs
                    (trade_id, leg_type, strike, expiry, direction,
                     open_action, open_qty, open_price, open_date,
                     close_action, close_qty, close_price, close_date,
                     leg_pnl, schwab_open_activity_id, schwab_close_activity_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (trade_id, leg["leg_type"], leg["strike"], leg["expiry"],
                  leg["direction"], leg.get("open_action"), leg.get("open_qty"),
                  leg.get("open_price"), leg.get("open_date"),
                  leg.get("close_action"), leg.get("close_qty"),
                  leg.get("close_price"), leg.get("close_date"),
                  leg.get("leg_pnl"),
                  leg.get("schwab_open_activity_id"),
                  leg.get("schwab_close_activity_id")))

        conn.commit()
        return trade_id


def set_income_sync_time():
    with _connection() as conn:
        _ensure_income_tables(conn)
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute("""
            INSERT INTO income_sync_meta (id, last_synced) VALUES (1, ?)
            ON CONFLICT(id) DO UPDATE SET last_synced = excluded.last_synced
        """, (now,))
        conn.commit()


def get_income_sync_time():
    with _connection() as conn:
        _ensure_income_tables(conn)
        cur = conn.cursor()
        cur.execute("SELECT last_synced FROM income_sync_meta WHERE id=1")
        row = cur.fetchone()
        return row[0] if row else None


def _income_trades_where(
    ticker="",
    status="",
    strategy="",
    outcome="",
    table_alias="t",
    date_from="",
    date_to="",
):
    """Build WHERE fragments for income_trades filters. Returns (list of SQL fragments, params)."""
    p = f"{table_alias}." if table_alias else ""
    where, params = [], []
    if ticker:
        where.append(f"{p}underlying = ?"); params.append(ticker.upper())
    if status:
        where.append(f"{p}status = ?"); params.append(status)
    if strategy:
        where.append(f"{p}strategy LIKE ?"); params.append(f"%{strategy}%")
    if outcome == "win":
        where.append(f"{p}is_win = 1")
    elif outcome == "perfect":
        where.append(f"{p}is_perfect_win = 1")
    elif outcome == "assigned":
        where.append(f"{p}status = 'assigned'")
    elif outcome == "open":
        where.append(f"{p}status = 'open'")
    elif outcome == "closed":
        # Must match get_income_stats closed_trades: SUM(status != 'open'), not status='closed' only.
        where.append(f"{p}status != 'open'")
    df = (date_from or "").strip()
    dt_ = (date_to or "").strip()
    if df or dt_:
        eff = f"COALESCE(NULLIF(TRIM({p}close_date), ''), {p}open_date)"
        where.append(f"{eff} >= ? AND {eff} <= ?")
        params.append(df or "1970-01-01")
        params.append(dt_ or "9999-12-31")
    return where, params


def get_income_trade_ids_filtered(
    ticker="", status="", strategy="", outcome="", date_from="", date_to=""
):
    """Return list of {id, underlying} dicts for income_trades matching the given filters."""
    wfrag, params = _income_trades_where(ticker, status, strategy, outcome, "", date_from, date_to)
    clause = ("WHERE " + " AND ".join(wfrag)) if wfrag else ""
    with _connection() as conn:
        _ensure_income_tables(conn)
        cur = conn.cursor()
        cur.execute(f"SELECT id, underlying FROM income_trades {clause}", params)
        return [dict(r) for r in cur.fetchall()]


def _income_attach_legs(cur, trades):
    if not trades:
        return
    trade_ids = [t["id"] for t in trades]
    ph = ",".join("?" for _ in trade_ids)
    cur.execute(f"""
        SELECT * FROM income_trade_legs WHERE trade_id IN ({ph})
        ORDER BY trade_id, id
    """, trade_ids)
    legs_by_trade = {}
    for leg in cur.fetchall():
        leg_dict = dict(leg)
        tid = leg_dict["trade_id"]
        legs_by_trade.setdefault(tid, []).append(leg_dict)
    for t in trades:
        t["legs"] = legs_by_trade.get(t["id"], [])


def _income_efficiency_score(trade: dict) -> float | None:
    """Same formula as static/js/income.js _incomeEfficiencyScore.

    score = net_pnl / sqrt(days_held + 1) * 100 / strike

    sqrt normalises time sublinearly (Sharpe-like) so short-duration
    trades aren't disproportionately inflated vs longer ones.
    """
    if trade.get("status") == "open":
        return None
    net = trade.get("net_pnl")
    if net is None:
        return None
    legs = trade.get("legs") or []
    strike = None
    for leg in legs:
        if leg.get("direction") == "short":
            k = leg.get("strike")
            if k is not None:
                kf = float(k)
                if kf > 0:
                    strike = kf
                    break
    if strike is None and legs:
        k = legs[0].get("strike")
        if k is not None:
            kf = float(k)
            if kf > 0:
                strike = kf
    if strike is None:
        return None
    d = trade.get("days_held")
    d = int(d) if d is not None and d >= 0 else 0
    return (float(net) / math.sqrt(d + 1)) * (100.0 / strike)


def get_income_trades(
    page=1,
    limit=25,
    ticker="",
    status="",
    strategy="",
    outcome="",
    sort_by="open_date",
    sort_dir="desc",
    date_from="",
    date_to="",
):
    offset = (page - 1) * limit
    wfrag, params = _income_trades_where(ticker, status, strategy, outcome, "t", date_from, date_to)
    clause = ("WHERE " + " AND ".join(wfrag)) if wfrag else ""
    sb = (sort_by or "open_date").lower()
    rev = (sort_dir or "desc").lower() != "asc"

    sort_map = {
        "open_date": "t.open_date",
        "close_date": "t.close_date",
        "underlying": "t.underlying",
        "strategy": "t.strategy",
        "net_pnl": "t.net_pnl",
        "net_pnl_pct": "t.net_pnl_pct",
        "days_held": "t.days_held",
        "status": "t.status",
        "net_premium": "t.net_premium",
    }

    with _connection() as conn:
        _ensure_income_tables(conn)
        cur = conn.cursor()

        cur.execute(f"SELECT COUNT(*) FROM income_trades t {clause}", params)
        total = cur.fetchone()[0]

        if sb in ("recovery", "recovery_pnl"):
            from services.recovery import attach_recovery_summaries
            cur.execute(f"SELECT t.* FROM income_trades t {clause} ORDER BY t.id DESC", params)
            trades = [dict(r) for r in cur.fetchall()]
            _income_attach_legs(cur, trades)
            attach_recovery_summaries(trades)

            def _rec_frac(t):
                tgt = t.get("recovery_target")
                if not tgt:
                    return -1.0
                return (t.get("recovery_recovered") or 0) / max(tgt, 1)

            if sb == "recovery":
                trades.sort(key=_rec_frac, reverse=rev)
            else:
                def _pnl_key(t):
                    v = t.get("true_recovery_pnl")
                    if v is None:
                        return float("-inf") if rev else float("inf")
                    return v
                trades.sort(key=_pnl_key, reverse=rev)
            trades = trades[offset:offset + limit]
        elif sb == "score":
            cur.execute(f"SELECT t.* FROM income_trades t {clause} ORDER BY t.id DESC", params)
            trades = [dict(r) for r in cur.fetchall()]
            _income_attach_legs(cur, trades)

            def _score_key(t):
                v = _income_efficiency_score(t)
                if v is None:
                    return float("-inf") if rev else float("inf")
                return v

            trades.sort(key=_score_key, reverse=rev)
            trades = trades[offset:offset + limit]
        else:
            order_col = sort_map.get(sb, "t.open_date")
            order_dir = "ASC" if not rev else "DESC"
            cur.execute(f"""
                SELECT t.* FROM income_trades t {clause}
                ORDER BY {order_col} {order_dir}, t.id DESC
                LIMIT ? OFFSET ?
            """, params + [limit, offset])
            trades = [dict(r) for r in cur.fetchall()]
            _income_attach_legs(cur, trades)

    return {
        "data": trades,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": math.ceil(total / limit) if total else 0,
    }


def get_income_stats(ticker="", status="", strategy="", outcome="", date_from="", date_to=""):
    wfrag, params = _income_trades_where(ticker, status, strategy, outcome, "", date_from, date_to)
    clause = ("WHERE " + " AND ".join(wfrag)) if wfrag else ""

    with _connection() as conn:
        _ensure_income_tables(conn)
        cur = conn.cursor()

        cur.execute(f"""
            SELECT
                COUNT(*)                                          AS total_trades,
                SUM(CASE WHEN status != 'open' THEN 1 ELSE 0 END) AS closed_trades,
                SUM(CASE WHEN status  = 'open' THEN 1 ELSE 0 END) AS open_trades,
                SUM(CASE WHEN is_win = 1 THEN 1 ELSE 0 END)       AS win_count,
                SUM(CASE WHEN status != 'open' AND is_win = 0 THEN 1 ELSE 0 END) AS loss_count,
                SUM(CASE WHEN is_perfect_win = 1 THEN 1 ELSE 0 END) AS perfect_win_count,
                SUM(CASE WHEN status = 'assigned' THEN 1 ELSE 0 END) AS assigned_count,
                COALESCE(SUM(CASE WHEN status != 'open' THEN net_pnl ELSE 0 END), 0) AS total_pnl,
                COALESCE(SUM(net_premium), 0)                      AS total_premium_collected,
                COALESCE(SUM(CASE WHEN status = 'open' THEN net_premium ELSE 0 END), 0) AS open_premium
            FROM income_trades {clause}
        """, params)
        row = dict(cur.fetchone())

    closed = row["closed_trades"] or 0
    row["win_rate"] = round(100 * (row["win_count"] or 0) / closed, 1) if closed else 0
    row["perfect_win_rate"] = round(100 * (row["perfect_win_count"] or 0) / closed, 1) if closed else 0
    row["avg_pnl_per_trade"] = round((row["total_pnl"] or 0) / closed, 2) if closed else 0
    row["total_pnl"] = round(row["total_pnl"] or 0, 2)
    row["total_premium_collected"] = round(row["total_premium_collected"] or 0, 2)
    row["open_premium"] = round(row["open_premium"] or 0, 2)
    row["last_synced"] = get_income_sync_time()
    return row


def _income_week_monday(eff: str) -> date:
    """Return the Monday (ISO week start) of the calendar week containing *eff*."""
    d = date.fromisoformat(eff[:10])
    return d - timedelta(days=d.weekday())


def get_income_weekly_timeseries(
    ticker="",
    status="",
    strategy="",
    outcome="",
    date_from="",
    date_to="",
):
    """Weekly buckets aligned with Income KPI **Sum Net P&L** (option + recovery).

    Each trade is placed in the Monday week of its effective date
    (``COALESCE(NULLIF(TRIM(close_date), ''), open_date)``) — same as
    ``_income_trades_where``.

    Per trade, the week receives the same components the dashboard uses for
    **Sum Net P&L**:

    * **Option leg:** ``net_pnl`` when ``status != 'open'`` (matches
      ``get_income_stats`` ``total_pnl``); open trades contribute ``0``.
    * **Recovery:** ``true_recovery_pnl`` after ``attach_recovery_summaries``
      (assigned, non–fully-exercised rows only; others ``0``).

    Returns::

        week_starts: ISO Mondays, ascending
        weekly_sum_net: bar height = sum(option + recovery) in that week
        weekly_option_pnl: option-only portion (same week)
        weekly_recovery_pnl: true recovery portion (same week)
        cumulative_sum_net: running sum of ``weekly_sum_net`` (line)
    """
    from services.recovery import attach_recovery_summaries

    wfrag, params = _income_trades_where(ticker, status, strategy, outcome, "t", date_from, date_to)
    clause = ("WHERE " + " AND ".join(wfrag)) if wfrag else ""

    with _connection() as conn:
        _ensure_income_tables(conn)
        cur = conn.cursor()
        cur.execute(f"SELECT t.* FROM income_trades t {clause}", params)
        trades = [dict(r) for r in cur.fetchall()]
        _income_attach_legs(cur, trades)

    attach_recovery_summaries(trades)

    opt_by_week: dict[date, float] = defaultdict(float)
    rec_by_week: dict[date, float] = defaultdict(float)

    for t in trades:
        eff = (t.get("close_date") or "").strip()
        if not eff:
            eff = (t.get("open_date") or "").strip()
        if not eff or len(eff) < 10:
            continue
        try:
            wk = _income_week_monday(eff)
        except ValueError:
            continue
        st = (t.get("status") or "").strip()
        opt = float(t.get("net_pnl") or 0) if st != "open" else 0.0
        rec = float(t.get("true_recovery_pnl") or 0)
        opt_by_week[wk] += opt
        rec_by_week[wk] += rec

    week_keys = set(opt_by_week) | set(rec_by_week)

    df_s = (date_from or "").strip()
    dt_s = (date_to or "").strip()

    if df_s and dt_s:
        try:
            d0 = date.fromisoformat(df_s[:10])
            d1 = date.fromisoformat(dt_s[:10])
        except ValueError:
            d0, d1 = None, None
        if d0 and d1 and d0 > d1:
            d0, d1 = d1, d0
        if d0 and d1:
            axis_start = _income_week_monday(d0.isoformat())
            axis_end = _income_week_monday(d1.isoformat())
            weeks = []
            cur_w = axis_start
            while cur_w <= axis_end:
                weeks.append(cur_w)
                cur_w += timedelta(days=7)
        else:
            weeks = sorted(week_keys)
    else:
        weeks = sorted(week_keys)

    raw_weekly = [opt_by_week.get(w, 0.0) + rec_by_week.get(w, 0.0) for w in weeks]
    weekly_option = [round(opt_by_week.get(w, 0.0), 2) for w in weeks]
    weekly_recovery = [round(rec_by_week.get(w, 0.0), 2) for w in weeks]
    weekly_sum_net = [round(x, 2) for x in raw_weekly]

    cum = []
    running = 0.0
    for x in raw_weekly:
        running += x
        cum.append(round(running, 2))

    return {
        "week_starts": [w.isoformat() for w in weeks],
        "weekly_sum_net": weekly_sum_net,
        "weekly_option_pnl": weekly_option,
        "weekly_recovery_pnl": weekly_recovery,
        "cumulative_sum_net": cum,
        "date_from": df_s or None,
        "date_to": dt_s or None,
    }


def dismiss_recovery(trade_id, qty):
    """Set recovery_dismissed_qty on an assigned income trade."""
    with _connection() as conn:
        _ensure_income_tables(conn)
        conn.execute(
            "UPDATE income_trades SET recovery_dismissed_qty = ? WHERE id = ? AND status = 'assigned'",
            (qty, trade_id),
        )
        conn.commit()


def get_assigned_trades_for_ticker(ticker):
    """Return all assigned income trades + legs for a given ticker."""
    with _connection() as conn:
        _ensure_income_tables(conn)
        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM income_trades
            WHERE underlying = ? AND status = 'assigned'
            ORDER BY close_date ASC
        """, (ticker.upper(),))
        trades = [dict(r) for r in cur.fetchall()]
        if trades:
            ids = [t["id"] for t in trades]
            ph = ",".join("?" for _ in ids)
            cur.execute(f"""
                SELECT * FROM income_trade_legs WHERE trade_id IN ({ph})
                ORDER BY trade_id, id
            """, ids)
            legs_map = {}
            for leg in cur.fetchall():
                ld = dict(leg)
                legs_map.setdefault(ld["trade_id"], []).append(ld)
            for t in trades:
                t["legs"] = legs_map.get(t["id"], [])
        return trades


def get_recovery_equity_trades(ticker, min_date, actions):
    """Return equity transactions for a ticker from min_date onwards, filtered by actions."""
    with _connection() as conn:
        cur = conn.cursor()
        ph = ",".join("?" for _ in actions)
        cur.execute(f"""
            SELECT trade_date, action, quantity, price, amount
            FROM transactions
            WHERE underlying = ? AND is_option = 0 AND category = 'equity'
              AND trade_date >= ?
              AND action IN ({ph})
              AND (is_from_option_event IS NULL OR is_from_option_event = 0)
            ORDER BY trade_date ASC, ROWID ASC
        """, [ticker.upper(), min_date] + list(actions))
        return [dict(r) for r in cur.fetchall()]
