"""Channel-agnostic alert dispatcher."""
from __future__ import annotations

import logging

from alerts import discord, email
from catalysts.types import RerankedItem

log = logging.getLogger("alerts")

_CHANNELS = (email, discord)


def send(item: RerankedItem) -> tuple[bool, list[str]]:
    sent: list[str] = []
    ok = True
    subject = f"[{item.ticker}] {(item.tags[0] if item.tags else 'catalyst')} " \
              f"\u2014 score {item.final_score}"
    for channel in _CHANNELS:
        try:
            channel.send(
                subject=subject,
                headline=item.headline,
                rationale=item.rationale,
                url=item.url,
                source=item.source,
                published_at=item.published_at,
            )
            sent.append(channel.NAME)
        except Exception as ex:
            ok = False
            log.warning("alert channel %s failed: %s", channel.NAME, ex)
    return ok, sent
