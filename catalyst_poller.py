"""Catalyst Radar poller — run by Windows Task Scheduler every 15 min."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

from catalysts import db as cdb
from catalysts import edgar, news, score, rerank
from catalysts.dedup import filter_unseen
from catalysts.types import RawCatalyst, ScoredItem, RerankedItem


def _to_reranked_kw_only(s: ScoredItem) -> RerankedItem:
    return RerankedItem(
        scored=s, llm_score=None, rationale=None, final_score=s.kw_score,
    )


def run_once(dry_run: bool = False) -> int:
    load_dotenv()
    conn = cdb.connect()
    cdb.migrate(conn)

    tickers = cdb.load_active_universe(conn)
    if not tickers:
        print("[poller] no active tickers, nothing to do")
        return 0

    raw: list[RawCatalyst] = []
    raw += edgar.fetch(tickers, since_hours=2)
    raw += news.fetch_yfinance(tickers)
    raw += news.fetch_gnews_rss(tickers)
    print(f"[poller] fetched {len(raw)} raw items")

    fresh = filter_unseen(conn, raw)
    print(f"[poller] {len(fresh)} new after dedup")

    scored = [score.score_item(r) for r in fresh]
    rerank_pool = [s for s in scored if s.kw_score >= 20]
    reranked_map = {id(s): r for s, r in zip(rerank_pool,
                    rerank.rerank_batched(rerank_pool, batch=10))}
    reranked: list[RerankedItem] = []
    for s in scored:
        r = reranked_map.get(id(s))
        if r is not None:
            reranked.append(r)
        else:
            reranked.append(_to_reranked_kw_only(s))

    if dry_run:
        print(json.dumps([
            {"ticker": i.ticker, "score": i.final_score,
             "tags": list(i.tags), "headline": i.headline}
            for i in reranked
        ], indent=2))
        return 0

    persisted = 0
    for item in reranked:
        cdb.persist_catalyst(conn, item)
        persisted += 1
    print(f"[poller] persisted {persisted} catalysts")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return run_once(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
