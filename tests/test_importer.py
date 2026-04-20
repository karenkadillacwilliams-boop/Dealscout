from pathlib import Path
import pytest

from portfolios import importer


FIXTURES = Path(__file__).parent / "fixtures" / "broker_csvs"


# --- Normalization primitives ---

def test_normalize_ticker():
    assert importer.normalize_ticker(" nvda ") == "NVDA"


def test_normalize_ticker_rejects_empty():
    with pytest.raises(ValueError):
        importer.normalize_ticker("")


def test_normalize_price_strips_currency():
    assert importer.normalize_price("$500.00") == 500.0
    assert importer.normalize_price("$1,234.56") == 1234.56
    assert importer.normalize_price("($500.00)") == 500.0  # Robinhood neg-in-parens


def test_normalize_price_rejects_negative_result_when_not_paren():
    with pytest.raises(ValueError):
        importer.normalize_price("-50")


def test_normalize_qty_positive():
    assert importer.normalize_qty("10") == 10.0
    assert importer.normalize_qty("10.5") == 10.5


def test_normalize_qty_abs_value():
    assert importer.normalize_qty("-10") == 10.0  # some brokers sign by side


def test_normalize_qty_rejects_zero():
    with pytest.raises(ValueError):
        importer.normalize_qty("0")


def test_parse_date_iso():
    assert importer.parse_date("2026-04-10") == "2026-04-10"


def test_parse_date_us():
    assert importer.parse_date("04/10/2026") == "2026-04-10"


def test_parse_date_rejects_future():
    from datetime import date, timedelta
    future = (date.today() + timedelta(days=5)).strftime("%Y-%m-%d")
    with pytest.raises(ValueError):
        importer.parse_date(future)


# --- Profile application ---

def test_apply_profile_fidelity():
    profile = {
        "column_map": {
            "ticker": "Symbol", "side": "Action", "qty": "Quantity",
            "price": "Price", "trade_date": "Run Date",
        },
        "value_map": {
            "side": {"YOU BOUGHT": "BUY", "YOU SOLD": "SELL"},
        },
        "row_filter": {
            "skip_if_side_not_in_value_map": True,
        },
    }
    csv_text = (FIXTURES / "fidelity.csv").read_text()
    result = importer.apply_profile(csv_text, profile)
    assert len(result.valid) == 3  # dividend row filtered out
    assert result.valid[0].ticker == "NVDA"
    assert result.valid[0].side == "BUY"
    assert result.valid[0].qty == 10.0
    assert result.valid[0].price == 500.0
    assert result.valid[0].trade_date == "2026-04-10"
    assert result.valid[2].side == "SELL"


def test_apply_profile_schwab():
    profile = {
        "column_map": {
            "ticker": "Symbol", "side": "Action", "qty": "Quantity",
            "price": "Price", "trade_date": "Date",
        },
        "value_map": {"side": {"Buy": "BUY", "Sell": "SELL"}},
        "row_filter": {},
    }
    csv_text = (FIXTURES / "schwab.csv").read_text()
    result = importer.apply_profile(csv_text, profile)
    assert len(result.valid) == 3
    assert result.valid[0].price == 500.0  # stripped $


def test_apply_profile_robinhood():
    profile = {
        "column_map": {
            "ticker": "Instrument", "side": "Trans Code",
            "qty": "Quantity", "price": "Price",
            "trade_date": "Activity Date",
        },
        "value_map": {"side": {"Buy": "BUY", "Sell": "SELL"}},
        "row_filter": {},
    }
    csv_text = (FIXTURES / "robinhood.csv").read_text()
    result = importer.apply_profile(csv_text, profile)
    assert len(result.valid) == 3


def test_apply_profile_rejects_missing_column():
    profile = {
        "column_map": {
            "ticker": "DoesNotExist", "side": "Action", "qty": "Quantity",
            "price": "Price", "trade_date": "Date",
        },
        "value_map": {"side": {"Buy": "BUY", "Sell": "SELL"}},
        "row_filter": {},
    }
    csv_text = (FIXTURES / "schwab.csv").read_text()
    with pytest.raises(ValueError, match="DoesNotExist"):
        importer.apply_profile(csv_text, profile)


def test_apply_profile_rejects_untranslated_side():
    profile = {
        "column_map": {
            "ticker": "Symbol", "side": "Action", "qty": "Quantity",
            "price": "Price", "trade_date": "Run Date",
        },
        "value_map": {"side": {"YOU BOUGHT": "BUY"}},  # missing YOU SOLD
        "row_filter": {},  # no row filter → untranslated row rejected
    }
    csv_text = (FIXTURES / "fidelity.csv").read_text()
    result = importer.apply_profile(csv_text, profile)
    # 2 BUYs valid, 1 SELL rejected (dividend also rejected)
    assert len(result.valid) == 2
    assert len(result.rejected) == 2  # SELL + DIVIDEND
