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

### Local development

Nothing changes locally. With `TURSO_*` env vars **unset**, `catalysts.db.connect()`
falls through to stock `sqlite3` against `dealscout.db`. `.env` continues to
hold local secrets and is loaded via `python-dotenv`.

### Architecture summary

```
Browser ──► Streamlit Community Cloud (Google email allowlist)
              │
              └──► libsql embedded replica ──sync──► Turso (remote SQLite)
                                                       ▲
GitHub Actions cron (every 15m) ──► catalyst_poller.py ┘
```
