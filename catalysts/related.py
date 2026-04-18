"""Related tickers via Polygon — DB-persisted cache with in-memory fast path."""
from __future__ import annotations

import logging
import sqlite3
import time
from typing import Optional

from catalysts import polygon_client

log = logging.getLogger("related")

# In-memory cache survives only within a single process. The DB cache survives
# across poller restarts (Windows Task Scheduler) and is shared with the UI.
_cache: dict[str, tuple[float, list[str]]] = {}
_TTL = 86400  # 24 hours — related companies rarely change
_TTL_HOURS = 24


def fetch_related(
    ticker: str,
    limit: int = 5,
    conn: Optional[sqlite3.Connection] = None,
) -> list[str]:
    """Return up to `limit` related tickers for `ticker`.

    Lookup order: in-memory cache → DB cache (if conn given) → Polygon API.
    Writes back to both caches on API hit.
    """
    now = time.time()
    if ticker in _cache and (now - _cache[ticker][0]) < _TTL:
        return _cache[ticker][1][:limit]

    if conn is not None:
        from catalysts.db import load_related_tickers, upsert_related_tickers
        cached = load_related_tickers(conn, ticker, ttl_hours=_TTL_HOURS)
        if cached is not None:
            _cache[ticker] = (now, cached)
            return cached[:limit]
    else:
        load_related_tickers = None
        upsert_related_tickers = None

    body = polygon_client.get(f"/v1/related-companies/{ticker}")
    if body is None:
        return []
    results = body.get("results", [])
    related = [r["ticker"] for r in results if "ticker" in r]

    _cache[ticker] = (now, related)
    if conn is not None and upsert_related_tickers is not None:
        try:
            upsert_related_tickers(conn, ticker, related)
        except Exception as exc:
            log.warning("related DB write for %s failed: %s", ticker, exc)

    return related[:limit]
