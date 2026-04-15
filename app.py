"""Dealscout — Streamlit portfolio tracker with momentum grading."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

import portfolio
from data import add_grades, compute_power_gauge_ratings, fetch_history, period_returns
from tickers import NAMES, TICKERS

st.set_page_config(page_title="Dealscout", page_icon="📈", layout="wide")
portfolio.init_db()

from catalysts import db as cdb

_conn = cdb.connect()
cdb.migrate(_conn)
cdb.seed_universe_if_empty(_conn, TICKERS)
ACTIVE_TICKERS = cdb.load_active_universe(_conn) or TICKERS

# ── Sidebar ───────────────────────────────────────────────────────────────────
st.sidebar.title("📈 Dealscout")
page = st.sidebar.radio("Navigate", ["Dashboard", "Power Gauge", "Holdings", "Trades", "Performance", "Universe"])
st.sidebar.caption(f"Universe: {len(ACTIVE_TICKERS)} tickers")
if st.sidebar.button("🔄 Refresh prices"):
    st.cache_data.clear()
    st.rerun()

# ── Shared data load ──────────────────────────────────────────────────────────
prices = fetch_history(ACTIVE_TICKERS, period="6mo")
returns_df = add_grades(period_returns(prices))
last_prices = dict(zip(returns_df["ticker"], returns_df["last"])) if not returns_df.empty else {}


def _fmt_pct(v):
    return "—" if pd.isna(v) else f"{v:+.2f}%"


def _fmt_money(v):
    return "—" if pd.isna(v) else f"${v:,.2f}"


# ── Pages ─────────────────────────────────────────────────────────────────────
if page == "Dashboard":
    st.title("Dashboard")
    st.caption("Daily / weekly / monthly returns and momentum grade for the watchlist.")

    if returns_df.empty:
        st.warning("No price data fetched. Try Refresh.")
    else:
        top_cols = st.columns(4)
        top_cols[0].metric("Tickers", len(returns_df))
        top_cols[1].metric("Avg daily",   _fmt_pct(returns_df["daily_pct"].mean()))
        top_cols[2].metric("Avg weekly",  _fmt_pct(returns_df["weekly_pct"].mean()))
        top_cols[3].metric("Avg monthly", _fmt_pct(returns_df["monthly_pct"].mean()))

        view = returns_df.copy()
        view.insert(1, "name", view["ticker"].map(NAMES).fillna(""))
        view = view.rename(columns={
            "ticker": "Ticker", "name": "Name", "last": "Last",
            "daily_pct": "Daily %", "weekly_pct": "Weekly %",
            "monthly_pct": "Monthly %", "grade": "Grade",
        })
        st.dataframe(
            view.style.format({
                "Last": "${:,.2f}",
                "Daily %": "{:+.2f}%", "Weekly %": "{:+.2f}%", "Monthly %": "{:+.2f}%",
            }),
            width="stretch", hide_index=True,
        )

elif page == "Power Gauge":
    st.title("Power Gauge")
    st.caption("Chaikin-style 4-category composite rating ported from stock_evaluator.")

    pg = compute_power_gauge_ratings(ACTIVE_TICKERS)
    if pg.empty:
        st.warning("No ratings available — check your network and refresh.")
    else:
        bucket_counts = pg["label"].value_counts()
        cols = st.columns(7)
        bucket_order = ["Very Bullish", "Bullish", "Neutral+", "Neutral", "Neutral-", "Bearish", "Very Bearish"]
        for i, label in enumerate(bucket_order):
            cols[i].metric(label, int(bucket_counts.get(label, 0)))

        view = pg.copy()
        view.insert(1, "name", view["ticker"].map(NAMES).fillna(""))
        view = view.rename(columns={
            "ticker": "Ticker", "name": "Name", "label": "Rating", "score": "Score",
            "fin": "Fin", "earn": "Earn", "tech": "Tech", "exp": "Exp",
            "adjustment": "Adjustment",
        })
        st.dataframe(
            view[["Ticker", "Name", "Rating", "Score", "Fin", "Earn", "Tech", "Exp", "Adjustment"]],
            width="stretch", hide_index=True,
        )

        with st.expander("Ticker drilldown"):
            pick = st.selectbox("Ticker", options=sorted(pg["ticker"].tolist()))
            row = pg[pg["ticker"] == pick].iloc[0]
            st.markdown(f"**{pick} — {row['label']}** (score {row['score']})")
            st.write(row["narrative"])
            st.write({
                "Financials": row["fin"], "Earnings": row["earn"],
                "Technicals": row["tech"], "Experts": row["exp"],
                "Adjustment": row["adjustment"],
            })

elif page == "Holdings":
    st.title("Holdings")
    pos = portfolio.positions(last_prices)
    if pos.empty:
        st.info("No positions yet. Record a buy on the **Trades** page to get started.")
    else:
        total_mv  = pos["market_value"].sum()
        total_unr = pos["unrealized_pl"].sum()
        total_rea = pos["realized_pl"].sum()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Positions",     len(pos))
        c2.metric("Market value",  _fmt_money(total_mv))
        c3.metric("Unrealized P/L", _fmt_money(total_unr))
        c4.metric("Realized P/L",   _fmt_money(total_rea))

        view = pos.copy()
        view.insert(1, "name", view["ticker"].map(NAMES).fillna(""))
        view = view.rename(columns={
            "ticker": "Ticker", "name": "Name", "qty": "Qty",
            "avg_cost": "Avg cost", "last": "Last",
            "market_value": "Market value",
            "unrealized_pl": "Unrealized P/L",
            "realized_pl": "Realized P/L",
            "total_pl": "Total P/L",
        })
        st.dataframe(
            view.style.format({
                "Qty": "{:,.4f}",
                "Avg cost": "${:,.2f}", "Last": "${:,.2f}",
                "Market value": "${:,.2f}",
                "Unrealized P/L": "${:,.2f}",
                "Realized P/L": "${:,.2f}",
                "Total P/L": "${:,.2f}",
            }),
            width="stretch", hide_index=True,
        )

        if total_mv > 0:
            st.subheader("Allocation")
            fig = px.pie(pos, values="market_value", names="ticker", hole=0.45)
            st.plotly_chart(fig, width="stretch")

elif page == "Trades":
    st.title("Trades")
    st.caption("Record buys and sells. Positions are derived from this ledger.")

    with st.form("new_trade", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
        with c1:
            ticker = st.selectbox("Ticker", options=sorted(ACTIVE_TICKERS))
        with c2:
            side = st.selectbox("Side", ["BUY", "SELL"])
        with c3:
            qty = st.number_input("Qty", min_value=0.0001, value=1.0, step=1.0, format="%.4f")
        with c4:
            default_price = float(last_prices.get(ticker, 0.0)) if last_prices else 0.0
            price = st.number_input("Price", min_value=0.0, value=default_price, step=0.01, format="%.2f")
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

elif page == "Performance":
    st.title("Performance")
    st.caption("Per-ticker price history. Pick one or more tickers to chart.")

    if prices.empty:
        st.warning("No price data fetched.")
    else:
        default_pick = ["NVDA"] if "NVDA" in prices.columns else [prices.columns[0]]
        picks = st.multiselect(
            "Tickers", options=list(prices.columns), default=default_pick,
        )
        if picks:
            normalized = prices[picks].dropna(how="all")
            normalized = normalized / normalized.iloc[0] * 100.0
            fig = px.line(normalized, title="Indexed to 100 at start of window")
            fig.update_layout(yaxis_title="Index (start = 100)", xaxis_title="")
            st.plotly_chart(fig, width="stretch")

elif page == "Universe":
    import re
    st.title("Universe")
    st.caption("Active tickers used by the Dashboard, Power Gauge, and Catalyst poller.")

    active = cdb.load_active_universe(_conn)
    st.metric("Active tickers", len(active))

    rows = _conn.execute(
        "SELECT ticker, name, added_at, active FROM universe ORDER BY ticker"
    ).fetchall()
    import pandas as pd
    df = pd.DataFrame([dict(r) for r in rows])
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
                cdb.upsert_universe(_conn, new_t, new_n or None)
                st.success(f"Added {new_t}")
                st.rerun()
            else:
                st.error("Invalid ticker format.")

    st.subheader("Remove ticker")
    rm = st.selectbox("Ticker to deactivate", options=active)
    if st.button("Deactivate", type="secondary"):
        cdb.deactivate_ticker(_conn, rm)
        st.success(f"Deactivated {rm}")
        st.rerun()

    st.subheader("Bulk import")
    raw = st.text_area("Paste comma- or newline-separated tickers")
    if st.button("Import"):
        tokens = [t.strip().upper() for t in re.split(r"[,\s]+", raw) if t.strip()]
        added, bad = 0, []
        for t in tokens:
            if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", t):
                cdb.upsert_universe(_conn, t)
                added += 1
            else:
                bad.append(t)
        st.success(f"Added {added} tickers.")
        if bad:
            st.warning(f"Skipped invalid: {', '.join(bad)}")
        st.rerun()
