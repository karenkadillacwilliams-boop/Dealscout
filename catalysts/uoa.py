"""Unusual Options Activity detector — flags contracts with volume >> open interest."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from catalysts import polygon_client
from catalysts.options import OptionContract

log = logging.getLogger("uoa")

MIN_VOLUME = 500
VOL_OI_RATIO = 3.0


def _fetch_recent_trades(contract_ticker: str, limit: int = 50) -> list[dict]:
    body = polygon_client.get(
        f"/v3/trades/{contract_ticker}",
        params={"limit": limit, "sort": "timestamp", "order": "desc"},
    )
    if body is None:
        return []
    return body.get("results", [])


def classify_flow(trades: list[dict], ask_price: float) -> str:
    """Classify trade flow as 'sweep', 'block', or 'normal'.

    Sweep: 3+ trades at or above ask price from 2+ different exchanges.
    Block: any single trade with size >= 100 contracts.
    Normal: everything else.
    """
    if not trades:
        return "normal"

    at_ask = [t for t in trades if t.get("price", 0) >= ask_price * 0.98]

    if len(at_ask) >= 3:
        exchanges = {t.get("exchange") for t in at_ask}
        if len(exchanges) >= 2:
            return "sweep"

    if any(t.get("size", 0) >= 100 for t in trades):
        return "block"

    return "normal"


def detect_unusual(contracts: list[OptionContract], classify: bool = True) -> list[dict]:
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

        flow_type = "normal"
        if classify:
            trades = _fetch_recent_trades(c.contract_ticker)
            flow_type = classify_flow(trades, c.ask)

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
            "flow_type": flow_type,
            "detected_at": now_str,
        })
    signals.sort(key=lambda s: (-s["vol_oi_ratio"]))
    return signals
