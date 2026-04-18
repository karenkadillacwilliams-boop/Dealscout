from unittest.mock import patch, MagicMock
from catalysts import polygon_client as pc
from catalysts.related import fetch_related, _cache


def _mock_response(tickers):
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {}
    resp.json.return_value = {"results": [{"ticker": t} for t in tickers]}
    resp.raise_for_status = MagicMock()
    return resp


def test_fetch_related_returns_tickers(monkeypatch):
    pc.reset_bucket_for_tests()
    _cache.clear()
    monkeypatch.setenv("POLYGON_API_KEY", "test_key")
    with patch.object(pc.requests, "get",
                      return_value=_mock_response(["AMD", "INTC", "AVGO"])):
        result = fetch_related("NVDA")
    assert result == ["AMD", "INTC", "AVGO"]


def test_fetch_related_limits_results(monkeypatch):
    pc.reset_bucket_for_tests()
    _cache.clear()
    monkeypatch.setenv("POLYGON_API_KEY", "test_key")
    with patch.object(pc.requests, "get",
                      return_value=_mock_response(["A", "B", "C", "D", "E", "F", "G"])):
        result = fetch_related("NVDA", limit=3)
    assert len(result) == 3


def test_fetch_related_caches(monkeypatch):
    pc.reset_bucket_for_tests()
    _cache.clear()
    monkeypatch.setenv("POLYGON_API_KEY", "test_key")
    call_count = [0]

    def _counting(*a, **kw):
        call_count[0] += 1
        return _mock_response(["AMD"])

    with patch.object(pc.requests, "get", side_effect=_counting):
        fetch_related("NVDA")
        fetch_related("NVDA")
    assert call_count[0] == 1


def test_fetch_related_handles_failure(monkeypatch):
    pc.reset_bucket_for_tests()
    _cache.clear()
    monkeypatch.setenv("POLYGON_API_KEY", "test_key")
    monkeypatch.setattr(pc.time, "sleep", lambda _: None)
    with patch.object(pc.requests, "get", side_effect=Exception("timeout")):
        result = fetch_related("NVDA")
    assert result == []


def test_fetch_related_no_key(monkeypatch):
    pc.reset_bucket_for_tests()
    _cache.clear()
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    result = fetch_related("NVDA")
    assert result == []


def test_fetch_related_persists_to_db(tmp_db, monkeypatch):
    """With a conn passed, the API result is written through to related_tickers."""
    from catalysts import db as cdb
    pc.reset_bucket_for_tests()
    _cache.clear()
    cdb.migrate(tmp_db)
    monkeypatch.setenv("POLYGON_API_KEY", "test_key")

    with patch.object(pc.requests, "get",
                      return_value=_mock_response(["AMD", "INTC", "AVGO"])):
        result = fetch_related("NVDA", conn=tmp_db)
    assert result == ["AMD", "INTC", "AVGO"]

    cached = cdb.load_related_tickers(tmp_db, "NVDA")
    assert cached == ["AMD", "INTC", "AVGO"]


def test_fetch_related_hits_db_cache_after_process_restart(tmp_db, monkeypatch):
    """The in-memory _cache evaporates on restart — the DB cache must cover it.

    Simulates: first call populates DB, in-memory cache is cleared (as on
    poller restart), second call returns from DB without hitting Polygon.
    """
    from catalysts import db as cdb
    pc.reset_bucket_for_tests()
    _cache.clear()
    cdb.migrate(tmp_db)
    monkeypatch.setenv("POLYGON_API_KEY", "test_key")
    call_count = [0]

    def _counting(*a, **kw):
        call_count[0] += 1
        return _mock_response(["AMD"])

    with patch.object(pc.requests, "get", side_effect=_counting):
        fetch_related("NVDA", conn=tmp_db)
        _cache.clear()  # simulate process restart
        fetch_related("NVDA", conn=tmp_db)

    assert call_count[0] == 1  # second call served from DB, no API hit


def test_fetch_related_respects_limit_from_db_cache(tmp_db, monkeypatch):
    from catalysts import db as cdb
    pc.reset_bucket_for_tests()
    _cache.clear()
    cdb.migrate(tmp_db)
    cdb.upsert_related_tickers(tmp_db, "NVDA", ["A", "B", "C", "D", "E", "F"])

    result = fetch_related("NVDA", limit=3, conn=tmp_db)
    assert result == ["A", "B", "C"]


def test_fetch_related_ignores_stale_db_cache(tmp_db, monkeypatch):
    """Rows older than TTL must trigger a refetch, not be returned stale."""
    from catalysts import db as cdb
    pc.reset_bucket_for_tests()
    _cache.clear()
    cdb.migrate(tmp_db)
    monkeypatch.setenv("POLYGON_API_KEY", "test_key")

    # Manually insert a row with a stale fetched_at
    tmp_db.execute(
        "INSERT INTO related_tickers(ticker, related, fetched_at) VALUES(?,?,?)",
        ("NVDA", '["STALE"]', "2020-01-01T00:00:00+00:00"),
    )
    tmp_db.commit()

    with patch.object(pc.requests, "get", return_value=_mock_response(["FRESH"])):
        result = fetch_related("NVDA", conn=tmp_db)
    assert result == ["FRESH"]
