"""Holdings page — account filter, aggregated positions across all accounts."""
from __future__ import annotations

import plotly.express as px
import streamlit as st

import portfolio
from tickers import NAMES

from app_pages.shared import (
    active_accounts, fmt_money, get_conn, price_context,
)


def render() -> None:
    conn = get_conn()
    accounts = active_accounts()
    _t, _p, _r, last_prices = price_context()

    st.title("Holdings")

    if not accounts:
        st.info("No accounts yet. Go to the **Accounts** page to create one, "
                "then use **Import** or **Trades** to add positions.")
        return

    options = [("All accounts", None)] + [(a["name"], a["id"]) for a in accounts]
    labels = [o[0] for o in options]
    choice = st.selectbox("Account", options=labels, index=0)
    account_id = dict(options)[choice]

    if account_id is None:
        pos = portfolio.positions_all_accounts(conn, last_prices)
    else:
        pos = portfolio.positions_for_account(conn, account_id, last_prices)

    if pos.empty:
        st.info("No positions in this view. Record a buy on Trades or upload "
                "via Import.")
        return

    total_mv = pos["market_value"].sum()
    total_unr = pos["unrealized_pl"].sum()
    total_rea = pos["realized_pl"].sum()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Rows", len(pos))
    c2.metric("Market value", fmt_money(total_mv))
    c3.metric("Unrealized P/L", fmt_money(total_unr))
    c4.metric("Realized P/L", fmt_money(total_rea))

    view = pos.copy()
    if "ticker" in view.columns:
        view.insert(view.columns.get_loc("ticker") + 1, "name",
                     view["ticker"].map(NAMES).fillna(""))
    rename_map = {
        "account_name": "Account", "ticker": "Ticker", "name": "Name",
        "qty": "Qty", "avg_cost": "Avg cost", "last": "Last",
        "market_value": "Market value",
        "unrealized_pl": "Unrealized P/L",
        "realized_pl": "Realized P/L", "total_pl": "Total P/L",
    }
    view = view.rename(columns=rename_map)
    display_cols = [c for c in
                     ["Account", "Ticker", "Name", "Qty", "Avg cost", "Last",
                      "Market value", "Unrealized P/L", "Realized P/L", "Total P/L"]
                     if c in view.columns]

    st.dataframe(
        view[display_cols].style.format({
            "Qty": "{:,.4f}",
            "Avg cost": "${:,.2f}", "Last": "${:,.2f}",
            "Market value": "${:,.2f}", "Unrealized P/L": "${:,.2f}",
            "Realized P/L": "${:,.2f}", "Total P/L": "${:,.2f}",
        }, na_rep="—"),
        width="stretch", hide_index=True,
    )

    if total_mv > 0 and "Ticker" in view.columns:
        st.subheader("Allocation")
        fig = px.pie(view, values="Market value", names="Ticker", hole=0.45)
        st.plotly_chart(fig, width="stretch")
