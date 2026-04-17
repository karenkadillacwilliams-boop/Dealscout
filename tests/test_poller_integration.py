from catalyst_poller import run_once
from catalysts import db as cdb, edgar, news, rerank
from catalysts.types import RawCatalyst
from alerts import dispatcher


def _stub_fetchers(monkeypatch, items):
    monkeypatch.setattr(edgar, "fetch", lambda *a, **k: items)
    monkeypatch.setattr(news, "fetch_yfinance", lambda *a, **k: [])
    monkeypatch.setattr(news, "fetch_gnews_rss", lambda *a, **k: [])
    monkeypatch.setattr(news, "fetch_polygon_news", lambda *a, **k: [])


def _stub_rerank(monkeypatch):
    def _rr(items, batch=10):
        from catalysts.types import RerankedItem
        return [RerankedItem(scored=s, llm_score=9,
                             rationale="rumor", final_score=85) for s in items]
    monkeypatch.setattr(rerank, "rerank_batched", _rr)


def _stub_alerts(monkeypatch, calls):
    monkeypatch.setattr(dispatcher, "send",
                        lambda item, **kw: (calls.append(item) or (True, ["stub"])))


def test_end_to_end_idempotent(monkeypatch, tmp_path):
    monkeypatch.setattr(cdb, "DB_PATH", tmp_path / "d.db")
    conn = cdb.connect(tmp_path / "d.db")
    cdb.migrate(conn)
    cdb.upsert_universe(conn, "NVDA")
    conn.close()

    raw = [RawCatalyst("NVDA", "edgar", "edgar:NVDA:acc1",
                       "NVDA to acquire Widgets", "https://sec.gov/x",
                       "2026-04-13T10:00:00Z", "8-K")]
    _stub_fetchers(monkeypatch, raw)
    _stub_rerank(monkeypatch)
    calls: list = []
    _stub_alerts(monkeypatch, calls)

    run_once()
    run_once()  # second run must be a no-op for persistence AND alerts

    conn = cdb.connect(tmp_path / "d.db")
    n_cat = conn.execute("SELECT COUNT(*) FROM catalysts").fetchone()[0]
    n_alert = conn.execute("SELECT COUNT(*) FROM alert_log").fetchone()[0]
    conn.close()
    assert n_cat == 1
    assert n_alert == 1  # 6h dedup suppressed the second
    assert len(calls) == 1
