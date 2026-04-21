"""Catalyst Radar poller — run by Windows Task Scheduler every 15 min."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

_SHARED_ENV = Path.home() / ".secrets" / "shared.env"

from catalysts import db as cdb
from catalysts import edgar, news, score, rerank, options
from catalysts.dedup import filter_unseen, recently_alerted
from catalysts.iv_rank import compute_atm_avg_iv, compute_iv_rank
from catalysts.market_status import is_market_open
from catalysts.options_score import rank_contracts
from catalysts.related import fetch_related
from catalysts.types import RawCatalyst, ScoredItem, RerankedItem
from alerts import dispatcher

log = logging.getLogger("poller")


def _to_reranked_kw_only(s: ScoredItem) -> RerankedItem:
    return RerankedItem(
        scored=s, llm_score=None, rationale=None, final_score=s.kw_score,
    )


def _fetch_options(conn, tickers: list[str]) -> dict[str, str]:
    if not os.environ.get("POLYGON_API_KEY"):
        return {}

    # Polygon's /v3/snapshot/options returns IV+greeks 24/7 but last_quote,
    # day, and last_trade are all null outside regular market hours +
    # extended-hours — so ask/bid are unusable off-hours. We skip fetching
    # entirely; the most recent in-hours snapshot in options_snapshot stays
    # in the DB (clear_stale_options only runs when we actually fetch).
    if not is_market_open():
        log.info("market closed, skipping options fetch")
        return {}

    now_str = datetime.now(timezone.utc).isoformat(timespec="seconds")
    summaries: dict[str, str] = {}

    cdb.clear_stale_options(conn)

    all_contracts = options.fetch_chains_batch(tickers)
    if not all_contracts:
        return {}

    from catalysts.uoa import detect_unusual
    uoa_signals = detect_unusual(all_contracts)
    for sig in uoa_signals:
        cdb.insert_uoa_signal(conn, sig)
    if uoa_signals:
        log.info("detected %d UOA signals", len(uoa_signals))

    by_ticker: dict[str, list] = {}
    for c in all_contracts:
        by_ticker.setdefault(c.ticker, []).append(c)

    for ticker, contracts in by_ticker.items():
        underlying = contracts[0].underlying_price if contracts else 0.0
        avg_iv = compute_atm_avg_iv(contracts, underlying)
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        iv_rank_val = 50.0
        if avg_iv is not None:
            cdb.upsert_iv_history(conn, ticker, today_str, avg_iv)
            history_rows = conn.execute(
                "SELECT avg_iv FROM iv_history WHERE ticker=? ORDER BY date",
                (ticker,),
            ).fetchall()
            history = [r["avg_iv"] for r in history_rows]
            iv_rank_val = compute_iv_rank(avg_iv, history)

        cat_row = conn.execute(
            "SELECT MAX(final_score) AS best FROM catalysts "
            "WHERE ticker=? AND datetime(fetched_at) >= datetime('now', '-24 hours')",
            (ticker,),
        ).fetchone()
        catalyst_score = cat_row["best"] if cat_row and cat_row["best"] else 0

        ranked = rank_contracts(contracts, catalyst_score=catalyst_score, iv_rank=iv_rank_val)
        for row in ranked:
            row["fetched_at"] = now_str
            cdb.upsert_option_snapshot(conn, row)

        if ranked:
            best = ranked[0]
            exp_short = best["expiration_date"][5:]  # MM-DD
            ct = "C" if best["contract_type"] == "call" else "P"
            n_calls = sum(1 for r in ranked if r["contract_type"] == "call")
            n_puts = sum(1 for r in ranked if r["contract_type"] == "put")
            parts = []
            if n_calls:
                parts.append(f"{n_calls} call{'s' if n_calls != 1 else ''}")
            if n_puts:
                parts.append(f"{n_puts} put{'s' if n_puts != 1 else ''}")
            summaries[ticker] = (
                f"Options: {' + '.join(parts)} under $2 | "
                f"best: {exp_short} ${best['strike']}{ct} @ ${best['ask']:.2f} "
                f"(leverage {best['leverage_ratio']:.1f}x, IV rank {iv_rank_val:.0f}%)"
            )

    cdb.prune_iv_history(conn)
    return summaries


def run_once(dry_run: bool = False, force_alert: bool = False) -> int:
    if _SHARED_ENV.exists():
        load_dotenv(_SHARED_ENV)
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
    raw += news.fetch_polygon_news(tickers)
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

    alert_tickers = set()
    persisted: list[tuple[RerankedItem, int]] = []
    for item in reranked:
        cid = cdb.persist_catalyst(conn, item)
        persisted.append((item, cid))
        if item.final_score >= 70 and item.llm_score is not None:
            alert_tickers.add(item.ticker)

    # Backfill IV history for new tickers
    if os.environ.get("POLYGON_API_KEY"):
        from catalysts.iv_rank import backfill_batch
        try:
            n_filled = backfill_batch(tickers, conn)
            if n_filled:
                print(f"[poller] backfilled IV history for {n_filled} tickers")
        except Exception as exc:
            print(f"[poller] IV backfill failed: {exc}")

    options_summaries = _fetch_options(conn, tickers)
    print(f"[poller] options summaries for {len(options_summaries)} tickers")

    # Technical confluence scoring
    if os.environ.get("POLYGON_API_KEY"):
        from catalysts.technicals import fetch_technicals_batch
        try:
            tech_map = fetch_technicals_batch(tickers, prices=None)
            for t, sig in tech_map.items():
                cdb.upsert_technical(conn, t, sig.rsi, sig.macd_histogram,
                                     sig.price_vs_sma50, sig.label, sig.score)
            print(f"[poller] technicals updated for {len(tech_map)} tickers")
        except Exception as exc:
            print(f"[poller] technicals failed: {exc}")

    # Triple-play (post-earnings fundamental momentum) — Finnhub + yfinance
    if os.environ.get("FINNHUB_API_KEY"):
        from catalysts.earnings import get_earnings_data
        from catalysts.triple_play import score_triple_play
        try:
            fresh = cdb.load_triple_play_fresh(conn, max_age_hours=24)
            stale_tickers = [t for t in tickers if t not in fresh]
            tp_count = 0
            for t in stale_tickers:
                data = get_earnings_data(t)
                score_tp = score_triple_play(data)
                cdb.upsert_triple_play(
                    conn, ticker=t, score=score_tp.score,
                    eps=score_tp.eps_component, revenue=score_tp.revenue_component,
                    guidance_delta=score_tp.guidance_component,
                    days=score_tp.days_since_report,
                    is_full=score_tp.is_full_triple_play,
                    report_period=score_tp.report_period,
                )
                tp_count += 1
            if tp_count:
                print(f"[poller] triple-play scored {tp_count} tickers ({len(fresh)} fresh)")
        except Exception as exc:
            print(f"[poller] triple-play failed: {exc}")

    # Position event detection (portfolios)
    if os.environ.get("POLYGON_API_KEY"):
        try:
            from portfolios.events import detect_events_for_all_accounts
            n_events = detect_events_for_all_accounts(conn)
            if n_events:
                print(f"[poller] detected {n_events} position events (pending review)")
        except Exception as exc:
            print(f"[poller] event detection failed: {exc}")

    alerts_sent = 0
    for item, cid in persisted:
        should_alert = force_alert or (
            item.final_score >= 70 and item.llm_score is not None
        )
        if not should_alert:
            continue
        bucket = item.final_score // 10
        if recently_alerted(conn, item.ticker, bucket, hours=6):
            continue
        summary = options_summaries.get(item.ticker)
        related = fetch_related(item.ticker, limit=5, conn=conn)
        related = [r for r in related if r in set(tickers)]
        related_str = f"Related: {', '.join(related)}" if related else None
        ok, channels = dispatcher.send(item, options_summary=summary, related_tickers=related_str)
        sent_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO alert_log(catalyst_id,ticker,score_bucket,channels,sent_at,ok) "
            "VALUES(?,?,?,?,?,?)",
            (cid, item.ticker, bucket, json.dumps(channels), sent_at, 1 if ok else 0),
        )
        conn.commit()
        alerts_sent += 1

    # Aggregate visibility — approx Polygon calls per run (for rate-budget tuning)
    approx_polygon_calls = (
        len(tickers)                                         # options chains (1/ticker)
        + len(tickers) * 3                                   # technicals (3/ticker)
        + (n_filled if 'n_filled' in dir() else 0)           # iv backfill
        + (n_events if 'n_events' in dir() else 0)           # detector bars (~1/unique held ticker)
    )
    print(f"[poller] approx polygon calls this run: {approx_polygon_calls}")
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
