#!/usr/bin/env python3
"""One-shot repair for malformed-JSON blobs left by the char-slice footgun.

BL-20260618-CLOSEDFLAT-MALFORMED-JSON. Before the write-side was migrated to
``dump_capped`` (and the char-slice sites in ``order_monitor.py`` were removed),
``json.dumps(payload)[:N]`` could persist **invalid JSON** into
``trades.notes`` and ``order_packages.{signal_logic,meta}``. One such row made
``closed_flat_invariant``'s ``json_extract(notes,'$.closed_at')`` raise
"malformed JSON", aborting the whole invariant query.

This script finds every row whose target column is present but
``json_valid(col)=0`` and rewrites it into a **valid** JSON envelope that:

  * best-effort salvages a small set of load-bearing keys still textually
    readable in the truncated blob (``closed_at`` / ``closed_by`` /
    ``closed_reason`` / ``pnl_source`` / ``exit_price_source`` / ``trade_id``
    for notes) via a tolerant regex — so ``closed_flat_invariant`` can read
    ``closed_at`` again where it survived the cut; and
  * preserves the raw original under ``_original_truncated`` for forensics; and
  * marks the row ``_repaired_at`` / ``_repair_reason``.

The result is guaranteed valid + length-bounded via ``dump_capped``. **Idempotent
by construction**: a repaired row has ``json_valid=1``, so a re-run never
re-touches it. **Dry-run by default** — prints the per-table count and a sample;
pass ``--apply`` to write (Tier-2, a DB writeback).

Usage::

    python scripts/ops/repair_malformed_notes.py            # dry-run (counts only)
    python scripts/ops/repair_malformed_notes.py --apply    # perform the repair
    python scripts/ops/repair_malformed_notes.py --db /path/to/trade_journal.db
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

# scripts/ops/repair_malformed_notes.py → repo root is parents[2]
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.utils.json_notes import dump_capped, sanitize_nonfinite  # noqa: E402

# (table, column, cap) — the blobs the char-slice footgun could truncate.
_TARGETS: Tuple[Tuple[str, str, int], ...] = (
    ("trades", "notes", 2000),
    ("order_packages", "signal_logic", 4000),
    ("order_packages", "meta", 2000),
)

# Keys worth salvaging from a truncated notes blob (front-of-blob, usually intact).
_SALVAGE_KEYS = (
    "closed_at", "closed_by", "closed_reason", "pnl_source",
    "exit_price_source", "trade_id", "adopted_at", "adopted_by",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _salvage(raw: str) -> Dict[str, Any]:
    """Best-effort extract of intact ``"key": "value"`` pairs from a truncated blob."""
    out: Dict[str, Any] = {}
    for key in _SALVAGE_KEYS:
        # Only a COMPLETE "key": "value" (closing quote present) — a value cut
        # mid-string is skipped rather than guessed.
        m = re.search(rf'"{re.escape(key)}"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
        if m:
            out[key] = m.group(1)
    return out


def _classify(raw: str) -> str:
    """Why is ``raw`` ``json_valid=0``? Drives the repair strategy.

    * ``"nonfinite"`` — COMPLETE JSON that is invalid ONLY because it carries a
      non-finite float token (``NaN`` / ``Infinity`` / ``-Infinity``).
      ``json.loads`` accepts those tokens by default, so a complete-but-non-
      finite blob parses cleanly here while a genuinely-truncated one raises.
      This is the dominant BL-20260709 case (a ``std_dev`` / z-score with a
      zero denominator serialized by the old ``json.dumps`` default) and it is
      **losslessly** repairable — re-serialize the whole object with the
      non-finite values mapped to ``null``.
    * ``"truncated"`` — does not parse even with the permissive loader: the
      char-slice footgun cut it mid-token. Only a best-effort salvage of the
      still-readable front keys is possible.
    """
    try:
        json.loads(raw)  # stdlib accepts NaN/Infinity/-Infinity by default
    except (ValueError, TypeError):
        return "truncated"
    return "nonfinite"


def _repaired_blob(raw: str, cap: int) -> str:
    if _classify(raw) == "nonfinite":
        # Complete JSON, invalid only for a non-finite float. Re-serialize the
        # WHOLE object with NaN/Infinity → null (dump_capped now does this via
        # sanitize_nonfinite) — lossless, every field preserved. A tiny
        # provenance marker is added only to a dict (a top-level array is left
        # structurally intact). Idempotent: the result is json_valid=1, so a
        # re-scan never re-touches it.
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            clean = sanitize_nonfinite(parsed)
            clean.setdefault("_repaired_at", _now_iso())
            clean.setdefault(
                "_repair_reason",
                "json_valid=0 (non-finite float NaN/Infinity → null, BL-20260709)",
            )
            return dump_capped(clean, cap)
        if parsed is not None:  # top-level array / scalar — re-dump losslessly
            return dump_capped(parsed, cap)
    # Truncated / unparseable: best-effort salvage of the intact front keys,
    # raw preserved under _original_truncated for forensics.
    envelope: Dict[str, Any] = {
        "_repaired_at": _now_iso(),
        "_repair_reason": "json_valid=0 (char-slice truncation, BL-20260618)",
    }
    envelope.update(_salvage(raw))
    envelope["_original_truncated"] = raw
    return dump_capped(envelope, cap)


def _table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return False
    return column in cols


def find_malformed(conn: sqlite3.Connection) -> Dict[str, List[Tuple[Any, str]]]:
    """Return ``{"<table>.<col>": [(rowid, raw), ...]}`` for json_valid(col)=0 rows."""
    found: Dict[str, List[Tuple[Any, str]]] = {}
    for table, col, _cap in _TARGETS:
        if not _table_has_column(conn, table, col):
            continue
        try:
            # Use SQLite's implicit ``rowid`` rather than a declared ``id``
            # column: ``trades`` has ``id INTEGER PRIMARY KEY`` (an alias for
            # rowid) but ``order_packages``'s PK is ``order_package_id`` with
            # NO ``id`` column — a hardcoded ``SELECT id`` raised "no such
            # column: id" there and silently skipped both order_packages
            # targets (BL-20260709 dry-run, 2026-07-09). ``rowid`` addresses
            # any ordinary (non-WITHOUT-ROWID) table uniformly.
            rows = conn.execute(
                f"SELECT rowid, {col} FROM {table} "
                f"WHERE {col} IS NOT NULL AND {col} != '' AND json_valid({col}) = 0"
            ).fetchall()
        except sqlite3.Error as exc:
            print(f"  ! {table}.{col}: query failed ({exc})", file=sys.stderr)
            continue
        if rows:
            found[f"{table}.{col}"] = [(r[0], r[1]) for r in rows]
    return found


def repair(db_path: str, apply: bool) -> int:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        found = find_malformed(conn)
        total = sum(len(v) for v in found.values())
        if total == 0:
            print("repair_malformed_notes: OK — no malformed-JSON rows found (no-op).")
            return 0
        caps = {(t, c): cap for t, c, cap in _TARGETS}
        print(f"repair_malformed_notes: {total} malformed row(s) across "
              f"{len(found)} column(s){' — DRY RUN' if not apply else ''}:")
        for key, rows in found.items():
            table, col = key.split(".")
            # Classify so the operator sees WHY these are invalid before a write:
            # a "nonfinite" majority is losslessly repairable (re-dump with
            # NaN→null), a "truncated" row can only be front-key-salvaged.
            n_nonfinite = sum(1 for _rid, raw in rows if _classify(raw) == "nonfinite")
            n_truncated = len(rows) - n_nonfinite
            print(f"  {key}: {len(rows)} row(s) "
                  f"[nonfinite={n_nonfinite} (lossless), truncated={n_truncated} (salvage)]")
            sample_id, sample_raw = rows[0]
            print(f"    e.g. id={sample_id} raw[:120]={sample_raw[:120]!r}")
            # Show a truncated sample's TAIL too (the front looks like valid
            # JSON, so the cut only shows at the end) when any exist.
            for rid, raw in rows:
                if _classify(raw) == "truncated":
                    print(f"    trunc e.g. id={rid} raw[-80:]={raw[-80:]!r}")
                    break
            if not apply:
                continue
            cap = caps[(table, col)]
            for rowid, raw in rows:
                conn.execute(
                    f"UPDATE {table} SET {col} = ? WHERE rowid = ?",
                    (_repaired_blob(raw, cap), rowid),
                )
        if apply:
            conn.commit()
            # Verify idempotency: a second scan must find zero.
            remaining = sum(len(v) for v in find_malformed(conn).values())
            print(f"repair_malformed_notes: applied; remaining malformed rows = {remaining}")
            return 0 if remaining == 0 else 1
        print("repair_malformed_notes: dry run — re-run with --apply to repair (Tier-2 DB write).")
        return 0
    finally:
        conn.close()


def _resolve_db(explicit: str | None) -> str:
    if explicit:
        return explicit
    from src.utils.paths import trade_journal_db_path
    return str(trade_journal_db_path())


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="perform the repair (default: dry-run)")
    ap.add_argument("--db", default=None, help="DB path (default: canonical resolver)")
    args = ap.parse_args(argv[1:])
    return repair(_resolve_db(args.db), apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
