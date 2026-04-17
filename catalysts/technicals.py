"""Technical confluence scoring via Polygon RSI, MACD, SMA indicators."""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import requests

log = logging.getLogger("technicals")

_BASE = "https://api.polygon.io/v1/indicators"


@dataclass
class TechSignals:
    ticker: str
    rsi: Optional[float] = None
    macd_histogram: Optional[float] = None
    price_vs_sma50: Optional[float] = None  # % above/below SMA50
    label: str = "—"
    score: int = 0  # -3 to +3


def _api_key() -> str:
    return os.environ.get("POLYGON_API_KEY", "")


def _fetch_indicator(ticker: str, indicator: str, params: dict) -> Optional[dict]:
    key = _api_key()
    if not key:
        return None
    params["apiKey"] = key
    try:
        r = requests.get(f"{_BASE}/{indicator}/{ticker}", params=params, timeout=10)
        r.raise_for_status()
        values = r.json().get("results", {}).get("values", [])
        return values[0] if values else None
    except Exception as exc:
        log.warning("indicator %s for %s failed: %s", indicator, ticker, exc)
        return None


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

    # Score: -3 to +3
    score = 0
    if sig.rsi is not None:
        if sig.rsi < 30:
            score += 1  # oversold = bullish opportunity
        elif sig.rsi > 70:
            score -= 1  # overbought = bearish warning
    if sig.macd_histogram is not None:
        if sig.macd_histogram > 0:
            score += 1  # bullish momentum
        else:
            score -= 1  # bearish momentum
    if sig.price_vs_sma50 is not None:
        if sig.price_vs_sma50 > 0:
            score += 1  # above 50-day SMA
        else:
            score -= 1  # below 50-day SMA

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
    delay: float = 0.05,
) -> dict[str, TechSignals]:
    results: dict[str, TechSignals] = {}
    for t in tickers:
        last = prices.get(t) if prices else None
        results[t] = fetch_technicals(t, last_price=last)
        time.sleep(delay)  # 3 calls per ticker, light delay between tickers
    return results
