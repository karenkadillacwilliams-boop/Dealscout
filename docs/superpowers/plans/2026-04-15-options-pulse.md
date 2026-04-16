# Options Pulse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface cheap calls/puts (ask < $2, 7-28 DTE) ranked by catalyst-weighted leverage + IV rank for the 45-ticker universe, enriching existing catalyst alerts with an options line.

**Architecture:** Polygon Options Starter chain snapshot -> client-side filter -> composite scoring (leverage × catalyst_score + IV rank tiebreaker) -> SQLite persistence -> Streamlit Options Pulse tab + Dashboard badge + alert enrichment.

**Tech Stack:** Python 3.11+, Streamlit, SQLite (WAL mode), Polygon REST API (urllib), pytest.

---

## File map

### New files

| File | Responsibility |
|------|---------------|
| `catalysts/options.py` | Polygon fetcher: per-ticker chain snapshot, client-side DTE/ask/greeks filter |
| `catalysts/iv_rank.py` | IV history persistence + percentile rank computation |
| `catalysts/options_score.py` | Leverage ratio + composite scoring |
| `tests/test_options.py` | Unit tests for fetcher + filter logic |
| `tests/test_iv_rank.py` | Unit tests for IV rank computation |
| `tests/test_options_score.py` | Unit tests for scoring math |
| `tests/test_options_integration.py` | End-to-end: mock Polygon -> fetch -> score -> DB -> alert enrichment |
| `tests/fixtures/polygon_snapshot.py` | Reusable mock Polygon responses |

### Modified files

| File | Changes |
|------|---------|
| `catalysts/db.py` | Add `options_snapshot` + `iv_history` tables to SCHEMA; add CRUD helpers |
| `catalyst_poller.py` | Orchestrate options fetch after catalyst pipeline |
| `alerts/dispatcher.py` | Accept optional `options_summary` kwarg, append to alert body |
| `alerts/email.py` | Accept optional `options_summary` kwarg, append to body |
| `alerts/discord.py` | Accept optional `options_summary` kwarg, add embed field |
| `app.py` | Options Pulse page, Dashboard options column, sidebar badge |
| `.env.example` | Add `POLYGON_API_KEY` placeholder |
| `tests/conftest.py` | Add `POLYGON_API_KEY` to autouse env fixture |
| `requirements.txt` | No changes needed (urllib + json are stdlib) |

---

### Task 1: DB schema — options_snapshot and iv_history tables

**Files:**
- Modify: `catalysts/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write failing tests for new tables**

Add to `tests/test_db.py`:

```python
def test_options_snapshot_table_exists(tmp_db):
    from catalysts import db as cdb
    cdb.migrate(tmp_db)
    tmp_db.execute("SELECT id, ticker, contract_ticker, contract_type, strike, "
                   "expiration_date, dte, ask, bid, mid, volume, open_interest, "
                   "iv, delta, gamma, theta, vega, underlying_price, "
                   "leverage_ratio, iv_rank, composite_score, fetched_at "
                   "FROM options_snapshot LIMIT 1")


def test_iv_history_table_exists(tmp_db):
    from catalysts import db as cdb
    cdb.migrate(tmp_db)
    tmp_db.execute("SELECT ticker, date, avg_iv FROM iv_history LIMIT 1")


def test_upsert_option_snapshot(tmp_db):
    from catalysts import db as cdb
    cdb.migrate(tmp_db)
    row = {
        "ticker": "AAPL", "contract_ticker": "O:AAPL260425C00200000",
        "contract_type": "call", "strike": 200.0, "expiration_date": "2026-04-25",
        "dte": 10, "ask": 1.50, "bid": 1.40, "mid": 1.45,
        "volume": 500, "open_interest": 2000,
        "iv": 0.42, "delta": 0.35, "gamma": 0.02, "theta": -0.05, "vega": 0.10,
        "underlying_price": 195.0, "leverage_ratio": 3.33, "iv_rank": 22.0,
        "composite_score": 7.6, "fetched_at": "2026-04-15T12:00:00Z",
    }
    cdb.upsert_option_snapshot(tmp_db, row)
    r = tmp_db.execute("SELECT * FROM options_snapshot WHERE contract_ticker=?",
                       ("O:AAPL260425C00200000",)).fetchone()
    assert r["ask"] == 1.50
    assert r["composite_score"] == 7.6
    # upsert overwrites
    row["ask"] = 1.60
    cdb.upsert_option_snapshot(tmp_db, row)
    r = tmp_db.execute("SELECT * FROM options_snapshot WHERE contract_ticker=?",
                       ("O:AAPL260425C00200000",)).fetchone()
    assert r["ask"] == 1.60


def test_upsert_iv_history(tmp_db):
    from catalysts import db as cdb
    cdb.migrate(tmp_db)
    cdb.upsert_iv_history(tmp_db, "AAPL", "2026-04-15", 0.35)
    cdb.upsert_iv_history(tmp_db, "AAPL", "2026-04-15", 0.40)  # overwrite
    r = tmp_db.execute("SELECT avg_iv FROM iv_history WHERE ticker='AAPL' AND date='2026-04-15'").fetchone()
    assert r["avg_iv"] == 0.40


