"""Catalysts page — scored filings/news with filter/drilldown and related tickers."""
from __future__ import annotations

import json
from urllib.parse import urlparse

import pandas as pd
import streamlit as st

from catalysts import db as cdb

from app_pages.shared import get_conn


def render() -> None:
    conn = get_conn()

    st.title("Catalysts")
    st.caption("Ingested filings and news, scored for M&A / partnership / product signal.")

    c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
    with c1:
        min_score = st.slider("Min score", 0, 100, 70, 5)
    with c2:
        lookback = st.selectbox("Lookback", ["6h", "24h", "7d", "30d"], index=1)
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
        st.info("No catalysts match the current filters. Run the poller or lower the min score.")
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
