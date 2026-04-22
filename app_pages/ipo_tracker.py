"""IPO Tracker page — recently listed stocks via Polygon reference data."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from catalysts import db as cdb
from catalysts.ipo import fetch_recent_ipos

from app_pages.shared import get_conn

# Auto-review criterion: IPOs listed on one of these Polygon primary_exchange
# codes within the last 7 days. OTC / foreign ADRs require a deliberate add
# via the Older section, avoiding noise in the default flow.
_PRIMARY_EXCHANGES = ("XNYS", "XNAS")


@st.cache_data(ttl=3600, show_spinner="Fetching recent IPOs...")
def _load_ipos():
    return fetch_recent_ipos(lookback_days=90)


def _entry_dict(e):
    return {
        "Ticker": e.ticker,
        "Name": e.name,
        "List Date": e.list_date,
        "Exchange": e.primary_exchange,
    }


def render() -> None:
    conn = get_conn()

    st.title("IPO Tracker")
    st.caption("Recently listed stocks from the last 90 days via Polygon.")

    ipos = _load_ipos()
    if not ipos:
        st.info("No recent IPOs found or POLYGON_API_KEY not set.")
        return

    today = date.today()
    week_ago = (today - timedelta(days=7)).isoformat()
    active_universe = set(cdb.load_active_universe(conn))

    needs_review = [
        e for e in ipos
        if e.list_date >= week_ago
        and e.ticker not in active_universe
        and e.primary_exchange in _PRIMARY_EXCHANGES
    ]
    in_universe = [
        e for e in ipos
        if e.list_date >= week_ago and e.ticker in active_universe
    ]
    older = [e for e in ipos if e.list_date < week_ago]

    c1, c2, c3 = st.columns(3)
    c1.metric("Needs review", len(needs_review))
    c2.metric("Already tracked (7d)", len(in_universe))
    c3.metric("Older (7-90d)", len(older))

    # ── Needs review ────────────────────────────────────────────────────────
    st.subheader("Needs review (last 7 days, NYSE/NASDAQ)")
    if not needs_review:
        st.caption("Nothing to review — all recent NYSE/NASDAQ IPOs are in the "
                   "universe or there haven't been any.")
    else:
        st.caption("Check the box on rows you want to add to the watchlist, "
                   "then click Sync all.")
        review_df = pd.DataFrame([
            {**_entry_dict(e), "Add to universe": False} for e in needs_review
        ])
        edited = st.data_editor(
            review_df,
            column_config={
                "Add to universe": st.column_config.CheckboxColumn(
                    "Add to universe", default=False,
                ),
                "Ticker": st.column_config.TextColumn(disabled=True),
                "Name": st.column_config.TextColumn(disabled=True),
                "List Date": st.column_config.TextColumn(disabled=True),
                "Exchange": st.column_config.TextColumn(disabled=True),
            },
            hide_index=True,
            width="stretch",
            key="ipo_review_editor",
        )
        checked = edited[edited["Add to universe"]] if not edited.empty else edited
        n_checked = len(checked)
        if st.button(f"Sync all ({n_checked})", type="primary",
                     disabled=n_checked == 0):
            for _, row in checked.iterrows():
                cdb.upsert_universe(conn, row["Ticker"], row["Name"])
            st.success(f"Added {n_checked} IPO{'s' if n_checked != 1 else ''} to universe")
            st.rerun()

    # ── Already tracked ────────────────────────────────────────────────────
    st.subheader("Already tracked (last 7 days)")
    if not in_universe:
        st.caption("No recent IPOs are in the universe yet.")
    else:
        st.dataframe(
            pd.DataFrame([_entry_dict(e) for e in in_universe]),
            width="stretch", hide_index=True,
        )

    # ── Older (7-90d) ──────────────────────────────────────────────────────
    st.subheader("Older (7-90 days)")
    if not older:
        st.caption("No IPOs in the 7-90 day window.")
    else:
        older_df = pd.DataFrame([_entry_dict(e) for e in older])
        search = st.text_input(
            "Search older IPOs",
            placeholder="Filter by ticker or name",
        ).strip().upper()
        if search:
            older_df = older_df[
                older_df["Ticker"].str.contains(search, na=False)
                | older_df["Name"].str.upper().str.contains(search, na=False)
            ]
        st.dataframe(older_df, width="stretch", hide_index=True)

        older_tickers = sorted(older_df["Ticker"].tolist())
        if older_tickers:
            selected = st.selectbox(
                "Add an older IPO to universe",
                options=older_tickers,
                key="ipo_older_select",
            )
            if st.button("Add to Universe", type="secondary",
                          key="ipo_older_add"):
                name_row = older_df[older_df["Ticker"] == selected]
                name = (
                    name_row["Name"].iloc[0]
                    if not name_row.empty else None
                )
                cdb.upsert_universe(conn, selected, name)
                st.success(f"Added {selected} to universe")
                st.rerun()
