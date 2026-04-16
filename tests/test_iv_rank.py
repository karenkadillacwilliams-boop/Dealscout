from catalysts.iv_rank import compute_iv_rank, compute_atm_avg_iv
from catalysts.options import OptionContract


def _contract(strike, iv, underlying=195.0):
    return OptionContract(
        ticker="AAPL", contract_ticker=f"O:AAPL260425C00{int(strike*1000):08d}",
        contract_type="call", strike=strike, expiration_date="2026-04-25",
        dte=10, ask=1.00, bid=0.90, mid=0.95, volume=100, open_interest=500,
        iv=iv, delta=0.3, gamma=0.02, theta=-0.05, vega=0.1,
        underlying_price=underlying,
    )


def test_compute_atm_avg_iv():
    contracts = [
        _contract(190.0, 0.40),  # ATM-1
        _contract(195.0, 0.42),  # ATM
        _contract(200.0, 0.44),  # ATM+1
        _contract(210.0, 0.55),  # out of range
        _contract(250.0, 0.70),  # far OTM
    ]
    avg = compute_atm_avg_iv(contracts, underlying_price=195.0)
    assert abs(avg - 0.42) < 0.01  # (0.40 + 0.42 + 0.44) / 3


def test_compute_atm_avg_iv_empty():
    assert compute_atm_avg_iv([], underlying_price=195.0) is None


def test_compute_iv_rank_with_history():
    history = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60]
    rank = compute_iv_rank(0.35, history)
    assert 10 < rank < 30  # 0.35 is near the low end


def test_compute_iv_rank_at_extremes():
    history = [0.30, 0.35, 0.40, 0.45, 0.50]
    assert compute_iv_rank(0.30, history) < 20
    assert compute_iv_rank(0.50, history) > 80


def test_compute_iv_rank_insufficient_history():
    rank = compute_iv_rank(0.40, [0.40])
    assert rank == 50.0  # default when < 5 data points
