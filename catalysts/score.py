"""Keyword-based first-pass scorer.
Pure function — no I/O, no state, import-time regex compilation."""
from __future__ import annotations

import re
from typing import Iterable

from catalysts.types import RawCatalyst, ScoredItem

# (phrase, weight, tag)
_DICT: list[tuple[str, int, str]] = [
    # M&A — confirmed
    ("definitive agreement",   35, "m&a-confirmed"),
    ("to acquire",             35, "m&a-confirmed"),
    ("agrees to acquire",      35, "m&a-confirmed"),
    ("agreed to acquire",      35, "m&a-confirmed"),
    ("merger agreement",       35, "m&a-confirmed"),
    ("tender offer",           35, "m&a-confirmed"),
    # M&A — rumored
    ("in talks to",                    25, "m&a-rumor"),
    ("exploring sale",                 25, "m&a-rumor"),
    ("exploring strategic alternatives", 25, "m&a-rumor"),
    ("weighing bid",                   25, "m&a-rumor"),
    ("approached about",               25, "m&a-rumor"),
    ("considering offer",              25, "m&a-rumor"),
    # Activist
    ("13D filed",        20, "activist"),
    ("activist stake",   20, "activist"),
    ("nominates directors", 20, "activist"),
    ("urges board",      20, "activist"),
    # Partnership
    ("strategic partnership",  15, "partnership"),
    ("collaboration agreement",15, "partnership"),
    ("joint venture",          15, "partnership"),
    ("licensing deal",         15, "partnership"),
    # Product / tech
    ("launches",       10, "product"),
    ("unveils",        10, "product"),
    ("first-in-class", 10, "product"),
    ("fda approval",   10, "product"),
    ("design win",     10, "product"),
    # Negative modifiers
    ("denies",           -15, "weak"),
    ("not in talks",     -15, "weak"),
    ("speculation only", -15, "weak"),
]

# Hostile-headline safety: cap phrase length, compile once with re.IGNORECASE.
MAX_PHRASE_LEN = 64
for _p, _w, _t in _DICT:
    assert len(_p) <= MAX_PHRASE_LEN, f"phrase too long: {_p}"

_COMPILED: list[tuple[re.Pattern, int, str, str]] = [
    (re.compile(r"\b" + re.escape(p) + r"\b", re.IGNORECASE), w, t, p)
    for p, w, t in _DICT
]

_FILING_FORM_PREFIXES: tuple[str, ...] = (
    "8-K", "SCHEDULE 13D", "SCHEDULE 13G", "425",
    "SC TO-T", "SCHEDULE TO-T", "S-4", "DEFM14A",
)
_FILING_BONUS = 20


def _is_high_signal_form(form_type: str | None) -> bool:
    if not form_type:
        return False
    return any(form_type.startswith(p) for p in _FILING_FORM_PREFIXES)


def score_item(
    raw: RawCatalyst,
    tag_multipliers: dict[str, float] | None = None,
) -> ScoredItem:
    """Score a raw catalyst; `tag_multipliers` is the learned {tag: multiplier}
    produced by weight_learner. When None, behavior is unchanged."""
    text = raw.headline
    tags: list[str] = []
    matched: list[str] = []
    total = 0.0

    mults = tag_multipliers or {}

    for pattern, weight, tag, phrase in _COMPILED:
        if pattern.search(text):
            total += weight * mults.get(tag, 1.0)
            matched.append(phrase)
            if tag not in tags:
                tags.append(tag)

    # Form-type-based auto-tagging for filings whose titles never contain the
    # activist phrases. EDGAR labels: "SCHEDULE 13D", "SCHEDULE 13G", with "/A"
    # suffix for amendments (passive position adjustments — discount).
    ft = raw.form_type or ""
    if ft.startswith("SCHEDULE 13D"):
        base = 15 if ft.endswith("/A") else 25
        total += base * mults.get("activist", 1.0)
        if "activist" not in tags:
            tags.append("activist")
    elif ft.startswith("SCHEDULE 13G"):
        base = 5 if ft.endswith("/A") else 15
        total += base * mults.get("activist", 1.0)
        if "activist" not in tags:
            tags.append("activist")

    # Strong M&A signal in an M&A-relevant form → filing bonus and tag.
    has_ma_tag = any(t.startswith("m&a") or t == "activist" for t in tags)
    if _is_high_signal_form(raw.form_type) and has_ma_tag:
        total += _FILING_BONUS * mults.get("filing", 1.0)
        if "filing" not in tags:
            tags.append("filing")

    kw = max(0, min(100, int(round(total))))
    return ScoredItem(
        raw=raw,
        kw_score=kw,
        tags=tuple(tags),
        matched_phrases=tuple(matched),
    )
