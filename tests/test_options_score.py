import pytest

from catalysts.options import OptionContract
from catalysts.options_score import (
    CATALYST_SCORE_FLOOR,
    composite_score,
    leverage_ratio,
    rank_contracts,
    screener_score,
)


def _contract(strike=200.0, ask=1.50, underlying=195.0, **kw):
    defaults = dict(
        ticker="AAPL", contract_ticker="O:AAPL260425C00200000",
        contract_type="call", expiration_date="2026-04-25", dte=10,
        bid=ask - 0.10, mid=ask - 0.05, volume=500, open_interest=2000,
        iv=0.42, delta=0.35, gamma=0.02, theta=-0.05, vega=0.10,
        underlying_price=underlying,
    )
    defaults.update(kw)
    return OptionContract(strike=strike, ask=ask, **defaults)


def test_leverage_ratio():
    assert leverage_ratio(200.0, 195.0, 1.50) == abs(200.0 - 195.0) / 1.50


def test_leverage_ratio_zero_ask():
    assert leverage_ratio(200.0, 195.0, 0.0) == 0.0


def test_composite_score_basic():
    score = composite_score(leverage_ratio=3.33, catalyst_score=85, iv_rank=22.0)
    expected = (3.33 * 85 / 100) + (100 - 22.0) * 0.1
    assert abs(score - expected) < 0.01


def test_composite_score_zero_catalyst_applies_floor():
    """Previously: catalyst=0 collapsed the leverage term, so all contracts
    clustered at (100 - iv_rank) * 0.1. The floor keeps leverage meaningful."""
    score = composite_score(leverage_ratio=5.0, catalyst_score=0, iv_rank=50.0)
    expected = (5.0 * CATALYST_SCORE_FLOOR / 100) + (100 - 50.0) * 0.1
    assert abs(score - expected) < 0.001
    assert score > (100 - 50.0) * 0.1, "floor must give leverage nonzero weight"


def test_composite_score_above_floor_uses_actual_catalyst():
    """When catalyst_score >= floor, it uses the real value (no flooring)."""
    above = composite_score(leverage_ratio=5.0, catalyst_score=80, iv_rank=30.0)
    expected = (5.0 * 80 / 100) + (100 - 30.0) * 0.1
    assert abs(above - expected) < 0.001


def test_composite_score_floor_is_overridable():
    score = composite_score(leverage_ratio=5.0, catalyst_score=0,
                            iv_rank=50.0, catalyst_score_floor=0)
    assert score == (100 - 50.0) * 0.1  # floor=0 restores old behavior


def test_rank_contracts_differentiates_without_catalyst():
    """With catalyst=0, ranking must still separate high-leverage from low."""
    low_lev = _contract(strike=195.5, ask=1.00, contract_ticker="LOW")   # lev ~ 0.5
    high_lev = _contract(strike=210.0, ask=0.40, contract_ticker="HIGH")  # lev ~ 37.5
    ranked = rank_contracts([low_lev, high_lev], catalyst_score=0, iv_rank=50.0)
    assert ranked[0]["contract_ticker"] == "HIGH"
    assert ranked[0]["composite_score"] > ranked[1]["composite_score"]


def test_screener_score_ignores_catalyst():
    s1 = screener_score(leverage_ratio=10.0, iv_rank=20.0)
    s2 = screener_score(leverage_ratio=2.0, iv_rank=20.0)
    assert s1 > s2


def test_rank_contracts_screener_mode():
    low_lev = _contract(strike=195.5, ask=1.00, contract_ticker="LOW")
    high_lev = _contract(strike=210.0, ask=0.40, contract_ticker="HIGH")
    ranked = rank_contracts([low_lev, high_lev],
                            catalyst_score=0, iv_rank=20.0, mode="screener")
    assert ranked[0]["contract_ticker"] == "HIGH"


def test_rank_contracts_rejects_unknown_mode():
    c = _contract()
    with pytest.raises(ValueError):
        rank_contracts([c], catalyst_score=50, iv_rank=50.0, mode="bogus")


def test_rank_contracts_sorted_descending():
    c1 = _contract(strike=200.0, ask=1.50, contract_ticker="C1")
    c2 = _contract(strike=210.0, ask=0.35, contract_ticker="C2")
    ranked = rank_contracts([c1, c2], catalyst_score=85, iv_rank=22.0)
    assert ranked[0]["composite_score"] >= ranked[1]["composite_score"] or \
           ranked[0]["ask"] <= ranked[1]["ask"]


def test_rank_contracts_tiebreak_by_ask():
    c1 = _contract(strike=200.0, ask=1.50, contract_ticker="C1")
    c2 = _contract(strike=200.0, ask=1.00, contract_ticker="C2")
    ranked = rank_contracts([c1, c2], catalyst_score=85, iv_rank=22.0)
    assert ranked[0]["ask"] <= ranked[1]["ask"]
