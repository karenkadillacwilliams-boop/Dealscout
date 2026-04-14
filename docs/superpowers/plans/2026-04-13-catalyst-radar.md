# Catalyst Radar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship Dealscout Phase 3 — a background poller that ingests EDGAR filings, yfinance news, and Google News RSS, scores items with a keyword pass plus a Claude Haiku re-ranker, persists to SQLite, fires dedup'd alerts to Gmail SMTP and a Discord webhook when `final_score ≥ 70`, and exposes everything through new Catalysts / Universe pages in the Streamlit app.

**Architecture:** Streamlit is a **reader only**. A separate `catalyst_poller.py` process (run by Windows Task Scheduler every 15 min) handles all network I/O, scoring, LLM calls, and alerting, writing to `dealscout.db`. Both passes of the scorer are pure functions with stable signatures so the implementation can evolve without touching the poller, DB, or alerts.

**Tech Stack:** Python 3.11+, Streamlit, SQLite (WAL mode), `requests`, `feedparser`, `defusedxml`, yfinance, `anthropic` (Claude Haiku 4.5), stdlib `smtplib` + `email.message`, `python-dotenv`.

**Spec:** `docs/superpowers/specs/2026-04-13-catalyst-radar-design.md` (commit 4dfc386).

---

## File structure

**New files:**
- `catalysts/__init__.py` — package marker + public re-exports
- `catalysts/types.py` — `RawCatalyst`, `ScoredItem`, `RerankedItem` dataclasses
- `catalysts/db.py` — connection, migrations, typed row helpers, universe CRUD
- `catalysts/edgar.py` — EDGAR RSS + submissions fetcher
- `catalysts/news.py` — yfinance + Google News RSS fetchers
- `catalysts/score.py` — pure keyword scorer, compiled regex dict
- `catalysts/rerank.py` — Claude Haiku re-ranker, batched, JSON-mode
- `catalysts/dedup.py` — `filter_unseen`, `recently_alerted`
- `alerts/__init__.py`
- `alerts/dispatcher.py` — channel-agnostic fan-out
- `alerts/email.py` — stdlib `EmailMessage` over Gmail SMTP SSL
- `alerts/discord.py` — single webhook POST with embed
- `catalyst_poller.py` — Task Scheduler entry point
- `tests/__init__.py`
- `tests/conftest.py` — temp DB fixture, env patching
- `tests/test_score.py` — golden-file regression suite
- `tests/test_dedup.py` — unseen/alerted queries
- `tests/test_dispatcher.py` — channel fan-out semantics
- `tests/test_poller_integration.py` — full round-trip with stubbed fetchers/LLM
- `tests/fixtures/headlines.py` — 30-item golden set for the scorer
- `.env.example` — committed template of required env vars

**Modified files:**
- `app.py` — sidebar badge, Catalysts page, Universe page, Dashboard catalyst column
- `tickers.py` — add `SEED_TICKERS` alias; comment that `catalysts.db.load_active_universe` is authoritative
- `requirements.txt` — add `python-dotenv`, `requests`, `feedparser`, `defusedxml`, `anthropic`
- `.gitignore` — ensure `.env`, `*.db-wal`, `*.db-shm` are ignored

**Shared types** (defined once in `catalysts/types.py`, imported by every consumer):

```python
# catalysts/types.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

@dataclass(frozen=True)
class RawCatalyst:
    ticker: str
    source: str            # 'edgar' | 'yfinance' | 'gnews'
    source_id: str         # accession number, url, or guid
    headline: str
    url: str
    published_at: str      # ISO8601 UTC
    form_type: Optional[str] = None

@dataclass(frozen=True)
class ScoredItem:
    raw: RawCatalyst
    kw_score: int          # 0..100
    tags: tuple[str, ...]
    matched_phrases: tuple[str, ...]

@dataclass(frozen=True)
class RerankedItem:
    scored: ScoredItem
    llm_score: Optional[int]   # 0..10, None if skipped
    rationale: Optional[str]   # <= 25 words
    final_score: int           # 0..100

    # convenience passthroughs used by the alert dispatcher
    @property
    def ticker(self) -> str: return self.scored.raw.ticker
    @property
    def headline(self) -> str: return self.scored.raw.headline
    @property
    def url(self) -> str: return self.scored.raw.url
    @property
    def source(self) -> str: return self.scored.raw.source
    @property
    def published_at(self) -> str: return self.scored.raw.published_at
    @property
    def tags(self) -> tuple[str, ...]: return self.scored.tags
```

These names are **stable** — every later task imports them by these exact names.

---

## Rollout sequence (matches spec §13)

| Task | Rollout step | Ships |
|---|---|---|
| 1–3  | Step 1 | DB migration + Universe page |
| 4–7  | Step 2 | EDGAR fetcher + keyword scorer + Catalysts page (no LLM, no alerts) |
| 8    | Step 3 | yfinance + Google News fetchers |
| 9    | Step 4 | LLM re-ranker + score fusion |
| 10–12| Step 5 | Alert dispatcher (email + Discord) + 6h dedup |
| 13   | Step 6 | Task Scheduler wire-up |
| 14   | Step 7 | Dashboard catalyst column + sidebar badge |

---

## Task 1: Bootstrap — requirements, env, gitignore, types

**Files:**
- Modify: `requirements.txt`
- Modify: `.gitignore`
- Create: `.env.example`
- Create: `catalysts/__init__.py`
- Create: `catalysts/types.py`
- Create: `alerts/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Append new deps to `requirements.txt`**

```
python-dotenv>=1.0.0
requests>=2.31.0
feedparser>=6.0.10
defusedxml>=0.7.1
anthropic>=0.39.0
pytest>=8.0.0
```

- [ ] **Step 2: Append to `.gitignore`**

```
.env
*.db-wal
*.db-shm
__pycache__/
.pytest_cache/
```

- [ ] **Step 3: Create `.env.example`**

```
GMAIL_USER=alerts@example.com
GMAIL_APP_PW=xxxx-xxxx-xxxx-xxxx
ALERT_TO_EMAIL=you@example.com
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
ANTHROPIC_API_KEY=sk-ant-...
MAX_RERANK_CALLS_PER_DAY=200
SEC_USER_AGENT=Dealscout/1.0 contact@example.com
```

- [ ] **Step 4: Create `catalysts/__init__.py`**

```python
"""Catalyst Radar package — EDGAR/news ingestion, scoring, persistence."""
from catalysts.types import RawCatalyst, ScoredItem, RerankedItem

__all__ = ["RawCatalyst", "ScoredItem", "RerankedItem"]
```

- [ ] **Step 5: Create `catalysts/types.py` with the full dataclass block from the File Structure section above.**

Copy the entire `catalysts/types.py` block shown earlier in this plan verbatim.

- [ ] **Step 6: Create `alerts/__init__.py`**

```python
"""Alert delivery package — channel-agnostic dispatcher + senders."""
```

- [ ] **Step 7: Create `tests/__init__.py`** (empty file).

- [ ] **Step 8: Create `tests/conftest.py`**

```python
import os
import sqlite3
from pathlib import Path
import pytest

@pytest.fixture
def tmp_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    yield conn
    conn.close()

@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("GMAIL_USER", "alerts@test.local")
    monkeypatch.setenv("GMAIL_APP_PW", "test-pw")
    monkeypatch.setenv("ALERT_TO_EMAIL", "you@test.local")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/webhook")
    monkeypatch.setenv("MAX_RERANK_CALLS_PER_DAY", "200")
    monkeypatch.setenv("SEC_USER_AGENT", "Dealscout-Test/1.0")
```

- [ ] **Step 9: Install deps**

Run: `pip install -r requirements.txt`
Expected: success.

- [ ] **Step 10: Verify imports**

Run: `python -c "from catalysts.types import RawCatalyst, ScoredItem, RerankedItem; print('ok')"`
Expected: `ok`

- [ ] **Step 11: Commit**

```bash
git add requirements.txt .gitignore .env.example catalysts/__init__.py catalysts/types.py alerts/__init__.py tests/__init__.py tests/conftest.py
git commit -m "chore(catalyst-radar): bootstrap packages, types, and test config"
```

---

## Task 2: DB migrations and universe CRUD

**Files:**
- Create: `catalysts/db.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: Write failing test `tests/test_db.py`**

