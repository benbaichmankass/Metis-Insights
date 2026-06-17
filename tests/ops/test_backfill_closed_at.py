"""Tests for scripts/ops/backfill_closed_at.py (P1-E).

Verifies the backfill fills ``trades.closed_at`` for historical closed rows
using the SAME read-path derivation
(``src/web/api/routers/trades_closed.py``): prefer the linked
``order_packages.updated_at``, else parse ``notes.closed_at`` JSON, else leave
NULL. Idempotent; dry-run is the default and writes nothing.

Uses the canonical-schema fixture (``tests/fixtures/real_schema_db.py``) so a
column rename in production fails the test rather than silently passing.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

from tests.fixtures.real_schema_db import (
    insert_order_package,
    insert_trade,
    make_canonical_db,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "ops" / "backfill_closed_at.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("backfill_closed_at", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _closed_at(db: Path, trade_id: int):
    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute("SELECT closed_at FROM trades WHERE id = ?", (trade_id,))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# closed_at derived from the linked order_package.updated_at
# ---------------------------------------------------------------------------

def test_apply_fills_from_order_package_updated_at(tmp_path):
    mod = _load_module()
    db = tmp_path / "j.db"
    make_canonical_db(db)

    tid = insert_trade(
        db, account_id="bybit_2", symbol="BTCUSDT", direction="long",
        entry_price=60000.0, exit_price=61000.0, position_size=0.001,
        status="closed", is_backtest=0, timestamp="2026-06-01T10:00:00Z",
        closed_at=None,
    )
    # Package linked by the read-path join (op.linked_trade_id = t.id).
    insert_order_package(
        db, order_package_id="op-1", linked_trade_id=tid,
        updated_at="2026-06-01T12:34:56Z",
    )

    summary = mod.plan_and_apply(db, apply=True)

    assert summary["scanned"] == 1
    assert summary["fillable"] == 1
    assert summary["by_source"]["op_updated_at"] == 1
    assert _closed_at(db, tid) == "2026-06-01T12:34:56Z"


def test_apply_fills_from_canonical_order_package_id_link(tmp_path):
    """Widened vs the read path: a package attached only by the canonical
    ``trades.order_package_id = order_packages.order_package_id`` link (no
    ``linked_trade_id``) is still resolved."""
    mod = _load_module()
    db = tmp_path / "j.db"
    make_canonical_db(db)

    tid = insert_trade(
        db, account_id="bybit_2", symbol="BTCUSDT", direction="long",
        entry_price=60000.0, exit_price=61000.0, position_size=0.001,
        status="closed", is_backtest=0, timestamp="2026-06-01T10:00:00Z",
        closed_at=None, order_package_id="op-canon",
    )
    insert_order_package(
        db, order_package_id="op-canon",  # linked_trade_id left NULL
        updated_at="2026-06-02T09:00:00Z",
    )

    mod.plan_and_apply(db, apply=True)
    assert _closed_at(db, tid) == "2026-06-02T09:00:00Z"


# ---------------------------------------------------------------------------
# closed_at parsed from notes JSON when there's no linked package
# ---------------------------------------------------------------------------

def test_apply_fills_from_notes_closed_at(tmp_path):
    mod = _load_module()
    db = tmp_path / "j.db"
    make_canonical_db(db)

    tid = insert_trade(
        db, account_id="bybit_2", symbol="ETHUSDT", direction="short",
        entry_price=2000.0, exit_price=1950.0, position_size=0.01,
        status="closed", is_backtest=0, timestamp="2026-06-03T08:00:00Z",
        closed_at=None,
        notes=json.dumps({"closed_at": "2026-06-03T15:00:00Z",
                           "exit_reason": "reconciler_filled"}),
    )

    summary = mod.plan_and_apply(db, apply=True)
    assert summary["by_source"]["notes_closed_at"] == 1
    assert _closed_at(db, tid) == "2026-06-03T15:00:00Z"


# ---------------------------------------------------------------------------
# underivable -> left NULL (no package, no notes.closed_at)
# ---------------------------------------------------------------------------

def test_underivable_row_left_null(tmp_path):
    mod = _load_module()
    db = tmp_path / "j.db"
    make_canonical_db(db)

    tid = insert_trade(
        db, account_id="bybit_2", symbol="BTCUSDT", direction="long",
        entry_price=60000.0, exit_price=61000.0, position_size=0.001,
        status="closed", is_backtest=0, timestamp="2026-06-04T10:00:00Z",
        closed_at=None,
        notes=json.dumps({"exit_reason": "sl"}),  # no closed_at key
    )

    summary = mod.plan_and_apply(db, apply=True)
    assert summary["fillable"] == 0
    assert summary["left_null"] == 1
    assert _closed_at(db, tid) is None


def test_malformed_notes_does_not_crash(tmp_path):
    """A non-JSON notes blob is skipped, not raised on (best-effort)."""
    mod = _load_module()
    db = tmp_path / "j.db"
    make_canonical_db(db)

    tid = insert_trade(
        db, account_id="bybit_2", symbol="BTCUSDT", direction="long",
        entry_price=60000.0, exit_price=61000.0, position_size=0.001,
        status="closed", is_backtest=0, timestamp="2026-06-04T11:00:00Z",
        closed_at=None, notes="{not valid json",
    )

    summary = mod.plan_and_apply(db, apply=True)
    assert summary["left_null"] == 1
    assert _closed_at(db, tid) is None


# ---------------------------------------------------------------------------
# idempotency: an already-set closed_at is untouched
# ---------------------------------------------------------------------------

def test_already_set_closed_at_untouched(tmp_path):
    mod = _load_module()
    db = tmp_path / "j.db"
    make_canonical_db(db)

    tid = insert_trade(
        db, account_id="bybit_2", symbol="BTCUSDT", direction="long",
        entry_price=60000.0, exit_price=61000.0, position_size=0.001,
        status="closed", is_backtest=0, timestamp="2026-06-05T10:00:00Z",
        closed_at="2026-06-05T11:00:00Z",
    )
    # A package with a DIFFERENT updated_at — must NOT overwrite the existing
    # closed_at (the row isn't even scanned).
    insert_order_package(
        db, order_package_id="op-2", linked_trade_id=tid,
        updated_at="2026-06-05T23:59:59Z",
    )

    summary = mod.plan_and_apply(db, apply=True)
    assert summary["scanned"] == 0
    assert _closed_at(db, tid) == "2026-06-05T11:00:00Z"


def test_rerun_after_apply_is_noop(tmp_path):
    mod = _load_module()
    db = tmp_path / "j.db"
    make_canonical_db(db)

    tid = insert_trade(
        db, account_id="bybit_2", symbol="BTCUSDT", direction="long",
        entry_price=60000.0, exit_price=61000.0, position_size=0.001,
        status="closed", is_backtest=0, timestamp="2026-06-06T10:00:00Z",
        closed_at=None,
    )
    insert_order_package(
        db, order_package_id="op-3", linked_trade_id=tid,
        updated_at="2026-06-06T12:00:00Z",
    )

    first = mod.plan_and_apply(db, apply=True)
    assert first["fillable"] == 1
    second = mod.plan_and_apply(db, apply=True)
    assert second["scanned"] == 0
    assert second["fillable"] == 0
    assert _closed_at(db, tid) == "2026-06-06T12:00:00Z"


# ---------------------------------------------------------------------------
# dry-run writes nothing
# ---------------------------------------------------------------------------

def test_dry_run_writes_nothing(tmp_path):
    mod = _load_module()
    db = tmp_path / "j.db"
    make_canonical_db(db)

    tid = insert_trade(
        db, account_id="bybit_2", symbol="BTCUSDT", direction="long",
        entry_price=60000.0, exit_price=61000.0, position_size=0.001,
        status="closed", is_backtest=0, timestamp="2026-06-07T10:00:00Z",
        closed_at=None,
    )
    insert_order_package(
        db, order_package_id="op-4", linked_trade_id=tid,
        updated_at="2026-06-07T12:00:00Z",
    )

    summary = mod.plan_and_apply(db, apply=False)
    assert summary["fillable"] == 1   # it WOULD fill 1
    assert summary["applied"] == 0    # but wrote nothing
    assert _closed_at(db, tid) is None


def test_backtest_rows_ignored(tmp_path):
    """is_backtest=1 closed rows are never touched (synthetic data)."""
    mod = _load_module()
    db = tmp_path / "j.db"
    make_canonical_db(db)

    tid = insert_trade(
        db, account_id="bybit_2", symbol="BTCUSDT", direction="long",
        entry_price=60000.0, exit_price=61000.0, position_size=0.001,
        status="closed", is_backtest=1, timestamp="2026-06-08T10:00:00Z",
        closed_at=None,
    )
    insert_order_package(
        db, order_package_id="op-5", linked_trade_id=tid,
        updated_at="2026-06-08T12:00:00Z",
    )

    summary = mod.plan_and_apply(db, apply=True)
    assert summary["scanned"] == 0
    assert _closed_at(db, tid) is None
