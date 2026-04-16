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
_unseen = cdb.unseen_alert_count(_conn)
_last_poll = cdb.last_poll_time(_conn)
_total_opts = _conn.execute("SELECT COUNT(*) FROM options_snapshot").fetchone()[0]

# ── Sidebar ───────────────────────────────────────────────────────────────────
st.sidebar.title("📈 Dealscout")
_cat_badge = f" 🔴 {_unseen}" if _unseen else ""
_opt_badge = f" ({_total_opts})" if _total_opts else ""
page = st.sidebar.radio("Navigate",
    ["Dashboard", "Catalysts" + _cat_badge, "Options Pulse" + _opt_badge,
     "Power Gauge", "Holdings", "Trades", "Performance", "Universe"])
st.sidebar.caption(f"Universe: {len(ACTIVE_TICKERS)} tickers")
st.sidebar.caption(f"Last catalyst poll: {_last_poll or '—'}")
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

        # ── Filter / search ──
        _filter_q = st.text_input("Filter tickers", placeholder="Type to filter (e.g. NV, ARM)").strip().upper()

        view = returns_df.copy()
        if _filter_q:
            view = view[view["ticker"].str.contains(_filter_q, na=False)]

        cat_rows = _conn.execute(
            """SELECT ticker, MAX(final_score) AS cat
               FROM catalysts
               WHERE datetime(published_at) >= datetime('now','-24 hours')
               GROUP BY ticker"""
        ).fetchall()
        cat_map = {r["ticker"]: r["cat"] for r in cat_rows}
        view["catalyst"] = view["ticker"].map(cat_map).fillna(0).astype(int)

        def _opts_badge(ticker):
            r = _conn.execute(
                "SELECT "
                "SUM(CASE WHEN contract_type='call' THEN 1 ELSE 0 END) AS c, "
                "SUM(CASE WHEN contract_type='put' THEN 1 ELSE 0 END) AS p "
                "FROM options_snapshot WHERE ticker=?", (ticker,)
            ).fetchone()
            c, p = (r["c"] or 0), (r["p"] or 0)
            if c + p == 0:
                return "—"
            parts = []
            if c:
                parts.append(f"{c}C")
            if p:
                parts.append(f"{p}P")
            return " ".join(parts)

        view["options"] = view["ticker"].map(_opts_badge)

        # ── IV Rank from options_snapshot ──
        ivr_rows = _conn.execute(
            "SELECT ticker, iv_rank FROM options_snapshot "
            "WHERE id IN (SELECT MIN(id) FROM options_snapshot GROUP BY ticker)"
        ).fetchall()
        ivr_map = {r["ticker"]: r["iv_rank"] for r in ivr_rows}
        view["iv_rank"] = view["ticker"].map(ivr_map)

        # ── Days to Earnings ──
        @st.cache_data(ttl=3600, show_spinner=False)
        def _earnings_countdowns(tickers_tuple):
            import yfinance as yf
            from datetime import datetime, timezone
            out = {}
            now = datetime.now(timezone.utc)
            for t in tickers_tuple:
                try:
                    cal = yf.Ticker(t).calendar
                    if cal is not None and not (isinstance(cal, pd.DataFrame) and cal.empty):
                        if isinstance(cal, dict):
                            ed = cal.get("Earnings Date")
                            if isinstance(ed, list) and ed:
                                ed = ed[0]
                        elif isinstance(cal, pd.DataFrame):
                            ed = cal.iloc[0, 0] if not cal.empty else None
                        else:
                            ed = None
                        if ed is not None:
                            if isinstance(ed, pd.Timestamp):
                                ed = ed.to_pydatetime()
                            if hasattr(ed, "date"):
                                delta = (ed - now).days
                                if 0 <= delta <= 90:
                                    out[t] = delta
                except Exception:
                    pass
            return out

        dte_map = _earnings_countdowns(tuple(view["ticker"].tolist()))
        view["earnings_dte"] = view["ticker"].map(dte_map)

        entry_rows = _conn.execute(
            "SELECT ticker, added_at FROM universe WHERE active=1"
        ).fetchall()
        entry_map = {r["ticker"]: r["added_at"][:10] for r in entry_rows}
        view["entry"] = view["ticker"].map(entry_map).fillna("—")

        view.insert(1, "name", view["ticker"].map(NAMES).fillna(""))
        view = view.rename(columns={
            "ticker": "Ticker", "name": "Name", "last": "Last",
            "daily_pct": "Daily %", "weekly_pct": "Weekly %",
            "monthly_pct": "Monthly %", "ytd_pct": "YTD %",
            "grade": "Grade", "catalyst": "Catalyst",
            "options": "Options", "iv_rank": "IV Rank",
            "earnings_dte": "Earn DTE", "entry": "Entry",
        })

        _display_cols = ["Ticker", "Name", "Last", "Daily %", "Weekly %",
                         "Monthly %", "YTD %", "Grade", "Catalyst", "Options",
                         "IV Rank", "Earn DTE", "Entry"]

        def _pct_bar(val):
            if pd.isna(val):
                return ""
            color = "#d4edda" if val >= 0 else "#f8d7da"
            width = min(abs(val) * 2, 100)
            return f"background: linear-gradient(90deg, {color} {width}%, transparent {width}%)"

        def _ivr_color(val):
            if pd.isna(val):
                return ""
            if val < 30:
                return "background-color: #d4edda"
            if val <= 60:
                return "background-color: #fff3cd"
            return "background-color: #f8d7da"

        def _dte_color(val):
            if pd.isna(val):
                return ""
            if val <= 7:
                return "color: #dc3545; font-weight: bold"
            if val <= 14:
                return "color: #fd7e14"
            return ""

        styled = (
            view[_display_cols].style
            .format({
                "Last": "${:,.2f}",
                "Daily %": "{:+.2f}%", "Weekly %": "{:+.2f}%",
                "Monthly %": "{:+.2f}%", "YTD %": "{:+.2f}%",
                "Catalyst": "{:d}",
                "IV Rank": lambda v: f"{v:.0f}%" if pd.notna(v) else "—",
                "Earn DTE": lambda v: f"{int(v)}d" if pd.notna(v) else "—",
            })
            .map(_pct_bar, subset=["Daily %", "Weekly %", "Monthly %", "YTD %"])
            .map(_ivr_color, subset=["IV Rank"])
            .map(_dte_color, subset=["Earn DTE"])
        )
        st.dataframe(styled, width="stretch", hide_index=True)

        with st.expander("Grade legend"):
            st.markdown(
                "**Momentum grade** — weighted score: 20% daily + 30% weekly + 50% monthly, "
                "relative to portfolio average weekly return.\n\n"
                "| Grade | Relative score |\n|---|---|\n"
                "| **A** | >= +8 |\n| **B** | >= +3 |\n| **C** | >= -2 |\n"
                "| **D** | >= -7 |\n| **F** | < -7 |"
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

elif page.startswith("Catalysts"):
    import json
    from urllib.parse import urlparse
    st.title("Catalysts")
    st.caption("Ingested filings and news, scored for M&A / partnership / product signal.")

    c1, c2, c3, c4 = st.columns([1, 1, 1, 2])
    with c1:
        min_score = st.slider("Min score", 0, 100, 70, 5)
    with c2:
        lookback = st.selectbox("Lookback", ["6h", "24h", "7d", "30d"], index=1)
    with c3:
        tag_filter = st.selectbox("Tag", ["All", "m&a-confirmed", "m&a-rumor", "activist", "partnership", "product", "filing"])
    with c4:
        ticker_filter = st.text_input("Ticker contains").strip().upper()

    hours = {"6h": 6, "24h": 24, "7d": 168, "30d": 720}[lookback]
    rows = _conn.execute(
        """SELECT id, ticker, source, form_type, headline, url, published_at,
                  kw_score, llm_score, final_score, tags, rationale
           FROM catalysts
           WHERE final_score >= ?
             AND datetime(published_at) >= datetime('now', ?)
           ORDER BY final_score DESC, published_at DESC
           LIMIT 300""",
        (min_score, f"-{hours} hours"),
    ).fetchall()

    records = [dict(r) for r in rows]
    for r in records:
        r["tags"] = json.loads(r["tags"] or "[]")
    if tag_filter != "All":
        records = [r for r in records if tag_filter in r["tags"]]
    if ticker_filter:
        records = [r for r in records if ticker_filter in r["ticker"]]

    if not records:
        st.info("No catalysts match the current filters. Run the poller or lower the min score.")
    else:
        import pandas as pd
        df = pd.DataFrame(records)[[
            "final_score", "ticker", "headline", "source", "form_type",
            "published_at", "kw_score", "llm_score"
        ]]
        st.dataframe(
            df, width="stretch", hide_index=True,
            column_config={
                "final_score": st.column_config.ProgressColumn(
                    "Score", min_value=0, max_value=100, format="%d",
                ),
            },
        )
        cdb.mark_seen(_conn, [r["id"] for r in records])

        st.subheader("Drilldown")
        _fmt = {r["id"]: f"{r['ticker']} — {r['headline'][:80]}" for r in records}
        pick_id = st.selectbox(
            "Catalyst",
            options=[r["id"] for r in records],
            format_func=_fmt.get,
        )
        row = next(r for r in records if r["id"] == pick_id)
        st.markdown(f"**{row['ticker']} — score {row['final_score']}**")
        if row.get("rationale"):
            st.write(row["rationale"])
        st.write({"tags": row["tags"], "kw": row["kw_score"], "llm": row["llm_score"]})
        parsed = urlparse(row["url"] or "")
        if parsed.scheme in ("http", "https"):
            st.link_button("Open source", row["url"])
        else:
            st.text(row["url"] or "(no url)")

        st.subheader("Catalyst heatmap — 24h")
        heat = _conn.execute(
            """SELECT ticker, SUM(final_score) AS s
               FROM catalysts
               WHERE datetime(published_at) >= datetime('now', '-24 hours')
               GROUP BY ticker ORDER BY s DESC LIMIT 15"""
        ).fetchall()
        if heat:
            hdf = pd.DataFrame([dict(r) for r in heat]).set_index("ticker")
            st.bar_chart(hdf)

elif page.startswith("Options Pulse"):
    st.title("Options Pulse")
    st.caption("Cheap convexity screener — calls & puts under $2, 7-28 DTE, ranked by catalyst-weighted leverage + IV rank.")

    opts_rows = cdb.load_all_options(_conn)
    if not opts_rows:
        st.info("No qualifying options found. Run the poller with POLYGON_API_KEY set.")
    else:
        import pandas as pd
        opts_df = pd.DataFrame(opts_rows)

        # Filters
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

        # Summary row
        s1, s2, s3 = st.columns(3)
        s1.metric("Qualifying contracts", len(filtered))
        cheap_vol = filtered[filtered["iv_rank"] < 30]["ticker"].nunique() if not filtered.empty else 0
        s2.metric("Cheap vol tickers (IV rank < 30)", cheap_vol)
        if not filtered.empty:
            s3.metric("Best composite", f"{filtered['composite_score'].max():.1f}")

        # Main table
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

            def _iv_color(val):
                if pd.isna(val):
                    return ""
                if val < 30:
                    return "background-color: #d4edda"
                if val <= 60:
                    return "background-color: #fff3cd"
                return "background-color: #f8d7da"

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
    from datetime import datetime
    from zoneinfo import ZoneInfo
    _AZ = ZoneInfo("America/Phoenix")

    def _to_az(iso: str | None) -> str:
        if not iso:
            return ""
        try:
            return datetime.fromisoformat(iso).astimezone(_AZ).strftime("%Y-%m-%d %H:%M:%S MST")
        except Exception:
            return iso

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
