from catalysts import db as cdb
from portfolios import analytics


def _seed(tmp_db):
    cdb.migrate(tmp_db)
    a = cdb.create_account(tmp_db, name="A", type="taxable",
                             broker="fidelity", opened_date="2024-01-01")
    eid, _ = cdb.insert_event(
        tmp_db, account_id=a, ticker="NVDA", event_date="2026-04-15",
        move_pct=8.2, move_window="1d", position_qty=10.0,
        value_before=5000.0, value_after=5410.0, pnl_dollars=410.0,
    )
    cdb.update_event(tmp_db, eid, status="confirmed",
                      catalyst_type="earnings")

    eid2, _ = cdb.insert_event(
        tmp_db, account_id=a, ticker="AAPL", event_date="2026-04-14",
        move_pct=-6.0, move_window="1d", position_qty=5.0,
        value_before=1000.0, value_after=940.0, pnl_dollars=-60.0,
    )
    cdb.update_event(tmp_db, eid2, status="confirmed",
                      catalyst_type="earnings")

    eid3, _ = cdb.insert_event(
        tmp_db, account_id=a, ticker="TSLA", event_date="2026-04-13",
        move_pct=7.0, move_window="1d", position_qty=2.0,
        value_before=500.0, value_after=535.0, pnl_dollars=35.0,
    )
    cdb.update_event(tmp_db, eid3, status="confirmed",
                      catalyst_type="rumor")
    return a


def test_catalyst_type_pnl_rollup(tmp_db):
    _seed(tmp_db)
    rows = analytics.pnl_by_catalyst_type(tmp_db)
    by_type = {r["catalyst_type"]: r for r in rows}
    assert by_type["earnings"]["net_pnl"] == 350.0  # 410 - 60
    assert by_type["earnings"]["wins"] == 1
    assert by_type["earnings"]["losses"] == 1
    assert by_type["rumor"]["net_pnl"] == 35.0
    assert by_type["rumor"]["wins"] == 1
    assert by_type["rumor"]["losses"] == 0


def test_hit_rate_by_catalyst_type(tmp_db):
    _seed(tmp_db)
    rows = analytics.hit_rate_by_catalyst_type(tmp_db)
    by_type = {r["catalyst_type"]: r for r in rows}
    assert abs(by_type["earnings"]["hit_rate"] - 0.5) < 0.001  # 1/2
    assert by_type["rumor"]["hit_rate"] == 1.0


def test_linked_vs_unlinked(tmp_db):
    acc = _seed(tmp_db)
    # Pre-seed one catalyst and attach the NVDA event to it
    tmp_db.execute(
        "INSERT INTO catalysts(ticker,source,source_id,headline,url,"
        "published_at,kw_score,final_score,seen,fetched_at) "
        "VALUES('NVDA','edgar','x','x','x','2026-04-15',60,85,0,'x')"
    )
    cid = tmp_db.execute(
        "SELECT id FROM catalysts WHERE ticker='NVDA'"
    ).fetchone()[0]
    tmp_db.execute("UPDATE events SET catalyst_id=? WHERE ticker='NVDA'",
                     (cid,))
    tmp_db.commit()

    counts = analytics.linked_vs_unlinked(tmp_db)
    assert counts["linked"] == 1
    assert counts["unlinked"] == 2


def test_events_for_catalyst(tmp_db):
    acc = _seed(tmp_db)
    tmp_db.execute(
        "INSERT INTO catalysts(ticker,source,source_id,headline,url,"
        "published_at,kw_score,final_score,seen,fetched_at) "
        "VALUES('NVDA','edgar','x','x','x','2026-04-15',60,85,0,'x')"
    )
    cid = tmp_db.execute(
        "SELECT id FROM catalysts WHERE ticker='NVDA'"
    ).fetchone()[0]
    tmp_db.execute("UPDATE events SET catalyst_id=? WHERE ticker='NVDA'",
                     (cid,))
    tmp_db.commit()
    rows = analytics.events_for_catalyst(tmp_db, cid)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "NVDA"
    assert rows[0]["account_name"] == "A"
