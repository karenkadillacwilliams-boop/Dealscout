# catalysts/types.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

@dataclass(frozen=True)
class RawCatalyst:
    ticker: str
    source: str            # 'edgar' | 'yfinance' | 'gnews'
    source_id: str         # accession number, url, or guid
    headline: str
    url: str
    published_at: str      # ISO8601 UTC
    form_type: Optional[str] = None

@dataclass(frozen=True)
class ScoredItem:
    raw: RawCatalyst
    kw_score: int          # 0..100
    tags: tuple[str, ...]
    matched_phrases: tuple[str, ...]

@dataclass(frozen=True)
class RerankedItem:
    scored: ScoredItem
    llm_score: Optional[int]   # 0..10, None if skipped
    rationale: Optional[str]   # <= 25 words
    final_score: int           # 0..100

    # convenience passthroughs used by the alert dispatcher
    @property
    def ticker(self) -> str: return self.scored.raw.ticker
    @property
    def headline(self) -> str: return self.scored.raw.headline
    @property
    def url(self) -> str: return self.scored.raw.url
    @property
    def source(self) -> str: return self.scored.raw.source
    @property
    def published_at(self) -> str: return self.scored.raw.published_at
    @property
    def tags(self) -> tuple[str, ...]: return self.scored.tags
    @property
    def kw_score(self) -> int: return self.scored.kw_score
