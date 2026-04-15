"""EDGAR fetcher — pulls the company-filing Atom feed per ticker.

SEC rate-limit rules: descriptive User-Agent and <= 10 req/sec. We sleep
100ms between tickers to stay comfortably under the limit.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from typing import Iterable

import defusedxml.ElementTree as ET
import requests

from catalysts.types import RawCatalyst

ATOM_NS = "{http://www.w3.org/2005/Atom}"
FEED_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar?"
    "action=getcompany&CIK={ticker}&type=&dateb=&owner=include&count=40&output=atom"
)


def _ua() -> str:
    return os.environ.get("SEC_USER_AGENT", "Dealscout/1.0 contact@example.com")


_ACC_RE = re.compile(r"accession-number=([0-9\-]+)")
_BRACKET_TAIL = re.compile(r"\s*\[.*?\]\s*$")


def _extract_form(title: str) -> str | None:
    """Form type = the chunk before the first ' - ' separator in EDGAR titles.
    Strips trailing bracketed annotations like '[Amend]' so 'SCHEDULE 13G/A
    [Amend]  - ...' becomes 'SCHEDULE 13G/A'.
    """
    if " - " not in title:
        return None
    head = title.split(" - ", 1)[0]
    head = _BRACKET_TAIL.sub("", head).strip()
    return head or None


def _parse_atom(body: bytes, ticker: str) -> list[RawCatalyst]:
    root = ET.fromstring(body)
    out: list[RawCatalyst] = []
    for entry in root.findall(f"{ATOM_NS}entry"):
        title = (entry.findtext(f"{ATOM_NS}title") or "").strip()
        link_el = entry.find(f"{ATOM_NS}link")
        url = link_el.get("href") if link_el is not None else ""
        updated = (entry.findtext(f"{ATOM_NS}updated") or "").strip()
        id_text = (entry.findtext(f"{ATOM_NS}id") or "").strip()

        m_acc = _ACC_RE.search(id_text)
        acc = m_acc.group(1) if m_acc else id_text or url

        form_type = _extract_form(title)

        try:
            dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            published = dt.astimezone(timezone.utc).isoformat(timespec="seconds")
        except Exception:
            published = updated

        out.append(RawCatalyst(
            ticker=ticker,
            source="edgar",
            source_id=f"edgar:{ticker}:{acc}",
            form_type=form_type,
            headline=title,
            url=url,
            published_at=published,
        ))
    return out


def fetch(tickers: Iterable[str], since_hours: int = 2) -> list[RawCatalyst]:
    """Fetch recent filings per ticker. `since_hours` filters by published_at."""
    cutoff = datetime.now(timezone.utc).timestamp() - since_hours * 3600
    headers = {"User-Agent": _ua(), "Accept": "application/atom+xml"}
    out: list[RawCatalyst] = []
    for t in tickers:
        try:
            r = requests.get(FEED_URL.format(ticker=t), headers=headers, timeout=10)
            r.raise_for_status()
            entries = _parse_atom(r.content, t)
            for e in entries:
                try:
                    ts = datetime.fromisoformat(
                        e.published_at.replace("Z", "+00:00")
                    ).timestamp()
                except Exception:
                    continue  # unparseable timestamp → drop, don't bias toward noise
                if ts >= cutoff:
                    out.append(e)
        except Exception as ex:  # network hiccup, 403, etc. — skip this ticker
            print(f"[edgar] {t}: {ex}")
        time.sleep(0.1)
    return out
