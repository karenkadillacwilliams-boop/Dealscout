"""SQLite connection, migrations, and typed CRUD for Catalyst Radar."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from catalysts.types import RerankedItem

DB_PATH = Path(__file__).resolve().parent.parent / "dealscout.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS catalysts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker        TEXT    NOT NULL,
    source        TEXT    NOT NULL,
    source_id     TEXT    NOT NULL,
    form_type     TEXT,
    headline      TEXT    NOT NULL,
    url           TEXT    NOT NULL,
    published_at  TEXT    NOT NULL,
    kw_score      INTEGER NOT NULL,
    llm_score     INTEGER,
    final_score   INTEGER NOT NULL,
    tags          TEXT,
    rationale     TEXT,
    seen          INTEGER NOT NULL DEFAULT 0,
    fetched_at    TEXT    NOT NULL,
    UNIQUE(source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_catalysts_ticker_time ON catalysts(ticker, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_catalysts_score       ON catalysts(final_score DESC);

CREATE TABLE IF NOT EXISTS alert_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    catalyst_id    INTEGER NOT NULL REFERENCES catalysts(id),
    ticker         TEXT    NOT NULL,
    score_bucket   INTEGER NOT NULL,
    channels       TEXT    NOT NULL,
    sent_at        TEXT    NOT NULL,
    ok             INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alertlog_dedup ON alert_log(ticker, score_bucket, sent_at DESC);

CREATE TABLE IF NOT EXISTS universe (
    ticker     TEXT PRIMARY KEY,
    name       TEXT,
    added_at   TEXT NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS options_snapshot (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker           TEXT    NOT NULL,
    contract_ticker  TEXT    NOT NULL UNIQUE,
    contract_type    TEXT    NOT NULL,
    strike           REAL    NOT NULL,
    expiration_date  TEXT    NOT NULL,
    dte              INTEGER NOT NULL,
    ask              REAL    NOT NULL,
    bid              REAL    NOT NULL,
    mid              REAL    NOT NULL,
    volume           INTEGER NOT NULL DEFAULT 0,
    open_interest    INTEGER NOT NULL DEFAULT 0,
    iv               REAL,
    delta            REAL,
    gamma            REAL,
    theta            REAL,
    vega             REAL,
    underlying_price REAL    NOT NULL,
    leverage_ratio   REAL    NOT NULL,
    iv_rank          REAL,
    composite_score  REAL    NOT NULL,
    fetched_at       TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_opts_ticker    ON options_snapshot(ticker);
CREATE INDEX IF NOT EXISTS idx_opts_composite ON options_snapshot(composite_score DESC);

CREATE TABLE IF NOT EXISTS iv_history (
    ticker  TEXT NOT NULL,
    date    TEXT NOT NULL,
    avg_iv  REAL NOT NULL,
    UNIQUE(ticker, date)
);
"""


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def upsert_universe(conn: sqlite3.Connection, ticker: str, name: str | None = None) -> None:
    conn.execute(
        "INSERT INTO universe(ticker,name,added_at,active) VALUES(?,?,?,1) "
        "ON CONFLICT(ticker) DO UPDATE SET name=excluded.name, active=1",
        (ticker.upper(), name, _now()),
    )
    conn.commit()


def deactivate_ticker(conn: sqlite3.Connection, ticker: str) -> None:
    conn.execute("UPDATE universe SET active=0 WHERE ticker=?", (ticker.upper(),))
    conn.commit()


def load_active_universe(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT ticker FROM universe WHERE active=1 ORDER BY ticker"
    )]


def seed_universe_if_empty(conn: sqlite3.Connection, tickers: Iterable[str]) -> None:
    n = conn.execute("SELECT COUNT(*) FROM universe").fetchone()[0]
    if n:
        return
    for t in tickers:
        conn.execute(
            "INSERT OR IGNORE INTO universe(ticker,added_at,active) VALUES(?,?,1)",
            (t.upper(), _now()),
        )
    conn.commit()