def test_prune_iv_history(tmp_db):
    from catalysts import db as cdb
    cdb.migrate(tmp_db)
    for i in range(70):
        cdb.upsert_iv_history(tmp_db, "AAPL", f"2026-01-{i+1:02d}" if i < 28 else f"2026-02-{i-27:02d}", 0.30 + i * 0.001)
    cdb.prune_iv_history(tmp_db, keep_days=60)
    n = tmp_db.execute("SELECT COUNT(*) FROM iv_history WHERE ticker='AAPL'").fetchone()[0]
    assert n <= 60
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_db.py::test_options_snapshot_table_exists tests/test_db.py::test_iv_history_table_exists tests/test_db.py::test_upsert_option_snapshot tests/test_db.py::test_upsert_iv_history tests/test_db.py::test_prune_iv_history -v`
Expected: FAIL — table does not exist / function not found

- [ ] **Step 3: Add tables to SCHEMA and CRUD helpers in catalysts/db.py**

Append to the `SCHEMA` string (after the `universe` table):

```python
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
```

Add these functions to `catalysts/db.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_db.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add catalysts/db.py tests/test_db.py
git commit -m "feat(options-pulse): options_snapshot + iv_history tables and CRUD"
```

---

### Task 2: Polygon fetcher — catalysts/options.py

**Files:**
- Create: `catalysts/options.py`
- Create: `tests/fixtures/polygon_snapshot.py`
- Create: `tests/test_options.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Create test fixture — mock Polygon response**

Create `tests/fixtures/polygon_snapshot.py`:

```python
"""Canned Polygon options snapshot responses for testing."""

AAPL_SNAPSHOT = {
    "status": "OK",
    "results": [
        {
            "details": {
                "ticker": "O:AAPL260425C00200000",
                "contract_type": "call",
                "strike_price": 200.0,
                "expiration_date": "2026-04-25",
            },
            "underlying_asset": {"price": 195.0, "ticker": "AAPL"},
            "day": {"open": 1.50, "high": 1.60, "low": 1.35, "close": 1.50,
                    "volume": 500, "vwap": 1.48},
            "last_quote": {"ask": 1.55, "bid": 1.45, "ask_size": 10, "bid_size": 8},
            "open_interest": 2000,
            "implied_volatility": 0.42,
            "greeks": {"delta": 0.35, "gamma": 0.02, "theta": -0.05, "vega": 0.10},
        },
        {
            "details": {
                "ticker": "O:AAPL260425P00180000",
                "contract_type": "put",
                "strike_price": 180.0,
                "expiration_date": "2026-04-25",
            },
            "underlying_asset": {"price": 195.0, "ticker": "AAPL"},
            "day": {"open": 0.80, "high": 0.90, "low": 0.75, "close": 0.85,
                    "volume": 300, "vwap": 0.82},
            "last_quote": {"ask": 0.90, "bid": 0.80, "ask_size": 20, "bid_size": 15},
            "open_interest": 1500,
            "implied_volatility": 0.38,
            "greeks": {"delta": -0.20, "gamma": 0.015, "theta": -0.04, "vega": 0.08},
        },
        {
            "details": {
                "ticker": "O:AAPL260425C00250000",
                "contract_type": "call",
                "strike_price": 250.0,
                "expiration_date": "2026-04-25",
            },
            "underlying_asset": {"price": 195.0, "ticker": "AAPL"},
            "day": {"open": 3.00, "high": 3.20, "low": 2.90, "close": 3.10,
                    "volume": 100, "vwap": 3.05},
            "last_quote": {"ask": 3.15, "bid": 3.00, "ask_size": 5, "bid_size": 3},
            "open_interest": 800,
            "implied_volatility": 0.55,
            "greeks": {"delta": 0.15, "gamma": 0.01, "theta": -0.03, "vega": 0.06},
        },
        {
            "details": {
                "ticker": "O:AAPL260425C00210000",
                "contract_type": "call",
                "strike_price": 210.0,
                "expiration_date": "2026-04-25",
            },
            "underlying_asset": {"price": 195.0, "ticker": "AAPL"},
            "day": {"open": 0.30, "high": 0.35, "low": 0.25, "close": 0.30,
                    "volume": 1200, "vwap": 0.30},
            "last_quote": {"ask": 0.35, "bid": 0.25, "ask_size": 50, "bid_size": 40},
            "open_interest": 5000,
            "implied_volatility": 0.40,
            "greeks": {"delta": 0.12, "gamma": 0.008, "theta": -0.02, "vega": 0.04},
        },
        {
            "details": {
                "ticker": "O:AAPL260425C00205000",
                "contract_type": "call",
                "strike_price": 205.0,
                "expiration_date": "2026-04-25",
            },
            "underlying_asset": {"price": 195.0, "ticker": "AAPL"},
            "day": {"volume": 0},
            "last_quote": {},
            "open_interest": 100,
            "implied_volatility": None,
            "greeks": {},
        },
    ],
}
```

- [ ] **Step 2: Add POLYGON_API_KEY to conftest.py**

In `tests/conftest.py`, add to the `_env` fixture:

```python
    monkeypatch.setenv("POLYGON_API_KEY", "test-polygon-key")
```

- [ ] **Step 3: Write failing tests for fetcher**

Create `tests/test_options.py`:

