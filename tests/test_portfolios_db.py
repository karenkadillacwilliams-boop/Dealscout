import pytest
import sqlite3

from catalysts import db as cdb


def test_migrate_creates_portfolio_tables(tmp_db):
    cdb.migrate(tmp_db)
    names = {r[0] for r in tmp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"accounts", "trades", "events",
            "import_profiles", "import_batches"} <= names


def test_create_and_load_account(tmp_db):
    cdb.migrate(tmp_db)
    acc_id = cdb.create_account(
        tmp_db, name="Main Roth", type="roth", broker="fidelity",
        opened_date="2024-01-15", initial_cash=5000.0,
    )
    assert acc_id > 0
    rows = cdb.load_accounts(tmp_db)
    assert len(rows) == 1
    assert rows[0]["name"] == "Main Roth"
    assert rows[0]["initial_cash"] == 5000.0
    assert rows[0]["active"] == 1


def test_load_accounts_excludes_inactive_by_default(tmp_db):
    cdb.migrate(tmp_db)
    a = cdb.create_account(tmp_db, name="Old", type="taxable",
                            broker="robinhood", opened_date="2020-01-01")
    cdb.deactivate_account(tmp_db, a)
    assert cdb.load_accounts(tmp_db) == []
    assert len(cdb.load_accounts(tmp_db, active_only=False)) == 1


def test_account_name_must_be_unique(tmp_db):
    cdb.migrate(tmp_db)
    cdb.create_account(tmp_db, name="Main", type="taxable",
                        broker="fidelity", opened_date="2024-01-01")
    with pytest.raises(sqlite3.IntegrityError):
        cdb.create_account(tmp_db, name="Main", type="roth",
                            broker="schwab", opened_date="2024-02-01")


def test_account_event_thresholds_are_optional(tmp_db):
    cdb.migrate(tmp_db)
    acc_id = cdb.create_account(
        tmp_db, name="Custom", type="taxable", broker="other",
        opened_date="2024-01-01", event_daily_pct=3.0, event_5day_pct=7.5,
    )
    row = cdb.load_accounts(tmp_db)[0]
    assert row["event_daily_pct"] == 3.0
    assert row["event_5day_pct"] == 7.5


def test_update_account_changes_fields(tmp_db):
    cdb.migrate(tmp_db)
    acc_id = cdb.create_account(
        tmp_db, name="Original", type="taxable", broker="fidelity",
        opened_date="2024-01-01", initial_cash=1000.0,
    )
    cdb.update_account(
        tmp_db, acc_id, name="Renamed", initial_cash=2500.0, event_daily_pct=4.0,
    )
    row = cdb.load_account(tmp_db, acc_id)
    assert row["name"] == "Renamed"
    assert row["initial_cash"] == 2500.0
    assert row["event_daily_pct"] == 4.0
    # Untouched fields preserved
    assert row["type"] == "taxable"
    assert row["broker"] == "fidelity"
    assert row["opened_date"] == "2024-01-01"


def test_update_account_unknown_id_raises(tmp_db):
    cdb.migrate(tmp_db)
    with pytest.raises(ValueError, match="account 9999 not found"):
        cdb.update_account(tmp_db, 9999, name="ghost")
