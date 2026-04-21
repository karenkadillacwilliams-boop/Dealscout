from unittest.mock import patch, MagicMock

from catalysts import db as cdb, polygon_client as pc
from portfolios import events


def _make_bars(closes: list[float], start_date="2026-04-07"):
    """Daily bars with monotonic dates; 't' is epoch-ms at 21:00 UTC."""
    from datetime import date, datetime, timedelta, timezone
    d0 = date.fromisoformat(start_date)
    bars = []
    for i, c in enumerate(closes):
        d = d0 + timedelta(days=i)
        ts = int(datetime(d.year, d.month, d.day, 21, 0, 0,
                            tzinfo=timezone.utc).timestamp() * 1000)
        bars.append({"t": ts, "o": c, "h": c, "l": c, "c": c, "v": 1_000_000})
    return bars


def _mock_bars_response(bars):
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {}
    resp.json.return_value = {"results": bars}
    resp.raise_for_status = MagicMock()
    return resp


def _seed_account_with_position(tmp_db, ticker="NVDA", qty=10.0, price=500.0,
                                  trade_date="2026-04-01"):
    acc = cdb.create_account(tmp_db, name="A", type="taxable",
                               broker="fidelity", opened_date="2024-01-01")
    cdb.insert_trade(tmp_db, account_id=acc, ticker=ticker, side="BUY",
                      qty=qty, price=price, trade_date=trade_date)
    return acc