```python
import json
from datetime import date
from unittest.mock import patch, MagicMock

from catalysts.options import fetch_chain, OptionContract
from tests.fixtures.polygon_snapshot import AAPL_SNAPSHOT


def _mock_urlopen(snapshot_data):
    resp = MagicMock()
    resp.read.return_value = json.dumps(snapshot_data).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_fetch_chain_returns_contracts():
    with patch("catalysts.options.urlopen", return_value=_mock_urlopen(AAPL_SNAPSHOT)):
        contracts = fetch_chain("AAPL", ref_date=date(2026, 4, 15))
    assert len(contracts) > 0
    assert all(isinstance(c, OptionContract) for c in contracts)


def test_fetch_chain_filters_ask_over_2():
    with patch("catalysts.options.urlopen", return_value=_mock_urlopen(AAPL_SNAPSHOT)):
        contracts = fetch_chain("AAPL", max_ask=2.00, ref_date=date(2026, 4, 15))
    asks = [c.ask for c in contracts]
    assert all(a <= 2.00 for a in asks)
    assert all(a > 0 for a in asks)


def test_fetch_chain_filters_dte_window():
    with patch("catalysts.options.urlopen", return_value=_mock_urlopen(AAPL_SNAPSHOT)):
        contracts = fetch_chain("AAPL", min_dte=7, max_dte=28, ref_date=date(2026, 4, 15))
    for c in contracts:
        assert 7 <= c.dte <= 28


def test_fetch_chain_drops_empty_greeks():
    with patch("catalysts.options.urlopen", return_value=_mock_urlopen(AAPL_SNAPSHOT)):
        contracts = fetch_chain("AAPL", ref_date=date(2026, 4, 15))
    for c in contracts:
        assert c.iv is not None


def test_fetch_chain_http_error_returns_empty():
    from urllib.error import HTTPError
    with patch("catalysts.options.urlopen", side_effect=HTTPError(
            "url", 429, "rate limit", {}, None)):
        contracts = fetch_chain("AAPL", ref_date=date(2026, 4, 15))
    assert contracts == []
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `python -m pytest tests/test_options.py -v`
Expected: FAIL — cannot import `catalysts.options`

- [ ] **Step 5: Implement catalysts/options.py**

Create `catalysts/options.py`:

```python
"""Polygon.io options chain fetcher with client-side filtering."""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

log = logging.getLogger("options")


@dataclass(frozen=True)
class OptionContract:
    ticker: str
    contract_ticker: str
    contract_type: str
    strike: float
    expiration_date: str
    dte: int
    ask: float
    bid: float
    mid: float
    volume: int
    open_interest: int
    iv: float
    delta: float
    gamma: float
    theta: float
    vega: float
    underlying_price: float


_BASE = "https://api.polygon.io/v3/snapshot/options"


def _api_key() -> str:
    return os.environ.get("POLYGON_API_KEY", "")


def fetch_chain(
    ticker: str,
    *,
    max_ask: float = 2.00,
    min_dte: int = 7,
    max_dte: int = 28,
    ref_date: Optional[date] = None,
) -> list[OptionContract]:
    key = _api_key()
    if not key:
        log.warning("POLYGON_API_KEY not set, skipping options fetch")
        return []

    today = ref_date or date.today()
    exp_gte = (today + timedelta(days=min_dte)).isoformat()
    exp_lte = (today + timedelta(days=max_dte)).isoformat()

    results: list[dict] = []
    url: Optional[str] = (
        f"{_BASE}/{ticker}"
        f"?expiration_date.gte={exp_gte}"
        f"&expiration_date.lte={exp_lte}"
        f"&limit=250"
        f"&apiKey={key}"
    )

    while url:
        try:
            req = Request(url)
            with urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read())
            results.extend(body.get("results", []))
            url = body.get("next_url")
            if url and "&apiKey=" not in url:
                url += f"&apiKey={key}"
        except (HTTPError, URLError, TimeoutError) as exc:
            log.warning("polygon fetch %s failed: %s", ticker, exc)
            return []

    contracts: list[OptionContract] = []
    for r in results:
        details = r.get("details", {})
        greeks = r.get("greeks", {})
        quote = r.get("last_quote", {})
        day = r.get("day", {})
        underlying = r.get("underlying_asset", {})

        iv = r.get("implied_volatility")
        if iv is None or not greeks:
            continue

        ask = quote.get("ask") or 0.0
        bid = quote.get("bid") or 0.0
        if ask <= 0 or ask > max_ask:
            continue

        exp_str = details.get("expiration_date", "")
        try:
            exp_d = date.fromisoformat(exp_str)
        except ValueError:
            continue
        dte = (exp_d - today).days
        if dte < min_dte or dte > max_dte:
            continue

        contracts.append(OptionContract(
            ticker=underlying.get("ticker", ticker),
            contract_ticker=details.get("ticker", ""),
            contract_type=details.get("contract_type", ""),
            strike=details.get("strike_price", 0.0),
            expiration_date=exp_str,
            dte=dte,
            ask=ask,
            bid=bid,
            mid=round((ask + bid) / 2, 4),
            volume=day.get("volume", 0),
            open_interest=r.get("open_interest", 0),
            iv=iv,
            delta=greeks.get("delta", 0.0),
            gamma=greeks.get("gamma", 0.0),
            theta=greeks.get("theta", 0.0),
            vega=greeks.get("vega", 0.0),
            underlying_price=underlying.get("price", 0.0),
        ))

    return contracts


def fetch_chains_batch(
    tickers: list[str],
    *,
    max_ask: float = 2.00,
    min_dte: int = 7,
    max_dte: int = 28,
    delay: float = 0.1,
) -> list[OptionContract]:
    all_contracts: list[OptionContract] = []
    for t in tickers:
        all_contracts.extend(fetch_chain(t, max_ask=max_ask, min_dte=min_dte, max_dte=max_dte))
        time.sleep(delay)
    return all_contracts
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_options.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add catalysts/options.py tests/test_options.py tests/fixtures/polygon_snapshot.py tests/conftest.py
git commit -m "feat(options-pulse): Polygon chain fetcher with DTE/ask/greeks filter"
```

---

### Task 3: IV Rank — catalysts/iv_rank.py

**Files:**
- Create: `catalysts/iv_rank.py`
- Create: `tests/test_iv_rank.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_iv_rank.py`:

```python
from catalysts.iv_rank import compute_iv_rank, compute_atm_avg_iv
from catalysts.options import OptionContract


