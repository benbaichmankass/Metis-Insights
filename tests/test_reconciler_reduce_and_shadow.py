"""Regression tests for two reconciler correctness fixes (2026-07-19 QA):

  * BL-20260711 (site 2) — ``_sweep_local_pnl_for_unpriced`` must NOT
    re-fabricate pnl on an ``intent_reduce`` reduce leg. The write-path
    fix in ``_close_trade_from_order_status`` leaves reduce-leg pnl NULL;
    the universal mark-to-market fallback must skip reduce legs too, or it
    re-books the phantom next tick.
  * BL-20260705 — ``_sweep_unlinked_packages`` must terminalise an
    ``execution: shadow`` order package as ``shadow_expired`` (NOT
    ``orphaned``), so shadow-soak noise never pollutes orphan-rate
    analytics.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "bot-data"))
    return tmp_path


def _old_iso(minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def test_sweep_local_pnl_skips_reduce_leg(isolated_env: Path) -> None:
    """A closed ``intent_reduce`` row with pnl NULL survives the universal
    local-compute sweep untouched (BL-20260711 site 2)."""
    from src.runtime.order_monitor import _sweep_local_pnl_for_unpriced
    from src.units.db.database import Database
    from src.utils.paths import trade_journal_db_path

    db = Database(db_path=trade_journal_db_path())
    tid = db.insert_trade({
        "symbol": "BTCUSDT",
        "direction": "short",
        "entry_price": 77000.0,
        "position_size": 0.6,
        "setup_type": "intent_reduce",
        "status": "closed",
        "pnl": None,
        "account_id": "bybit_2",
        "timestamp": _old_iso(30),
        "created_at": _old_iso(30),
    })

    _sweep_local_pnl_for_unpriced(db)

    conn = db.connect()
    try:
        pnl = conn.execute(
            "SELECT pnl FROM trades WHERE id = ?", (tid,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert pnl is None  # fails on pre-fix code (mark-to-market fabricated)


def test_sweep_unlinked_packages_shadow_vs_live(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shadow-strategy unlinked package → ``shadow_expired``; a
    live-strategy one → ``orphaned`` (BL-20260705)."""
    import src.strategy_registry as sr
    from src.runtime.order_monitor import _sweep_unlinked_packages
    from src.units.db.database import Database
    from src.utils.paths import trade_journal_db_path

    db = Database(db_path=trade_journal_db_path())
    for pid, strat in (("op-shadow", "shadow_strat"), ("op-live", "live_strat")):
        db.insert_order_package({
            "order_package_id": pid,
            "strategy_name": strat,
            "symbol": "ETHUSDT",
            "direction": "long",
            "entry": 1800.0,
            "sl": 1770.0,
            "tp": 1980.0,
            "status": "open",
        })
    # Age both past the 5-min sweep threshold.
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE order_packages SET created_at = ?, linked_trade_id = NULL",
            (_old_iso(10),),
        )
        conn.commit()
    finally:
        conn.close()

    # Resolve shadow_strat as execution: shadow, everything else live.
    monkeypatch.setattr(
        sr, "execution_mode",
        lambda name, *a, **k: "shadow" if name == "shadow_strat" else "live",
    )

    _sweep_unlinked_packages(db)

    conn = db.connect()
    try:
        rows = {
            r[0]: (r[1], r[2])
            for r in conn.execute(
                "SELECT order_package_id, status, close_reason FROM order_packages"
            ).fetchall()
        }
    finally:
        conn.close()
    assert rows["op-shadow"][0] == "shadow_expired"
    assert rows["op-shadow"][1] == "shadow_no_execute"
    assert rows["op-live"][0] == "orphaned"
