"""One-shot copy of dealscout.db -> Turso, using the HTTP /v2/pipeline API.

This avoids the libsql native dependency (no Windows wheels) by talking to
Turso over HTTPS with the auth token. The runtime app and poller still use
libsql on Linux (Streamlit Cloud, GitHub Actions) where wheels exist; this
script is only for the one-time data load.

Usage:
    set TURSO_DATABASE_URL=libsql://<your-db>.turso.io
    set TURSO_AUTH_TOKEN=<jwt>
    python scripts/migrate_to_turso.py [--source dealscout.db] [--batch 100]

Re-runnable: schema is applied first, then data is copied via INSERT OR IGNORE
so partial runs don't double-insert.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Match catalyst_poller.py: shared secrets (across projects) first, then
# project-local .env can override.
load_dotenv(Path.home() / ".secrets" / "shared.env")
load_dotenv(ROOT / ".env", override=True)

from catalysts.db import SCHEMA  # noqa: E402


def _http_url(database_url: str) -> str:
    """libsql://foo.turso.io -> https://foo.turso.io/v2/pipeline."""
    if database_url.startswith("libsql://"):
        host = database_url[len("libsql://"):]
    elif database_url.startswith("https://"):
        host = database_url[len("https://"):]
    else:
        raise ValueError(f"unrecognised TURSO_DATABASE_URL: {database_url!r}")
    return "https://" + host.rstrip("/") + "/v2/pipeline"


def _post_pipeline(endpoint: str, token: str, statements: list[str]) -> None:
    """POST a batch of SQL strings as one Hrana pipeline request."""
    pipeline = [{"type": "execute", "stmt": {"sql": s}} for s in statements]
    pipeline.append({"type": "close"})

    resp = requests.post(
        endpoint,
        json={"requests": pipeline},
        headers={"Authorization": "Bearer " + token},
        timeout=60,
    )
    resp.raise_for_status()
    body = resp.json()

    for i, result in enumerate(body.get("results", [])):
        if result.get("type") == "error":
            raise RuntimeError(
                f"Turso rejected statement #{i}: {result.get('error')}\n"
                f"SQL: {statements[i][:200]}"
            )


def _seed_profiles_inline() -> list[str]:
    """Replay portfolios.profiles seed without importing it (avoids pulling in
    the rest of the app at migration time). Generated lazily via iterdump on
    a throwaway in-memory DB."""
    from portfolios.profiles import seed_builtin_profiles
    mem = sqlite3.connect(":memory:")
    mem.executescript(SCHEMA)
    seed_builtin_profiles(mem)
    seeds = [
        s.replace("INSERT INTO", "INSERT OR IGNORE INTO", 1)
        for s in mem.iterdump()
        if s.startswith("INSERT INTO") and "import_profiles" in s
    ]
    mem.close()
    return seeds


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(ROOT / "dealscout.db"))
    ap.add_argument("--batch", type=int, default=100,
                    help="statements per HTTP request")
    args = ap.parse_args()

    database_url = os.environ.get("TURSO_DATABASE_URL")
    token = os.environ.get("TURSO_AUTH_TOKEN")
    if not (database_url and token):
        print("ERROR: TURSO_DATABASE_URL and TURSO_AUTH_TOKEN must be set.",
              file=sys.stderr)
        return 2

    src_path = Path(args.source)
    if not src_path.exists():
        print(f"ERROR: source DB not found: {src_path}", file=sys.stderr)
        return 2

    endpoint = _http_url(database_url)
    print(f"source:   {src_path}")
    print(f"endpoint: {endpoint}")

    src_uri = "file:" + str(src_path).replace("\\", "/") + "?mode=ro"
    src = sqlite3.connect(src_uri, uri=True)
    src.row_factory = sqlite3.Row

    print("applying schema...")
    schema_stmts = [
        s.strip() for s in SCHEMA.split(";") if s.strip()
    ]
    _post_pipeline(endpoint, token, schema_stmts)

    print("seeding built-in profiles...")
    seeds = _seed_profiles_inline()
    if seeds:
        _post_pipeline(endpoint, token, seeds)

    # iterdump emits tables alphabetically, so child tables (alert_log) come
    # before parents (catalysts). FK is per-connection, so disabling it here
    # has no effect on the runtime app, which sets PRAGMA foreign_keys=ON in
    # cdb.connect() for every session.
    fk_off = ["PRAGMA foreign_keys=OFF"]

    print("copying rows...")
    batch: list[str] = list(fk_off)
    total = 0
    for stmt in src.iterdump():
        if not stmt.startswith("INSERT INTO"):
            continue
        if "sqlite_sequence" in stmt:
            continue
        batch.append(stmt.replace("INSERT INTO", "INSERT OR IGNORE INTO", 1))
        # +1 in the threshold accounts for the leading PRAGMA in each batch
        if len(batch) >= args.batch + 1:
            _post_pipeline(endpoint, token, batch)
            total += len(batch) - 1
            print(f"  pushed {total} rows")
            batch = list(fk_off)
    if len(batch) > 1:
        _post_pipeline(endpoint, token, batch)
        total += len(batch) - 1

    print(f"done. {total} rows pushed to Turso.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
