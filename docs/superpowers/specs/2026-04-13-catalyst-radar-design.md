# Catalyst Radar (Dealscout Phase 3) — Design Spec

**Date:** 2026-04-13
**Status:** Draft, pending user review
**Author:** brainstormed with Claude (acting as sr. quant/fundamental/momentum analyst)
**Supersedes:** n/a
**Related:** Phase 1 (portfolio tracker), Phase 2 (Power Gauge rating)

## 1. Problem

Dealscout today surfaces *post-move* momentum and a static fundamentals+technicals composite (Power Gauge). A rumor-hunting analyst needs **leading signals** — filings, news, and language patterns that precede material events (M&A, activist stakes, partnerships, tech/product launches). There is currently no news ingestion, no filings ingestion, no event scoring, no alerting, and the ticker universe is hardcoded in `tickers.py`.

## 2. Goal

Ship a **Catalyst Radar** subsystem that:

1. Polls SEC EDGAR, yfinance news, and Google News RSS for every ticker in the active universe on a 15-minute cadence.
2. Scores each item with a transparent keyword-based first pass, then re-ranks high-signal items with Claude Haiku 4.5 for a refined score and one-line rationale.
3. Persists all catalysts to SQLite and exposes them through a new **Catalysts** page in Streamlit (sortable, filterable, with an LLM rationale drilldown).
4. Fires dedup'd alerts to Gmail SMTP and a Discord webhook when `final_score ≥ 70`.
5. Replaces the hardcoded `TICKERS` list with an editable **Universe** page.

Non-goals for this phase: options flow, short interest, social sentiment, a rumor journal, backfill of historical filings. Those are Phase 4 and Phase 5.

## 3. Decisions (locked)

| # | Decision | Value |
|---|---|---|
| D1 | Data sources | EDGAR + yfinance `Ticker.news` + Google News RSS |
| D2 | Cadence / freshness | Background poll every **15 min** via Windows Task Scheduler |
| D3 | Alert channels | Gmail SMTP **and** Discord webhook, both via one dispatcher |
| D4 | Scoring model | Keyword first pass with stable interface, LLM re-ranker on top-N |
| D5 | LLM | Claude Haiku 4.5 (`claude-haiku-4-5-20251001`) |
| D6 | Alert threshold | `final_score ≥ 70` (= score ≥ 7/10) |
| D7 | Dedup window | 6h per `(ticker, final_score // 10)` bucket |
| D8 | Alert watchlist scope | Entire active universe (not just Holdings) |
| D9 | Editable universe UI | New Universe page, ships in this phase |
| D10 | Gmail from-address | Dedicated `alerts@` account (user provides), never personal |
| D11 | Discord setup | Private server + `#dealscout-alerts` channel, webhook URL in env |
| D12 | LLM daily cap | `MAX_RERANK_CALLS_PER_DAY=200` env var |
| D13 | Backfill | None — fresh start from day-one deployment |

## 4. Architecture

```
┌──────────────────────────┐         ┌──────────────────────────┐
│ catalyst_poller.py       │ writes  │ dealscout.db (SQLite)    │
│ (Task Scheduler, 15 min) │────────▶│  catalysts               │
│                          │         │  alert_log               │
│  1. fetch EDGAR          │         │  universe                │
│  2. fetch yf + GNews     │         └────────────┬─────────────┘
│  3. keyword score        │                      │ reads
│  4. LLM re-rank top-N    │                      ▼
│  5. dedup + persist      │         ┌──────────────────────────┐
│  6. alerts.send() if ≥70 │         │ app.py (Streamlit)       │
└──────────────────────────┘         │  + Catalysts page        │
           │                         │  + Universe page         │
           ▼                         │  + sidebar alert badge   │
 ┌───────────────────┐                └──────────────────────────┘
 │ alerts/           │
 │  dispatcher.py    │──▶ email (SMTP)
 │  email.py         │──▶ Discord webhook
 │  discord.py       │
 └───────────────────┘
```

