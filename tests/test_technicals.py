from catalysts.technicals import TechSignals, fetch_technicals
from unittest.mock import patch, MagicMock


def _mock_indicator(value_dict):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"results": {"values": [value_dict]}}
    resp.raise_for_status = MagicMock()
    return resp


def test_bullish_confluence(monkeypatch):
    responses = [
        _mock_indicator({"value": 25.0}),           # RSI oversold
        _mock_indicator({"histogram": 1.5}),          # MACD bullish
        _mock_indicator({"value": 180.0}),            # SMA50=180
    ]
    call_count = [0]
    def _side(*a, **kw):
        r = responses[call_count[0]]
        call_count[0] += 1
        return r
    with patch("catalysts.technicals.requests.get", side_effect=_side):
        sig = fetch_technicals("AAPL", last_price=195.0)
    assert sig.label == "Bullish"
    assert sig.score >= 2


def test_bearish_confluence(monkeypatch):
    responses = [
        _mock_indicator({"value": 75.0}),           # RSI overbought
        _mock_indicator({"histogram": -1.5}),         # MACD bearish
        _mock_indicator({"value": 200.0}),            # SMA50=200 (price below)
    ]
    call_count = [0]
    def _side(*a, **kw):
        r = responses[call_count[0]]
        call_count[0] += 1
        return r
    with patch("catalysts.technicals.requests.get", side_effect=_side):
        sig = fetch_technicals("AAPL", last_price=190.0)
    assert sig.label == "Bearish"
    assert sig.score <= -2


def test_neutral_mixed_signals(monkeypatch):
    responses = [
        _mock_indicator({"value": 50.0}),           # RSI neutral
        _mock_indicator({"histogram": 0.5}),          # MACD slightly bullish
        _mock_indicator({"value": 200.0}),            # SMA50=200 (price below)
    ]
    call_count = [0]
    def _side(*a, **kw):
        r = responses[call_count[0]]
        call_count[0] += 1
        return r
    with patch("catalysts.technicals.requests.get", side_effect=_side):
        sig = fetch_technicals("AAPL", last_price=190.0)
    assert sig.label == "Neutral"


def test_handles_api_failure(monkeypatch):
    with patch("catalysts.technicals.requests.get", side_effect=Exception("timeout")):
        sig = fetch_technicals("AAPL")
    assert sig.label == "—"
    assert sig.score == 0
