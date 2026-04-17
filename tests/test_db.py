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


def test_options_snapshot_table_exists(tmp_db):
    from catalysts import db as cdb
    cdb.migrate(tmp_db)
    tmp_db.execute("SELECT id, ticker, contract_ticker, contract_type, strike, "
                   "expiration_date, dte, ask, bid, mid, volume, open_interest, "
                   "iv, delta, gamma, theta, vega, underlying_price, "
                   "leverage_ratio, iv_rank, composite_score, fetched_at "
                   "FROM options_snapshot LIMIT 1")


def test_iv_history_table_exists(tmp_db):
    from catalysts import db as cdb
    cdb.migrate(tmp_db)
    tmp_db.execute("SELECT ticker, date, avg_iv FROM iv_history LIMIT 1")


def test_upsert_option_snapshot(tmp_db):
    from catalysts import db as cdb
    cdb.migrate(tmp_db)
    row = {
        "ticker": "AAPL", "contract_ticker": "O:AAPL260425C00200000",
        "contract_type": "call", "strike": 200.0, "expiration_date": "2026-04-25",
        "dte": 10, "ask": 1.50, "bid": 1.40, "mid": 1.45,
        "volume": 500, "open_interest": 2000,
        "iv": 0.42, "delta": 0.35, "gamma": 0.02, "theta": -0.05, "vega": 0.10,
        "underlying_price": 195.0, "leverage_ratio": 3.33, "iv_rank": 22.0,
        "composite_score": 7.6, "fetched_at": "2026-04-15T12:00:00Z",
    }
    cdb.upsert_option_snapshot(tmp_db, row)
    r = tmp_db.execute("SELECT * FROM options_snapshot WHERE contract_ticker=?",
                       ("O:AAPL260425C00200000",)).fetchone()
    assert r["ask"] == 1.50
    assert r["composite_score"] == 7.6
    # upsert overwrites
    row["ask"] = 1.60
    cdb.upsert_option_snapshot(tmp_db, row)
    r = tmp_db.execute("SELECT * FROM options_snapshot WHERE contract_ticker=?",
                       ("O:AAPL260425C00200000",)).fetchone()
    assert r["ask"] == 1.60


def test_upsert_iv_history(tmp_db):
    from catalysts import db as cdb
    cdb.migrate(tmp_db)
    cdb.upsert_iv_history(tmp_db, "AAPL", "2026-04-15", 0.35)
    cdb.upsert_iv_history(tmp_db, "AAPL", "2026-04-15", 0.40)  # overwrite
    r = tmp_db.execute("SELECT avg_iv FROM iv_history WHERE ticker='AAPL' AND date='2026-04-15'").fetchone()
    assert r["avg_iv"] == 0.40


def test_prune_iv_history(tmp_db):
    from catalysts import db as cdb
    cdb.migrate(tmp_db)
    for i in range(70):
        cdb.upsert_iv_history(tmp_db, "AAPL", f"2026-01-{i+1:02d}" if i < 28 else f"2026-02-{i-27:02d}", 0.30 + i * 0.001)
    cdb.prune_iv_history(tmp_db, keep_days=60)
    n = tmp_db.execute("SELECT COUNT(*) FROM iv_history WHERE ticker='AAPL'").fetchone()[0]
    assert n <= 60


def test_uoa_signals_table_exists(tmp_db):
    from catalysts import db as cdb
    cdb.migrate(tmp_db)
    tmp_db.execute("SELECT id, ticker, contract_ticker, contract_type, strike, "
                   "expiration_date, volume, open_interest, vol_oi_ratio, "
                   "ask, underlying_price, detected_at FROM uoa_signals LIMIT 1")


def test_insert_and_load_uoa(tmp_db):
    from catalysts import db as cdb
    cdb.migrate(tmp_db)
    sig = {
        "ticker": "AAPL", "contract_ticker": "O:AAPL260425C00200000",
        "contract_type": "call", "strike": 200.0, "expiration_date": "2026-04-25",
        "volume": 5000, "open_interest": 800, "vol_oi_ratio": 6.25,
        "ask": 1.50, "underlying_price": 195.0,
        "detected_at": "2026-04-16T14:00:00Z",
    }
    cdb.insert_uoa_signal(tmp_db, sig)
    rows = cdb.load_uoa_signals(tmp_db, hours=24)
    assert len(rows) == 1
    assert rows[0]["vol_oi_ratio"] == 6.25
