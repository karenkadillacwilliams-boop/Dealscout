"""Built-in import profile definitions + user-profile CRUD."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


BUILTIN_PROFILES: list[dict] = [
    {
        "name": "Fidelity",
        "broker": "fidelity",
        "column_map": {
            "ticker":     "Symbol",
            "side":       "Action",
            "qty":        "Quantity",
            "price":      "Price",
            "trade_date": "Run Date",
        },
        "value_map": {
            "side": {"YOU BOUGHT": "BUY", "YOU SOLD": "SELL"},
        },
        "row_filter": {
            "skip_if_side_not_in_value_map": True,
        },
    },
    {
        "name": "Schwab",
        "broker": "schwab",
        "column_map": {
            "ticker":     "Symbol",
            "side":       "Action",
            "qty":        "Quantity",
            "price":      "Price",
            "trade_date": "Date",
        },
        "value_map": {
            "side": {"Buy": "BUY", "Sell": "SELL"},
        },
        "row_filter": {
            "skip_if_side_not_in_value_map": True,
        },
    },
    {
        "name": "Robinhood",
        "broker": "robinhood",
        "column_map": {
            "ticker":     "Instrument",
            "side":       "Trans Code",
            "qty":        "Quantity",
            "price":      "Price",
            "trade_date": "Activity Date",
        },
        "value_map": {
            "side": {"Buy": "BUY", "Sell": "SELL"},
        },
        "row_filter": {
            "skip_if_side_not_in_value_map": True,
        },
    },
    {
        "name": "MooMoo",
        "broker": "moomoo",
        "column_map": {
            "ticker":     "Symbol",
            "side":       "Direction",
            "qty":        "Quantity",
            "price":      "Price",
            "trade_date": "TradeTime",
        },
        "value_map": {
            "side": {"BUY": "BUY", "SELL": "SELL"},
        },
        "row_filter": {},
    },
    {
        "name": "Vanguard",
        "broker": "vanguard",
        "column_map": {
            "ticker":     "Symbol",
            "side":       "Transaction Type",
            "qty":        "Quantity",
            "price":      "Price",
            "trade_date": "Trade Date",
        },
        "value_map": {
            "side": {"Buy": "BUY", "Sell": "SELL"},
        },
        "row_filter": {
            "skip_if_side_not_in_value_map": True,
        },
    },
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def seed_builtin_profiles(conn: sqlite3.Connection) -> None:
    for p in BUILTIN_PROFILES:
        conn.execute(
            "INSERT OR IGNORE INTO import_profiles"
            "(name, broker, column_map, value_map, row_filter, builtin, created_at) "
            "VALUES(?,?,?,?,?,1,?)",
            (p["name"], p["broker"],
             json.dumps(p["column_map"]),
             json.dumps(p["value_map"]),
             json.dumps(p["row_filter"]),
             _now()),
        )
    conn.commit()


def load_profiles(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM import_profiles ORDER BY builtin DESC, name"
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        d["column_map"] = json.loads(d["column_map"])
        d["value_map"]  = json.loads(d["value_map"])
        d["row_filter"] = json.loads(d["row_filter"])
        out.append(d)
    return out


def load_profile(conn: sqlite3.Connection, profile_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM import_profiles WHERE id=?", (profile_id,)
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["column_map"] = json.loads(d["column_map"])
    d["value_map"]  = json.loads(d["value_map"])
    d["row_filter"] = json.loads(d["row_filter"])
    return d


def create_user_profile(
    conn: sqlite3.Connection,
    *,
    name: str,
    broker: str,
    column_map: dict,
    value_map: dict | None = None,
    row_filter: dict | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO import_profiles(name,broker,column_map,value_map,"
        "row_filter,builtin,created_at) VALUES(?,?,?,?,?,0,?)",
        (name, broker, json.dumps(column_map),
         json.dumps(value_map or {}), json.dumps(row_filter or {}), _now()),
    )
    conn.commit()
    return int(cur.lastrowid)