def _contract(strike, iv, underlying=195.0):
    return OptionContract(
        ticker="AAPL", contract_ticker=f"O:AAPL260425C00{int(strike*1000):08d}",
        contract_type="call", strike=strike, expiration_date="2026-04-25",
        dte=10, ask=1.00, bid=0.90, mid=0.95, volume=100, open_interest=500,
        iv=iv, delta=0.3, gamma=0.02, theta=-0.05, vega=0.1,
        underlying_price=underlying,
    )


def test_compute_atm_avg_iv():
    contracts = [
        _contract(190.0, 0.40),  # ATM-1
        _contract(195.0, 0.42),  # ATM
        _contract(200.0, 0.44),  # ATM+1
        _contract(210.0, 0.55),  # out of range
        _contract(250.0, 0.70),  # far OTM
    ]
    avg = compute_atm_avg_iv(contracts, underlying_price=195.0)
    assert abs(avg - 0.42) < 0.01  # (0.40 + 0.42 + 0.44) / 3


def test_compute_atm_avg_iv_empty():
    assert compute_atm_avg_iv([], underlying_price=195.0) is None


def test_compute_iv_rank_with_history():
    history = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60]
    rank = compute_iv_rank(0.35, history)
    assert 10 < rank < 30  # 0.35 is near the low end


def test_compute_iv_rank_at_extremes():
    history = [0.30, 0.35, 0.40, 0.45, 0.50]
    assert compute_iv_rank(0.30, history) < 20
    assert compute_iv_rank(0.50, history) > 80


def test_compute_iv_rank_insufficient_history():
    rank = compute_iv_rank(0.40, [0.40])
    assert rank == 50.0  # default when < 5 data points
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_iv_rank.py -v`
Expected: FAIL — cannot import `catalysts.iv_rank`

- [ ] **Step 3: Implement catalysts/iv_rank.py**

Create `catalysts/iv_rank.py`:

```python
"""IV history tracking and IV rank computation."""
from __future__ import annotations

from typing import Optional, Sequence

from catalysts.options import OptionContract

_MIN_HISTORY = 5
_DEFAULT_RANK = 50.0


def compute_atm_avg_iv(
    contracts: list[OptionContract],
    underlying_price: float,
) -> Optional[float]:
    if not contracts or underlying_price <= 0:
        return None
    strikes = sorted({c.strike for c in contracts})
    if not strikes:
        return None
    atm_strike = min(strikes, key=lambda s: abs(s - underlying_price))
    atm_idx = strikes.index(atm_strike)
    lo = max(0, atm_idx - 1)
    hi = min(len(strikes), atm_idx + 2)
    nearby_strikes = set(strikes[lo:hi])
    ivs = [c.iv for c in contracts if c.strike in nearby_strikes and c.iv is not None]
    return sum(ivs) / len(ivs) if ivs else None