def test_daily_move_over_threshold_fires(tmp_db, monkeypatch):
    cdb.migrate(tmp_db)
    pc.reset_bucket_for_tests()
    acc = _seed_account_with_position(tmp_db)
    # 6% daily bar-to-bar move on the latest bar
    bars = _make_bars([100, 100, 100, 100, 100, 100, 106])
    monkeypatch.setattr(events, "_today_et", lambda: bars[-1]["t"] // 86400_000 + 1)
    with patch.object(pc.requests, "get", return_value=_mock_bars_response(bars)):
        n = events.detect_events_for_all_accounts(tmp_db)
    assert n >= 1
    rows = cdb.load_events(tmp_db, account_id=acc)
    daily = [r for r in rows if r["move_window"] == "1d"]
    assert len(daily) == 1
    assert abs(daily[0]["move_pct"] - 6.0) < 0.01


def test_daily_move_under_threshold_does_not_fire(tmp_db, monkeypatch):
    cdb.migrate(tmp_db)
    pc.reset_bucket_for_tests()
    acc = _seed_account_with_position(tmp_db)
    bars = _make_bars([100, 100, 100, 100, 100, 100, 104])  # 4% daily
    monkeypatch.setattr(events, "_today_et", lambda: bars[-1]["t"] // 86400_000 + 1)
    with patch.object(pc.requests, "get", return_value=_mock_bars_response(bars)):
        events.detect_events_for_all_accounts(tmp_db)
    rows = cdb.load_events(tmp_db, account_id=acc)
    assert [r for r in rows if r["move_window"] == "1d"] == []


def test_5day_move_over_threshold_fires(tmp_db, monkeypatch):
    cdb.migrate(tmp_db)
    pc.reset_bucket_for_tests()
    acc = _seed_account_with_position(tmp_db)
    # 12% 5-day move, 2% daily (under daily threshold)
    bars = _make_bars([100, 99, 100, 101, 102, 100, 112])
    monkeypatch.setattr(events, "_today_et", lambda: bars[-1]["t"] // 86400_000 + 1)
    with patch.object(pc.requests, "get", return_value=_mock_bars_response(bars)):
        events.detect_events_for_all_accounts(tmp_db)
    rows = cdb.load_events(tmp_db, account_id=acc)
    five_day = [r for r in rows if r["move_window"] == "5d"]
    assert len(five_day) == 1


def test_detection_is_idempotent(tmp_db, monkeypatch):
    cdb.migrate(tmp_db)
    pc.reset_bucket_for_tests()
    acc = _seed_account_with_position(tmp_db)
    bars = _make_bars([100, 100, 100, 100, 100, 100, 108])
    monkeypatch.setattr(events, "_today_et", lambda: bars[-1]["t"] // 86400_000 + 1)
    with patch.object(pc.requests, "get", return_value=_mock_bars_response(bars)):
        events.detect_events_for_all_accounts(tmp_db)
        events.detect_events_for_all_accounts(tmp_db)
    rows = cdb.load_events(tmp_db, account_id=acc)
    assert len(rows) == 1  # unchanged on re-scan


def test_per_account_threshold_override(tmp_db, monkeypatch):
    cdb.migrate(tmp_db)
    pc.reset_bucket_for_tests()
    a = cdb.create_account(tmp_db, name="A", type="taxable", broker="x",
                             opened_date="2024-01-01", event_daily_pct=3.0)
    b = cdb.create_account(tmp_db, name="B", type="taxable", broker="x",
                             opened_date="2024-01-01")  # uses default 5.0
    cdb.insert_trade(tmp_db, account_id=a, ticker="NVDA", side="BUY",
                      qty=1, price=100, trade_date="2026-04-01")
    cdb.insert_trade(tmp_db, account_id=b, ticker="NVDA", side="BUY",
                      qty=1, price=100, trade_date="2026-04-01")
    bars = _make_bars([100, 100, 100, 100, 100, 100, 104])  # 4% daily
    monkeypatch.setattr(events, "_today_et", lambda: bars[-1]["t"] // 86400_000 + 1)
    with patch.object(pc.requests, "get", return_value=_mock_bars_response(bars)):
        events.detect_events_for_all_accounts(tmp_db)
    rows_a = cdb.load_events(tmp_db, account_id=a)
    rows_b = cdb.load_events(tmp_db, account_id=b)
    assert len(rows_a) == 1  # 4% >= 3% override
    assert len(rows_b) == 0  # 4% < 5% default


def test_auto_link_to_nearby_catalyst(tmp_db, monkeypatch):
    cdb.migrate(tmp_db)
    pc.reset_bucket_for_tests()
    acc = _seed_account_with_position(tmp_db)
    # seed a catalyst on NVDA dated +2 days from event
    tmp_db.execute(
        "INSERT INTO catalysts(ticker,source,source_id,headline,url,"
        "published_at,kw_score,final_score,seen,fetched_at) "
        "VALUES(?,?,?,?,?,?,?,?,0,?)",
        ("NVDA", "edgar", "edgar:NVDA:accX", "NVDA guides up",
         "https://x", "2026-04-11T10:00:00+00:00", 60, 85,
         "2026-04-11T10:00:00+00:00"),
    )
    tmp_db.commit()
    bars = _make_bars([100, 100, 100, 100, 100, 100, 108])  # event on bar[6]
    # bars[-1] date is 2026-04-13; catalyst at 2026-04-11 is within ±3 days
    monkeypatch.setattr(events, "_today_et", lambda: bars[-1]["t"] // 86400_000 + 1)
    with patch.object(pc.requests, "get", return_value=_mock_bars_response(bars)):
        events.detect_events_for_all_accounts(tmp_db)
    rows = cdb.load_events(tmp_db, account_id=acc)
    assert rows[0]["catalyst_id"] is not None


def test_auto_link_does_not_cross_tickers(tmp_db, monkeypatch):
    cdb.migrate(tmp_db)
    pc.reset_bucket_for_tests()
    acc = _seed_account_with_position(tmp_db, ticker="NVDA")
    # catalyst is on AAPL not NVDA
    tmp_db.execute(
        "INSERT INTO catalysts(ticker,source,source_id,headline,url,"
        "published_at,kw_score,final_score,seen,fetched_at) "
        "VALUES('AAPL','edgar','x','x','x','2026-04-12T10:00:00+00:00',60,85,0,'2026-04-12T10:00:00+00:00')"
    )
    tmp_db.commit()
    bars = _make_bars([100, 100, 100, 100, 100, 100, 108])
    monkeypatch.setattr(events, "_today_et", lambda: bars[-1]["t"] // 86400_000 + 1)
    with patch.object(pc.requests, "get", return_value=_mock_bars_response(bars)):
        events.detect_events_for_all_accounts(tmp_db)
    rows = cdb.load_events(tmp_db, account_id=acc)
    assert rows[0]["catalyst_id"] is None


def test_auto_link_window_respects_3_day_limit(tmp_db, monkeypatch):
    cdb.migrate(tmp_db)
    pc.reset_bucket_for_tests()
    acc = _seed_account_with_position(tmp_db)
    # catalyst 5 days before event - outside window
    tmp_db.execute(
        "INSERT INTO catalysts(ticker,source,source_id,headline,url,"
        "published_at,kw_score,final_score,seen,fetched_at) "
        "VALUES('NVDA','edgar','x','x','x','2026-04-08T10:00:00+00:00',60,85,0,'x')"
    )
    tmp_db.commit()
    bars = _make_bars([100, 100, 100, 100, 100, 100, 108])  # event on 2026-04-13
    monkeypatch.setattr(events, "_today_et", lambda: bars[-1]["t"] // 86400_000 + 1)
    with patch.object(pc.requests, "get", return_value=_mock_bars_response(bars)):
        events.detect_events_for_all_accounts(tmp_db)
    rows = cdb.load_events(tmp_db, account_id=acc)
    assert rows[0]["catalyst_id"] is None


def test_dismissed_status_survives_rescan(tmp_db, monkeypatch):
    cdb.migrate(tmp_db)
    pc.reset_bucket_for_tests()
    acc = _seed_account_with_position(tmp_db)
    bars = _make_bars([100, 100, 100, 100, 100, 100, 108])
    monkeypatch.setattr(events, "_today_et", lambda: bars[-1]["t"] // 86400_000 + 1)
    with patch.object(pc.requests, "get", return_value=_mock_bars_response(bars)):
        events.detect_events_for_all_accounts(tmp_db)
    rows = cdb.load_events(tmp_db, account_id=acc)
    cdb.update_event(tmp_db, rows[0]["id"], status="dismissed")
    with patch.object(pc.requests, "get", return_value=_mock_bars_response(bars)):
        events.detect_events_for_all_accounts(tmp_db)
    rows = cdb.load_events(tmp_db, account_id=acc)
    assert len(rows) == 1
    assert rows[0]["status"] == "dismissed"  # not recreated as pending
