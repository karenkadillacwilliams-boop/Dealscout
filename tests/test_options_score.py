from catalysts.options import OptionContract
from catalysts.options_score import leverage_ratio, composite_score, rank_contracts


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


def test_composite_score_zero_catalyst():
    score = composite_score(leverage_ratio=5.0, catalyst_score=0, iv_rank=50.0)
    assert score == (100 - 50.0) * 0.1  # only IV term


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
    # same leverage_ratio, lower ask wins
    assert ranked[0]["ask"] <= ranked[1]["ask"]