**Key principle:** the Streamlit app is a **reader only**. All network I/O, scoring, LLM calls, and alert delivery live in the poller process. Streamlit performs no outbound HTTP for catalyst data. This keeps the UI instantaneous and makes the poller independently testable and debuggable.

## 5. File layout

New files:

- `catalyst_poller.py` — Task Scheduler entry point, runs one poll cycle and exits.
- `catalysts/__init__.py`
- `catalysts/edgar.py` — EDGAR fetcher (8-K, 13D/G, 425, SC TO-T, S-4, DEFM14A).
- `catalysts/news.py` — yfinance news + Google News RSS fetchers.
- `catalysts/score.py` — pure keyword scorer, compiled regex dictionary.
- `catalysts/rerank.py` — Claude Haiku re-ranker, batched.
- `catalysts/db.py` — connection, migrations, typed row helpers.
- `catalysts/dedup.py` — `filter_unseen`, `recently_alerted`.
- `alerts/__init__.py`
- `alerts/dispatcher.py` — channel-agnostic fan-out.
- `alerts/email.py` — `smtplib` + `EmailMessage` (safe headers).
- `alerts/discord.py` — single webhook POST with embed payload.
- `.env` — gitignored secrets file (see §10).
- `docs/superpowers/specs/2026-04-13-catalyst-radar-design.md` — this file.
- `tests/test_score.py`, `tests/test_dedup.py`, `tests/test_dispatcher.py`, `tests/test_poller_integration.py`.

Modified files:

- `app.py` — sidebar badge + last-poll caption; add Catalysts and Universe pages; insert Catalyst column on Dashboard.
- `tickers.py` — becomes seed/fallback only; `load_active_universe(db)` is authoritative.
- `requirements.txt` — add `python-dotenv`, `requests`, `feedparser`, `defusedxml`, `anthropic`.
- `.gitignore` — ensure `.env`, `*.db-wal`, `*.db-shm` are ignored.

## 6. Data model

Three new tables in `dealscout.db` alongside existing `trades`.

```sql
CREATE TABLE catalysts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker        TEXT    NOT NULL,
    source        TEXT    NOT NULL,      -- 'edgar' | 'yfinance' | 'gnews'
    source_id     TEXT    NOT NULL,      -- accession number, url, or guid
    form_type     TEXT,                  -- '8-K', '13D', NULL for news
    headline      TEXT    NOT NULL,
    url           TEXT    NOT NULL,
    published_at  TEXT    NOT NULL,      -- ISO8601 UTC
    kw_score      INTEGER NOT NULL,      -- 0..100 from keyword pass
    llm_score     INTEGER,               -- 0..10 from re-ranker, NULL if skipped
    final_score   INTEGER NOT NULL,      -- 0..100 used for threshold
    tags          TEXT,                  -- JSON list
    rationale     TEXT,                  -- LLM one-liner, NULL if skipped
    seen          INTEGER NOT NULL DEFAULT 0,
    fetched_at    TEXT    NOT NULL,
    UNIQUE(source, source_id)
);
CREATE INDEX idx_catalysts_ticker_time ON catalysts(ticker, published_at DESC);
CREATE INDEX idx_catalysts_score       ON catalysts(final_score DESC);

CREATE TABLE alert_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    catalyst_id    INTEGER NOT NULL REFERENCES catalysts(id),
    ticker         TEXT    NOT NULL,
    score_bucket   INTEGER NOT NULL,     -- final_score // 10
    channels       TEXT    NOT NULL,     -- JSON: ["email","discord"]
    sent_at        TEXT    NOT NULL,
    ok             INTEGER NOT NULL
);
CREATE INDEX idx_alertlog_dedup ON alert_log(ticker, score_bucket, sent_at DESC);

CREATE TABLE universe (
    ticker     TEXT PRIMARY KEY,
    name       TEXT,
    added_at   TEXT NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1
);
```

