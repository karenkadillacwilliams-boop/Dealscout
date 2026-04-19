"""IPO Tracker page — recently listed stocks via Polygon reference data."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from catalysts import db as cdb
from catalysts.ipo import fetch_recent_ipos

from app_pages.shared import get_conn


@st.cache_data(ttl=3600, show_spinner="Fetching recent IPOs...")
def _load_ipos():
    return fetch_recent_ipos(lookback_days=90)


def render() -> None:
    conn = get_conn()

    st.title("IPO Tracker")
    st.caption("Recently listed stocks from the last 90 days via Polygon.")

    ipos = _load_ipos()
    if not ipos:
        st.info("No recent IPOs found or POLYGON_API_KEY not set.")
        return

    ipo_df = pd.DataFrame([{
        "Ticker": e.ticker,
        "Name": e.name,
        "List Date": e.list_date,
        "Exchange": e.primary_exchange,
    } for e in ipos])

    c1, c2 = st.columns(2)
    c1.metric("Recent IPOs (90d)", len(ipo_df))
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    recent_count = len(ipo_df[ipo_df["List Date"] >= week_ago])
    c2.metric("Last 7 days", recent_count)

    search = st.text_input("Search IPOs",
                           placeholder="Filter by ticker or name").strip().upper()
    if search:
        ipo_df = ipo_df[
            ipo_df["Ticker"].str.contains(search, na=False) |
            ipo_df["Name"].str.upper().str.contains(search, na=False)
        ]

    st.dataframe(ipo_df, width="stretch", hide_index=True)

    st.subheader("Add IPO to watchlist")
    ipo_tickers = sorted(ipo_df["Ticker"].tolist())
    if ipo_tickers:
        selected = st.selectbox("Select ticker", options=ipo_tickers)
        if st.button("Add to Universe", type="primary"):
            cdb.upsert_universe(conn, selected)
            st.success(f"Added {selected} to universe")
            st.rerun()
