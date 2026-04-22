"""Learn per-catalyst-type score multipliers from confirmed events.

The portfolios pipeline detects 5%+ position moves and auto-links them to
nearby catalysts; the user then labels each confirmed event with a
`catalyst_type` (earnings, m&a, rumor, product, etc.). This module closes
the feedback loop: it reads those confirmed events, computes a hit rate
per catalyst_type, and derives a multiplier that the scorer applies on
the next run.

  hit_rate >= 0.70  -> 1.30    (reliably pays off)
  hit_rate >= 0.55  -> 1.10
  hit_rate >= 0.45  -> 1.00    (neutral)
  hit_rate >= 0.30  -> 0.80
  hit_rate <  0.30  -> 0.60    (consistently loses)

Fewer than 5 events for a catalyst_type -> multiplier = 1.0 (no signal).
All multipliers are clamped to [0.5, 1.5].
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

# catalyst_type (the user-applied label on an event) -> scorer tags
# (the keys `score.score_item` emits). A single catalyst_type can map to
# several scorer tags — e.g. "m&a" covers both confirmed agreements and
# rumored ones. When two catalyst_types contribute to the same scorer tag,
# their multipliers are averaged in `load_catalyst_tag_multipliers`.
_TYPE_TO_TAGS: dict[str, list[str]] = {
    "earnings":   ["filing"],           # earnings are 10-Q / 8-K filings
    "m&a":        ["m&a-confirmed", "m&a-rumor"],
    "rumor":      ["m&a-rumor"],
    "product":    ["product"],
    "management": ["activist"],
    "political":  [],  # no native scorer tag
    "industry":   [],  # no native scorer tag
    "market":     [],  # no native scorer tag
    "other":      [],
}

_MULTIPLIER_MIN = 0.5
_MULTIPLIER_MAX = 1.5


def tags_for_catalyst_type(catalyst_type: str) -> list[str]:
    """Return the scorer tag(s) corresponding to an event-level catalyst_type."""
    return list(_TYPE_TO_TAGS.get(catalyst_type, []))


def _multiplier_for(hit_rate: float, n: int, min_events: int) -> float:
    if n < min_events:
        return 1.0
    if hit_rate >= 0.70:
        mult = 1.30
    elif hit_rate >= 0.55:
        mult = 1.10
    elif hit_rate >= 0.45:
        mult = 1.00
    elif hit_rate >= 0.30:
        mult = 0.80
    else:
        mult = 0.60
    return max(_MULTIPLIER_MIN, min(_MULTIPLIER_MAX, mult))


def compute_tag_multipliers(
    conn: sqlite3.Connection,
    min_events: int = 5,
    return_stats: bool = False,
):
    """Compute {catalyst_type: multiplier} from confirmed events.

    When `return_stats=True` returns (multipliers, stats) where stats is
    {catalyst_type: {"n": int, "hit_rate": float, "net_pnl_per_event": float}}.
    """
    rows = conn.execute(
        "SELECT catalyst_type, move_pct, pnl_dollars "
        "FROM events "
        "WHERE status='confirmed' AND catalyst_type IS NOT NULL"
    ).fetchall()

    buckets: dict[str, dict] = {}
    for r in rows:
        # sqlite3.Row and tuple both support index access.
        ctype = r[0] if not hasattr(r, "keys") else r["catalyst_type"]
        move = r[1] if not hasattr(r, "keys") else r["move_pct"]
        pnl = r[2] if not hasattr(r, "keys") else r["pnl_dollars"]
        b = buckets.setdefault(ctype, {"n": 0, "wins": 0, "pnl_sum": 0.0})
        b["n"] += 1
        if move is not None and move > 0:
            b["wins"] += 1
        if pnl is not None:
            b["pnl_sum"] += float(pnl)

    multipliers: dict[str, float] = {}
    stats: dict[str, dict] = {}
    for ctype, b in buckets.items():
        n = b["n"]
        hit_rate = (b["wins"] / n) if n else 0.0
        net = (b["pnl_sum"] / n) if n else 0.0
        multipliers[ctype] = _multiplier_for(hit_rate, n, min_events)
        stats[ctype] = {"n": n, "hit_rate": hit_rate, "net_pnl_per_event": net}

    if return_stats:
        return multipliers, stats
    return multipliers


def persist_tag_multipliers(
    conn: sqlite3.Connection,
    multipliers: dict[str, float],
    stats: dict[str, dict],
) -> None:
    """Upsert a row per catalyst_type into `tag_multipliers`."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for ctype, mult in multipliers.items():
        s = stats.get(ctype, {})
        n = int(s.get("n", 0))
        hr = float(s.get("hit_rate", 0.0))
        conn.execute(
            "INSERT INTO tag_multipliers(catalyst_type, multiplier, n_events, hit_rate, updated_at) "
            "VALUES(?,?,?,?,?) "
            "ON CONFLICT(catalyst_type) DO UPDATE SET "
            "  multiplier=excluded.multiplier, n_events=excluded.n_events, "
            "  hit_rate=excluded.hit_rate, updated_at=excluded.updated_at",
            (ctype, float(mult), n, hr, now),
        )
    conn.commit()


def load_catalyst_tag_multipliers(conn: sqlite3.Connection) -> dict[str, float]:
    """Return {scorer_tag: multiplier} by expanding catalyst_type -> tags.

    When two catalyst_types map to the same scorer tag (e.g. "m&a" and
    "rumor" both cover "m&a-rumor"), the multipliers are averaged.
    Returns {} when the tag_multipliers table is empty.
    """
    try:
        rows = conn.execute(
            "SELECT catalyst_type, multiplier FROM tag_multipliers"
        ).fetchall()
    except Exception:
        return {}
    if not rows:
        return {}

    agg: dict[str, list[float]] = {}
    for r in rows:
        ctype = r[0] if not hasattr(r, "keys") else r["catalyst_type"]
        mult = r[1] if not hasattr(r, "keys") else r["multiplier"]
        for tag in _TYPE_TO_TAGS.get(ctype, []):
            agg.setdefault(tag, []).append(float(mult))
    return {tag: sum(vals) / len(vals) for tag, vals in agg.items() if vals}
