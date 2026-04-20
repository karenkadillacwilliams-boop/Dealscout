"""Events page — review pending position events, confirm or dismiss, link to catalysts."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from catalysts import db as cdb

from app_pages.shared import active_accounts, get_conn

_CATALYST_TYPES = ["earnings", "m&a", "rumor", "political", "industry",
                    "market", "product", "management", "other"]


def render() -> None:
    conn = get_conn()
    accounts = active_accounts()

    st.title("Events")
    st.caption("Auto-detected position moves awaiting a catalyst label. "
                "Confirmed events feed the catalyst scorer.")

    pending = cdb.pending_event_count(conn)
    total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    confirmed = conn.execute(
        "SELECT COUNT(*) FROM events WHERE status='confirmed'"
    ).fetchone()[0]
    c1, c2, c3 = st.columns(3)
    c1.metric("Pending", pending)
    c2.metric("Confirmed", confirmed)
    c3.metric("Total events", total)

    # Rollup charts over confirmed events
    with st.expander("Feedback-loop analytics", expanded=True):
        _render_rollup_charts(conn)

    # Filters
    fcols = st.columns([1, 1, 1, 1])
    with fcols[0]:
        acc_filter = st.selectbox(
            "Account",
            options=[None] + [a["id"] for a in accounts],
            format_func=lambda i: "All" if i is None
                else next(a["name"] for a in accounts if a["id"] == i),
        )
    with fcols[1]:
        status_filter = st.selectbox(
            "Status", options=["pending", "confirmed", "dismissed", "all"]
        )
    with fcols[2]:
        days_filter = st.selectbox(
            "Lookback",
            options=[7, 30, 90, 365, None],
            format_func=lambda d: "All time" if d is None else f"{d} days",
            index=1,
        )
    with fcols[3]:
        ticker_q = st.text_input("Ticker contains").strip().upper()

    events = cdb.load_events(
        conn,
        account_id=acc_filter,
        status=None if status_filter == "all" else status_filter,
        since_days=days_filter,
    )
    if ticker_q:
        events = [e for e in events if ticker_q in e["ticker"]]

    if not events:
        st.info("No events match the current filters.")
        return

    # Table
    acc_map = {a["id"]: a["name"] for a in accounts}
    ev_df = pd.DataFrame(events)
    ev_df["account"] = ev_df["account_id"].map(acc_map).fillna("—")
    display = ev_df[[
        "id", "detected_at", "account", "ticker", "event_date",
        "move_window", "move_pct", "pnl_dollars", "catalyst_id",
        "catalyst_type", "status",
    ]].rename(columns={
        "id": "ID", "detected_at": "Detected", "account": "Account",
        "ticker": "Ticker", "event_date": "Date", "move_window": "Win",
        "move_pct": "Move %", "pnl_dollars": "P&L $",
        "catalyst_id": "Link", "catalyst_type": "Tag", "status": "Status",
    })
    st.dataframe(
        display.style.format({"Move %": "{:+.2f}%", "P&L $": "${:,.2f}"}),
        width="stretch", hide_index=True,
    )

    # Side-panel review for a single event
    st.subheader("Review")
    pick_id = st.selectbox(
        "Event",
        options=[e["id"] for e in events],
        format_func=lambda i: _event_label(conn, i),
    )
    _render_review(conn, pick_id)


def _render_rollup_charts(conn) -> None:
    from portfolios import analytics
    import plotly.express as px

    pnl = analytics.pnl_by_catalyst_type(conn)
    hit = analytics.hit_rate_by_catalyst_type(conn)
    linked = analytics.linked_vs_unlinked(conn)

    if not pnl:
        st.caption("No confirmed events yet — confirm some below to populate these charts.")
        return

    cc = st.columns(3)
    pnl_df = pd.DataFrame(pnl)
    fig1 = px.bar(pnl_df, x="net_pnl", y="catalyst_type", orientation="h",
                   color="net_pnl", color_continuous_scale="RdYlGn",
                   title="Net P&L by catalyst type")
    fig1.update_layout(coloraxis_showscale=False, height=260,
                        yaxis_title="", xaxis_title="$")
    cc[0].plotly_chart(fig1, width="stretch")

    if hit:
        hit_df = pd.DataFrame(hit)
        hit_df["hit_rate_pct"] = (hit_df["hit_rate"] * 100).round(1)
        fig2 = px.bar(hit_df, x="hit_rate_pct", y="catalyst_type",
                       orientation="h", color="hit_rate_pct",
                       color_continuous_scale="RdYlGn", range_color=[0, 100],
                       title="Hit rate (% gains) by type")
        fig2.update_layout(coloraxis_showscale=False, height=260,
                            yaxis_title="", xaxis_title="%")
        cc[1].plotly_chart(fig2, width="stretch")

    total = linked["linked"] + linked["unlinked"]
    if total > 0:
        link_df = pd.DataFrame([
            {"kind": "Linked",   "count": linked["linked"]},
            {"kind": "Unlinked", "count": linked["unlinked"]},
        ])
        fig3 = px.pie(link_df, values="count", names="kind",
                       title=f"Linked vs unlinked events ({total})",
                       color="kind",
                       color_discrete_map={"Linked": "#2E7D32",
                                             "Unlinked": "#C62828"})
        fig3.update_layout(height=260)
        cc[2].plotly_chart(fig3, width="stretch")


def _event_label(conn, event_id: int) -> str:
    row = cdb.load_event(conn, event_id)
    return (f"#{row['id']} {row['ticker']} {row['event_date']} "
             f"{row['move_pct']:+.1f}% ({row['move_window']})")


def _render_review(conn, event_id: int) -> None:
    row = cdb.load_event(conn, event_id)
    if not row:
        st.warning("Event not found.")
        return
    accounts = {a["id"]: a["name"] for a in active_accounts()}

    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(f"**Account** &nbsp; {accounts.get(row['account_id'], '—')}")
    c2.markdown(f"**Ticker** &nbsp; {row['ticker']}")
    c3.markdown(f"**Position** &nbsp; {row['position_qty']:.4f}")
    c4.markdown(f"**P&L** &nbsp; ${row['pnl_dollars']:+,.2f}")

    c5, c6 = st.columns(2)
    c5.markdown(f"**Before** &nbsp; ${row['value_before']:,.2f}")
    c6.markdown(f"**After** &nbsp; ${row['value_after']:,.2f}")

    # Catalyst link
    st.markdown("**Catalyst link**")
    if row["catalyst_id"]:
        cat = conn.execute(
            "SELECT id, ticker, headline, source, final_score, url, published_at "
            "FROM catalysts WHERE id=?", (row["catalyst_id"],),
        ).fetchone()
        if cat:
            st.info(
                f"**{cat['ticker']} · score {cat['final_score']}**  "
                f"[{cat['source']}]  \n{cat['headline']}"
            )
    else:
        st.caption("No auto-match. You can leave this unlinked or search manually below.")

    candidate_rows = conn.execute(
        "SELECT id, ticker, headline, source, final_score, published_at "
        "FROM catalysts WHERE ticker=? "
        "AND ABS(julianday(substr(published_at, 1, 10)) - julianday(?)) <= 14 "
        "ORDER BY final_score DESC LIMIT 10",
        (row["ticker"], row["event_date"]),
    ).fetchall()
    if candidate_rows:
        new_link = st.selectbox(
            "Change linked catalyst (within ±14 days)",
            options=[None] + [c["id"] for c in candidate_rows],
            format_func=lambda i: "— no link —" if i is None
                else next(
                    f"[{c['final_score']}] {c['headline'][:70]}"
                    for c in candidate_rows if c["id"] == i
                ),
            index=0 if row["catalyst_id"] is None
                else next((i for i, c in enumerate(candidate_rows, start=1)
                           if c["id"] == row["catalyst_id"]), 0),
        )
    else:
        new_link = row["catalyst_id"]

    # Label
    current_type_index = (_CATALYST_TYPES.index(row["catalyst_type"])
                           if row["catalyst_type"] in _CATALYST_TYPES else 0)
    new_type = st.selectbox("Catalyst type", options=_CATALYST_TYPES,
                              index=current_type_index)
    notes = st.text_area("Notes", value=row.get("notes") or "")

    bcols = st.columns(3)
    if bcols[0].button("Confirm", type="primary"):
        cdb.update_event(
            conn, event_id, status="confirmed",
            catalyst_id=new_link, catalyst_type=new_type,
            notes=notes or None,
        )
        st.success("Confirmed.")
        st.rerun()
    if bcols[1].button("Dismiss", type="secondary"):
        cdb.update_event(conn, event_id, status="dismissed",
                          notes=notes or None)
        st.info("Dismissed.")
        st.rerun()
    if bcols[2].button("Save pending"):
        cdb.update_event(
            conn, event_id, catalyst_id=new_link,
            catalyst_type=new_type, notes=notes or None,
        )
        st.success("Saved, still pending.")
        st.rerun()
