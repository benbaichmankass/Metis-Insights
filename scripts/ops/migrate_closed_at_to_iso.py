#!/usr/bin/env python3
"""One-shot migration: normalise existing ``trades.closed_at`` epoch-ms rows to ISO-8601.

Companion to the writer-side fix (BL-20260620-RECONCILER-CLOSEDAT-MS): the
reconciler-filled close path historically wrote Bybit's ``updatedTime`` /
``execTime`` as a raw epoch-milliseconds string (e.g. ``"1782128223798"``) into
``trades.closed_at`` (and ``notes.closed_at``), while the column contract is
ISO-8601. The read-side guard (src/web/api/_closed_at.py, PR #4162) makes
consumers tolerant, and the writer now normalises going forward; this script
rewrites the rows ALREADY persisted in ms form so the column is uniformly ISO
and the read-side guard becomes belt-and-suspenders.

DRY-RUN BY DEFAULT — prints what it would change. Pass ``--apply`` to write.
SELECT-then-UPDATE inside a single transaction; only rows whose ``closed_at``
is an all-digit, >=12-char epoch-ms value are touched (ISO rows are left alone),
so re-running is idempotent.

Usage:
    python3 scripts/ops/migrate_closed_at_to_iso.py              # dry-run
    python3 scripts/ops/migrate_closed_at_to_iso.py --apply      # write
    python3 scripts/ops/migrate_closed_at_to_iso.py --db /path/to/trade_journal.db
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# Allow ``python3 scripts/ops/migrate_closed_at_to_iso.py`` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.utils.closed_at import normalize_closed_at_value  # noqa: E402
from src.utils.paths import trade_journal_db_path  # noqa: E402


def _is_ms(value) -> bool:
    s = "" if value is None else str(value).strip()
    return s.isdigit() and len(s) >= 12


def migrate(db_path: Path, apply: bool) -> int:
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 2
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    col_changed = 0
    notes_changed = 0
    try:
        rows = conn.execute(
            "SELECT id, closed_at, notes FROM trades WHERE closed_at IS NOT NULL"
        ).fetchall()
        updates = []  # (id, new_closed_at_or_None, new_notes_or_None)
        for r in rows:
            new_closed_at = None
            new_notes = None
            if _is_ms(r["closed_at"]):
                iso = normalize_closed_at_value(r["closed_at"])
                if iso and iso != str(r["closed_at"]):
                    new_closed_at = iso
                    col_changed += 1
            # notes.closed_at (best-effort; only when it's an ms string)
            raw_notes = r["notes"]
            if raw_notes:
                try:
                    notes = json.loads(raw_notes)
                except (ValueError, TypeError):
                    notes = None
                if isinstance(notes, dict) and _is_ms(notes.get("closed_at")):
                    iso = normalize_closed_at_value(notes.get("closed_at"))
                    if iso and iso != str(notes.get("closed_at")):
                        notes["closed_at"] = iso
                        new_notes = json.dumps(notes)
                        notes_changed += 1
            if new_closed_at is not None or new_notes is not None:
                updates.append((r["id"], new_closed_at, new_notes))

        print(
            f"trades rows scanned: {len(rows)} | "
            f"closed_at ms→ISO: {col_changed} | notes.closed_at ms→ISO: {notes_changed}"
        )
        for tid, nca, _ in updates[:20]:
            if nca is not None:
                print(f"  trade {tid}: closed_at → {nca}")
        if len(updates) > 20:
            print(f"  … and {len(updates) - 20} more")

        if not apply:
            print("\nDRY-RUN — no changes written. Re-run with --apply to commit.")
            return 0

        for tid, nca, nnotes in updates:
            if nca is not None and nnotes is not None:
                conn.execute(
                    "UPDATE trades SET closed_at=?, notes=? WHERE id=?", (nca, nnotes, tid)
                )
            elif nca is not None:
                conn.execute("UPDATE trades SET closed_at=? WHERE id=?", (nca, tid))
            elif nnotes is not None:
                conn.execute("UPDATE trades SET notes=? WHERE id=?", (nnotes, tid))
        conn.commit()
        print(f"\nAPPLIED — updated {len(updates)} row(s).")
        return 0
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None, help="trade_journal.db path (default: canonical resolver)")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    args = ap.parse_args()
    db_path = Path(args.db) if args.db else Path(trade_journal_db_path())
    return migrate(db_path, args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
