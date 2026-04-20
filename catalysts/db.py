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

CREATE TABLE IF NOT EXISTS uoa_signals (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker           TEXT    NOT NULL,
    contract_ticker  TEXT    NOT NULL,
    contract_type    TEXT    NOT NULL,
    strike           REAL    NOT NULL,
    expiration_date  TEXT    NOT NULL,
    volume           INTEGER NOT NULL,
    open_interest    INTEGER NOT NULL,
    vol_oi_ratio     REAL    NOT NULL,
    ask              REAL,
    underlying_price REAL,
    flow_type        TEXT    NOT NULL DEFAULT 'normal',
    detected_at      TEXT    NOT NULL,
    UNIQUE(contract_ticker, detected_at)
);
CREATE INDEX IF NOT EXISTS idx_uoa_ticker ON uoa_signals(ticker, detected_at DESC);

CREATE TABLE IF NOT EXISTS technicals (
    ticker          TEXT PRIMARY KEY,
    rsi             REAL,
    macd_histogram  REAL,
    price_vs_sma50  REAL,
    label           TEXT NOT NULL,
    score           INTEGER NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS related_tickers (
    ticker         TEXT NOT NULL,
    related        TEXT NOT NULL,  -- JSON array
    fetched_at     TEXT NOT NULL,
    PRIMARY KEY (ticker)
);

CREATE TABLE IF NOT EXISTS accounts (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  name          TEXT    NOT NULL UNIQUE,
  type          TEXT    NOT NULL,
  broker        TEXT    NOT NULL,
  opened_date   TEXT    NOT NULL,
  initial_cash  REAL    NOT NULL DEFAULT 0,
  active        INTEGER NOT NULL DEFAULT 1,
  created_at    TEXT    NOT NULL,
  event_daily_pct  REAL,
  event_5day_pct   REAL
);

CREATE TABLE IF NOT EXISTS trades (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id      INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  ticker          TEXT    NOT NULL,
  side            TEXT    NOT NULL CHECK (side IN ('BUY','SELL')),
  qty             REAL    NOT NULL CHECK (qty > 0),
  price           REAL    NOT NULL CHECK (price >= 0),
  trade_date      TEXT    NOT NULL,
  notes           TEXT,
  import_batch_id INTEGER,
  dedup_key       TEXT    NOT NULL UNIQUE,
  created_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trades_account_date ON trades(account_id, trade_date DESC);
CREATE INDEX IF NOT EXISTS idx_trades_ticker       ON trades(ticker);

CREATE TABLE IF NOT EXISTS events (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id     INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  ticker         TEXT    NOT NULL,
  event_date     TEXT    NOT NULL,
  move_pct       REAL    NOT NULL,
  move_window    TEXT    NOT NULL,
  position_qty   REAL    NOT NULL,
  value_before   REAL    NOT NULL,
  value_after    REAL    NOT NULL,
  pnl_dollars    REAL    NOT NULL,
  catalyst_id    INTEGER REFERENCES catalysts(id) ON DELETE SET NULL,
  catalyst_type  TEXT,
  status         TEXT    NOT NULL DEFAULT 'pending',
  notes          TEXT,
  detected_at    TEXT    NOT NULL,
  confirmed_at   TEXT,
  UNIQUE(account_id, ticker, event_date, move_window)
);
CREATE INDEX IF NOT EXISTS idx_events_status   ON events(status, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_catalyst ON events(catalyst_id);

CREATE TABLE IF NOT EXISTS import_profiles (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT    NOT NULL UNIQUE,
  broker      TEXT    NOT NULL,
  column_map  TEXT    NOT NULL,
  value_map   TEXT    NOT NULL DEFAULT '{}',
  row_filter  TEXT    NOT NULL DEFAULT '{}',
  builtin     INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS import_batches (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id   INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  profile_id   INTEGER REFERENCES import_profiles(id),
  filename     TEXT    NOT NULL,
  row_count    INTEGER NOT NULL,
  inserted     INTEGER NOT NULL,
  duplicates   INTEGER NOT NULL,
  rejected     INTEGER NOT NULL DEFAULT 0,
  imported_at  TEXT    NOT NULL,
  notes        TEXT
);
"""


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    # check_same_thread=False lets Streamlit reuse a cached connection across
    # its per-rerun worker threads. Safe: SQLite (threadsafe=1 build) serialises
    # statements with its own mutex, and WAL permits concurrent readers.
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # Phase 5 migration: add flow_type to uoa_signals if missing
    try:
        conn.execute("ALTER TABLE uoa_signals ADD COLUMN flow_type TEXT DEFAULT 'normal'")
    except sqlite3.OperationalError:
        pass  # column already exists
    conn.commit()
    # Portfolio seeded profiles (idempotent via UNIQUE name + INSERT OR IGNORE)
    from portfolios.profiles import seed_builtin_profiles
    seed_builtin_profiles(conn)


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


def insert_uoa_signal(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO uoa_signals
           (ticker, contract_ticker, contract_type, strike, expiration_date,
            volume, open_interest, vol_oi_ratio, ask, underlying_price, flow_type, detected_at)
           VALUES(:ticker,:contract_ticker,:contract_type,:strike,:expiration_date,
                  :volume,:open_interest,:vol_oi_ratio,:ask,:underlying_price,:flow_type,:detected_at)""",
        row,
    )
    conn.commit()


def load_uoa_signals(conn: sqlite3.Connection, hours: int = 24) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM uoa_signals WHERE datetime(detected_at) >= datetime('now', ?) "
        "ORDER BY vol_oi_ratio DESC",
        (f"-{hours} hours",),
    ).fetchall()
    return [dict(r) for r in rows]


def uoa_count_for_ticker(conn: sqlite3.Connection, ticker: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM uoa_signals WHERE ticker=? "
        "AND datetime(detected_at) >= datetime('now', '-24 hours')",
        (ticker,),
    ).fetchone()[0]


def upsert_technical(
    conn: sqlite3.Connection,
    ticker: str,
    rsi,
    macd_histogram,
    price_vs_sma50,
    label: str,
    score: int,
) -> None:
    conn.execute(
        """INSERT INTO technicals(ticker, rsi, macd_histogram, price_vs_sma50, label, score, updated_at)
           VALUES(?,?,?,?,?,?,datetime('now'))
           ON CONFLICT(ticker) DO UPDATE SET
              rsi=excluded.rsi, macd_histogram=excluded.macd_histogram,
              price_vs_sma50=excluded.price_vs_sma50, label=excluded.label,
              score=excluded.score, updated_at=excluded.updated_at""",
        (ticker, rsi, macd_histogram, price_vs_sma50, label, score),
    )
    conn.commit()


def load_technicals(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute("SELECT * FROM technicals").fetchall()
    return {r["ticker"]: dict(r) for r in rows}


def upsert_related_tickers(
    conn: sqlite3.Connection, ticker: str, related: list[str]
) -> None:
    conn.execute(
        "INSERT INTO related_tickers(ticker, related, fetched_at) VALUES(?,?,?) "
        "ON CONFLICT(ticker) DO UPDATE SET "
        "related=excluded.related, fetched_at=excluded.fetched_at",
        (ticker.upper(), json.dumps(related), _now()),
    )
    conn.commit()


def load_related_tickers(
    conn: sqlite3.Connection, ticker: str, ttl_hours: int = 24
) -> list[str] | None:
    """Return cached related tickers if fresh (within ttl_hours), else None."""
    row = conn.execute(
        "SELECT related FROM related_tickers "
        "WHERE ticker=? AND datetime(fetched_at) >= datetime('now', ?)",
        (ticker.upper(), f"-{ttl_hours} hours"),
    ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return None


def load_related_tickers_all(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Return {ticker: [related...]} for all cached rows (no TTL filter)."""
    rows = conn.execute("SELECT ticker, related FROM related_tickers").fetchall()
    out: dict[str, list[str]] = {}
    for r in rows:
        try:
            out[r["ticker"]] = json.loads(r["related"])
        except (TypeError, ValueError):
            continue
    return out


def create_account(
    conn: sqlite3.Connection,
    *,
    name: str,
    type: str,
    broker: str,
    opened_date: str,
    initial_cash: float = 0.0,
    event_daily_pct: float | None = None,
    event_5day_pct: float | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO accounts(name,type,broker,opened_date,initial_cash,"
        "event_daily_pct,event_5day_pct,active,created_at) "
        "VALUES(?,?,?,?,?,?,?,1,?)",
        (name, type, broker, opened_date, initial_cash,
         event_daily_pct, event_5day_pct, _now()),
    )
    conn.commit()
    return int(cur.lastrowid)


def load_accounts(conn: sqlite3.Connection, active_only: bool = True) -> list[dict]:
    q = "SELECT * FROM accounts"
    if active_only:
        q += " WHERE active=1"
    q += " ORDER BY name"
    return [dict(r) for r in conn.execute(q).fetchall()]


def load_account(conn: sqlite3.Connection, account_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM accounts WHERE id=?", (account_id,)
    ).fetchone()
    return dict(row) if row else None


def deactivate_account(conn: sqlite3.Connection, account_id: int) -> None:
    conn.execute("UPDATE accounts SET active=0 WHERE id=?", (account_id,))
    conn.commit()


def update_account(
    conn: sqlite3.Connection,
    account_id: int,
    **fields,
) -> None:
    """Update any subset of: name, type, broker, opened_date, initial_cash,
    event_daily_pct, event_5day_pct, active.

    Each column is updated via its own fully-literal parameterised statement
    so no user-supplied text ever touches the SQL template.
    """
    if not conn.execute(
        "SELECT 1 FROM accounts WHERE id=?", (account_id,)
    ).fetchone():
        raise ValueError(f"account {account_id} not found")
    if "name" in fields:
        conn.execute(
            "UPDATE accounts SET name=? WHERE id=?",
            (fields["name"], account_id),
        )
    if "type" in fields:
        conn.execute(
            "UPDATE accounts SET type=? WHERE id=?",
            (fields["type"], account_id),
        )
    if "broker" in fields:
        conn.execute(
            "UPDATE accounts SET broker=? WHERE id=?",
            (fields["broker"], account_id),
        )
    if "opened_date" in fields:
        conn.execute(
            "UPDATE accounts SET opened_date=? WHERE id=?",
            (fields["opened_date"], account_id),
        )
    if "initial_cash" in fields:
        conn.execute(
            "UPDATE accounts SET initial_cash=? WHERE id=?",
            (fields["initial_cash"], account_id),
        )
    if "event_daily_pct" in fields:
        conn.execute(
            "UPDATE accounts SET event_daily_pct=? WHERE id=?",
            (fields["event_daily_pct"], account_id),
        )
    if "event_5day_pct" in fields:
        conn.execute(
            "UPDATE accounts SET event_5day_pct=? WHERE id=?",
            (fields["event_5day_pct"], account_id),
        )
    if "active" in fields:
        conn.execute(
            "UPDATE accounts SET active=? WHERE id=?",
            (fields["active"], account_id),
        )
    conn.commit()


def _trade_dedup_key(
    account_id: int, ticker: str, side: str,
    qty: float, price: float, trade_date: str,
) -> str:
    return f"{account_id}|{ticker.upper()}|{side}|{qty:.6f}|{price:.6f}|{trade_date}"


def insert_trade(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    ticker: str,
    side: str,
    qty: float,
    price: float,
    trade_date: str,
    notes: str | None = None,
    import_batch_id: int | None = None,
) -> tuple[int, bool]:
    """Insert a trade; on dedup_key conflict return (existing_id, False)."""
    dedup = _trade_dedup_key(account_id, ticker, side, qty, price, trade_date)
    try:
        cur = conn.execute(
            "INSERT INTO trades(account_id,ticker,side,qty,price,trade_date,"
            "notes,import_batch_id,dedup_key,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (account_id, ticker.upper(), side, qty, price, trade_date,
             notes, import_batch_id, dedup, _now()),
        )
        conn.commit()
        return int(cur.lastrowid), True
    except sqlite3.IntegrityError:
        row = conn.execute(
            "SELECT id FROM trades WHERE dedup_key=?", (dedup,)
        ).fetchone()
        if row is None:
            raise  # not a dedup collision — propagate the real error (FK violation, etc.)
        return int(row[0]), False


def load_trades(
    conn: sqlite3.Connection,
    *,
    account_id: int | None = None,
    ticker: str | None = None,
) -> list[dict]:
    q = "SELECT * FROM trades WHERE 1=1"
    params: list = []
    if account_id is not None:
        q += " AND account_id=?"
        params.append(account_id)
    if ticker is not None:
        q += " AND ticker=?"
        params.append(ticker.upper())
    q += " ORDER BY trade_date DESC, id DESC"
    return [dict(r) for r in conn.execute(q, params).fetchall()]


def delete_trade(conn: sqlite3.Connection, trade_id: int) -> None:
    conn.execute("DELETE FROM trades WHERE id=?", (trade_id,))
    conn.commit()


def insert_event(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    ticker: str,
    event_date: str,
    move_pct: float,
    move_window: str,
    position_qty: float,
    value_before: float,
    value_after: float,
    pnl_dollars: float,
    catalyst_id: int | None = None,
) -> tuple[int, bool]:
    """Insert an event; on UNIQUE conflict return (existing_id, False)."""
    try:
        cur = conn.execute(
            "INSERT INTO events(account_id,ticker,event_date,move_pct,"
            "move_window,position_qty,value_before,value_after,pnl_dollars,"
            "catalyst_id,status,detected_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,'pending',?)",
            (account_id, ticker.upper(), event_date, move_pct, move_window,
             position_qty, value_before, value_after, pnl_dollars,
             catalyst_id, _now()),
        )
        conn.commit()
        return int(cur.lastrowid), True
    except sqlite3.IntegrityError:
        row = conn.execute(
            "SELECT id FROM events WHERE account_id=? AND ticker=? "
            "AND event_date=? AND move_window=?",
            (account_id, ticker.upper(), event_date, move_window),
        ).fetchone()
        if row is None:
            raise
        return int(row[0]), False


def load_events(
    conn: sqlite3.Connection,
    *,
    account_id: int | None = None,
    status: str | None = None,
    ticker: str | None = None,
    since_days: int | None = None,
) -> list[dict]:
    q = "SELECT * FROM events WHERE 1=1"
    params: list = []
    if account_id is not None:
        q += " AND account_id=?"
        params.append(account_id)
    if status is not None:
        q += " AND status=?"
        params.append(status)
    if ticker is not None:
        q += " AND ticker=?"
        params.append(ticker.upper())
    if since_days is not None:
        q += " AND date(event_date) >= date('now', ?)"
        params.append(f"-{since_days} days")
    q += " ORDER BY event_date DESC, detected_at DESC"
    return [dict(r) for r in conn.execute(q, params).fetchall()]


def load_event(conn: sqlite3.Connection, event_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    return dict(row) if row else None


def pending_event_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM events WHERE status='pending'"
    ).fetchone()[0]


def update_event(conn: sqlite3.Connection, event_id: int, **fields) -> None:
    """Update status / catalyst_id / catalyst_type / notes.
    If status becomes 'confirmed', stamps confirmed_at."""
    if "status" in fields:
        conn.execute(
            "UPDATE events SET status=? WHERE id=?",
            (fields["status"], event_id),
        )
    if "catalyst_id" in fields:
        conn.execute(
            "UPDATE events SET catalyst_id=? WHERE id=?",
            (fields["catalyst_id"], event_id),
        )
    if "catalyst_type" in fields:
        conn.execute(
            "UPDATE events SET catalyst_type=? WHERE id=?",
            (fields["catalyst_type"], event_id),
        )
    if "notes" in fields:
        conn.execute(
            "UPDATE events SET notes=? WHERE id=?",
            (fields["notes"], event_id),
        )
    if fields.get("status") == "confirmed":
        conn.execute(
            "UPDATE events SET confirmed_at=? WHERE id=?",
            (_now(), event_id),
        )
    conn.commit()


def create_import_batch(
    conn: sqlite3.Connection,
    *,
    account_id: int,
    profile_id: int | None,
    filename: str,
    row_count: int,
    inserted: int,
    duplicates: int,
    rejected: int = 0,
    notes: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO import_batches(account_id,profile_id,filename,row_count,"
        "inserted,duplicates,rejected,imported_at,notes) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (account_id, profile_id, filename, row_count, inserted, duplicates,
         rejected, _now(), notes),
    )
    conn.commit()
    return int(cur.lastrowid)


def load_import_batches(
    conn: sqlite3.Connection, account_id: int | None = None, limit: int = 50,
) -> list[dict]:
    q = "SELECT * FROM import_batches"
    params: list = []
    if account_id is not None:
        q += " WHERE account_id=?"
        params.append(account_id)
    q += " ORDER BY imported_at DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(q, params).fetchall()]
