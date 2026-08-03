"""Guard: the test suite must never reach the production Turso database.

catalyst_poller.run_once() calls load_dotenv() against ~/.secrets/shared.env,
which on a developer machine holds real TURSO_DATABASE_URL / TURSO_AUTH_TOKEN.
catalysts.db.connect() consults those env vars *before* the path argument, so
an unguarded run would silently swap a test's tmp_path database for an embedded
replica synced to live data.

The conftest _env fixture pins both vars to "" to block that. These tests fail
if that pinning is weakened or removed.
"""
from __future__ import annotations

import os
import sqlite3

from dotenv import load_dotenv

from catalyst_poller import _SHARED_ENV
from catalysts import db as cdb


def test_dotenv_cannot_inject_real_turso_credentials():
    """Replay the exact load order run_once() uses; vars must stay falsy."""
    if _SHARED_ENV.exists():
        load_dotenv(_SHARED_ENV)
    load_dotenv()

    assert not os.environ.get("TURSO_DATABASE_URL"), (
        "real TURSO_DATABASE_URL leaked into the test environment"
    )
    assert not os.environ.get("TURSO_AUTH_TOKEN"), (
        "real TURSO_AUTH_TOKEN leaked into the test environment"
    )


def test_connect_uses_local_sqlite_after_dotenv_load(tmp_path):
    """connect() must return a plain sqlite3 handle bound to the given path."""
    if _SHARED_ENV.exists():
        load_dotenv(_SHARED_ENV)
    load_dotenv()

    db_file = tmp_path / "isolated.db"
    conn = cdb.connect(db_file)
    try:
        assert isinstance(conn, sqlite3.Connection)
        # The file the test asked for is the file that actually got opened.
        opened = conn.execute("PRAGMA database_list").fetchall()
        main_path = [r for r in opened if r[1] == "main"][0][2]
        assert os.path.abspath(main_path) == os.path.abspath(str(db_file))
    finally:
        conn.close()
