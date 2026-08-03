"""Tests for the poller's alert gate.

The kw_score arm of this gate is only reached when final_score >= 70 AND
llm_score is None — i.e. a high-scoring item that was never reranked. That is
produced by rerank._kw_only() when the Anthropic call fails, the daily cap is
exhausted, or the model omits an id from its response.

Short-circuit evaluation means every ordinary reranked item skips that arm, so
a missing RerankedItem.kw_score passthrough stayed invisible until exactly the
case the fallback exists to serve. These tests walk the boundary explicitly.
"""
from __future__ import annotations

import pytest

from catalyst_poller import should_alert
from catalysts.rerank import _kw_only
from catalysts.types import RawCatalyst, RerankedItem, ScoredItem


def _item(final_score: int, llm_score: int | None, kw_score: int) -> RerankedItem:
    raw = RawCatalyst(ticker="T", source="edgar", source_id="i", headline="h",
                      url="u", published_at="2026-01-01T00:00:00")
    scored = ScoredItem(raw=raw, kw_score=kw_score, tags=(), matched_phrases=())
    return RerankedItem(scored=scored, llm_score=llm_score, rationale=None,
                        final_score=final_score)


def test_reranked_item_exposes_kw_score():
    """The gate reads item.kw_score; without this passthrough it raises."""
    assert _item(90, None, 90).kw_score == 90


@pytest.mark.parametrize("kw_score", [70, 85, 100])
def test_keyword_only_high_scorer_does_not_crash_the_gate(kw_score):
    """rerank fell back to keyword-only — the gate must still evaluate.

    Regression: this raised AttributeError and took down the whole poller run
    partway through the persist loop.
    """
    raw = RawCatalyst(ticker="T", source="edgar", source_id="i", headline="h",
                      url="u", published_at="2026-01-01T00:00:00")
    scored = ScoredItem(raw=raw, kw_score=kw_score, tags=(), matched_phrases=())
    item = _kw_only(scored)  # the real fallback constructor

    assert item.llm_score is None
    assert item.final_score == kw_score
    assert should_alert(item) is (kw_score >= 85)


@pytest.mark.parametrize(
    "final_score,llm_score,kw_score,expected",
    [
        (70,  5,    0,   True),   # reranked, exactly at the threshold
        (100, 5,    0,   True),   # reranked, well above
        (69,  5,    100, False),  # final_score below threshold dominates
        (70,  None, 85,  True),   # keyword fallback, exactly at its threshold
        (70,  None, 84,  False),  # keyword fallback, one under
        (70,  None, 100, True),   # keyword fallback, well above
        (0,   None, 100, False),  # high keywords but final_score never made it
        (70,  0,    0,   True),   # llm_score 0 is not None — still counts
    ],
)
def test_gate_decision_boundary(final_score, llm_score, kw_score, expected):
    assert should_alert(_item(final_score, llm_score, kw_score)) is expected
