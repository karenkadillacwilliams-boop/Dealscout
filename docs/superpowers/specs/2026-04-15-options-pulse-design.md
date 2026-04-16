# Phase 4 Design: Options Pulse — Catalyst-Driven Cheap Convexity Screener

**Date:** 2026-04-15
**Status:** Approved
**Depends on:** Phase 3 Catalyst Radar (merged 2026-04-13, commit a28c657)

## 1. Purpose

For every ticker in the active universe that hits the Catalyst Radar score threshold, surface every call and put with ask < $2.00 and 7-28 DTE, ranked by a composite of catalyst-weighted leverage + IV rank. Enrich existing catalyst alerts with the top qualifying contract when one exists.

## 2. Non-goals

- Real-time WebSocket streaming (overkill for 15-min cycles).
- Options trade execution or brokerage integration.
- Greeks-based strategy suggestions ("sell this spread").
- Historical backtest of the composite score.
- Social sentiment or news-specific IV analysis.
- Contracts with ask >= $2.00 or DTE outside 7-28.

## 3. Data source

Polygon.io Options Starter ($29/mo). Chain snapshot endpoint with greeks + IV per contract.

- Endpoint: `GET /v3/snapshot/options/{ticker}`
- Auth: `POLYGON_API_KEY` env var (loaded from `~/.secrets/shared.env`)
- Rate limit: stagger requests ~100ms apart (45 tickers x 100ms ~ 5s total)
- Estimated volume per cycle: ~1,350 contracts across 45 tickers (3 expirations x ~10 strikes per ticker)

## 4. Fetch strategy

1. Hit `/v3/snapshot/options/{ticker}` for each active-universe ticker.
2. Server-side params where available: expiration date range (7-28 DTE from today).
3. Client-side filters:
   - `ask_price > 0 AND ask_price <= 2.00`
   - `DTE BETWEEN 7 AND 28`
   - `contract_type IN ('call', 'put')`
4. Drop contracts with empty greeks/IV (no active market = no signal).
5. Graceful degradation: if Polygon returns an error for a ticker, skip it and continue. Log the failure.

## 5. Scoring algorithm

```
leverage_ratio = abs(strike - underlying_price) / ask_price
catalyst_score = max(final_score) from catalysts table for this ticker in the current fetch cycle
iv_rank        = percentile of current IV vs. 30-day IV history (0-100, lower = cheaper vol)

composite = (leverage_ratio * catalyst_score / 100) + (100 - iv_rank) * 0.1
```

- **Primary sort:** composite descending.
- **Tiebreaker:** lower ask price wins (less capital at risk).
- Contracts with `iv_rank < 30` are flagged as "cheap vol" in the UI.

### Scoring rationale

