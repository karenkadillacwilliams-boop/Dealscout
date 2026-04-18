"""Technical confluence scoring via Polygon RSI, MACD, SMA indicators."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from catalysts import polygon_client

log = logging.getLogger("technicals")


@dataclass
class TechSignals:
    ticker: str
    rsi: Optional[float] = None
    macd_histogram: Optional[float] = None
    price_vs_sma50: Optional[float] = None  # % above/below SMA50
    label: str = "—"
    score: int = 0  # -3 to +3


def _fetch_indicator(ticker: str, indicator: str, params: dict) -> Optional[dict]:
    body = polygon_client.get(f"/v1/indicators/{indicator}/{ticker}", params=params)
    if body is None:
        return None
    values = body.get("results", {}).get("values", [])
    return values[0] if values else None


def fetch_technicals(ticker: str, last_price: Optional[float] = None) -> TechSignals:
    sig = TechSignals(ticker=ticker)

    rsi_data = _fetch_indicator(ticker, "rsi", {
        "timespan": "day", "window": 14, "series_type": "close", "limit": 1,
    })
    if rsi_data:
        sig.rsi = rsi_data.get("value")

    macd_data = _fetch_indicator(ticker, "macd", {
        "timespan": "day", "short_window": 12, "long_window": 26,
        "signal_window": 9, "series_type": "close", "limit": 1,
    })
    if macd_data:
        sig.macd_histogram = macd_data.get("histogram")

    sma_data = _fetch_indicator(ticker, "sma", {
        "timespan": "day", "window": 50, "series_type": "close", "limit": 1,
    })
    if sma_data and last_price and sma_data.get("value"):
        sma_val = sma_data["value"]
        sig.price_vs_sma50 = round((last_price / sma_val - 1) * 100, 2)

    score = 0
    if sig.rsi is not None:
        if sig.rsi < 30:
            score += 1  # oversold = bullish opportunity
        elif sig.rsi > 70:
            score -= 1  # overbought = bearish warning
    if sig.macd_histogram is not None:
        if sig.macd_histogram > 0:
            score += 1
        else:
            score -= 1
    if sig.price_vs_sma50 is not None:
        if sig.price_vs_sma50 > 0:
            score += 1
        else:
            score -= 1

    sig.score = score
    has_data = (
        sig.rsi is not None
        or sig.macd_histogram is not None
        or sig.price_vs_sma50 is not None
    )
    if has_data:
        if score >= 2:
            sig.label = "Bullish"
        elif score <= -2:
            sig.label = "Bearish"
        else:
            sig.label = "Neutral"

    return sig


def fetch_technicals_batch(
    tickers: list[str],
    prices: Optional[dict[str, float]] = None,
) -> dict[str, TechSignals]:
    """Fetch technicals for many tickers. Rate-limiting handled by polygon_client."""
    results: dict[str, TechSignals] = {}
    for t in tickers:
        last = prices.get(t) if prices else None
        results[t] = fetch_technicals(t, last_price=last)
    return results