```python
from catalysts import db as cdb

def test_migrate_creates_all_tables(tmp_db):
    cdb.migrate(tmp_db)
    names = {r[0] for r in tmp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"catalysts", "alert_log", "universe"} <= names

def test_upsert_universe_and_load_active(tmp_db):
    cdb.migrate(tmp_db)
    cdb.upsert_universe(tmp_db, "NVDA", "NVIDIA Corp")
    cdb.upsert_universe(tmp_db, "AAPL", "Apple Inc")
    cdb.deactivate_ticker(tmp_db, "AAPL")
    active = cdb.load_active_universe(tmp_db)
    assert active == ["NVDA"]

def test_seed_from_list_only_when_empty(tmp_db):
    cdb.migrate(tmp_db)
    cdb.seed_universe_if_empty(tmp_db, ["NVDA", "AAPL"])
    cdb.seed_universe_if_empty(tmp_db, ["TSLA"])  # should be a no-op
    active = cdb.load_active_universe(tmp_db)
    assert set(active) == {"NVDA", "AAPL"}

def test_persist_catalyst_is_idempotent(tmp_db):
    cdb.migrate(tmp_db)
    from catalysts.types import RawCatalyst, ScoredItem, RerankedItem
    raw = RawCatalyst("NVDA", "edgar", "acc-1", "NVDA to acquire X", "https://sec.gov/x", "2026-04-13T10:00:00Z", "8-K")
    scored = ScoredItem(raw, 55, ("m&a-confirmed",), ("to acquire",))
    item = RerankedItem(scored, 9, "Clear M&A", 87)
    cid1 = cdb.persist_catalyst(tmp_db, item)
    cid2 = cdb.persist_catalyst(tmp_db, item)
    assert cid1 == cid2  # UNIQUE(source, source_id) returns existing id
    n = tmp_db.execute("SELECT COUNT(*) FROM catalysts").fetchone()[0]
    assert n == 1
```

- [ ] **Step 2: Run it to verify failure**

Run: `pytest tests/test_db.py -v`
Expected: all four tests fail with `ModuleNotFoundError` or `AttributeError`.

- [ ] **Step 3: Create `catalysts/db.py`**

```python
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
    existing = conn.execute(
        "SELECT id FROM catalysts WHERE source=? AND source_id=?",
        (raw.source, raw.source_id),
    ).fetchone()
    if existing:
        return existing[0]
    cur = conn.execute(
        """INSERT INTO catalysts
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
    conn.commit()
    return int(cur.lastrowid)


def mark_seen(conn: sqlite3.Connection, catalyst_ids: Iterable[int]) -> None:
    ids = list(catalyst_ids)
    if not ids:
        return
    q = f"UPDATE catalysts SET seen=1 WHERE id IN ({','.join('?' * len(ids))})"
    conn.execute(q, ids)
    conn.commit()


def unseen_alert_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM catalysts WHERE final_score >= 70 AND seen = 0"
    ).fetchone()[0]


def last_poll_time(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT MAX(fetched_at) FROM catalysts").fetchone()
    return row[0] if row else None
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_db.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add catalysts/db.py tests/test_db.py
git commit -m "feat(catalyst-radar): add SQLite schema, migrations, and universe CRUD"
```

---

## Task 3: Universe page in Streamlit + seed on first app run

**Files:**
- Modify: `app.py`
- Modify: `tickers.py`

- [ ] **Step 1: Add `SEED_TICKERS` alias in `tickers.py`**

At the bottom of `tickers.py`, append:

```python
# Authoritative ticker list now lives in the `universe` table.
# TICKERS is kept as a seed for first-run and as a fallback.
SEED_TICKERS = TICKERS
```

- [ ] **Step 2: In `app.py`, replace the existing universe-loading line**

Locate `app.py:10` (the `from tickers import NAMES, TICKERS` line). Leave it. Immediately after `portfolio.init_db()` on line 13, add:

```python
from catalysts import db as cdb

_conn = cdb.connect()
cdb.migrate(_conn)
cdb.seed_universe_if_empty(_conn, TICKERS)
ACTIVE_TICKERS = cdb.load_active_universe(_conn) or TICKERS
```

Then change line 24 from:

```python
prices = fetch_history(TICKERS, period="6mo")
```

to:

```python
prices = fetch_history(ACTIVE_TICKERS, period="6mo")
```

And change the sidebar caption on line 18 from:

```python
st.sidebar.caption(f"Universe: {len(TICKERS)} tickers")
```

to:

```python
st.sidebar.caption(f"Universe: {len(ACTIVE_TICKERS)} tickers")
```

And change the radio options on line 17 from:

```python
page = st.sidebar.radio("Navigate", ["Dashboard", "Power Gauge", "Holdings", "Trades", "Performance"])
```

to:

```python
page = st.sidebar.radio("Navigate", ["Dashboard", "Power Gauge", "Holdings", "Trades", "Performance", "Universe"])
```

Also change the Trades-page ticker selectbox (currently `options=sorted(TICKERS)`) and the Power Gauge call `compute_power_gauge_ratings(TICKERS)` to use `ACTIVE_TICKERS` instead.

- [ ] **Step 3: Append the Universe page block at the bottom of `app.py`**

```python
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
    df = pd.DataFrame([dict(r) for r in rows])
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
```

- [ ] **Step 4: Manual smoke test**

Run: `streamlit run app.py`
Click Universe in the sidebar. Add `NVDA`, remove it, re-add it, bulk import `MSFT, AAPL`. Verify each round-trip succeeds and the Active tickers metric updates.

- [ ] **Step 5: Commit**

```bash
git add app.py tickers.py
git commit -m "feat(catalyst-radar): add Universe page, seed from TICKERS, DB-backed active list"
```

---

## Task 4: Keyword scorer with golden-file regression suite

**Files:**
- Create: `catalysts/score.py`
- Create: `tests/fixtures/__init__.py`
- Create: `tests/fixtures/headlines.py`
- Create: `tests/test_score.py`

- [ ] **Step 1: Create `tests/fixtures/__init__.py`** (empty).

- [ ] **Step 2: Create `tests/fixtures/headlines.py`**

```python
"""30 fixture headlines for the keyword scorer golden-file suite.
Each tuple: (headline, form_type, expected_min_kw_score, expected_tag)."""

CONFIRMED_MA = [
    ("ACME Corp to acquire Widgets Inc for $2.1B in cash", None, 35, "m&a-confirmed"),
    ("ACME and Widgets enter definitive agreement on merger", None, 35, "m&a-confirmed"),
    ("Widgets announces merger agreement with ACME Corp", None, 35, "m&a-confirmed"),
    ("ACME agrees to acquire rival Pinnacle in stock deal", None, 35, "m&a-confirmed"),
    ("Tender offer launched by Alpha for all outstanding Beta shares", None, 35, "m&a-confirmed"),
    ("Beta Corp files 8-K: entry into merger agreement", "8-K", 55, "m&a-confirmed"),
    ("Alpha Industries to acquire Beta for $800M", None, 35, "m&a-confirmed"),
    ("Definitive agreement reached between Delta and Epsilon", None, 35, "m&a-confirmed"),
    ("Gamma Corp launches tender offer for Omega", None, 35, "m&a-confirmed"),
    ("Zeta agrees to acquire Theta Systems", None, 35, "m&a-confirmed"),
]

RUMORED_MA = [
    ("Sources: ACME in talks to acquire Widgets", None, 25, "m&a-rumor"),
    ("Beta Corp exploring sale, sources say", None, 25, "m&a-rumor"),
    ("Gamma exploring strategic alternatives including sale", None, 25, "m&a-rumor"),
    ("Report: Alpha weighing bid for Omega", None, 25, "m&a-rumor"),
    ("Delta approached about takeover, per Bloomberg", None, 25, "m&a-rumor"),
    ("Epsilon considering offer from private equity group", None, 25, "m&a-rumor"),
    ("Theta in talks to acquire smaller rival", None, 25, "m&a-rumor"),
    ("Zeta exploring sale of semiconductor unit", None, 25, "m&a-rumor"),
    ("Pinnacle approached about potential acquisition", None, 25, "m&a-rumor"),
    ("Sources: Omega exploring strategic alternatives", None, 25, "m&a-rumor"),
]

NOISE = [
    ("Q3 earnings beat analyst estimates by 2 cents", None, 0, None),
    ("Company announces quarterly dividend of $0.25", None, 0, None),
    ("CEO to present at investor conference next week", None, 0, None),
    ("Firm denies rumor of any acquisition talks", None, 0, "weak"),
    ("ACME says speculation only, not in talks", None, 0, "weak"),
    ("Board approves $500M buyback program", None, 0, None),
    ("Company reports 5% revenue growth year-over-year", None, 0, None),
    ("Analyst upgrades stock to buy", None, 0, None),
    ("Firm files routine 10-K annual report", "10-K", 0, None),
    ("Stock price rises on broad market rally", None, 0, None),
]

ALL = CONFIRMED_MA + RUMORED_MA + NOISE
```