def compute_iv_rank(current_iv: float, history: Sequence[float]) -> float:
    if len(history) < _MIN_HISTORY:
        return _DEFAULT_RANK
    below = sum(1 for h in history if h <= current_iv)
    return round(below / len(history) * 100, 1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_iv_rank.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add catalysts/iv_rank.py tests/test_iv_rank.py
git commit -m "feat(options-pulse): IV rank computation with ATM-band averaging"
```

---

### Task 4: Composite scoring — catalysts/options_score.py

**Files:**
- Create: `catalysts/options_score.py`
- Create: `tests/test_options_score.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_options_score.py`:

```python
from catalysts.options import OptionContract
from catalysts.options_score import leverage_ratio, composite_score, rank_contracts


def _contract(strike=200.0, ask=1.50, underlying=195.0, **kw):
    defaults = dict(
        ticker="AAPL", contract_ticker="O:AAPL260425C00200000",
        contract_type="call", expiration_date="2026-04-25", dte=10,
        bid=ask - 0.10, mid=ask - 0.05, volume=500, open_interest=2000,
        iv=0.42, delta=0.35, gamma=0.02, theta=-0.05, vega=0.10,
        underlying_price=underlying,
    )
    defaults.update(kw)
    return OptionContract(strike=strike, ask=ask, **defaults)


def test_leverage_ratio():
    assert leverage_ratio(200.0, 195.0, 1.50) == abs(200.0 - 195.0) / 1.50


def test_leverage_ratio_zero_ask():
    assert leverage_ratio(200.0, 195.0, 0.0) == 0.0


def test_composite_score_basic():
    score = composite_score(leverage_ratio=3.33, catalyst_score=85, iv_rank=22.0)
    expected = (3.33 * 85 / 100) + (100 - 22.0) * 0.1
    assert abs(score - expected) < 0.01


def test_composite_score_zero_catalyst():
    score = composite_score(leverage_ratio=5.0, catalyst_score=0, iv_rank=50.0)
    assert score == (100 - 50.0) * 0.1  # only IV term


def test_rank_contracts_sorted_descending():
    c1 = _contract(strike=200.0, ask=1.50, contract_ticker="C1")
    c2 = _contract(strike=210.0, ask=0.35, contract_ticker="C2")
    ranked = rank_contracts([c1, c2], catalyst_score=85, iv_rank=22.0)
    assert ranked[0]["composite_score"] >= ranked[1]["composite_score"] or \
           ranked[0]["ask"] <= ranked[1]["ask"]


def test_rank_contracts_tiebreak_by_ask():
    c1 = _contract(strike=200.0, ask=1.50, contract_ticker="C1")
    c2 = _contract(strike=200.0, ask=1.00, contract_ticker="C2")
    ranked = rank_contracts([c1, c2], catalyst_score=85, iv_rank=22.0)
    # same leverage_ratio, lower ask wins
    assert ranked[0]["ask"] <= ranked[1]["ask"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_options_score.py -v`
Expected: FAIL — cannot import `catalysts.options_score`

- [ ] **Step 3: Implement catalysts/options_score.py**

Create `catalysts/options_score.py`:

```python
"""Composite scoring for filtered option contracts."""
from __future__ import annotations

from catalysts.options import OptionContract


def leverage_ratio(strike: float, underlying_price: float, ask: float) -> float:
    if ask <= 0:
        return 0.0
    return abs(strike - underlying_price) / ask


def composite_score(*, leverage_ratio: float, catalyst_score: int, iv_rank: float) -> float:
    return (leverage_ratio * catalyst_score / 100) + (100 - iv_rank) * 0.1


def rank_contracts(
    contracts: list[OptionContract],
    catalyst_score: int,
    iv_rank: float,
) -> list[dict]:
    rows: list[dict] = []
    for c in contracts:
        lev = leverage_ratio(c.strike, c.underlying_price, c.ask)
        comp = composite_score(
            leverage_ratio=lev, catalyst_score=catalyst_score, iv_rank=iv_rank,
        )
        rows.append({
            "ticker": c.ticker,
            "contract_ticker": c.contract_ticker,
            "contract_type": c.contract_type,
            "strike": c.strike,
            "expiration_date": c.expiration_date,
            "dte": c.dte,
            "ask": c.ask,
            "bid": c.bid,
            "mid": c.mid,
            "volume": c.volume,
            "open_interest": c.open_interest,
            "iv": c.iv,
            "delta": c.delta,
            "gamma": c.gamma,
            "theta": c.theta,
            "vega": c.vega,
            "underlying_price": c.underlying_price,
            "leverage_ratio": round(lev, 4),
            "iv_rank": iv_rank,
            "composite_score": round(comp, 4),
        })
    rows.sort(key=lambda r: (-r["composite_score"], r["ask"]))
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_options_score.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add catalysts/options_score.py tests/test_options_score.py
git commit -m "feat(options-pulse): composite scoring — leverage x catalyst + IV rank"
```

---

### Task 5: Alert enrichment — dispatcher + channels

**Files:**
- Modify: `alerts/dispatcher.py`
- Modify: `alerts/email.py`
- Modify: `alerts/discord.py`
- Modify: `tests/test_dispatcher.py`

- [ ] **Step 1: Write failing test for enriched alert**

Add to `tests/test_dispatcher.py`:

```python
def test_send_with_options_summary(monkeypatch):
    from alerts import dispatcher, email, discord
    from catalysts.types import RawCatalyst, ScoredItem, RerankedItem

    email_calls = []
    discord_calls = []
    monkeypatch.setattr(email, "send", lambda **kw: email_calls.append(kw))
    monkeypatch.setattr(discord, "send", lambda **kw: discord_calls.append(kw))

    raw = RawCatalyst("AAPL", "edgar", "acc1", "AAPL to acquire X",
                      "https://sec.gov/x", "2026-04-15T10:00:00Z", "8-K")
    scored = ScoredItem(raw=raw, kw_score=85, tags=("m&a-confirmed",), matched_phrases=("to acquire",))
    item = RerankedItem(scored=scored, llm_score=9, rationale="strong signal", final_score=85)

    options_summary = "Options: 3 calls under $2 | best: Apr 25 $200C @ $1.50 (leverage 3.3x, IV rank 22%)"
    ok, channels = dispatcher.send(item, options_summary=options_summary)

    assert ok
    assert email_calls[0]["options_summary"] == options_summary
    assert discord_calls[0]["options_summary"] == options_summary
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_dispatcher.py::test_send_with_options_summary -v`
Expected: FAIL — `send()` got unexpected keyword argument

- [ ] **Step 3: Update alerts/dispatcher.py**

Replace the `send` function in `alerts/dispatcher.py`:

```python
def send(item: RerankedItem, *, options_summary: str | None = None) -> tuple[bool, list[str]]:
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
                options_summary=options_summary,
            )
            sent.append(channel.NAME)
        except Exception as ex:
            ok = False
            log.warning("alert channel %s failed: %s", channel.NAME, ex)
    return ok, sent
```

- [ ] **Step 4: Update alerts/email.py to include options_summary in body**

Replace the `send` function in `alerts/email.py`:

```python
def send(*, subject: str, headline: str, rationale: str | None, url: str,
         source: str, published_at: str, options_summary: str | None = None, **_) -> None:
    missing = [k for k in _REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"email channel missing env vars: {', '.join(missing)}")
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
    if options_summary:
        body += f"\n{options_summary}\n"
    msg.set_content(body)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as s:
        s.login(user, pw)
        s.send_message(msg)
```

- [ ] **Step 5: Update alerts/discord.py to include options_summary as embed field**

Replace the `send` function in `alerts/discord.py`:

```python
def send(*, subject: str, headline: str, rationale: str | None, url: str,
         source: str, published_at: str, options_summary: str | None = None, **_) -> None:
    if not os.environ.get("DISCORD_WEBHOOK_URL"):
        raise RuntimeError("discord channel missing env var: DISCORD_WEBHOOK_URL")
    webhook = os.environ["DISCORD_WEBHOOK_URL"]

    fields = [
        {"name": "Headline", "value": headline[:900]},
        {"name": "Source", "value": f"{source} · {published_at}", "inline": True},
    ]
    if options_summary:
        fields.append({"name": "Options", "value": options_summary[:900]})

    payload = {
        "embeds": [{
            "title": subject[:256],
            "description": (rationale or "")[:2000],
            "url": url if url.startswith(("http://", "https://")) else None,
            "fields": fields,
            "color": 15158332,  # red
        }]
    }
    r = requests.post(webhook, json=payload, timeout=10)
    r.raise_for_status()
```

- [ ] **Step 6: Run all dispatcher/channel tests**

Run: `python -m pytest tests/test_dispatcher.py tests/test_discord.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add alerts/dispatcher.py alerts/email.py alerts/discord.py tests/test_dispatcher.py
git commit -m "feat(options-pulse): alert enrichment with options summary line"
```

---

### Task 6: Poller orchestration — wire options into catalyst_poller.py

**Files:**
- Modify: `catalyst_poller.py`
- Create: `tests/test_options_integration.py`

- [ ] **Step 1: Write integration test**

Create `tests/test_options_integration.py`:

```python
import json
from unittest.mock import patch, MagicMock

from catalyst_poller import run_once
from catalysts import db as cdb, edgar, news, rerank, options
from catalysts.types import RawCatalyst, ScoredItem, RerankedItem
from alerts import dispatcher
from tests.fixtures.polygon_snapshot import AAPL_SNAPSHOT


def _stub_fetchers(monkeypatch, items):
    monkeypatch.setattr(edgar, "fetch", lambda *a, **k: items)
    monkeypatch.setattr(news, "fetch_yfinance", lambda *a, **k: [])
    monkeypatch.setattr(news, "fetch_gnews_rss", lambda *a, **k: [])


def _stub_rerank(monkeypatch):
    def _rr(items, batch=10):
        return [RerankedItem(scored=s, llm_score=9,
                             rationale="rumor", final_score=85) for s in items]
    monkeypatch.setattr(rerank, "rerank_batched", _rr)


def _mock_urlopen(snapshot_data):
    resp = MagicMock()
    resp.read.return_value = json.dumps(snapshot_data).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_poller_fetches_options_and_enriches_alert(monkeypatch, tmp_path):
    monkeypatch.setattr(cdb, "DB_PATH", tmp_path / "d.db")
    conn = cdb.connect(tmp_path / "d.db")
    cdb.migrate(conn)
    cdb.upsert_universe(conn, "AAPL")
    conn.close()

    raw = [RawCatalyst("AAPL", "edgar", "edgar:AAPL:acc1",
                       "AAPL to acquire WidgetCo",
                       "https://sec.gov/x", "2026-04-15T10:00:00Z", "8-K")]
    _stub_fetchers(monkeypatch, raw)
    _stub_rerank(monkeypatch)

    alert_calls: list = []
    monkeypatch.setattr(dispatcher, "send",
                        lambda item, **kw: (alert_calls.append((item, kw)) or (True, ["stub"])))

    with patch("catalysts.options.urlopen", return_value=_mock_urlopen(AAPL_SNAPSHOT)):
        run_once()

    assert len(alert_calls) == 1
    _, kw = alert_calls[0]
    assert "options_summary" in kw

    conn = cdb.connect(tmp_path / "d.db")
    opts = conn.execute("SELECT COUNT(*) FROM options_snapshot").fetchone()[0]
    conn.close()
    assert opts > 0


def test_poller_survives_polygon_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(cdb, "DB_PATH", tmp_path / "d.db")
    conn = cdb.connect(tmp_path / "d.db")
    cdb.migrate(conn)
    cdb.upsert_universe(conn, "AAPL")
    conn.close()

    raw = [RawCatalyst("AAPL", "edgar", "edgar:AAPL:acc2",
                       "AAPL to acquire GizmoInc",
                       "https://sec.gov/y", "2026-04-15T11:00:00Z", "8-K")]
    _stub_fetchers(monkeypatch, raw)
    _stub_rerank(monkeypatch)

    alert_calls: list = []
    monkeypatch.setattr(dispatcher, "send",
                        lambda item, **kw: (alert_calls.append((item, kw)) or (True, ["stub"])))

    from urllib.error import HTTPError
    with patch("catalysts.options.urlopen",
               side_effect=HTTPError("url", 500, "server error", {}, None)):
        run_once()

    # catalyst alert still fires even when Polygon fails
    assert len(alert_calls) == 1
    _, kw = alert_calls[0]
    assert kw.get("options_summary") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_options_integration.py -v`
Expected: FAIL — poller doesn't import or call options code yet

- [ ] **Step 3: Update catalyst_poller.py**

Replace the entire file with the updated version. Key changes: import options modules, add `_fetch_options` helper, call it after persist, pass `options_summary` to `dispatcher.send`.

Full updated `catalyst_poller.py`:

```python
"""Catalyst Radar poller — run by Windows Task Scheduler every 15 min."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

_SHARED_ENV = Path.home() / ".secrets" / "shared.env"

from catalysts import db as cdb
from catalysts import edgar, news, score, rerank, options
from catalysts.dedup import filter_unseen, recently_alerted
from catalysts.iv_rank import compute_atm_avg_iv, compute_iv_rank
from catalysts.options_score import rank_contracts
from catalysts.types import RawCatalyst, ScoredItem, RerankedItem
from alerts import dispatcher

log = logging.getLogger("poller")


def _to_reranked_kw_only(s: ScoredItem) -> RerankedItem:
    return RerankedItem(
        scored=s, llm_score=None, rationale=None, final_score=s.kw_score,
    )


def _fetch_options(conn, tickers: list[str]) -> dict[str, str]:
    if not os.environ.get("POLYGON_API_KEY"):
        return {}

    now_str = datetime.now(timezone.utc).isoformat(timespec="seconds")
    summaries: dict[str, str] = {}

    cdb.clear_stale_options(conn)

    all_contracts = options.fetch_chains_batch(tickers, delay=0.1)
    if not all_contracts:
        return {}

    by_ticker: dict[str, list] = {}
    for c in all_contracts:
        by_ticker.setdefault(c.ticker, []).append(c)

    for ticker, contracts in by_ticker.items():
        underlying = contracts[0].underlying_price if contracts else 0.0
        avg_iv = compute_atm_avg_iv(contracts, underlying)
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        iv_rank_val = 50.0
        if avg_iv is not None:
            cdb.upsert_iv_history(conn, ticker, today_str, avg_iv)
            history_rows = conn.execute(
                "SELECT avg_iv FROM iv_history WHERE ticker=? ORDER BY date",
                (ticker,),
            ).fetchall()
            history = [r["avg_iv"] for r in history_rows]
            iv_rank_val = compute_iv_rank(avg_iv, history)

        cat_row = conn.execute(
            "SELECT MAX(final_score) AS best FROM catalysts "
            "WHERE ticker=? AND datetime(fetched_at) >= datetime('now', '-24 hours')",
            (ticker,),
        ).fetchone()
        catalyst_score = cat_row["best"] if cat_row and cat_row["best"] else 0

        ranked = rank_contracts(contracts, catalyst_score=catalyst_score, iv_rank=iv_rank_val)
        for row in ranked:
            row["fetched_at"] = now_str
            cdb.upsert_option_snapshot(conn, row)

        if ranked:
            best = ranked[0]
            exp_short = best["expiration_date"][5:]  # MM-DD
            ct = "C" if best["contract_type"] == "call" else "P"
            n_calls = sum(1 for r in ranked if r["contract_type"] == "call")
            n_puts = sum(1 for r in ranked if r["contract_type"] == "put")
            parts = []
            if n_calls:
                parts.append(f"{n_calls} call{'s' if n_calls != 1 else ''}")
            if n_puts:
                parts.append(f"{n_puts} put{'s' if n_puts != 1 else ''}")
            summaries[ticker] = (
                f"Options: {' + '.join(parts)} under $2 | "
                f"best: {exp_short} ${best['strike']}{ct} @ ${best['ask']:.2f} "
                f"(leverage {best['leverage_ratio']:.1f}x, IV rank {iv_rank_val:.0f}%)"
            )

    cdb.prune_iv_history(conn)
    return summaries


def run_once(dry_run: bool = False, force_alert: bool = False) -> int:
    if _SHARED_ENV.exists():
        load_dotenv(_SHARED_ENV)
    load_dotenv()
    conn = cdb.connect(cdb.DB_PATH)
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

    alert_tickers = set()
    persisted: list[tuple[RerankedItem, int]] = []
    for item in reranked:
        cid = cdb.persist_catalyst(conn, item)
        persisted.append((item, cid))
        if item.final_score >= 70 and item.llm_score is not None:
            alert_tickers.add(item.ticker)

    options_summaries = _fetch_options(conn, list(alert_tickers)) if alert_tickers else {}
    print(f"[poller] options summaries for {len(options_summaries)} tickers")

    alerts_sent = 0
    for item, cid in persisted:
        should_alert = force_alert or (
            item.final_score >= 70 and item.llm_score is not None
        )
        if not should_alert:
            continue
        bucket = item.final_score // 10
        if recently_alerted(conn, item.ticker, bucket, hours=6):
            continue
        summary = options_summaries.get(item.ticker)
        ok, channels = dispatcher.send(item, options_summary=summary)
        sent_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO alert_log(catalyst_id,ticker,score_bucket,channels,sent_at,ok) "
            "VALUES(?,?,?,?,?,?)",
            (cid, item.ticker, bucket, json.dumps(channels), sent_at, 1 if ok else 0),
        )
        conn.commit()
        alerts_sent += 1

    print(f"[poller] persisted {len(reranked)} catalysts, {alerts_sent} alerts sent")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force-alert", action="store_true")
    args = ap.parse_args()
    return run_once(dry_run=args.dry_run, force_alert=args.force_alert)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run integration tests**

Run: `python -m pytest tests/test_options_integration.py tests/test_poller_integration.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add catalyst_poller.py tests/test_options_integration.py
git commit -m "feat(options-pulse): wire options fetch + scoring into poller pipeline"
```

---

### Task 7: Streamlit UI — Options Pulse tab + Dashboard column + sidebar badge

**Files:**
- Modify: `app.py`
- Modify: `.env.example`

- [ ] **Step 1: Add POLYGON_API_KEY to .env.example**

Append to `.env.example`:

```
POLYGON_API_KEY=your-polygon-api-key-here
```

- [ ] **Step 2: Add sidebar options badge + Options Pulse nav entry**

In `app.py`, after the existing `_unseen` and `_last_poll` lines (around line 22), add:

```python
_total_opts = _conn.execute("SELECT COUNT(*) FROM options_snapshot").fetchone()[0]
```

Then update the sidebar radio (around line 28) to include the Options Pulse page:

```python
_cat_badge = f" 🔴 {_unseen}" if _unseen else ""
_opt_badge = f" ({_total_opts})" if _total_opts else ""
page = st.sidebar.radio("Navigate",
    ["Dashboard", "Catalysts" + _cat_badge, "Options Pulse" + _opt_badge,
     "Power Gauge", "Holdings", "Trades", "Performance", "Universe"])
```

- [ ] **Step 3: Add Options column to Dashboard**

In the Dashboard page section of `app.py`, after the catalyst column mapping (around line 71), add the options badge column:

```python
        def _opts_badge(ticker):
            r = _conn.execute(
                "SELECT "
                "SUM(CASE WHEN contract_type='call' THEN 1 ELSE 0 END) AS c, "
                "SUM(CASE WHEN contract_type='put' THEN 1 ELSE 0 END) AS p "
                "FROM options_snapshot WHERE ticker=?", (ticker,)
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

        view["options"] = view["ticker"].map(_opts_badge)
```

Add `"options": "Options"` to the rename dict and add `"Options"` to the column display.

- [ ] **Step 4: Add Options Pulse page**

Add this page block in `app.py` (after the Catalysts page block, before Power Gauge):

```python
elif page.startswith("Options Pulse"):
    st.title("Options Pulse")
    st.caption("Cheap convexity screener — calls & puts under $2, 7-28 DTE, ranked by catalyst-weighted leverage + IV rank.")

    opts_rows = cdb.load_all_options(_conn)
    if not opts_rows:
        st.info("No qualifying options found. Run the poller with POLYGON_API_KEY set.")
    else:
        import pandas as pd
        opts_df = pd.DataFrame(opts_rows)

        # Filters
        c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
        with c1:
            all_tickers = sorted(opts_df["ticker"].unique())
            sel_tickers = st.multiselect("Ticker", options=all_tickers, default=all_tickers)
        with c2:
            sel_type = st.selectbox("Type", ["All", "call", "put"])
        with c3:
            sel_dte = st.slider("Max DTE", 7, 28, 28)
        with c4:
            sel_ask = st.slider("Max ask ($)", 0.05, 2.00, 2.00, 0.05)

        mask = opts_df["ticker"].isin(sel_tickers)
        if sel_type != "All":
            mask &= opts_df["contract_type"] == sel_type
        mask &= opts_df["dte"] <= sel_dte
        mask &= opts_df["ask"] <= sel_ask
        filtered = opts_df[mask].copy()

        # Summary row
        s1, s2, s3 = st.columns(3)
        s1.metric("Qualifying contracts", len(filtered))
        cheap_vol = filtered[filtered["iv_rank"] < 30]["ticker"].nunique() if not filtered.empty else 0
        s2.metric("Cheap vol tickers (IV rank < 30)", cheap_vol)
        if not filtered.empty:
            s3.metric("Best composite", f"{filtered['composite_score'].max():.1f}")

        # Main table
        if filtered.empty:
            st.info("No contracts match current filters.")
        else:
            display = filtered[[
                "ticker", "contract_type", "strike", "expiration_date", "dte",
                "ask", "leverage_ratio", "iv", "iv_rank", "composite_score",
                "volume", "open_interest",
            ]].rename(columns={
                "ticker": "Ticker", "contract_type": "Type", "strike": "Strike",
                "expiration_date": "Exp", "dte": "DTE", "ask": "Ask",
                "leverage_ratio": "Leverage", "iv": "IV", "iv_rank": "IV Rank",
                "composite_score": "Composite", "volume": "Volume",
                "open_interest": "OI",
            })

            def _iv_color(val):
                if pd.isna(val):
                    return ""
                if val < 30:
                    return "background-color: #d4edda"
                if val <= 60:
                    return "background-color: #fff3cd"
                return "background-color: #f8d7da"

            st.dataframe(
                display.style
                    .format({
                        "Strike": "${:,.2f}", "Ask": "${:,.2f}",
                        "Leverage": "{:.1f}x", "IV": "{:.1%}",
                        "IV Rank": "{:.0f}%", "Composite": "{:.1f}",
                    })
                    .map(_iv_color, subset=["IV Rank"]),
                width="stretch", hide_index=True,
            )
```

- [ ] **Step 5: Manual verification — start Streamlit and check UI**

Run: `streamlit run app.py`

Check:
1. Sidebar shows "Options Pulse" in the nav (with count if data exists).
2. Dashboard has "Options" column (shows "—" if no options data).
3. Options Pulse page loads with filters and empty state message.
4. Run `python catalyst_poller.py --dry-run` to verify the poller doesn't crash.

- [ ] **Step 6: Commit**

```bash
git add app.py .env.example
git commit -m "feat(options-pulse): Options Pulse tab, Dashboard column, sidebar badge"
```

---

### Task 8: Full regression — run all tests

**Files:** None (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `python -m pytest tests/ -v`
Expected: all PASS. If any Phase 3 tests broke, fix before continuing.

- [ ] **Step 2: Run the poller in dry-run mode end-to-end**

Run: `python catalyst_poller.py --dry-run`
Expected: prints JSON of catalyst items, no crash. Options fetch runs if `POLYGON_API_KEY` is set.

- [ ] **Step 3: Verify UI end-to-end**

Run: `streamlit run app.py`

Check:
1. Dashboard page renders with Options column.
2. Catalysts page still works (no regressions).
3. Options Pulse page shows either data or a clean empty state.
4. All other pages (Holdings, Trades, Performance, Universe, Power Gauge) unchanged.

- [ ] **Step 4: Final commit — merge tag**

```bash
git add -A
git commit -m "test(options-pulse): full regression pass"
```
