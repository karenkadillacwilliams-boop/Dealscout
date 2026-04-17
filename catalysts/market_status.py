"""Polygon market status check with TTL cache."""
from __future__ import annotations

import logging
import os
import time

import requests

log = logging.getLogger("market_status")

_cache: dict[str, tuple[float, bool]] = {}
_TTL = 300  # 5 minutes


def is_market_open() -> bool:
    key = os.environ.get("POLYGON_API_KEY", "")
    if not key:
        return True  # assume open if no key (don't block catalyst pipeline)

    now = time.time()
    if "status" in _cache and (now - _cache["status"][0]) < _TTL:
        return _cache["status"][1]

    try:
        r = requests.get(
            "https://api.polygon.io/v1/marketstatus/now",
            params={"apiKey": key},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        market = data.get("market", "closed")
        is_open = market in ("open", "extended-hours")
        _cache["status"] = (now, is_open)
        log.info("market status: %s (is_open=%s)", market, is_open)
        return is_open
    except Exception as exc:
        log.warning("market status check failed: %s", exc)
        return True  # fail-open: don't skip options if we can't check
