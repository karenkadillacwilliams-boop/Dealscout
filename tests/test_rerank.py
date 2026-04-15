from catalysts import rerank
from catalysts.types import RawCatalyst, ScoredItem


def _scored(headline: str, kw: int = 30) -> ScoredItem:
    raw = RawCatalyst("NVDA", "gnews", f"gnews:NVDA:{headline[:20]}",
                      headline, "https://x", "2026-04-13T10:00:00Z", None)
    return ScoredItem(raw, kw, ("m&a-rumor",), ("in talks to",))


def test_rerank_fuses_scores(monkeypatch):
    def fake_call(batch):
        return [{"id": i, "score": 8, "rationale": "strong M&A signal",
                 "tags": ["m&a-rumor"]} for i, _ in enumerate(batch)]
    monkeypatch.setattr(rerank, "_call_claude", fake_call)
    items = [_scored("ACME in talks to acquire Widgets", kw=40)]
    out = rerank.rerank_batched(items, batch=10)
    assert len(out) == 1
    r = out[0]
    # final = round(0.6*40 + 0.4*80) = round(24 + 32) = 56
    assert r.final_score == 56
    assert r.llm_score == 8
    assert r.rationale == "strong M&A signal"


def test_rerank_empty_input():
    assert rerank.rerank_batched([], batch=10) == []


def test_rerank_respects_daily_cap(monkeypatch):
    monkeypatch.setenv("MAX_RERANK_CALLS_PER_DAY", "0")
    calls = []
    monkeypatch.setattr(rerank, "_call_claude",
                        lambda batch: calls.append(batch) or [])
    out = rerank.rerank_batched([_scored("x")], batch=10)
    assert out[0].llm_score is None
    assert out[0].final_score == out[0].scored.kw_score
    assert calls == []
