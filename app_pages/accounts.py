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

_ALL_ACCOUNTS_ID = -1  # sentinel: drilldown entry for cross-account view

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
        # "All accounts" is the default cross-account view (absorbed from the
        # removed Holdings page); account-specific drilldown follows.
        drill_options = [_ALL_ACCOUNTS_ID] + [a["id"] for a in accounts]

        def _drill_label(i: int) -> str:
            if i == _ALL_ACCOUNTS_ID:
                return "All accounts"
            return next(a["name"] for a in accounts if a["id"] == i)

        pick = st.selectbox(
            "Account to drill into",
            options=drill_options,
            format_func=_drill_label,
        )
        if pick == _ALL_ACCOUNTS_ID:
            _render_all_accounts(conn, last_prices)
        else:
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


def _render_all_accounts(conn, last_prices: dict) -> None:
    """Cross-account position summary (absorbed from the former Holdings page).

    Events timeline is skipped in this view — it only makes sense per-account.
    """
    pos = portfolio.positions_all_accounts(conn, last_prices)
    _warn_unmatched_sells(pos)
    if pos.empty:
        st.info("No positions across any account yet. Record a buy on Trades "
                "or upload via Import.")
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


def _warn_unmatched_sells(df) -> None:
    """Say so when sells were excluded for lack of a matching buy.

    Without this the omission is invisible and realized P/L simply reads low.
    """
    unmatched = df.attrs.get(portfolio.UNMATCHED_SELLS_ATTR) or {}
    if not unmatched:
        return
    detail = ", ".join(f"{tk} ({qty:,.4g})" for tk, qty in sorted(unmatched.items()))
    st.warning(
        f"⚠️ Realized P/L excludes {len(unmatched)} "
        f"ticker{'s' if len(unmatched) != 1 else ''} with sells that have no "
        f"matching buy on record: {detail}. There is no cost basis to compute "
        f"against, so those gains are missing from the totals below. Usually "
        f"this means the purchase predates your imported history — add the "
        f"opening trade to correct it."
    )


def _render_drilldown(conn, account_id: int, last_prices: dict) -> None:
    df = portfolio.positions_for_account(conn, account_id, last_prices)
    _warn_unmatched_sells(df)
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
