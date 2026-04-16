"""Polygon.io options chain fetcher with client-side filtering."""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import requests

log = logging.getLogger("options")


@dataclass(frozen=True)
class OptionContract:
    ticker: str
    contract_ticker: str
    contract_type: str
    strike: float
    expiration_date: str
    dte: int
    ask: float
    bid: float
    mid: float
    volume: int
    open_interest: int
    iv: float
    delta: float
    gamma: float
    theta: float
    vega: float
    underlying_price: float


_BASE = "https://api.polygon.io/v3/snapshot/options"


def _api_key() -> str:
    return os.environ.get("POLYGON_API_KEY", "")


def fetch_chain(
    ticker: str,
    *,
    max_ask: float = 2.00,
    min_dte: int = 7,
    max_dte: int = 28,
    ref_date: Optional[date] = None,
) -> list[OptionContract]:
    key = _api_key()
    if not key:
        log.warning("POLYGON_API_KEY not set, skipping options fetch")
        return []

    today = ref_date or date.today()
    exp_gte = (today + timedelta(days=min_dte)).isoformat()
    exp_lte = (today + timedelta(days=max_dte)).isoformat()

    params: dict = {
        "expiration_date.gte": exp_gte,
        "expiration_date.lte": exp_lte,
        "limit": 250,
        "apiKey": key,
    }

    results: list[dict] = []
    url: Optional[str] = f"{_BASE}/{ticker}"

    try:
        while url:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            body = resp.json()
            results.extend(body.get("results", []))
            next_url = body.get("next_url")
            if next_url:
                # subsequent pages: URL already contains query params from Polygon
                url = next_url
                params = {"apiKey": key}
            else:
                url = None
    except requests.HTTPError as exc:
        log.warning("polygon fetch %s failed: %s", ticker, exc)
        return []
    except requests.RequestException as exc:
        log.warning("polygon fetch %s failed: %s", ticker, exc)
        return []

    contracts: list[OptionContract] = []
    for r in results:
        details = r.get("details", {})
        greeks = r.get("greeks", {})
        quote = r.get("last_quote", {})
        day = r.get("day", {})
        underlying = r.get("underlying_asset", {})

        iv = r.get("implied_volatility")
        if iv is None or not greeks:
            continue

        ask = quote.get("ask") or 0.0
        bid = quote.get("bid") or 0.0
        if ask <= 0 or ask > max_ask:
            continue

        exp_str = details.get("expiration_date", "")
        try:
            exp_d = date.fromisoformat(exp_str)
        except ValueError:
            continue
        dte = (exp_d - today).days
        if dte < min_dte or dte > max_dte:
            continue

        contracts.append(OptionContract(
            ticker=underlying.get("ticker", ticker),
            contract_ticker=details.get("ticker", ""),
            contract_type=details.get("contract_type", ""),
            strike=details.get("strike_price", 0.0),
            expiration_date=exp_str,
            dte=dte,
            ask=ask,
            bid=bid,
            mid=round((ask + bid) / 2, 4),
            volume=day.get("volume", 0),
            open_interest=r.get("open_interest", 0),
            iv=iv,
            delta=greeks.get("delta", 0.0),
            gamma=greeks.get("gamma", 0.0),
            theta=greeks.get("theta", 0.0),
            vega=greeks.get("vega", 0.0),
            underlying_price=underlying.get("price", 0.0),
        ))

    return contracts


def fetch_chains_batch(
    tickers: list[str],
    *,
    max_ask: float = 2.00,
    min_dte: int = 7,
    max_dte: int = 28,
    delay: float = 0.1,
) -> list[OptionContract]:
    all_contracts: list[OptionContract] = []
    for t in tickers:
        all_contracts.extend(fetch_chain(t, max_ask=max_ask, min_dte=min_dte, max_dte=max_dte))
        time.sleep(delay)
    return all_contracts
