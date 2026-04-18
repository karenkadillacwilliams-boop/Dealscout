from unittest.mock import patch, MagicMock

from catalyst_poller import run_once
from catalysts import db as cdb, edgar, news, rerank, polygon_client as pc
from catalysts.types import RawCatalyst, RerankedItem
from alerts import dispatcher
from tests.fixtures.polygon_snapshot import AAPL_SNAPSHOT


def _stub_fetchers(monkeypatch, items):
    monkeypatch.setattr(edgar, "fetch", lambda *a, **k: items)
    monkeypatch.setattr(news, "fetch_yfinance", lambda *a, **k: [])
    monkeypatch.setattr(news, "fetch_gnews_rss", lambda *a, **k: [])
    monkeypatch.setattr(news, "fetch_polygon_news", lambda *a, **k: [])


def _stub_rerank(monkeypatch):
    def _rr(items, batch=10):
        return [RerankedItem(scored=s, llm_score=9,
                             rationale="rumor", final_score=85) for s in items]
    monkeypatch.setattr(rerank, "rerank_batched", _rr)


def _mock_polygon_response(snapshot_data, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {}
    resp.json.return_value = snapshot_data
    resp.raise_for_status = MagicMock()
    return resp


def test_poller_fetches_options_and_enriches_alert(monkeypatch, tmp_path):
    pc.reset_bucket_for_tests()
    monkeypatch.setattr(cdb, "DB_PATH", tmp_path / "d.db")
    conn = cdb.connect(tmp_path / "d.db")
    cdb.migrate(conn)
    cdb.upsert_universe(conn, "AAPL")
    conn.close()

    raw = [RawCatalyst("AAPL", "edgar", "edgar:AAPL:acc1",
                       "AAPL to acquire WidgetCo",
                       "https://sec.gov/x", "2026-04-15T10:00:00Z", "8-K")]
    _stub_fetchers(monkeypatch, raw)
    _stub_rerank(monkeypatch)

    alert_calls: list = []
    monkeypatch.setattr(dispatcher, "send",
                        lambda item, **kw: (alert_calls.append((item, kw)) or (True, ["stub"])))

    with patch.object(pc.requests, "get",
                      return_value=_mock_polygon_response(AAPL_SNAPSHOT)):
        run_once()

    assert len(alert_calls) == 1
    _, kw = alert_calls[0]
    assert "options_summary" in kw

    conn = cdb.connect(tmp_path / "d.db")
    opts = conn.execute("SELECT COUNT(*) FROM options_snapshot").fetchone()[0]
    conn.close()
    assert opts > 0


def test_poller_survives_polygon_failure(monkeypatch, tmp_path):
    pc.reset_bucket_for_tests()
    monkeypatch.setattr(pc.time, "sleep", lambda _: None)
    monkeypatch.setattr(cdb, "DB_PATH", tmp_path / "d.db")
    conn = cdb.connect(tmp_path / "d.db")
    cdb.migrate(conn)
    cdb.upsert_universe(conn, "AAPL")
    conn.close()

    raw = [RawCatalyst("AAPL", "edgar", "edgar:AAPL:acc2",
                       "AAPL to acquire GizmoInc",
                       "https://sec.gov/y", "2026-04-15T11:00:00Z", "8-K")]
    _stub_fetchers(monkeypatch, raw)
    _stub_rerank(monkeypatch)

    alert_calls: list = []
    monkeypatch.setattr(dispatcher, "send",
                        lambda item, **kw: (alert_calls.append((item, kw)) or (True, ["stub"])))

    # 503 keeps retrying through max_retries then returns None — alert still fires
    with patch.object(pc.requests, "get",
                      return_value=_mock_polygon_response({}, status=503)):
        run_once()

    assert len(alert_calls) == 1
    _, kw = alert_calls[0]
    assert kw.get("options_summary") is None
