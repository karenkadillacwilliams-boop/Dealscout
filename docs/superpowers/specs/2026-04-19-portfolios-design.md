# Multi-Account Portfolios — Design

**Date:** 2026-04-19
**Status:** Approved (brainstorming), pending writing-plans handoff.
**Author:** Dealscout + Claude (brainstorming session, 2026-04-19)

## Problem

The app tracks catalysts well but has no concept of *which of my positions got hit by those catalysts*. Portfolio support is single-book, empty, and cannot be imported from the five brokers the user actually uses (Fidelity, Robinhood, MooMoo, Vanguard, Schwab). There is no feedback loop from "a catalyst fired" → "my position moved" → "label the catalyst type so the scorer learns".

## Goals

1. Support up to ~10 investment accounts (Taxable, Roth, 401k, HSA, Joint, etc.) across multiple brokers.
2. Import trades from broker CSV exports **and** a canonical format, via a saved-profile column-mapping engine.
3. Detect meaningful position events (≥5% daily or ≥10% 5-day move) and present them for user labeling.
4. Auto-link each event to a matching catalyst from the `catalysts` table when one exists within ±3 days.
5. Surface catalyst-type analytics ("which catalyst types actually move my money") so the catalyst scorer has labeled outcomes to learn from.

## Explicit non-goals (YAGNI)

- Continuous daily NAV time-series or benchmark comparison
- Cash flow ledger (deposits/withdrawals/dividends)
- Time-weighted return, IRR, Sharpe
- Cost-basis methods beyond average cost
- Sector/industry allocation views
- Stock split / symbol change handling
- Tax reporting / realized-loss harvesting
- Multi-currency

## Architecture decision

**Unify portfolio data into the root `dealscout.db`** (same database as catalysts, options, technicals, related_tickers). The event → catalyst linkage is the whole point, and it needs to be a real SQL foreign key. Since the existing `data/dealscout.db.trades` table is empty, migration cost is zero. The abandoned `data/dealscout.db` file will be left in place untouched.

## Data model

Five new tables in `catalysts/db.py` schema:

```sql
CREATE TABLE accounts (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  name          TEXT    NOT NULL UNIQUE,
  type          TEXT    NOT NULL,          -- 'taxable'|'roth'|'traditional'|'401k'|'hsa'|'joint'|'other'
  broker        TEXT    NOT NULL,          -- 'fidelity'|'schwab'|'robinhood'|'moomoo'|'vanguard'|'other'
  opened_date   TEXT    NOT NULL,          -- ISO date
  initial_cash  REAL    NOT NULL DEFAULT 0,
  active        INTEGER NOT NULL DEFAULT 1,
  created_at    TEXT    NOT NULL,
  event_daily_pct  REAL,                   -- per-account override; NULL = use global 5.0
  event_5day_pct   REAL                    -- per-account override; NULL = use global 10.0
);

CREATE TABLE trades (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id      INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  ticker          TEXT    NOT NULL,
  side            TEXT    NOT NULL CHECK (side IN ('BUY','SELL')),
  qty             REAL    NOT NULL CHECK (qty > 0),
  price           REAL    NOT NULL CHECK (price >= 0),
  trade_date      TEXT    NOT NULL,        -- ISO date (purchase/sale date)
  notes           TEXT,
  import_batch_id INTEGER,                 -- nullable for manually entered trades
  dedup_key       TEXT    NOT NULL UNIQUE, -- account_id|ticker|side|qty|price|trade_date
  created_at      TEXT    NOT NULL
);
CREATE INDEX idx_trades_account_date ON trades(account_id, trade_date DESC);
CREATE INDEX idx_trades_ticker       ON trades(ticker);

CREATE TABLE events (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id     INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  ticker         TEXT    NOT NULL,
  event_date     TEXT    NOT NULL,         -- ISO date of the closing bar that triggered
  move_pct       REAL    NOT NULL,         -- signed
  move_window    TEXT    NOT NULL,         -- '1d' | '5d'
  position_qty   REAL    NOT NULL,
  value_before   REAL    NOT NULL,
  value_after    REAL    NOT NULL,
  pnl_dollars    REAL    NOT NULL,
  catalyst_id    INTEGER REFERENCES catalysts(id) ON DELETE SET NULL,
  catalyst_type  TEXT,                     -- 'earnings'|'m&a'|'rumor'|'political'|'industry'|'market'|'product'|'management'|'other'
  status         TEXT    NOT NULL DEFAULT 'pending',   -- 'pending'|'confirmed'|'dismissed'
  notes          TEXT,
  detected_at    TEXT    NOT NULL,
  confirmed_at   TEXT,
  UNIQUE(account_id, ticker, event_date, move_window)
);
CREATE INDEX idx_events_status   ON events(status, detected_at DESC);
CREATE INDEX idx_events_catalyst ON events(catalyst_id);

CREATE TABLE import_profiles (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT    NOT NULL UNIQUE,
  broker      TEXT    NOT NULL,
  column_map  TEXT    NOT NULL,            -- JSON {canonical: source_column}
  value_map   TEXT    NOT NULL DEFAULT '{}',  -- JSON per-field translation tables
  row_filter  TEXT    NOT NULL DEFAULT '{}',  -- JSON predicate for skipping non-trade rows
  builtin     INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT    NOT NULL
);

CREATE TABLE import_batches (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id   INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  profile_id   INTEGER REFERENCES import_profiles(id),
  filename     TEXT    NOT NULL,
  row_count    INTEGER NOT NULL,
  inserted     INTEGER NOT NULL,
  duplicates   INTEGER NOT NULL,
  rejected     INTEGER NOT NULL DEFAULT 0,
  imported_at  TEXT    NOT NULL,
  notes        TEXT                        -- JSON of per-row rejection reasons
);
```

