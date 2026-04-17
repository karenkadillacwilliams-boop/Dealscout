from catalysts.options import OptionContract
from catalysts.uoa import detect_unusual


def _contract(volume=1000, open_interest=200, **kw):
    defaults = dict(
        ticker="AAPL", contract_ticker="O:AAPL260425C00200000",
        contract_type="call", strike=200.0, expiration_date="2026-04-25",
        dte=10, ask=1.50, bid=1.40, mid=1.45,
        iv=0.42, delta=0.35, gamma=0.02, theta=-0.05, vega=0.10,
        underlying_price=195.0,
    )
    defaults.update(kw)
    return OptionContract(volume=volume, open_interest=open_interest, **defaults)


def test_detect_unusual_flags_high_ratio():
    contracts = [_contract(volume=1000, open_interest=200)]  # ratio=5.0
    signals = detect_unusual(contracts)
    assert len(signals) == 1
    assert signals[0]["vol_oi_ratio"] == 5.0


def test_detect_unusual_skips_low_volume():
    contracts = [_contract(volume=100, open_interest=10)]  # ratio=10 but vol<500
    signals = detect_unusual(contracts)
    assert len(signals) == 0


def test_detect_unusual_skips_low_ratio():
    contracts = [_contract(volume=1000, open_interest=1000)]  # ratio=1.0
    signals = detect_unusual(contracts)
    assert len(signals) == 0


def test_detect_unusual_skips_zero_oi():
    contracts = [_contract(volume=1000, open_interest=0)]
    signals = detect_unusual(contracts)
    assert len(signals) == 0


def test_detect_unusual_sorted_by_ratio():
    contracts = [
        _contract(volume=600, open_interest=100, contract_ticker="A"),   # ratio=6
        _contract(volume=2000, open_interest=100, contract_ticker="B"),  # ratio=20
    ]
    signals = detect_unusual(contracts)
    assert signals[0]["vol_oi_ratio"] > signals[1]["vol_oi_ratio"]
