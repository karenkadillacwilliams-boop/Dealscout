"""Catalyst Radar poller — run by Windows Task Scheduler every 15 min."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

from catalysts import db as cdb
from catalysts import edgar, news, score, rerank
from catalysts.dedup import filter_unseen, recently_alerted
from catalysts.types import RawCatalyst, ScoredItem, RerankedItem
from alerts import dispatcher


def _to_reranked_kw_only(s: ScoredItem) -> RerankedItem:
    return RerankedItem(
        scored=s, llm_score=None, rationale=None, final_score=s.kw_score,
    )


def run_once(dry_run: bool = False, force_alert: bool = False) -> int:
    load_dotenv()
    conn = cdb.connect(cdb.DB_PATH)
    cdb.migrate(conn)

    tickers = cdb.load_active_universe(conn)
    if not tickers:
        print("[poller] no active tickers")
        return 0

    raw: list[RawCatalyst] = []
    raw += edgar.fetch(tickers, since_hours=2)
    raw += news.fetch_yfinance(tickers)
    raw += news.fetch_gnews_rss(tickers)
    print(f"[poller] fetched {len(raw)} raw items")

    fresh = filter_unseen(conn, raw)
    print(f"[poller] {len(fresh)} new after dedup")

    scored = [score.score_item(r) for r in fresh]
    pool = [s for s in scored if s.kw_score >= 20]
    rr_map = {id(s): r for s, r in zip(pool, rerank.rerank_batched(pool, batch=10))}
    reranked = [rr_map.get(id(s)) or _to_reranked_kw_only(s) for s in scored]

    if dry_run:
        print(json.dumps([
            {"ticker": i.ticker, "score": i.final_score, "tags": list(i.tags),
             "headline": i.headline, "rationale": i.rationale}
            for i in reranked
        ], indent=2))
        return 0

    alerts_sent = 0
    for item in reranked:
        cid = cdb.persist_catalyst(conn, item)
        should_alert = force_alert or (
            item.final_score >= 70 and item.llm_score is not None
        )
        if not should_alert:
            continue
        bucket = item.final_score // 10
        if recently_alerted(conn, item.ticker, bucket, hours=6):
            continue
        ok, channels = dispatcher.send(item)
        sent_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO alert_log(catalyst_id,ticker,score_bucket,channels,sent_at,ok) "
            "VALUES(?,?,?,?,?,?)",
            (cid, item.ticker, bucket, json.dumps(channels), sent_at, 1 if ok else 0),
        )
        conn.commit()
        alerts_sent += 1

    print(f"[poller] persisted {len(reranked)} catalysts, {alerts_sent} alerts sent")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force-alert", action="store_true")
    args = ap.parse_args()
    return run_once(dry_run=args.dry_run, force_alert=args.force_alert)


if __name__ == "__main__":
    sys.exit(main())
