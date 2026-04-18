from unittest.mock import patch, MagicMock

from catalysts import polygon_client as pc
from catalysts.iv_rank import compute_iv_rank, compute_atm_avg_iv
from catalysts.options import OptionContract


def _contract(strike, iv, underlying=195.0):
    return OptionContract(
        ticker="AAPL", contract_ticker=f"O:AAPL260425C00{int(strike*1000):08d}",
        contract_type="call", strike=strike, expiration_date="2026-04-25",
        dte=10, ask=1.00, bid=0.90, mid=0.95, volume=100, open_interest=500,
        iv=iv, delta=0.3, gamma=0.02, theta=-0.05, vega=0.1,
        underlying_price=underlying,
    )


def test_compute_atm_avg_iv():
    contracts = [
        _contract(190.0, 0.40),  # ATM-1
        _contract(195.0, 0.42),  # ATM
        _contract(200.0, 0.44),  # ATM+1
        _contract(210.0, 0.55),  # out of range
        _contract(250.0, 0.70),  # far OTM
    ]
    avg = compute_atm_avg_iv(contracts, underlying_price=195.0)
    assert abs(avg - 0.42) < 0.01  # (0.40 + 0.42 + 0.44) / 3


def test_compute_atm_avg_iv_empty():
    assert compute_atm_avg_iv([], underlying_price=195.0) is None


def test_compute_iv_rank_with_history():
    history = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60]
    rank = compute_iv_rank(0.35, history)
    assert 10 < rank < 30  # 0.35 is near the low end


def test_compute_iv_rank_at_extremes():
    history = [0.30, 0.35, 0.40, 0.45, 0.50]
    assert compute_iv_rank(0.30, history) < 20
    assert compute_iv_rank(0.50, history) > 80


def test_compute_iv_rank_insufficient_history():
    rank = compute_iv_rank(0.40, [0.40])
    assert rank == 50.0  # default when < 5 data points


def _mock_aggs_response(num_bars=50):
    """Generate mock daily OHLC bars."""
    import time
    base_price = 100.0
    bars = []
    start_ts = int(time.time() * 1000) - (num_bars * 86400 * 1000)
    for i in range(num_bars):
        price = base_price + i * 0.5 + (i % 3 - 1) * 2  # slight trend + noise
        bars.append({
            "t": start_ts + i * 86400 * 1000,
            "o": price - 0.5,
            "h": price + 1.0,
            "l": price - 1.0,
            "c": price,
            "v": 1000000,
        })
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {}
    resp.json.return_value = {"results": bars}
    resp.raise_for_status = MagicMock()
    return resp


def test_backfill_realized_vol(tmp_db, monkeypatch):
    from catalysts import db as cdb
    from catalysts.iv_rank import backfill_realized_vol
    pc.reset_bucket_for_tests()
    cdb.migrate(tmp_db)

    with patch.object(pc.requests, "get", return_value=_mock_aggs_response(50)):
        result = backfill_realized_vol("AAPL", tmp_db)

    assert result is True
    count = tmp_db.execute("SELECT COUNT(*) FROM iv_history WHERE ticker='AAPL'").fetchone()[0]
    assert count > 0


def test_backfill_skips_if_history_exists(tmp_db, monkeypatch):
    from catalysts import db as cdb
    from catalysts.iv_rank import backfill_realized_vol
    pc.reset_bucket_for_tests()
    cdb.migrate(tmp_db)

    # Pre-populate with 5 entries
    for i in range(5):
        cdb.upsert_iv_history(tmp_db, "AAPL", f"2026-04-{10+i:02d}", 0.30 + i * 0.01)

    result = backfill_realized_vol("AAPL", tmp_db)
    assert result is False  # skipped


def test_backfill_no_key(tmp_db, monkeypatch):
    from catalysts import db as cdb
    from catalysts.iv_rank import backfill_realized_vol
    pc.reset_bucket_for_tests()
    cdb.migrate(tmp_db)
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)

    result = backfill_realized_vol("AAPL", tmp_db)
    assert result is False
