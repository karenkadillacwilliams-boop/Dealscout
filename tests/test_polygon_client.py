import time
from unittest.mock import patch, MagicMock

import requests

from catalysts import polygon_client as pc


def _resp(status=200, body=None, headers=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body if body is not None else {}
    r.headers = headers or {}
    r.raise_for_status = MagicMock()
    return r


def test_get_returns_json_on_success():
    pc.reset_bucket_for_tests()
    with patch.object(pc.requests, "get", return_value=_resp(200, {"ok": True})):
        out = pc.get("/v3/test", params={"x": 1})
    assert out == {"ok": True}


def test_get_returns_none_when_no_key(monkeypatch):
    pc.reset_bucket_for_tests()
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    out = pc.get("/v3/test")
    assert out is None


def test_get_passes_apikey_via_params_not_url(monkeypatch):
    """Regression: api key must never be embedded in the URL string."""
    pc.reset_bucket_for_tests()
    captured = {}

    def _capture(url, **kwargs):
        captured["url"] = url
        captured["params"] = kwargs.get("params", {})
        return _resp(200, {})

    with patch.object(pc.requests, "get", side_effect=_capture):
        pc.get("/v3/reference/tickers", params={"market": "stocks"})

    assert "apiKey=" not in captured["url"]
    assert captured["params"]["apiKey"] == "test-polygon-key"


def test_get_retries_on_429_then_succeeds(monkeypatch):
    pc.reset_bucket_for_tests()
    monkeypatch.setattr(pc.time, "sleep", lambda _: None)
    responses = [_resp(429, headers={"Retry-After": "0"}), _resp(200, {"ok": True})]
    call_count = [0]

    def _side(*a, **kw):
        r = responses[call_count[0]]
        call_count[0] += 1
        return r

    with patch.object(pc.requests, "get", side_effect=_side):
        out = pc.get("/v3/test")
    assert out == {"ok": True}
    assert call_count[0] == 2


def test_get_returns_none_on_4xx_no_retry(monkeypatch):
    pc.reset_bucket_for_tests()
    monkeypatch.setattr(pc.time, "sleep", lambda _: None)
    call_count = [0]

    def _side(*a, **kw):
        call_count[0] += 1
        return _resp(404)

    with patch.object(pc.requests, "get", side_effect=_side):
        out = pc.get("/v3/test")
    assert out is None
    assert call_count[0] == 1  # no retry on permanent client error


def test_get_retries_on_5xx_then_succeeds(monkeypatch):
    pc.reset_bucket_for_tests()
    monkeypatch.setattr(pc.time, "sleep", lambda _: None)
    responses = [_resp(503), _resp(200, {"ok": True})]
    call_count = [0]

    def _side(*a, **kw):
        r = responses[call_count[0]]
        call_count[0] += 1
        return r

    with patch.object(pc.requests, "get", side_effect=_side):
        out = pc.get("/v3/test")
    assert out == {"ok": True}


def test_get_gives_up_after_max_retries(monkeypatch):
    pc.reset_bucket_for_tests()
    monkeypatch.setattr(pc.time, "sleep", lambda _: None)
    with patch.object(pc.requests, "get", return_value=_resp(429)):
        out = pc.get("/v3/test", max_retries=2)
    assert out is None


def test_get_handles_network_error(monkeypatch):
    pc.reset_bucket_for_tests()
    monkeypatch.setattr(pc.time, "sleep", lambda _: None)
    with patch.object(pc.requests, "get", side_effect=requests.ConnectionError("down")):
        out = pc.get("/v3/test", max_retries=1)
    assert out is None


def test_token_bucket_rate_limits():
    bucket = pc.TokenBucket(rate=20.0, capacity=2.0)
    bucket.acquire()
    bucket.acquire()
    start = time.monotonic()
    bucket.acquire()  # third acquire requires refill of ~1 token at 20/s = 0.05s
    elapsed = time.monotonic() - start
    assert elapsed >= 0.04  # allow timer slack on Windows


def test_paginate_follows_next_url(monkeypatch):
    pc.reset_bucket_for_tests()
    page1 = {
        "results": [1, 2],
        "next_url": "https://api.polygon.io/v3/test?cursor=abc",
    }
    page2 = {"results": [3, 4]}
    responses = [_resp(200, page1), _resp(200, page2)]
    call_count = [0]

    def _side(*a, **kw):
        r = responses[call_count[0]]
        call_count[0] += 1
        return r

    with patch.object(pc.requests, "get", side_effect=_side):
        pages = list(pc.paginate("/v3/test"))
    assert len(pages) == 2
    assert pages[0]["results"] == [1, 2]
    assert pages[1]["results"] == [3, 4]


def test_paginate_stops_on_get_failure(monkeypatch):
    pc.reset_bucket_for_tests()
    monkeypatch.setattr(pc.time, "sleep", lambda _: None)
    page1 = {"results": [1], "next_url": "https://api.polygon.io/v3/test?cursor=x"}
    responses = [_resp(200, page1), _resp(500), _resp(500), _resp(500), _resp(500)]
    call_count = [0]

    def _side(*a, **kw):
        r = responses[min(call_count[0], len(responses) - 1)]
        call_count[0] += 1
        return r

    with patch.object(pc.requests, "get", side_effect=_side):
        pages = list(pc.paginate("/v3/test", timeout=1))
    assert len(pages) == 1  # second page failed, generator stopped
