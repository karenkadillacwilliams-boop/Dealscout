"""Dedup helpers used by the poller."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Iterable

from catalysts.types import RawCatalyst


def filter_unseen(conn: sqlite3.Connection, items: Iterable[RawCatalyst]) -> list[RawCatalyst]:
    out: list[RawCatalyst] = []
    for item in items:
        row = conn.execute(
            "SELECT 1 FROM catalysts WHERE source=? AND source_id=?",
            (item.source, item.source_id),
        ).fetchone()
        if row is None:
            out.append(item)
    return out


def recently_alerted(
    conn: sqlite3.Connection, ticker: str, score_bucket: int, hours: int = 6
) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    row = conn.execute(
        "SELECT 1 FROM alert_log "
        "WHERE ticker=? AND score_bucket=? AND ok=1 AND sent_at > ? LIMIT 1",
        (ticker, score_bucket, cutoff),
    ).fetchone()
    return row is not None
