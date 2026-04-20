"""Trades page — account-scoped manual trade entry and deletion."""
from __future__ import annotations

from datetime import date

import streamlit as st

import portfolio
from app_pages.shared import active_accounts, get_conn, price_context


def render() -> None:
    conn = get_conn()
    accounts = active_accounts()
    _t, _p, _r, last_prices = price_context()

    st.title("Trades")
    st.caption("Record buys and sells per account. Positions are derived from this ledger.")

    if not accounts:
        st.info("No accounts yet. Create one on the **Accounts** page first.")
        return

    acc_options = {a["name"]: a["id"] for a in accounts}
    selected_name = st.selectbox("Account", options=list(acc_options.keys()))
    account_id = acc_options[selected_name]

    with st.form("new_trade", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
        with c1:
            ticker = st.text_input("Ticker").strip().upper()
        with c2:
            side = st.selectbox("Side", ["BUY", "SELL"])
        with c3:
            qty = st.number_input("Qty", min_value=0.0001, value=1.0,
                                    step=1.0, format="%.4f")
        with c4:
            default_price = float(last_prices.get(ticker, 0.0)) if last_prices else 0.0
            price = st.number_input("Price", min_value=0.0,
                                      value=default_price, step=0.01, format="%.2f")
        c5, c6 = st.columns([1, 3])
        with c5:
            trade_date = st.date_input("Trade date", value=date.today())
        with c6:
            notes = st.text_input("Notes (optional)")
        submitted = st.form_submit_button("Add trade", type="primary")
        if submitted:
            if not ticker:
                st.error("Ticker is required.")
            else:
                try:
                    tid, was_new = portfolio.add_trade_to_account(
                        conn, account_id=account_id, ticker=ticker,
                        side=side, qty=qty, price=price,
                        trade_date=trade_date.isoformat(), notes=notes,
                    )
                    if was_new:
                        st.success(f"Recorded {side} {qty} {ticker} @ ${price:.2f}")
                    else:
                        st.info(f"Duplicate trade skipped (trade #{tid} already exists).")
                except ValueError as e:
                    st.error(str(e))

    st.subheader("Trade history")
    trades = portfolio.list_trades_df(conn, account_id=account_id)
    if trades.empty:
        st.info("No trades yet for this account.")
    else:
        st.dataframe(
            trades.drop(columns=["account_id"]),
            width="stretch", hide_index=True,
        )
        with st.expander("Delete a trade"):
            tid = st.number_input("Trade ID to delete", min_value=1, step=1)
            if st.button("Delete", type="secondary"):
                from catalysts import db as cdb
                cdb.delete_trade(conn, int(tid))
                st.rerun()