- [ ] **Step 3: Write failing test `tests/test_score.py`**

```python
from catalysts.score import score_item
from catalysts.types import RawCatalyst
from tests.fixtures.headlines import ALL, CONFIRMED_MA, RUMORED_MA, NOISE


def _raw(headline: str, form_type: str | None) -> RawCatalyst:
    return RawCatalyst(
        ticker="TEST", source="edgar" if form_type else "gnews",
        source_id=headline[:40], headline=headline, url="https://example.com",
        published_at="2026-04-13T10:00:00Z", form_type=form_type,
    )


def test_confirmed_ma_scores_high():
    for h, ft, min_score, tag in CONFIRMED_MA:
        item = score_item(_raw(h, ft))
        assert item.kw_score >= min_score, (h, item.kw_score)
        assert tag in item.tags, (h, item.tags)


def test_rumored_ma_scores_medium():
    for h, ft, min_score, tag in RUMORED_MA:
        item = score_item(_raw(h, ft))
        assert item.kw_score >= min_score, (h, item.kw_score)
        assert tag in item.tags, (h, item.tags)


def test_noise_scores_zero_or_low():
    for h, ft, min_score, tag in NOISE:
        item = score_item(_raw(h, ft))
        assert item.kw_score <= 20, (h, item.kw_score)
        if tag == "weak":
            assert "weak" in item.tags


def test_filing_bonus_added_for_mna_8k():
    base = score_item(_raw("Company enters merger agreement", None))
    filed = score_item(_raw("Company enters merger agreement", "8-K"))
    assert filed.kw_score > base.kw_score


def test_score_is_bounded_0_100():
    item = score_item(_raw(
        "Definitive agreement, to acquire, tender offer, merger agreement, strategic alternatives",
        "8-K"
    ))
    assert 0 <= item.kw_score <= 100
```

- [ ] **Step 4: Run test to verify fails**

Run: `pytest tests/test_score.py -v`
Expected: all fail with `ModuleNotFoundError`.

- [ ] **Step 5: Create `catalysts/score.py`**

```python
"""Keyword-based first-pass scorer.
Pure function — no I/O, no state, import-time regex compilation."""
from __future__ import annotations

import re
from typing import Iterable

from catalysts.types import RawCatalyst, ScoredItem

# (phrase, weight, tag)
_DICT: list[tuple[str, int, str]] = [
    # M&A — confirmed
    ("definitive agreement",   35, "m&a-confirmed"),
    ("to acquire",             35, "m&a-confirmed"),
    ("agrees to acquire",      35, "m&a-confirmed"),
    ("agreed to acquire",      35, "m&a-confirmed"),
    ("merger agreement",       35, "m&a-confirmed"),
    ("tender offer",           35, "m&a-confirmed"),
    # M&A — rumored
    ("in talks to",                    25, "m&a-rumor"),
    ("in talks to acquire",            25, "m&a-rumor"),
    ("exploring sale",                 25, "m&a-rumor"),
    ("exploring strategic alternatives", 25, "m&a-rumor"),
    ("weighing bid",                   25, "m&a-rumor"),
    ("approached about",               25, "m&a-rumor"),
    ("considering offer",              25, "m&a-rumor"),
    # Activist
    ("13D filed",        20, "activist"),
    ("activist stake",   20, "activist"),
    ("nominates directors", 20, "activist"),
    ("urges board",      20, "activist"),
    # Partnership
    ("strategic partnership",  15, "partnership"),
    ("collaboration agreement",15, "partnership"),
    ("joint venture",          15, "partnership"),
    ("licensing deal",         15, "partnership"),
    # Product / tech
    ("launches",       10, "product"),
    ("unveils",        10, "product"),
    ("first-in-class", 10, "product"),
    ("fda approval",   10, "product"),
    ("design win",     10, "product"),
    # Negative modifiers
    ("denies",           -15, "weak"),
    ("not in talks",     -15, "weak"),
    ("speculation only", -15, "weak"),
]

# Hostile-headline safety: cap phrase length, compile once with re.IGNORECASE.
MAX_PHRASE_LEN = 64
for _p, _w, _t in _DICT:
    assert len(_p) <= MAX_PHRASE_LEN, f"phrase too long: {_p}"

_COMPILED: list[tuple[re.Pattern, int, str, str]] = [
    (re.compile(r"\b" + re.escape(p) + r"\b", re.IGNORECASE), w, t, p)
    for p, w, t in _DICT
]

_FILING_FORMS_HIGH_SIGNAL: frozenset[str] = frozenset({
    "8-K", "13D", "SC 13D", "SC 13G", "425", "SC TO-T", "S-4", "DEFM14A",
})
_FILING_BONUS = 20


def score_item(raw: RawCatalyst) -> ScoredItem:
    text = raw.headline
    tags: list[str] = []
    matched: list[str] = []
    total = 0

    for pattern, weight, tag, phrase in _COMPILED:
        if pattern.search(text):
            total += weight
            matched.append(phrase)
            if tag not in tags:
                tags.append(tag)

    # Strong M&A signal in an M&A-relevant form → filing bonus and tag.
    has_ma_tag = any(t.startswith("m&a") or t == "activist" for t in tags)
    if raw.form_type in _FILING_FORMS_HIGH_SIGNAL and has_ma_tag:
        total += _FILING_BONUS
        if "filing" not in tags:
            tags.append("filing")

    kw = max(0, min(100, total))
    return ScoredItem(
        raw=raw,
        kw_score=kw,
        tags=tuple(tags),
        matched_phrases=tuple(matched),
    )
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_score.py -v`
Expected: 5 passed.

- [ ] **Step 7: Commit**

```bash
git add catalysts/score.py tests/test_score.py tests/fixtures/__init__.py tests/fixtures/headlines.py
git commit -m "feat(catalyst-radar): keyword scorer with 30-headline golden suite"
```

---

## Task 5: EDGAR fetcher

**Files:**
- Create: `catalysts/edgar.py`
- Create: `tests/test_edgar.py`

- [ ] **Step 1: Write failing test `tests/test_edgar.py`**

```python
from catalysts import edgar
from catalysts.types import RawCatalyst

SAMPLE_ATOM = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>8-K - ACME CORP (0001234567) (Filer)</title>
    <link href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&amp;CIK=0001234567&amp;type=8-K"/>
    <updated>2026-04-13T10:00:00-04:00</updated>
    <id>urn:tag:sec.gov,2008:accession-number=0001234567-26-000001</id>
    <category term="8-K"/>
  </entry>
</feed>
"""

def test_parse_atom_entries():
    entries = edgar._parse_atom(SAMPLE_ATOM, ticker="ACME")
    assert len(entries) == 1
    e = entries[0]
    assert isinstance(e, RawCatalyst)
    assert e.ticker == "ACME"
    assert e.source == "edgar"
    assert e.form_type == "8-K"
    assert "0001234567-26-000001" in e.source_id
    assert e.url.startswith("https://www.sec.gov/")

def test_fetch_stubbed(monkeypatch):
    captured = {}
    def fake_get(url, headers, timeout):
        captured["ua"] = headers.get("User-Agent", "")
        class R:
            status_code = 200
            content = SAMPLE_ATOM
            def raise_for_status(self): pass
        return R()
    monkeypatch.setattr(edgar.requests, "get", fake_get)
    items = edgar.fetch(["ACME"], since_hours=24)
    assert len(items) == 1
    assert captured["ua"].startswith("Dealscout")
```

