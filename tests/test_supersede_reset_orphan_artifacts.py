"""Tests for scripts/ops/supersede_reset_orphan_artifacts.py.

The one-shot journal-hygiene tool that void-flags the paper-account RESET
orphan-adoption artifacts (the 2026-07-07 alpaca_paper external reset — bare
`adopted_orphan` rows with fabricated local-compute PnL, e.g. the SLV short
double-adopted as trades 3265+3266 at -693.6 each).

Coverage:
  * the bare-orphan phantom pair (strategy_name='orphan_adopt', NULL
    order_package_id, local_compute marker, is_demo=1, closed) is matched
  * a genuinely-reattached adopted orphan (real strategy_name + order_package_id
    + reconcile_status='reconciled') is NEVER matched
  * a real-money bare orphan (is_demo=0) is NEVER matched
  * a bare orphan without the local_compute marker is NEVER matched
  * dry-run writes nothing; apply void-flags only the matched rows
  * --ids restricts the match; apply is idempotent (a second run is a no-op)
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

from tests.fixtures.real_schema_db import real_schema_db, insert_trade  # noqa: F401

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_script_module():
    path = _REPO_ROOT / "scripts" / "ops" / "supersede_reset_orphan_artifacts.py"
    spec = importlib.util.spec_from_file_location("supersede_reset_orphan_artifacts", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["supersede_reset_orphan_artifacts"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def script():
    return _load_script_module()


_LOCAL_COMPUTE = '{"pnl_source": "local_compute", "contract_value_usd": 1.0}'


def _seed(real_schema_db) -> tuple[Path, dict]:
    """Materialise a DB with the SLV reset scenario; return (db, ids-by-role)."""
    db = real_schema_db()
    ids = {}
    ids["phantom_a"] = insert_trade(
        db, symbol="SLV", timestamp="2026-07-07T20:00:00+00:00", direction="short", entry_price=53.94, position_size=1360.0, status="closed",
        setup_type="adopted_orphan", strategy_name="orphan_adopt",
        order_package_id=None, pnl=-693.6, account_id="alpaca_paper",
        is_demo=1, is_backtest=0, notes=_LOCAL_COMPUTE,
        reconcile_status="unreconciled",
    )
    ids["phantom_b"] = insert_trade(
        db, symbol="SLV", timestamp="2026-07-07T20:00:00+00:00", direction="short", entry_price=53.94, position_size=1360.0, status="closed",
        setup_type="adopted_orphan", strategy_name="orphan_adopt",
        order_package_id=None, pnl=-693.6, account_id="alpaca_paper",
        is_demo=1, is_backtest=0, notes=_LOCAL_COMPUTE,
        reconcile_status="unreconciled",
    )
    # Genuinely reattached adopted orphan — real strategy + package + reconciled.
    ids["reattached"] = insert_trade(
        db, symbol="SLV", timestamp="2026-07-07T20:00:00+00:00", direction="long", entry_price=53.94, position_size=1360.0, status="closed",
        setup_type="adopted_orphan", strategy_name="slv_trend_1h",
        order_package_id="pkg-11cd7b3e51ef49ed", pnl=-262.5,
        account_id="alpaca_paper", is_demo=1, is_backtest=0,
        notes=_LOCAL_COMPUTE, reconcile_status="reconciled",
    )
    # Real-money bare orphan — categorically excluded (is_demo=0).
    ids["real_money"] = insert_trade(
        db, symbol="SLV", timestamp="2026-07-07T20:00:00+00:00", direction="short", entry_price=53.94, position_size=1360.0, status="closed",
        setup_type="adopted_orphan", strategy_name="orphan_adopt",
        order_package_id=None, pnl=-693.6, account_id="alpaca_live",
        is_demo=0, is_backtest=0, notes=_LOCAL_COMPUTE,
        reconcile_status="unreconciled",
    )
    # Bare orphan without the local_compute marker — excluded.
    ids["no_marker"] = insert_trade(
        db, symbol="SLV", timestamp="2026-07-07T20:00:00+00:00", direction="short", entry_price=53.94, position_size=1360.0, status="closed",
        setup_type="adopted_orphan", strategy_name="orphan_adopt",
        order_package_id=None, pnl=-1.0, account_id="alpaca_paper",
        is_demo=1, is_backtest=0, notes="{}", reconcile_status="unreconciled",
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
    rc = script.run(str(db), apply=False, account_id="alpaca_paper", ids=None)
    assert rc == 0
    for tid in ids.values():
        assert _status(db, tid) != "superseded"


def test_apply_supersedes_only_phantoms(script, real_schema_db):
    db, ids = _seed(real_schema_db)
    rc = script.run(str(db), apply=True, account_id="alpaca_paper", ids=None)
    assert rc == 0
    assert _status(db, ids["phantom_a"]) == "superseded"
    assert _status(db, ids["phantom_b"]) == "superseded"
    # Genuine reattached + real-money + no-marker rows untouched.
    assert _status(db, ids["reattached"]) == "reconciled"
    assert _status(db, ids["real_money"]) == "unreconciled"
    assert _status(db, ids["no_marker"]) == "unreconciled"


def test_ids_allowlist_and_idempotent(script, real_schema_db):
    db, ids = _seed(real_schema_db)
    # Restrict to one id → only that phantom is superseded.
    rc = script.run(str(db), apply=True, account_id="alpaca_paper",
                    ids=[ids["phantom_a"]])
    assert rc == 0
    assert _status(db, ids["phantom_a"]) == "superseded"
    assert _status(db, ids["phantom_b"]) == "unreconciled"
    # Second run over the same id is a no-op (already superseded).
    rc2 = script.run(str(db), apply=True, account_id="alpaca_paper",
                     ids=[ids["phantom_a"]])
    assert rc2 == 0
    assert _status(db, ids["phantom_a"]) == "superseded"
