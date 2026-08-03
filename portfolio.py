"""Account-scoped position and P/L computation on the unified dealscout.db.

The single-book `trades` table from data/dealscout.db is superseded by the
multi-account `trades` table in the root dealscout.db (see catalysts/db.py
schema). Positions are derived from the trade ledger using the same
average-cost basis as the old single-book code, scoped by account_id.
"""
from __future__ import annotations

from collections import defaultdict

import pandas as pd

from catalysts import db as cdb


_POSITION_COLS = [
    "ticker", "qty", "avg_cost", "last",
    "market_value", "unrealized_pl", "realized_pl", "total_pl",
]

#: DataFrame.attrs key holding {ticker: qty} for sells that had no open lot to
#: close against. Such trades cannot contribute realized P/L — there is no cost
#: basis to subtract — so they are excluded from the maths. Callers should
#: surface this rather than let the omission pass unnoticed; the usual cause is
#: a broker CSV whose window starts after the original purchase, which makes
#: reported realized gains look smaller than they really were.
UNMATCHED_SELLS_ATTR = "unmatched_sells"


def _positions_from_rows(
    trade_rows: list[dict],
    last_prices: dict[str, float],
) -> pd.DataFrame:
    """Derive positions using average-cost basis from a chronological trade list."""
    rows_sorted = sorted(trade_rows, key=lambda r: (r["trade_date"], r["id"]))

    qty_by: dict[str, float] = defaultdict(float)
    cost_by: dict[str, float] = defaultdict(float)
    realized_by: dict[str, float] = defaultdict(float)
    unmatched_by: dict[str, float] = defaultdict(float)

    for t in rows_sorted:
        tk = t["ticker"]
        if t["side"] == "BUY":
            cost_by[tk] += t["qty"] * t["price"]
            qty_by[tk] += t["qty"]
        else:  # SELL
            if qty_by[tk] <= 0:
                # Nothing open to close against, so there is no cost basis and
                # no derivable realized P/L. Record the shortfall so the caller
                # can flag it — see UNMATCHED_SELLS_ATTR.
                unmatched_by[tk] += t["qty"]
                continue
            avg_cost = cost_by[tk] / qty_by[tk]
            sell_qty = min(t["qty"], qty_by[tk])
            realized_by[tk] += (t["price"] - avg_cost) * sell_qty
            cost_by[tk] -= avg_cost * sell_qty
            qty_by[tk] -= sell_qty

    rows = []
    for tk, qty in qty_by.items():
        if qty <= 1e-9 and realized_by[tk] == 0:
            continue
        avg_cost = (cost_by[tk] / qty) if qty > 0 else 0.0
        last = float(last_prices.get(tk, float("nan")))
        market_value = qty * last if qty > 0 else 0.0
        unrealized = (last - avg_cost) * qty if qty > 0 else 0.0
        rows.append({
            "ticker": tk,
            "qty": qty,
            "avg_cost": avg_cost,
            "last": last,
            "market_value": market_value,
            "unrealized_pl": unrealized,
            "realized_pl": realized_by[tk],
            "total_pl": unrealized + realized_by[tk],
        })
    unmatched = {k: v for k, v in unmatched_by.items() if v > 0}
    if not rows:
        empty = pd.DataFrame(columns=_POSITION_COLS)
        empty.attrs[UNMATCHED_SELLS_ATTR] = unmatched
        return empty
    df = pd.DataFrame(rows).sort_values("market_value", ascending=False)
    df.attrs[UNMATCHED_SELLS_ATTR] = unmatched
    return df


def positions_for_account(
    conn,
    account_id: int,
    last_prices: dict[str, float],
) -> pd.DataFrame:
    rows = cdb.load_trades(conn, account_id=account_id)
    return _positions_from_rows(rows, last_prices)


def positions_all_accounts(
    conn,
    last_prices: dict[str, float],
) -> pd.DataFrame:
    """One row per (account, ticker) — allows per-account attribution."""
    accounts = {a["id"]: a["name"] for a in cdb.load_accounts(conn)}
    frames: list[pd.DataFrame] = []
    # Summed across accounts; pd.concat does not carry .attrs through, so this
    # is rebuilt explicitly rather than inherited from the first frame.
    unmatched: dict[str, float] = {}
    for acc_id, acc_name in accounts.items():
        df = positions_for_account(conn, acc_id, last_prices)
        for tk, qty in df.attrs.get(UNMATCHED_SELLS_ATTR, {}).items():
            unmatched[tk] = unmatched.get(tk, 0.0) + qty
        if df.empty:
            continue
        df = df.copy()
        df.insert(0, "account_name", acc_name)
        df.insert(0, "account_id", acc_id)
        frames.append(df)
    if not frames:
        cols = ["account_id", "account_name"] + _POSITION_COLS
        out = pd.DataFrame(columns=cols)
    else:
        out = pd.concat(frames, ignore_index=True)
    out.attrs[UNMATCHED_SELLS_ATTR] = unmatched
    return out


def add_trade_to_account(
    conn,
    *,
    account_id: int,
    ticker: str,
    side: str,
    qty: float,
    price: float,
    trade_date: str,
    notes: str = "",
) -> tuple[int, bool]:
    ticker = ticker.strip().upper()
    side = side.strip().upper()
    if side not in ("BUY", "SELL"):
        raise ValueError("side must be BUY or SELL")
    if qty <= 0 or price < 0:
        raise ValueError("qty must be > 0 and price >= 0")
    return cdb.insert_trade(
        conn, account_id=account_id, ticker=ticker, side=side,
        qty=qty, price=price, trade_date=trade_date, notes=notes or None,
    )


def list_trades_df(conn, account_id: int | None = None) -> pd.DataFrame:
    rows = cdb.load_trades(conn, account_id=account_id)
    if not rows:
        return pd.DataFrame(columns=["id", "account_id", "ticker", "side",
                                       "qty", "price", "trade_date", "notes"])
    return pd.DataFrame(rows)[["id", "account_id", "ticker", "side",
                                 "qty", "price", "trade_date", "notes"]]


def init_db() -> None:
    """No-op shim — DB initialisation is handled by catalysts.db.migrate()."""
    pass
