"""Unusual Options Activity detector — flags contracts with volume >> open interest."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from catalysts.options import OptionContract

log = logging.getLogger("uoa")

MIN_VOLUME = 500
VOL_OI_RATIO = 3.0


def detect_unusual(contracts: list[OptionContract]) -> list[dict]:
    signals: list[dict] = []
    now_str = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for c in contracts:
        if c.volume < MIN_VOLUME:
            continue
        if c.open_interest <= 0:
            continue
        ratio = c.volume / c.open_interest
        if ratio < VOL_OI_RATIO:
            continue
        signals.append({
            "ticker": c.ticker,
            "contract_ticker": c.contract_ticker,
            "contract_type": c.contract_type,
            "strike": c.strike,
            "expiration_date": c.expiration_date,
            "volume": c.volume,
            "open_interest": c.open_interest,
            "vol_oi_ratio": round(ratio, 2),
            "ask": c.ask,
            "underlying_price": c.underlying_price,
            "detected_at": now_str,
        })
    signals.sort(key=lambda s: -s["vol_oi_ratio"])
    return signals
