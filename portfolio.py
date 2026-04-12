"""SQLite trade ledger and derived position computation."""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pandas as pd

DB_PATH = Path(__file__).parent / "data" / "dealscout.db"


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker    TEXT    NOT NULL,
                side      TEXT    NOT NULL CHECK (side IN ('BUY','SELL')),
                qty       REAL    NOT NULL CHECK (qty > 0),
                price     REAL    NOT NULL CHECK (price >= 0),
                ts        TEXT    NOT NULL,
                notes     TEXT
            )
        """)


@contextmanager
def _conn():
    con = sqlite3.connect(DB_PATH)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def add_trade(ticker: str, side: str, qty: float, price: float, notes: str = "") -> None:
    ticker = ticker.strip().upper()
    side = side.strip().upper()
    if side not in ("BUY", "SELL"):
        raise ValueError("side must be BUY or SELL")
    if qty <= 0 or price < 0:
        raise ValueError("qty must be > 0 and price >= 0")
    with _conn() as c:
        c.execute(
            "INSERT INTO trades (ticker, side, qty, price, ts, notes) VALUES (?,?,?,?,?,?)",
            (ticker, side, float(qty), float(price), datetime.utcnow().isoformat(timespec="seconds"), notes),
        )


def delete_trade(trade_id: int) -> None:
    with _conn() as c:
        c.execute("DELETE FROM trades WHERE id = ?", (int(trade_id),))


def list_trades() -> pd.DataFrame:
    with _conn() as c:
        df = pd.read_sql_query("SELECT id, ticker, side, qty, price, ts, notes FROM trades ORDER BY ts DESC", c)
    return df


def positions(last_prices: dict[str, float]) -> pd.DataFrame:
    """Compute current positions from the trade ledger using average cost basis.

    SELL trades realize P&L proportionally and reduce qty; cost basis stays per-share-average.
    """
    trades = list_trades().sort_values("ts")
    qty_by: dict[str, float] = defaultdict(float)
    cost_by: dict[str, float] = defaultdict(float)
    realized_by: dict[str, float] = defaultdict(float)

    for _, t in trades.iterrows():
        tk = t["ticker"]
        if t["side"] == "BUY":
            cost_by[tk] += t["qty"] * t["price"]
            qty_by[tk]  += t["qty"]
        else:  # SELL
            if qty_by[tk] <= 0:
                continue  # ignore unmatched sell
            avg_cost = cost_by[tk] / qty_by[tk]
            sell_qty = min(t["qty"], qty_by[tk])
            realized_by[tk] += (t["price"] - avg_cost) * sell_qty
            cost_by[tk] -= avg_cost * sell_qty
            qty_by[tk]  -= sell_qty

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
    return pd.DataFrame(rows).sort_values("market_value", ascending=False) if rows else pd.DataFrame(
        columns=["ticker", "qty", "avg_cost", "last", "market_value", "unrealized_pl", "realized_pl", "total_pl"]
    )
