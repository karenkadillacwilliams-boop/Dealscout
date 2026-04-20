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


def _make_acc(conn, name="Main"):
    return cdb.create_account(conn, name=name, type="taxable",
                                broker="fidelity", opened_date="2024-01-01")


def test_insert_trade_and_compute_dedup_key(tmp_db):
    cdb.migrate(tmp_db)
    acc = _make_acc(tmp_db)
    tid, was_new = cdb.insert_trade(
        tmp_db, account_id=acc, ticker="NVDA", side="BUY",
        qty=10.0, price=500.0, trade_date="2026-04-10",
    )
    assert tid > 0
    assert was_new is True


def test_insert_duplicate_trade_is_noop(tmp_db):
    cdb.migrate(tmp_db)
    acc = _make_acc(tmp_db)
    tid1, _ = cdb.insert_trade(
        tmp_db, account_id=acc, ticker="NVDA", side="BUY",
        qty=10.0, price=500.0, trade_date="2026-04-10",
    )
    tid2, was_new = cdb.insert_trade(
        tmp_db, account_id=acc, ticker="NVDA", side="BUY",
        qty=10.0, price=500.0, trade_date="2026-04-10",
    )
    assert was_new is False
    assert tid2 == tid1
    count = tmp_db.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    assert count == 1


def test_dedup_key_differs_per_account(tmp_db):
    """Same trade in two accounts = two rows."""
    cdb.migrate(tmp_db)
    a = _make_acc(tmp_db, "A")
    b = _make_acc(tmp_db, "B")
    cdb.insert_trade(tmp_db, account_id=a, ticker="NVDA", side="BUY",
                      qty=10.0, price=500.0, trade_date="2026-04-10")
    _, was_new = cdb.insert_trade(tmp_db, account_id=b, ticker="NVDA",
                                   side="BUY", qty=10.0, price=500.0,
                                   trade_date="2026-04-10")
    assert was_new is True


def test_load_trades_for_account(tmp_db):
    cdb.migrate(tmp_db)
    acc = _make_acc(tmp_db)
    cdb.insert_trade(tmp_db, account_id=acc, ticker="NVDA", side="BUY",
                      qty=10.0, price=500.0, trade_date="2026-04-10")
    cdb.insert_trade(tmp_db, account_id=acc, ticker="AAPL", side="BUY",
                      qty=5.0, price=200.0, trade_date="2026-04-11")
    rows = cdb.load_trades(tmp_db, account_id=acc)
    assert len(rows) == 2
    # Newest first
    assert rows[0]["ticker"] == "AAPL"


def test_insert_event_idempotent(tmp_db):
    cdb.migrate(tmp_db)
    acc = _make_acc(tmp_db)
    eid1, was_new1 = cdb.insert_event(
        tmp_db, account_id=acc, ticker="NVDA", event_date="2026-04-15",
        move_pct=8.2, move_window="1d", position_qty=10.0,
        value_before=5000.0, value_after=5410.0, pnl_dollars=410.0,
    )
    eid2, was_new2 = cdb.insert_event(
        tmp_db, account_id=acc, ticker="NVDA", event_date="2026-04-15",
        move_pct=8.2, move_window="1d", position_qty=10.0,
        value_before=5000.0, value_after=5410.0, pnl_dollars=410.0,
    )
    assert was_new1 is True
    assert was_new2 is False
    assert eid1 == eid2


def test_1d_and_5d_events_on_same_day_coexist(tmp_db):
    cdb.migrate(tmp_db)
    acc = _make_acc(tmp_db)
    cdb.insert_event(tmp_db, account_id=acc, ticker="NVDA",
                      event_date="2026-04-15", move_pct=6.0, move_window="1d",
                      position_qty=10.0, value_before=5000.0,
                      value_after=5300.0, pnl_dollars=300.0)
    _, was_new = cdb.insert_event(tmp_db, account_id=acc, ticker="NVDA",
                                   event_date="2026-04-15", move_pct=12.0,
                                   move_window="5d", position_qty=10.0,
                                   value_before=4700.0, value_after=5300.0,
                                   pnl_dollars=600.0)
    assert was_new is True
    rows = cdb.load_events(tmp_db, account_id=acc)
    assert len(rows) == 2
    windows = {r["move_window"] for r in rows}
    assert windows == {"1d", "5d"}


def test_update_event_status_and_confirmed_at(tmp_db):
    from datetime import datetime, timezone, timedelta
    cdb.migrate(tmp_db)
    acc = _make_acc(tmp_db)
    eid, _ = cdb.insert_event(
        tmp_db, account_id=acc, ticker="NVDA", event_date="2026-04-15",
        move_pct=8.2, move_window="1d", position_qty=10.0,
        value_before=5000.0, value_after=5410.0, pnl_dollars=410.0,
    )
    cdb.update_event(tmp_db, eid,
                      status="confirmed", catalyst_type="earnings",
                      notes="FY26 guide")
    row = cdb.load_event(tmp_db, eid)
    assert row["status"] == "confirmed"
    assert row["catalyst_type"] == "earnings"
    assert row["confirmed_at"] is not None
    ts = datetime.fromisoformat(row["confirmed_at"])
    assert datetime.now(timezone.utc) - ts < timedelta(seconds=5)


def test_load_pending_events_excludes_dismissed(tmp_db):
    cdb.migrate(tmp_db)
    acc = _make_acc(tmp_db)
    e1, _ = cdb.insert_event(tmp_db, account_id=acc, ticker="A",
                              event_date="2026-04-15", move_pct=6.0,
                              move_window="1d", position_qty=1.0,
                              value_before=100.0, value_after=106.0,
                              pnl_dollars=6.0)
    e2, _ = cdb.insert_event(tmp_db, account_id=acc, ticker="B",
                              event_date="2026-04-15", move_pct=7.0,
                              move_window="1d", position_qty=1.0,
                              value_before=100.0, value_after=107.0,
                              pnl_dollars=7.0)
    cdb.update_event(tmp_db, e2, status="dismissed")
    pending = cdb.load_events(tmp_db, status="pending")
    assert len(pending) == 1
    assert pending[0]["id"] == e1


def test_count_pending_events(tmp_db):
    cdb.migrate(tmp_db)
    acc = _make_acc(tmp_db)
    cdb.insert_event(tmp_db, account_id=acc, ticker="A",
                      event_date="2026-04-15", move_pct=6.0, move_window="1d",
                      position_qty=1.0, value_before=100.0, value_after=106.0,
                      pnl_dollars=6.0)
    cdb.insert_event(tmp_db, account_id=acc, ticker="B",
                      event_date="2026-04-15", move_pct=7.0, move_window="1d",
                      position_qty=1.0, value_before=100.0, value_after=107.0,
                      pnl_dollars=7.0)
    assert cdb.pending_event_count(tmp_db) == 2


def test_create_import_batch(tmp_db):
    cdb.migrate(tmp_db)
    acc = _make_acc(tmp_db)
    bid = cdb.create_import_batch(
        tmp_db, account_id=acc, profile_id=None, filename="t.csv",
        row_count=5, inserted=3, duplicates=2, rejected=0,
    )
    assert bid > 0
    rows = cdb.load_import_batches(tmp_db, account_id=acc)
    assert rows[0]["inserted"] == 3
    assert rows[0]["duplicates"] == 2
