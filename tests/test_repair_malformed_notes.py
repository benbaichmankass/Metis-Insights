"""BL-20260618 — the one-shot malformed-notes repair (scripts/ops/repair_malformed_notes.py).

A synthetic ``trades`` row with an invalid-JSON (char-slice-truncated) ``notes``
blob must be: counted by a dry-run, rewritten into VALID JSON by ``--apply``
(salvaging the intact ``closed_at``), and left untouched by a second run
(idempotent).
"""
from __future__ import annotations

import json
import sqlite3

from scripts.ops.repair_malformed_notes import find_malformed, repair


def _make_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, notes TEXT)")
    # A truncated char-slice of {"closed_at":"...","closed_reason":"<long>"} —
    # closed_at is intact at the front; the blob as a whole is invalid JSON.
    truncated = '{"closed_at": "2026-07-09T00:00:00Z", "closed_reason": "the position was ' + "x" * 40
    conn.execute("INSERT INTO trades (id, notes) VALUES (1, ?)", (truncated,))
    conn.execute("INSERT INTO trades (id, notes) VALUES (2, ?)",
                 ('{"closed_at": "2026-07-09T00:00:00Z", "ok": true}',))  # already valid
    conn.execute("INSERT INTO trades (id, notes) VALUES (3, NULL)")       # null skipped
    conn.commit()
    conn.close()


def test_finds_only_the_malformed_row(tmp_path):
    db = str(tmp_path / "t.db")
    _make_db(db)
    conn = sqlite3.connect(db)
    try:
        found = find_malformed(conn)
    finally:
        conn.close()
    assert list(found.keys()) == ["trades.notes"]
    assert [rowid for rowid, _ in found["trades.notes"]] == [1]


def test_dry_run_does_not_write(tmp_path):
    db = str(tmp_path / "t.db")
    _make_db(db)
    assert repair(db, apply=False) == 0
    conn = sqlite3.connect(db)
    try:
        # Row 1 still invalid (dry-run wrote nothing).
        bad = conn.execute("SELECT COUNT(*) FROM trades WHERE notes IS NOT NULL AND json_valid(notes)=0").fetchone()[0]
    finally:
        conn.close()
    assert bad == 1


def test_apply_repairs_and_is_idempotent(tmp_path):
    db = str(tmp_path / "t.db")
    _make_db(db)
    assert repair(db, apply=True) == 0
    conn = sqlite3.connect(db)
    try:
        bad = conn.execute("SELECT COUNT(*) FROM trades WHERE notes IS NOT NULL AND json_valid(notes)=0").fetchone()[0]
        row1 = conn.execute("SELECT notes FROM trades WHERE id=1").fetchone()[0]
        row2 = conn.execute("SELECT notes FROM trades WHERE id=2").fetchone()[0]
    finally:
        conn.close()
    assert bad == 0  # everything valid now
    repaired = json.loads(row1)  # must parse
    assert repaired["closed_at"] == "2026-07-09T00:00:00Z"  # salvaged
    assert repaired["_repair_reason"].startswith("json_valid=0")
    assert "_original_truncated" in repaired
    # Untouched valid row is unchanged.
    assert json.loads(row2)["ok"] is True
    # Idempotent: a second apply finds nothing to do.
    assert repair(db, apply=True) == 0
