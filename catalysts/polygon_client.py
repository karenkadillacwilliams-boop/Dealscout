"""Centralized Polygon.io HTTP client with rate limiting and 429/5xx retry.

All Polygon callers route through `get()` and `paginate()` so we share
a single token bucket across the process and consistent retry/error handling.
The `apiKey` is always passed via `params=` so it never appears in URL
strings (avoids logging/traceback leaks).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Iterable, Optional

import requests

log = logging.getLogger("polygon_client")

_BASE_HOST = "api.polygon.io"
_DEFAULT_RPS = 5.0
_DEFAULT_TIMEOUT = 15
_DEFAULT_MAX_RETRIES = 3


class TokenBucket:
    """Thread-safe token bucket for shared rate limiting."""

    def __init__(self, rate: float, capacity: Optional[float] = None) -> None:
        self.rate = rate
        self.capacity = capacity if capacity is not None else max(rate, 1.0)
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: float = 1.0) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self.capacity, self._tokens + (now - self._last) * self.rate
                )
                self._last = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                wait = (tokens - self._tokens) / self.rate
            time.sleep(wait)


def _rps() -> float:
    try:
        return float(os.environ.get("POLYGON_RPS", _DEFAULT_RPS))
    except ValueError:
        return _DEFAULT_RPS


_bucket: Optional[TokenBucket] = None
_bucket_lock = threading.Lock()


def _get_bucket() -> TokenBucket:
    global _bucket
    if _bucket is None:
        with _bucket_lock:
            if _bucket is None:
                _bucket = TokenBucket(rate=_rps())
    return _bucket


def reset_bucket_for_tests() -> None:
    """Drop the cached bucket so tests can reconfigure rate."""
    global _bucket
    _bucket = None


def _api_key() -> str:
    return os.environ.get("POLYGON_API_KEY", "")


def get(
    path_or_url: str,
    *,
    params: Optional[dict] = None,
    timeout: int = _DEFAULT_TIMEOUT,
    max_retries: int = _DEFAULT_MAX_RETRIES,
) -> Optional[dict]:
    """GET a Polygon endpoint with rate-limit + retry on 429/5xx/network errors.

    `path_or_url` may be a path like `/v3/snapshot/options/AAPL` or a full
    `https://api.polygon.io/...` URL (used for pagination `next_url`s).
    Returns parsed JSON dict on success, or `None` on permanent failure.
    """
    key = _api_key()
    if not key:
        log.warning("polygon unconfigured; skipping %s", path_or_url)
        return None

    if path_or_url.startswith("http"):
        url = path_or_url
    else:
        url = f"https://{_BASE_HOST}{path_or_url}"

    p = dict(params or {})
    p["apiKey"] = key

    bucket = _get_bucket()
    backoff = 1.0
    for attempt in range(max_retries + 1):
        bucket.acquire()
        try:
            resp = requests.get(url, params=p, timeout=timeout)
        except Exception as exc:  # broad: matches pre-refactor behavior across callers
            log.warning("polygon GET network error: %s", exc)
            if attempt >= max_retries:
                return None
            time.sleep(backoff)
            backoff *= 2
            continue

        status = resp.status_code
        if status == 429:
            if attempt >= max_retries:
                log.warning("polygon 429 after %d retries — giving up", max_retries)
                return None
            retry_after = resp.headers.get("Retry-After") if hasattr(resp, "headers") else None
            try:
                wait = float(retry_after) if retry_after else backoff
            except (TypeError, ValueError):
                wait = backoff
            log.info("polygon 429 — retry %d in %.1fs", attempt + 1, wait)
            time.sleep(wait)
            backoff *= 2
            continue

        if 500 <= status < 600:
            if attempt >= max_retries:
                log.warning("polygon %d after %d retries", status, max_retries)
                return None
            time.sleep(backoff)
            backoff *= 2
            continue

        if status >= 400:
            log.warning("polygon %d on GET — not retrying", status)
            return None

        try:
            return resp.json()
        except ValueError:
            log.warning("polygon returned non-JSON body")
            return None

    return None


def paginate(
    path_or_url: str,
    *,
    params: Optional[dict] = None,
    timeout: int = _DEFAULT_TIMEOUT,
    max_pages: int = 50,
) -> Iterable[dict]:
    """Yield parsed JSON pages, following Polygon `next_url` cursors."""
    next_url: Optional[str] = path_or_url
    next_params = dict(params or {})
    pages = 0
    while next_url and pages < max_pages:
        body = get(next_url, params=next_params, timeout=timeout)
        if body is None:
            return
        yield body
        pages += 1
        nxt = body.get("next_url")
        if not nxt:
            return
        next_url = nxt
        next_params = {}