### Schema rationale

- `dedup_key` makes re-importing the same CSV a safe no-op. Two *genuinely distinct* trades with identical (account, ticker, side, qty, price, trade_date) are indistinguishable by this key — the user can add `notes` to disambiguate if needed. This matters for analytics approximately zero.
- `events.pnl_dollars` is the **single-bar move** (`value_after − value_before`), not cumulative position P&L. Cumulative P&L comes from aggregating the trade ledger; this field exists to label individual catalyst-driven moves.
- `events` UNIQUE constraint makes the detector idempotent: the same bar close produces the same row.
- `catalyst_id ON DELETE SET NULL` lets us prune old catalyst rows (if ever) without orphaning events. The event survives with a NULL link; the `catalyst_type` remains whatever the user labeled.
- `import_profiles.builtin=1` flags the five seeded broker profiles so migrations won't stomp user edits.

## Code layout

```
portfolios/
  __init__.py
  db.py           # CRUD for accounts/trades/events/import_profiles/import_batches
  importer.py     # CSV → canonical rows via profile; validation, dedup, preview
  profiles.py     # seed definitions for Fidelity/Schwab/Robinhood/MooMoo/Vanguard
  events.py       # position event detector (invoked from catalyst_poller)
  analytics.py    # rollups, catalyst-linked event queries, per-account stats
app_pages/
  accounts.py         # NEW  — account CRUD, summary, drilldown
  import_trades.py    # NEW  — 3-step upload wizard
  events.py           # NEW  — pending events review + rollup charts
  holdings.py         # UPDATE — account filter, per-account/all-accounts modes
  trades.py           # UPDATE — scoped to selected account
  catalysts_page.py   # UPDATE — "Did this catalyst move my portfolio?" panel
  dashboard.py        # UPDATE — "Your positions this week" column (sparkline of confirmed-event P&L)
tests/
  test_portfolios_db.py
  test_importer.py
  test_events.py
  test_analytics.py
  fixtures/broker_csvs/
    fidelity_positions.csv
    schwab_transactions.csv
    robinhood_statement.csv
    moomoo_export.csv
    vanguard_transactions.csv
```

## CSV import flow

Three-step wizard kept on one page via `st.session_state`:

1. **Upload**: choose account (or create inline); upload CSV (max ~10MB, .csv only); app reads headers + first 5 rows.
2. **Profile**: if header signature matches a saved profile, auto-select it. Otherwise show the column-mapping UI — five selectboxes (ticker, qty, price, trade_date, side) populated from detected columns, optional value_map (e.g. "Bought" → "BUY"), optional row_filter. User can save mapping as a new profile.
3. **Preview + commit**: normalized table with NEW / DUPLICATE / SKIPPED / REJECTED per row. Commit button runs INSERT OR IGNORE in a single transaction. Writes an `import_batches` audit row. Triggers event detector on the affected account in-line.

### Normalization

| Field | Type | Rule |
|---|---|---|
| ticker | str | `UPPER(strip)`, non-empty |
| side | str | Via `value_map` → exactly "BUY" or "SELL" |
| qty | float | Numeric, positive; abs-value if broker signs by direction |
| price | float | Strip `$,`, non-negative |
| trade_date | ISO str | Accept `YYYY-MM-DD`, `MM/DD/YYYY`, `DD/MM/YYYY` (per-profile hint); reject future dates |

