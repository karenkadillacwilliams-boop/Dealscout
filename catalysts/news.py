"""News fetchers: yfinance Ticker.news and Google News RSS."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import quote_plus

import feedparser
import requests
import yfinance as yf

from catalysts.types import RawCatalyst

GNEWS_URL = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def fetch_gnews_rss(tickers: Iterable[str]) -> list[RawCatalyst]:
    out: list[RawCatalyst] = []
    for t in tickers:
        q = quote_plus(f'"{t}" (merger OR acquisition OR partnership OR "in talks")')
        try:
            r = requests.get(GNEWS_URL.format(q=q), timeout=10)
            r.raise_for_status()
            feed = feedparser.parse(r.content)
            for entry in feed.entries[:20]:
                guid = getattr(entry, "id", None) or getattr(entry, "link", "")
                published = getattr(entry, "published", "") or ""
                try:
                    dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                    published = dt.isoformat(timespec="seconds")
                except Exception:
                    published = datetime.now(timezone.utc).isoformat(timespec="seconds")
                out.append(RawCatalyst(
                    ticker=t, source="gnews",
                    source_id=f"gnews:{t}:{guid}",
                    headline=entry.title,
                    url=getattr(entry, "link", ""),
                    published_at=published,
                ))
        except Exception as ex:
            print(f"[gnews] {t}: {ex}")
        time.sleep(0.1)
    return out


def fetch_yfinance(tickers: Iterable[str]) -> list[RawCatalyst]:
    out: list[RawCatalyst] = []
    for t in tickers:
        try:
            raw = yf.Ticker(t).news or []
        except Exception as ex:
            print(f"[yfinance] {t}: {ex}")
            continue
        for item in raw:
            title = item.get("title") or ""
            link = item.get("link") or ""
            uid = item.get("uuid") or link
            ts = item.get("providerPublishTime")
            if ts:
                published = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")
            else:
                published = datetime.now(timezone.utc).isoformat(timespec="seconds")
            if not title or not link:
                continue
            out.append(RawCatalyst(
                ticker=t, source="yfinance",
                source_id=f"yfinance:{t}:{uid}",
                headline=title, url=link, published_at=published,
            ))
    return out
