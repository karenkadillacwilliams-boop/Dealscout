"""Tests for catalysts.db.sync() — the Turso replica refresh.

The deployed app holds one cached connection for the life of the container
while the poller writes to Turso every 15 minutes, so sync() is what keeps
viewers from seeing frozen data. These tests cover the three cases that matter:
it calls through on a libsql connection, it is inert on plain sqlite3, and a
network failure degrades to stale data instead of a crashed page.
"""
from __future__ import annotations

import sqlite3

from catalysts import db as cdb


class _FakeLibsqlConn:
    """Stands in for a libsql embedded-replica connection."""

    def __init__(self, fail: bool = False):
        self.calls = 0
        self._fail = fail

    def sync(self):
        self.calls += 1
        if self._fail:
            raise RuntimeError("connection reset by peer")


def test_sync_calls_through_on_libsql_connection():
    conn = _FakeLibsqlConn()
    assert cdb.sync(conn) is True
    assert conn.calls == 1


def test_sync_is_noop_on_plain_sqlite(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "local.db"))
    try:
        # stdlib sqlite3 has no .sync attribute; must not raise.
        assert cdb.sync(conn) is False
    finally:
        conn.close()


def test_sync_swallows_network_failure(capsys):
    """A flaky network must not take the page down."""
    conn = _FakeLibsqlConn(fail=True)
    assert cdb.sync(conn) is False
    assert conn.calls == 1
    assert "sync failed" in capsys.readouterr().out


def test_sync_reports_each_attempt_independently():
    """Result tracks the current attempt, not a cached first answer."""
    conn = _FakeLibsqlConn()
    assert cdb.sync(conn) is True
    conn._fail = True
    assert cdb.sync(conn) is False
    assert conn.calls == 2
