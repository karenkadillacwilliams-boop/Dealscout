from datetime import date
from unittest.mock import patch, MagicMock

from catalysts import polygon_client as pc
from catalysts.options import fetch_chain, OptionContract
from tests.fixtures.polygon_snapshot import AAPL_SNAPSHOT


def _mock_get(snapshot_data, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {}
    resp.json.return_value = snapshot_data
    resp.raise_for_status.return_value = None
    return resp


def test_fetch_chain_returns_contracts():
    pc.reset_bucket_for_tests()
    with patch.object(pc.requests, "get", return_value=_mock_get(AAPL_SNAPSHOT)):
        contracts = fetch_chain("AAPL", ref_date=date(2026, 4, 15))
    assert len(contracts) > 0
    assert all(isinstance(c, OptionContract) for c in contracts)


def test_fetch_chain_filters_ask_over_2():
    pc.reset_bucket_for_tests()
    with patch.object(pc.requests, "get", return_value=_mock_get(AAPL_SNAPSHOT)):
        contracts = fetch_chain("AAPL", max_ask=2.00, ref_date=date(2026, 4, 15))
    asks = [c.ask for c in contracts]
    assert all(a <= 2.00 for a in asks)
    assert all(a > 0 for a in asks)


def test_fetch_chain_filters_dte_window():
    pc.reset_bucket_for_tests()
    with patch.object(pc.requests, "get", return_value=_mock_get(AAPL_SNAPSHOT)):
        contracts = fetch_chain("AAPL", min_dte=7, max_dte=28, ref_date=date(2026, 4, 15))
    for c in contracts:
        assert 7 <= c.dte <= 28


def test_fetch_chain_drops_empty_greeks():
    pc.reset_bucket_for_tests()
    with patch.object(pc.requests, "get", return_value=_mock_get(AAPL_SNAPSHOT)):
        contracts = fetch_chain("AAPL", ref_date=date(2026, 4, 15))
    for c in contracts:
        assert c.iv is not None


def test_fetch_chain_http_error_returns_empty(monkeypatch):
    pc.reset_bucket_for_tests()
    monkeypatch.setattr(pc.time, "sleep", lambda _: None)
    with patch.object(pc.requests, "get", return_value=_mock_get({}, status=429)):
        contracts = fetch_chain("AAPL", ref_date=date(2026, 4, 15))
    assert contracts == []
