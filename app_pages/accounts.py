"""Accounts page — CRUD + per-account drilldown."""
from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.express as px
import streamlit as st

import portfolio
from catalysts import db as cdb
from tickers import NAMES

from app_pages.shared import (
    active_accounts, fmt_money, get_conn, price_context,
)

_TYPES = ["taxable", "roth", "traditional", "401k", "hsa", "joint", "other"]
_BROKERS = ["fidelity", "schwab", "robinhood", "moomoo", "vanguard", "other"]


def _rollup(conn, accounts: list[dict], last_prices: dict) -> dict:
    total_mv = 0.0
    total_unr = 0.0
    total_rea = 0.0
    total_positions = 0
    for a in accounts:
        df = portfolio.positions_for_account(conn, a["id"], last_prices)
        if df.empty:
            continue
        total_mv  += float(df["market_value"].sum())
        total_unr += float(df["unrealized_pl"].sum())
        total_rea += float(df["realized_pl"].sum())
        total_positions += len(df)
    return {
        "account_count": len(accounts),
        "positions": total_positions,
        "market_value": total_mv,
        "unrealized_pl": total_unr,
        "realized_pl": total_rea,
        "pending_events": cdb.pending_event_count(conn),
    }


def _account_row(conn, a: dict, last_prices: dict) -> dict:
    df = portfolio.positions_for_account(conn, a["id"], last_prices)
    pending = conn.execute(
        "SELECT COUNT(*) FROM events WHERE account_id=? AND status='pending'",
        (a["id"],),
    ).fetchone()[0]
    return {
        "Account":    a["name"],
        "Type":       a["type"],
        "Broker":     a["broker"],
        "Opened":     a["opened_date"],
        "Positions":  len(df),
        "Market":     float(df["market_value"].sum()) if not df.empty else 0.0,
        "Unrealized": float(df["unrealized_pl"].sum()) if not df.empty else 0.0,
        "Realized":   float(df["realized_pl"].sum()) if not df.empty else 0.0,
        "Pending":    pending,
        "_id":        a["id"],
    }


def render() -> None:
    conn = get_conn()
    _t, _p, _r, last_prices = price_context()

    st.title("Accounts")
    st.caption("Your investment accounts, aggregated positions, and drilldown.")

    accounts = active_accounts()

    if not accounts:
        st.info("No accounts yet — create one below to get started.")
    else:
        roll = _rollup(conn, accounts, last_prices)
        cols = st.columns(6)
        cols[0].metric("Accounts",    roll["account_count"])
        cols[1].metric("Positions",   roll["positions"])
        cols[2].metric("Market",      fmt_money(roll["market_value"]))
        cols[3].metric("Unrealized",  fmt_money(roll["unrealized_pl"]))
        cols[4].metric("Realized",    fmt_money(roll["realized_pl"]))
        cols[5].metric("Pending events", roll["pending_events"])

        table = pd.DataFrame([_account_row(conn, a, last_prices) for a in accounts])
        st.dataframe(
            table.drop(columns=["_id"]).style.format({
                "Market": "${:,.2f}", "Unrealized": "${:,.2f}",
                "Realized": "${:,.2f}",
            }, na_rep="—"),
            width="stretch", hide_index=True,
        )

        st.subheader("Drilldown")
        pick = st.selectbox(
            "Account to drill into",
            options=[a["id"] for a in accounts],
            format_func=lambda i: next(a["name"] for a in accounts if a["id"] == i),
        )
        _render_drilldown(conn, pick, last_prices)

    st.subheader("Create account")
    _render_create_form(conn)

    if accounts:
        st.subheader("Deactivate account")
        rm_id = st.selectbox(
            "Account to deactivate",
            options=[a["id"] for a in accounts],
            format_func=lambda i: next(a["name"] for a in accounts if a["id"] == i),
            key="deactivate_pick",
        )
        if st.button("Deactivate", type="secondary"):
            cdb.deactivate_account(conn, rm_id)
            st.success("Account deactivated. Its trades and events are preserved.")
            st.rerun()


def _render_drilldown(conn, account_id: int, last_prices: dict) -> None:
    df = portfolio.positions_for_account(conn, account_id, last_prices)
    if df.empty:
        st.info("No positions in this account yet. Use Import or Trades to add some.")
    else:
        view = df.copy()
        view.insert(1, "name", view["ticker"].map(NAMES).fillna(""))
        view = view.rename(columns={
            "ticker": "Ticker", "name": "Name", "qty": "Qty",
            "avg_cost": "Avg cost", "last": "Last",
            "market_value": "Market", "unrealized_pl": "Unrealized",
            "realized_pl": "Realized", "total_pl": "Total",
        })
        st.dataframe(
            view.style.format({
                "Qty": "{:,.4f}", "Avg cost": "${:,.2f}", "Last": "${:,.2f}",
                "Market": "${:,.2f}", "Unrealized": "${:,.2f}",
                "Realized": "${:,.2f}", "Total": "${:,.2f}",
            }, na_rep="—"),
            width="stretch", hide_index=True,
        )
        mv = float(view["Market"].sum())
        if mv > 0:
            fig = px.pie(view, values="Market", names="Ticker", hole=0.45,
                          title="Allocation")
            st.plotly_chart(fig, width="stretch")

    events = cdb.load_events(conn, account_id=account_id)
    if events:
        ev_df = pd.DataFrame(events)[[
            "event_date", "ticker", "move_window", "move_pct",
            "pnl_dollars", "catalyst_type", "status",
        ]].rename(columns={
            "event_date": "Date", "ticker": "Ticker",
            "move_window": "Window", "move_pct": "Move %",
            "pnl_dollars": "P&L $", "catalyst_type": "Tag",
            "status": "Status",
        })
        st.caption("Events on this account")
        st.dataframe(
            ev_df.style.format({"Move %": "{:+.2f}%", "P&L $": "${:,.2f}"}),
            width="stretch", hide_index=True,
        )


def _render_create_form(conn) -> None:
    with st.form("create_account", clear_on_submit=True):
        c1, c2, c3 = st.columns([2, 1, 1])
        with c1:
            name = st.text_input("Name", placeholder="Main Roth / Kid's UTMA / etc.").strip()
        with c2:
            acc_type = st.selectbox("Type", options=_TYPES)
        with c3:
            broker = st.selectbox("Broker", options=_BROKERS)
        c4, c5 = st.columns([1, 1])
        with c4:
            opened = st.date_input("Opened", value=date.today())
        with c5:
            initial_cash = st.number_input(
                "Initial cash ($)", min_value=0.0, value=0.0, step=100.0,
            )
        with st.expander("Advanced — event thresholds"):
            c6, c7 = st.columns(2)
            with c6:
                daily = st.number_input(
                    "Daily move % (blank = 5.0 default)",
                    min_value=0.0, max_value=50.0, value=0.0, step=0.5,
                )
            with c7:
                five = st.number_input(
                    "5-day move % (blank = 10.0 default)",
                    min_value=0.0, max_value=100.0, value=0.0, step=0.5,
                )

        ok = st.form_submit_button("Create account", type="primary")
        if ok:
            if not name:
                st.error("Name is required.")
                return
            try:
                cdb.create_account(
                    conn, name=name, type=acc_type, broker=broker,
                    opened_date=opened.isoformat(), initial_cash=initial_cash,
                    event_daily_pct=(daily or None),
                    event_5day_pct=(five or None),
                )
                st.success(f"Created {name}")
                st.rerun()
            except Exception as exc:
                st.error(f"Create failed: {exc}")
