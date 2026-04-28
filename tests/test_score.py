from catalysts.score import score_item
from catalysts.types import RawCatalyst
from tests.fixtures.headlines import ALL, CONFIRMED_MA, RUMORED_MA, NOISE


def _raw(headline: str, form_type: str | None) -> RawCatalyst:
    return RawCatalyst(
        ticker="TEST", source="edgar" if form_type else "gnews",
        source_id=headline[:40], headline=headline, url="https://example.com",
        published_at="2026-04-13T10:00:00Z", form_type=form_type,
    )


def test_confirmed_ma_scores_high():
    for h, ft, min_score, tag in CONFIRMED_MA:
        item = score_item(_raw(h, ft))
        assert item.kw_score >= min_score, (h, item.kw_score)
        assert tag in item.tags, (h, item.tags)


def test_rumored_ma_scores_medium():
    for h, ft, min_score, tag in RUMORED_MA:
        item = score_item(_raw(h, ft))
        assert item.kw_score >= min_score, (h, item.kw_score)
        assert tag in item.tags, (h, item.tags)


def test_noise_scores_zero_or_low():
    for h, ft, min_score, tag in NOISE:
        item = score_item(_raw(h, ft))
        assert item.kw_score <= 20, (h, item.kw_score)
        if tag == "weak":
            assert "weak" in item.tags


def test_filing_bonus_added_for_mna_8k():
    base = score_item(_raw("Company enters merger agreement", None))
    filed = score_item(_raw("Company enters merger agreement", "8-K"))
    assert filed.kw_score > base.kw_score


def test_score_is_bounded_0_100():
    item = score_item(_raw(
        "Definitive agreement, to acquire, tender offer, merger agreement, strategic alternatives",
        "8-K"
    ))
    assert 0 <= item.kw_score <= 100


def test_tag_multipliers_boost_score():
    """Learned multipliers > 1.0 raise the score of a matching tag."""
    raw = _raw("Company enters definitive agreement to acquire rival", None)
    base = score_item(raw)
    boosted = score_item(raw, tag_multipliers={"m&a-confirmed": 1.30})
    assert boosted.kw_score > base.kw_score


def test_tag_multipliers_none_preserves_behavior():
    """tag_multipliers=None is identical to not passing the argument."""
    raw = _raw("Company enters definitive agreement to acquire rival", None)
    a = score_item(raw)
    b = score_item(raw, tag_multipliers=None)
    assert a.kw_score == b.kw_score
