"""Universe page — manage active tickers."""
from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from catalysts import db as cdb

from app_pages.shared import active_tickers, get_conn

_AZ = ZoneInfo("America/Phoenix")


def _to_az(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).astimezone(_AZ).strftime(
            "%Y-%m-%d %H:%M:%S MST"
        )
    except Exception:
        return iso


def render() -> None:
    conn = get_conn()
    active = active_tickers()

    st.title("Universe")
    st.caption("Active tickers used by the Dashboard, Power Gauge, and Catalyst poller.")

    st.metric("Active tickers", len(active))

    rows = conn.execute(
        "SELECT ticker, name, added_at, active FROM universe ORDER BY ticker"
    ).fetchall()
    df = pd.DataFrame([dict(r) for r in rows])
    if not df.empty:
        df["added_at"] = df["added_at"].map(_to_az)
    st.dataframe(df, width="stretch", hide_index=True)

    st.subheader("Add ticker")
    with st.form("add_ticker", clear_on_submit=True):
        c1, c2, c3 = st.columns([1, 2, 1])
        with c1:
            new_t = st.text_input("Ticker").strip().upper()
        with c2:
            new_n = st.text_input("Name (optional)").strip()
        with c3:
            add = st.form_submit_button("Add", type="primary")
        if add:
            if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", new_t or ""):
                cdb.upsert_universe(conn, new_t, new_n or None)
                st.success(f"Added {new_t}")
                st.rerun()
            else:
                st.error("Invalid ticker format.")

    st.subheader("Remove ticker")
    rm = st.selectbox("Ticker to deactivate", options=active)
    if st.button("Deactivate", type="secondary"):
        cdb.deactivate_ticker(conn, rm)
        st.success(f"Deactivated {rm}")
        st.rerun()

    st.subheader("Bulk import")
    raw = st.text_area("Paste comma- or newline-separated tickers")
    if st.button("Import"):
        tokens = [t.strip().upper() for t in re.split(r"[,\s]+", raw) if t.strip()]
        added, bad = 0, []
        for t in tokens:
            if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", t):
                cdb.upsert_universe(conn, t)
                added += 1
            else:
                bad.append(t)
        st.success(f"Added {added} tickers.")
        if bad:
            st.warning(f"Skipped invalid: {', '.join(bad)}")
        st.rerun()
