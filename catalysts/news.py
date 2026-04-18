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
                title = getattr(entry, "title", "") or ""
                link = getattr(entry, "link", "") or ""
                if not title or not link:
                    continue
                guid = getattr(entry, "id", None) or link
                try:
                    dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                    published = dt.isoformat(timespec="seconds")
                except Exception:
                    published = datetime.now(timezone.utc).isoformat(timespec="seconds")
                out.append(RawCatalyst(
                    ticker=t, source="gnews",
                    source_id=f"gnews:{t}:{guid}",
                    headline=title,
                    url=link,
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


def fetch_polygon_news(tickers: list[str], limit_per_ticker: int = 10) -> list[RawCatalyst]:
    """Fetch news from Polygon.io REST API for the given tickers."""
    from catalysts import polygon_client

    seen_ids: set[str] = set()
    results: list[RawCatalyst] = []

    for ticker in tickers:
        body = polygon_client.get(
            "/v2/reference/news",
            params={"ticker": ticker, "limit": limit_per_ticker},
        )
        if body is None:
            continue
        articles = body.get("results", [])

        for art in articles:
            art_id = art.get("id", "")
            if not art_id or art_id in seen_ids:
                continue
            seen_ids.add(art_id)

            title = art.get("title", "").strip()
            url = art.get("article_url", "").strip()
            pub = art.get("published_utc", "")
            if not title or not url:
                continue

            results.append(RawCatalyst(
                ticker=ticker,
                source="polygon-news",
                source_id=f"polygon:{art_id}",
                headline=title,
                url=url,
                published_at=pub,
            ))

    return results
