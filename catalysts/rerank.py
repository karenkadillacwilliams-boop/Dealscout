"""Claude Haiku 4.5 re-ranker.

Pass 2 of the scoring pipeline. Pure function above the `_call_claude`
boundary, which is stubbed in tests. Enforces a daily call cap via the
MAX_RERANK_CALLS_PER_DAY env var (persisted in a small counter file so
it survives process restarts within the same UTC day).
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from catalysts.types import RerankedItem, ScoredItem

_COUNTER_FILE = Path(__file__).resolve().parent.parent / ".rerank_counter.json"
_MODEL = "claude-haiku-4-5"
_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)

_SYSTEM = (
    "You are an M&A and catalyst analyst. For each headline, rate 0-10 how "
    "likely it signals a near-term material event (M&A, activist stake, major "
    "partnership, tech/product launch with revenue impact). Ignore routine "
    "press. Rationale must be <= 25 words. Output JSON only."
)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_counter() -> tuple[str, int]:
    try:
        data = json.loads(_COUNTER_FILE.read_text())
        return data.get("day", ""), int(data.get("calls", 0))
    except Exception:
        return "", 0


def _save_counter(day: str, calls: int) -> None:
    _COUNTER_FILE.write_text(json.dumps({"day": day, "calls": calls}))


def _cap() -> int:
    try:
        return int(os.environ.get("MAX_RERANK_CALLS_PER_DAY", "200"))
    except ValueError:
        return 200


def _fuse(kw: int, llm: int | None) -> int:
    if llm is None:
        return kw
    return round(0.6 * kw + 0.4 * (llm * 10))


def _kw_only(s: ScoredItem) -> RerankedItem:
    return RerankedItem(scored=s, llm_score=None, rationale=None, final_score=s.kw_score)


def _call_claude(batch: list[tuple[int, ScoredItem]]) -> list[dict]:
    """Real Anthropic call. Returns list of {id, score, rationale, tags}."""
    from anthropic import Anthropic
    client = Anthropic()
    user = json.dumps([
        {"id": i, "ticker": s.raw.ticker, "headline": s.raw.headline,
         "source": s.raw.source, "form_type": s.raw.form_type}
        for i, s in batch
    ])
    resp = client.messages.create(
        model=_MODEL,
        max_tokens=1024,
        system=_SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    text = resp.content[0].text
    # Extract the first [...] block — tolerates code fences, leading text,
    # trailing commentary, and ```json wrappers without fragile char strips.
    m = _JSON_ARRAY_RE.search(text)
    if not m:
        raise ValueError(f"no JSON array in model output: {text[:200]!r}")
    return json.loads(m.group(0))


def rerank_batched(items: Iterable[ScoredItem], batch: int = 10) -> list[RerankedItem]:
    items = list(items)
    if not items:
        return []

    day, calls = _load_counter()
    if day != _today():
        day, calls = _today(), 0

    cap = _cap()
    out: list[RerankedItem] = []
    i = 0
    while i < len(items):
        chunk = items[i : i + batch]
        if calls >= cap:
            out.extend(_kw_only(s) for s in chunk)
            i += batch
            continue
        indexed = list(enumerate(chunk))
        try:
            results = _call_claude(indexed)
            calls += 1
            by_id = {r["id"]: r for r in results}
        except Exception as ex:
            print(f"[rerank] call failed: {ex}")
            out.extend(_kw_only(s) for s in chunk)
            i += batch
            continue

        for idx, s in indexed:
            r = by_id.get(idx)
            if not r:
                out.append(_kw_only(s))
                continue
            llm_score = int(r.get("score", 0))
            rationale = (r.get("rationale") or "")[:200]
            out.append(RerankedItem(
                scored=s,
                llm_score=llm_score,
                rationale=rationale,
                final_score=_fuse(s.kw_score, llm_score),
            ))
        i += batch

    _save_counter(day, calls)
    return out
