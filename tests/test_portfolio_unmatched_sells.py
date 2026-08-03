"""A sell with no open lot has no cost basis, so it cannot produce realized P/L.

Excluding it is correct; doing so silently is not. The usual trigger is a
broker CSV whose window starts after the original purchase, which makes
reported realized gains read lower than reality with nothing on screen to say
why. These tests pin the reporting side of that behaviour.
"""
from __future__ import annotations

import portfolio
from catalysts import db as cdb

ATTR = portfolio.UNMATCHED_SELLS_ATTR


def _seed_account(conn, name="A"):
    return cdb.create_account(conn, name=name, type="taxable",
                              broker="fidelity", opened_date="2024-01-01")


def test_clean_book_reports_no_unmatched_sells(tmp_db):
    cdb.migrate(tmp_db)
    acc = _seed_account(tmp_db)
    cdb.insert_trade(tmp_db, account_id=acc, ticker="NVDA", side="BUY",
                     qty=10.0, price=500.0, trade_date="2026-04-10")
    cdb.insert_trade(tmp_db, account_id=acc, ticker="NVDA", side="SELL",
                     qty=4.0, price=520.0, trade_date="2026-04-11")

    df = portfolio.positions_for_account(tmp_db, acc, last_prices={"NVDA": 520.0})
    assert df.attrs[ATTR] == {}
    # 4 shares sold at +20 over a 500 basis.
    assert df.iloc[0]["realized_pl"] == 80.0


def test_sell_without_matching_buy_is_recorded(tmp_db):
    cdb.migrate(tmp_db)
    acc = _seed_account(tmp_db)
    # No BUY for AAPL — the opening trade predates the import window.
    cdb.insert_trade(tmp_db, account_id=acc, ticker="AAPL", side="SELL",
                     qty=25.0, price=180.0, trade_date="2026-04-11")

    df = portfolio.positions_for_account(tmp_db, acc, last_prices={"AAPL": 190.0})
    assert df.attrs[ATTR] == {"AAPL": 25.0}


def test_oversized_sell_records_only_the_uncovered_remainder(tmp_db):
    """Selling 10 against an open lot of 6 closes 6 and flags nothing.

    The existing code caps the closed quantity at the open lot rather than
    treating the whole trade as unmatched, so the position still zeroes out.
    """
    cdb.migrate(tmp_db)
    acc = _seed_account(tmp_db)
    cdb.insert_trade(tmp_db, account_id=acc, ticker="MSFT", side="BUY",
                     qty=6.0, price=400.0, trade_date="2026-04-10")
    cdb.insert_trade(tmp_db, account_id=acc, ticker="MSFT", side="SELL",
                     qty=10.0, price=420.0, trade_date="2026-04-11")

    df = portfolio.positions_for_account(tmp_db, acc, last_prices={"MSFT": 420.0})
    # The lot was open when the sell arrived, so it is not flagged as unmatched.
    assert df.attrs[ATTR] == {}
    assert df.iloc[0]["realized_pl"] == 120.0  # 6 shares x +20


def test_second_sell_after_position_closed_is_flagged(tmp_db):
    """Once the lot is exhausted a further sell has nothing left to close."""
    cdb.migrate(tmp_db)
    acc = _seed_account(tmp_db)
    cdb.insert_trade(tmp_db, account_id=acc, ticker="TSLA", side="BUY",
                     qty=5.0, price=200.0, trade_date="2026-04-10")
    cdb.insert_trade(tmp_db, account_id=acc, ticker="TSLA", side="SELL",
                     qty=5.0, price=210.0, trade_date="2026-04-11")
    cdb.insert_trade(tmp_db, account_id=acc, ticker="TSLA", side="SELL",
                     qty=3.0, price=215.0, trade_date="2026-04-12")

    df = portfolio.positions_for_account(tmp_db, acc, last_prices={"TSLA": 215.0})
    assert df.attrs[ATTR] == {"TSLA": 3.0}
    assert df.iloc[0]["realized_pl"] == 50.0  # only the covered 5 shares


def test_empty_result_still_carries_the_attr(tmp_db):
    """A book of nothing but unmatched sells produces no rows but must warn."""
    cdb.migrate(tmp_db)
    acc = _seed_account(tmp_db)
    cdb.insert_trade(tmp_db, account_id=acc, ticker="AMD", side="SELL",
                     qty=8.0, price=150.0, trade_date="2026-04-11")

    df = portfolio.positions_for_account(tmp_db, acc, last_prices={})
    assert df.empty
    assert df.attrs[ATTR] == {"AMD": 8.0}


def test_all_accounts_sums_unmatched_across_accounts(tmp_db):
    cdb.migrate(tmp_db)
    a = _seed_account(tmp_db, "A")
    b = _seed_account(tmp_db, "B")
    cdb.insert_trade(tmp_db, account_id=a, ticker="AAPL", side="SELL",
                     qty=5.0, price=180.0, trade_date="2026-04-11")
    cdb.insert_trade(tmp_db, account_id=b, ticker="AAPL", side="SELL",
                     qty=7.0, price=182.0, trade_date="2026-04-11")
    cdb.insert_trade(tmp_db, account_id=b, ticker="NVDA", side="BUY",
                     qty=2.0, price=500.0, trade_date="2026-04-10")

    df = portfolio.positions_all_accounts(tmp_db, last_prices={"NVDA": 500.0})
    # pd.concat drops .attrs, so this only holds because it is rebuilt.
    assert df.attrs[ATTR] == {"AAPL": 12.0}
