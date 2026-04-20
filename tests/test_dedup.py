from catalysts import db as cdb
from catalysts.dedup import filter_unseen, recently_alerted
from catalysts.types import RawCatalyst, ScoredItem, RerankedItem


def _mk() -> RerankedItem:
    raw = RawCatalyst("NVDA", "gnews", "gnews:NVDA:1", "in talks",
                      "https://x", "2026-04-13T10:00:00Z", None)
    return RerankedItem(ScoredItem(raw, 60, ("m&a-rumor",), ("in talks",)),
                        llm_score=9, rationale="rumor", final_score=85)


def test_filter_unseen(tmp_db):
    cdb.migrate(tmp_db)
    item = _mk()
    assert len(filter_unseen(tmp_db, [item.scored.raw])) == 1
    cdb.persist_catalyst(tmp_db, item)
    assert len(filter_unseen(tmp_db, [item.scored.raw])) == 0


def test_recently_alerted(tmp_db):
    from datetime import datetime, timezone
    cdb.migrate(tmp_db)
    assert recently_alerted(tmp_db, "NVDA", 8, hours=6) is False
    # Insert a real catalyst so the FK constraint on alert_log is satisfied
    cat_id = cdb.persist_catalyst(tmp_db, _mk())
    sent_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tmp_db.execute(
        "INSERT INTO alert_log(catalyst_id,ticker,score_bucket,channels,sent_at,ok) "
        "VALUES(?,'NVDA',8,'[\"email\"]', ?, 1)",
        (cat_id, sent_at),
    )
    tmp_db.commit()
    assert recently_alerted(tmp_db, "NVDA", 8, hours=6) is True
    assert recently_alerted(tmp_db, "AAPL", 8, hours=6) is False
