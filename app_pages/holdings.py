"""Holdings page — positions, P/L, allocation pie."""
from __future__ import annotations

import plotly.express as px
import streamlit as st

import portfolio
from tickers import NAMES

from app_pages.shared import fmt_money, price_context


def render() -> None:
    _tickers, _prices, _returns_df, last_prices = price_context()

    st.title("Holdings")
    pos = portfolio.positions(last_prices)
    if pos.empty:
        st.info("No positions yet. Record a buy on the **Trades** page to get started.")
        return

    total_mv = pos["market_value"].sum()
    total_unr = pos["unrealized_pl"].sum()
    total_rea = pos["realized_pl"].sum()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Positions",     len(pos))
    c2.metric("Market value",  fmt_money(total_mv))
    c3.metric("Unrealized P/L", fmt_money(total_unr))
    c4.metric("Realized P/L",   fmt_money(total_rea))

    view = pos.copy()
    view.insert(1, "name", view["ticker"].map(NAMES).fillna(""))
    view = view.rename(columns={
        "ticker": "Ticker", "name": "Name", "qty": "Qty",
        "avg_cost": "Avg cost", "last": "Last",
        "market_value": "Market value",
        "unrealized_pl": "Unrealized P/L",
        "realized_pl": "Realized P/L",
        "total_pl": "Total P/L",
    })
    st.dataframe(
        view.style.format({
            "Qty": "{:,.4f}",
            "Avg cost": "${:,.2f}", "Last": "${:,.2f}",
            "Market value": "${:,.2f}",
            "Unrealized P/L": "${:,.2f}",
            "Realized P/L": "${:,.2f}",
            "Total P/L": "${:,.2f}",
        }),
        width="stretch", hide_index=True,
    )

    if total_mv > 0:
        st.subheader("Allocation")
        fig = px.pie(pos, values="market_value", names="ticker", hole=0.45)
        st.plotly_chart(fig, width="stretch")
