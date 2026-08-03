# Dealscout

## Deploying to Streamlit Community Cloud

Dealscout is a Streamlit app backed by SQLite plus a 15-minute catalyst poller.
On Streamlit Community Cloud the local SQLite file would be wiped on every
restart, so the deploy uses **Turso** (libsql, SQLite-wire-compatible) for the
DB and **GitHub Actions** for the poller.

### One-time setup

1. **Create a Turso DB.**
   - Sign up at https://turso.tech (free tier).
   - Dashboard -> Create Database -> name it `dealscout`.
   - Copy the Database URL (`libsql://dealscout-<org>.turso.io`) and generate
     a full-access token.
   - (CLI alternative: `turso db create dealscout && turso db show dealscout --url
     && turso db tokens create dealscout`. The CLI does not run on Windows;
     use WSL or skip it — the dashboard works fine.)

2. **Migrate local data into Turso.**
   ```bash
   export TURSO_DATABASE_URL=libsql://dealscout-<org>.turso.io
   export TURSO_AUTH_TOKEN=<token>
   python scripts/migrate_to_turso.py
   ```
   Uses Turso's HTTP `/v2/pipeline` API so it runs from any platform with
   `requests` (no native deps). Re-runnable via `INSERT OR IGNORE`.

3. **Push the repo to GitHub** (private repo recommended — the app stores trade
   data even after auth is enabled).

4. **Connect Streamlit Community Cloud → New app → pick `app.py`.**
   In the app's **Settings → Secrets**, paste the contents of
   `.streamlit/secrets.toml.example` with real values filled in.

5. **Lock the app down.**
   In Streamlit Cloud → app → **Settings → Sharing**, set viewer access to a
   specific Google email allowlist (just yours). This is the auth gate.

6. **Add GitHub Actions secrets** (repo → Settings → Secrets and variables →
   Actions). Mirror the same keys from `secrets.toml.example`:
   `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`, `POLYGON_API_KEY`,
   `SEC_USER_AGENT`, `ANTHROPIC_API_KEY`, `MAX_RERANK_CALLS_PER_DAY`,
   `GMAIL_USER`, `GMAIL_APP_PW`, `ALERT_TO_EMAIL`, `DISCORD_WEBHOOK_URL`.

   The workflow at `.github/workflows/poller.yml` runs every 15 min during
   US market hours and writes catalysts to Turso, which the Streamlit app
   reads from.

   > **GitHub disables scheduled workflows after 60 days without repo
   > activity**, and does not notify you. The poller then stops silently and
   > the app quietly serves older and older data. If you go a while without
   > commits, check Actions → Catalyst Poller occasionally and hit
   > "Enable workflow" if it has been paused.

### Data freshness

The app holds one cached DB connection for the life of its container, so the
Turso embedded replica is refreshed explicitly rather than only at startup:
`app_pages/shared.py` calls `catalysts.db.sync()` on a 60-second TTL from the
sidebar, which renders ahead of every page body. Without that, viewers would
see whatever was current when the container last booted, no matter how often
the poller ran. The sidebar **Refresh data** button clears the TTL and forces
an immediate pull.

### Local development

```powershell
py -3.11 -m venv .venv311
.\.venv311\Scripts\python.exe -m pip install -r requirements.txt
.\.venv311\Scripts\python.exe -m pytest -q
.\.venv311\Scripts\python.exe -m streamlit run app.py
```

With `TURSO_*` env vars **unset**, `catalysts.db.connect()` falls through to
stock `sqlite3` against a local `dealscout.db`. `.env` holds local secrets and
is loaded by `app.py` via `python-dotenv`.

Two deliberate choices worth knowing:

- **`app.py` does not read `~/.secrets/shared.env`**, though `catalyst_poller.py`
  does. That file holds the production `TURSO_*` credentials, so reading it from
  the app would silently point local development at the live database. Copy the
  specific keys you want into the repo `.env` instead.
- **`libsql-experimental` is marked `sys_platform != "win32"`** in
  `requirements.txt`. It publishes no Windows wheel and would try to compile
  from Rust source. It is only imported when `TURSO_*` are set, so Windows
  never needs it; Streamlit Cloud and GitHub Actions (both Linux) still get it.

The test suite pins `TURSO_*` to empty strings in `tests/conftest.py`. That is
load-bearing: `run_once()` calls `load_dotenv(~/.secrets/shared.env)` mid-test,
and without the pin the integration tests would read and write production.

### Architecture summary

```
Browser ──► Streamlit Community Cloud (Google email allowlist)
              │
              └──► libsql embedded replica ──sync──► Turso (remote SQLite)
                                                       ▲
GitHub Actions cron (every 15m) ──► catalyst_poller.py ┘
```
