from datetime import date
from unittest.mock import patch, MagicMock

import requests

from catalysts.options import fetch_chain, OptionContract
from tests.fixtures.polygon_snapshot import AAPL_SNAPSHOT


def _mock_get(snapshot_data):
    resp = MagicMock()
    resp.json.return_value = snapshot_data
    resp.raise_for_status.return_value = None
    return resp


def test_fetch_chain_returns_contracts():
    with patch("catalysts.options.requests.get", return_value=_mock_get(AAPL_SNAPSHOT)):
        contracts = fetch_chain("AAPL", ref_date=date(2026, 4, 15))
    assert len(contracts) > 0
    assert all(isinstance(c, OptionContract) for c in contracts)


def test_fetch_chain_filters_ask_over_2():
    with patch("catalysts.options.requests.get", return_value=_mock_get(AAPL_SNAPSHOT)):
        contracts = fetch_chain("AAPL", max_ask=2.00, ref_date=date(2026, 4, 15))
    asks = [c.ask for c in contracts]
    assert all(a <= 2.00 for a in asks)
    assert all(a > 0 for a in asks)


def test_fetch_chain_filters_dte_window():
    with patch("catalysts.options.requests.get", return_value=_mock_get(AAPL_SNAPSHOT)):
        contracts = fetch_chain("AAPL", min_dte=7, max_dte=28, ref_date=date(2026, 4, 15))
    for c in contracts:
        assert 7 <= c.dte <= 28


def test_fetch_chain_drops_empty_greeks():
    with patch("catalysts.options.requests.get", return_value=_mock_get(AAPL_SNAPSHOT)):
        contracts = fetch_chain("AAPL", ref_date=date(2026, 4, 15))
    for c in contracts:
        assert c.iv is not None


def test_fetch_chain_http_error_returns_empty():
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = requests.HTTPError("429 rate limit")
    with patch("catalysts.options.requests.get", return_value=mock_resp):
        contracts = fetch_chain("AAPL", ref_date=date(2026, 4, 15))
    assert contracts == []