**Dedup query** (before sending any alert):

```sql
SELECT 1 FROM alert_log
WHERE ticker = ? AND score_bucket = ? AND ok = 1
  AND sent_at > datetime('now', '-6 hours')
LIMIT 1;
```

**Seed behavior:** first poller run copies `tickers.TICKERS` into `universe` if the table is empty; afterwards the Universe page is authoritative.

## 7. Scoring pipeline

### 7.1 Pass 1 — keyword scorer (`catalysts/score.py`)

Pure function, no I/O. Compiled regex dictionary validated at import. Case-insensitive, word-boundary matching.

| Category | Example phrases | Weight | Tag |
|---|---|---|---|
| M&A confirmed | "definitive agreement", "to acquire", "agrees to acquire", "merger agreement", "tender offer" | 35 | `m&a-confirmed` |
| M&A rumored | "in talks to", "exploring sale", "exploring strategic alternatives", "weighing bid", "approached about", "considering offer" | 25 | `m&a-rumor` |
| Activist | "13D filed", "activist stake", "nominates directors", "urges board" | 20 | `activist` |
| Partnership | "strategic partnership", "collaboration agreement", "joint venture", "licensing deal" | 15 | `partnership` |
| Product/tech | "launches", "unveils", "first-in-class", "FDA approval", "design win" | 10 | `product` |
| Filing signal | form type ∈ {8-K 1.01/2.01, 13D, 425, SC TO-T, S-4, DEFM14A} | +20 | `filing` |
| Negative modifiers | "denies", "not in talks", "rumor", "speculation only" | −15 | `weak` |

`kw_score = min(100, max(0, sum_of_hits))`. Items with `kw_score ≥ 20` advance to Pass 2.

### 7.2 Pass 2 — LLM re-ranker (`catalysts/rerank.py`)

Claude Haiku 4.5 (`claude-haiku-4-5-20251001`), batched up to 10 items per API call, JSON-mode output. Daily cap enforced by `MAX_RERANK_CALLS_PER_DAY=200` — once exceeded, the poller persists kw-only rows and skips re-ranking until the next UTC day.

Prompt (system message):

> You are an M&A and catalyst analyst. For each headline, rate 0–10 how likely it signals a near-term material event (M&A, activist stake, major partnership, tech/product launch with revenue impact). Ignore routine press. Output JSON: `[{"id":…, "score":…, "rationale":"…","tags":[…]}]`. Rationale must be ≤ 25 words.

### 7.3 Final score fusion

```
if llm_score is None:
    final_score = kw_score          # never alerts
else:
    final_score = round(0.6 * kw_score + 0.4 * (llm_score * 10))
```

**Alert gate:** `final_score ≥ 70 AND llm_score IS NOT NULL` — guarantees every alert carries an LLM-generated rationale.

### 7.4 Interface guarantee

Both passes are pure functions with stable signatures:

```python
def score_item(item: RawCatalyst) -> ScoredItem: ...
def rerank_batched(items: list[ScoredItem], batch: int = 10) -> list[RerankedItem]: ...
```

Swapping keyword → embeddings, or Haiku → Sonnet, touches one file each. The poller, DB, and alert dispatcher never need to change.

## 8. Poller flow (`catalyst_poller.py`)

```python
def main() -> int:
    load_dotenv()
    db = connect()                              # SQLite WAL mode
    tickers = load_active_universe(db)

    raw: list[RawCatalyst] = []
    raw += edgar.fetch(tickers, since=lookback(hours=2))
    raw += news.fetch_yfinance(tickers)
    raw += news.fetch_gnews_rss(tickers)

    fresh = filter_unseen(db, raw)
    scored = [score.score_item(r) for r in fresh]
    rerank_pool = [s for s in scored if s.kw_score >= 20]
    reranked = rerank.rerank_batched(rerank_pool, batch=10)

    for item in merge(scored, reranked):
        cid = persist_catalyst(db, item)
        if item.final_score >= 70 and item.llm_score is not None:
            if not recently_alerted(db, item.ticker, item.final_score // 10, hours=6):
                ok, channels = alerts.dispatcher.send(item)
                log_alert(db, cid, item, channels, ok)

    db.commit()
    return 0
```

