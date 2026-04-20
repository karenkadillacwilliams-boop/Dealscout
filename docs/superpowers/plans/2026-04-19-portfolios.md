# Multi-Account Portfolios Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add multi-account portfolio tracking with CSV import and catalyst-tagged position-event labeling, so the user's own trade outcomes become training signal for the catalyst scorer.

**Architecture:** Unify all portfolio tables (`accounts`, `trades`, `events`, `import_profiles`, `import_batches`) into the root `dealscout.db` alongside `catalysts`. `events.catalyst_id` is a real FK so the feedback loop query is trivial SQL. A CSV importer with saved column-mapping profiles covers Fidelity / Schwab / Robinhood / MooMoo / Vanguard on day one and any new broker via the mapping wizard.

**Tech Stack:** Python 3.14, SQLite (WAL mode), Streamlit 1.56+, pandas, Polygon.io daily bars (via existing `catalysts/polygon_client.py`), pytest.

**Spec:** `docs/superpowers/specs/2026-04-19-portfolios-design.md`

---

## Task 1: Schema + accounts CRUD

**Files:**
- Modify: `catalysts/db.py` — extend `SCHEMA` string; add account CRUD functions at the end of the file
- Create: `tests/test_portfolios_db.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_portfolios_db.py`:

```python
import pytest
import sqlite3

from catalysts import db as cdb


def test_migrate_creates_portfolio_tables(tmp_db):
    cdb.migrate(tmp_db)
    names = {r[0] for r in tmp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"accounts", "trades", "events",
            "import_profiles", "import_batches"} <= names


def test_create_and_load_account(tmp_db):
    cdb.migrate(tmp_db)
    acc_id = cdb.create_account(
        tmp_db, name="Main Roth", type="roth", broker="fidelity",
        opened_date="2024-01-15", initial_cash=5000.0,
    )
    assert acc_id > 0
    rows = cdb.load_accounts(tmp_db)
    assert len(rows) == 1
    assert rows[0]["name"] == "Main Roth"
    assert rows[0]["initial_cash"] == 5000.0
    assert rows[0]["active"] == 1


def test_load_accounts_excludes_inactive_by_default(tmp_db):
    cdb.migrate(tmp_db)
    a = cdb.create_account(tmp_db, name="Old", type="taxable",
                            broker="robinhood", opened_date="2020-01-01")
    cdb.deactivate_account(tmp_db, a)
    assert cdb.load_accounts(tmp_db) == []
    assert len(cdb.load_accounts(tmp_db, active_only=False)) == 1


def test_account_name_must_be_unique(tmp_db):
    cdb.migrate(tmp_db)
    cdb.create_account(tmp_db, name="Main", type="taxable",
                        broker="fidelity", opened_date="2024-01-01")
    with pytest.raises(sqlite3.IntegrityError):
        cdb.create_account(tmp_db, name="Main", type="roth",
                            broker="schwab", opened_date="2024-02-01")


def test_account_event_thresholds_are_optional(tmp_db):
    cdb.migrate(tmp_db)
    acc_id = cdb.create_account(
        tmp_db, name="Custom", type="taxable", broker="other",
        opened_date="2024-01-01", event_daily_pct=3.0, event_5day_pct=7.5,
    )
    row = cdb.load_accounts(tmp_db)[0]
    assert row["event_daily_pct"] == 3.0
    assert row["event_5day_pct"] == 7.5
```

- [ ] **Step 2: Run tests — expect all to FAIL**

