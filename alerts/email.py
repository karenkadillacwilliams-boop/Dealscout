"""Gmail SMTP channel using stdlib EmailMessage (header-injection safe)."""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

NAME = "email"


_REQUIRED_ENV = ("GMAIL_USER", "GMAIL_APP_PW", "ALERT_TO_EMAIL")


def send(*, subject: str, headline: str, rationale: str | None, url: str,
         source: str, published_at: str, options_summary: str | None = None,
         related_tickers: str | None = None, **_) -> None:
    missing = [k for k in _REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"email channel missing env vars: {', '.join(missing)}")
    user = os.environ["GMAIL_USER"]
    pw = os.environ["GMAIL_APP_PW"]
    to = os.environ["ALERT_TO_EMAIL"]

    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = to
    msg["Subject"] = subject  # EmailMessage sanitizes newlines
    body = (
        f"{headline}\n\n"
        f"{rationale or ''}\n\n"
        f"Source: {source}    Published: {published_at}\n"
        f"{url}\n"
    )
    if options_summary:
        body += f"\n{options_summary}\n"
    if related_tickers:
        body += f"\n{related_tickers}\n"
    msg.set_content(body)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as s:
        s.login(user, pw)
        s.send_message(msg)
