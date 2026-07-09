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
    # order_packages' real PK is order_package_id (TEXT) — it has NO ``id``
    # column, so the original ``SELECT id`` raised "no such column: id" and
    # silently skipped both its targets (BL-20260709 dry-run). Model that here
    # so the rowid fix is locked: this table must be scanned + repaired too.
    conn.execute(
        "CREATE TABLE order_packages "
        "(order_package_id TEXT PRIMARY KEY, signal_logic TEXT, meta TEXT)"
    )
    # A truncated char-slice of {"closed_at":"...","closed_reason":"<long>"} —
    # closed_at is intact at the front; the blob as a whole is invalid JSON.
    truncated = '{"closed_at": "2026-07-09T00:00:00Z", "closed_reason": "the position was ' + "x" * 40
    conn.execute("INSERT INTO trades (id, notes) VALUES (1, ?)", (truncated,))
    conn.execute("INSERT INTO trades (id, notes) VALUES (2, ?)",
                 ('{"closed_at": "2026-07-09T00:00:00Z", "ok": true}',))  # already valid
    conn.execute("INSERT INTO trades (id, notes) VALUES (3, NULL)")       # null skipped
    # A malformed order_packages.signal_logic (no ``id`` column on this table).
    op_truncated = '{"setup_type": "breakout", "reasoning": "swept the ' + "y" * 40
    conn.execute(
        "INSERT INTO order_packages (order_package_id, signal_logic, meta) VALUES (?, ?, ?)",
        ("pkg-abc123", op_truncated, '{"killzone": "ny", "ok": true}'),  # meta already valid
    )
    # A COMPLETE-but-non-finite signal_logic — the dominant BL-20260709 case: a
    # std_dev / z-score with a zero denominator serialized by the old
    # json.dumps default emits the bare token NaN (invalid JSON). This is
    # LOSSLESSLY repairable (re-dump with NaN→null), NOT a truncation.
    op_nonfinite = ('{"strategy_name": "vwap", "vwap": 77114.58, '
                    '"std_dev": 0.0, "deviation": NaN, "z": Infinity}')
    conn.execute(
        "INSERT INTO order_packages (order_package_id, signal_logic, meta) VALUES (?, ?, ?)",
        ("pkg-nonfinite", op_nonfinite, '{"ok": true}'),
    )
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
    # Both the trades.notes AND the order_packages.signal_logic malformed rows
    # are found — the latter proves the rowid fix (order_packages has no `id`).
    assert set(found.keys()) == {"trades.notes", "order_packages.signal_logic"}
    assert [rowid for rowid, _ in found["trades.notes"]] == [1]
    # Both the truncated AND the non-finite signal_logic rows are found.
    assert len(found["order_packages.signal_logic"]) == 2


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
    # The order_packages.signal_logic row (no `id` column on that table) was
    # ALSO repaired — the rowid fix reaches it where `SELECT id` could not.
    conn2 = sqlite3.connect(db)
    try:
        op_bad = conn2.execute(
            "SELECT COUNT(*) FROM order_packages "
            "WHERE signal_logic IS NOT NULL AND json_valid(signal_logic)=0"
        ).fetchone()[0]
        op_sig = conn2.execute(
            "SELECT signal_logic FROM order_packages WHERE order_package_id='pkg-abc123'"
        ).fetchone()[0]
    finally:
        conn2.close()
    assert op_bad == 0
    # Now valid JSON; the original blob is preserved under _original_truncated
    # (setup_type isn't in _SALVAGE_KEYS — those are the trades.notes
    # load-bearing keys — but the raw text is kept verbatim for forensics).
    op_repaired = json.loads(op_sig)
    assert op_repaired["_repair_reason"].startswith("json_valid=0")
    assert op_repaired["_original_truncated"].startswith('{"setup_type": "breakout"')
    # The COMPLETE-but-non-finite row is repaired LOSSLESSLY: every field is
    # preserved (not dumped into _original_truncated), the non-finite floats
    # become null, and the blob is now valid JSON.
    conn3 = sqlite3.connect(db)
    try:
        nf_sig = conn3.execute(
            "SELECT signal_logic FROM order_packages WHERE order_package_id='pkg-nonfinite'"
        ).fetchone()[0]
    finally:
        conn3.close()
    nf = json.loads(nf_sig)
    assert nf["strategy_name"] == "vwap"        # structure preserved
    assert nf["vwap"] == 77114.58               # finite value untouched
    assert nf["std_dev"] == 0.0
    assert nf["deviation"] is None              # NaN → null
    assert nf["z"] is None                      # Infinity → null
    assert "_original_truncated" not in nf      # NOT the destructive salvage path
    assert nf["_repair_reason"].startswith("json_valid=0")
    # Idempotent: a second apply finds nothing to do.
    assert repair(db, apply=True) == 0
