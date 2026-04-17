from catalysts import news


def test_gnews_parses_rss(monkeypatch):
    sample = """<?xml version="1.0"?><rss><channel>
      <item><title>ACME in talks to acquire Widgets</title>
      <link>https://news.example.com/a</link>
      <pubDate>Mon, 13 Apr 2026 10:00:00 GMT</pubDate>
      <guid>https://news.example.com/a</guid></item>
    </channel></rss>"""

    class R:
        status_code = 200
        content = sample.encode()
        text = sample
        def raise_for_status(self): pass

    monkeypatch.setattr(news.requests, "get", lambda *a, **k: R())
    items = news.fetch_gnews_rss(["ACME"])
    assert len(items) >= 1
    assert items[0].source == "gnews"
    assert items[0].ticker == "ACME"
    assert "acquire" in items[0].headline.lower()


def test_yfinance_news_handles_empty(monkeypatch):
    class FakeTicker:
        news = []
    monkeypatch.setattr(news.yf, "Ticker", lambda t: FakeTicker())
    items = news.fetch_yfinance(["ACME"])
    assert items == []


def test_polygon_news_parses_articles(monkeypatch):
    import requests as req_mod
    from unittest.mock import MagicMock

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "results": [
            {
                "id": "poly1",
                "title": "AAPL beats earnings estimates",
                "article_url": "https://example.com/aapl",
                "published_utc": "2026-04-16T14:00:00Z",
                "tickers": ["AAPL"],
            },
            {
                "id": "poly2",
                "title": "",  # empty title — should be skipped
                "article_url": "https://example.com/empty",
                "published_utc": "2026-04-16T14:00:00Z",
            },
        ],
    }
    mock_resp.raise_for_status = MagicMock()
    monkeypatch.setenv("POLYGON_API_KEY", "test-key")
    monkeypatch.setattr(req_mod, "get", lambda *a, **kw: mock_resp)

    from catalysts.news import fetch_polygon_news
    items = fetch_polygon_news(["AAPL"])
    assert len(items) == 1
    assert items[0].source == "polygon-news"
    assert items[0].source_id == "polygon:poly1"
    assert items[0].headline == "AAPL beats earnings estimates"


def test_polygon_news_deduplicates(monkeypatch):
    import requests as req_mod
    from unittest.mock import MagicMock

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "results": [
            {
                "id": "poly1",
                "title": "Breaking news",
                "article_url": "https://example.com/a",
                "published_utc": "2026-04-16T14:00:00Z",
            },
        ],
    }
    mock_resp.raise_for_status = MagicMock()
    monkeypatch.setenv("POLYGON_API_KEY", "test-key")
    monkeypatch.setattr(req_mod, "get", lambda *a, **kw: mock_resp)

    from catalysts.news import fetch_polygon_news
    # Same article appears for both tickers — should deduplicate by ID
    items = fetch_polygon_news(["AAPL", "NVDA"])
    assert len(items) == 1


def test_polygon_news_no_key_returns_empty(monkeypatch):
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    from catalysts.news import fetch_polygon_news
    items = fetch_polygon_news(["AAPL"])
    assert items == []
