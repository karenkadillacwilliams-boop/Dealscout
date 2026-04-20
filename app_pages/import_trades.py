"""Import page — 3-step wizard: upload → map → commit."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from catalysts import db as cdb
from portfolios import importer, profiles

from app_pages.shared import active_accounts, get_conn


def _auto_match_profile(header: list[str], all_profiles: list[dict]) -> dict | None:
    """Return first profile whose column_map values are all present in `header`."""
    for p in all_profiles:
        required = set(p["column_map"].values())
        if required.issubset(set(header)):
            return p
    return None


def render() -> None:
    conn = get_conn()
    accounts = active_accounts()

    st.title("Import trades")
    st.caption("Upload a broker CSV or a canonical file. Trades are deduplicated "
                "by (account, ticker, side, qty, price, date).")

    if not accounts:
        st.info("No accounts yet. Create one on the **Accounts** page before importing.")
        return

    all_profiles = profiles.load_profiles(conn)
    st.caption(f"Profiles loaded: {', '.join(p['name'] for p in all_profiles)}")

    # Step 1 — account + file
    col_a, col_b = st.columns([1, 2])
    with col_a:
        acc_options = {a["name"]: a["id"] for a in accounts}
        selected_name = st.selectbox("Account", options=list(acc_options.keys()))
        account_id = acc_options[selected_name]
    with col_b:
        uploaded = st.file_uploader("CSV file", type=["csv"],
                                     accept_multiple_files=False)

    if uploaded is None:
        return

    raw_bytes = uploaded.read()
    try:
        csv_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        csv_text = raw_bytes.decode("latin-1")

    # Step 2 — profile selection / mapping
    try:
        header, first_rows = _peek_csv(csv_text, n=5)
    except Exception as exc:
        st.error(f"CSV unreadable: {exc}")
        return

    st.caption("Detected columns: " + ", ".join(header))
    st.dataframe(pd.DataFrame(first_rows), width="stretch", hide_index=True)

    auto = _auto_match_profile(header, all_profiles)
    default_index = (
        [p["name"] for p in all_profiles].index(auto["name"]) if auto else 0
    )
    profile_name = st.selectbox(
        "Profile",
        options=[p["name"] for p in all_profiles] + ["✏ Custom mapping"],
        index=default_index,
    )

    if profile_name == "✏ Custom mapping":
        profile = _render_mapping_wizard(header)
        if profile is None:
            return
    else:
        profile = next(p for p in all_profiles if p["name"] == profile_name)
        if auto and auto["name"] == profile_name:
            st.success(f"Matched profile: **{profile_name}**")

    # Step 3 — preview + commit
    try:
        result = importer.apply_profile(csv_text, profile)
    except ValueError as exc:
        st.error(f"Profile cannot parse this file: {exc}")
        return

    st.subheader("Preview")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Valid rows",   len(result.valid))
    c2.metric("Rejected",     len(result.rejected))
    c3.metric("Skipped (filter)", result.skipped)
    # Detect duplicates at preview time
    dup_count = _count_existing_duplicates(conn, account_id, result.valid)
    c4.metric("Already in DB (dedup)", dup_count)

    if result.valid:
        preview_df = pd.DataFrame([{
            "ticker": r.ticker, "side": r.side, "qty": r.qty,
            "price": r.price, "trade_date": r.trade_date,
        } for r in result.valid])
        st.dataframe(preview_df, width="stretch", hide_index=True)

    if result.rejected:
        with st.expander(f"Rejected rows ({len(result.rejected)}) — click to inspect"):
            rej_df = pd.DataFrame([{"reason": r.reason, **r.raw}
                                     for r in result.rejected])
            st.dataframe(rej_df, width="stretch", hide_index=True)

    if profile_name == "✏ Custom mapping":
        with st.expander("Save this mapping as a profile"):
            new_name = st.text_input("Profile name",
                                       placeholder="My Broker Positions")
            new_broker = st.selectbox("Broker tag",
                                       options=["fidelity", "schwab", "robinhood",
                                                "moomoo", "vanguard", "other"],
                                       key="save_profile_broker")
            if st.button("Save profile"):
                if not new_name:
                    st.error("Profile name required.")
                else:
                    try:
                        profiles.create_user_profile(
                            conn, name=new_name, broker=new_broker,
                            column_map=profile["column_map"],
                            value_map=profile["value_map"],
                            row_filter=profile["row_filter"],
                        )
                        st.success(f"Saved profile '{new_name}'")
                    except Exception as exc:
                        st.error(f"Save failed: {exc}")

    if result.valid and st.button(
        f"Import {len(result.valid)} trades",
        type="primary",
        disabled=len(result.valid) == 0,
    ):
        profile_id = profile.get("id") if isinstance(profile, dict) else None
        summary = importer.commit_to_db(
            conn, account_id=account_id, rows=result.valid,
            profile_id=profile_id, filename=uploaded.name,
            rejected_count=len(result.rejected),
        )
        st.success(
            f"Imported {summary['inserted']} new trades, "
            f"skipped {summary['duplicates']} duplicates "
            f"(batch #{summary['batch_id']})."
        )
        st.rerun()


def _peek_csv(csv_text: str, n: int = 5) -> tuple[list[str], list[dict]]:
    import csv, io
    reader = csv.DictReader(io.StringIO(csv_text))
    header = reader.fieldnames or []
    rows = []
    for i, row in enumerate(reader):
        if i >= n:
            break
        rows.append(row)
    return header, rows


def _count_existing_duplicates(
    conn, account_id: int, rows: list,
) -> int:
    """Count how many of these rows already exist in the DB (dedup check).

    Uses individual parameterised lookups (one ? per row) to stay safe from
    SQL injection warnings — no dynamic SQL text is constructed.
    """
    if not rows:
        return 0
    count = 0
    for r in rows:
        key = (
            f"{account_id}|{r.ticker}|{r.side}"
            f"|{r.qty:.6f}|{r.price:.6f}|{r.trade_date}"
        )
        found = conn.execute(
            "SELECT 1 FROM trades WHERE dedup_key = ? LIMIT 1",
            (key,),
        ).fetchone()
        if found:
            count += 1
    return count


def _render_mapping_wizard(header: list[str]) -> dict | None:
    """Let user map our 5 canonical fields onto source columns. Returns a
    profile-shaped dict or None if not ready yet."""
    st.caption("Map the five canonical fields onto your CSV's columns:")
    opts = ["— select —"] + header
    cmap = {}
    cols = st.columns(5)
    for i, canonical in enumerate(("ticker", "side", "qty", "price", "trade_date")):
        with cols[i]:
            cmap[canonical] = st.selectbox(canonical, options=opts, key=f"map_{canonical}")
    if any(v == "— select —" for v in cmap.values()):
        st.info("Pick a column for each canonical field above to continue.")
        return None

    st.caption("side value map — translate your CSV's action strings to BUY / SELL:")
    side_map_input = st.text_area(
        "One pair per line: `CSV_VALUE = CANONICAL` (e.g. `Buy = BUY`)",
        value="Buy = BUY\nSell = SELL",
        height=100,
    )
    side_map: dict[str, str] = {}
    for line in side_map_input.splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().upper()
        if k and v in ("BUY", "SELL"):
            side_map[k] = v

    skip_untranslated = st.checkbox(
        "Skip rows whose action isn't mapped (e.g. dividend / transfer)",
        value=True,
    )

    return {
        "column_map": cmap,
        "value_map": {"side": side_map},
        "row_filter": {"skip_if_side_not_in_value_map": skip_untranslated},
    }
