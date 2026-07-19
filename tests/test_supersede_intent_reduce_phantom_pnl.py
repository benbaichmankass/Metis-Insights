"""Tests for scripts/ops/supersede_intent_reduce_phantom_pnl.py.

The one-shot journal-hygiene tool that void-flags historical INTENT-REDUCE
phantom-PnL rows (BL-20260711, PR #6926 follow-up): a closed `intent_reduce`
bookkeeping leg carrying a non-NULL pnl (the reconciler/mark-to-market
attributed the parent position's close onto the leg — the entry==exit
+$561/+620/+898 fabrication).

Coverage:
  * a reduce leg (setup_type='intent_reduce') with non-NULL pnl + entry==exit is matched
  * a reduce leg tagged only via the notes.intent_reduce flag is matched
  * a reduce leg with entry != exit is matched by default, EXCLUDED by --equal-only
  * a non-reduce-leg trade is NEVER matched (the parent close is safe)
  * a reduce leg with pnl already NULL is NEVER matched (nothing to void)
  * an open reduce leg is NEVER matched (only closed rows)
  * dry-run writes nothing; apply void-flags only the matched rows
  * --equal-only restricts to entry==exit; --ids restricts; apply is idempotent
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

from tests.fixtures.real_schema_db import insert_trade

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_script_module():
    path = _REPO_ROOT / "scripts" / "ops" / "supersede_intent_reduce_phantom_pnl.py"
    spec = importlib.util.spec_from_file_location(
        "supersede_intent_reduce_phantom_pnl", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["supersede_intent_reduce_phantom_pnl"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def script():
    return _load_script_module()


def _seed(real_schema_db) -> tuple[Path, dict]:
    db = real_schema_db()
    ids = {}
    # entry==exit phantom (setup_type=intent_reduce) — ironclad.
    ids["equal"] = insert_trade(
        db, symbol="BTCUSDT", timestamp="2026-06-07T00:00:00+00:00",
        direction="short", entry_price=80000.0, exit_price=80000.0,
        position_size=0.003, status="closed", setup_type="intent_reduce",
        strategy_name="htf_pullback_trend_2h", order_package_id=None,
        pnl=561.0, account_id="bybit_2", is_demo=0, is_backtest=0,
        notes="{}", reconcile_status="unreconciled",
    )
    # reduce leg tagged only via the notes flag (setup_type reattached).
    ids["notes"] = insert_trade(
        db, symbol="BTCUSDT", timestamp="2026-06-07T00:05:00+00:00",
        direction="short", entry_price=80000.0, exit_price=80000.0,
        position_size=0.003, status="closed", setup_type="reconciler_filled",
        strategy_name="trend_donchian", order_package_id=None, pnl=620.0,
        account_id="bybit_1", is_demo=1, is_backtest=0,
        notes='{"intent_reduce": true}', reconcile_status="unreconciled",
    )
    # reduce leg with entry != exit — matched by default, not by --equal-only.
    ids["nonequal"] = insert_trade(
        db, symbol="BTCUSDT", timestamp="2026-06-07T00:10:00+00:00",
        direction="short", entry_price=80000.0, exit_price=80100.0,
        position_size=0.003, status="closed", setup_type="intent_reduce",
        strategy_name="htf_pullback_trend_2h", order_package_id=None,
        pnl=100.0, account_id="bybit_2", is_demo=0, is_backtest=0,
        notes="{}", reconcile_status="unreconciled",
    )
    # NON-reduce-leg real trade — the parent close must never be touched.
    ids["normal"] = insert_trade(
        db, symbol="BTCUSDT", timestamp="2026-06-07T00:15:00+00:00",
        direction="short", entry_price=80000.0, exit_price=79000.0,
        position_size=0.01, status="closed", setup_type="vwap",
        strategy_name="vwap", order_package_id="pkg-real", pnl=1000.0,
        account_id="bybit_2", is_demo=0, is_backtest=0, notes="{}",
        reconcile_status="reconciled",
    )
    # reduce leg already NULL — nothing to void (the new-code contract).
    ids["already_null"] = insert_trade(
        db, symbol="BTCUSDT", timestamp="2026-06-07T00:20:00+00:00",
        direction="short", entry_price=80000.0, exit_price=80000.0,
        position_size=0.003, status="closed", setup_type="intent_reduce",
        strategy_name="htf_pullback_trend_2h", order_package_id=None,
        pnl=None, account_id="bybit_2", is_demo=0, is_backtest=0,
        notes="{}", reconcile_status="unreconciled",
    )
    # open reduce leg — only closed rows are targeted.
    ids["open"] = insert_trade(
        db, symbol="BTCUSDT", timestamp="2026-06-07T00:25:00+00:00",
        direction="short", entry_price=80000.0, exit_price=None,
        position_size=0.003, status="open", setup_type="intent_reduce",
        strategy_name="htf_pullback_trend_2h", order_package_id=None,
        pnl=None, account_id="bybit_2", is_demo=0, is_backtest=0,
        notes="{}", reconcile_status="unreconciled",
    )
    return db, ids


def _status(db: Path, tid: int) -> str | None:
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT reconcile_status FROM trades WHERE id=?", (tid,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def test_dry_run_writes_nothing(script, real_schema_db):
    db, ids = _seed(real_schema_db)
    rc = script.run(str(db), apply=False, equal_only=False, ids=None)
    assert rc == 0
    for tid in ids.values():
        assert _status(db, tid) != "superseded"


def test_apply_supersedes_all_reduce_leg_phantoms(script, real_schema_db):
    db, ids = _seed(real_schema_db)
    rc = script.run(str(db), apply=True, equal_only=False, ids=None)
    assert rc == 0
    assert _status(db, ids["equal"]) == "superseded"
    assert _status(db, ids["notes"]) == "superseded"
    assert _status(db, ids["nonequal"]) == "superseded"
    # The parent close, the already-NULL leg, and the open leg are untouched.
    assert _status(db, ids["normal"]) == "reconciled"
    assert _status(db, ids["already_null"]) == "unreconciled"
    assert _status(db, ids["open"]) == "unreconciled"


def test_equal_only_excludes_nonequal(script, real_schema_db):
    db, ids = _seed(real_schema_db)
    rc = script.run(str(db), apply=True, equal_only=True, ids=None)
    assert rc == 0
    assert _status(db, ids["equal"]) == "superseded"
    assert _status(db, ids["notes"]) == "superseded"
    # entry != exit leg is NOT touched under --equal-only.
    assert _status(db, ids["nonequal"]) == "unreconciled"


def test_ids_allowlist_and_idempotent(script, real_schema_db):
    db, ids = _seed(real_schema_db)
    rc = script.run(str(db), apply=True, equal_only=False, ids=[ids["equal"]])
    assert rc == 0
    assert _status(db, ids["equal"]) == "superseded"
    assert _status(db, ids["notes"]) == "unreconciled"
    # Second run over the same id is a no-op.
    rc2 = script.run(str(db), apply=True, equal_only=False, ids=[ids["equal"]])
    assert rc2 == 0
    assert _status(db, ids["equal"]) == "superseded"
