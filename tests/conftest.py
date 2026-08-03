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
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()

# All tests under tests/ are catalyst-radar tests; revisit scope if non-catalyst tests are added.
@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("GMAIL_USER", "alerts@test.local")
    monkeypatch.setenv("GMAIL_APP_PW", "test-pw")
    monkeypatch.setenv("ALERT_TO_EMAIL", "you@test.local")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.test/webhook")
    monkeypatch.setenv("MAX_RERANK_CALLS_PER_DAY", "200")
    monkeypatch.setenv("SEC_USER_AGENT", "Dealscout-Test/1.0")
    monkeypatch.setenv("POLYGON_API_KEY", "test-polygon-key")
    monkeypatch.setenv("FINNHUB_API_KEY", "test-finnhub-key")

    # Pin the Turso vars to empty rather than deleting them. Deleting is not
    # enough: catalyst_poller.run_once() calls load_dotenv(~/.secrets/shared.env)
    # mid-test, and load_dotenv only skips keys already present in os.environ.
    # An empty string is present (so the real credentials cannot be loaded over
    # it) yet falsy, so catalysts.db.connect() takes the local sqlite3 branch
    # and honours the tmp_path the test passed it. Without this, integration
    # tests read and write the live production database.
    monkeypatch.setenv("TURSO_DATABASE_URL", "")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "")
