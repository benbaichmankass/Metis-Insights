"""Regression: ``order_packages.linked_trade_id`` must be stamped on a
successful entry so the strategy_monocle gate (pipeline.py's
``_has_open_package_for_strategy`` with ``linked_only=True``) actually
finds an open package to block re-fires on.

History: pre-fix, ``_log_new_order_package`` inserted the row with
``linked_trade_id=NULL`` and nothing ever updated it. The gate's
``linked_only=True`` filter therefore always returned empty, so every
tick generated a fresh entry signal even when a real position was
open on Bybit. The reconciler grace window (PR #501) didn't help
because the gate was already broken upstream of orphan-stamping.

Three contracts under test:

1. **Open path stamps the link.** Calling ``_log_trade_to_journal``
   with ``status='open'`` and a ``pkg.meta["order_package_id"]``
   updates ``order_packages.linked_trade_id`` with the int id of the
   newly-inserted ``trades`` row.

2. **Rejection paths must not stamp the link.** Calling with
   ``status='rejected'`` or ``status='exchange_rejected'`` leaves
   ``linked_trade_id`` ``NULL``. (Stamping a rejection row would
   suppress legitimate retries forever — the trade was never live.)

3. **Coordinator end-to-end.** ``_log_new_order_package`` writes the
   resolved id back to ``pkg.meta["order_package_id"]`` so the
   executor can read it. After ``multi_account_execute`` runs a
   successful entry through, the gate query returns the package.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.core.coordinator import OrderPackage, _log_new_order_package
from src.units.accounts.execute import _log_trade_to_journal
from src.units.db.database import Database


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Tmp trade journal pointed at by ``TRADE_JOURNAL_DB`` so both
    coordinator helpers and the executor write to the same file.
    """
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    db = Database(db_path=str(db_path))
    return db


def _pkg(order_package_id=None) -> OrderPackage:
    meta = {}
    if order_package_id is not None:
        meta["order_package_id"] = order_package_id
    return OrderPackage(
        symbol="BTCUSDT",
        direction="short",
        entry=80_000.0,
        sl=80_500.0,
        tp=79_000.0,
        confidence=1.0,
        strategy="vwap",
        meta=meta,
    )


def _account_cfg() -> dict:
    return {
        "account_id": "bybit_test",
        "exchange": "bybit",
        "api_key_env": "BYBIT_KEY_TEST",
    }


def _read_package(db, pkg_id):
    conn = db.connect()
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT order_package_id, status, linked_trade_id "
            "FROM order_packages WHERE order_package_id=?",
            (pkg_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _last_trade_id(db):
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT id FROM trades ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return int(row[0]) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Contract 1: open path stamps the link
# ---------------------------------------------------------------------------


class TestOpenPathStampsLink:
    """The headline contract: a successful entry's ``trades`` row id
    gets stamped onto ``order_packages.linked_trade_id``."""

    def test_open_status_with_pkg_meta_id_stamps_link(self, tmp_db):
        # Set up: insert an order package the way the coordinator does.
        pkg_id = "pkg-test-open-001"
        tmp_db.insert_order_package({
            "order_package_id": pkg_id,
            "strategy_name": "vwap",
            "symbol": "BTCUSDT",
            "direction": "short",
            "entry": 80_000.0,
            "sl": 80_500.0,
            "tp": 79_000.0,
            "confidence": 1.0,
            "status": "open",
        })
        pkg = _pkg(order_package_id=pkg_id)

        ok = _log_trade_to_journal(
            pkg, _account_cfg(),
            order={"qty": 0.005},
            trade_id="2210345722057156608",  # numeric Bybit orderId
            is_dry=False,
            status="open",
        )

        assert ok is True
        trade_row_id = _last_trade_id(tmp_db)
        assert trade_row_id is not None

        pkg_row = _read_package(tmp_db, pkg_id)
        assert pkg_row is not None
        assert pkg_row["status"] == "open"
        assert pkg_row["linked_trade_id"] == trade_row_id

    def test_open_status_without_pkg_meta_id_is_silent(self, tmp_db):
        """An open-path call without ``pkg.meta["order_package_id"]``
        must still log the trades row but not raise. Backward-compat
        for direct-executor test callers that don't go through the
        coordinator."""
        pkg = _pkg(order_package_id=None)

        ok = _log_trade_to_journal(
            pkg, _account_cfg(),
            order={"qty": 0.005},
            trade_id="orphan-orderId",
            is_dry=False,
            status="open",
        )

        assert ok is True
        # No package row was inserted; nothing to assert there.

    def test_open_path_link_failure_does_not_break_journal_write(
            self, tmp_db, monkeypatch):
        """If the package-link update raises, the trades row write must
        still succeed — observability writes never crash the order
        path."""
        pkg = _pkg(order_package_id="pkg-does-not-exist")

        # Patch update_order_package to blow up; verify the journal
        # write still returns True.
        from src.units.db import database as db_mod

        def _boom(self, pkg_id, updates):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            db_mod.Database, "update_order_package", _boom,
        )

        ok = _log_trade_to_journal(
            pkg, _account_cfg(),
            order={"qty": 0.005},
            trade_id="orderId-x",
            is_dry=False,
            status="open",
        )

        assert ok is True
        # The trades row landed even though the package-link update raised.
        assert _last_trade_id(tmp_db) is not None