def persist_catalyst(conn: sqlite3.Connection, item: RerankedItem) -> int:
    raw = item.scored.raw
    conn.execute(
        """INSERT OR IGNORE INTO catalysts
           (ticker, source, source_id, form_type, headline, url, published_at,
            kw_score, llm_score, final_score, tags, rationale, seen, fetched_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0,?)""",
        (
            raw.ticker, raw.source, raw.source_id, raw.form_type,
            raw.headline, raw.url, raw.published_at,
            item.scored.kw_score, item.llm_score, item.final_score,
            json.dumps(list(item.scored.tags)), item.rationale, _now(),
        ),
    )
    row = conn.execute(
        "SELECT id FROM catalysts WHERE source=? AND source_id=?",
        (raw.source, raw.source_id),
    ).fetchone()
    conn.commit()
    return int(row[0])


def mark_seen(conn: sqlite3.Connection, catalyst_ids: Iterable[int]) -> None:
    ids = list(catalyst_ids)
    if not ids:
        return
    conn.executemany("UPDATE catalysts SET seen=1 WHERE id=?", [(i,) for i in ids])
    conn.commit()


def unseen_alert_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM catalysts WHERE final_score >= 70 AND seen = 0"
    ).fetchone()[0]


def last_poll_time(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT MAX(fetched_at) FROM catalysts").fetchone()
    return row[0]


def upsert_option_snapshot(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """INSERT INTO options_snapshot
           (ticker, contract_ticker, contract_type, strike, expiration_date, dte,
            ask, bid, mid, volume, open_interest, iv, delta, gamma, theta, vega,
            underlying_price, leverage_ratio, iv_rank, composite_score, fetched_at)
           VALUES(:ticker,:contract_ticker,:contract_type,:strike,:expiration_date,:dte,
                  :ask,:bid,:mid,:volume,:open_interest,:iv,:delta,:gamma,:theta,:vega,
                  :underlying_price,:leverage_ratio,:iv_rank,:composite_score,:fetched_at)
           ON CONFLICT(contract_ticker) DO UPDATE SET
              ask=excluded.ask, bid=excluded.bid, mid=excluded.mid,
              volume=excluded.volume, open_interest=excluded.open_interest,
              iv=excluded.iv, delta=excluded.delta, gamma=excluded.gamma,
              theta=excluded.theta, vega=excluded.vega,
              underlying_price=excluded.underlying_price,
              leverage_ratio=excluded.leverage_ratio,
              iv_rank=excluded.iv_rank, composite_score=excluded.composite_score,
              fetched_at=excluded.fetched_at""",
        row,
    )
    conn.commit()


def upsert_iv_history(conn: sqlite3.Connection, ticker: str, date: str, avg_iv: float) -> None:
    conn.execute(
        "INSERT INTO iv_history(ticker, date, avg_iv) VALUES(?,?,?) "
        "ON CONFLICT(ticker, date) DO UPDATE SET avg_iv=excluded.avg_iv",
        (ticker, date, avg_iv),
    )
    conn.commit()


def prune_iv_history(conn: sqlite3.Connection, keep_days: int = 60) -> None:
    conn.execute(
        "DELETE FROM iv_history WHERE date < date('now', ?)",
        (f"-{keep_days} days",),
    )
    conn.commit()


def load_options_for_ticker(conn: sqlite3.Connection, ticker: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM options_snapshot WHERE ticker=? ORDER BY composite_score DESC",
        (ticker,),
    ).fetchall()
    return [dict(r) for r in rows]


def load_all_options(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM options_snapshot ORDER BY composite_score DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def options_badge_counts(conn: sqlite3.Connection, ticker: str) -> tuple[int, int]:
    row = conn.execute(
        "SELECT "
        "SUM(CASE WHEN contract_type='call' THEN 1 ELSE 0 END) AS calls, "
        "SUM(CASE WHEN contract_type='put' THEN 1 ELSE 0 END) AS puts "
        "FROM options_snapshot WHERE ticker=?",
        (ticker,),
    ).fetchone()
    return (row["calls"] or 0, row["puts"] or 0)


def clear_stale_options(conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM options_snapshot WHERE date(expiration_date) < date('now') OR dte < 7"
    )
    conn.commit()
