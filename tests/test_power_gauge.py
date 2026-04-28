import pytest


def test_earnings_surprises_boosted_by_revenue():
    from power_gauge import score_earnings_surprises
    base = {"earningsHistory": [{"surprisePct": 5}, {"surprisePct": 3}]}
    with_rev = dict(base, revenueSurprisePct=8.0)
    without = score_earnings_surprises(base)
    with_ = score_earnings_surprises(with_rev)
    assert with_["score"] > without["score"]
    # 8% falls in the >5% bucket => +10 additive booster
    assert with_["score"] - without["score"] >= 10


def test_earnings_surprises_boosted_by_guidance():
    from power_gauge import score_earnings_surprises
    base = {"earningsHistory": [{"surprisePct": 5}]}
    with_gd = dict(base, bullishShareDelta=6.0)
    without = score_earnings_surprises(base)
    with_ = score_earnings_surprises(with_gd)
    assert with_["score"] > without["score"]
    # 6pp falls in the >5pp bucket => +10 additive booster
    assert with_["score"] - without["score"] >= 10


def test_earnings_surprises_no_extras_unchanged_detail():
    """Without rev/guidance inputs the detail string is the pre-change format."""
    from power_gauge import score_earnings_surprises
    base = {"earningsHistory": [{"surprisePct": 5}, {"surprisePct": 3}]}
    out = score_earnings_surprises(base)
    assert "rev " not in out["detail"]
    assert "gdn " not in out["detail"]


def test_earnings_surprises_detail_includes_extras():
    from power_gauge import score_earnings_surprises
    f = {
        "earningsHistory": [{"surprisePct": 5}],
        "revenueSurprisePct": 8.0,
        "bullishShareDelta": 6.0,
    }
    out = score_earnings_surprises(f)
    assert "rev +8.0%" in out["detail"]
    assert "gdn +6.0pp" in out["detail"]


def test_revenue_miss_penalises_score():
    from power_gauge import score_earnings_surprises
    base = {"earningsHistory": [{"surprisePct": 5}]}
    miss = dict(base, revenueSurprisePct=-8.0)
    assert score_earnings_surprises(miss)["score"] < score_earnings_surprises(base)["score"]