# ---------------------------------------------------------------------------
# Contract 2: rejection paths must NOT stamp the link
# ---------------------------------------------------------------------------


class TestRejectionPathsDoNotStampLink:
    """Stamping a rejection row's id onto a package would make the
    monocle gate suppress every subsequent retry forever — the trade
    was never live, the package shouldn't be considered "linked"."""

    @pytest.mark.parametrize("status", ["rejected", "exchange_rejected"])
    def test_rejection_status_leaves_linked_trade_id_null(self, tmp_db, status):
        pkg_id = f"pkg-test-{status}"
        tmp_db.insert_order_package({
            "order_package_id": pkg_id,
            "strategy_name": "vwap",
            "symbol": "BTCUSDT",
            "direction": "short",
            "entry": 80_000.0,
            "sl": 80_500.0,
            "tp": 79_000.0,
            "confidence": 1.0,
            "status": "open",
        })
        pkg = _pkg(order_package_id=pkg_id)

        ok = _log_trade_to_journal(
            pkg, _account_cfg(),
            order={"qty": 0.0},  # rejection rows often log qty=0
            trade_id=f"{status}-abc",
            is_dry=False,
            status=status,
            reason="zero_balance: gate_balance=0.00 USD (no funds available to size against)",
        )

        assert ok is True
        pkg_row = _read_package(tmp_db, pkg_id)
        assert pkg_row is not None
        assert pkg_row["linked_trade_id"] is None, (
            f"{status} must NOT stamp linked_trade_id"
        )


# ---------------------------------------------------------------------------
# Contract 3: _log_new_order_package writes id back to pkg.meta
# ---------------------------------------------------------------------------


class TestLogNewOrderPackageWritesIdBack:
    """``_log_new_order_package`` must write the resolved
    ``order_package_id`` back to ``pkg.meta`` so the executor can read
    it on the way through. Pre-fix the id was returned but not
    stamped, so the executor had no way to wire the link."""

    def test_id_stamped_on_pkg_meta_when_meta_was_empty(self, tmp_db):
        pkg = _pkg(order_package_id=None)
        # pkg.meta starts as {} — no id available.
        assert "order_package_id" not in pkg.meta

        result = _log_new_order_package(pkg)

        assert result is not None
        assert pkg.meta["order_package_id"] == result

    def test_id_stamped_on_pkg_meta_when_meta_was_none(self, tmp_db):
        pkg = OrderPackage(
            symbol="BTCUSDT",
            direction="short",
            entry=80_000.0,
            sl=80_500.0,
            tp=79_000.0,
            confidence=1.0,
            strategy="vwap",
            meta=None,
        )
        assert pkg.meta is None

        result = _log_new_order_package(pkg)

        assert result is not None
        assert isinstance(pkg.meta, dict)
        assert pkg.meta["order_package_id"] == result

    def test_pre_existing_id_preserved(self, tmp_db):
        pkg = _pkg(order_package_id="pkg-caller-supplied-001")

        result = _log_new_order_package(pkg)

        assert result == "pkg-caller-supplied-001"
        assert pkg.meta["order_package_id"] == "pkg-caller-supplied-001"


# ---------------------------------------------------------------------------
# Contract 4: end-to-end gate behaviour
# ---------------------------------------------------------------------------


class TestStrategyMonocleGateAfterEntry:
    """Once a successful entry has wired ``linked_trade_id``, the
    strategy_monocle gate's ``linked_only=True`` query must find the
    open package and report it back."""

    def test_gate_finds_linked_open_package(self, tmp_db):
        # Simulate: package inserted, trade logged with open status,
        # link wired by _log_trade_to_journal.
        pkg_id = "pkg-gate-001"
        tmp_db.insert_order_package({
            "order_package_id": pkg_id,
            "strategy_name": "vwap",
            "symbol": "BTCUSDT",
            "direction": "short",
            "entry": 80_000.0,
            "sl": 80_500.0,
            "tp": 79_000.0,
            "confidence": 1.0,
            "status": "open",
        })
        pkg = _pkg(order_package_id=pkg_id)
        _log_trade_to_journal(
            pkg, _account_cfg(),
            order={"qty": 0.005},
            trade_id="2210345722057156608",
            is_dry=False,
            status="open",
        )

        # Mirror the gate's query.
        rows = tmp_db.get_order_packages_by_strategy(
            "vwap", status="open", linked_only=True, limit=1,
        )

        assert len(rows) == 1
        assert rows[0]["order_package_id"] == pkg_id
        assert rows[0]["linked_trade_id"] is not None

    def test_gate_skips_unlinked_open_package(self, tmp_db):
        """Belt-and-braces: a package that was logged but never had a
        successful trade landed must not be visible to the gate
        (BUG-046 contract — pre-existing, kept passing)."""
        pkg_id = "pkg-unlinked-001"
        tmp_db.insert_order_package({
            "order_package_id": pkg_id,
            "strategy_name": "vwap",
            "symbol": "BTCUSDT",
            "direction": "short",
            "entry": 80_000.0,
            "sl": 80_500.0,
            "tp": 79_000.0,
            "confidence": 1.0,
            "status": "open",
        })

        rows = tmp_db.get_order_packages_by_strategy(
            "vwap", status="open", linked_only=True, limit=1,
        )

        assert rows == []
