"""Related tickers via Polygon — cached per ticker."""
from __future__ import annotations

import logging
import time

from catalysts import polygon_client

log = logging.getLogger("related")

_cache: dict[str, tuple[float, list[str]]] = {}
_TTL = 86400  # 24 hours — related companies rarely change


def fetch_related(ticker: str, limit: int = 5) -> list[str]:
    now = time.time()
    if ticker in _cache and (now - _cache[ticker][0]) < _TTL:
        return _cache[ticker][1]

    body = polygon_client.get(f"/v1/related-companies/{ticker}")
    if body is None:
        return []
    results = body.get("results", [])
    related = [r["ticker"] for r in results[:limit] if "ticker" in r]
    _cache[ticker] = (now, related)
    return related
