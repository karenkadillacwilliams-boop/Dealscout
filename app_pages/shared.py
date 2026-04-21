"""Shared helpers for app_pages/ — cached DB connection and price loader.

The DB connection is wrapped in @st.cache_resource so it is opened once per
Streamlit session and reused across page renders (replaces the pre-split
module-scope connection that lived for the lifetime of the process and
contended with poller writes).
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from catalysts import db as cdb
from data import add_grades, fetch_history, period_returns
from tickers import TICKERS


@st.cache_resource
def get_conn():
    """One DB connection per Streamlit session, reused across page renders."""
    import portfolio
    portfolio.init_db()
    conn = cdb.connect()
    cdb.migrate(conn)
    cdb.seed_universe_if_empty(conn, TICKERS)
    return conn


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
