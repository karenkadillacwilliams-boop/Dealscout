"""Trades page — buy/sell ledger."""
from __future__ import annotations

import streamlit as st

import portfolio

from app_pages.shared import active_tickers, price_context


def render() -> None:
    _tickers, _prices, _returns_df, last_prices = price_context()

    st.title("Trades")
    st.caption("Record buys and sells. Positions are derived from this ledger.")

    with st.form("new_trade", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
        with c1:
            ticker = st.selectbox("Ticker", options=sorted(active_tickers()))
        with c2:
            side = st.selectbox("Side", ["BUY", "SELL"])
        with c3:
            qty = st.number_input("Qty", min_value=0.0001, value=1.0, step=1.0, format="%.4f")
        with c4:
            default_price = float(last_prices.get(ticker, 0.0)) if last_prices else 0.0
            price = st.number_input(
                "Price", min_value=0.0, value=default_price, step=0.01, format="%.2f"
            )
        notes = st.text_input("Notes (optional)")
        submitted = st.form_submit_button("Add trade", type="primary")
        if submitted:
            try:
                portfolio.add_trade(ticker, side, qty, price, notes)
                st.success(f"Recorded {side} {qty} {ticker} @ ${price:.2f}")
            except ValueError as e:
                st.error(str(e))

    st.subheader("Trade history")
    trades = portfolio.list_trades()
    if trades.empty:
        st.info("No trades yet.")
    else:
        st.dataframe(trades, width="stretch", hide_index=True)
        with st.expander("Delete a trade"):
            tid = st.number_input("Trade ID to delete", min_value=1, step=1)
            if st.button("Delete", type="secondary"):
                portfolio.delete_trade(int(tid))
                st.rerun()
