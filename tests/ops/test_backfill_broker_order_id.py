"""Tests for scripts/ops/backfill_broker_order_id.py (Slice B / B0, MB-20260629-ALLOC-COSTCAP)."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "backfill_broker_order_id",
    Path(__file__).resolve().parents[2] / "scripts" / "ops" / "backfill_broker_order_id.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)  # type: ignore[union-attr]


def _seed(db: Path, rows: list[tuple]) -> None:
    """rows = [(id, notes, broker_order_id), ...]."""
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY, notes TEXT, "
        "broker_order_id TEXT, pnl REAL)"
    )
    for r in rows:
        conn.execute(
            "INSERT INTO trades (id, notes, broker_order_id, pnl) VALUES (?,?,?,?)",
            (*r, 1.23),  # a pnl the backfill must never touch
        )
    conn.commit()
    conn.close()


def _fetch(db: Path):
    conn = sqlite3.connect(str(db))
    out = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT id, broker_order_id, pnl FROM trades")}
    conn.close()
    return out


def test_backfill_copies_notes_trade_id_and_preserves_existing(tmp_path: Path):
    db = tmp_path / "trade_journal.db"
    _seed(db, [
        # NULL column + a recoverable notes.trade_id -> backfilled
        (1, json.dumps({"trade_id": "bybit-oid-111", "is_dry": False}), None),
        # already populated -> must NOT be overwritten
        (2, json.dumps({"trade_id": "bybit-oid-999"}), "already-set-222"),
        # notes with no trade_id -> skipped (nothing to copy)
        (3, json.dumps({"is_dry": True}), None),
        # empty-string trade_id -> skipped (not a usable id)
        (4, json.dumps({"trade_id": ""}), None),
        # malformed notes (not JSON) -> json_extract returns NULL -> skipped
        (5, "not-json", None),
    ])
    # dry-run writes nothing.
    s = _MOD.backfill(str(db), apply=False)
    assert s["candidates"] == 1  # only id 1 qualifies
    assert s["written"] == 1
    assert s["skipped_no_id"] == 3  # ids 3, 4, 5
    after_dry = _fetch(db)
    assert after_dry[1][0] is None  # dry-run: untouched

    # apply copies notes.trade_id onto id 1 only.
    s2 = _MOD.backfill(str(db), apply=True)
    assert s2["candidates"] == 1 and s2["written"] == 1
    after = _fetch(db)
    assert after[1][0] == "bybit-oid-111"
    assert after[1][1] == 1.23  # pnl never touched
    assert after[2][0] == "already-set-222"  # existing value preserved
    assert after[3][0] is None and after[4][0] is None and after[5][0] is None


def test_backfill_is_idempotent(tmp_path: Path):
    db = tmp_path / "trade_journal.db"
    _seed(db, [(1, json.dumps({"trade_id": "oid-abc"}), None)])
    _MOD.backfill(str(db), apply=True)
    first = _fetch(db)[1]
    # second run finds no candidates (already populated) -> no-op.
    s = _MOD.backfill(str(db), apply=True)
    assert s["candidates"] == 0 and s["written"] == 0
    assert _fetch(db)[1] == first
