"""Rollup analytics for the Accounts/Events/Catalysts UI."""
from __future__ import annotations


def pnl_by_catalyst_type(conn) -> list[dict]:
    """Return per-catalyst_type: wins, losses, gross_wins $, gross_losses $,
    net_pnl $. Only status='confirmed' events count."""
    rows = conn.execute(
        "SELECT catalyst_type, "
        "SUM(CASE WHEN move_pct > 0 THEN 1 ELSE 0 END) AS wins, "
        "SUM(CASE WHEN move_pct < 0 THEN 1 ELSE 0 END) AS losses, "
        "COALESCE(SUM(CASE WHEN pnl_dollars > 0 THEN pnl_dollars ELSE 0 END), 0) AS gross_wins, "
        "COALESCE(SUM(CASE WHEN pnl_dollars < 0 THEN pnl_dollars ELSE 0 END), 0) AS gross_losses, "
        "COALESCE(SUM(pnl_dollars), 0) AS net_pnl "
        "FROM events "
        "WHERE status='confirmed' AND catalyst_type IS NOT NULL "
        "GROUP BY catalyst_type "
        "ORDER BY net_pnl DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def hit_rate_by_catalyst_type(conn) -> list[dict]:
    """Per-catalyst_type: n, hit_rate (fraction of events with move_pct > 0)."""
    rows = conn.execute(
        "SELECT catalyst_type, COUNT(*) AS n, "
        "  (1.0 * SUM(CASE WHEN move_pct > 0 THEN 1 ELSE 0 END) / COUNT(*)) AS hit_rate "
        "FROM events "
        "WHERE status='confirmed' AND catalyst_type IS NOT NULL "
        "GROUP BY catalyst_type "
        "ORDER BY hit_rate DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def linked_vs_unlinked(conn) -> dict:
    """Return {linked, unlinked} counts across all confirmed events."""
    row = conn.execute(
        "SELECT "
        "SUM(CASE WHEN catalyst_id IS NOT NULL THEN 1 ELSE 0 END) AS linked, "
        "SUM(CASE WHEN catalyst_id IS NULL THEN 1 ELSE 0 END) AS unlinked "
        "FROM events WHERE status='confirmed'"
    ).fetchone()
    return {
        "linked":   int(row["linked"]   or 0),
        "unlinked": int(row["unlinked"] or 0),
    }


def events_for_catalyst(conn, catalyst_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT e.*, a.name AS account_name "
        "FROM events e JOIN accounts a ON a.id = e.account_id "
        "WHERE e.catalyst_id = ? "
        "ORDER BY e.event_date DESC",
        (catalyst_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def weekly_events_per_ticker(conn, tickers: list[str], days: int = 30) -> dict:
    """Return {ticker: [{event_date, pnl_dollars, cumulative_pnl}]} for
    recent confirmed events. Used by the Dashboard sparkline."""
    if not tickers:
        return {}
    ticker_set = set(tickers)
    rows = conn.execute(
        "SELECT ticker, event_date, pnl_dollars FROM events "
        "WHERE status='confirmed' "
        "AND date(event_date) >= date('now', ?) "
        "ORDER BY ticker, event_date",
        (f"-{days} days",),
    ).fetchall()
    out: dict[str, list[dict]] = {}
    cum: dict[str, float] = {}
    for r in rows:
        tk = r["ticker"]
        if tk not in ticker_set:
            continue
        cum[tk] = cum.get(tk, 0.0) + r["pnl_dollars"]
        out.setdefault(tk, []).append({
            "event_date": r["event_date"],
            "pnl_dollars": r["pnl_dollars"],
            "cumulative_pnl": cum[tk],
        })
    return out
