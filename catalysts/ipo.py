"""IPO tracker using Polygon ticker reference data."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from catalysts import polygon_client

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
    since = (date.today() - timedelta(days=lookback_days)).isoformat()
    entries: list[IPOEntry] = []

    params = {
        "market": "stocks",
        "type": "CS",
        "sort": "list_date",
        "order": "desc",
        "list_date.gte": since,
        "limit": 100,
    }

    for page in polygon_client.paginate("/v3/reference/tickers", params=params):
        for t in page.get("results", []):
            entries.append(IPOEntry(
                ticker=t.get("ticker", ""),
                name=t.get("name", ""),
                list_date=t.get("list_date", ""),
                market=t.get("market", ""),
                primary_exchange=t.get("primary_exchange", ""),
                currency=t.get("currency_name", ""),
            ))

    return entries