**Guarantees:**

- **Idempotent.** Reruns are safe via `UNIQUE(source, source_id)`.
- **Crash-safe.** One transaction per poll; partial failures roll back cleanly.
- **Rate-limit friendly.** EDGAR fetcher sends `User-Agent: Dealscout/1.0 <contact>`, sleeps 100ms between requests, caches the company-tickers index locally for 24h.
- **Lookback 2h** exceeds the 15-min cadence — missed runs are caught on the next cycle.

## 9. Alert dispatcher

```python
def send(item: RerankedItem) -> tuple[bool, list[str]]:
    sent: list[str] = []
    ok = True
    for channel in (email, discord):
        try:
            channel.send(
                subject=f"[{item.ticker}] {item.tags[0]} — score {item.final_score}",
                headline=item.headline,
                rationale=item.rationale,
                url=item.url,
                source=item.source,
                published_at=item.published_at,
            )
            sent.append(channel.NAME)
        except Exception as e:
            ok = False
            log.warning("alert channel %s failed: %s", channel.NAME, e)
    return ok, sent
```

**Email (`alerts/email.py`)** — `smtplib` SSL on port 465, Gmail app password from env, uses stdlib `email.message.EmailMessage` (no header-injection risk even with `\r\n` in headlines).

**Discord (`alerts/discord.py`)** — single `requests.post` to webhook URL with an embed payload (title, rationale as description, headline/source/tags fields, color coded by severity). 10s timeout. Webhook URL is a secret — env only, never logged.

## 10. Security posture

