from unittest.mock import patch, MagicMock
from catalysts.market_status import is_market_open, _cache


def _mock_response(market_value):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"market": market_value}
    resp.raise_for_status = MagicMock()
    return resp


def test_market_open(monkeypatch):
    _cache.clear()
    with patch("catalysts.market_status.requests.get", return_value=_mock_response("open")):
        assert is_market_open() is True


def test_market_closed(monkeypatch):
    _cache.clear()
    with patch("catalysts.market_status.requests.get", return_value=_mock_response("closed")):
        assert is_market_open() is False


def test_extended_hours_is_open(monkeypatch):
    _cache.clear()
    with patch("catalysts.market_status.requests.get", return_value=_mock_response("extended-hours")):
        assert is_market_open() is True


def test_api_failure_defaults_open(monkeypatch):
    _cache.clear()
    with patch("catalysts.market_status.requests.get", side_effect=Exception("timeout")):
        assert is_market_open() is True


def test_cache_avoids_repeat_calls(monkeypatch):
    _cache.clear()
    call_count = [0]
    def _counting_get(*a, **kw):
        call_count[0] += 1
        return _mock_response("open")
    with patch("catalysts.market_status.requests.get", side_effect=_counting_get):
        is_market_open()
        is_market_open()
        assert call_count[0] == 1
