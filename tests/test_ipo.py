from unittest.mock import patch, MagicMock
from catalysts import polygon_client as pc
from catalysts.ipo import fetch_recent_ipos


def _mock_response(tickers_data):
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {}
    resp.json.return_value = {"results": tickers_data}
    resp.raise_for_status = MagicMock()
    return resp


def test_fetch_recent_ipos(monkeypatch):
    pc.reset_bucket_for_tests()
    data = [
        {"ticker": "NEWCO", "name": "NewCo Inc", "list_date": "2026-04-10",
         "market": "stocks", "primary_exchange": "XNAS", "currency_name": "usd"},
        {"ticker": "FRESH", "name": "Fresh Corp", "list_date": "2026-04-05",
         "market": "stocks", "primary_exchange": "XNYS", "currency_name": "usd"},
    ]
    with patch.object(pc.requests, "get", return_value=_mock_response(data)):
        ipos = fetch_recent_ipos(lookback_days=30)
    assert len(ipos) == 2
    assert ipos[0].ticker == "NEWCO"
    assert ipos[1].primary_exchange == "XNYS"


def test_fetch_ipos_no_key(monkeypatch):
    pc.reset_bucket_for_tests()
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    ipos = fetch_recent_ipos()
    assert ipos == []


def test_fetch_ipos_api_failure(monkeypatch):
    pc.reset_bucket_for_tests()
    monkeypatch.setattr(pc.time, "sleep", lambda _: None)
    with patch.object(pc.requests, "get", side_effect=Exception("timeout")):
        ipos = fetch_recent_ipos()
    assert ipos == []


def test_fetch_ipos_does_not_leak_key_in_url(monkeypatch):
    """Regression: ipo.py used to embed apiKey= directly in the URL string."""
    pc.reset_bucket_for_tests()
    captured_urls: list[str] = []

    def _capture(url, **kwargs):
        captured_urls.append(url)
        return _mock_response([])

    with patch.object(pc.requests, "get", side_effect=_capture):
        fetch_recent_ipos(lookback_days=30)

    assert captured_urls
    for url in captured_urls:
        assert "apiKey=" not in url