- [ ] **Step 2: Run test**

Run: `pytest tests/test_edgar.py -v`
Expected: fail (`ModuleNotFoundError`).

- [ ] **Step 3: Create `catalysts/edgar.py`**

```python
"""EDGAR fetcher — pulls the company-filing Atom feed per ticker.

SEC rate-limit rules: descriptive User-Agent and <= 10 req/sec. We sleep
100ms between tickers to stay comfortably under the limit.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from typing import Iterable

import defusedxml.ElementTree as ET
import requests

from catalysts.types import RawCatalyst

ATOM_NS = "{http://www.w3.org/2005/Atom}"
FEED_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar?"
    "action=getcompany&CIK={ticker}&type=&dateb=&owner=include&count=40&output=atom"
)


def _ua() -> str:
    return os.environ.get("SEC_USER_AGENT", "Dealscout/1.0 contact@example.com")


_ACC_RE = re.compile(r"accession-number=([0-9\-]+)")
_FORM_RE = re.compile(r"^([A-Z0-9\-/]+)\s*-")


def _parse_atom(body: bytes, ticker: str) -> list[RawCatalyst]:
    root = ET.fromstring(body)
    out: list[RawCatalyst] = []
    for entry in root.findall(f"{ATOM_NS}entry"):
        title = (entry.findtext(f"{ATOM_NS}title") or "").strip()
        link_el = entry.find(f"{ATOM_NS}link")
        url = link_el.get("href") if link_el is not None else ""
        updated = (entry.findtext(f"{ATOM_NS}updated") or "").strip()
        id_text = (entry.findtext(f"{ATOM_NS}id") or "").strip()

        m_acc = _ACC_RE.search(id_text)
        acc = m_acc.group(1) if m_acc else id_text or url

        m_form = _FORM_RE.match(title)
        form_type = m_form.group(1) if m_form else None

        try:
            dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            published = dt.astimezone(timezone.utc).isoformat(timespec="seconds")
        except Exception:
            published = updated

        out.append(RawCatalyst(
            ticker=ticker,
            source="edgar",
            source_id=f"edgar:{ticker}:{acc}",
            form_type=form_type,
            headline=title,
            url=url,
            published_at=published,
        ))
    return out


def fetch(tickers: Iterable[str], since_hours: int = 2) -> list[RawCatalyst]:
    """Fetch recent filings per ticker. `since_hours` filters by published_at."""
    cutoff = datetime.now(timezone.utc).timestamp() - since_hours * 3600
    headers = {"User-Agent": _ua(), "Accept": "application/atom+xml"}
    out: list[RawCatalyst] = []
    for t in tickers:
        try:
            r = requests.get(FEED_URL.format(ticker=t), headers=headers, timeout=10)
            r.raise_for_status()
            entries = _parse_atom(r.content, t)
            for e in entries:
                try:
                    ts = datetime.fromisoformat(
                        e.published_at.replace("Z", "+00:00")
                    ).timestamp()
                except Exception:
                    ts = cutoff  # keep, don't crash
                if ts >= cutoff:
                    out.append(e)
        except Exception as ex:  # network hiccup, 403, etc. — skip this ticker
            print(f"[edgar] {t}: {ex}")
        time.sleep(0.1)
    return out
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_edgar.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add catalysts/edgar.py tests/test_edgar.py
git commit -m "feat(catalyst-radar): EDGAR Atom feed fetcher with defusedxml"
```

---

## Task 6: Catalysts page (read-only, pre-LLM)

**Files:**
- Modify: `app.py`

- [ ] **Step 1: Update the sidebar radio in `app.py` to include "Catalysts"**

Change the radio line (touched in Task 3) to:

```python
page = st.sidebar.radio("Navigate",
    ["Dashboard", "Catalysts", "Power Gauge", "Holdings", "Trades", "Performance", "Universe"])
```

- [ ] **Step 2: Append the Catalysts page block in `app.py`** (before the Universe block)

```python
elif page == "Catalysts":
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
        pick_id = st.selectbox(
            "Catalyst",
            options=[r["id"] for r in records],
            format_func=lambda i: next(f"{r['ticker']} — {r['headline'][:80]}"
                                       for r in records if r["id"] == i),
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
```

- [ ] **Step 3: Manual smoke test**

Run: `streamlit run app.py`
Open Catalysts page. Expected: the "No catalysts match" info message (DB is empty). No errors. Switch between filter values. No errors.

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "feat(catalyst-radar): Catalysts page with filters, drilldown, heatmap"
```

---

## Task 7: Poller dry-run mode — EDGAR only, no LLM, no alerts

**Files:**
- Create: `catalyst_poller.py`
- Create: `catalysts/dedup.py`

- [ ] **Step 1: Create `catalysts/dedup.py`**

```python
"""Dedup helpers used by the poller."""
from __future__ import annotations

import sqlite3
from typing import Iterable

from catalysts.types import RawCatalyst


def filter_unseen(conn: sqlite3.Connection, items: Iterable[RawCatalyst]) -> list[RawCatalyst]:
    out: list[RawCatalyst] = []
    for item in items:
        row = conn.execute(
            "SELECT 1 FROM catalysts WHERE source=? AND source_id=?",
            (item.source, item.source_id),
        ).fetchone()
        if row is None:
            out.append(item)
    return out


def recently_alerted(
    conn: sqlite3.Connection, ticker: str, score_bucket: int, hours: int = 6
) -> bool:
    row = conn.execute(
        "SELECT 1 FROM alert_log "
        "WHERE ticker=? AND score_bucket=? AND ok=1 "
        "  AND sent_at > datetime('now', ?) LIMIT 1",
        (ticker, score_bucket, f"-{hours} hours"),
    ).fetchone()
    return row is not None
```

- [ ] **Step 2: Create `catalyst_poller.py`**

```python
"""Catalyst Radar poller — run by Windows Task Scheduler every 15 min."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

from catalysts import db as cdb
from catalysts import edgar, score
from catalysts.dedup import filter_unseen
from catalysts.types import RawCatalyst, ScoredItem, RerankedItem


def _to_reranked_kw_only(s: ScoredItem) -> RerankedItem:
    return RerankedItem(
        scored=s, llm_score=None, rationale=None, final_score=s.kw_score,
    )


def run_once(dry_run: bool = False) -> int:
    load_dotenv()
    conn = cdb.connect()
    cdb.migrate(conn)

    tickers = cdb.load_active_universe(conn)
    if not tickers:
        print("[poller] no active tickers, nothing to do")
        return 0

    raw: list[RawCatalyst] = []
    raw += edgar.fetch(tickers, since_hours=2)
    print(f"[poller] fetched {len(raw)} raw items")

    fresh = filter_unseen(conn, raw)
    print(f"[poller] {len(fresh)} new after dedup")

    scored = [score.score_item(r) for r in fresh]
    reranked = [_to_reranked_kw_only(s) for s in scored]

    if dry_run:
        print(json.dumps([
            {"ticker": i.ticker, "score": i.final_score,
             "tags": list(i.tags), "headline": i.headline}
            for i in reranked
        ], indent=2))
        return 0

    persisted = 0
    for item in reranked:
        cdb.persist_catalyst(conn, item)
        persisted += 1
    print(f"[poller] persisted {persisted} catalysts")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return run_once(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Manual smoke test — dry run**

Run: `python catalyst_poller.py --dry-run`
Expected: prints JSON array of catalysts (may be empty outside market hours). No errors. No rows written.

- [ ] **Step 4: Manual smoke test — real persistence**

Run: `python catalyst_poller.py`
Expected: prints `[poller] persisted N catalysts`. Open Streamlit Catalysts page and lower min-score slider to 0 — rows should appear.

- [ ] **Step 5: Commit**

```bash
git add catalyst_poller.py catalysts/dedup.py
git commit -m "feat(catalyst-radar): poller skeleton with EDGAR fetch and dry-run mode"
```

---

## Task 8: yfinance news + Google News RSS fetchers

**Files:**
- Create: `catalysts/news.py`
- Create: `tests/test_news.py`
- Modify: `catalyst_poller.py`

- [ ] **Step 1: Write failing test `tests/test_news.py`**

```python
from catalysts import news


def test_gnews_parses_rss(monkeypatch):
    sample = """<?xml version="1.0"?><rss><channel>
      <item><title>ACME in talks to acquire Widgets</title>
      <link>https://news.example.com/a</link>
      <pubDate>Mon, 13 Apr 2026 10:00:00 GMT</pubDate>
      <guid>https://news.example.com/a</guid></item>
    </channel></rss>"""

    class R:
        status_code = 200
        content = sample.encode()
        text = sample
        def raise_for_status(self): pass

    monkeypatch.setattr(news.requests, "get", lambda *a, **k: R())
    items = news.fetch_gnews_rss(["ACME"])
    assert len(items) >= 1
    assert items[0].source == "gnews"
    assert items[0].ticker == "ACME"
    assert "acquire" in items[0].headline.lower()


def test_yfinance_news_handles_empty(monkeypatch):
    class FakeTicker:
        news = []
    monkeypatch.setattr(news.yf, "Ticker", lambda t: FakeTicker())
    items = news.fetch_yfinance(["ACME"])
    assert items == []
```

- [ ] **Step 2: Run test**

Run: `pytest tests/test_news.py -v`
Expected: fail (`ModuleNotFoundError`).

- [ ] **Step 3: Create `catalysts/news.py`**

```python
"""News fetchers: yfinance Ticker.news and Google News RSS."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import quote_plus

