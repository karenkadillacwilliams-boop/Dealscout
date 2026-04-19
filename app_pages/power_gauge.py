"""Power Gauge page — Chaikin-style composite rating."""
from __future__ import annotations

import streamlit as st

from data import compute_power_gauge_ratings
from tickers import NAMES

from app_pages.shared import active_tickers


def render() -> None:
    st.title("Power Gauge")
    st.caption("Chaikin-style 4-category composite rating ported from stock_evaluator.")

    pg = compute_power_gauge_ratings(active_tickers())
    if pg.empty:
        st.warning("No ratings available — check your network and refresh.")
        return

    bucket_counts = pg["label"].value_counts()
    cols = st.columns(7)
    bucket_order = ["Very Bullish", "Bullish", "Neutral+", "Neutral",
                    "Neutral-", "Bearish", "Very Bearish"]
    for i, label in enumerate(bucket_order):
        cols[i].metric(label, int(bucket_counts.get(label, 0)))

    view = pg.copy()
    view.insert(1, "name", view["ticker"].map(NAMES).fillna(""))
    view = view.rename(columns={
        "ticker": "Ticker", "name": "Name", "label": "Rating", "score": "Score",
        "fin": "Fin", "earn": "Earn", "tech": "Tech", "exp": "Exp",
        "adjustment": "Adjustment",
    })
    st.dataframe(
        view[["Ticker", "Name", "Rating", "Score", "Fin", "Earn",
              "Tech", "Exp", "Adjustment"]],
        width="stretch", hide_index=True,
    )

    with st.expander("Ticker drilldown"):
        pick = st.selectbox("Ticker", options=sorted(pg["ticker"].tolist()))
        row = pg[pg["ticker"] == pick].iloc[0]
        st.markdown(f"**{pick} — {row['label']}** (score {row['score']})")
        st.write(row["narrative"])
        st.write({
            "Financials": row["fin"], "Earnings": row["earn"],
            "Technicals": row["tech"], "Experts": row["exp"],
            "Adjustment": row["adjustment"],
        })
