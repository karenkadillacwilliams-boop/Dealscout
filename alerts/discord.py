"""Discord webhook channel — single POST with an embed."""
from __future__ import annotations

import os
import requests

NAME = "discord"


def send(*, subject: str, headline: str, rationale: str | None, url: str,
         source: str, published_at: str, options_summary: str | None = None, **_) -> None:
    if not os.environ.get("DISCORD_WEBHOOK_URL"):
        raise RuntimeError("discord channel missing env var: DISCORD_WEBHOOK_URL")
    webhook = os.environ["DISCORD_WEBHOOK_URL"]

    fields = [
        {"name": "Headline", "value": headline[:900]},
        {"name": "Source", "value": f"{source} · {published_at}", "inline": True},
    ]
    if options_summary:
        fields.append({"name": "Options", "value": options_summary[:900]})

    payload = {
        "embeds": [{
            "title": subject[:256],
            "description": (rationale or "")[:2000],
            "url": url if url.startswith(("http://", "https://")) else None,
            "fields": fields,
            "color": 15158332,  # red
        }]
    }
    r = requests.post(webhook, json=payload, timeout=10)
    r.raise_for_status()
