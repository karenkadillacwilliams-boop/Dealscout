"""CSV → canonical trade rows via saved column-mapping profiles."""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


_REQUIRED_FIELDS = ("ticker", "side", "qty", "price", "trade_date")

_PAREN_NEG_RE = re.compile(r"^\([\$]?([\d,.]+)\)$")
_MONEY_RE = re.compile(r"[$,\s]")


@dataclass
class ImportRow:
    ticker: str
    side: str
    qty: float
    price: float
    trade_date: str
    raw: dict[str, Any]  # original CSV cells, for display/debug

    def __repr__(self) -> str:
        return (f"ImportRow(ticker={self.ticker!r}, side={self.side!r}, "
                f"qty={self.qty}, price={self.price}, "
                f"trade_date={self.trade_date!r}, raw=<{len(self.raw)} fields>)")


@dataclass
class RejectedRow:
    raw: dict[str, Any]
    reason: str

    def __repr__(self) -> str:
        return f"RejectedRow(reason={self.reason!r}, raw=<{len(self.raw)} fields>)"


@dataclass
class ImportResult:
    valid: list[ImportRow]
    rejected: list[RejectedRow]
    skipped: int  # count dropped by row_filter without being "rejected" (e.g. dividend lines)


# --- Normalization primitives ---

def normalize_ticker(value: Any) -> str:
    s = str(value or "").strip().upper()
    if not s:
        raise ValueError("empty ticker")
    return s


def normalize_qty(value: Any) -> float:
    if value is None or value == "":
        raise ValueError("empty qty")
    stripped = _MONEY_RE.sub("", str(value))
    try:
        q = abs(float(stripped))
    except ValueError as exc:
        raise ValueError(f"qty not numeric: {value!r}") from exc
    if q <= 0:
        raise ValueError(f"qty must be > 0: {value!r}")
    return q


def normalize_price(value: Any) -> float:
    if value is None or value == "":
        raise ValueError("empty price")
    s = str(value).strip()
    m = _PAREN_NEG_RE.match(s)
    if m:
        s = _MONEY_RE.sub("", m.group(1))  # strip commas ("1,234.56" → "1234.56")
    else:
        s = _MONEY_RE.sub("", s)
        if s.startswith("-"):
            raise ValueError(f"price must be >= 0: {value!r}")
    try:
        p = float(s)
    except ValueError as exc:
        raise ValueError(f"price not numeric: {value!r}") from exc
    if p < 0:
        raise ValueError(f"price must be >= 0: {value!r}")
    return p


def parse_date(value: Any) -> str:
    """Accept YYYY-MM-DD and MM/DD/YYYY (US format) → ISO date; reject future dates.

    European DD/MM/YYYY is not auto-detected — use an explicit profile hint
    if support is needed later (not required for the 5 seed brokers, all US).
    """
    if value is None or value == "":
        raise ValueError("empty date")
    s = str(value).strip()
    # Strip time portion if present (MooMoo: "2026-04-10 09:45:00")
    s = s.split(" ")[0].split("T")[0]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            d = datetime.strptime(s, fmt).date()
            break
        except ValueError:
            continue
    else:
        raise ValueError(f"unparseable date: {value!r}")
    if d > date.today():
        raise ValueError(f"trade_date is in the future: {value!r}")
    return d.isoformat()


# --- Profile application ---

def apply_profile(csv_text: str, profile: dict) -> ImportResult:
    """Parse CSV and normalize each row per `profile` (column_map / value_map / row_filter).

    Raises ValueError if a required column in `column_map` is absent from the
    CSV header (the whole file is unusable).

    Per-row errors go into `rejected`. Rows dropped by row_filter go into
    `skipped` count only (they're expected non-trade rows like dividends).
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    header = reader.fieldnames or []

    column_map = profile["column_map"]
    value_map = profile.get("value_map", {}) or {}
    row_filter = profile.get("row_filter", {}) or {}

    for canonical, source in column_map.items():
        if source not in header:
            raise ValueError(f"CSV missing expected column {source!r} "
                              f"(mapped from canonical {canonical!r})")

    side_map = value_map.get("side", {}) or {}
    skip_not_in_side_map = bool(row_filter.get("skip_if_side_not_in_value_map"))
    skip_action_contains = [s.upper() for s in
                             row_filter.get("skip_if_action_contains", []) or []]

    valid: list[ImportRow] = []
    rejected: list[RejectedRow] = []
    skipped = 0

    for raw in reader:
        # row_filter: action-contains short-circuit (e.g. "DIV", "INT", "TRANSFER")
        if skip_action_contains:
            action_cell = raw.get(column_map["side"], "") or ""
            upper = action_cell.upper()
            if any(tok in upper for tok in skip_action_contains):
                skipped += 1
                continue

        try:
            raw_side = raw.get(column_map["side"], "") or ""
            if side_map:
                if raw_side in side_map:
                    side = side_map[raw_side]
                elif skip_not_in_side_map:
                    skipped += 1
                    continue
                else:
                    raise ValueError(f"side value not in value_map: {raw_side!r}")
            else:
                side = raw_side.strip().upper()
                if side not in ("BUY", "SELL"):
                    raise ValueError(f"side must be BUY or SELL, got: {raw_side!r}")

            row = ImportRow(
                ticker=normalize_ticker(raw.get(column_map["ticker"])),
                side=side,
                qty=normalize_qty(raw.get(column_map["qty"])),
                price=normalize_price(raw.get(column_map["price"])),
                trade_date=parse_date(raw.get(column_map["trade_date"])),
                raw=raw,
            )
            valid.append(row)
        except ValueError as exc:
            rejected.append(RejectedRow(raw=raw, reason=str(exc)))

    return ImportResult(valid=valid, rejected=rejected, skipped=skipped)


def commit_to_db(conn, account_id: int, rows: list[ImportRow],
                 profile_id: int | None, filename: str,
                 rejected_count: int = 0) -> dict:
    """Insert rows into trades table with dedup + record an import_batch.
    Returns {batch_id, inserted, duplicates}."""
    from catalysts import db as cdb
    inserted = 0
    duplicates = 0
    # Create the batch first so we can stamp it on trades
    batch_id = cdb.create_import_batch(
        conn, account_id=account_id, profile_id=profile_id,
        filename=filename, row_count=len(rows) + rejected_count,
        inserted=0, duplicates=0, rejected=rejected_count,
    )
    try:
        for r in rows:
            _tid, was_new = cdb.insert_trade(
                conn, account_id=account_id, ticker=r.ticker, side=r.side,
                qty=r.qty, price=r.price, trade_date=r.trade_date,
                import_batch_id=batch_id,
            )
            if was_new:
                inserted += 1
            else:
                duplicates += 1
    finally:
        # Always update the batch row so the audit trail matches reality,
        # even if the loop raised mid-way.
        conn.execute(
            "UPDATE import_batches SET inserted=?, duplicates=? WHERE id=?",
            (inserted, duplicates, batch_id),
        )
        conn.commit()
    return {"batch_id": batch_id, "inserted": inserted, "duplicates": duplicates}