Rejected rows are logged into `import_batches.notes` (JSON). User can either import valid rows only or cancel the whole batch.

### Dedup

`dedup_key = f"{account_id}|{ticker}|{side}|{qty:.6f}|{price:.6f}|{trade_date}"`. Second import of the same file inserts zero rows, records `duplicates=N` in `import_batches`.

### Seeded builtin profiles

On first `migrate()`, insert five `builtin=1` rows for Fidelity, Schwab, Robinhood, MooMoo, Vanguard — with initial column_map / value_map / row_filter based on the documented current format of each. Future migrations use INSERT OR IGNORE on `name` so user edits survive.

## Event detection

Invoked from `catalyst_poller.run_once()` tail, after technicals:

```python
if os.environ.get("POLYGON_API_KEY"):
    from portfolios.events import detect_events_for_all_accounts
    try:
        n = detect_events_for_all_accounts(conn)
        if n: print(f"[poller] detected {n} position events")
    except Exception as exc:
        print(f"[poller] event detection failed: {exc}")
```

### Algorithm

1. Build set of `(account_id, ticker)` with `position_qty > 0` today (derive from `trades`).
2. Collect unique tickers across accounts.
3. For each ticker, fetch 8 days of daily bars via `polygon_client.get`. (8 covers 5-day window + weekend slack.)
4. For each `(account, ticker)` with a position:
   - `daily_pct = (today_close / yesterday_close - 1) * 100`
   - `five_day_pct = (today_close / close_5d_ago - 1) * 100`
   - Threshold = account override or global default (5.0 / 10.0)
   - If `|daily_pct| >= daily_threshold` → write event with `move_window='1d'`
   - If `|five_day_pct| >= 5day_threshold` → write event with `move_window='5d'`
5. For each fresh pending event, attempt auto-link:
   ```sql
   SELECT id FROM catalysts
     WHERE ticker = ?
       AND ABS(julianday(published_at) - julianday(?)) <= 3
       AND final_score >= 30
     ORDER BY final_score DESC
     LIMIT 1
   ```
   If found, set `catalyst_id`. `catalyst_type` stays NULL — user fills on review.

### Key properties

- **Idempotent**: `UNIQUE(account_id, ticker, event_date, move_window)` + INSERT OR IGNORE. Re-scanning writes zero duplicates.
- **Close-aware**: detection scans the most recent bar whose date < today in US/Eastern. Intraday polls during market hours are a no-op for events.
- **Dismissed survives rescan**: a `dismissed` row remains in the DB so dedup continues to work; it's just hidden from the default Events view.
- **Best-effort**: any failure in the detector is caught at the poller boundary and logged — never blocks catalyst alerts.

### Cost budget

10 accounts × ~20 tickers avg ≈ ~50 unique tickers. 50 Polygon calls per detection run at the 5 rps shared bucket ≈ 10 s added to each poll. Acceptable.

## UI pages

### Accounts (new)

- Top-of-page rollup cards: total accounts, total market value, total unrealized P/L, total realized P/L (YTD), pending-events badge.
- Table: Name / Type / Broker / Opened / Positions / Market value / Unrealized / Realized / Pending / Actions.
- Drilldown panel per account: positions table, allocation pie, "Events on this account" timeline, "Import trades" shortcut.
- Create-account form at bottom.

### Import (new)

3-step wizard described above.

### Events (new)

- Filters: account, status, ticker contains, date range, gain/loss.
- Default view: `status='pending'`, newest first.
- Side panel per event: position context, catalyst link accept/change/clear, catalyst_type selector (required to confirm), notes, three buttons (Confirm / Dismiss / Save pending).
- Three rollup charts above the table, for `status='confirmed'` only:
  1. **Wins vs losses by catalyst type** — horizontal stacked bar of ΣP&L (green positive / red negative), sorted by net P&L.
  2. **Hit rate by catalyst type** — bar chart of % events with `move_pct > 0`.
  3. **Linked vs unlinked** — pie of how many confirmed events had an auto-matched catalyst.

### Trades (updated)

Account selector at top. Add-trade form writes with `account_id = selected.id`. Empty-state guides user to Accounts page first.

### Holdings (updated)

Account filter dropdown ("All" or a specific account). When "All", show an "Accounts" count badge per ticker row.

### Catalysts (updated)

