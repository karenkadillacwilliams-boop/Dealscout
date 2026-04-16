"""Composite scoring for filtered option contracts."""
from __future__ import annotations

from catalysts.options import OptionContract


def leverage_ratio(strike: float, underlying_price: float, ask: float) -> float:
    if ask <= 0:
        return 0.0
    return abs(strike - underlying_price) / ask


def composite_score(*, leverage_ratio: float, catalyst_score: int, iv_rank: float) -> float:
    return (leverage_ratio * catalyst_score / 100) + (100 - iv_rank) * 0.1


def rank_contracts(
    contracts: list[OptionContract],
    catalyst_score: int,
    iv_rank: float,
) -> list[dict]:
    rows: list[dict] = []
    for c in contracts:
        lev = leverage_ratio(c.strike, c.underlying_price, c.ask)
        comp = composite_score(
            leverage_ratio=lev, catalyst_score=catalyst_score, iv_rank=iv_rank,
        )
        rows.append({
            "ticker": c.ticker,
            "contract_ticker": c.contract_ticker,
            "contract_type": c.contract_type,
            "strike": c.strike,
            "expiration_date": c.expiration_date,
            "dte": c.dte,
            "ask": c.ask,
            "bid": c.bid,
            "mid": c.mid,
            "volume": c.volume,
            "open_interest": c.open_interest,
            "iv": c.iv,
            "delta": c.delta,
            "gamma": c.gamma,
            "theta": c.theta,
            "vega": c.vega,
            "underlying_price": c.underlying_price,
            "leverage_ratio": round(lev, 4),
            "iv_rank": iv_rank,
            "composite_score": round(comp, 4),
        })
    rows.sort(key=lambda r: (-r["composite_score"], r["ask"]))
    return rows
