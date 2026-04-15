from alerts import discord


def test_discord_posts_embed(monkeypatch):
    captured = {}

    class R:
        status_code = 204
        def raise_for_status(self): pass

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["payload"] = json
        return R()

    monkeypatch.setattr(discord.requests, "post", fake_post)
    discord.send(
        subject="[NVDA] m&a-rumor — score 88",
        headline="NVDA in talks to acquire X",
        rationale="strong rumor signal",
        url="https://news.example.com/a",
        source="gnews",
        published_at="2026-04-13T10:00:00Z",
    )
    assert captured["url"] == "https://discord.test/webhook"
    embed = captured["payload"]["embeds"][0]
    assert "NVDA" in embed["title"]
    assert embed["description"] == "strong rumor signal"
