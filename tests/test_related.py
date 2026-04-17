from unittest.mock import patch, MagicMock
from catalysts.related import fetch_related, _cache


def _mock_response(tickers):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"results": [{"ticker": t} for t in tickers]}
    resp.raise_for_status = MagicMock()
    return resp


def test_fetch_related_returns_tickers(monkeypatch):
    _cache.clear()
    monkeypatch.setenv("POLYGON_API_KEY", "test_key")
    with patch("catalysts.related.requests.get",
               return_value=_mock_response(["AMD", "INTC", "AVGO"])):
        result = fetch_related("NVDA")
    assert result == ["AMD", "INTC", "AVGO"]


def test_fetch_related_limits_results(monkeypatch):
    _cache.clear()
    monkeypatch.setenv("POLYGON_API_KEY", "test_key")
    with patch("catalysts.related.requests.get",
               return_value=_mock_response(["A", "B", "C", "D", "E", "F", "G"])):
        result = fetch_related("NVDA", limit=3)
    assert len(result) == 3


def test_fetch_related_caches(monkeypatch):
    _cache.clear()
    monkeypatch.setenv("POLYGON_API_KEY", "test_key")
    call_count = [0]
    def _counting(*a, **kw):
        call_count[0] += 1
        return _mock_response(["AMD"])
    with patch("catalysts.related.requests.get", side_effect=_counting):
        fetch_related("NVDA")
        fetch_related("NVDA")
    assert call_count[0] == 1


def test_fetch_related_handles_failure(monkeypatch):
    _cache.clear()
    monkeypatch.setenv("POLYGON_API_KEY", "test_key")
    with patch("catalysts.related.requests.get", side_effect=Exception("timeout")):
        result = fetch_related("NVDA")
    assert result == []


def test_fetch_related_no_key(monkeypatch):
    _cache.clear()
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    result = fetch_related("NVDA")
    assert result == []