- **All secrets via env vars** (`python-dotenv`): `GMAIL_USER`, `GMAIL_APP_PW`, `ALERT_TO_EMAIL`, `DISCORD_WEBHOOK_URL`, `ANTHROPIC_API_KEY`, `MAX_RERANK_CALLS_PER_DAY`.
- `.env` confirmed in `.gitignore` before first commit.
- **Email header safety** via stdlib `EmailMessage` — no custom string building.
- **XML parsing** (EDGAR feeds) via `defusedxml` to prevent XXE.
- **Regex dictionary** validated at module import (compile check + per-phrase length cap); no user input ever becomes a regex.
- **SQL** fully parameterized; no f-string queries anywhere.
- **URL rendering** in Streamlit goes through `urllib.parse.urlparse`; only `http`/`https` schemes render as clickable links.
- **No `unsafe_allow_html`** anywhere catalyst content is rendered — Streamlit's default escaping is relied on for all third-party text.
- **Gmail from-address** is a dedicated account (not the user's personal Gmail) to limit app-password blast radius.

## 11. Streamlit UI changes (`app.py`)

### 11.1 Sidebar

```python
unseen = db.fetchone("SELECT COUNT(*) FROM catalysts WHERE final_score >= 70 AND seen = 0")[0]
last_poll = db.fetchone("SELECT MAX(fetched_at) FROM catalysts")[0]

st.sidebar.title("📈 Dealscout")
badge = f" 🔴 {unseen}" if unseen else ""
page = st.sidebar.radio("Navigate",
    ["Dashboard", "Catalysts" + badge, "Power Gauge", "Holdings", "Trades", "Performance", "Universe"])
st.sidebar.caption(f"Last catalyst poll: {last_poll or '—'}")
```

Opening the Catalysts page flips `seen = 1` for all currently-visible rows.

### 11.2 Catalysts page

Filters: min score (slider, default 70), lookback (6h/24h/7d), tag (All / m&a-confirmed / m&a-rumor / activist / partnership / product), ticker (free text).

Table: Score (progress bar column_config), Ticker, Tag, Headline, Source, Published. Sorted by `final_score DESC, published_at DESC`.

Expander per selected row: LLM rationale, matched keyword phrases, all tags, validated clickable source URL, "Copy to rumor journal" button (disabled stub for Phase 5).

Secondary section: **Catalyst heatmap** — `st.bar_chart` of top 15 tickers by rolling-24h summed `final_score`.

### 11.3 Universe page

Table of active rows with Remove buttons (soft delete: `active = 0`, history preserved).

Add-ticker form: ticker regex `^[A-Z][A-Z0-9.\-]{0,9}$`, uppercased, deduped.

Bulk import textarea: comma- or newline-separated; validated per row; errors reported inline.

### 11.4 Dashboard

Insert a `Catalyst` column right after `Grade`: max `final_score` in the last 24h per ticker, or `—`.

## 12. Testing

### 12.1 Unit (no mocks)

- `tests/test_score.py` — ~30 fixture headlines (10 confirmed M&A / 10 rumored / 10 noise) as a golden-file regression suite. The scorer is where analyst judgment lives — this file carries the heaviest coverage.
- `tests/test_dedup.py` — `filter_unseen` and `recently_alerted` with temp SQLite.

### 12.2 Integration (temp SQLite + stubbed fetchers/LLM)

- `tests/test_poller_integration.py`:
  - Full poll round-trip with `edgar.fetch` / `news.fetch_*` / `rerank.rerank_batched` monkey-patched to fixtures.
  - Asserts: catalysts persisted, exactly one alert fired at score ≥ 70, second run is idempotent (zero new rows), 6h dedup suppresses the second alert with identical inputs.
- `tests/test_dispatcher.py`:
  - Stub both channels; partial-failure semantics (`ok=False` but other channels still attempted).

### 12.3 Manual smoke tests (before Task Scheduler wire-up)

1. `python catalyst_poller.py --dry-run` — fetches, scores, re-ranks, prints JSON, writes nothing, sends nothing.
2. `python catalyst_poller.py --once --force-alert` — sends one synthetic alert to both channels to verify Gmail + Discord delivery.
3. Only after both pass: `schtasks /Create /SC MINUTE /MO 15 /TN Dealscout-Poller /TR "python ...\catalyst_poller.py"`.

## 13. Rollout order

Each step is independently shippable and commit-sized.

1. **DB migration + Universe page** — schema + UI land first, nothing downstream blocked.
2. **EDGAR fetcher + keyword scorer + Catalysts page** (no LLM, no alerts). Poller run manually. First real "huh, look at that" moment.
3. **yfinance + Google News fetchers** — broader recall, same UI.
4. **LLM re-ranker** — scores gain `rationale` and final_score fusion kicks in.
5. **Alert dispatcher (email + Discord) + 6h dedup** — dry-run first, then enable.
6. **Task Scheduler wire-up** — 15-min cadence goes live; let it run overnight before step 7.
7. **Dashboard catalyst column + sidebar badge** — polish layer.

## 14. Cost envelope

- LLM: ~200 tickers × 15-min polls × ~5% advancing to rerank ≈ 10 items/poll; batching 10-per-call → ~100 API calls/day; Haiku pricing → a few cents/day. Hard cap `MAX_RERANK_CALLS_PER_DAY=200`.
- EDGAR / yfinance / Google News: free, rate-limit-polite.
- Gmail SMTP / Discord webhook: free.

## 15. Out of scope (explicitly deferred)

- Options flow, IV, short interest (Phase 4 — uses Polygon).
- Social sentiment (Twitter/X, Reddit).
- Rumor journal with conviction tracking and resolution (Phase 5).
- Historical backfill of filings.
- Multi-user / auth / hosting — this is a local single-user tool.
- Mobile UI.

## 16. Open items

None at spec-write time. All D1–D13 decisions are locked. The only user-provided values needed at deploy time are the five env-var secrets in §10.
