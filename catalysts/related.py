"""Related tickers via Polygon — cached per ticker."""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

import requests

log = logging.getLogger("related")

_cache: dict[str, tuple[float, list[str]]] = {}
_TTL = 86400  # 24 hours — related companies rarely change


def fetch_related(ticker: str, limit: int = 5) -> list[str]:
    key = os.environ.get("POLYGON_API_KEY", "")
    if not key:
        return []

    now = time.time()
    if ticker in _cache and (now - _cache[ticker][0]) < _TTL:
        return _cache[ticker][1]

    try:
        r = requests.get(
            f"https://api.polygon.io/v1/related-companies/{ticker}",
            params={"apiKey": key},
            timeout=10,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        related = [r["ticker"] for r in results[:limit] if "ticker" in r]
        _cache[ticker] = (now, related)
        return related
    except Exception as exc:
        log.warning("related tickers for %s failed: %s", ticker, exc)
        return []
