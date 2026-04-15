from catalysts import db as cdb

def test_migrate_creates_all_tables(tmp_db):
    cdb.migrate(tmp_db)
    names = {r[0] for r in tmp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"catalysts", "alert_log", "universe"} <= names

def test_upsert_universe_and_load_active(tmp_db):
    cdb.migrate(tmp_db)
    cdb.upsert_universe(tmp_db, "NVDA", "NVIDIA Corp")
    cdb.upsert_universe(tmp_db, "AAPL", "Apple Inc")
    cdb.deactivate_ticker(tmp_db, "AAPL")
    active = cdb.load_active_universe(tmp_db)
    assert active == ["NVDA"]

def test_seed_from_list_only_when_empty(tmp_db):
    cdb.migrate(tmp_db)
    cdb.seed_universe_if_empty(tmp_db, ["NVDA", "AAPL"])
    cdb.seed_universe_if_empty(tmp_db, ["TSLA"])  # should be a no-op
    active = cdb.load_active_universe(tmp_db)
    assert set(active) == {"NVDA", "AAPL"}

def test_persist_catalyst_is_idempotent(tmp_db):
    cdb.migrate(tmp_db)
    from catalysts.types import RawCatalyst, ScoredItem, RerankedItem
    raw = RawCatalyst("NVDA", "edgar", "acc-1", "NVDA to acquire X", "https://sec.gov/x", "2026-04-13T10:00:00Z", "8-K")
    scored = ScoredItem(raw, 55, ("m&a-confirmed",), ("to acquire",))
    item = RerankedItem(scored, 9, "Clear M&A", 87)
    cid1 = cdb.persist_catalyst(tmp_db, item)
    cid2 = cdb.persist_catalyst(tmp_db, item)
    assert cid1 == cid2  # UNIQUE(source, source_id) returns existing id
    n = tmp_db.execute("SELECT COUNT(*) FROM catalysts").fetchone()[0]
    assert n == 1
