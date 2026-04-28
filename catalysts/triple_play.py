"""Triple-play post-earnings fundamental momentum scoring.

Triple play = (1) EPS beat, (2) revenue beat, (3) raised guidance.
Score 0..100, linearly decayed toward neutral 50 as days_since_report
approaches TRIPLE_PLAY_STALE_DAYS. A full-triple-play bonus of +10
fires when all three components clear their thresholds AND the report
is fresh (< 45 days).

Ported from breakout_scanner/backend/app/scoring/factors.py (triple_play).
"""
from __future__ import annotations

from dataclasses import dataclass

from catalysts.earnings import EarningsData

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TRIPLE_PLAY_STALE_DAYS = 90

_EPS_BEAT_THRESHOLD = 3.0        # % — needs at least a modest beat
_REV_BEAT_THRESHOLD = 2.0        # % — revenue surprises tend to be smaller
_GUIDANCE_RAISE_THRESHOLD = 3.0  # percentage points of analyst bullishness


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class TriplePlayScore:
    score: float                    # 0..100
    eps_component: float | None
    revenue_component: float | None
    guidance_component: float | None
    is_full_triple_play: bool
    bonus: float
    recency_decay: float
    days_since_report: int
    report_period: str | None


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _clip01(x: float) -> float:
    """Clip to [0, 100] (mirrors breakout_scanner's clip01 which clips 0..100)."""
    return max(0.0, min(100.0, x))


# ---------------------------------------------------------------------------
# Scoring function
# ---------------------------------------------------------------------------

def score_triple_play(data: EarningsData | None) -> TriplePlayScore:
    """Score post-earnings fundamental momentum on a 0..100 scale.

    When data is None returns a neutral score of 50.0.

    Three components (each 0..100, missing components excluded from blend):
      * eps: 50 + eps_surprise_pct * 3, so 10% beat -> 80, 20% beat -> 100.
      * revenue: 50 + revenue_surprise_pct * 4 (revenue surprises tend to be
        smaller in magnitude than EPS, so the coefficient is higher).
      * guidance: 50 + bullish_share_delta * 4. A 5-point rise in analyst
        "buy or better" share scores 70 — real but not triple-play level.

    Recency decay: linearly fades the score toward the neutral 50 as
    days_since_report approaches TRIPLE_PLAY_STALE_DAYS.

    Full-triple-play bonus: when all three components clear their thresholds
    AND the report is fresh (< 45d), add +10.
    """
    if data is None:
        return TriplePlayScore(
            score=50.0,
            eps_component=None,
            revenue_component=None,
            guidance_component=None,
            is_full_triple_play=False,
            bonus=0.0,
            recency_decay=0.0,
            days_since_report=999,
            report_period=None,
        )

    eps = data.eps_surprise_pct
    rev = data.revenue_surprise_pct
    guidance = data.bullish_share_delta
    days = int(data.days_since_report if data.days_since_report is not None else 999)

    eps_component: float | None = None
    revenue_component: float | None = None
    guidance_component: float | None = None

    components: list[float] = []

    if eps is not None:
        eps_component = _clip01(50.0 + float(eps) * 3.0)
        components.append(eps_component)
    if rev is not None:
        revenue_component = _clip01(50.0 + float(rev) * 4.0)
        components.append(revenue_component)
    if guidance is not None:
        guidance_component = _clip01(50.0 + float(guidance) * 4.0)
        components.append(guidance_component)

    if not components:
        return TriplePlayScore(
            score=50.0,
            eps_component=None,
            revenue_component=None,
            guidance_component=None,
            is_full_triple_play=False,
            bonus=0.0,
            recency_decay=0.0,
            days_since_report=days,
            report_period=data.report_period,
        )

    blended = sum(components) / len(components)

    # Full triple-play bonus — all three must be present, above threshold, fresh.
    is_full_triple = (
        eps is not None and eps >= _EPS_BEAT_THRESHOLD
        and rev is not None and rev >= _REV_BEAT_THRESHOLD
        and guidance is not None and guidance >= _GUIDANCE_RAISE_THRESHOLD
        and days < 45
    )
    bonus = 10.0 if is_full_triple else 0.0

    # Recency decay: stale reports fade the delta-from-50, preserving the
    # sign but shrinking the magnitude as we approach TRIPLE_PLAY_STALE_DAYS.
    if days >= TRIPLE_PLAY_STALE_DAYS:
        decay = 0.0
    else:
        decay = max(0.0, 1.0 - days / TRIPLE_PLAY_STALE_DAYS)

    decayed = 50.0 + (blended + bonus - 50.0) * decay
    final_score = _clip01(decayed)

    return TriplePlayScore(
        score=final_score,
        eps_component=eps_component,
        revenue_component=revenue_component,
        guidance_component=guidance_component,
        is_full_triple_play=is_full_triple,
        bonus=bonus,
        recency_decay=decay,
        days_since_report=days,
        report_period=data.report_period,
    )
