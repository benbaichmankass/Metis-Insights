"""Tests for scripts/ops/reconcile_orphan_history.py.

Orphan-flap hardening item #5 — the historical reconciliation tool that
collapses phantom flap duplicates so every physical position is ONE
reconciled row and no trade rests silently in an orphan state
(void-flag duplicates as ``reconcile_status='superseded'``, never delete).

Coverage:
  * a flap cluster (1 open + N closed phantoms) collapses to one canonical
    row with the N phantoms superseded — and the live OPEN row is the one kept
  * an orphan whose entry matches an order package is reconciled to it
    (reconcile_status='reconciled', order_package_id filled)
  * an orphan with no recoverable package stays 'unreconciled' (red flag)
  * two orphan rows that link DISTINCT real packages are NOT collapsed
  * a second OPEN row in a cluster is never void-flagged
  * a time gap splits one (account,symbol,dir) group into separate clusters
  * backtest rows are ignored
  * dry-run writes nothing
  * apply is idempotent (a second run is a no-op)
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from tests.fixtures.real_schema_db import insert_order_package, insert_trade

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_script_module():
    path = _REPO_ROOT / "scripts" / "ops" / "reconcile_orphan_history.py"
    spec = importlib.util.spec_from_file_location("reconcile_orphan_history", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["reconcile_orphan_history"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def script():
    return _load_script_module()


def _rows(db: Path):
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return {int(r["id"]): dict(r) for r in conn.execute(
            "SELECT id, status, setup_type, strategy_name, reconcile_status, "
            "order_package_id, notes FROM trades")}
    finally:
        conn.close()


# ── flap collapse ─────────────────────────────────────────────────────────

def test_flap_cluster_collapses_keeping_open_canonical(script, real_schema_db):
    db = real_schema_db()
    # One live OPEN orphan_adopt + three phantom closed adopted_orphan rows,
    # same account/symbol/direction, close in time.
    open_id = insert_trade(
        db, timestamp="2026-06-24T10:00:00Z", created_at="2026-06-24T10:00:00Z",
        symbol="MHG", direction="short", entry_price=4.10, position_size=1,
        status="open", strategy_name="orphan_adopt", setup_type="adopted_orphan",
        account_id="ib_paper", is_backtest=0)
    phantom_ids = [
        insert_trade(
            db, timestamp=f"2026-06-24T10:0{i}:00Z",
            created_at=f"2026-06-24T10:0{i}:00Z", symbol="MHG",
            direction="short", entry_price=4.10, position_size=1,
            status="closed", strategy_name="orphan_adopt",
            setup_type="adopted_orphan", exit_reason="sl_cross",
            pnl=-12.0, account_id="ib_paper", is_backtest=0)
        for i in (1, 2, 3)
    ]

    rc = script.run(str(db), apply=True, gap_hours=6.0, entry_tol=0.02)
    assert rc == 0

    rows = _rows(db)
    # The live OPEN row is the canonical kept (never superseded). No package
    # recoverable here → flagged unreconciled.
    assert rows[open_id]["reconcile_status"] == "unreconciled"
    assert rows[open_id]["status"] == "open"
    # All three phantom closes are void-flagged.
    for pid in phantom_ids:
        assert rows[pid]["reconcile_status"] == "superseded"
        notes = json.loads(rows[pid]["notes"])
        assert notes["superseded_by"] == open_id
        assert notes["superseded_reason"] == "phantom_orphan_flap_duplicate"
        # The original row is preserved (status untouched — void-flag, not delete).
        assert rows[pid]["status"] == "closed"


# ── reconcile to package ──────────────────────────────────────────────────

def test_orphan_reconciled_to_matching_package(script, real_schema_db):
    db = real_schema_db()
    insert_order_package(
        db, order_package_id="pkg-A", symbol="BTCUSDT", direction="long",
        entry=60000.0, status="closed", strategy_name="vwap")
    oid = insert_trade(
        db, timestamp="2026-06-20T10:00:00Z", created_at="2026-06-20T10:00:00Z",
        symbol="BTCUSDT", direction="long", entry_price=60050.0,
        position_size=0.001, status="orphaned", setup_type="adopted_orphan",
        account_id="bybit_2", is_backtest=0)

    script.run(str(db), apply=True, gap_hours=6.0, entry_tol=0.02)
    row = _rows(db)[oid]
    assert row["reconcile_status"] == "reconciled"
    assert row["order_package_id"] == "pkg-A"
    notes = json.loads(row["notes"])
    assert notes["reconciled_to_package"] == "pkg-A"


def test_orphan_without_package_stays_unreconciled(script, real_schema_db):
    db = real_schema_db()
    oid = insert_trade(
        db, timestamp="2026-06-20T10:00:00Z", created_at="2026-06-20T10:00:00Z",
        symbol="MHG", direction="short", entry_price=4.10, position_size=1,
        status="orphaned", strategy_name="orphan_adopt", account_id="ib_paper",
        is_backtest=0)
    script.run(str(db), apply=True, gap_hours=6.0, entry_tol=0.02)
    row = _rows(db)[oid]
    assert row["reconcile_status"] == "unreconciled"
    notes = json.loads(row["notes"])
    assert notes["reconcile_outcome"] == "no_recoverable_order_package"


# ── conservatism: never collapse genuinely distinct trades ────────────────

def test_distinct_packages_not_collapsed(script, real_schema_db):
    db = real_schema_db()
    insert_order_package(db, order_package_id="pkg-A", symbol="BTCUSDT",
                         direction="long", entry=60000.0)
    insert_order_package(db, order_package_id="pkg-B", symbol="BTCUSDT",
                         direction="long", entry=60000.0)
    a = insert_trade(
        db, timestamp="2026-06-20T10:00:00Z", created_at="2026-06-20T10:00:00Z",
        symbol="BTCUSDT", direction="long", entry_price=60000.0,
        position_size=0.001, status="closed", setup_type="adopted_orphan",
        order_package_id="pkg-A", account_id="bybit_2", is_backtest=0)
    b = insert_trade(
        db, timestamp="2026-06-20T10:05:00Z", created_at="2026-06-20T10:05:00Z",
        symbol="BTCUSDT", direction="long", entry_price=60000.0,
        position_size=0.001, status="closed", setup_type="adopted_orphan",
        order_package_id="pkg-B", account_id="bybit_2", is_backtest=0)
    script.run(str(db), apply=True, gap_hours=6.0, entry_tol=0.02)
    rows = _rows(db)
    # Two distinct real packages → two distinct trades; neither superseded.
    assert rows[a]["reconcile_status"] == "reconciled"
    assert rows[b]["reconcile_status"] == "reconciled"
    superseded = [r for r in rows.values() if r["reconcile_status"] == "superseded"]
    assert superseded == []


def test_second_open_row_never_superseded(script, real_schema_db):
    db = real_schema_db()
    first = insert_trade(
        db, timestamp="2026-06-24T10:00:00Z", created_at="2026-06-24T10:00:00Z",
        symbol="MHG", direction="short", entry_price=4.10, position_size=1,
        status="open", strategy_name="orphan_adopt", account_id="ib_paper",
        is_backtest=0)
    second = insert_trade(
        db, timestamp="2026-06-24T10:01:00Z", created_at="2026-06-24T10:01:00Z",
        symbol="MHG", direction="short", entry_price=4.10, position_size=1,
        status="open", strategy_name="orphan_adopt", account_id="ib_paper",
        is_backtest=0)
    script.run(str(db), apply=True, gap_hours=6.0, entry_tol=0.02)
    rows = _rows(db)
    # Neither open row is void-flagged; the 2nd is flagged for manual review.
    assert rows[first]["status"] == "open"
    assert rows[second]["status"] == "open"
    assert rows[second]["reconcile_status"] != "superseded"
    notes = json.loads(rows[second]["notes"])
    assert notes["reconcile_outcome"] == "second_open_row_in_cluster_manual_review"


def test_time_gap_splits_clusters(script, real_schema_db):
    db = real_schema_db()
    early = insert_trade(
        db, timestamp="2026-06-20T10:00:00Z", created_at="2026-06-20T10:00:00Z",
        symbol="MHG", direction="short", entry_price=4.10, position_size=1,
        status="closed", strategy_name="orphan_adopt", setup_type="adopted_orphan",
        account_id="ib_paper", is_backtest=0)
    late = insert_trade(
        db, timestamp="2026-06-22T10:00:00Z", created_at="2026-06-22T10:00:00Z",
        symbol="MHG", direction="short", entry_price=4.10, position_size=1,
        status="closed", strategy_name="orphan_adopt", setup_type="adopted_orphan",
        account_id="ib_paper", is_backtest=0)
    # 48h apart, gap=6h → two separate clusters → neither is a duplicate.
    script.run(str(db), apply=True, gap_hours=6.0, entry_tol=0.02)
    rows = _rows(db)
    assert rows[early]["reconcile_status"] != "superseded"
    assert rows[late]["reconcile_status"] != "superseded"


# ── exclusions + safety ───────────────────────────────────────────────────

def test_backtest_rows_ignored(script, real_schema_db):
    db = real_schema_db()
    bt = insert_trade(
        db, timestamp="2026-06-20T10:00:00Z", created_at="2026-06-20T10:00:00Z",
        symbol="MHG", direction="short", entry_price=4.10, position_size=1,
        status="orphaned", strategy_name="orphan_adopt", account_id="ib_paper",
        is_backtest=1)
    script.run(str(db), apply=True, gap_hours=6.0, entry_tol=0.02)
    assert _rows(db)[bt]["reconcile_status"] is None


def test_dry_run_writes_nothing(script, real_schema_db):
    db = real_schema_db()
    ids = [insert_trade(
        db, timestamp=f"2026-06-24T10:0{i}:00Z",
        created_at=f"2026-06-24T10:0{i}:00Z", symbol="MHG", direction="short",
        entry_price=4.10, position_size=1, status="closed",
        strategy_name="orphan_adopt", setup_type="adopted_orphan",
        account_id="ib_paper", is_backtest=0) for i in (0, 1, 2)]
    script.run(str(db), apply=False, gap_hours=6.0, entry_tol=0.02)
    rows = _rows(db)
    for tid in ids:
        assert rows[tid]["reconcile_status"] is None


def test_apply_is_idempotent(script, real_schema_db, capsys):
    db = real_schema_db()
    for i in (0, 1, 2):
        insert_trade(
            db, timestamp=f"2026-06-24T10:0{i}:00Z",
            created_at=f"2026-06-24T10:0{i}:00Z", symbol="MHG",
            direction="short", entry_price=4.10, position_size=1,
            status="closed", strategy_name="orphan_adopt",
            setup_type="adopted_orphan", account_id="ib_paper", is_backtest=0)
    script.run(str(db), apply=True, gap_hours=6.0, entry_tol=0.02)
    capsys.readouterr()
    # Second apply: the orphan predicate still matches the canonical (it is
    # unreconciled, not superseded), but every row is already in its terminal
    # state, so no UPDATE writes a row.
    script.run(str(db), apply=True, gap_hours=6.0, entry_tol=0.02)
    out = capsys.readouterr().out
    assert "wrote 0 row(s)." in out
