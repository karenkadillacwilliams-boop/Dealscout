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
