"""Reverse-reconciler orphan RE-ATTACH (return an adopted orphan to its
originating strategy for monitoring, instead of a bare orphan_adopt row).

Covers _adopt_orphan_position's two paths:
  * confident recovery of the originating order package → trade attributed to
    that strategy, carries the package's SL/TP, package reopened + re-linked
    (so run_monitor_tick's monitor() governs it);
  * no confident match → fallback bare orphan_adopt (NULL SL/TP).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from src.runtime.order_monitor import (
    _adopt_orphan_position,
    _reattach_adopted_orphans,
    _resolve_account_class,
)
from src.units.db.database import Database
from tests.fixtures.real_schema_db import (
    insert_order_package as _insert_package,
    insert_trade as _insert_trade,
    make_canonical_db,
)


def _orphan_adopt_trade(db: Database, **over) -> int:
    fields = {
        "timestamp": "2026-06-14T06:30:00Z", "symbol": "MHG",
        "direction": "long", "entry_price": 6.40, "position_size": 3.0,
        "status": "open", "is_backtest": 0, "is_demo": 0,
        "strategy_name": "orphan_adopt", "setup_type": "adopted_orphan",
        "account_id": "ib_paper",
    }
    fields.update(over)
    return int(_insert_trade(db.db_path, **fields))


def _db(tmp_path: Path) -> Database:
    path = tmp_path / "trade_journal.db"
    make_canonical_db(path)
    return Database(str(path))


def _trade_row(db: Database, trade_id: int) -> dict:
    conn = sqlite3.connect(db.db_path)
    conn.row_factory = sqlite3.Row
    try:
        r = conn.execute("SELECT * FROM trades WHERE id = ?", [trade_id]).fetchone()
        return dict(r)
    finally:
        conn.close()


def _pkg_row(db: Database, opid: str) -> dict:
    conn = sqlite3.connect(db.db_path)
    conn.row_factory = sqlite3.Row
    try:
        r = conn.execute(
            "SELECT * FROM order_packages WHERE order_package_id = ?", [opid]
        ).fetchone()
        return dict(r)
    finally:
        conn.close()


def test_reattaches_orphan_to_originating_strategy(tmp_path: Path):
    db = _db(tmp_path)
    # The closed package that originally opened the (now-orphan) MHG position.
    _insert_package(
        db.db_path, order_package_id="op-mhg", strategy_name="mhg_pullback_1d",
        symbol="MHG", direction="long", entry=6.40, sl=6.05, tp=7.03,
        status="closed", close_reason="reconciler",
        created_at="2026-06-14T06:00:00Z",
    )

    tid = _adopt_orphan_position(
        db=db, account_id="ib_paper", symbol="MHG", direction="long",
        size=3.0, entry_price=6.40,
    )

    trade = _trade_row(db, tid)
    # Attributed to the originating strategy (NOT orphan_adopt), with its SL/TP.
    assert trade["strategy_name"] == "mhg_pullback_1d"
    assert trade["setup_type"] == "adopted_orphan"
    assert trade["stop_loss"] == 6.05
    assert trade["take_profit_1"] == 7.03
    assert trade["status"] == "open"
    # The original package is reopened + re-linked so the monitor loop finds it.
    pkg = _pkg_row(db, "op-mhg")
    assert pkg["status"] == "open"
    assert str(pkg["linked_trade_id"]) == str(tid)


def test_direction_normalised_buy_matches_long(tmp_path: Path):
    db = _db(tmp_path)
    _insert_package(
        db.db_path, order_package_id="op-buy", strategy_name="trend_donchian",
        symbol="BTCUSDT", direction="buy", entry=80000.0, sl=79000.0, tp=82000.0,
        status="closed", created_at="2026-06-14T06:00:00Z",
    )
    tid = _adopt_orphan_position(
        db=db, account_id="bybit_2", symbol="BTCUSDT", direction="long",
        size=0.01, entry_price=80050.0,  # within 2% of 80000
    )
    assert _trade_row(db, tid)["strategy_name"] == "trend_donchian"


def test_no_confident_match_falls_back_to_orphan_adopt(tmp_path: Path):
    db = _db(tmp_path)
    # A package for the same symbol but a far-off entry (>2%) — not confident.
    _insert_package(
        db.db_path, order_package_id="op-far", strategy_name="mhg_pullback_1d",
        symbol="MHG", direction="long", entry=5.00, sl=4.80, tp=5.50,
        status="closed", created_at="2026-06-14T06:00:00Z",
    )
    tid = _adopt_orphan_position(
        db=db, account_id="ib_paper", symbol="MHG", direction="long",
        size=3.0, entry_price=6.40,
    )
    trade = _trade_row(db, tid)
    assert trade["strategy_name"] == "orphan_adopt"
    assert trade["stop_loss"] is None
    assert trade["take_profit_1"] is None
    # The far-off package is untouched (still closed).
    assert _pkg_row(db, "op-far")["status"] == "closed"


def test_self_heal_reattaches_existing_orphan_adopt_row(tmp_path: Path):
    """An already-adopted orphan_adopt row (created before the fix) is driven
    back to its strategy on the next reconcile pass — orphan_adopt is a
    problem state, not a resting status."""
    db = _db(tmp_path)
    tid = _orphan_adopt_trade(db)  # open orphan_adopt MHG row, no SL/TP
    _insert_package(
        db.db_path, order_package_id="op-mhg", strategy_name="mhg_pullback_1d",
        symbol="MHG", direction="long", entry=6.40, sl=6.05, tp=7.03,
        status="closed", created_at="2026-06-14T06:00:00Z",
    )
    summary: dict = {}
    _reattach_adopted_orphans(db, summary)

    trade = _trade_row(db, tid)
    assert trade["strategy_name"] == "mhg_pullback_1d"
    assert trade["stop_loss"] == 6.05
    assert trade["take_profit_1"] == 7.03
    assert summary.get("reattached_existing") == 1
    pkg = _pkg_row(db, "op-mhg")
    assert pkg["status"] == "open"
    assert str(pkg["linked_trade_id"]) == str(tid)


def test_self_heal_skips_unrecoverable_orphan(tmp_path: Path):
    """No recoverable package → ``_reattach_adopted_orphans`` leaves the row
    untouched (reattach-only). The flatten of a still-alive un-attributable
    orphan happens in the per-account pass — see test_reverse_reconciler.py."""
    db = _db(tmp_path)
    tid = _orphan_adopt_trade(db)
    summary: dict = {}
    _reattach_adopted_orphans(db, summary)
    assert _trade_row(db, tid)["strategy_name"] == "orphan_adopt"
    assert _trade_row(db, tid)["status"] == "open"
    assert summary.get("reattached_existing", 0) == 0


# ---------------------------------------------------------------------------
# WC-2: paper/real_money + order_package_id stamping at the adopt insert.
# A PAPER adopted-orphan must not be mis-classified as real_money (the column
# defaults — is_demo=0, account_class=NULL→real_money — leak it into real-money
# PnL/stats); the recovered branch must also link the trade to its package via
# the canonical trades.order_package_id.
# ---------------------------------------------------------------------------


def test_resolve_account_class_canonical_coercion():
    """Mirror execute.py: paper→paper, real_money→real_money, missing/invalid
    →real_money, unknown account→real_money, lookup failure→real_money."""
    cfgs = {
        "p": {"account_class": "Paper"},          # case-insensitive
        "r": {"account_class": "real_money"},
        "blank": {"account_class": ""},            # falsy → real_money
        "weird": {"account_class": "demo"},        # invalid → real_money
        "none": {},                                # absent → real_money
    }
    assert _resolve_account_class("p", cfgs) == "paper"
    assert _resolve_account_class("r", cfgs) == "real_money"
    assert _resolve_account_class("blank", cfgs) == "real_money"
    assert _resolve_account_class("weird", cfgs) == "real_money"
    assert _resolve_account_class("none", cfgs) == "real_money"
    assert _resolve_account_class("missing_account", cfgs) == "real_money"
    # Best-effort: a malformed cfgs map never raises — falls back to real_money.
    assert _resolve_account_class("p", cfgs={"p": "not-a-dict"}) == "real_money"


def test_recovered_branch_paper_account_stamps_paper_and_links_package(tmp_path: Path):
    """(a) recovered branch on a PAPER account (ib_paper, account_class:paper in
    config/accounts.yaml) → account_class='paper', is_demo=1, and
    order_package_id == the recovered package id."""
    db = _db(tmp_path)
    _insert_package(
        db.db_path, order_package_id="op-mhg", strategy_name="mhg_pullback_1d",
        symbol="MHG", direction="long", entry=6.40, sl=6.05, tp=7.03,
        status="closed", created_at="2026-06-14T06:00:00Z",
    )
    tid = _adopt_orphan_position(
        db=db, account_id="ib_paper", symbol="MHG", direction="long",
        size=3.0, entry_price=6.40,
    )
    trade = _trade_row(db, tid)
    assert trade["strategy_name"] == "mhg_pullback_1d"   # recovered branch
    assert trade["account_class"] == "paper"
    assert int(trade["is_demo"]) == 1
    assert trade["order_package_id"] == "op-mhg"
    # The existing re-link write-back is unchanged.
    assert str(_pkg_row(db, "op-mhg")["linked_trade_id"]) == str(tid)


def test_recovered_branch_real_money_account_stamps_real_and_links_package(tmp_path: Path):
    """(b) recovered branch on a REAL_MONEY account (bybit_2, account_class:
    real_money in config/accounts.yaml) → account_class='real_money',
    is_demo=0, order_package_id set."""
    db = _db(tmp_path)
    _insert_package(
        db.db_path, order_package_id="op-btc", strategy_name="trend_donchian",
        symbol="BTCUSDT", direction="long", entry=80000.0, sl=79000.0, tp=82000.0,
        status="closed", created_at="2026-06-14T06:00:00Z",
    )
    tid = _adopt_orphan_position(
        db=db, account_id="bybit_2", symbol="BTCUSDT", direction="long",
        size=0.01, entry_price=80050.0,
    )
    trade = _trade_row(db, tid)
    assert trade["strategy_name"] == "trend_donchian"    # recovered branch
    assert trade["account_class"] == "real_money"
    assert int(trade["is_demo"]) == 0
    assert trade["order_package_id"] == "op-btc"


def test_bare_branch_stamps_account_class_with_null_order_package(tmp_path: Path):
    """(c) bare branch (no recoverable package) → account_class stamped from the
    account, is_demo consistent, order_package_id NULL (no package)."""
    # PAPER account, no package recovered → bare orphan_adopt, paper-stamped.
    db = _db(tmp_path)
    tid = _adopt_orphan_position(
        db=db, account_id="ib_paper", symbol="MHG", direction="long",
        size=3.0, entry_price=6.40,
    )
    trade = _trade_row(db, tid)
    assert trade["strategy_name"] == "orphan_adopt"      # bare branch
    assert trade["account_class"] == "paper"
    assert int(trade["is_demo"]) == 1
    assert trade["order_package_id"] is None             # no package → NULL

    # REAL_MONEY account, no package recovered → bare, real_money-stamped.
    rm_dir = tmp_path / "rm"
    rm_dir.mkdir()
    db2 = _db(rm_dir)
    tid2 = _adopt_orphan_position(
        db=db2, account_id="bybit_2", symbol="BTCUSDT", direction="long",
        size=0.01, entry_price=80050.0,
    )
    trade2 = _trade_row(db2, tid2)
    assert trade2["strategy_name"] == "orphan_adopt"
    assert trade2["account_class"] == "real_money"
    assert int(trade2["is_demo"]) == 0
    assert trade2["order_package_id"] is None


def test_wrong_direction_does_not_match(tmp_path: Path):
    db = _db(tmp_path)
    _insert_package(
        db.db_path, order_package_id="op-short", strategy_name="mhg_pullback_1d",
        symbol="MHG", direction="short", entry=6.40, sl=6.75, tp=5.80,
        status="closed", created_at="2026-06-14T06:00:00Z",
    )
    tid = _adopt_orphan_position(
        db=db, account_id="ib_paper", symbol="MHG", direction="long",
        size=3.0, entry_price=6.40,
    )
    assert _trade_row(db, tid)["strategy_name"] == "orphan_adopt"
