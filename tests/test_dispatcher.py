from alerts import dispatcher
from catalysts.types import RawCatalyst, ScoredItem, RerankedItem


def _item(score: int = 90) -> RerankedItem:
    raw = RawCatalyst("NVDA", "gnews", "gnews:NVDA:x", "in talks to acquire",
                      "https://x", "2026-04-13T10:00:00Z", None)
    return RerankedItem(ScoredItem(raw, 60, ("m&a-rumor",), ("in talks to",)),
                        llm_score=9, rationale="clear rumor", final_score=score)


def test_all_channels_called(monkeypatch):
    sent = []

    class Stub:
        NAME = "stub"
        @staticmethod
        def send(**kw):
            sent.append(kw)

    class Stub2:
        NAME = "stub2"
        @staticmethod
        def send(**kw):
            sent.append(kw)

    monkeypatch.setattr(dispatcher, "_CHANNELS", (Stub, Stub2))
    ok, channels = dispatcher.send(_item())
    assert ok is True
    assert channels == ["stub", "stub2"]
    assert len(sent) == 2


def test_partial_failure(monkeypatch):
    class OK:
        NAME = "ok"
        @staticmethod
        def send(**kw): pass

    class Bad:
        NAME = "bad"
        @staticmethod
        def send(**kw): raise RuntimeError("boom")

    monkeypatch.setattr(dispatcher, "_CHANNELS", (OK, Bad))
    ok, channels = dispatcher.send(_item())
    assert ok is False
    assert channels == ["ok"]


def test_send_with_options_summary(monkeypatch):
    from alerts import dispatcher, email, discord
    from catalysts.types import RawCatalyst, ScoredItem, RerankedItem

    email_calls = []
    discord_calls = []
    monkeypatch.setattr(email, "send", lambda **kw: email_calls.append(kw))
    monkeypatch.setattr(discord, "send", lambda **kw: discord_calls.append(kw))

    raw = RawCatalyst("AAPL", "edgar", "acc1", "AAPL to acquire X",
                      "https://sec.gov/x", "2026-04-15T10:00:00Z", "8-K")
    scored = ScoredItem(raw=raw, kw_score=85, tags=("m&a-confirmed",), matched_phrases=("to acquire",))
    item = RerankedItem(scored=scored, llm_score=9, rationale="strong signal", final_score=85)

    options_summary = "Options: 3 calls under $2 | best: Apr 25 $200C @ $1.50 (leverage 3.3x, IV rank 22%)"
    ok, channels = dispatcher.send(item, options_summary=options_summary)

    assert ok
    assert email_calls[0]["options_summary"] == options_summary
    assert discord_calls[0]["options_summary"] == options_summary
