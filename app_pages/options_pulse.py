"""Options Pulse page — cheap convexity screener + UOA feed."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from catalysts import db as cdb

from app_pages.shared import get_conn


def _iv_color(val):
    if pd.isna(val):
        return ""
    if val < 30:
        return "background-color: #d4edda"
    if val <= 60:
        return "background-color: #fff3cd"
    return "background-color: #f8d7da"


def _flow_color(val):
    if val == "sweep":
        return "color: red; font-weight: bold"
    if val == "block":
        return "color: orange"
    return ""


def render() -> None:
    conn = get_conn()

    st.title("Options Pulse")
    st.caption(
        "Cheap convexity screener — calls & puts under $2, 7-28 DTE, "
        "ranked by catalyst-weighted leverage + IV rank."
    )

    opts_rows = cdb.load_all_options(conn)
    if not opts_rows:
        st.info("No qualifying options found. Run the poller with POLYGON_API_KEY set.")
    else:
        opts_df = pd.DataFrame(opts_rows)

        c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
        with c1:
            all_tickers = sorted(opts_df["ticker"].unique())
            sel_tickers = st.multiselect("Ticker", options=all_tickers, default=all_tickers)
        with c2:
            sel_type = st.selectbox("Type", ["All", "call", "put"])
        with c3:
            sel_dte = st.slider("Max DTE", 7, 28, 28)
        with c4:
            sel_ask = st.slider("Max ask ($)", 0.05, 2.00, 2.00, 0.05)

        mask = opts_df["ticker"].isin(sel_tickers)
        if sel_type != "All":
            mask &= opts_df["contract_type"] == sel_type
        mask &= opts_df["dte"] <= sel_dte
        mask &= opts_df["ask"] <= sel_ask
        filtered = opts_df[mask].copy()

        s1, s2, s3 = st.columns(3)
        s1.metric("Qualifying contracts", len(filtered))
        cheap_vol = (
            filtered[filtered["iv_rank"] < 30]["ticker"].nunique()
            if not filtered.empty else 0
        )
        s2.metric("Cheap vol tickers (IV rank < 30)", cheap_vol)
        if not filtered.empty:
            s3.metric("Best composite", f"{filtered['composite_score'].max():.1f}")

        if filtered.empty:
            st.info("No contracts match current filters.")
        else:
            display = filtered[[
                "ticker", "contract_type", "strike", "expiration_date", "dte",
                "ask", "leverage_ratio", "iv", "iv_rank", "composite_score",
                "volume", "open_interest",
            ]].rename(columns={
                "ticker": "Ticker", "contract_type": "Type", "strike": "Strike",
                "expiration_date": "Exp", "dte": "DTE", "ask": "Ask",
                "leverage_ratio": "Leverage", "iv": "IV", "iv_rank": "IV Rank",
                "composite_score": "Composite", "volume": "Volume",
                "open_interest": "OI",
            })

            st.dataframe(
                display.style
                    .format({
                        "Strike": "${:,.2f}", "Ask": "${:,.2f}",
                        "Leverage": "{:.1f}x", "IV": "{:.1%}",
                        "IV Rank": "{:.0f}%", "Composite": "{:.1f}",
                    })
                    .map(_iv_color, subset=["IV Rank"]),
                width="stretch", hide_index=True,
            )

    st.subheader("Unusual Options Activity (24h)")
    uoa_rows = cdb.load_uoa_signals(conn, hours=24)
    if not uoa_rows:
        st.info("No unusual activity detected in the last 24 hours.")
    else:
        flow_cols = ["ticker", "contract_type", "strike", "expiration_date",
                     "volume", "open_interest", "vol_oi_ratio", "ask",
                     "flow_type", "detected_at"]
        uoa_df = pd.DataFrame(uoa_rows)
        if "flow_type" not in uoa_df.columns:
            uoa_df["flow_type"] = "normal"
        uoa_df = uoa_df[flow_cols].rename(columns={
            "ticker": "Ticker", "contract_type": "Type", "strike": "Strike",
            "expiration_date": "Exp", "volume": "Volume", "open_interest": "OI",
            "vol_oi_ratio": "Vol/OI", "ask": "Ask", "flow_type": "Flow",
            "detected_at": "Detected",
        })
        st.dataframe(
            uoa_df.style.format({
                "Strike": "${:,.2f}", "Ask": "${:,.2f}",
                "Vol/OI": "{:.1f}x",
            }).map(_flow_color, subset=["Flow"]),
            width="stretch", hide_index=True,
        )
