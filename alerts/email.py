"""Gmail SMTP channel using stdlib EmailMessage (header-injection safe)."""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

NAME = "email"


def send(*, subject: str, headline: str, rationale: str | None, url: str,
         source: str, published_at: str, **_) -> None:
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
    msg.set_content(body)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as s:
        s.login(user, pw)
        s.send_message(msg)
