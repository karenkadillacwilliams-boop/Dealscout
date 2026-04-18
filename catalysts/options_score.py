"""Composite scoring for filtered option contracts.

Two scoring modes:
  • "catalyst" (default): rewards contracts where a recent catalyst is driving
    expected move. A floor on catalyst_score ensures leverage stays meaningful
    even when the most recent catalyst has expired or scored 0.
  • "screener": ignores catalyst entirely — ranks purely on leverage and
    cheapness-of-IV. Use for the standalone Options Pulse screener when the
    user wants to surface convexity independent of news flow.
"""
from __future__ import annotations

from catalysts.options import OptionContract

CATALYST_SCORE_FLOOR = 20  # keeps leverage differentiable when no catalyst is fresh


def leverage_ratio(strike: float, underlying_price: float, ask: float) -> float:
    if ask <= 0:
        return 0.0
    return abs(strike - underlying_price) / ask


def composite_score(
    *,
    leverage_ratio: float,
    catalyst_score: int,
    iv_rank: float,
    catalyst_score_floor: int = CATALYST_SCORE_FLOOR,
) -> float:
    """Catalyst-aware composite. Floors catalyst_score so that when no fresh
    catalyst exists, leverage still drives ranking differentiation instead
    of the whole population collapsing to ~(100 - iv_rank) * 0.1."""
    effective = max(catalyst_score, catalyst_score_floor)
    return (leverage_ratio * effective / 100) + (100 - iv_rank) * 0.1


def screener_score(*, leverage_ratio: float, iv_rank: float) -> float:
    """Standalone screener: ranks purely on leverage and IV cheapness.
    Use when catalyst context is irrelevant (e.g. the Options Pulse tab
    browsed by a user looking for cheap convexity)."""
    return leverage_ratio * 0.5 + (100 - iv_rank) * 0.1


def rank_contracts(
    contracts: list[OptionContract],
    catalyst_score: int,
    iv_rank: float,
    mode: str = "catalyst",
) -> list[dict]:
    if mode not in ("catalyst", "screener"):
        raise ValueError(f"unknown mode: {mode!r}")

    rows: list[dict] = []
    for c in contracts:
        lev = leverage_ratio(c.strike, c.underlying_price, c.ask)
        if mode == "screener":
            comp = screener_score(leverage_ratio=lev, iv_rank=iv_rank)
        else:
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
