"""Dashboard page — per-ticker returns, grade, catalyst, options, IV, earnings DTE."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from catalysts import db as cdb
from tickers import NAMES

from app_pages.shared import fmt_pct, get_conn, price_context


@st.cache_data(ttl=3600, show_spinner=False)
def _earnings_countdowns(tickers_tuple: tuple[str, ...]):
    import yfinance as yf
    from datetime import datetime, timezone
    out: dict[str, int] = {}
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


def _opts_badge(conn, ticker: str) -> str:
    r = conn.execute(
        "SELECT "
        "SUM(CASE WHEN contract_type='call' THEN 1 ELSE 0 END) AS c, "
        "SUM(CASE WHEN contract_type='put' THEN 1 ELSE 0 END) AS p "
        "FROM options_snapshot WHERE ticker=?", (ticker,),
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


def _entry_price_map(conn, last_prices: dict) -> dict[str, float]:
    """Qty-weighted average cost basis per ticker across all accounts.

    A ticker held in Account A (10 shares @ $100) and Account B (5 shares @ $110)
    has an entry price of (10*100 + 5*110) / 15 = $103.33. Only tickers with at
    least one long position appear in the result.

    Implementation note: uses avg-cost basis at the TRADE level — equivalent to
    sum(BUY qty*price) / sum(BUY qty) per ticker, ignoring SELL side (SELLs
    don't change cost basis in avg-cost accounting). This matches what
    portfolio._positions_from_rows produces but avoids its per-account Python
    loop. One SQL round-trip for the whole dashboard.
    """
    rows = conn.execute(
        "SELECT ticker, "
        "  SUM(CASE WHEN side='BUY' THEN qty * price ELSE 0 END) AS buy_cost, "
        "  SUM(CASE WHEN side='BUY' THEN qty ELSE 0 END) AS buy_qty, "
        "  SUM(CASE WHEN side='BUY' THEN qty ELSE -qty END) AS net_qty "
        "FROM trades GROUP BY ticker"
    ).fetchall()
    # Only show tickers still held (net_qty > 0). Basis uses BUY-only denominator,
    # which matches portfolio._positions_from_rows under avg-cost accounting:
    # a SELL reduces qty + realized P/L but leaves avg_cost unchanged, so the
    # ratio of cumulative-BUY-cost to cumulative-BUY-qty is the remaining basis.
    return {
        r["ticker"]: float(r["buy_cost"] / r["buy_qty"])
        for r in rows
        if r["net_qty"] > 1e-9 and r["buy_qty"] > 0
    }


def render() -> None:
    conn = get_conn()
    _tickers, _prices, returns_df, last_prices = price_context()

    st.title("Dashboard")
    st.caption("Daily / weekly / monthly returns and momentum grade for the watchlist.")

    if returns_df.empty:
        st.warning("No price data fetched. Try Refresh.")
        return

    top_cols = st.columns(4)
    top_cols[0].metric("Tickers", len(returns_df))
    top_cols[1].metric("Avg daily",   fmt_pct(returns_df["daily_pct"].mean()))
    top_cols[2].metric("Avg weekly",  fmt_pct(returns_df["weekly_pct"].mean()))
    top_cols[3].metric("Avg monthly", fmt_pct(returns_df["monthly_pct"].mean()))

    filter_q = st.text_input(
        "Filter tickers", placeholder="Type to filter (e.g. NV, ARM)"
    ).strip().upper()

    view = returns_df.copy()
    if filter_q:
        view = view[view["ticker"].str.contains(filter_q, na=False)]

    cat_rows = conn.execute(
        """SELECT ticker, MAX(final_score) AS cat
           FROM catalysts
           WHERE datetime(published_at) >= datetime('now','-24 hours')
           GROUP BY ticker"""
    ).fetchall()
    cat_map = {r["ticker"]: r["cat"] for r in cat_rows}
    view["catalyst"] = view["ticker"].map(cat_map).fillna(0).astype(int)

    view["options"] = view["ticker"].map(lambda t: _opts_badge(conn, t))

    ivr_rows = conn.execute(
        "SELECT ticker, iv_rank FROM options_snapshot "
        "WHERE id IN (SELECT MIN(id) FROM options_snapshot GROUP BY ticker)"
    ).fetchall()
    ivr_map = {r["ticker"]: r["iv_rank"] for r in ivr_rows}
    view["iv_rank"] = view["ticker"].map(ivr_map)

    dte_map = _earnings_countdowns(tuple(view["ticker"].tolist()))
    view["earnings_dte"] = view["ticker"].map(dte_map)

    entry_rows = conn.execute(
        "SELECT ticker, added_at FROM universe WHERE active=1"
    ).fetchall()
    entry_map = {r["ticker"]: r["added_at"][:10] for r in entry_rows}
    view["entry"] = view["ticker"].map(entry_map).fillna("—")

    tech_data = cdb.load_technicals(conn)

    def _tech_label(ticker: str) -> str:
        t = tech_data.get(ticker)
        return t["label"] if t else "—"

    view["tech"] = view["ticker"].map(_tech_label)

    # Confirmed-event summary per ticker (last 30d)
    from portfolios import analytics
    ev_map = analytics.weekly_events_per_ticker(
        conn, tickers=view["ticker"].tolist(), days=30,
    )

    def _event_cell(ticker: str) -> str:
        evs = ev_map.get(ticker)
        if not evs:
            return "—"
        wins = sum(1 for e in evs if e["pnl_dollars"] > 0)
        losses = sum(1 for e in evs if e["pnl_dollars"] < 0)
        total_pnl = evs[-1]["cumulative_pnl"]
        sign = "+" if total_pnl >= 0 else ""
        return f"{wins}W/{losses}L {sign}${total_pnl:,.0f}"

    view["events"] = view["ticker"].map(_event_cell)

    # Triple-play score (post-earnings momentum)
    tp_data = cdb.load_triple_play(conn)

    def _tp_cell(ticker: str) -> str:
        row = tp_data.get(ticker)
        if not row:
            return "—"
        prefix = "🎯 " if row["is_full_triple_play"] else ""
        return f"{prefix}{row['score']:.0f}"

    view["triple_play"] = view["ticker"].map(_tp_cell)

    # Entry price (qty-weighted avg cost across all accounts; NaN if not held)
    entry_map = _entry_price_map(conn, last_prices)
    view["entry_price"] = view["ticker"].map(entry_map)

    view.insert(1, "name", view["ticker"].map(NAMES).fillna(""))
    view = view.rename(columns={
        "ticker": "Ticker", "name": "Name", "last": "Last",
        "daily_pct": "Daily %", "weekly_pct": "Weekly %",
        "monthly_pct": "Monthly %", "ytd_pct": "YTD %",
        "grade": "Grade", "catalyst": "Catalyst",
        "options": "Options", "iv_rank": "IV Rank",
        "earnings_dte": "Earn DTE",
        "entry": "Added",              # date the ticker was added to the universe
        "entry_price": "Entry",        # qty-weighted avg cost across accounts
        "tech": "Tech", "events": "Events",
        "triple_play": "Triple",
    })

    _RETURNS_COLS = ["Ticker", "Name", "Last", "Daily %", "Weekly %",
                     "Monthly %", "YTD %", "Grade"]
    _SIGNALS_COLS = ["Ticker", "Triple", "Tech", "Catalyst",
                     "Options", "IV Rank", "Earn DTE"]
    _POSITIONS_COLS = ["Ticker", "Entry", "Last", "Events", "Added"]

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

    def _tech_color(val):
        if val == "Bullish":
            return "color: #28a745; font-weight: bold"
        if val == "Bearish":
            return "color: #dc3545; font-weight: bold"
        return ""

    def _triple_color(val):
        import re
        # Strip emoji prefix for parsing
        m = re.search(r"(\d+)", str(val))
        if not m:
            return ""
        n = int(m.group(1))
        if n >= 70:
            return "color: #28a745; font-weight: bold"
        if n >= 55:
            return "color: #fd7e14"
        if n <= 35:
            return "color: #dc3545"
        return ""

    def _entry_vs_last_row(row):
        """Color the Entry cell green if Last > Entry, red if Last < Entry.
        Uses row-level apply so the comparison reads both columns."""
        styles = [""] * len(row)
        if "Entry" not in row.index or "Last" not in row.index:
            return styles
        entry = row["Entry"]
        last = row["Last"]
        if pd.isna(entry) or pd.isna(last):
            return styles
        idx = row.index.get_loc("Entry")
        if last > entry:
            styles[idx] = "color: #28a745; font-weight: bold"
        elif last < entry:
            styles[idx] = "color: #dc3545; font-weight: bold"
        return styles

    def _style_subset(frame: pd.DataFrame, cols: list[str]):
        """Build a Styler applying only the formatters that match the subset."""
        present = [c for c in cols if c in frame.columns]
        fmt_map = {}
        if "Entry" in present:
            fmt_map["Entry"] = "${:,.2f}"
        if "Last" in present:
            fmt_map["Last"] = "${:,.2f}"
        for c in ("Daily %", "Weekly %", "Monthly %", "YTD %"):
            if c in present:
                fmt_map[c] = "{:+.2f}%"
        if "Catalyst" in present:
            fmt_map["Catalyst"] = "{:d}"
        if "IV Rank" in present:
            fmt_map["IV Rank"] = lambda v: f"{v:.0f}%" if pd.notna(v) else "—"
        if "Earn DTE" in present:
            fmt_map["Earn DTE"] = lambda v: f"{int(v)}d" if pd.notna(v) else "—"

        styler = frame[present].style.format(fmt_map, na_rep="—")
        pct_cols = [c for c in ("Daily %", "Weekly %", "Monthly %", "YTD %") if c in present]
        if pct_cols:
            styler = styler.map(_pct_bar, subset=pct_cols)
        if "IV Rank" in present:
            styler = styler.map(_ivr_color, subset=["IV Rank"])
        if "Earn DTE" in present:
            styler = styler.map(_dte_color, subset=["Earn DTE"])
        if "Tech" in present:
            styler = styler.map(_tech_color, subset=["Tech"])
        if "Triple" in present:
            styler = styler.map(_triple_color, subset=["Triple"])
        if "Entry" in present and "Last" in present:
            styler = styler.apply(_entry_vs_last_row, axis=1)
        return styler

    returns_tab, signals_tab, positions_tab = st.tabs(
        ["Returns", "Signals", "Positions"]
    )
    with returns_tab:
        st.dataframe(_style_subset(view, _RETURNS_COLS),
                     width="stretch", hide_index=True)
    with signals_tab:
        st.dataframe(_style_subset(view, _SIGNALS_COLS),
                     width="stretch", hide_index=True)
    with positions_tab:
        st.dataframe(_style_subset(view, _POSITIONS_COLS),
                     width="stretch", hide_index=True)

    with st.expander("Grade legend"):
        st.markdown(
            "**Momentum grade** — weighted score: 20% daily + 30% weekly + 50% monthly, "
            "relative to portfolio average weekly return.\n\n"
            "| Grade | Relative score |\n|---|---|\n"
            "| **A** | >= +8 |\n| **B** | >= +3 |\n| **C** | >= -2 |\n"
            "| **D** | >= -7 |\n| **F** | < -7 |"
        )
