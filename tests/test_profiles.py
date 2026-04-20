from pathlib import Path
import json

from catalysts import db as cdb
from portfolios import importer, profiles


FIXTURES = Path(__file__).parent / "fixtures" / "broker_csvs"


def test_migrate_seeds_five_builtin_profiles(tmp_db):
    cdb.migrate(tmp_db)
    rows = tmp_db.execute(
        "SELECT name, broker, builtin FROM import_profiles ORDER BY name"
    ).fetchall()
    names = [r["name"] for r in rows]
    assert "Fidelity" in names
    assert "Schwab" in names
    assert "Robinhood" in names
    assert "MooMoo" in names
    assert "Vanguard" in names
    for r in rows:
        if r["name"] in ("Fidelity", "Schwab", "Robinhood", "MooMoo", "Vanguard"):
            assert r["builtin"] == 1


def test_migrate_is_idempotent_for_profiles(tmp_db):
    cdb.migrate(tmp_db)
    cdb.migrate(tmp_db)
    n = tmp_db.execute(
        "SELECT COUNT(*) FROM import_profiles WHERE builtin=1"
    ).fetchone()[0]
    assert n == 5


def _profile_from_row(row):
    return {
        "column_map": json.loads(row["column_map"]),
        "value_map":  json.loads(row["value_map"]),
        "row_filter": json.loads(row["row_filter"]),
    }


def test_fidelity_profile_parses_fixture(tmp_db):
    cdb.migrate(tmp_db)
    row = tmp_db.execute(
        "SELECT * FROM import_profiles WHERE name='Fidelity'"
    ).fetchone()
    profile = _profile_from_row(row)
    csv_text = (FIXTURES / "fidelity.csv").read_text()
    result = importer.apply_profile(csv_text, profile)
    assert len(result.valid) == 3
    tickers = [r.ticker for r in result.valid]
    assert tickers == ["NVDA", "AAPL", "NVDA"]


def test_schwab_profile_parses_fixture(tmp_db):
    cdb.migrate(tmp_db)
    row = tmp_db.execute(
        "SELECT * FROM import_profiles WHERE name='Schwab'"
    ).fetchone()
    profile = _profile_from_row(row)
    csv_text = (FIXTURES / "schwab.csv").read_text()
    result = importer.apply_profile(csv_text, profile)
    assert len(result.valid) == 3


def test_robinhood_profile_parses_fixture(tmp_db):
    cdb.migrate(tmp_db)
    row = tmp_db.execute(
        "SELECT * FROM import_profiles WHERE name='Robinhood'"
    ).fetchone()
    profile = _profile_from_row(row)
    csv_text = (FIXTURES / "robinhood.csv").read_text()
    result = importer.apply_profile(csv_text, profile)
    assert len(result.valid) == 3


def test_moomoo_profile_parses_fixture(tmp_db):
    cdb.migrate(tmp_db)
    row = tmp_db.execute(
        "SELECT * FROM import_profiles WHERE name='MooMoo'"
    ).fetchone()
    profile = _profile_from_row(row)
    csv_text = (FIXTURES / "moomoo.csv").read_text()
    result = importer.apply_profile(csv_text, profile)
    assert len(result.valid) == 3


def test_vanguard_profile_parses_fixture(tmp_db):
    cdb.migrate(tmp_db)
    row = tmp_db.execute(
        "SELECT * FROM import_profiles WHERE name='Vanguard'"
    ).fetchone()
    profile = _profile_from_row(row)
    csv_text = (FIXTURES / "vanguard.csv").read_text()
    result = importer.apply_profile(csv_text, profile)
    assert len(result.valid) == 3


def test_user_profile_has_builtin_zero(tmp_db):
    cdb.migrate(tmp_db)
    pid = profiles.create_user_profile(
        tmp_db, name="My Custom", broker="other",
        column_map={"ticker": "Sym", "side": "Dir", "qty": "Q",
                     "price": "P", "trade_date": "When"},
        value_map={"side": {"B": "BUY", "S": "SELL"}},
        row_filter={},
    )
    row = tmp_db.execute(
        "SELECT builtin FROM import_profiles WHERE id=?", (pid,)
    ).fetchone()
    assert row["builtin"] == 0
