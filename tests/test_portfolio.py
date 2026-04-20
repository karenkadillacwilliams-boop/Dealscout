from catalysts import db as cdb
import portfolio


def _seed_account(conn, name="A"):
    return cdb.create_account(conn, name=name, type="taxable",
                                broker="fidelity", opened_date="2024-01-01")


def test_positions_empty_when_no_trades(tmp_db):
    cdb.migrate(tmp_db)
    acc = _seed_account(tmp_db)
    df = portfolio.positions_for_account(tmp_db, acc, last_prices={})
    assert df.empty


def test_positions_single_buy(tmp_db):
    cdb.migrate(tmp_db)
    acc = _seed_account(tmp_db)
    cdb.insert_trade(tmp_db, account_id=acc, ticker="NVDA", side="BUY",
                      qty=10.0, price=500.0, trade_date="2026-04-10")
    df = portfolio.positions_for_account(tmp_db, acc, last_prices={"NVDA": 520.0})
    assert len(df) == 1
    row = df.iloc[0]
    assert row["ticker"] == "NVDA"
    assert row["qty"] == 10.0
    assert row["avg_cost"] == 500.0
    assert row["market_value"] == 5200.0
    assert row["unrealized_pl"] == 200.0


def test_positions_buy_then_sell_realizes_pl(tmp_db):
    cdb.migrate(tmp_db)
    acc = _seed_account(tmp_db)
    cdb.insert_trade(tmp_db, account_id=acc, ticker="NVDA", side="BUY",
                      qty=10.0, price=500.0, trade_date="2026-04-10")
    cdb.insert_trade(tmp_db, account_id=acc, ticker="NVDA", side="SELL",
                      qty=4.0, price=600.0, trade_date="2026-04-12")
    df = portfolio.positions_for_account(tmp_db, acc, last_prices={"NVDA": 520.0})
    row = df.iloc[0]
    assert row["qty"] == 6.0
    assert row["avg_cost"] == 500.0
    assert row["realized_pl"] == 400.0  # (600-500) * 4


def test_positions_scoped_per_account(tmp_db):
    cdb.migrate(tmp_db)
    a = _seed_account(tmp_db, "A")
    b = _seed_account(tmp_db, "B")
    cdb.insert_trade(tmp_db, account_id=a, ticker="NVDA", side="BUY",
                      qty=10.0, price=500.0, trade_date="2026-04-10")
    cdb.insert_trade(tmp_db, account_id=b, ticker="AAPL", side="BUY",
                      qty=5.0, price=200.0, trade_date="2026-04-10")
    df_a = portfolio.positions_for_account(tmp_db, a, last_prices={})
    df_b = portfolio.positions_for_account(tmp_db, b, last_prices={})
    assert list(df_a["ticker"]) == ["NVDA"]
    assert list(df_b["ticker"]) == ["AAPL"]


def test_positions_all_accounts(tmp_db):
    cdb.migrate(tmp_db)
    a = _seed_account(tmp_db, "A")
    b = _seed_account(tmp_db, "B")
    cdb.insert_trade(tmp_db, account_id=a, ticker="NVDA", side="BUY",
                      qty=10.0, price=500.0, trade_date="2026-04-10")
    cdb.insert_trade(tmp_db, account_id=b, ticker="NVDA", side="BUY",
                      qty=5.0, price=450.0, trade_date="2026-04-11")
    df = portfolio.positions_all_accounts(tmp_db,
                                           last_prices={"NVDA": 500.0})
    # Same ticker across accounts stays as two rows so we can see per-account
    assert len(df) == 2
    assert set(df["account_name"]) == {"A", "B"}
