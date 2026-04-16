"""IV history tracking and IV rank computation."""
from __future__ import annotations

from typing import Optional, Sequence

from catalysts.options import OptionContract

_MIN_HISTORY = 5
_DEFAULT_RANK = 50.0


def compute_atm_avg_iv(
    contracts: list[OptionContract],
    underlying_price: float,
) -> Optional[float]:
    if not contracts or underlying_price <= 0:
        return None
    strikes = sorted({c.strike for c in contracts})
    if not strikes:
        return None
    atm_strike = min(strikes, key=lambda s: abs(s - underlying_price))
    atm_idx = strikes.index(atm_strike)
    lo = max(0, atm_idx - 1)
    hi = min(len(strikes), atm_idx + 2)
    nearby_strikes = set(strikes[lo:hi])
    ivs = [c.iv for c in contracts if c.strike in nearby_strikes and c.iv is not None]
    return sum(ivs) / len(ivs) if ivs else None


def compute_iv_rank(current_iv: float, history: Sequence[float]) -> float:
    if len(history) < _MIN_HISTORY:
        return _DEFAULT_RANK
    below = sum(1 for h in history if h < current_iv)
    equal = sum(1 for h in history if h == current_iv)
    return round((below + 0.5 * equal) / len(history) * 100, 1)
