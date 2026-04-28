from catalysts import db as cdb
from catalysts.weight_learner import (
    compute_tag_multipliers,
    persist_tag_multipliers,
    load_catalyst_tag_multipliers,
)


def _seed_events(conn, catalyst_type, n_wins, n_losses):
    acc = cdb.create_account(
        conn, name=catalyst_type, type="taxable",
        broker="x", opened_date="2024-01-01",
    )
    for i in range(n_wins):
        eid, _ = cdb.insert_event(
            conn, account_id=acc, ticker=f"W{i}",
            event_date=f"2026-04-{1 + i:02d}", move_pct=8.0, move_window="1d",
            position_qty=1.0, value_before=100.0, value_after=108.0,
            pnl_dollars=8.0,
        )
        cdb.update_event(conn, eid, status="confirmed", catalyst_type=catalyst_type)
    for i in range(n_losses):
        eid, _ = cdb.insert_event(
            conn, account_id=acc, ticker=f"L{i}",
            event_date=f"2026-03-{1 + i:02d}", move_pct=-6.0, move_window="1d",
            position_qty=1.0, value_before=100.0, value_after=94.0,
            pnl_dollars=-6.0,
        )
        cdb.update_event(conn, eid, status="confirmed", catalyst_type=catalyst_type)


def test_insufficient_events_returns_neutral(tmp_db):
    cdb.migrate(tmp_db)
    _seed_events(tmp_db, "earnings", n_wins=2, n_losses=1)  # < min_events
    mults = compute_tag_multipliers(tmp_db)
    assert mults.get("earnings", 1.0) == 1.0


def test_high_hit_rate_boosts_multiplier(tmp_db):
    cdb.migrate(tmp_db)
    _seed_events(tmp_db, "earnings", n_wins=8, n_losses=2)  # 80% hit rate
    mults = compute_tag_multipliers(tmp_db)
    assert mults["earnings"] == 1.30


def test_low_hit_rate_drops_multiplier(tmp_db):
    cdb.migrate(tmp_db)
    _seed_events(tmp_db, "rumor", n_wins=2, n_losses=8)  # 20% hit rate
    mults = compute_tag_multipliers(tmp_db)
    assert mults["rumor"] == 0.60


def test_neutral_hit_rate_keeps_multiplier(tmp_db):
    cdb.migrate(tmp_db)
    _seed_events(tmp_db, "m&a", n_wins=5, n_losses=5)  # 50% exactly
    mults = compute_tag_multipliers(tmp_db)
    assert mults["m&a"] == 1.00


def test_persist_and_load_roundtrip(tmp_db):
    cdb.migrate(tmp_db)
    _seed_events(tmp_db, "earnings", n_wins=8, n_losses=2)
    mults, stats = compute_tag_multipliers(tmp_db, return_stats=True)
    persist_tag_multipliers(tmp_db, mults, stats)
    rows = cdb.load_tag_multipliers(tmp_db)
    assert "earnings" in rows
    assert rows["earnings"]["multiplier"] == 1.30
    assert rows["earnings"]["n_events"] == 10


def test_load_catalyst_tag_multipliers_expands_mapping(tmp_db):
    cdb.migrate(tmp_db)
    _seed_events(tmp_db, "m&a", n_wins=8, n_losses=2)  # -> 1.30
    mults, stats = compute_tag_multipliers(tmp_db, return_stats=True)
    persist_tag_multipliers(tmp_db, mults, stats)
    tag_mults = load_catalyst_tag_multipliers(tmp_db)
    # "m&a" catalyst_type maps to both "m&a-confirmed" and "m&a-rumor"
    assert tag_mults.get("m&a-confirmed") == 1.30
    assert tag_mults.get("m&a-rumor") == 1.30


def test_empty_db_returns_empty_dict(tmp_db):
    cdb.migrate(tmp_db)
    tag_mults = load_catalyst_tag_multipliers(tmp_db)
    assert tag_mults == {}