Run: `.venv/Scripts/python -m pytest tests/test_portfolios_db.py -v`
Expected: 5 failures (tables don't exist / `create_account` not defined).

- [ ] **Step 3: Extend `catalysts/db.py` SCHEMA**

In `catalysts/db.py`, inside the `SCHEMA` triple-quoted string, append after the `technicals` and `related_tickers` table blocks (before the closing `"""`):

```sql

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
```

- [ ] **Step 4: Add account CRUD at the end of `catalysts/db.py`**

Append to `catalysts/db.py`:

```python
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
    event_daily_pct, event_5day_pct, active."""
    allowed = {"name", "type", "broker", "opened_date", "initial_cash",
               "event_daily_pct", "event_5day_pct", "active"}
    sets = [f"{k}=?" for k in fields if k in allowed]
    if not sets:
        return
    values = [fields[k] for k in fields if k in allowed]
    values.append(account_id)
    conn.execute(
        f"UPDATE accounts SET {', '.join(sets)} WHERE id=?",
        values,
    )
    conn.commit()
```

- [ ] **Step 5: Run tests — expect all PASS**

Run: `.venv/Scripts/python -m pytest tests/test_portfolios_db.py -v`
Expected: 5 passing.

- [ ] **Step 6: Run the full suite — expect no regressions**

Run: `.venv/Scripts/python -m pytest tests/ -q`
Expected: all pre-existing tests still pass (105 + 5 new = 110).

- [ ] **Step 7: Commit**

```bash
git add catalysts/db.py tests/test_portfolios_db.py
git commit -m "feat(portfolios): add accounts/trades/events schema + account CRUD

Five new tables in the root dealscout.db: accounts, trades, events,
import_profiles, import_batches. All FKs into catalysts.id are set up so
the event auto-link query is trivial SQL.

Accounts CRUD covers create/load/deactivate/update with unique-name
enforcement and optional per-account event-threshold overrides.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Trades CRUD, events CRUD, import_batches CRUD

**Files:**
- Modify: `catalysts/db.py` — append trade/event/import_batch CRUD
- Modify: `tests/test_portfolios_db.py` — append tests

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_portfolios_db.py`:

```python
def _make_acc(conn, name="Main"):
    return cdb.create_account(conn, name=name, type="taxable",
                                broker="fidelity", opened_date="2024-01-01")


def test_insert_trade_and_compute_dedup_key(tmp_db):
    cdb.migrate(tmp_db)
    acc = _make_acc(tmp_db)
    tid, was_new = cdb.insert_trade(
        tmp_db, account_id=acc, ticker="NVDA", side="BUY",
        qty=10.0, price=500.0, trade_date="2026-04-10",
    )
    assert tid > 0
    assert was_new is True


def test_insert_duplicate_trade_is_noop(tmp_db):
    cdb.migrate(tmp_db)
    acc = _make_acc(tmp_db)
    cdb.insert_trade(tmp_db, account_id=acc, ticker="NVDA", side="BUY",
                      qty=10.0, price=500.0, trade_date="2026-04-10")
    tid2, was_new = cdb.insert_trade(
        tmp_db, account_id=acc, ticker="NVDA", side="BUY",
        qty=10.0, price=500.0, trade_date="2026-04-10",
    )
    assert was_new is False
    count = tmp_db.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    assert count == 1


def test_dedup_key_differs_per_account(tmp_db):
    """Same trade in two accounts = two rows."""
    cdb.migrate(tmp_db)
    a = _make_acc(tmp_db, "A")
    b = _make_acc(tmp_db, "B")
    cdb.insert_trade(tmp_db, account_id=a, ticker="NVDA", side="BUY",
                      qty=10.0, price=500.0, trade_date="2026-04-10")
    _, was_new = cdb.insert_trade(tmp_db, account_id=b, ticker="NVDA",
                                   side="BUY", qty=10.0, price=500.0,
                                   trade_date="2026-04-10")
    assert was_new is True


def test_load_trades_for_account(tmp_db):
    cdb.migrate(tmp_db)
    acc = _make_acc(tmp_db)
    cdb.insert_trade(tmp_db, account_id=acc, ticker="NVDA", side="BUY",
                      qty=10.0, price=500.0, trade_date="2026-04-10")
    cdb.insert_trade(tmp_db, account_id=acc, ticker="AAPL", side="BUY",
                      qty=5.0, price=200.0, trade_date="2026-04-11")
    rows = cdb.load_trades(tmp_db, account_id=acc)
    assert len(rows) == 2
    # Newest first
    assert rows[0]["ticker"] == "AAPL"


def test_insert_event_idempotent(tmp_db):
    cdb.migrate(tmp_db)
    acc = _make_acc(tmp_db)
    eid1, was_new1 = cdb.insert_event(
        tmp_db, account_id=acc, ticker="NVDA", event_date="2026-04-15",
        move_pct=8.2, move_window="1d", position_qty=10.0,
        value_before=5000.0, value_after=5410.0, pnl_dollars=410.0,
    )
    eid2, was_new2 = cdb.insert_event(
        tmp_db, account_id=acc, ticker="NVDA", event_date="2026-04-15",
        move_pct=8.2, move_window="1d", position_qty=10.0,
        value_before=5000.0, value_after=5410.0, pnl_dollars=410.0,
    )
    assert was_new1 is True
    assert was_new2 is False
    assert eid1 == eid2


def test_1d_and_5d_events_on_same_day_coexist(tmp_db):
    cdb.migrate(tmp_db)
    acc = _make_acc(tmp_db)
    cdb.insert_event(tmp_db, account_id=acc, ticker="NVDA",
                      event_date="2026-04-15", move_pct=6.0, move_window="1d",
                      position_qty=10.0, value_before=5000.0,
                      value_after=5300.0, pnl_dollars=300.0)
    _, was_new = cdb.insert_event(tmp_db, account_id=acc, ticker="NVDA",
                                   event_date="2026-04-15", move_pct=12.0,
                                   move_window="5d", position_qty=10.0,
                                   value_before=4700.0, value_after=5300.0,
                                   pnl_dollars=600.0)
    assert was_new is True
    assert tmp_db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2


def test_update_event_status_and_confirmed_at(tmp_db):
    cdb.migrate(tmp_db)
    acc = _make_acc(tmp_db)
    eid, _ = cdb.insert_event(
        tmp_db, account_id=acc, ticker="NVDA", event_date="2026-04-15",
        move_pct=8.2, move_window="1d", position_qty=10.0,
        value_before=5000.0, value_after=5410.0, pnl_dollars=410.0,
    )
    cdb.update_event(tmp_db, eid,
                      status="confirmed", catalyst_type="earnings",
                      notes="FY26 guide")
    row = cdb.load_event(tmp_db, eid)
    assert row["status"] == "confirmed"
    assert row["catalyst_type"] == "earnings"
    assert row["confirmed_at"] is not None


def test_load_pending_events_excludes_dismissed(tmp_db):
    cdb.migrate(tmp_db)
    acc = _make_acc(tmp_db)
    e1, _ = cdb.insert_event(tmp_db, account_id=acc, ticker="A",
                              event_date="2026-04-15", move_pct=6.0,
                              move_window="1d", position_qty=1.0,
                              value_before=100.0, value_after=106.0,
                              pnl_dollars=6.0)
    e2, _ = cdb.insert_event(tmp_db, account_id=acc, ticker="B",
                              event_date="2026-04-15", move_pct=7.0,
                              move_window="1d", position_qty=1.0,
                              value_before=100.0, value_after=107.0,
                              pnl_dollars=7.0)
    cdb.update_event(tmp_db, e2, status="dismissed")
    pending = cdb.load_events(tmp_db, status="pending")
    assert len(pending) == 1
    assert pending[0]["id"] == e1


def test_count_pending_events(tmp_db):
    cdb.migrate(tmp_db)
    acc = _make_acc(tmp_db)
    cdb.insert_event(tmp_db, account_id=acc, ticker="A",
                      event_date="2026-04-15", move_pct=6.0, move_window="1d",
                      position_qty=1.0, value_before=100.0, value_after=106.0,
                      pnl_dollars=6.0)
    cdb.insert_event(tmp_db, account_id=acc, ticker="B",
                      event_date="2026-04-15", move_pct=7.0, move_window="1d",
                      position_qty=1.0, value_before=100.0, value_after=107.0,
                      pnl_dollars=7.0)
    assert cdb.pending_event_count(tmp_db) == 2


def test_create_import_batch(tmp_db):
    cdb.migrate(tmp_db)
    acc = _make_acc(tmp_db)
    bid = cdb.create_import_batch(
        tmp_db, account_id=acc, profile_id=None, filename="t.csv",
        row_count=5, inserted=3, duplicates=2, rejected=0,
    )
    assert bid > 0
    rows = cdb.load_import_batches(tmp_db, account_id=acc)
    assert rows[0]["inserted"] == 3
    assert rows[0]["duplicates"] == 2
```

- [ ] **Step 2: Run tests — expect all to FAIL**

Run: `.venv/Scripts/python -m pytest tests/test_portfolios_db.py -v -k "trade or event or import_batch"`
Expected: multiple failures (functions not defined).

- [ ] **Step 3: Implement trade/event/batch CRUD**

Append to `catalysts/db.py`:

```python
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
    allowed = {"status", "catalyst_id", "catalyst_type", "notes"}
    sets = [f"{k}=?" for k in fields if k in allowed]
    values = [fields[k] for k in fields if k in allowed]
    if fields.get("status") == "confirmed":
        sets.append("confirmed_at=?")
        values.append(_now())
    if not sets:
        return
    values.append(event_id)
    conn.execute(
        f"UPDATE events SET {', '.join(sets)} WHERE id=?", values,
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
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `.venv/Scripts/python -m pytest tests/test_portfolios_db.py -v`
Expected: all 15 tests passing.

- [ ] **Step 5: Full suite**

Run: `.venv/Scripts/python -m pytest tests/ -q`
Expected: 115 passing.

- [ ] **Step 6: Commit**

```bash
git add catalysts/db.py tests/test_portfolios_db.py
git commit -m "feat(portfolios): trades/events/import_batches CRUD + dedup

Trade dedup_key (account|ticker|side|qty|price|date) is computed at
insert time; conflict returns (existing_id, False) so re-importing the
same CSV is a safe no-op.

Events use UNIQUE(account_id, ticker, event_date, move_window) so the
detector can INSERT OR IGNORE and re-running writes zero duplicates.
Same day can have both a 1d and 5d event (distinct move_window).

update_event stamps confirmed_at when status transitions to 'confirmed'.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Rewrite `portfolio.py` for multi-account; update Holdings + Trades pages

**Files:**
- Rewrite: `portfolio.py` — replace single-book logic with account-scoped API
- Modify: `app_pages/holdings.py` — account filter dropdown
- Modify: `app_pages/trades.py` — scope to selected account, empty-state guard
- Modify: `app_pages/shared.py` — add `portfolio_context()` helper (selected account + positions)
- Create: `tests/test_portfolio.py` — positions math under multi-account

- [ ] **Step 1: Write the failing tests**

Create `tests/test_portfolio.py`:

```python
from catalysts import db as cdb
import portfolio


def _seed_account(conn, name="A"):
    return cdb.create_account(conn, name=name, type="taxable",
                                broker="fidelity", opened_date="2024-01-01")


def test_positions_empty_when_no_trades(tmp_db):
    cdb.migrate(tmp_db)
    acc = _seed_account(tmp_db)
    df = portfolio.positions_for_account(tmp_db, acc, last_prices={})
    assert df.empty


def test_positions_single_buy(tmp_db):
    cdb.migrate(tmp_db)
    acc = _seed_account(tmp_db)
    cdb.insert_trade(tmp_db, account_id=acc, ticker="NVDA", side="BUY",
                      qty=10.0, price=500.0, trade_date="2026-04-10")
    df = portfolio.positions_for_account(tmp_db, acc, last_prices={"NVDA": 520.0})
    assert len(df) == 1
    row = df.iloc[0]
    assert row["ticker"] == "NVDA"
    assert row["qty"] == 10.0
    assert row["avg_cost"] == 500.0
    assert row["market_value"] == 5200.0
    assert row["unrealized_pl"] == 200.0


def test_positions_buy_then_sell_realizes_pl(tmp_db):
    cdb.migrate(tmp_db)
    acc = _seed_account(tmp_db)
    cdb.insert_trade(tmp_db, account_id=acc, ticker="NVDA", side="BUY",
                      qty=10.0, price=500.0, trade_date="2026-04-10")
    cdb.insert_trade(tmp_db, account_id=acc, ticker="NVDA", side="SELL",
                      qty=4.0, price=600.0, trade_date="2026-04-12")
    df = portfolio.positions_for_account(tmp_db, acc, last_prices={"NVDA": 520.0})
    row = df.iloc[0]
    assert row["qty"] == 6.0
    assert row["avg_cost"] == 500.0
    assert row["realized_pl"] == 400.0  # (600-500) * 4


def test_positions_scoped_per_account(tmp_db):
    cdb.migrate(tmp_db)
    a = _seed_account(tmp_db, "A")
    b = _seed_account(tmp_db, "B")
    cdb.insert_trade(tmp_db, account_id=a, ticker="NVDA", side="BUY",
                      qty=10.0, price=500.0, trade_date="2026-04-10")
    cdb.insert_trade(tmp_db, account_id=b, ticker="AAPL", side="BUY",
                      qty=5.0, price=200.0, trade_date="2026-04-10")
    df_a = portfolio.positions_for_account(tmp_db, a, last_prices={})
    df_b = portfolio.positions_for_account(tmp_db, b, last_prices={})
    assert list(df_a["ticker"]) == ["NVDA"]
    assert list(df_b["ticker"]) == ["AAPL"]


def test_positions_all_accounts(tmp_db):
    cdb.migrate(tmp_db)
    a = _seed_account(tmp_db, "A")
    b = _seed_account(tmp_db, "B")
    cdb.insert_trade(tmp_db, account_id=a, ticker="NVDA", side="BUY",
                      qty=10.0, price=500.0, trade_date="2026-04-10")
    cdb.insert_trade(tmp_db, account_id=b, ticker="NVDA", side="BUY",
                      qty=5.0, price=450.0, trade_date="2026-04-11")
    df = portfolio.positions_all_accounts(tmp_db,
                                           last_prices={"NVDA": 500.0})
    # Same ticker across accounts stays as two rows so we can see per-account
    assert len(df) == 2
    assert set(df["account_name"]) == {"A", "B"}
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `.venv/Scripts/python -m pytest tests/test_portfolio.py -v`
Expected: failures (functions not defined).

- [ ] **Step 3: Rewrite `portfolio.py`**

Replace the entire contents of `portfolio.py` with:

```python
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


def _positions_from_rows(
    trade_rows: list[dict],
    last_prices: dict[str, float],
) -> pd.DataFrame:
    """Derive positions using average-cost basis from a chronological trade list."""
    rows_sorted = sorted(trade_rows, key=lambda r: (r["trade_date"], r["id"]))

    qty_by: dict[str, float] = defaultdict(float)
    cost_by: dict[str, float] = defaultdict(float)
    realized_by: dict[str, float] = defaultdict(float)

    for t in rows_sorted:
        tk = t["ticker"]
        if t["side"] == "BUY":
            cost_by[tk] += t["qty"] * t["price"]
            qty_by[tk] += t["qty"]
        else:  # SELL
            if qty_by[tk] <= 0:
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
    if not rows:
        return pd.DataFrame(columns=_POSITION_COLS)
    return pd.DataFrame(rows).sort_values("market_value", ascending=False)


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
    for acc_id, acc_name in accounts.items():
        df = positions_for_account(conn, acc_id, last_prices)
        if df.empty:
            continue
        df = df.copy()
        df.insert(0, "account_name", acc_name)
        df.insert(0, "account_id", acc_id)
        frames.append(df)
    if not frames:
        cols = ["account_id", "account_name"] + _POSITION_COLS
        return pd.DataFrame(columns=cols)
    return pd.concat(frames, ignore_index=True)


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
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `.venv/Scripts/python -m pytest tests/test_portfolio.py -v`
Expected: 5 passing.

- [ ] **Step 5: Update `app_pages/shared.py`**

Append to `app_pages/shared.py`:

```python
def active_accounts() -> list[dict]:
    """All active accounts for the current session."""
    from catalysts import db as cdb
    return cdb.load_accounts(get_conn())
```

- [ ] **Step 6: Replace `app_pages/holdings.py`**

Replace the entire file contents with:

```python
"""Holdings page — account filter, aggregated positions across all accounts."""
from __future__ import annotations

import plotly.express as px
import streamlit as st

import portfolio
from tickers import NAMES

from app_pages.shared import (
    active_accounts, fmt_money, get_conn, price_context,
)


def render() -> None:
    conn = get_conn()
    accounts = active_accounts()
    _t, _p, _r, last_prices = price_context()

    st.title("Holdings")

    if not accounts:
        st.info("No accounts yet. Go to the **Accounts** page to create one, "
                "then use **Import** or **Trades** to add positions.")
        return

    options = [("All accounts", None)] + [(a["name"], a["id"]) for a in accounts]
    labels = [o[0] for o in options]
    choice = st.selectbox("Account", options=labels, index=0)
    account_id = dict(options)[choice]

    if account_id is None:
        pos = portfolio.positions_all_accounts(conn, last_prices)
    else:
        pos = portfolio.positions_for_account(conn, account_id, last_prices)

    if pos.empty:
        st.info("No positions in this view. Record a buy on Trades or upload "
                "via Import.")
        return

    total_mv = pos["market_value"].sum()
    total_unr = pos["unrealized_pl"].sum()
    total_rea = pos["realized_pl"].sum()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Rows", len(pos))
    c2.metric("Market value", fmt_money(total_mv))
    c3.metric("Unrealized P/L", fmt_money(total_unr))
    c4.metric("Realized P/L", fmt_money(total_rea))

    view = pos.copy()
    if "ticker" in view.columns:
        view.insert(view.columns.get_loc("ticker") + 1, "name",
                     view["ticker"].map(NAMES).fillna(""))
    rename_map = {
        "account_name": "Account", "ticker": "Ticker", "name": "Name",
        "qty": "Qty", "avg_cost": "Avg cost", "last": "Last",
        "market_value": "Market value",
        "unrealized_pl": "Unrealized P/L",
        "realized_pl": "Realized P/L", "total_pl": "Total P/L",
    }
    view = view.rename(columns=rename_map)
    display_cols = [c for c in
                     ["Account", "Ticker", "Name", "Qty", "Avg cost", "Last",
                      "Market value", "Unrealized P/L", "Realized P/L", "Total P/L"]
                     if c in view.columns]

    st.dataframe(
        view[display_cols].style.format({
            "Qty": "{:,.4f}",
            "Avg cost": "${:,.2f}", "Last": "${:,.2f}",
            "Market value": "${:,.2f}", "Unrealized P/L": "${:,.2f}",
            "Realized P/L": "${:,.2f}", "Total P/L": "${:,.2f}",
        }),
        width="stretch", hide_index=True,
    )

    if total_mv > 0 and "Ticker" in view.columns:
        st.subheader("Allocation")
        fig = px.pie(view, values="Market value", names="Ticker", hole=0.45)
        st.plotly_chart(fig, width="stretch")
```

- [ ] **Step 7: Replace `app_pages/trades.py`**

Replace the entire file contents with:

```python
"""Trades page — account-scoped manual trade entry and deletion."""
from __future__ import annotations

from datetime import date

import streamlit as st

import portfolio
from app_pages.shared import active_accounts, get_conn, price_context


def render() -> None:
    conn = get_conn()
    accounts = active_accounts()
    _t, _p, _r, last_prices = price_context()

    st.title("Trades")
    st.caption("Record buys and sells per account. Positions are derived from this ledger.")

    if not accounts:
        st.info("No accounts yet. Create one on the **Accounts** page first.")
        return

    acc_options = {a["name"]: a["id"] for a in accounts}
    selected_name = st.selectbox("Account", options=list(acc_options.keys()))
    account_id = acc_options[selected_name]

    with st.form("new_trade", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
        with c1:
            ticker = st.text_input("Ticker").strip().upper()
        with c2:
            side = st.selectbox("Side", ["BUY", "SELL"])
        with c3:
            qty = st.number_input("Qty", min_value=0.0001, value=1.0,
                                    step=1.0, format="%.4f")
        with c4:
            default_price = float(last_prices.get(ticker, 0.0)) if last_prices else 0.0
            price = st.number_input("Price", min_value=0.0,
                                      value=default_price, step=0.01, format="%.2f")
        c5, c6 = st.columns([1, 3])
        with c5:
            trade_date = st.date_input("Trade date", value=date.today())
        with c6:
            notes = st.text_input("Notes (optional)")
        submitted = st.form_submit_button("Add trade", type="primary")
        if submitted:
            if not ticker:
                st.error("Ticker is required.")
            else:
                try:
                    tid, was_new = portfolio.add_trade_to_account(
                        conn, account_id=account_id, ticker=ticker,
                        side=side, qty=qty, price=price,
                        trade_date=trade_date.isoformat(), notes=notes,
                    )
                    if was_new:
                        st.success(f"Recorded {side} {qty} {ticker} @ ${price:.2f}")
                    else:
                        st.info(f"Duplicate trade skipped (trade #{tid} already exists).")
                except ValueError as e:
                    st.error(str(e))

    st.subheader("Trade history")
    trades = portfolio.list_trades_df(conn, account_id=account_id)
    if trades.empty:
        st.info("No trades yet for this account.")
    else:
        st.dataframe(
            trades.drop(columns=["account_id"]),
            width="stretch", hide_index=True,
        )
        with st.expander("Delete a trade"):
            tid = st.number_input("Trade ID to delete", min_value=1, step=1)
            if st.button("Delete", type="secondary"):
                from catalysts import db as cdb
                cdb.delete_trade(conn, int(tid))
                st.rerun()
```

- [ ] **Step 8: Smoke test — imports + full suite**

Run: `.venv/Scripts/python -c "from app_pages import holdings, trades; print('ok')"`
Expected: `ok` (ignore Streamlit cache warnings).

Run: `.venv/Scripts/python -m pytest tests/ -q`
Expected: all 120 passing (110 + 5 new + 5 more).

- [ ] **Step 9: Commit**

```bash
git add portfolio.py app_pages/shared.py app_pages/holdings.py app_pages/trades.py tests/test_portfolio.py
git commit -m "feat(portfolios): rewrite portfolio.py for multi-account + UI wiring

portfolio.py now derives positions from the new multi-account trades
table (still avg-cost basis). positions_for_account(conn, account_id)
and positions_all_accounts(conn) are the two read paths.

Holdings page gains an Account selector with an 'All accounts' option
that produces per-(account, ticker) rows for attribution.

Trades page is scoped to one account at a time with a date picker
(previously auto-used now()). Duplicate trades are caught by dedup_key
and surfaced to the user as an info toast rather than a second INSERT.

Both pages guard against zero accounts with a nudge to the Accounts
page (which lands in the next task).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: CSV importer core + broker fixture files

**Files:**
- Create: `portfolios/__init__.py` (empty)
- Create: `portfolios/importer.py`
- Create: `tests/test_importer.py`
- Create: `tests/fixtures/broker_csvs/fidelity.csv`
- Create: `tests/fixtures/broker_csvs/schwab.csv`
- Create: `tests/fixtures/broker_csvs/robinhood.csv`
- Create: `tests/fixtures/broker_csvs/moomoo.csv`
- Create: `tests/fixtures/broker_csvs/vanguard.csv`

- [ ] **Step 1: Create fixture CSVs**

Create `tests/fixtures/broker_csvs/fidelity.csv`:

```csv
Run Date,Action,Symbol,Security Description,Quantity,Price,Amount
04/10/2026,YOU BOUGHT,NVDA,NVIDIA CORP,10,500.00,-5000.00
04/11/2026,YOU BOUGHT,AAPL,APPLE INC,5,200.00,-1000.00
04/12/2026,YOU SOLD,NVDA,NVIDIA CORP,4,520.00,2080.00
04/13/2026,DIVIDEND RECEIVED,AAPL,APPLE INC,0,0,2.50
```

Create `tests/fixtures/broker_csvs/schwab.csv`:

```csv
Date,Action,Symbol,Description,Quantity,Price,Amount
04/10/2026,Buy,NVDA,NVIDIA CORP,10,$500.00,-$5000.00
04/11/2026,Buy,AAPL,APPLE INC,5,$200.00,-$1000.00
04/12/2026,Sell,NVDA,NVIDIA CORP,4,$520.00,$2080.00
```

Create `tests/fixtures/broker_csvs/robinhood.csv`:

```csv
Activity Date,Process Date,Settle Date,Instrument,Description,Trans Code,Quantity,Price,Amount
4/10/2026,4/10/2026,4/11/2026,NVDA,NVIDIA CORP,Buy,10,$500.00,($5000.00)
4/11/2026,4/11/2026,4/12/2026,AAPL,APPLE INC,Buy,5,$200.00,($1000.00)
4/12/2026,4/12/2026,4/13/2026,NVDA,NVIDIA CORP,Sell,4,$520.00,$2080.00
```

Create `tests/fixtures/broker_csvs/moomoo.csv`:

```csv
Symbol,Direction,Quantity,Price,TradeTime
NVDA,BUY,10,500.00,2026-04-10 09:45:00
AAPL,BUY,5,200.00,2026-04-11 10:15:00
NVDA,SELL,4,520.00,2026-04-12 14:30:00
```

Create `tests/fixtures/broker_csvs/vanguard.csv`:

```csv
Account Number,Trade Date,Settlement Date,Transaction Type,Symbol,Quantity,Price,Principal Amount
12345678,04/10/2026,04/12/2026,Buy,NVDA,10,500.00,-5000.00
12345678,04/11/2026,04/13/2026,Buy,AAPL,5,200.00,-1000.00
12345678,04/12/2026,04/14/2026,Sell,NVDA,4,520.00,2080.00
```

- [ ] **Step 2: Write the failing importer tests**

Create `tests/test_importer.py`:

```python
from pathlib import Path
import pytest

from portfolios import importer


FIXTURES = Path(__file__).parent / "fixtures" / "broker_csvs"


# --- Normalization primitives ---

def test_normalize_ticker():
    assert importer.normalize_ticker(" nvda ") == "NVDA"


def test_normalize_ticker_rejects_empty():
    with pytest.raises(ValueError):
        importer.normalize_ticker("")


def test_normalize_price_strips_currency():
    assert importer.normalize_price("$500.00") == 500.0
    assert importer.normalize_price("$1,234.56") == 1234.56
    assert importer.normalize_price("($500.00)") == 500.0  # Robinhood neg-in-parens


def test_normalize_price_rejects_negative_result_when_not_paren():
    with pytest.raises(ValueError):
        importer.normalize_price("-50")


def test_normalize_qty_positive():
    assert importer.normalize_qty("10") == 10.0
    assert importer.normalize_qty("10.5") == 10.5


def test_normalize_qty_abs_value():
    assert importer.normalize_qty("-10") == 10.0  # some brokers sign by side


def test_normalize_qty_rejects_zero():
    with pytest.raises(ValueError):
        importer.normalize_qty("0")


def test_parse_date_iso():
    assert importer.parse_date("2026-04-10") == "2026-04-10"


def test_parse_date_us():
    assert importer.parse_date("04/10/2026") == "2026-04-10"


def test_parse_date_rejects_future():
    from datetime import date, timedelta
    future = (date.today() + timedelta(days=5)).strftime("%Y-%m-%d")
    with pytest.raises(ValueError):
        importer.parse_date(future)


# --- Profile application ---

def test_apply_profile_fidelity():
    profile = {
        "column_map": {
            "ticker": "Symbol", "side": "Action", "qty": "Quantity",
            "price": "Price", "trade_date": "Run Date",
        },
        "value_map": {
            "side": {"YOU BOUGHT": "BUY", "YOU SOLD": "SELL"},
        },
        "row_filter": {
            "skip_if_side_not_in_value_map": True,
        },
    }
    csv_text = (FIXTURES / "fidelity.csv").read_text()
    result = importer.apply_profile(csv_text, profile)
    assert len(result.valid) == 3  # dividend row filtered out
    assert result.valid[0].ticker == "NVDA"
    assert result.valid[0].side == "BUY"
    assert result.valid[0].qty == 10.0
    assert result.valid[0].price == 500.0
    assert result.valid[0].trade_date == "2026-04-10"
    assert result.valid[2].side == "SELL"


def test_apply_profile_schwab():
    profile = {
        "column_map": {
            "ticker": "Symbol", "side": "Action", "qty": "Quantity",
            "price": "Price", "trade_date": "Date",
        },
        "value_map": {"side": {"Buy": "BUY", "Sell": "SELL"}},
        "row_filter": {},
    }
    csv_text = (FIXTURES / "schwab.csv").read_text()
    result = importer.apply_profile(csv_text, profile)
    assert len(result.valid) == 3
    assert result.valid[0].price == 500.0  # stripped $


def test_apply_profile_robinhood():
    profile = {
        "column_map": {
            "ticker": "Instrument", "side": "Trans Code",
            "qty": "Quantity", "price": "Price",
            "trade_date": "Activity Date",
        },
        "value_map": {"side": {"Buy": "BUY", "Sell": "SELL"}},
        "row_filter": {},
    }
    csv_text = (FIXTURES / "robinhood.csv").read_text()
    result = importer.apply_profile(csv_text, profile)
    assert len(result.valid) == 3


def test_apply_profile_rejects_missing_column():
    profile = {
        "column_map": {
            "ticker": "DoesNotExist", "side": "Action", "qty": "Quantity",
            "price": "Price", "trade_date": "Date",
        },
        "value_map": {"side": {"Buy": "BUY", "Sell": "SELL"}},
        "row_filter": {},
    }
    csv_text = (FIXTURES / "schwab.csv").read_text()
    with pytest.raises(ValueError, match="DoesNotExist"):
        importer.apply_profile(csv_text, profile)


def test_apply_profile_rejects_untranslated_side():
    profile = {
        "column_map": {
            "ticker": "Symbol", "side": "Action", "qty": "Quantity",
            "price": "Price", "trade_date": "Run Date",
        },
        "value_map": {"side": {"YOU BOUGHT": "BUY"}},  # missing YOU SOLD
        "row_filter": {},  # no row filter → untranslated row rejected
    }
    csv_text = (FIXTURES / "fidelity.csv").read_text()
    result = importer.apply_profile(csv_text, profile)
    # 2 BUYs valid, 1 SELL rejected (dividend also rejected)
    assert len(result.valid) == 2
    assert len(result.rejected) == 2  # SELL + DIVIDEND
```

- [ ] **Step 3: Run tests — expect FAIL**

Run: `.venv/Scripts/python -m pytest tests/test_importer.py -v`
Expected: failures (`portfolios.importer` not importable).

- [ ] **Step 4: Create `portfolios/__init__.py`**

Create empty file `portfolios/__init__.py`.

- [ ] **Step 5: Implement `portfolios/importer.py`**

Create `portfolios/importer.py`:

```python
"""CSV → canonical trade rows via saved column-mapping profiles."""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


_REQUIRED_FIELDS = ("ticker", "side", "qty", "price", "trade_date")

_PAREN_NEG_RE = re.compile(r"^\(([\d,.]+)\)$")
_MONEY_RE = re.compile(r"[$,\s]")


@dataclass
class ImportRow:
    ticker: str
    side: str
    qty: float
    price: float
    trade_date: str
    raw: dict[str, Any]  # original CSV cells, for display/debug


@dataclass
class RejectedRow:
    raw: dict[str, Any]
    reason: str


@dataclass
class ImportResult:
    valid: list[ImportRow]
    rejected: list[RejectedRow]
    skipped: int  # count dropped by row_filter without being "rejected" (e.g. dividend lines)


# --- Normalization primitives ---

def normalize_ticker(value: Any) -> str:
    s = str(value or "").strip().upper()
    if not s:
        raise ValueError("empty ticker")
    return s


def normalize_qty(value: Any) -> float:
    if value is None or value == "":
        raise ValueError("empty qty")
    stripped = _MONEY_RE.sub("", str(value))
    try:
        q = abs(float(stripped))
    except ValueError as exc:
        raise ValueError(f"qty not numeric: {value!r}") from exc
    if q <= 0:
        raise ValueError(f"qty must be > 0: {value!r}")
    return q


def normalize_price(value: Any) -> float:
    if value is None or value == "":
        raise ValueError("empty price")
    s = str(value).strip()
    m = _PAREN_NEG_RE.match(s)
    if m:
        s = m.group(1)  # treat parens as magnitude (broker uses ($500) for "you paid $500")
    else:
        s = _MONEY_RE.sub("", s)
        if s.startswith("-"):
            raise ValueError(f"price must be >= 0: {value!r}")
    try:
        p = float(s)
    except ValueError as exc:
        raise ValueError(f"price not numeric: {value!r}") from exc
    if p < 0:
        raise ValueError(f"price must be >= 0: {value!r}")
    return p


def parse_date(value: Any) -> str:
    """Accept YYYY-MM-DD, MM/DD/YYYY, M/D/YYYY → ISO date; reject future dates."""
    if value is None or value == "":
        raise ValueError("empty date")
    s = str(value).strip()
    # Strip time portion if present (MooMoo: "2026-04-10 09:45:00")
    s = s.split(" ")[0].split("T")[0]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            d = datetime.strptime(s, fmt).date()
            break
        except ValueError:
            continue
    else:
        raise ValueError(f"unparseable date: {value!r}")
    if d > date.today():
        raise ValueError(f"trade_date is in the future: {value!r}")
    return d.isoformat()


# --- Profile application ---

def apply_profile(csv_text: str, profile: dict) -> ImportResult:
    """Parse CSV and normalize each row per `profile` (column_map / value_map / row_filter).

    Raises ValueError if a required column in `column_map` is absent from the
    CSV header (the whole file is unusable).

    Per-row errors go into `rejected`. Rows dropped by row_filter go into
    `skipped` count only (they're expected non-trade rows like dividends).
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    header = reader.fieldnames or []

    column_map = profile["column_map"]
    value_map = profile.get("value_map", {}) or {}
    row_filter = profile.get("row_filter", {}) or {}

    for canonical, source in column_map.items():
        if source not in header:
            raise ValueError(f"CSV missing expected column {source!r} "
                              f"(mapped from canonical {canonical!r})")

    side_map = value_map.get("side", {}) or {}
    skip_not_in_side_map = bool(row_filter.get("skip_if_side_not_in_value_map"))
    skip_action_contains = [s.upper() for s in
                             row_filter.get("skip_if_action_contains", []) or []]

    valid: list[ImportRow] = []
    rejected: list[RejectedRow] = []
    skipped = 0

    for raw in reader:
        # row_filter: action-contains short-circuit (e.g. "DIV", "INT", "TRANSFER")
        if skip_action_contains:
            action_cell = raw.get(column_map["side"], "") or ""
            upper = action_cell.upper()
            if any(tok in upper for tok in skip_action_contains):
                skipped += 1
                continue

        try:
            raw_side = raw.get(column_map["side"], "") or ""
            if side_map:
                if raw_side in side_map:
                    side = side_map[raw_side]
                elif skip_not_in_side_map:
                    skipped += 1
                    continue
                else:
                    raise ValueError(f"side value not in value_map: {raw_side!r}")
            else:
                side = raw_side.strip().upper()
                if side not in ("BUY", "SELL"):
                    raise ValueError(f"side must be BUY or SELL, got: {raw_side!r}")

            row = ImportRow(
                ticker=normalize_ticker(raw.get(column_map["ticker"])),
                side=side,
                qty=normalize_qty(raw.get(column_map["qty"])),
                price=normalize_price(raw.get(column_map["price"])),
                trade_date=parse_date(raw.get(column_map["trade_date"])),
                raw=raw,
            )
            valid.append(row)
        except ValueError as exc:
            rejected.append(RejectedRow(raw=raw, reason=str(exc)))

    return ImportResult(valid=valid, rejected=rejected, skipped=skipped)


def commit_to_db(conn, account_id: int, rows: list[ImportRow],
                 profile_id: int | None, filename: str,
                 rejected_count: int = 0) -> dict:
    """Insert rows into trades table with dedup + record an import_batch.
    Returns {inserted, duplicates}."""
    from catalysts import db as cdb
    inserted = 0
    duplicates = 0
    # Create the batch first so we can stamp it on trades
    batch_id = cdb.create_import_batch(
        conn, account_id=account_id, profile_id=profile_id,
        filename=filename, row_count=len(rows) + rejected_count,
        inserted=0, duplicates=0, rejected=rejected_count,
    )
    for r in rows:
        _tid, was_new = cdb.insert_trade(
            conn, account_id=account_id, ticker=r.ticker, side=r.side,
            qty=r.qty, price=r.price, trade_date=r.trade_date,
            import_batch_id=batch_id,
        )
        if was_new:
            inserted += 1
        else:
            duplicates += 1
    # Update the batch with final counts
    conn.execute(
        "UPDATE import_batches SET inserted=?, duplicates=? WHERE id=?",
        (inserted, duplicates, batch_id),
    )
    conn.commit()
    return {"batch_id": batch_id, "inserted": inserted, "duplicates": duplicates}
```

- [ ] **Step 6: Run tests — expect PASS**

Run: `.venv/Scripts/python -m pytest tests/test_importer.py -v`
Expected: all passing.

- [ ] **Step 7: Full suite**

Run: `.venv/Scripts/python -m pytest tests/ -q`
Expected: 134 passing (120 + 14 new).

- [ ] **Step 8: Commit**

```bash
git add portfolios/__init__.py portfolios/importer.py tests/test_importer.py tests/fixtures/broker_csvs/
git commit -m "feat(portfolios): CSV importer core + broker fixture CSVs

Normalization primitives (normalize_ticker/qty/price, parse_date) handle
Robinhood's paren-negatives, Schwab's \$1,234.56 money strings, MooMoo's
ISO datetime strings with time portion, and future-date rejection.

apply_profile() applies column_map/value_map/row_filter to raw CSV text
and returns (valid, rejected, skipped). Missing required column in the
header raises outright (whole-file unusable); per-row errors go to
rejected; rows matched by row_filter just bump skipped count.

commit_to_db() writes the valid rows through trades.dedup_key and
creates an import_batches audit row in the same transaction.

Five hand-crafted broker fixture CSVs (Fidelity, Schwab, Robinhood,
MooMoo, Vanguard) exercise the documented quirks of each.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Seed builtin import profiles + profiles.py

**Files:**
- Create: `portfolios/profiles.py`
- Modify: `catalysts/db.py::migrate()` — seed builtin profiles if missing
- Create: `tests/test_profiles.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_profiles.py`:

```python
from pathlib import Path
import json

from catalysts import db as cdb
from portfolios import importer, profiles


FIXTURES = Path(__file__).parent / "fixtures" / "broker_csvs"


def test_migrate_seeds_five_builtin_profiles(tmp_db):
    cdb.migrate(tmp_db)
    rows = tmp_db.execute(
        "SELECT name, broker, builtin FROM import_profiles ORDER BY name"
    ).fetchall()
    names = [r["name"] for r in rows]
    assert "Fidelity" in names
    assert "Schwab" in names
    assert "Robinhood" in names
    assert "MooMoo" in names
    assert "Vanguard" in names
    for r in rows:
        if r["name"] in ("Fidelity", "Schwab", "Robinhood", "MooMoo", "Vanguard"):
            assert r["builtin"] == 1


def test_migrate_is_idempotent_for_profiles(tmp_db):
    cdb.migrate(tmp_db)
    cdb.migrate(tmp_db)
    n = tmp_db.execute(
        "SELECT COUNT(*) FROM import_profiles WHERE builtin=1"
    ).fetchone()[0]
    assert n == 5


def test_fidelity_profile_parses_fixture(tmp_db):
    cdb.migrate(tmp_db)
    row = tmp_db.execute(
        "SELECT * FROM import_profiles WHERE name='Fidelity'"
    ).fetchone()
    profile = {
        "column_map": json.loads(row["column_map"]),
        "value_map":  json.loads(row["value_map"]),
        "row_filter": json.loads(row["row_filter"]),
    }
    csv_text = (FIXTURES / "fidelity.csv").read_text()
    result = importer.apply_profile(csv_text, profile)
    assert len(result.valid) == 3
    tickers = [r.ticker for r in result.valid]
    assert tickers == ["NVDA", "AAPL", "NVDA"]


def test_schwab_profile_parses_fixture(tmp_db):
    cdb.migrate(tmp_db)
    row = tmp_db.execute(
        "SELECT * FROM import_profiles WHERE name='Schwab'"
    ).fetchone()
    profile = {
        "column_map": json.loads(row["column_map"]),
        "value_map":  json.loads(row["value_map"]),
        "row_filter": json.loads(row["row_filter"]),
    }
    csv_text = (FIXTURES / "schwab.csv").read_text()
    result = importer.apply_profile(csv_text, profile)
    assert len(result.valid) == 3


def test_robinhood_profile_parses_fixture(tmp_db):
    cdb.migrate(tmp_db)
    row = tmp_db.execute(
        "SELECT * FROM import_profiles WHERE name='Robinhood'"
    ).fetchone()
    profile = {
        "column_map": json.loads(row["column_map"]),
        "value_map":  json.loads(row["value_map"]),
        "row_filter": json.loads(row["row_filter"]),
    }
    csv_text = (FIXTURES / "robinhood.csv").read_text()
    result = importer.apply_profile(csv_text, profile)
    assert len(result.valid) == 3


def test_moomoo_profile_parses_fixture(tmp_db):
    cdb.migrate(tmp_db)
    row = tmp_db.execute(
        "SELECT * FROM import_profiles WHERE name='MooMoo'"
    ).fetchone()
    profile = {
        "column_map": json.loads(row["column_map"]),
        "value_map":  json.loads(row["value_map"]),
        "row_filter": json.loads(row["row_filter"]),
    }
    csv_text = (FIXTURES / "moomoo.csv").read_text()
    result = importer.apply_profile(csv_text, profile)
    assert len(result.valid) == 3


def test_vanguard_profile_parses_fixture(tmp_db):
    cdb.migrate(tmp_db)
    row = tmp_db.execute(
        "SELECT * FROM import_profiles WHERE name='Vanguard'"
    ).fetchone()
    profile = {
        "column_map": json.loads(row["column_map"]),
        "value_map":  json.loads(row["value_map"]),
        "row_filter": json.loads(row["row_filter"]),
    }
    csv_text = (FIXTURES / "vanguard.csv").read_text()
    result = importer.apply_profile(csv_text, profile)
    assert len(result.valid) == 3


def test_user_profile_has_builtin_zero(tmp_db):
    cdb.migrate(tmp_db)
    pid = profiles.create_user_profile(
        tmp_db, name="My Custom", broker="other",
        column_map={"ticker": "Sym", "side": "Dir", "qty": "Q",
                     "price": "P", "trade_date": "When"},
        value_map={"side": {"B": "BUY", "S": "SELL"}},
        row_filter={},
    )
    row = tmp_db.execute(
        "SELECT builtin FROM import_profiles WHERE id=?", (pid,)
    ).fetchone()
    assert row["builtin"] == 0
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `.venv/Scripts/python -m pytest tests/test_profiles.py -v`
Expected: failures (no seed + `create_user_profile` missing).

- [ ] **Step 3: Create `portfolios/profiles.py`**

Create `portfolios/profiles.py`:

```python
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
```

- [ ] **Step 4: Wire the seeder into `catalysts/db.py::migrate()`**

In `catalysts/db.py`, find the existing `def migrate(conn)` function. Replace its body with:

```python
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
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `.venv/Scripts/python -m pytest tests/test_profiles.py -v`
Expected: all passing.

- [ ] **Step 6: Full suite**

Run: `.venv/Scripts/python -m pytest tests/ -q`
Expected: 142 passing (134 + 8 new).

- [ ] **Step 7: Commit**

```bash
git add portfolios/profiles.py catalysts/db.py tests/test_profiles.py
git commit -m "feat(portfolios): seed 5 built-in import profiles on migrate

Fidelity, Schwab, Robinhood, MooMoo, Vanguard each ship with a
column_map/value_map/row_filter that parses its documented CSV format.
seed_builtin_profiles() uses INSERT OR IGNORE so re-running migrate is
safe and user-edited profiles (builtin=0) are never stomped.

load_profiles() and create_user_profile() cover the read + user-add
paths the Import UI will need.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Accounts page UI

**Files:**
- Create: `app_pages/accounts.py`
- Modify: `app.py` — register the new page in st.navigation

- [ ] **Step 1: Create `app_pages/accounts.py`**

```python
"""Accounts page — CRUD + per-account drilldown."""
from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.express as px
import streamlit as st

import portfolio
from catalysts import db as cdb
from tickers import NAMES

from app_pages.shared import (
    active_accounts, fmt_money, get_conn, price_context,
)

_TYPES = ["taxable", "roth", "traditional", "401k", "hsa", "joint", "other"]
_BROKERS = ["fidelity", "schwab", "robinhood", "moomoo", "vanguard", "other"]


def _rollup(conn, accounts: list[dict], last_prices: dict) -> dict:
    total_mv = 0.0
    total_unr = 0.0
    total_rea = 0.0
    total_positions = 0
    for a in accounts:
        df = portfolio.positions_for_account(conn, a["id"], last_prices)
        if df.empty:
            continue
        total_mv  += float(df["market_value"].sum())
        total_unr += float(df["unrealized_pl"].sum())
        total_rea += float(df["realized_pl"].sum())
        total_positions += len(df)
    return {
        "account_count": len(accounts),
        "positions": total_positions,
        "market_value": total_mv,
        "unrealized_pl": total_unr,
        "realized_pl": total_rea,
        "pending_events": cdb.pending_event_count(conn),
    }


def _account_row(conn, a: dict, last_prices: dict) -> dict:
    df = portfolio.positions_for_account(conn, a["id"], last_prices)
    pending = conn.execute(
        "SELECT COUNT(*) FROM events WHERE account_id=? AND status='pending'",
        (a["id"],),
    ).fetchone()[0]
    return {
        "Account":    a["name"],
        "Type":       a["type"],
        "Broker":     a["broker"],
        "Opened":     a["opened_date"],
        "Positions":  len(df),
        "Market":     float(df["market_value"].sum()) if not df.empty else 0.0,
        "Unrealized": float(df["unrealized_pl"].sum()) if not df.empty else 0.0,
        "Realized":   float(df["realized_pl"].sum()) if not df.empty else 0.0,
        "Pending":    pending,
        "_id":        a["id"],
    }


def render() -> None:
    conn = get_conn()
    _t, _p, _r, last_prices = price_context()

    st.title("Accounts")
    st.caption("Your investment accounts, aggregated positions, and drilldown.")

    accounts = active_accounts()

    if not accounts:
        st.info("No accounts yet — create one below to get started.")
    else:
        roll = _rollup(conn, accounts, last_prices)
        cols = st.columns(6)
        cols[0].metric("Accounts",    roll["account_count"])
        cols[1].metric("Positions",   roll["positions"])
        cols[2].metric("Market",      fmt_money(roll["market_value"]))
        cols[3].metric("Unrealized",  fmt_money(roll["unrealized_pl"]))
        cols[4].metric("Realized",    fmt_money(roll["realized_pl"]))
        cols[5].metric("Pending events", roll["pending_events"])

        table = pd.DataFrame([_account_row(conn, a, last_prices) for a in accounts])
        st.dataframe(
            table.drop(columns=["_id"]).style.format({
                "Market": "${:,.2f}", "Unrealized": "${:,.2f}",
                "Realized": "${:,.2f}",
            }),
            width="stretch", hide_index=True,
        )

        st.subheader("Drilldown")
        pick = st.selectbox(
            "Account to drill into",
            options=[a["id"] for a in accounts],
            format_func=lambda i: next(a["name"] for a in accounts if a["id"] == i),
        )
        _render_drilldown(conn, pick, last_prices)

    st.subheader("Create account")
    _render_create_form(conn)

    if accounts:
        st.subheader("Deactivate account")
        rm_id = st.selectbox(
            "Account to deactivate",
            options=[a["id"] for a in accounts],
            format_func=lambda i: next(a["name"] for a in accounts if a["id"] == i),
            key="deactivate_pick",
        )
        if st.button("Deactivate", type="secondary"):
            cdb.deactivate_account(conn, rm_id)
            st.success("Account deactivated. Its trades and events are preserved.")
            st.rerun()


def _render_drilldown(conn, account_id: int, last_prices: dict) -> None:
    df = portfolio.positions_for_account(conn, account_id, last_prices)
    if df.empty:
        st.info("No positions in this account yet. Use Import or Trades to add some.")
    else:
        view = df.copy()
        view.insert(1, "name", view["ticker"].map(NAMES).fillna(""))
        view = view.rename(columns={
            "ticker": "Ticker", "name": "Name", "qty": "Qty",
            "avg_cost": "Avg cost", "last": "Last",
            "market_value": "Market", "unrealized_pl": "Unrealized",
            "realized_pl": "Realized", "total_pl": "Total",
        })
        st.dataframe(
            view.style.format({
                "Qty": "{:,.4f}", "Avg cost": "${:,.2f}", "Last": "${:,.2f}",
                "Market": "${:,.2f}", "Unrealized": "${:,.2f}",
                "Realized": "${:,.2f}", "Total": "${:,.2f}",
            }),
            width="stretch", hide_index=True,
        )
        mv = float(view["Market"].sum())
        if mv > 0:
            fig = px.pie(view, values="Market", names="Ticker", hole=0.45,
                          title="Allocation")
            st.plotly_chart(fig, width="stretch")

    events = cdb.load_events(conn, account_id=account_id)
    if events:
        ev_df = pd.DataFrame(events)[[
            "event_date", "ticker", "move_window", "move_pct",
            "pnl_dollars", "catalyst_type", "status",
        ]].rename(columns={
            "event_date": "Date", "ticker": "Ticker",
            "move_window": "Window", "move_pct": "Move %",
            "pnl_dollars": "P&L $", "catalyst_type": "Tag",
            "status": "Status",
        })
        st.caption("Events on this account")
        st.dataframe(
            ev_df.style.format({"Move %": "{:+.2f}%", "P&L $": "${:,.2f}"}),
            width="stretch", hide_index=True,
        )


def _render_create_form(conn) -> None:
    with st.form("create_account", clear_on_submit=True):
        c1, c2, c3 = st.columns([2, 1, 1])
        with c1:
            name = st.text_input("Name", placeholder="Main Roth / Kid's UTMA / etc.").strip()
        with c2:
            acc_type = st.selectbox("Type", options=_TYPES)
        with c3:
            broker = st.selectbox("Broker", options=_BROKERS)
        c4, c5 = st.columns([1, 1])
        with c4:
            opened = st.date_input("Opened", value=date.today())
        with c5:
            initial_cash = st.number_input(
                "Initial cash ($)", min_value=0.0, value=0.0, step=100.0,
            )
        with st.expander("Advanced — event thresholds"):
            c6, c7 = st.columns(2)
            with c6:
                daily = st.number_input(
                    "Daily move % (blank = 5.0 default)",
                    min_value=0.0, max_value=50.0, value=0.0, step=0.5,
                )
            with c7:
                five = st.number_input(
                    "5-day move % (blank = 10.0 default)",
                    min_value=0.0, max_value=100.0, value=0.0, step=0.5,
                )

        ok = st.form_submit_button("Create account", type="primary")
        if ok:
            if not name:
                st.error("Name is required.")
                return
            try:
                cdb.create_account(
                    conn, name=name, type=acc_type, broker=broker,
                    opened_date=opened.isoformat(), initial_cash=initial_cash,
                    event_daily_pct=(daily or None),
                    event_5day_pct=(five or None),
                )
                st.success(f"Created {name}")
                st.rerun()
            except Exception as exc:
                st.error(f"Create failed: {exc}")
```

- [ ] **Step 2: Register the page in `app.py`**

In `app.py`, find the imports block:

```python
from app_pages import (
    catalysts_page,
    dashboard,
    holdings,
    ipo_tracker,
    options_pulse,
    performance,
    power_gauge,
    trades,
    universe,
)
```

Add `accounts` to the import list:

```python
from app_pages import (
    accounts,
    catalysts_page,
    dashboard,
    holdings,
    ipo_tracker,
    options_pulse,
    performance,
    power_gauge,
    trades,
    universe,
)
```

Then find the `_nav = st.navigation([...])` block and insert the Accounts page before Power Gauge:

```python
_nav = st.navigation([
    st.Page(dashboard.render,       title="Dashboard",     icon="📊",
            url_path="dashboard", default=True),
    st.Page(catalysts_page.render,  title="Catalysts",     icon="📰",
            url_path="catalysts"),
    st.Page(options_pulse.render,   title="Options Pulse", icon="📈",
            url_path="options-pulse"),
    st.Page(accounts.render,        title="Accounts",      icon="🏦",
            url_path="accounts"),
    st.Page(power_gauge.render,     title="Power Gauge",   icon="⚡",
            url_path="power-gauge"),
    st.Page(holdings.render,        title="Holdings",      icon="💼",
            url_path="holdings"),
    st.Page(trades.render,          title="Trades",        icon="🧾",
            url_path="trades"),
    st.Page(performance.render,     title="Performance",   icon="📉",
            url_path="performance"),
    st.Page(ipo_tracker.render,     title="IPO Tracker",   icon="🆕",
            url_path="ipo-tracker"),
    st.Page(universe.render,        title="Universe",      icon="🌐",
            url_path="universe"),
])
```

- [ ] **Step 3: Smoke test imports**

Run: `.venv/Scripts/python -c "from app_pages import accounts; print('ok')"`
Expected: `ok`.

Run: `.venv/Scripts/python -m py_compile app.py && echo "app.py compiles"`
Expected: `app.py compiles`.

Run: `.venv/Scripts/python -m pytest tests/ -q`
Expected: 142 passing (no new tests; this is UI-only).

- [ ] **Step 4: Commit**

```bash
git add app_pages/accounts.py app.py
git commit -m "feat(portfolios): Accounts page UI with CRUD + drilldown

Top-of-page rollup across all accounts: count, positions, market value,
unrealized, realized, pending events badge.

Per-account row summary, per-account drilldown panel (positions table +
allocation pie + events timeline).

Create-account form with type/broker dropdowns, opened date, optional
initial_cash, and an Advanced expander for per-account event thresholds.

Deactivate action preserves historical trades/events (FK CASCADE would
delete them — we use active=0 flag instead for audit).

Page registered in app.py navigation between Options Pulse and Power
Gauge with 🏦 icon.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Import page UI (3-step wizard)

**Files:**
- Create: `app_pages/import_trades.py`
- Modify: `app.py` — register the page

- [ ] **Step 1: Create `app_pages/import_trades.py`**

```python
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
    """Count how many of these rows would hit the dedup_key UNIQUE on insert."""
    if not rows:
        return 0
    keys = [
        f"{account_id}|{r.ticker}|{r.side}|{r.qty:.6f}|{r.price:.6f}|{r.trade_date}"
        for r in rows
    ]
    placeholders = ",".join(["?"] * len(keys))
    return conn.execute(
        f"SELECT COUNT(*) FROM trades WHERE dedup_key IN ({placeholders})",
        keys,
    ).fetchone()[0]


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
```

- [ ] **Step 2: Register page in `app.py`**

In `app.py`, add `import_trades` to the `from app_pages import` block (alphabetical).

Insert the page into `_nav` between `accounts` and `power_gauge`:

```python
    st.Page(import_trades.render,   title="Import",        icon="⬆",
            url_path="import"),
```

Full updated `_nav` block:

```python
_nav = st.navigation([
    st.Page(dashboard.render,       title="Dashboard",     icon="📊",
            url_path="dashboard", default=True),
    st.Page(catalysts_page.render,  title="Catalysts",     icon="📰",
            url_path="catalysts"),
    st.Page(options_pulse.render,   title="Options Pulse", icon="📈",
            url_path="options-pulse"),
    st.Page(accounts.render,        title="Accounts",      icon="🏦",
            url_path="accounts"),
    st.Page(import_trades.render,   title="Import",        icon="⬆",
            url_path="import"),
    st.Page(power_gauge.render,     title="Power Gauge",   icon="⚡",
            url_path="power-gauge"),
    st.Page(holdings.render,        title="Holdings",      icon="💼",
            url_path="holdings"),
    st.Page(trades.render,          title="Trades",        icon="🧾",
            url_path="trades"),
    st.Page(performance.render,     title="Performance",   icon="📉",
            url_path="performance"),
    st.Page(ipo_tracker.render,     title="IPO Tracker",   icon="🆕",
            url_path="ipo-tracker"),
    st.Page(universe.render,        title="Universe",      icon="🌐",
            url_path="universe"),
])
```

- [ ] **Step 3: Smoke test**

Run: `.venv/Scripts/python -c "from app_pages import import_trades; print('ok')"`
Expected: `ok`.

Run: `.venv/Scripts/python -m py_compile app.py && echo ok`
Expected: `ok`.

Run: `.venv/Scripts/python -m pytest tests/ -q`
Expected: 142 passing.

- [ ] **Step 4: Commit**

```bash
git add app_pages/import_trades.py app.py
git commit -m "feat(portfolios): Import page with auto-match + column-mapping wizard

On upload, the importer auto-matches a saved profile if the CSV header
contains all of that profile's source columns. Otherwise the user picks
from the profile dropdown or falls back to the Custom mapping wizard.

The wizard has five selectboxes (ticker/side/qty/price/trade_date), a
text area for the side value_map (one 'CSV = BUY' pair per line), and a
checkbox for 'skip untranslated action rows'. A 'Save profile' expander
persists the mapping as builtin=0 for next time.

Preview shows per-row Valid / Rejected / Skipped-by-filter / Already-in-DB
counts. Commit runs everything in one transaction via
importer.commit_to_db() and stamps an import_batches audit row.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Event detector + poller hook

**Files:**
- Create: `portfolios/events.py`
- Modify: `catalyst_poller.py` — tail hook
- Create: `tests/test_events.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_events.py`:

```python
from unittest.mock import patch, MagicMock

from catalysts import db as cdb, polygon_client as pc
from portfolios import events


def _make_bars(closes: list[float], start_date="2026-04-07"):
    """Daily bars with monotonic dates; 't' is epoch-ms at 21:00 UTC."""
    from datetime import date, datetime, timedelta, timezone
    d0 = date.fromisoformat(start_date)
    bars = []
    for i, c in enumerate(closes):
        d = d0 + timedelta(days=i)
        ts = int(datetime(d.year, d.month, d.day, 21, 0, 0,
                            tzinfo=timezone.utc).timestamp() * 1000)
        bars.append({"t": ts, "o": c, "h": c, "l": c, "c": c, "v": 1_000_000})
    return bars


def _mock_bars_response(bars):
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {}
    resp.json.return_value = {"results": bars}
    resp.raise_for_status = MagicMock()
    return resp


def _seed_account_with_position(tmp_db, ticker="NVDA", qty=10.0, price=500.0,
                                  trade_date="2026-04-01"):
    acc = cdb.create_account(tmp_db, name="A", type="taxable",
                               broker="fidelity", opened_date="2024-01-01")
    cdb.insert_trade(tmp_db, account_id=acc, ticker=ticker, side="BUY",
                      qty=qty, price=price, trade_date=trade_date)
    return acc


def test_daily_move_over_threshold_fires(tmp_db, monkeypatch):
    cdb.migrate(tmp_db)
    pc.reset_bucket_for_tests()
    acc = _seed_account_with_position(tmp_db)
    # 6% daily bar-to-bar move on the latest bar
    bars = _make_bars([100, 100, 100, 100, 100, 100, 106])
    monkeypatch.setattr(events, "_today_et", lambda: bars[-1]["t"] // 86400_000 + 1)
    with patch.object(pc.requests, "get", return_value=_mock_bars_response(bars)):
        n = events.detect_events_for_all_accounts(tmp_db)
    assert n >= 1
    rows = cdb.load_events(tmp_db, account_id=acc)
    daily = [r for r in rows if r["move_window"] == "1d"]
    assert len(daily) == 1
    assert abs(daily[0]["move_pct"] - 6.0) < 0.01


def test_daily_move_under_threshold_does_not_fire(tmp_db, monkeypatch):
    cdb.migrate(tmp_db)
    pc.reset_bucket_for_tests()
    acc = _seed_account_with_position(tmp_db)
    bars = _make_bars([100, 100, 100, 100, 100, 100, 104])  # 4% daily
    monkeypatch.setattr(events, "_today_et", lambda: bars[-1]["t"] // 86400_000 + 1)
    with patch.object(pc.requests, "get", return_value=_mock_bars_response(bars)):
        events.detect_events_for_all_accounts(tmp_db)
    rows = cdb.load_events(tmp_db, account_id=acc)
    assert [r for r in rows if r["move_window"] == "1d"] == []


def test_5day_move_over_threshold_fires(tmp_db, monkeypatch):
    cdb.migrate(tmp_db)
    pc.reset_bucket_for_tests()
    acc = _seed_account_with_position(tmp_db)
    # 12% 5-day move, 2% daily (under daily threshold)
    bars = _make_bars([100, 99, 100, 101, 102, 100, 112])
    monkeypatch.setattr(events, "_today_et", lambda: bars[-1]["t"] // 86400_000 + 1)
    with patch.object(pc.requests, "get", return_value=_mock_bars_response(bars)):
        events.detect_events_for_all_accounts(tmp_db)
    rows = cdb.load_events(tmp_db, account_id=acc)
    five_day = [r for r in rows if r["move_window"] == "5d"]
    assert len(five_day) == 1


def test_detection_is_idempotent(tmp_db, monkeypatch):
    cdb.migrate(tmp_db)
    pc.reset_bucket_for_tests()
    acc = _seed_account_with_position(tmp_db)
    bars = _make_bars([100, 100, 100, 100, 100, 100, 108])
    monkeypatch.setattr(events, "_today_et", lambda: bars[-1]["t"] // 86400_000 + 1)
    with patch.object(pc.requests, "get", return_value=_mock_bars_response(bars)):
        events.detect_events_for_all_accounts(tmp_db)
        events.detect_events_for_all_accounts(tmp_db)
    rows = cdb.load_events(tmp_db, account_id=acc)
    assert len(rows) == 1  # unchanged on re-scan


def test_per_account_threshold_override(tmp_db, monkeypatch):
    cdb.migrate(tmp_db)
    pc.reset_bucket_for_tests()
    a = cdb.create_account(tmp_db, name="A", type="taxable", broker="x",
                             opened_date="2024-01-01", event_daily_pct=3.0)
    b = cdb.create_account(tmp_db, name="B", type="taxable", broker="x",
                             opened_date="2024-01-01")  # uses default 5.0
    cdb.insert_trade(tmp_db, account_id=a, ticker="NVDA", side="BUY",
                      qty=1, price=100, trade_date="2026-04-01")
    cdb.insert_trade(tmp_db, account_id=b, ticker="NVDA", side="BUY",
                      qty=1, price=100, trade_date="2026-04-01")
    bars = _make_bars([100, 100, 100, 100, 100, 100, 104])  # 4% daily
    monkeypatch.setattr(events, "_today_et", lambda: bars[-1]["t"] // 86400_000 + 1)
    with patch.object(pc.requests, "get", return_value=_mock_bars_response(bars)):
        events.detect_events_for_all_accounts(tmp_db)
    rows_a = cdb.load_events(tmp_db, account_id=a)
    rows_b = cdb.load_events(tmp_db, account_id=b)
    assert len(rows_a) == 1  # 4% >= 3% override
    assert len(rows_b) == 0  # 4% < 5% default


def test_auto_link_to_nearby_catalyst(tmp_db, monkeypatch):
    cdb.migrate(tmp_db)
    pc.reset_bucket_for_tests()
    acc = _seed_account_with_position(tmp_db)
    # seed a catalyst on NVDA dated +2 days from event
    tmp_db.execute(
        "INSERT INTO catalysts(ticker,source,source_id,headline,url,"
        "published_at,kw_score,final_score,seen,fetched_at) "
        "VALUES(?,?,?,?,?,?,?,?,0,?)",
        ("NVDA", "edgar", "edgar:NVDA:accX", "NVDA guides up",
         "https://x", "2026-04-11T10:00:00+00:00", 60, 85,
         "2026-04-11T10:00:00+00:00"),
    )
    tmp_db.commit()
    bars = _make_bars([100, 100, 100, 100, 100, 100, 108])  # event on bar[6]
    # bars[-1] date is 2026-04-13; catalyst at 2026-04-11 is within ±3 days
    monkeypatch.setattr(events, "_today_et", lambda: bars[-1]["t"] // 86400_000 + 1)
    with patch.object(pc.requests, "get", return_value=_mock_bars_response(bars)):
        events.detect_events_for_all_accounts(tmp_db)
    rows = cdb.load_events(tmp_db, account_id=acc)
    assert rows[0]["catalyst_id"] is not None


def test_auto_link_does_not_cross_tickers(tmp_db, monkeypatch):
    cdb.migrate(tmp_db)
    pc.reset_bucket_for_tests()
    acc = _seed_account_with_position(tmp_db, ticker="NVDA")
    # catalyst is on AAPL not NVDA
    tmp_db.execute(
        "INSERT INTO catalysts(ticker,source,source_id,headline,url,"
        "published_at,kw_score,final_score,seen,fetched_at) "
        "VALUES('AAPL','edgar','x','x','x','2026-04-12T10:00:00+00:00',60,85,0,'2026-04-12T10:00:00+00:00')"
    )
    tmp_db.commit()
    bars = _make_bars([100, 100, 100, 100, 100, 100, 108])
    monkeypatch.setattr(events, "_today_et", lambda: bars[-1]["t"] // 86400_000 + 1)
    with patch.object(pc.requests, "get", return_value=_mock_bars_response(bars)):
        events.detect_events_for_all_accounts(tmp_db)
    rows = cdb.load_events(tmp_db, account_id=acc)
    assert rows[0]["catalyst_id"] is None


def test_auto_link_window_respects_3_day_limit(tmp_db, monkeypatch):
    cdb.migrate(tmp_db)
    pc.reset_bucket_for_tests()
    acc = _seed_account_with_position(tmp_db)
    # catalyst 5 days before event - outside window
    tmp_db.execute(
        "INSERT INTO catalysts(ticker,source,source_id,headline,url,"
        "published_at,kw_score,final_score,seen,fetched_at) "
        "VALUES('NVDA','edgar','x','x','x','2026-04-08T10:00:00+00:00',60,85,0,'x')"
    )
    tmp_db.commit()
    bars = _make_bars([100, 100, 100, 100, 100, 100, 108])  # event on 2026-04-13
    monkeypatch.setattr(events, "_today_et", lambda: bars[-1]["t"] // 86400_000 + 1)
    with patch.object(pc.requests, "get", return_value=_mock_bars_response(bars)):
        events.detect_events_for_all_accounts(tmp_db)
    rows = cdb.load_events(tmp_db, account_id=acc)
    assert rows[0]["catalyst_id"] is None


def test_dismissed_status_survives_rescan(tmp_db, monkeypatch):
    cdb.migrate(tmp_db)
    pc.reset_bucket_for_tests()
    acc = _seed_account_with_position(tmp_db)
    bars = _make_bars([100, 100, 100, 100, 100, 100, 108])
    monkeypatch.setattr(events, "_today_et", lambda: bars[-1]["t"] // 86400_000 + 1)
    with patch.object(pc.requests, "get", return_value=_mock_bars_response(bars)):
        events.detect_events_for_all_accounts(tmp_db)
    rows = cdb.load_events(tmp_db, account_id=acc)
    cdb.update_event(tmp_db, rows[0]["id"], status="dismissed")
    with patch.object(pc.requests, "get", return_value=_mock_bars_response(bars)):
        events.detect_events_for_all_accounts(tmp_db)
    rows = cdb.load_events(tmp_db, account_id=acc)
    assert len(rows) == 1
    assert rows[0]["status"] == "dismissed"  # not recreated as pending
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `.venv/Scripts/python -m pytest tests/test_events.py -v`
Expected: failures (module missing).

- [ ] **Step 3: Create `portfolios/events.py`**

```python
"""Position event detection — called from catalyst_poller tail.

Scans each account's current positions, fetches daily bars for each held
ticker, computes 1d and 5d moves, writes events whose |move| >= threshold.
Then auto-links to a nearby catalyst if one exists within ±3 days on the
same ticker.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date

from catalysts import db as cdb
from catalysts import polygon_client

log = logging.getLogger("portfolios.events")

DEFAULT_DAILY_PCT = 5.0
DEFAULT_5DAY_PCT = 10.0
CATALYST_LINK_WINDOW_DAYS = 3
CATALYST_MIN_LINK_SCORE = 30


def _today_et() -> int:
    """Return today's date as int yyyymmdd in US/Eastern. Overridable in tests."""
    from zoneinfo import ZoneInfo
    from datetime import datetime
    d = datetime.now(ZoneInfo("America/New_York")).date()
    return d.year * 10000 + d.month * 100 + d.day


def _positions_qty_by_account(conn) -> dict[int, dict[str, float]]:
    """For every active account, return {ticker: qty_held_today}."""
    out: dict[int, dict[str, float]] = {}
    for a in cdb.load_accounts(conn):
        positions: dict[str, float] = defaultdict(float)
        for t in cdb.load_trades(conn, account_id=a["id"]):
            if t["side"] == "BUY":
                positions[t["ticker"]] += t["qty"]
            else:
                positions[t["ticker"]] -= t["qty"]
        # Filter to actual longs
        held = {tk: q for tk, q in positions.items() if q > 1e-9}
        if held:
            out[a["id"]] = held
    return out


def _fetch_bars(ticker: str, days_back: int = 8) -> list[dict] | None:
    """Fetch last ~8 daily bars from Polygon (covers 5d window + weekends)."""
    from datetime import timedelta
    end = date.today()
    start = end - timedelta(days=days_back + 7)  # extra slack for holidays
    body = polygon_client.get(
        f"/v2/aggs/ticker/{ticker}/range/1/day/{start.isoformat()}/{end.isoformat()}",
        params={"adjusted": "true", "sort": "asc"},
    )
    if body is None:
        return None
    return body.get("results", [])


def _bar_date_iso(bar: dict) -> str:
    return date.fromtimestamp(bar["t"] / 1000).isoformat()


def _pct_change(a: float, b: float) -> float:
    if a <= 0:
        return 0.0
    return (b / a - 1) * 100.0


def _auto_link(conn, account_id: int, ticker: str, event_date_iso: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM catalysts "
        "WHERE ticker=? "
        "AND ABS(julianday(substr(published_at, 1, 10)) - julianday(?)) <= ? "
        "AND final_score >= ? "
        "ORDER BY final_score DESC LIMIT 1",
        (ticker, event_date_iso, CATALYST_LINK_WINDOW_DAYS, CATALYST_MIN_LINK_SCORE),
    ).fetchone()
    return int(row["id"]) if row else None


def detect_events_for_all_accounts(conn) -> int:
    """Return number of NEW events written."""
    by_acc = _positions_qty_by_account(conn)
    if not by_acc:
        return 0

    # Unique tickers across all accounts → fetch each once
    all_tickers: set[str] = set()
    for held in by_acc.values():
        all_tickers.update(held.keys())
    bars_by_ticker: dict[str, list[dict]] = {}
    for t in sorted(all_tickers):
        bars = _fetch_bars(t)
        if bars and len(bars) >= 2:
            bars_by_ticker[t] = bars

    # Accounts → per-account thresholds
    acc_rows = {a["id"]: a for a in cdb.load_accounts(conn)}

    written = 0
    for account_id, held in by_acc.items():
        acc = acc_rows.get(account_id)
        if acc is None:
            continue
        daily_th = acc["event_daily_pct"] or DEFAULT_DAILY_PCT
        five_th  = acc["event_5day_pct"]  or DEFAULT_5DAY_PCT

        for ticker, qty in held.items():
            bars = bars_by_ticker.get(ticker)
            if not bars or len(bars) < 2:
                continue
            today_bar = bars[-1]
            prev_bar = bars[-2]
            event_date_iso = _bar_date_iso(today_bar)

            # 1-day move
            daily_pct = _pct_change(prev_bar["c"], today_bar["c"])
            if abs(daily_pct) >= daily_th:
                written += _write_event(
                    conn, account_id, ticker, event_date_iso, "1d",
                    daily_pct, qty, prev_bar["c"], today_bar["c"],
                )

            # 5-day move (today vs 5 bars ago, if available)
            if len(bars) >= 6:
                five_ago = bars[-6]
                five_pct = _pct_change(five_ago["c"], today_bar["c"])
                if abs(five_pct) >= five_th:
                    written += _write_event(
                        conn, account_id, ticker, event_date_iso, "5d",
                        five_pct, qty, five_ago["c"], today_bar["c"],
                    )
    return written


def _write_event(
    conn, account_id: int, ticker: str, event_date_iso: str,
    move_window: str, move_pct: float, qty: float,
    close_before: float, close_after: float,
) -> int:
    value_before = qty * close_before
    value_after  = qty * close_after
    pnl_dollars  = value_after - value_before

    catalyst_id = _auto_link(conn, account_id, ticker, event_date_iso)

    _eid, was_new = cdb.insert_event(
        conn, account_id=account_id, ticker=ticker,
        event_date=event_date_iso, move_pct=round(move_pct, 2),
        move_window=move_window, position_qty=qty,
        value_before=round(value_before, 2),
        value_after=round(value_after, 2),
        pnl_dollars=round(pnl_dollars, 2),
        catalyst_id=catalyst_id,
    )
    return 1 if was_new else 0
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `.venv/Scripts/python -m pytest tests/test_events.py -v`
Expected: 9 passing.

- [ ] **Step 5: Hook detector into `catalyst_poller.py`**

In `catalyst_poller.py`, after the `# Technical confluence scoring` block (around line 170) and before `alerts_sent = 0`, append:

```python
    # Position event detection (portfolios)
    if os.environ.get("POLYGON_API_KEY"):
        try:
            from portfolios.events import detect_events_for_all_accounts
            n_events = detect_events_for_all_accounts(conn)
            if n_events:
                print(f"[poller] detected {n_events} position events (pending review)")
        except Exception as exc:
            print(f"[poller] event detection failed: {exc}")
```

- [ ] **Step 6: Full suite**

Run: `.venv/Scripts/python -m pytest tests/ -q`
Expected: 151 passing (142 + 9 new).

- [ ] **Step 7: Commit**

```bash
git add portfolios/events.py catalyst_poller.py tests/test_events.py
git commit -m "feat(portfolios): position event detector + poller hook

Runs at the tail of catalyst_poller.run_once(). For each held position,
fetches ~8 days of daily bars via polygon_client (rate-limited by Phase 6
bucket), computes 1d and 5d moves, writes events for |move| >= threshold.

Threshold is per-account with a global default (5% daily / 10% 5-day).
Idempotent via UNIQUE(account_id, ticker, event_date, move_window) so
re-scans are safe. Dismissed events survive re-scans.

Auto-link queries the catalysts table for ticker + published_at within
±3 days + final_score >= 30, taking the highest-scoring match. Never
crosses tickers.

Best-effort: detector failures are caught in the poller wrapper and
logged, never blocking catalyst alerts.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Events page UI

**Files:**
- Create: `app_pages/events.py`
- Modify: `app.py` — register page + pending-events badge

- [ ] **Step 1: Create `app_pages/events.py`**

```python
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
```

- [ ] **Step 2: Register page + pending badge in `app.py`**

In `app.py`, add `events` to the `from app_pages import` block (alphabetical).

Update `_sidebar_header` to surface the pending count as before; no other change needed. Then in `_nav`, add the Events page between Accounts and Import:

```python
    st.Page(events.render,          title="Events",        icon="⚡",
            url_path="events"),
```

Final `_nav` order:

```python
_nav = st.navigation([
    st.Page(dashboard.render,       title="Dashboard",     icon="📊",
            url_path="dashboard", default=True),
    st.Page(catalysts_page.render,  title="Catalysts",     icon="📰",
            url_path="catalysts"),
    st.Page(options_pulse.render,   title="Options Pulse", icon="📈",
            url_path="options-pulse"),
    st.Page(accounts.render,        title="Accounts",      icon="🏦",
            url_path="accounts"),
    st.Page(events.render,          title="Events",        icon="⚡",
            url_path="events"),
    st.Page(import_trades.render,   title="Import",        icon="⬆",
            url_path="import"),
    st.Page(power_gauge.render,     title="Power Gauge",   icon="⚡",
            url_path="power-gauge"),
    st.Page(holdings.render,        title="Holdings",      icon="💼",
            url_path="holdings"),
    st.Page(trades.render,          title="Trades",        icon="🧾",
            url_path="trades"),
    st.Page(performance.render,     title="Performance",   icon="📉",
            url_path="performance"),
    st.Page(ipo_tracker.render,     title="IPO Tracker",   icon="🆕",
            url_path="ipo-tracker"),
    st.Page(universe.render,        title="Universe",      icon="🌐",
            url_path="universe"),
])
```

Also update the `_sidebar_header()` function to surface pending events (replace the existing body):

```python
def _sidebar_header() -> None:
    st.sidebar.title("📈 Dealscout")

    conn = get_conn()
    tickers = active_tickers()
    unseen = cdb.unseen_alert_count(conn)
    last_poll = cdb.last_poll_time(conn)
    total_opts = conn.execute(
        "SELECT COUNT(*) FROM options_snapshot"
    ).fetchone()[0]
    pending_events = cdb.pending_event_count(conn)

    st.sidebar.caption(f"Universe: {len(tickers)} tickers")
    st.sidebar.caption(f"Last catalyst poll: {last_poll or '—'}")
    if unseen:
        st.sidebar.warning(f"🔴 {unseen} unseen alert{'s' if unseen != 1 else ''}")
    if pending_events:
        st.sidebar.warning(
            f"⚡ {pending_events} position event{'s' if pending_events != 1 else ''} "
            f"to review"
        )
    if total_opts:
        st.sidebar.caption(f"Options tracked: {total_opts}")
    if st.sidebar.button("🔄 Refresh prices"):
        st.cache_data.clear()
        st.rerun()
```

- [ ] **Step 3: Smoke test**

Run: `.venv/Scripts/python -c "from app_pages import events; print('ok')"`
Expected: `ok`.

Run: `.venv/Scripts/python -m py_compile app.py && echo ok`
Expected: `ok`.

Run: `.venv/Scripts/python -m pytest tests/ -q`
Expected: 151 passing (no new tests; UI-only).

- [ ] **Step 4: Commit**

```bash
git add app_pages/events.py app.py
git commit -m "feat(portfolios): Events page for labeling + sidebar pending badge

Filters: account, status (pending/confirmed/dismissed/all), lookback,
ticker-contains. Default view is pending newest-first.

Side panel shows: position context (before/after/P&L), current catalyst
link with headline, a 'change link' dropdown of all catalysts on the
same ticker within ±14 days (widened from detector's ±3), catalyst type
selector (9 options), notes. Three actions: Confirm / Dismiss / Save pending.

Sidebar surfaces pending event count as a warning banner alongside
unseen alerts.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Analytics module + UI integration

**Files:**
- Create: `portfolios/analytics.py`
- Create: `tests/test_analytics.py`
- Modify: `app_pages/events.py` — add 3 rollup charts above the table
- Modify: `app_pages/catalysts_page.py` — add "Did this catalyst move my portfolio?" panel
- Modify: `app_pages/dashboard.py` — add "events this week" column

- [ ] **Step 1: Write analytics tests**

Create `tests/test_analytics.py`:

```python
from catalysts import db as cdb
from portfolios import analytics


def _seed(tmp_db):
    cdb.migrate(tmp_db)
    a = cdb.create_account(tmp_db, name="A", type="taxable",
                             broker="fidelity", opened_date="2024-01-01")
    eid, _ = cdb.insert_event(
        tmp_db, account_id=a, ticker="NVDA", event_date="2026-04-15",
        move_pct=8.2, move_window="1d", position_qty=10.0,
        value_before=5000.0, value_after=5410.0, pnl_dollars=410.0,
    )
    cdb.update_event(tmp_db, eid, status="confirmed",
                      catalyst_type="earnings")

    eid2, _ = cdb.insert_event(
        tmp_db, account_id=a, ticker="AAPL", event_date="2026-04-14",
        move_pct=-6.0, move_window="1d", position_qty=5.0,
        value_before=1000.0, value_after=940.0, pnl_dollars=-60.0,
    )
    cdb.update_event(tmp_db, eid2, status="confirmed",
                      catalyst_type="earnings")

    eid3, _ = cdb.insert_event(
        tmp_db, account_id=a, ticker="TSLA", event_date="2026-04-13",
        move_pct=7.0, move_window="1d", position_qty=2.0,
        value_before=500.0, value_after=535.0, pnl_dollars=35.0,
    )
    cdb.update_event(tmp_db, eid3, status="confirmed",
                      catalyst_type="rumor")
    return a


def test_catalyst_type_pnl_rollup(tmp_db):
    _seed(tmp_db)
    rows = analytics.pnl_by_catalyst_type(tmp_db)
    by_type = {r["catalyst_type"]: r for r in rows}
    assert by_type["earnings"]["net_pnl"] == 350.0  # 410 - 60
    assert by_type["earnings"]["wins"] == 1
    assert by_type["earnings"]["losses"] == 1
    assert by_type["rumor"]["net_pnl"] == 35.0
    assert by_type["rumor"]["wins"] == 1
    assert by_type["rumor"]["losses"] == 0


def test_hit_rate_by_catalyst_type(tmp_db):
    _seed(tmp_db)
    rows = analytics.hit_rate_by_catalyst_type(tmp_db)
    by_type = {r["catalyst_type"]: r for r in rows}
    assert abs(by_type["earnings"]["hit_rate"] - 0.5) < 0.001  # 1/2
    assert by_type["rumor"]["hit_rate"] == 1.0


def test_linked_vs_unlinked(tmp_db):
    acc = _seed(tmp_db)
    # Pre-seed one catalyst and attach the NVDA event to it
    tmp_db.execute(
        "INSERT INTO catalysts(ticker,source,source_id,headline,url,"
        "published_at,kw_score,final_score,seen,fetched_at) "
        "VALUES('NVDA','edgar','x','x','x','2026-04-15',60,85,0,'x')"
    )
    cid = tmp_db.execute(
        "SELECT id FROM catalysts WHERE ticker='NVDA'"
    ).fetchone()[0]
    tmp_db.execute("UPDATE events SET catalyst_id=? WHERE ticker='NVDA'",
                     (cid,))
    tmp_db.commit()

    counts = analytics.linked_vs_unlinked(tmp_db)
    assert counts["linked"] == 1
    assert counts["unlinked"] == 2


def test_events_for_catalyst(tmp_db):
    acc = _seed(tmp_db)
    tmp_db.execute(
        "INSERT INTO catalysts(ticker,source,source_id,headline,url,"
        "published_at,kw_score,final_score,seen,fetched_at) "
        "VALUES('NVDA','edgar','x','x','x','2026-04-15',60,85,0,'x')"
    )
    cid = tmp_db.execute(
        "SELECT id FROM catalysts WHERE ticker='NVDA'"
    ).fetchone()[0]
    tmp_db.execute("UPDATE events SET catalyst_id=? WHERE ticker='NVDA'",
                     (cid,))
    tmp_db.commit()
    rows = analytics.events_for_catalyst(tmp_db, cid)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "NVDA"
    assert rows[0]["account_name"] == "A"
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `.venv/Scripts/python -m pytest tests/test_analytics.py -v`
Expected: failures (module missing).

- [ ] **Step 3: Create `portfolios/analytics.py`**

```python
"""Rollup analytics for the Accounts/Events/Catalysts UI."""
from __future__ import annotations


def pnl_by_catalyst_type(conn) -> list[dict]:
    """Return per-catalyst_type: wins, losses, gross_wins $, gross_losses $,
    net_pnl $. Only status='confirmed' events count."""
    rows = conn.execute(
        "SELECT catalyst_type, "
        "SUM(CASE WHEN move_pct > 0 THEN 1 ELSE 0 END) AS wins, "
        "SUM(CASE WHEN move_pct < 0 THEN 1 ELSE 0 END) AS losses, "
        "COALESCE(SUM(CASE WHEN pnl_dollars > 0 THEN pnl_dollars ELSE 0 END), 0) AS gross_wins, "
        "COALESCE(SUM(CASE WHEN pnl_dollars < 0 THEN pnl_dollars ELSE 0 END), 0) AS gross_losses, "
        "COALESCE(SUM(pnl_dollars), 0) AS net_pnl "
        "FROM events "
        "WHERE status='confirmed' AND catalyst_type IS NOT NULL "
        "GROUP BY catalyst_type "
        "ORDER BY net_pnl DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def hit_rate_by_catalyst_type(conn) -> list[dict]:
    """Per-catalyst_type: n, hit_rate (fraction of events with move_pct > 0)."""
    rows = conn.execute(
        "SELECT catalyst_type, COUNT(*) AS n, "
        "  (1.0 * SUM(CASE WHEN move_pct > 0 THEN 1 ELSE 0 END) / COUNT(*)) AS hit_rate "
        "FROM events "
        "WHERE status='confirmed' AND catalyst_type IS NOT NULL "
        "GROUP BY catalyst_type "
        "ORDER BY hit_rate DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def linked_vs_unlinked(conn) -> dict:
    """Return {linked, unlinked} counts across all confirmed events."""
    row = conn.execute(
        "SELECT "
        "SUM(CASE WHEN catalyst_id IS NOT NULL THEN 1 ELSE 0 END) AS linked, "
        "SUM(CASE WHEN catalyst_id IS NULL THEN 1 ELSE 0 END) AS unlinked "
        "FROM events WHERE status='confirmed'"
    ).fetchone()
    return {
        "linked":   int(row["linked"]   or 0),
        "unlinked": int(row["unlinked"] or 0),
    }


def events_for_catalyst(conn, catalyst_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT e.*, a.name AS account_name "
        "FROM events e JOIN accounts a ON a.id = e.account_id "
        "WHERE e.catalyst_id = ? "
        "ORDER BY e.event_date DESC",
        (catalyst_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def weekly_events_per_ticker(conn, tickers: list[str], days: int = 30) -> dict:
    """Return {ticker: [{event_date, pnl_dollars, cumulative_pnl}]} for
    recent confirmed events. Used by the Dashboard sparkline."""
    if not tickers:
        return {}
    placeholders = ",".join(["?"] * len(tickers))
    rows = conn.execute(
        f"SELECT ticker, event_date, pnl_dollars FROM events "
        f"WHERE status='confirmed' AND ticker IN ({placeholders}) "
        f"AND date(event_date) >= date('now', ?) "
        f"ORDER BY ticker, event_date",
        [*tickers, f"-{days} days"],
    ).fetchall()
    out: dict[str, list[dict]] = {}
    cum: dict[str, float] = {}
    for r in rows:
        tk = r["ticker"]
        cum[tk] = cum.get(tk, 0.0) + r["pnl_dollars"]
        out.setdefault(tk, []).append({
            "event_date": r["event_date"],
            "pnl_dollars": r["pnl_dollars"],
            "cumulative_pnl": cum[tk],
        })
    return out
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `.venv/Scripts/python -m pytest tests/test_analytics.py -v`
Expected: 4 passing.

- [ ] **Step 5: Wire charts into the Events page**

In `app_pages/events.py`, at the top of `render()` after the three metric columns but before the filter row, add:

```python
    # Rollup charts over confirmed events
    with st.expander("Feedback-loop analytics", expanded=True):
        _render_rollup_charts(conn)
```

And add at module scope:

```python
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
```

- [ ] **Step 6: Wire catalyst-drilldown panel into Catalysts page**

In `app_pages/catalysts_page.py`, find the drilldown section (after `st.subheader("Drilldown")`). After the `st.write({"tags": row["tags"], ...})` line, insert:

```python
    # Feedback loop: did this catalyst move any positions?
    _render_portfolio_events_for_catalyst(conn, row["id"])
```

And add at module scope:

```python
def _render_portfolio_events_for_catalyst(conn, catalyst_id: int) -> None:
    from portfolios import analytics
    rows = analytics.events_for_catalyst(conn, catalyst_id)
    st.subheader("Did this catalyst move my portfolio?")
    if not rows:
        # Find ticker for helpful suggestion
        tr = conn.execute(
            "SELECT ticker FROM catalysts WHERE id=?", (catalyst_id,),
        ).fetchone()
        tk = tr["ticker"] if tr else "this ticker"
        st.caption(f"No position events linked yet. Add {tk} to a portfolio "
                    f"(Import or Trades) to start tracking this catalyst's impact.")
        return
    df = pd.DataFrame(rows)[[
        "account_name", "ticker", "event_date", "move_window",
        "move_pct", "pnl_dollars", "catalyst_type", "status",
    ]].rename(columns={
        "account_name": "Account", "ticker": "Ticker", "event_date": "Date",
        "move_window": "Win", "move_pct": "Move %",
        "pnl_dollars": "P&L $", "catalyst_type": "Tag", "status": "Status",
    })
    st.dataframe(
        df.style.format({"Move %": "{:+.2f}%", "P&L $": "${:,.2f}"}),
        width="stretch", hide_index=True,
    )
```

- [ ] **Step 7: Wire "events this week" into Dashboard**

In `app_pages/dashboard.py`, find where `view["tech"]` is built (near line ~152). After:

```python
    view["tech"] = view["ticker"].map(_tech_label)
```

Insert:

```python
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
```

Then in the `view.rename(columns=...)` mapping, add:

```python
        "events": "Events",
```

And in `display_cols`, insert `"Events"` after `"Tech"`:

```python
    display_cols = ["Ticker", "Name", "Last", "Daily %", "Weekly %",
                    "Monthly %", "YTD %", "Grade", "Tech", "Events", "Catalyst",
                    "Options", "IV Rank", "Earn DTE", "Entry"]
```

- [ ] **Step 8: Full suite**

Run: `.venv/Scripts/python -m pytest tests/ -q`
Expected: 155 passing (151 + 4 analytics).

Run: `.venv/Scripts/python -c "from app_pages import events, catalysts_page, dashboard; print('ok')"`
Expected: `ok`.

- [ ] **Step 9: Commit**

```bash
git add portfolios/analytics.py tests/test_analytics.py app_pages/events.py app_pages/catalysts_page.py app_pages/dashboard.py
git commit -m "feat(portfolios): feedback-loop analytics across Events / Catalysts / Dashboard

portfolios/analytics.py:
  pnl_by_catalyst_type  — per-type wins/losses/net \$ over confirmed events
  hit_rate_by_catalyst_type — fraction with positive move_pct
  linked_vs_unlinked — count of events with a catalyst_id vs without
  events_for_catalyst — catalyst-centric reverse lookup
  weekly_events_per_ticker — 30d rollup for Dashboard sparkline

Events page: three charts above the table summarize the feedback loop.
Catalysts page: 'Did this catalyst move my portfolio?' panel inside the
drilldown — lists all events linked to the selected catalyst.
Dashboard: 'Events' column per ticker (wins/losses/cumulative P&L over
30d) alongside Tech and Catalyst.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Self-review summary

**Spec coverage:** Every section of the spec is covered: schema (Task 1+2), portfolio rewrite (3), importer (4+5), accounts UI (6), import UI (7), detector (8), events UI (9), analytics (10). Built-in profiles for the 5 named brokers seeded in Task 5. Rollout order in Tasks 1→10 matches spec's numbered phases 1→8 (phases 7 and 8 of spec = Task 9 and 10 here).

**Placeholders:** None. Every step contains actual code, actual commands, or actual commit messages. Where a function is referenced in a later task, it is defined in an earlier task or in this plan (e.g. `cdb.load_events` defined in Task 2, consumed in Task 10).

**Type consistency:** Function signatures agree across tasks (`insert_trade` returns `(int, bool)` in Task 2, consumed as `(tid, was_new)` in Task 4/7). Field names match (`catalyst_type`, `move_window`, `status`, `pnl_dollars`) across DB layer, detector, UI, and analytics.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-04-19-portfolios.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