In the drilldown panel, a new "Did this catalyst move my portfolio?" subsection. Runs:
```sql
SELECT e.account_id, e.ticker, e.event_date, e.move_pct, e.pnl_dollars,
       e.catalyst_type, e.status, a.name AS account_name
  FROM events e JOIN accounts a ON a.id = e.account_id
 WHERE e.catalyst_id = ?
 ORDER BY e.event_date
```
Results → compact table. Empty → gentle prompt ("No position events linked yet — add {ticker} to a portfolio…").

### Dashboard (updated)

New column on the existing ticker table: "Events this week" — count of confirmed events on that ticker in the last 30d + small cumulative-P&L sparkline sourced from `events`. Cheap, makes Dashboard feel personal.

### Sidebar (app.py)

- Nav order: Dashboard, Catalysts, Options Pulse, **Accounts**, **Events**, **Import**, Power Gauge, Holdings, Trades, Performance, IPO Tracker, Universe.
- 🔴 N badge on Events if pending events > 0.

## Analytics (summary)

Focused on the feedback loop. No institutional metrics (TWR, IRR, Sharpe, drawdown).

- **Accounts rollup**: total market value, unrealized P/L, realized P/L (YTD), positions held, pending events.
- **Per-account drilldown**: positions table, allocation pie, event timeline.
- **Events page rollups**: wins/losses by catalyst type, hit rate by catalyst type, linked vs unlinked rate.
- **Catalysts page cross-link**: events caused by the selected catalyst.
- **Dashboard per-ticker**: "events this week" badge + mini sparkline.

## Migration & rollout

### Migration

1. Extend `catalysts/db.py::SCHEMA` with the 5 new tables.
2. In `migrate()`, `INSERT OR IGNORE` the five builtin import profiles.
3. Rewrite `portfolio.py` to read/write the new tables (positions derived from `trades` scoped by `account_id`).
4. Update `holdings.py` and `trades.py` to use the rewritten API.
5. `data/dealscout.db` is abandoned. Left in place; user may delete manually.

No data migration job needed (current `trades` table is empty, verified).

### Rollout order

Each step is a commit; each leaves the app green and testable.

1. Schema + `portfolios/db.py` + unit tests. No UI yet.
2. Rewrite `portfolio.py` against new schema. `holdings.py` and `trades.py` gain empty-state guards (redirect user to Accounts page when zero accounts exist) so the rewrite is safe with an empty DB. Update positions math tests.
3. Accounts page UI (CRUD, drilldown).
4. Importer core (`portfolios/importer.py` + seeded profiles) + unit tests against fixture CSVs. No UI yet.
5. Import wizard UI (3 steps).
6. Event detector + poller hook + detector unit tests. No UI yet.
7. Events page UI (review + confirmation flow).
8. Analytics: Accounts rollups, Events charts, Catalysts "did this catalyst move my portfolio?" panel, Dashboard "events this week" column.

## Testing strategy

New test files + fixtures:

- `tests/test_portfolios_db.py` — accounts CRUD, trade dedup semantics, events idempotency, FK cascade.
- `tests/test_importer.py` — per-broker fixture parse, column-mapping wizard, row_filter, rejected-row handling.
- `tests/test_events.py` — threshold firing, auto-link precision and window, per-account overrides, dismissed survival.
- `tests/test_analytics.py` — rollup math, catalyst-linked event queries, allocation pie source data.
- `tests/fixtures/broker_csvs/` — five hand-crafted CSVs (3–5 rows each) mirroring each broker's documented header pattern and known quirks.

Critical regression guards:

- Dedup round-trip: import twice → `import_batches.duplicates=N` on second pass; `trades` count unchanged.
- Auto-link precision: AAPL catalyst does NOT link to NVDA event on the same date.
- Auto-link window: catalyst dated ±4 days from event is NOT auto-linked.
- Status survives rescan: dismissed event isn't recreated.
- Per-account override leak: account A's override does NOT affect account B.

Property-style importer test, per broker fixture:
```python
parsed = importer.apply_profile(fixture_csv, profile)
for row in parsed:
    assert row.ticker.isupper()
    assert row.qty > 0
    assert row.side in ("BUY", "SELL")
    assert row.price >= 0
    assert date.fromisoformat(row.trade_date) <= date.today()
```

Run the existing 105 tests after each phase; no existing test should change behavior.

## Open items for writing-plans

- Specific Fidelity / Schwab / Robinhood / MooMoo / Vanguard column-map seed values (documented formats as of 2026-04-19). Will confirm when drafting `portfolios/profiles.py`.
- Exact button labels / page placement (minor UX polish during implementation).
