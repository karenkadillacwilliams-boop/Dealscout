"""Catalysts page — scored filings/news with filter/drilldown and related tickers."""
from __future__ import annotations

import json
from urllib.parse import urlparse

import pandas as pd
import streamlit as st

from catalysts import db as cdb

from app_pages.shared import get_conn


def _render_portfolio_events_for_catalyst(conn, catalyst_id: int) -> None:
    from portfolios import analytics
    rows = analytics.events_for_catalyst(conn, catalyst_id)
    st.subheader("Did this catalyst move my portfolio?")
    if not rows:
        # Find ticker for helpful suggestion
        tr = conn.execute(
            "SELECT ticker FROM catalysts WHERE id=?", (catalyst_id,),
        ).fetchone()
        tk = tr["ticker"] if tr else "this ticker"
        st.caption(f"No position events linked yet. Add {tk} to a portfolio "
                    f"(Import or Trades) to start tracking this catalyst's impact.")
        return
    df = pd.DataFrame(rows)[[
        "account_name", "ticker", "event_date", "move_window",
        "move_pct", "pnl_dollars", "catalyst_type", "status",
    ]].rename(columns={
        "account_name": "Account", "ticker": "Ticker", "event_date": "Date",
        "move_window": "Win", "move_pct": "Move %",
        "pnl_dollars": "P&L $", "catalyst_type": "Tag", "status": "Status",
    })
    st.dataframe(
        df.style.format({"Move %": "{:+.2f}%", "P&L $": "${:,.2f}"}),
        width="stretch", hide_index=True,
    )


def _staleness_banner(conn) -> None:
    """Show last-poll time + most-recent-catalyst so users understand empty states."""
    last_poll = cdb.last_poll_time(conn)
    newest = conn.execute(
        "SELECT MAX(published_at) AS p FROM catalysts"
    ).fetchone()["p"]
    total = conn.execute("SELECT COUNT(*) FROM catalysts").fetchone()[0]

    cols = st.columns(3)
    cols[0].metric("Total catalysts", total)
    cols[1].metric("Last poll", (last_poll or "—")[:16].replace("T", " "))
    cols[2].metric("Newest catalyst", (newest or "—")[:16].replace("T", " "))

    if last_poll:
        from datetime import datetime, timezone
        try:
            dt = datetime.fromisoformat(last_poll.replace("Z", "+00:00"))
            hours_ago = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
            if hours_ago > 6:
                st.warning(
                    f"Poller hasn't run in {int(hours_ago)}h. "
                    "Run `python catalyst_poller.py` to fetch fresh filings and news."
                )
        except ValueError:
            pass


def render() -> None:
    conn = get_conn()

    st.title("Catalysts")
    st.caption("Ingested filings and news, scored for M&A / partnership / product signal.")
    _staleness_banner(conn)

    c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
    with c1:
        min_score = st.slider("Min score", 0, 100, 30, 5,
                              help="70 is LLM-reranked high-conviction. "
                                   "30 includes keyword-scored items.")
    with c2:
        lookback = st.selectbox("Lookback", ["6h", "24h", "7d", "30d"], index=3)
    with c3:
        tag_filter = st.selectbox(
            "Tag",
            ["All", "m&a-confirmed", "m&a-rumor", "activist",
             "partnership", "product", "filing"],
        )
    with c4:
        ticker_filter = st.text_input("Ticker contains").strip().upper()

    hours = {"6h": 6, "24h": 24, "7d": 168, "30d": 720}[lookback]
    rows = conn.execute(
        """SELECT id, ticker, source, form_type, headline, url, published_at,
                  kw_score, llm_score, final_score, tags, rationale
           FROM catalysts
           WHERE final_score >= ?
             AND datetime(published_at) >= datetime('now', ?)
           ORDER BY final_score DESC, published_at DESC
           LIMIT 300""",
        (min_score, f"-{hours} hours"),
    ).fetchall()

    records = [dict(r) for r in rows]
    for r in records:
        r["tags"] = json.loads(r["tags"] or "[]")
    if tag_filter != "All":
        records = [r for r in records if tag_filter in r["tags"]]
    if ticker_filter:
        records = [r for r in records if ticker_filter in r["ticker"]]

    related_map = cdb.load_related_tickers_all(conn)
    for r in records:
        kin = related_map.get(r["ticker"], [])
        r["related"] = ", ".join(kin[:5]) if kin else "—"

    if not records:
        total_in_window = conn.execute(
            "SELECT COUNT(*) FROM catalysts "
            "WHERE datetime(published_at) >= datetime('now', ?)",
            (f"-{hours} hours",),
        ).fetchone()[0]
        if total_in_window == 0:
            st.info(
                f"No catalysts in the last {lookback}. Widen the lookback window "
                "above, or run the poller."
            )
        else:
            st.info(
                f"No catalysts in the last {lookback} at score ≥ {min_score} "
                f"(there are {total_in_window} below that threshold). "
                "Lower the Min score slider above, or change the Tag filter."
            )
        return

    df = pd.DataFrame(records)[[
        "final_score", "ticker", "headline", "related", "source", "form_type",
        "published_at", "kw_score", "llm_score"
    ]]
    st.dataframe(
        df, width="stretch", hide_index=True,
        column_config={
            "final_score": st.column_config.ProgressColumn(
                "Score", min_value=0, max_value=100, format="%d",
            ),
        },
    )
    cdb.mark_seen(conn, [r["id"] for r in records])

    st.subheader("Drilldown")
    fmt = {r["id"]: f"{r['ticker']} — {r['headline'][:80]}" for r in records}
    pick_id = st.selectbox(
        "Catalyst",
        options=[r["id"] for r in records],
        format_func=fmt.get,
    )
    row = next(r for r in records if r["id"] == pick_id)
    st.markdown(f"**{row['ticker']} — score {row['final_score']}**")
    if row.get("rationale"):
        st.write(row["rationale"])
    st.write({"tags": row["tags"], "kw": row["kw_score"], "llm": row["llm_score"]})
    parsed = urlparse(row["url"] or "")
    if parsed.scheme in ("http", "https"):
        st.link_button("Open source", row["url"])
    else:
        st.text(row["url"] or "(no url)")

    # Feedback loop: did this catalyst move any positions?
    _render_portfolio_events_for_catalyst(conn, row["id"])

    st.subheader("Catalyst heatmap — 24h")
    heat = conn.execute(
        """SELECT ticker, SUM(final_score) AS s
           FROM catalysts
           WHERE datetime(published_at) >= datetime('now', '-24 hours')
           GROUP BY ticker ORDER BY s DESC LIMIT 15"""
    ).fetchall()
    if heat:
        hdf = pd.DataFrame([dict(r) for r in heat]).set_index("ticker")
        st.bar_chart(hdf)
