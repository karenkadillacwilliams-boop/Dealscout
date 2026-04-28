from unittest.mock import patch, MagicMock
from catalysts import earnings


def _mock_resp(json_body, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_body
    r.raise_for_status = MagicMock()
    return r


def test_get_earnings_returns_none_without_key(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    assert earnings.get_earnings_data("AAPL") is None


def test_get_earnings_returns_none_when_eps_fetch_empty(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
    with patch.object(earnings.requests, "get", return_value=_mock_resp([])):
        assert earnings.get_earnings_data("AAPL") is None


def test_get_earnings_assembles_components(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
    # First call = /stock/earnings; second call = /stock/recommendation
    eps_resp = _mock_resp([{"period": "2026-04-01", "surprisePercent": 10.5}])
    rec_resp = _mock_resp([
        {"period": "2026-05-01", "strongBuy": 3, "buy": 5, "hold": 2, "sell": 0, "strongSell": 0},
        {"period": "2026-03-01", "strongBuy": 2, "buy": 4, "hold": 3, "sell": 1, "strongSell": 0},
    ])
    calls = [eps_resp, rec_resp]

    def _side(*a, **kw):
        return calls.pop(0)

    # Stub yfinance revenue fetcher so we don't hit the network
    monkeypatch.setattr(earnings, "_fetch_revenue_surprise_yfinance",
                        lambda t: 4.2)
    with patch.object(earnings.requests, "get", side_effect=_side):
        data = earnings.get_earnings_data("AAPL")
    assert data is not None
    assert data.ticker == "AAPL"
    assert data.eps_surprise_pct == 10.5
    assert data.revenue_surprise_pct == 4.2
    # bullish share before = (2+4)/10 = 0.6; after = (3+5)/10 = 0.8; delta pp = 20
    assert abs(data.bullish_share_delta - 20.0) < 0.01
