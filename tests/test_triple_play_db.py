from catalysts import db as cdb


def test_upsert_and_load_triple_play(tmp_db):
    cdb.migrate(tmp_db)
    cdb.upsert_triple_play(
        tmp_db, ticker="AAPL", score=85.2,
        eps=80.0, revenue=75.0, guidance_delta=70.0,
        days=15, is_full=True, report_period="2026-04-05",
    )
    rows = cdb.load_triple_play(tmp_db)
    assert "AAPL" in rows
    assert abs(rows["AAPL"]["score"] - 85.2) < 0.01
    assert rows["AAPL"]["is_full_triple_play"] == 1


def test_load_triple_play_fresh(tmp_db):
    cdb.migrate(tmp_db)
    cdb.upsert_triple_play(
        tmp_db, ticker="NVDA", score=70.0, eps=70.0, revenue=None,
        guidance_delta=None, days=10, is_full=False,
        report_period="2026-04-10",
    )
    fresh = cdb.load_triple_play_fresh(tmp_db, max_age_hours=24)
    assert "NVDA" in fresh


def test_triple_play_upsert_replaces_row(tmp_db):
    cdb.migrate(tmp_db)
    cdb.upsert_triple_play(tmp_db, ticker="AAPL", score=60.0, eps=60.0,
                            revenue=None, guidance_delta=None, days=20,
                            is_full=False, report_period="2026-04-01")
    cdb.upsert_triple_play(tmp_db, ticker="AAPL", score=75.0, eps=75.0,
                            revenue=None, guidance_delta=None, days=5,
                            is_full=False, report_period="2026-04-15")
    rows = cdb.load_triple_play(tmp_db)
    assert rows["AAPL"]["score"] == 75.0
    assert rows["AAPL"]["days_since_report"] == 5
    count = tmp_db.execute("SELECT COUNT(*) FROM triple_play").fetchone()[0]
    assert count == 1