import feedparser
import requests
import yfinance as yf

from catalysts.types import RawCatalyst

GNEWS_URL = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def fetch_gnews_rss(tickers: Iterable[str]) -> list[RawCatalyst]:
    out: list[RawCatalyst] = []
    for t in tickers:
        q = quote_plus(f'"{t}" (merger OR acquisition OR partnership OR "in talks")')
        try:
            r = requests.get(GNEWS_URL.format(q=q), timeout=10)
            r.raise_for_status()
            feed = feedparser.parse(r.content)
            for entry in feed.entries[:20]:
                guid = getattr(entry, "id", None) or getattr(entry, "link", "")
                published = getattr(entry, "published", "") or ""
                try:
                    dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                    published = dt.isoformat(timespec="seconds")
                except Exception:
                    published = datetime.now(timezone.utc).isoformat(timespec="seconds")
                out.append(RawCatalyst(
                    ticker=t, source="gnews",
                    source_id=f"gnews:{t}:{guid}",
                    headline=entry.title,
                    url=getattr(entry, "link", ""),
                    published_at=published,
                ))
        except Exception as ex:
            print(f"[gnews] {t}: {ex}")
        time.sleep(0.1)
    return out


def fetch_yfinance(tickers: Iterable[str]) -> list[RawCatalyst]:
    out: list[RawCatalyst] = []
    for t in tickers:
        try:
            raw = yf.Ticker(t).news or []
        except Exception as ex:
            print(f"[yfinance] {t}: {ex}")
            continue
        for item in raw:
            title = item.get("title") or ""
            link = item.get("link") or ""
            uid = item.get("uuid") or link
            ts = item.get("providerPublishTime")
            if ts:
                published = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")
            else:
                published = datetime.now(timezone.utc).isoformat(timespec="seconds")
            if not title or not link:
                continue
            out.append(RawCatalyst(
                ticker=t, source="yfinance",
                source_id=f"yfinance:{t}:{uid}",
                headline=title, url=link, published_at=published,
            ))
    return out
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_news.py -v`
Expected: 2 passed.

- [ ] **Step 5: Wire news fetchers into `catalyst_poller.py`**

In `catalyst_poller.py`, change the import block to add:

```python
from catalysts import edgar, news, score
```

And update the fetch section inside `run_once`:

```python
    raw: list[RawCatalyst] = []
    raw += edgar.fetch(tickers, since_hours=2)
    raw += news.fetch_yfinance(tickers)
    raw += news.fetch_gnews_rss(tickers)
    print(f"[poller] fetched {len(raw)} raw items")
```

- [ ] **Step 6: Manual smoke test**

Run: `python catalyst_poller.py --dry-run`
Expected: the raw count is higher than before; JSON output includes items with `source` = `yfinance` and `gnews`.

- [ ] **Step 7: Commit**

```bash
git add catalysts/news.py catalyst_poller.py tests/test_news.py
git commit -m "feat(catalyst-radar): yfinance and Google News RSS fetchers"
```

---

## Task 9: LLM re-ranker + score fusion

**Files:**
- Create: `catalysts/rerank.py`
- Create: `tests/test_rerank.py`
- Modify: `catalyst_poller.py`

- [ ] **Step 1: Write failing test `tests/test_rerank.py`**

```python
from catalysts import rerank
from catalysts.types import RawCatalyst, ScoredItem


def _scored(headline: str, kw: int = 30) -> ScoredItem:
    raw = RawCatalyst("NVDA", "gnews", f"gnews:NVDA:{headline[:20]}",
                      headline, "https://x", "2026-04-13T10:00:00Z", None)
    return ScoredItem(raw, kw, ("m&a-rumor",), ("in talks to",))


def test_rerank_fuses_scores(monkeypatch):
    def fake_call(batch):
        return [{"id": i, "score": 8, "rationale": "strong M&A signal",
                 "tags": ["m&a-rumor"]} for i, _ in enumerate(batch)]
    monkeypatch.setattr(rerank, "_call_claude", fake_call)
    items = [_scored("ACME in talks to acquire Widgets", kw=40)]
    out = rerank.rerank_batched(items, batch=10)
    assert len(out) == 1
    r = out[0]
    # final = round(0.6*40 + 0.4*80) = round(24 + 32) = 56
    assert r.final_score == 56
    assert r.llm_score == 8
    assert r.rationale == "strong M&A signal"


def test_rerank_empty_input():
    assert rerank.rerank_batched([], batch=10) == []


def test_rerank_respects_daily_cap(monkeypatch):
    monkeypatch.setenv("MAX_RERANK_CALLS_PER_DAY", "0")
    calls = []
    monkeypatch.setattr(rerank, "_call_claude",
                        lambda batch: calls.append(batch) or [])
    out = rerank.rerank_batched([_scored("x")], batch=10)
    assert out[0].llm_score is None
    assert out[0].final_score == out[0].scored.kw_score
    assert calls == []
