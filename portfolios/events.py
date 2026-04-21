"""Position event detection — called from catalyst_poller tail.

Scans each account's current positions, fetches daily bars for each held
ticker, computes 1d and 5d moves, writes events whose |move| >= threshold.
Then auto-links to a nearby catalyst if one exists within ±3 days on the
same ticker.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date

from catalysts import db as cdb
from catalysts import polygon_client

log = logging.getLogger("portfolios.events")

DEFAULT_DAILY_PCT = 5.0
DEFAULT_5DAY_PCT = 10.0
CATALYST_LINK_WINDOW_DAYS = 3
CATALYST_MIN_LINK_SCORE = 30


def _today_et() -> int:
    """Return today's date as int yyyymmdd in US/Eastern. Overridable in tests."""
    from zoneinfo import ZoneInfo
    from datetime import datetime
    d = datetime.now(ZoneInfo("America/New_York")).date()
    return d.year * 10000 + d.month * 100 + d.day


def _positions_qty_by_account(conn) -> dict[int, dict[str, float]]:
    """For every active account, return {ticker: qty_held_today}."""
    out: dict[int, dict[str, float]] = {}
    for a in cdb.load_accounts(conn):
        positions: dict[str, float] = defaultdict(float)
        for t in cdb.load_trades(conn, account_id=a["id"]):
            if t["side"] == "BUY":
                positions[t["ticker"]] += t["qty"]
            else:
                positions[t["ticker"]] -= t["qty"]
        # Filter to actual longs
        held = {tk: q for tk, q in positions.items() if q > 1e-9}
        if held:
            out[a["id"]] = held
    return out


def _fetch_bars(ticker: str, days_back: int = 8) -> list[dict] | None:
    """Fetch last ~8 daily bars from Polygon (covers 5d window + weekends)."""
    from datetime import timedelta
    end = date.today()
    start = end - timedelta(days=days_back + 7)  # extra slack for holidays
    body = polygon_client.get(
        f"/v2/aggs/ticker/{ticker}/range/1/day/{start.isoformat()}/{end.isoformat()}",
        params={"adjusted": "true", "sort": "asc"},
    )
    if body is None:
        return None
    return body.get("results", [])


def _bar_date_iso(bar: dict) -> str:
    return date.fromtimestamp(bar["t"] / 1000).isoformat()


def _pct_change(a: float, b: float) -> float:
    if a <= 0:
        return 0.0
    return (b / a - 1) * 100.0


def _auto_link(conn, account_id: int, ticker: str, event_date_iso: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM catalysts "
        "WHERE ticker=? "
        "AND date(published_at) IS NOT NULL "
        "AND ABS(julianday(date(published_at)) - julianday(?)) <= ? "
        "AND final_score >= ? "
        "ORDER BY final_score DESC LIMIT 1",
        (ticker, event_date_iso, CATALYST_LINK_WINDOW_DAYS, CATALYST_MIN_LINK_SCORE),
    ).fetchone()
    return int(row["id"]) if row else None


def detect_events_for_all_accounts(conn) -> int:
    """Return number of NEW events written."""
    by_acc = _positions_qty_by_account(conn)
    if not by_acc:
        return 0

    # Unique tickers across all accounts → fetch each once
    all_tickers: set[str] = set()
    for held in by_acc.values():
        all_tickers.update(held.keys())
    bars_by_ticker: dict[str, list[dict]] = {}
    for t in sorted(all_tickers):
        bars = _fetch_bars(t)
        if bars and len(bars) >= 2:
            bars_by_ticker[t] = bars

    # Accounts → per-account thresholds
    acc_rows = {a["id"]: a for a in cdb.load_accounts(conn)}

    written = 0
    for account_id, held in by_acc.items():
        acc = acc_rows.get(account_id)
        if acc is None:
            continue
        daily_th = acc["event_daily_pct"] or DEFAULT_DAILY_PCT
        five_th  = acc["event_5day_pct"]  or DEFAULT_5DAY_PCT

        for ticker, qty in held.items():
            bars = bars_by_ticker.get(ticker)
            if not bars or len(bars) < 2:
                continue
            today_bar = bars[-1]
            prev_bar = bars[-2]
            event_date_iso = _bar_date_iso(today_bar)

            # 1-day move
            daily_pct = _pct_change(prev_bar["c"], today_bar["c"])
            if abs(daily_pct) >= daily_th:
                written += _write_event(
                    conn, account_id, ticker, event_date_iso, "1d",
                    daily_pct, qty, prev_bar["c"], today_bar["c"],
                )

            # 5-day move (today vs 5 bars ago, if available)
            if len(bars) >= 6:
                five_ago = bars[-6]
                five_pct = _pct_change(five_ago["c"], today_bar["c"])
                if abs(five_pct) >= five_th:
                    written += _write_event(
                        conn, account_id, ticker, event_date_iso, "5d",
                        five_pct, qty, five_ago["c"], today_bar["c"],
                    )
    return written


def _write_event(
    conn, account_id: int, ticker: str, event_date_iso: str,
    move_window: str, move_pct: float, qty: float,
    close_before: float, close_after: float,
) -> int:
    value_before = qty * close_before
    value_after  = qty * close_after
    pnl_dollars  = value_after - value_before

    catalyst_id = _auto_link(conn, account_id, ticker, event_date_iso)

    _eid, was_new = cdb.insert_event(
        conn, account_id=account_id, ticker=ticker,
        event_date=event_date_iso, move_pct=round(move_pct, 2),
        move_window=move_window, position_qty=qty,
        value_before=round(value_before, 2),
        value_after=round(value_after, 2),
        pnl_dollars=round(pnl_dollars, 2),
        catalyst_id=catalyst_id,
    )
    return 1 if was_new else 0
