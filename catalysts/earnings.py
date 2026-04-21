"""Earnings surprise + analyst momentum for the triple-play factor.

Triple play = (1) EPS beat, (2) revenue beat, (3) raised guidance.

Data sources:
  * Finnhub /stock/earnings — actual vs estimate EPS per quarter (free tier)
  * Finnhub /stock/recommendation — monthly analyst buy/hold/sell counts.
    Used as a proxy for "guidance raised": if analyst bullishness
    (strongBuy + buy as a share of total) rose in the month AFTER the latest
    earnings vs. the month BEFORE, we infer the company's guidance/commentary
    was received positively.
  * yfinance Ticker.earnings_history — revenue actual/estimate for the
    most recent quarter. Messy and often empty, but free; when unavailable
    we surface revenue_surprise_pct=None and the factor scores only on the
    two components we do have.

No parquet caching — the triple_play DB table is the cache.
All failures collapse to None.
Must NOT require FINNHUB_API_KEY to import.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
from dataclasses import dataclass
from typing import Any

import requests

log = logging.getLogger(__name__)

_EARNINGS_URL = "https://finnhub.io/api/v1/stock/earnings"
_RECOMMENDATION_URL = "https://finnhub.io/api/v1/stock/recommendation"
_HTTP_TIMEOUT = 3.0


@dataclass
class EarningsData:
    ticker: str
    report_period: str            # "YYYY-MM-DD" of quarter end
    days_since_report: int        # calendar days since the quarter-end
    eps_surprise_pct: float | None
    revenue_surprise_pct: float | None
    bullish_share_before: float | None  # (strongBuy+buy)/total, month before earnings
    bullish_share_after: float | None   # same, month after earnings
    bullish_share_delta: float | None   # after - before, percentage points


def _api_key() -> str | None:
    return os.environ.get("FINNHUB_API_KEY")


def _fetch_latest_eps_surprise(ticker: str, key: str) -> tuple[str, float] | None:
    """Return (report_period, surprise_pct) for most recent quarter, or None."""
    try:
        r = requests.get(
            _EARNINGS_URL,
            params={"symbol": ticker, "token": key},
            timeout=_HTTP_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, TimeoutError, ValueError) as exc:
        log.debug("eps surprise fetch failed for %s: %s", ticker, type(exc).__name__)
        return None
    if not isinstance(data, list) or not data:
        return None
    # Finnhub returns newest first.
    latest = data[0]
    period = latest.get("period")
    surprise_pct = latest.get("surprisePercent")
    if period is None or surprise_pct is None:
        return None
    return period, float(surprise_pct)


def _fetch_analyst_momentum(
    ticker: str, key: str, report_period: str,
) -> tuple[float | None, float | None]:
    """Compare analyst bullishness in the month BEFORE vs. AFTER earnings.

    Returns (before_share, after_share). Each is (strongBuy+buy) / total rating
    count for that month. None when data is missing.
    """
    try:
        r = requests.get(
            _RECOMMENDATION_URL,
            params={"symbol": ticker, "token": key},
            timeout=_HTTP_TIMEOUT,
        )
        r.raise_for_status()
        rows = r.json()
    except (requests.RequestException, TimeoutError, ValueError) as exc:
        log.debug("recommendation fetch failed for %s: %s", ticker, type(exc).__name__)
        return None, None
    if not isinstance(rows, list) or not rows:
        return None, None

    try:
        report_dt = dt.date.fromisoformat(report_period)
    except ValueError:
        return None, None

    # Finnhub returns month-bucketed snapshots keyed by `period` (start of month).
    # Find the bucket AFTER the report date and the one BEFORE.
    by_date: dict[dt.date, dict] = {}
    for row in rows:
        p = row.get("period")
        if not p:
            continue
        try:
            by_date[dt.date.fromisoformat(p)] = row
        except ValueError:
            continue

    def _share(row: dict | None) -> float | None:
        if not row:
            return None
        total = sum(float(row.get(k) or 0) for k in ("strongBuy", "buy", "hold", "sell", "strongSell"))
        if total <= 0:
            return None
        return (float(row.get("strongBuy") or 0) + float(row.get("buy") or 0)) / total

    sorted_dates = sorted(by_date.keys())
    before_row = next((by_date[d] for d in reversed(sorted_dates) if d < report_dt), None)
    after_row = next((by_date[d] for d in sorted_dates if d > report_dt), None)
    return _share(before_row), _share(after_row)


def _fetch_revenue_surprise_yfinance(ticker: str) -> float | None:
    """Best-effort revenue surprise via yfinance. Often returns None because
    yfinance's earnings_history endpoint is flaky — that's fine; the factor
    degrades to "2 out of 3 components" when this comes back empty.
    """
    try:
        import yfinance as yf
        yt = yf.Ticker(ticker)
        hist = yt.earnings_history
        if hist is None or hist.empty:
            return None
        latest = hist.iloc[-1]
        # yfinance column names vary by version; check a few.
        for est_col, actual_col in [
            ("revenueEstimate", "revenueActual"),
            ("revenue_estimate", "revenue_actual"),
        ]:
            if est_col in hist.columns and actual_col in hist.columns:
                est = latest.get(est_col)
                act = latest.get(actual_col)
                if est and act and est != 0:
                    return float((act - est) / est * 100.0)
    except Exception as exc:
        log.debug("yfinance revenue surprise failed for %s: %s", ticker, type(exc).__name__)
    return None


def get_earnings_data(ticker: str) -> EarningsData | None:
    """Return triple-play components for `ticker`, or None if no earnings
    data is available at all.

    If FINNHUB_API_KEY is not set, returns None immediately.
    If Finnhub EPS fetch returns empty, returns None.
    yfinance revenue is best-effort; failure returns EarningsData with
    revenue_surprise_pct=None — scoring still works on 2 of 3 components.
    """
    key = _api_key()
    if not key:
        return None

    eps = _fetch_latest_eps_surprise(ticker, key)
    if eps is None:
        return None
    report_period, eps_surprise_pct = eps
    try:
        days = (dt.date.today() - dt.date.fromisoformat(report_period)).days
    except ValueError:
        days = 999

    before, after = _fetch_analyst_momentum(ticker, key, report_period)
    delta: float | None = None
    if before is not None and after is not None:
        delta = (after - before) * 100.0  # convert share to percentage points

    rev = _fetch_revenue_surprise_yfinance(ticker)

    return EarningsData(
        ticker=ticker,
        report_period=report_period,
        days_since_report=days,
        eps_surprise_pct=eps_surprise_pct,
        revenue_surprise_pct=rev,
        bullish_share_before=before,
        bullish_share_after=after,
        bullish_share_delta=delta,
    )