```

- [ ] **Step 2: Run test**

Run: `pytest tests/test_rerank.py -v`
Expected: fail (`ModuleNotFoundError`).

- [ ] **Step 3: Create `catalysts/rerank.py`**

```python
"""Claude Haiku 4.5 re-ranker.

Pass 2 of the scoring pipeline. Pure function above the `_call_claude`
boundary, which is stubbed in tests. Enforces a daily call cap via the
MAX_RERANK_CALLS_PER_DAY env var (persisted in a small counter file so
it survives process restarts within the same UTC day).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from catalysts.types import RerankedItem, ScoredItem

_COUNTER_FILE = Path(__file__).resolve().parent.parent / ".rerank_counter.json"
_MODEL = "claude-haiku-4-5-20251001"

_SYSTEM = (
    "You are an M&A and catalyst analyst. For each headline, rate 0-10 how "
    "likely it signals a near-term material event (M&A, activist stake, major "
    "partnership, tech/product launch with revenue impact). Ignore routine "
    "press. Rationale must be <= 25 words. Output JSON only."
)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_counter() -> tuple[str, int]:
    try:
        data = json.loads(_COUNTER_FILE.read_text())
        return data.get("day", ""), int(data.get("calls", 0))
    except Exception:
        return "", 0


def _save_counter(day: str, calls: int) -> None:
    _COUNTER_FILE.write_text(json.dumps({"day": day, "calls": calls}))


def _cap() -> int:
    try:
        return int(os.environ.get("MAX_RERANK_CALLS_PER_DAY", "200"))
    except ValueError:
        return 200


def _fuse(kw: int, llm: int | None) -> int:
    if llm is None:
        return kw
    return round(0.6 * kw + 0.4 * (llm * 10))


def _kw_only(s: ScoredItem) -> RerankedItem:
    return RerankedItem(scored=s, llm_score=None, rationale=None, final_score=s.kw_score)


def _call_claude(batch: list[tuple[int, ScoredItem]]) -> list[dict]:
    """Real Anthropic call. Returns list of {id, score, rationale, tags}."""
    from anthropic import Anthropic
    client = Anthropic()
    user = json.dumps([
        {"id": i, "ticker": s.raw.ticker, "headline": s.raw.headline,
         "source": s.raw.source, "form_type": s.raw.form_type}
        for i, s in batch
    ])
    resp = client.messages.create(
        model=_MODEL,
        max_tokens=1024,
        system=_SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    text = resp.content[0].text.strip()
    # Tolerate code fences
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return json.loads(text)


def rerank_batched(items: Iterable[ScoredItem], batch: int = 10) -> list[RerankedItem]:
    items = list(items)
    if not items:
        return []

    day, calls = _load_counter()
    if day != _today():
        day, calls = _today(), 0

    cap = _cap()
    out: list[RerankedItem] = []
    i = 0
    while i < len(items):
        chunk = items[i : i + batch]
        if calls >= cap:
            out.extend(_kw_only(s) for s in chunk)
            i += batch
            continue
        indexed = list(enumerate(chunk))
        try:
            results = _call_claude(indexed)
            calls += 1
            by_id = {r["id"]: r for r in results}
        except Exception as ex:
            print(f"[rerank] call failed: {ex}")
            out.extend(_kw_only(s) for s in chunk)
            i += batch
            continue

        for idx, s in indexed:
            r = by_id.get(idx)
            if not r:
                out.append(_kw_only(s))
                continue
            llm_score = int(r.get("score", 0))
            rationale = (r.get("rationale") or "")[:200]
            out.append(RerankedItem(
                scored=s,
                llm_score=llm_score,
                rationale=rationale,
                final_score=_fuse(s.kw_score, llm_score),
            ))
        i += batch

    _save_counter(day, calls)
    return out
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_rerank.py -v`
Expected: 3 passed.

- [ ] **Step 5: Wire re-ranker into `catalyst_poller.py`**

Replace the scoring block in `run_once`:

```python
    scored = [score.score_item(r) for r in fresh]
    rerank_pool = [s for s in scored if s.kw_score >= 20]
    reranked_map = {id(s): r for s, r in zip(rerank_pool,
                    rerank.rerank_batched(rerank_pool, batch=10))}
    reranked: list[RerankedItem] = []
    for s in scored:
        r = reranked_map.get(id(s))
        if r is not None:
            reranked.append(r)
        else:
            reranked.append(_to_reranked_kw_only(s))
```

And add to imports at top of `catalyst_poller.py`:

```python
from catalysts import rerank
```

- [ ] **Step 6: Add `.rerank_counter.json` to `.gitignore`**

Append to `.gitignore`:

```
.rerank_counter.json
```

- [ ] **Step 7: Manual smoke test**

Run: `python catalyst_poller.py --dry-run`
If any items have `kw_score >= 20`, expect real Anthropic calls; otherwise no calls. JSON output should still be well-formed either way.

- [ ] **Step 8: Commit**

```bash
git add catalysts/rerank.py tests/test_rerank.py catalyst_poller.py .gitignore
git commit -m "feat(catalyst-radar): Claude Haiku re-ranker with daily cap and fusion"
```

---

## Task 10: Alert dispatcher + email channel

**Files:**
- Create: `alerts/email.py`
- Create: `alerts/dispatcher.py`
- Create: `tests/test_dispatcher.py`

- [ ] **Step 1: Write failing test `tests/test_dispatcher.py`**

```python
from alerts import dispatcher
from catalysts.types import RawCatalyst, ScoredItem, RerankedItem


def _item(score: int = 90) -> RerankedItem:
    raw = RawCatalyst("NVDA", "gnews", "gnews:NVDA:x", "in talks to acquire",
                      "https://x", "2026-04-13T10:00:00Z", None)
    return RerankedItem(ScoredItem(raw, 60, ("m&a-rumor",), ("in talks to",)),
                        llm_score=9, rationale="clear rumor", final_score=score)


def test_all_channels_called(monkeypatch):
    sent = []

    class Stub:
        NAME = "stub"
        @staticmethod
        def send(**kw):
            sent.append(kw)

    class Stub2:
        NAME = "stub2"
        @staticmethod
        def send(**kw):
            sent.append(kw)

    monkeypatch.setattr(dispatcher, "_CHANNELS", (Stub, Stub2))
    ok, channels = dispatcher.send(_item())
    assert ok is True
    assert channels == ["stub", "stub2"]
    assert len(sent) == 2


def test_partial_failure(monkeypatch):
    class OK:
        NAME = "ok"
        @staticmethod
        def send(**kw): pass

    class Bad:
        NAME = "bad"
        @staticmethod
        def send(**kw): raise RuntimeError("boom")

    monkeypatch.setattr(dispatcher, "_CHANNELS", (OK, Bad))
    ok, channels = dispatcher.send(_item())
    assert ok is False
    assert channels == ["ok"]
```

- [ ] **Step 2: Run test**

Run: `pytest tests/test_dispatcher.py -v`
Expected: fail (`ModuleNotFoundError`).

- [ ] **Step 3: Create `alerts/email.py`**

```python
"""Gmail SMTP channel using stdlib EmailMessage (header-injection safe)."""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

NAME = "email"


def send(*, subject: str, headline: str, rationale: str | None, url: str,
         source: str, published_at: str, **_) -> None:
    user = os.environ["GMAIL_USER"]
    pw = os.environ["GMAIL_APP_PW"]
    to = os.environ["ALERT_TO_EMAIL"]

    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = to
    msg["Subject"] = subject  # EmailMessage sanitizes newlines
    body = (
        f"{headline}\n\n"
        f"{rationale or ''}\n\n"
        f"Source: {source}    Published: {published_at}\n"
        f"{url}\n"
    )
    msg.set_content(body)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as s:
        s.login(user, pw)
        s.send_message(msg)
```

- [ ] **Step 4: Create `alerts/dispatcher.py`**

```python
"""Channel-agnostic alert dispatcher."""
from __future__ import annotations

import logging

from alerts import email
from catalysts.types import RerankedItem

log = logging.getLogger("alerts")

_CHANNELS = (email,)  # discord appended in Task 11


def send(item: RerankedItem) -> tuple[bool, list[str]]:
    sent: list[str] = []
    ok = True
    subject = f"[{item.ticker}] {(item.tags[0] if item.tags else 'catalyst')} " \
              f"\u2014 score {item.final_score}"
    for channel in _CHANNELS:
        try:
            channel.send(
                subject=subject,
                headline=item.headline,
                rationale=item.rationale,
                url=item.url,
                source=item.source,
                published_at=item.published_at,
            )
            sent.append(channel.NAME)
        except Exception as ex:
            ok = False
            log.warning("alert channel %s failed: %s", channel.NAME, ex)
    return ok, sent
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_dispatcher.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add alerts/email.py alerts/dispatcher.py tests/test_dispatcher.py
git commit -m "feat(catalyst-radar): alert dispatcher + Gmail SMTP email channel"
```

---

## Task 11: Discord webhook channel

**Files:**
- Create: `alerts/discord.py`
- Modify: `alerts/dispatcher.py`
- Create: `tests/test_discord.py`

- [ ] **Step 1: Write failing test `tests/test_discord.py`**

```python
from alerts import discord


def test_discord_posts_embed(monkeypatch):
    captured = {}

    class R:
        status_code = 204
        def raise_for_status(self): pass

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["payload"] = json
        return R()

    monkeypatch.setattr(discord.requests, "post", fake_post)
    discord.send(
        subject="[NVDA] m&a-rumor — score 88",
        headline="NVDA in talks to acquire X",
        rationale="strong rumor signal",
        url="https://news.example.com/a",
        source="gnews",
        published_at="2026-04-13T10:00:00Z",
    )
    assert captured["url"] == "https://discord.test/webhook"
    embed = captured["payload"]["embeds"][0]
    assert "NVDA" in embed["title"]
    assert embed["description"] == "strong rumor signal"
```

- [ ] **Step 2: Run test**

Run: `pytest tests/test_discord.py -v`
Expected: fail (`ModuleNotFoundError`).

- [ ] **Step 3: Create `alerts/discord.py`**

```python
"""Discord webhook channel — single POST with an embed."""
from __future__ import annotations

import os
import requests

NAME = "discord"


def send(*, subject: str, headline: str, rationale: str | None, url: str,
         source: str, published_at: str, **_) -> None:
    webhook = os.environ["DISCORD_WEBHOOK_URL"]

    payload = {
        "embeds": [{
            "title": subject[:256],
            "description": (rationale or "")[:2000],
            "url": url if url.startswith(("http://", "https://")) else None,
            "fields": [
                {"name": "Headline", "value": headline[:900]},
                {"name": "Source", "value": f"{source} · {published_at}", "inline": True},
            ],
            "color": 15158332,  # red
        }]
    }
    r = requests.post(webhook, json=payload, timeout=10)
    r.raise_for_status()
```

- [ ] **Step 4: Register channel in `alerts/dispatcher.py`**

Change the import block and `_CHANNELS` line:

```python
from alerts import discord, email

_CHANNELS = (email, discord)
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_discord.py tests/test_dispatcher.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add alerts/discord.py alerts/dispatcher.py tests/test_discord.py
git commit -m "feat(catalyst-radar): Discord webhook alert channel"
```

---

## Task 12: Poller end-to-end — alerts, dedup, integration test

**Files:**
- Modify: `catalyst_poller.py`
- Create: `tests/test_poller_integration.py`
- Create: `tests/test_dedup.py`

- [ ] **Step 1: Write failing test `tests/test_dedup.py`**

```python
from catalysts import db as cdb
from catalysts.dedup import filter_unseen, recently_alerted
from catalysts.types import RawCatalyst, ScoredItem, RerankedItem


def _mk() -> RerankedItem:
    raw = RawCatalyst("NVDA", "gnews", "gnews:NVDA:1", "in talks",
                      "https://x", "2026-04-13T10:00:00Z", None)
    return RerankedItem(ScoredItem(raw, 60, ("m&a-rumor",), ("in talks",)),
                        llm_score=9, rationale="rumor", final_score=85)


def test_filter_unseen(tmp_db):
    cdb.migrate(tmp_db)
    item = _mk()
    assert len(filter_unseen(tmp_db, [item.scored.raw])) == 1
    cdb.persist_catalyst(tmp_db, item)
    assert len(filter_unseen(tmp_db, [item.scored.raw])) == 0


def test_recently_alerted(tmp_db):
    cdb.migrate(tmp_db)
    assert recently_alerted(tmp_db, "NVDA", 8, hours=6) is False
    tmp_db.execute(
        "INSERT INTO alert_log(catalyst_id,ticker,score_bucket,channels,sent_at,ok) "
        "VALUES(1,'NVDA',8,'[\"email\"]', datetime('now'),1)"
    )
    tmp_db.commit()
    assert recently_alerted(tmp_db, "NVDA", 8, hours=6) is True
    assert recently_alerted(tmp_db, "AAPL", 8, hours=6) is False
```

- [ ] **Step 2: Update `catalyst_poller.py` with persistence, alerts, logging, `--force-alert`**

Replace the whole body of `run_once` with:

```python
def run_once(dry_run: bool = False, force_alert: bool = False) -> int:
    load_dotenv()
    conn = cdb.connect()
    cdb.migrate(conn)

    tickers = cdb.load_active_universe(conn)
    if not tickers:
        print("[poller] no active tickers")
        return 0

    raw: list[RawCatalyst] = []
    raw += edgar.fetch(tickers, since_hours=2)
    raw += news.fetch_yfinance(tickers)
    raw += news.fetch_gnews_rss(tickers)
    print(f"[poller] fetched {len(raw)} raw items")

    fresh = filter_unseen(conn, raw)
    print(f"[poller] {len(fresh)} new after dedup")

    scored = [score.score_item(r) for r in fresh]
    pool = [s for s in scored if s.kw_score >= 20]
    rr_map = {id(s): r for s, r in zip(pool, rerank.rerank_batched(pool, batch=10))}
    reranked = [rr_map.get(id(s)) or _to_reranked_kw_only(s) for s in scored]

    if dry_run:
        print(json.dumps([
            {"ticker": i.ticker, "score": i.final_score, "tags": list(i.tags),
             "headline": i.headline, "rationale": i.rationale}
            for i in reranked
        ], indent=2))
        return 0

    alerts_sent = 0
    for item in reranked:
        cid = cdb.persist_catalyst(conn, item)
        should_alert = force_alert or (
            item.final_score >= 70 and item.llm_score is not None
        )
        if not should_alert:
            continue
        bucket = item.final_score // 10
        if recently_alerted(conn, item.ticker, bucket, hours=6):
            continue
        ok, channels = dispatcher.send(item)
        conn.execute(
            "INSERT INTO alert_log(catalyst_id,ticker,score_bucket,channels,sent_at,ok) "
            "VALUES(?,?,?,?,datetime('now'),?)",
            (cid, item.ticker, bucket, json.dumps(channels), 1 if ok else 0),
        )
        conn.commit()
        alerts_sent += 1

    print(f"[poller] persisted {len(reranked)} catalysts, {alerts_sent} alerts sent")
    return 0
```

And update imports and `main`:

```python
from catalysts.dedup import filter_unseen, recently_alerted
from alerts import dispatcher
```

```python
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force-alert", action="store_true")
    args = ap.parse_args()
    return run_once(dry_run=args.dry_run, force_alert=args.force_alert)
```

- [ ] **Step 3: Write failing test `tests/test_poller_integration.py`**

```python
from catalyst_poller import run_once
from catalysts import db as cdb, edgar, news, rerank
from catalysts.types import RawCatalyst
from alerts import dispatcher


def _stub_fetchers(monkeypatch, items):
    monkeypatch.setattr(edgar, "fetch", lambda *a, **k: items)
    monkeypatch.setattr(news, "fetch_yfinance", lambda *a, **k: [])
    monkeypatch.setattr(news, "fetch_gnews_rss", lambda *a, **k: [])


def _stub_rerank(monkeypatch):
    def _rr(items, batch=10):
        from catalysts.types import RerankedItem
        return [RerankedItem(scored=s, llm_score=9,
                             rationale="rumor", final_score=85) for s in items]
    monkeypatch.setattr(rerank, "rerank_batched", _rr)


def _stub_alerts(monkeypatch, calls):
    monkeypatch.setattr(dispatcher, "send",
                        lambda item: (calls.append(item) or (True, ["stub"])))


def test_end_to_end_idempotent(monkeypatch, tmp_path):
    monkeypatch.setattr(cdb, "DB_PATH", tmp_path / "d.db")
    conn = cdb.connect(tmp_path / "d.db")
    cdb.migrate(conn)
    cdb.upsert_universe(conn, "NVDA")
    conn.close()

    raw = [RawCatalyst("NVDA", "edgar", "edgar:NVDA:acc1",
                       "NVDA to acquire Widgets", "https://sec.gov/x",
                       "2026-04-13T10:00:00Z", "8-K")]
    _stub_fetchers(monkeypatch, raw)
    _stub_rerank(monkeypatch)
    calls: list = []
    _stub_alerts(monkeypatch, calls)

    run_once()
    run_once()  # second run must be a no-op for persistence AND alerts

    conn = cdb.connect(tmp_path / "d.db")
    n_cat = conn.execute("SELECT COUNT(*) FROM catalysts").fetchone()[0]
    n_alert = conn.execute("SELECT COUNT(*) FROM alert_log").fetchone()[0]
    conn.close()
    assert n_cat == 1
    assert n_alert == 1  # 6h dedup suppressed the second
    assert len(calls) == 1
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_dedup.py tests/test_poller_integration.py -v`
Expected: all pass.

- [ ] **Step 5: Manual smoke test with `--force-alert`** (requires real `.env`)

Run: `python catalyst_poller.py --force-alert`
Expected: one email lands in `ALERT_TO_EMAIL` inbox, one message lands in Discord channel. (Run this only once — subsequent invocations within 6h will be deduped unless a new catalyst appears.)

- [ ] **Step 6: Commit**

```bash
git add catalyst_poller.py tests/test_dedup.py tests/test_poller_integration.py
git commit -m "feat(catalyst-radar): wire dispatcher + 6h dedup into poller, integration test"
```

---

## Task 13: Windows Task Scheduler wire-up (runbook + verification)

**Files:**
- Create: `docs/catalyst-poller-setup.md`

- [ ] **Step 1: Create `docs/catalyst-poller-setup.md`**

```markdown
# Catalyst poller — Windows Task Scheduler setup

## 1. Secrets
Copy `.env.example` to `.env` and fill in:
- `GMAIL_USER`, `GMAIL_APP_PW` — a **dedicated** alerts Gmail account with an App Password enabled. Never a personal Gmail.
- `ALERT_TO_EMAIL` — where alerts are delivered.
- `DISCORD_WEBHOOK_URL` — create a private Discord server, make a `#dealscout-alerts` channel, then Channel Settings → Integrations → Webhooks → New Webhook → Copy URL.
- `ANTHROPIC_API_KEY` — from https://console.anthropic.com.
- `MAX_RERANK_CALLS_PER_DAY` — leave at 200.
- `SEC_USER_AGENT` — `Dealscout/1.0 your.contact@example.com`.

## 2. Pre-flight
```
python catalyst_poller.py --dry-run
python catalyst_poller.py --force-alert     # one email + one Discord message
```

## 3. Schedule
From an **elevated** cmd prompt (replace absolute paths):

```
schtasks /Create /SC MINUTE /MO 15 /TN "Dealscout-Poller" ^
  /TR "\"C:\Path\To\python.exe\" \"C:\Users\mwill\OneDrive\Documents\mwilliams2733\Dealscout\catalyst_poller.py\"" ^
  /RL LIMITED /F
```

## 4. Verify
```
schtasks /Query /TN "Dealscout-Poller" /V /FO LIST
```
After 15 min:
```
schtasks /Run /TN "Dealscout-Poller"
```
Then open Streamlit, go to Catalysts, and confirm new rows.

## 5. Logs
Poller `print` output is captured in Task Scheduler history (Event Viewer → Task Scheduler → Operational) and nowhere else. If you want file logging, add `python catalyst_poller.py >> poller.log 2>&1` in a `.bat` wrapper.

## 6. Kill
```
schtasks /Delete /TN "Dealscout-Poller" /F
```
```

- [ ] **Step 2: Follow the runbook manually.** Run the pre-flight, create the scheduled task, verify it ran, check Catalysts page.

- [ ] **Step 3: Commit**

```bash
git add docs/catalyst-poller-setup.md
git commit -m "docs(catalyst-radar): Task Scheduler setup runbook"
```

---

## Task 14: Sidebar badge + Dashboard catalyst column

**Files:**
- Modify: `app.py`

- [ ] **Step 1: Add the sidebar badge and caption**

Near the top of `app.py` (after the `ACTIVE_TICKERS = ...` line from Task 3), compute:

```python
_unseen = cdb.unseen_alert_count(_conn)
_last_poll = cdb.last_poll_time(_conn)
```

Change the radio options line to:

```python
_badge = f" 🔴 {_unseen}" if _unseen else ""
page = st.sidebar.radio("Navigate",
    ["Dashboard", "Catalysts" + _badge, "Power Gauge", "Holdings", "Trades", "Performance", "Universe"])
st.sidebar.caption(f"Universe: {len(ACTIVE_TICKERS)} tickers")
st.sidebar.caption(f"Last catalyst poll: {_last_poll or '—'}")
```

Then change the page-compare checks everywhere from `page == "Catalysts"` to `page.startswith("Catalysts")` (same for no other pages — the badge suffix only attaches to Catalysts). The remaining `elif page == "..."` branches stay unchanged.

- [ ] **Step 2: Add the Catalyst column to the Dashboard view**

Locate the Dashboard block (line ~38 onwards in `app.py`). After `view = returns_df.copy()` but before the rename, add:

```python
        cat_rows = _conn.execute(
            """SELECT ticker, MAX(final_score) AS cat
               FROM catalysts
               WHERE datetime(published_at) >= datetime('now','-24 hours')
               GROUP BY ticker"""
        ).fetchall()
        cat_map = {r["ticker"]: r["cat"] for r in cat_rows}
        view["catalyst"] = view["ticker"].map(cat_map).fillna(0).astype(int)
```

Then in the `rename` call, add `"catalyst": "Catalyst"`, and in the `style.format`, add `"Catalyst": "{:d}"`.

- [ ] **Step 3: Manual smoke test**

Run: `streamlit run app.py`
- Confirm sidebar shows "Last catalyst poll: 2026-..."
- If unseen high-score rows exist, confirm `🔴 N` appears on Catalysts in the radio.
- Confirm Dashboard table has a Catalyst column with 24h max final_scores (0 for tickers with no recent catalysts).
- Open Catalysts page — after the page renders, navigate back to Dashboard. Sidebar badge should drop (because `mark_seen` fired).

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "feat(catalyst-radar): sidebar badge, last-poll caption, Dashboard catalyst column"
```

---

## Task 15: Final verification and branch cleanup

- [ ] **Step 1: Run full test suite**

Run: `pytest -v`
Expected: all tests pass.

- [ ] **Step 2: Lint-grade read of the diff**

Run: `git diff main...HEAD --stat`
Confirm: no unexpected files, no `.env` committed, no `dealscout.db` committed, no `.rerank_counter.json` committed.

- [ ] **Step 3: Verify `.env` not in git**

Run: `git ls-files | grep -E "^\.env$"`
Expected: empty output.

- [ ] **Step 4: Confirm poller still runs end-to-end**

Run: `python catalyst_poller.py --dry-run`
Expected: JSON output, no tracebacks.

- [ ] **Step 5: Optional — invoke `superpowers:finishing-a-development-branch`** to decide whether to merge, open a PR, or keep iterating.

---

## Self-review

**Spec coverage (walked section-by-section against `docs/superpowers/specs/2026-04-13-catalyst-radar-design.md`):**

- §3 decisions D1–D13 → all honored. D1 (EDGAR+yf+gnews) Tasks 5/8. D2 (15 min scheduler) Task 13. D3 (email+discord) Tasks 10/11. D4/D5 (kw + Haiku re-ranker) Tasks 4/9. D6 (≥70 gate) Task 12. D7 (6h dedup) Task 12. D8 (universe-wide alerts) Task 12 — alerts fire for any active-universe ticker. D9 (Universe page now) Task 3. D10 (dedicated Gmail) Task 13 runbook. D11 (Discord private server) Task 13. D12 (cap=200) Task 9. D13 (fresh start, no backfill) no task needed — default behavior.
- §4 architecture (Streamlit reader only) → enforced by Tasks 3/6/14 never calling fetchers.
- §5 file layout → every file mapped to a task.
- §6 data model → Task 2 schema matches spec §6 exactly (incl. `seen` column).
- §7 scoring pipeline (7.1–7.4) → Tasks 4 (pass 1) and 9 (pass 2 + fusion + interface stability).
- §8 poller flow → Task 12.
- §9 alerts → Tasks 10/11.
- §10 security posture → env vars (Task 1), `defusedxml` (Task 5), `EmailMessage` (Task 10), URL scheme validation (Task 6), parameterized SQL (Task 2), regex length cap + compile validation (Task 4), no `unsafe_allow_html` (Task 6).
- §11 UI → Tasks 3, 6, 14.
- §12 testing → Tasks 4 (golden), 9 (rerank), 10/11 (dispatcher), 12 (integration + dedup).
- §13 rollout order → Tasks map 1:1 onto spec rollout steps 1–7.
- §14 cost envelope → enforced by cap in Task 9.
- §15 out of scope → nothing crept in.

**Placeholder scan:** no TBD/TODO/"similar to"/"handle appropriately" remain. Every code step is a full code block. Every test step shows the assertion.

**Type consistency:** `RawCatalyst`, `ScoredItem`, `RerankedItem`, `score_item`, `rerank_batched`, `filter_unseen`, `recently_alerted`, `persist_catalyst`, `load_active_universe`, `upsert_universe`, `deactivate_ticker`, `seed_universe_if_empty`, `unseen_alert_count`, `last_poll_time`, `dispatcher.send`, `_CHANNELS`, `NAME` — each name is defined in one task and imported with that exact spelling in every consumer. Property passthroughs on `RerankedItem` (`ticker`, `headline`, `url`, `source`, `published_at`, `tags`) match the keyword arguments the dispatcher passes to channel `send` functions.
