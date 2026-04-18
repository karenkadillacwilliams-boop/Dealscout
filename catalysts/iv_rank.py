"""IV history tracking and IV rank computation."""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Optional, Sequence

from catalysts import polygon_client
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


def backfill_realized_vol(
    ticker: str,
    conn,
    days: int = 30,
) -> bool:
    """Backfill iv_history with realized volatility as IV proxy.

    Skips if the ticker already has >= 5 days of iv_history.
    Returns True if backfill was performed.
    """
    from catalysts.db import upsert_iv_history

    existing = conn.execute(
        "SELECT COUNT(*) FROM iv_history WHERE ticker=?", (ticker,)
    ).fetchone()[0]
    if existing >= 5:
        return False

    end = date.today()
    start = end - timedelta(days=days + 30)  # extra buffer for weekends/holidays

    body = polygon_client.get(
        f"/v2/aggs/ticker/{ticker}/range/1/day/{start.isoformat()}/{end.isoformat()}",
        params={"adjusted": "true", "sort": "asc"},
    )
    if body is None:
        return False
    bars = body.get("results", [])

    if len(bars) < 22:
        return False

    closes = [b["c"] for b in bars]
    log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]

    window = 20
    for i in range(window, len(log_returns)):
        segment = log_returns[i - window : i]
        mean = sum(segment) / window
        variance = sum((x - mean) ** 2 for x in segment) / (window - 1)
        realized_vol = math.sqrt(variance) * math.sqrt(252)

        bar_ts = bars[i + 1]["t"]  # +1 because log_returns is offset by 1
        bar_date = date.fromtimestamp(bar_ts / 1000).isoformat()
        upsert_iv_history(conn, ticker, bar_date, round(realized_vol, 6))

    return True


def backfill_batch(tickers: list[str], conn) -> int:
    """Backfill realized vol for all tickers that need it. Rate-limiting handled by polygon_client."""
    count = 0
    for t in tickers:
        if backfill_realized_vol(t, conn):
            count += 1
    return count