- `leverage_ratio` captures pure asymmetry: how many dollars of stock movement per dollar risked.
- Multiplying by `catalyst_score / 100` ensures cheap options on quiet tickers don't rank above cheap options on catalyst-active tickers.
- The IV rank term (0.1 weight) acts as a tiebreaker: among equally leveraged catalyst plays, prefer the ones where vol is historically cheap (market hasn't priced the catalyst yet).

## 6. IV Rank computation

- On each fetch, compute the mean IV of ATM +/- 1 strike contracts per ticker (ATM = strike closest to underlying_price).
- Store in `iv_history` table (one row per ticker per day).
- `iv_rank = percentile_rank(today_avg_iv, last_30_days)` per ticker.
- First 30 days: IV rank defaults to 50 (neutral) until enough history accumulates.
- Prune rows older than 60 days on each run.

## 7. Storage (SQLite)

### Table: `options_snapshot`

Latest fetch per contract. Upserted on each cycle (keyed on `contract_ticker`).

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `ticker` | TEXT | Underlying ticker (e.g., AAPL) |
| `contract_ticker` | TEXT UNIQUE | Polygon contract ticker (e.g., O:AAPL260425C00030000) |
| `contract_type` | TEXT | 'call' or 'put' |
| `strike` | REAL | Strike price |
| `expiration_date` | TEXT | ISO date (YYYY-MM-DD) |
| `dte` | INTEGER | Days to expiration |
| `ask` | REAL | Ask price |
| `bid` | REAL | Bid price |
| `mid` | REAL | Midpoint price |
| `volume` | INTEGER | Day volume |
| `open_interest` | INTEGER | Open interest |
| `iv` | REAL | Implied volatility |
| `delta` | REAL | Delta |
| `gamma` | REAL | Gamma |
| `theta` | REAL | Theta |
| `vega` | REAL | Vega |
| `underlying_price` | REAL | Underlying price at fetch time |
| `leverage_ratio` | REAL | Computed leverage ratio |
| `iv_rank` | REAL | Ticker-level IV rank at fetch time |
| `composite_score` | REAL | Final composite score |
| `fetched_at` | TEXT | ISO timestamp |

### Table: `iv_history`

Daily IV per ticker for IV rank calculation.

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | TEXT | Underlying ticker |
| `date` | TEXT | ISO date |
| `avg_iv` | REAL | Mean IV of ATM +/- 1 strike contracts |

Unique constraint on `(ticker, date)`. Pruned to 60-day rolling window.

## 8. Integration with Phase 3

### Poller (`catalyst_poller.py`)

1. Existing Phase 3 pipeline runs first: fetch catalysts -> score -> rerank -> dedup.
2. After Phase 3 completes, run the options fetch for tickers that scored above the alert threshold.
3. Options enrichment is a separate step that does NOT block catalyst alerts if Polygon is slow or down.
4. If options fetch fails entirely, catalyst alerts still fire without the options line.

### Alert enrichment (`alerts/dispatcher.py`)

Before dispatching a catalyst alert, check `options_snapshot` for qualifying contracts on that ticker:
- If found, append a summary line to the alert body:
  ```
  Options: 3 calls under $2 | best: Apr 25 $30C @ $1.45 (leverage 8.2x, IV rank 22%)
  ```
- If none found, alert fires as-is (Phase 3 behavior unchanged).
- No new dedup logic. Rides on Phase 3's 6h dedup window.

## 9. UI

### Dashboard (existing page) — new "Options" column

- Shows badge: "4C 2P" (4 qualifying calls, 2 qualifying puts) or "-" if none.
- Badge links/filters the Options Pulse tab to that ticker.

### Options Pulse (new Streamlit page)

**Top summary row:**
- Total qualifying contracts across universe.
- Top 5 composite scores with contract ticker + ask price.
- Count of tickers with IV rank < 30 ("cheap vol" names).

**Main table (ranked by composite descending):**

| Column | Example |
|--------|---------|
| Ticker | ASTS |
| Type | Call |
| Strike | $30 |
| Exp | Apr 25 |
| DTE | 10 |
| Ask | $1.45 |
| Leverage | 8.2x |
| IV | 42% |
| IV Rank | 22% |
| Catalyst | 87 |
| Composite | 7.6 |

**Filters:**
- Ticker dropdown (multi-select).
- Call/Put toggle.
- DTE slider (7-28).
- Max ask slider ($0.05 - $2.00).

**Color coding:**
- Green: IV rank < 30 (cheap vol).
- Amber: IV rank 30-60.
- Red: IV rank > 60 (expensive vol).

### Sidebar

- Badge: "X options" count next to the existing catalyst badge.

## 10. New files

```
catalysts/
  options.py          # Polygon fetcher + client-side filter
  iv_rank.py          # IV history storage + rank computation
  options_score.py    # Leverage ratio + composite scoring
```

## 11. Modified files

```
catalyst_poller.py    # Orchestrate options fetch after catalyst pipeline
catalysts/db.py       # New tables (options_snapshot, iv_history) + migration
alerts/dispatcher.py  # Enrich alert body with options line
app.py                # Options Pulse page + Dashboard options column + sidebar badge
.env.example          # Add POLYGON_API_KEY placeholder
requirements.txt      # No new deps (urllib/json suffice for Polygon REST)
```

## 12. Environment variables

| Variable | Source | Required |
|----------|--------|----------|
| `POLYGON_API_KEY` | `~/.secrets/shared.env` | Yes |

All other env vars are inherited from Phase 3.

## 13. Error handling

- Polygon HTTP errors (429, 500, timeout): log, skip ticker, continue. No retry in the poller cycle; the next 15-min cycle will retry naturally.
- Empty greeks/IV on a contract: drop the contract from scoring (no signal).
- No qualifying contracts for a ticker: normal case, no error.
- SQLite write failure: log and abort the options step only (catalyst pipeline unaffected).
- POLYGON_API_KEY missing: log warning at poller startup, skip options fetch entirely, Phase 3 runs normally.

## 14. Testing strategy

- Unit tests for scoring math (leverage_ratio, composite, iv_rank percentile).
- Unit tests for client-side contract filtering (DTE, ask, empty greeks).
- Integration test: mock Polygon response -> fetch -> score -> verify DB rows.
- Integration test: catalyst alert enrichment with/without qualifying options.
- Manual verification: run poller in dry-run mode, inspect Options Pulse tab.

## 15. Cost

- Polygon Options Starter: $29/mo.
- No additional API costs (unlimited calls on Starter tier).
- No new infra (local SQLite, existing Task Scheduler job).
