"""IPO tracker using Polygon ticker reference + news."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import requests

log = logging.getLogger("ipo")


@dataclass
class IPOEntry:
    ticker: str
    name: str
    list_date: str
    market: str
    primary_exchange: str
    currency: str


def fetch_recent_ipos(lookback_days: int = 90) -> list[IPOEntry]:
    """Fetch recently listed tickers from Polygon reference data."""
    key = os.environ.get("POLYGON_API_KEY", "")
    if not key:
        return []

    since = (date.today() - timedelta(days=lookback_days)).isoformat()
    entries: list[IPOEntry] = []

    url: Optional[str] = (
        "https://api.polygon.io/v3/reference/tickers"
        f"?market=stocks&type=CS&sort=list_date&order=desc"
        f"&list_date.gte={since}&limit=100&apiKey={key}"
    )

    while url:
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            body = r.json()
            for t in body.get("results", []):
                entries.append(IPOEntry(
                    ticker=t.get("ticker", ""),
                    name=t.get("name", ""),
                    list_date=t.get("list_date", ""),
                    market=t.get("market", ""),
                    primary_exchange=t.get("primary_exchange", ""),
                    currency=t.get("currency_name", ""),
                ))
            url = body.get("next_url")
            if url and "apiKey=" not in url:
                url += f"&apiKey={key}"
        except Exception as exc:
            log.warning("IPO fetch failed: %s", exc)
            break

    return entries
