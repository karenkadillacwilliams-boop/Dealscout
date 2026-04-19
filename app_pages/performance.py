"""Performance page — per-ticker price history indexed to 100."""
from __future__ import annotations

import plotly.express as px
import streamlit as st

from app_pages.shared import price_context


def render() -> None:
    _tickers, prices, _returns_df, _last = price_context()

    st.title("Performance")
    st.caption("Per-ticker price history. Pick one or more tickers to chart.")

    if prices.empty:
        st.warning("No price data fetched.")
        return

    default_pick = ["NVDA"] if "NVDA" in prices.columns else [prices.columns[0]]
    picks = st.multiselect(
        "Tickers", options=list(prices.columns), default=default_pick,
    )
    if picks:
        normalized = prices[picks].dropna(how="all")
        normalized = normalized / normalized.iloc[0] * 100.0
        fig = px.line(normalized, title="Indexed to 100 at start of window")
        fig.update_layout(yaxis_title="Index (start = 100)", xaxis_title="")
        st.plotly_chart(fig, width="stretch")
