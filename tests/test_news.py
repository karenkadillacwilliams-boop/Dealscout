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
