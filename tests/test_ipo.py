from unittest.mock import patch, MagicMock
from catalysts.ipo import fetch_recent_ipos


def _mock_response(tickers_data):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"results": tickers_data}
    resp.raise_for_status = MagicMock()
    return resp


def test_fetch_recent_ipos(monkeypatch):
    data = [
        {"ticker": "NEWCO", "name": "NewCo Inc", "list_date": "2026-04-10",
         "market": "stocks", "primary_exchange": "XNAS", "currency_name": "usd"},
        {"ticker": "FRESH", "name": "Fresh Corp", "list_date": "2026-04-05",
         "market": "stocks", "primary_exchange": "XNYS", "currency_name": "usd"},
    ]
    with patch("catalysts.ipo.requests.get", return_value=_mock_response(data)):
        ipos = fetch_recent_ipos(lookback_days=30)
    assert len(ipos) == 2
    assert ipos[0].ticker == "NEWCO"
    assert ipos[1].primary_exchange == "XNYS"


def test_fetch_ipos_no_key(monkeypatch):
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    ipos = fetch_recent_ipos()
    assert ipos == []


def test_fetch_ipos_api_failure(monkeypatch):
    with patch("catalysts.ipo.requests.get", side_effect=Exception("timeout")):
        ipos = fetch_recent_ipos()
    assert ipos == []
