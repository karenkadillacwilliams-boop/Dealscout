"""Shared helpers for app_pages/ — cached DB connection and price loader.

The DB connection is wrapped in @st.cache_resource, which is process-wide and
shared by every concurrent viewer — not per-session. It is opened once and
reused across page renders and users (replacing the pre-split module-scope
connection that contended with poller writes).

Because that connection outlives any single request, the Turso embedded replica
it wraps would otherwise never see the poller's 15-minute writes: connect()
syncs only at open time. synced_conn() below refreshes it on a short TTL so
deployed viewers get fresh catalysts without a container restart.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from catalysts import db as cdb
from data import add_grades, fetch_history, period_returns
from tickers import TICKERS


SYNC_TTL_SECONDS = 60


@st.cache_resource
def get_conn():
    """The process-wide DB connection, opened once and shared by all viewers."""
    import portfolio
    portfolio.init_db()
    conn = cdb.connect()
    cdb.migrate(conn)
    cdb.seed_universe_if_empty(conn, TICKERS)
    return conn


@st.cache_data(ttl=SYNC_TTL_SECONDS, show_spinner=False)
def _replica_sync_tick() -> bool:
    """Pull from Turso at most once per SYNC_TTL_SECONDS, app-wide.

    Cached on the value, so repeated calls inside the window are free. The
    poller runs every 15 min, so a 60s ceiling is well inside the write cadence
    while keeping reruns cheap. Cleared by the sidebar 'Refresh' button, which
    calls st.cache_data.clear() and therefore forces an immediate re-sync.
    """
    return cdb.sync(get_conn())


def synced_conn():
    """The shared connection, refreshed from Turso on a short TTL."""
    _replica_sync_tick()
    return get_conn()


def active_tickers() -> list[str]:
    return cdb.load_active_universe(get_conn()) or TICKERS


@st.cache_data(ttl=300, show_spinner=False)
def _cached_prices(tickers_tuple: tuple[str, ...]):
    return fetch_history(list(tickers_tuple), period="6mo")


def price_context():
    """Tuple of (active_tickers, prices_df, returns_df, last_prices)."""
    tickers = active_tickers()
    prices = _cached_prices(tuple(tickers))
    returns_df = add_grades(period_returns(prices))
    last_prices = (
        dict(zip(returns_df["ticker"], returns_df["last"]))
        if not returns_df.empty else {}
    )
    return tickers, prices, returns_df, last_prices


def fmt_pct(v):
    return "—" if pd.isna(v) else f"{v:+.2f}%"


def fmt_money(v):
    return "—" if pd.isna(v) else f"${v:,.2f}"


def active_accounts() -> list[dict]:
    """All active accounts for the current session."""
    from catalysts import db as cdb
    return cdb.load_accounts(get_conn())
