import pytest
from catalysts.earnings import EarningsData
from catalysts.triple_play import score_triple_play, TRIPLE_PLAY_STALE_DAYS


def test_score_none_returns_neutral():
    s = score_triple_play(None)
    assert s.score == 50.0
    assert s.is_full_triple_play is False


def test_score_full_triple_play_gets_bonus():
    data = EarningsData(
        ticker="AAPL", report_period="2026-04-10", days_since_report=10,
        eps_surprise_pct=15.0, revenue_surprise_pct=8.0,
        bullish_share_before=0.60, bullish_share_after=0.70,
        bullish_share_delta=10.0,
    )
    s = score_triple_play(data)
    assert s.is_full_triple_play is True
    assert s.bonus == 10.0
    # EPS 15% -> 50 + 45 = 95 (clipped)
    # Rev 8% -> 50 + 32 = 82
    # Guidance 10pp -> 50 + 40 = 90
    # Blend = (95+82+90)/3 = 89; +10 bonus = 99; decay at 10d ≈ 1 - 10/90 ≈ 0.889
    # Expected = 50 + (99-50)*0.889 ≈ 93.5
    assert 85 < s.score < 100


def test_score_only_eps_component_partial():
    data = EarningsData(
        ticker="X", report_period="2026-04-10", days_since_report=10,
        eps_surprise_pct=10.0, revenue_surprise_pct=None,
        bullish_share_before=None, bullish_share_after=None,
        bullish_share_delta=None,
    )
    s = score_triple_play(data)
    assert s.is_full_triple_play is False
    assert s.eps_component is not None
    assert s.revenue_component is None
    assert s.guidance_component is None


def test_score_decays_to_50_beyond_stale_days():
    data = EarningsData(
        ticker="X", report_period="2025-01-01", days_since_report=TRIPLE_PLAY_STALE_DAYS + 30,
        eps_surprise_pct=20.0, revenue_surprise_pct=10.0,
        bullish_share_before=0.5, bullish_share_after=0.6,
        bullish_share_delta=10.0,
    )
    s = score_triple_play(data)
    assert s.score == 50.0
    assert s.recency_decay == 0.0


def test_score_decays_linearly_within_window():
    data_fresh = EarningsData(
        ticker="X", report_period="2026-04-10", days_since_report=5,
        eps_surprise_pct=10.0, revenue_surprise_pct=5.0,
        bullish_share_before=None, bullish_share_after=None,
        bullish_share_delta=None,
    )
    data_mid = EarningsData(
        ticker="X", report_period="2026-04-10", days_since_report=60,
        eps_surprise_pct=10.0, revenue_surprise_pct=5.0,
        bullish_share_before=None, bullish_share_after=None,
        bullish_share_delta=None,
    )
    fresh = score_triple_play(data_fresh)
    mid = score_triple_play(data_mid)
    # Both are above-50 (positive), fresh should be further from 50 than mid
    assert fresh.score > mid.score
    assert fresh.score > 60
    assert 50 < mid.score < 60


def test_eps_miss_is_penalized():
    """Negative EPS surprise should push score below 50."""
    data = EarningsData(
        ticker="X", report_period="2026-04-10", days_since_report=10,
        eps_surprise_pct=-10.0, revenue_surprise_pct=None,
        bullish_share_before=None, bullish_share_after=None,
        bullish_share_delta=None,
    )
    s = score_triple_play(data)
    assert s.score < 50.0
