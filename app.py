"""Dealscout — Streamlit entry point.

Page bodies live in app_pages/*.py, each exposing a `render()` function.
This file is only the navigation shell and session-level sidebar status.
"""
from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="Dealscout", page_icon="📈", layout="wide")

from app_pages._boot import hydrate_env_from_secrets

hydrate_env_from_secrets()

from app_pages import (
    accounts,
    catalysts_page,
    dashboard,
    events,
    import_trades,
    ipo_tracker,
    options_pulse,
    performance,
    power_gauge,
    trades,
    universe,
)
from app_pages.shared import active_tickers, get_conn
from catalysts import db as cdb


def _sidebar_header() -> None:
    st.sidebar.title("📈 Dealscout")

    conn = get_conn()
    tickers = active_tickers()
    unseen = cdb.unseen_alert_count(conn)
    last_poll = cdb.last_poll_time(conn)
    total_opts = conn.execute(
        "SELECT COUNT(*) FROM options_snapshot"
    ).fetchone()[0]
    pending_events = cdb.pending_event_count(conn)

    st.sidebar.caption(f"Universe: {len(tickers)} tickers")
    st.sidebar.caption(f"Last catalyst poll: {last_poll or '—'}")
    if unseen:
        st.sidebar.warning(f"🔴 {unseen} unseen alert{'s' if unseen != 1 else ''}")
    if pending_events:
        st.sidebar.warning(
            f"⚡ {pending_events} position event{'s' if pending_events != 1 else ''} "
            f"to review"
        )
    if total_opts:
        st.sidebar.caption(f"Options tracked: {total_opts}")
    if st.sidebar.button("🔄 Refresh prices"):
        st.cache_data.clear()
        st.rerun()


_sidebar_header()

_nav = st.navigation({
    "Signals": [
        st.Page(dashboard.render,       title="Dashboard",     icon="📊",
                url_path="dashboard", default=True),
        st.Page(catalysts_page.render,  title="Catalysts",     icon="📰",
                url_path="catalysts"),
        st.Page(options_pulse.render,   title="Options Pulse", icon="📈",
                url_path="options-pulse"),
        st.Page(power_gauge.render,     title="Power Gauge",   icon="⚡",
                url_path="power-gauge"),
        st.Page(ipo_tracker.render,     title="IPO Tracker",   icon="🆕",
                url_path="ipo-tracker"),
    ],
    "Portfolio": [
        st.Page(accounts.render,        title="Accounts",      icon="🏦",
                url_path="accounts"),
        st.Page(events.render,          title="Events",        icon="⚡",
                url_path="events"),
        st.Page(import_trades.render,   title="Import",        icon="⬆",
                url_path="import"),
        st.Page(trades.render,          title="Trades",        icon="🧾",
                url_path="trades"),
        st.Page(performance.render,     title="Performance",   icon="📉",
                url_path="performance"),
    ],
    "Admin": [
        st.Page(universe.render,        title="Universe",      icon="🌐",
                url_path="universe"),
    ],
})
_nav.run()
