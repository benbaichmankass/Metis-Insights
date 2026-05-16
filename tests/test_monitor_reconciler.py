"""Monitor-loop write-back reconciler — SSOT-from-Bybit (issue #502).

Pins the contract for ``src.runtime.order_monitor._reconcile_open_trades``
under the per-orderId reconciliation path. Each DB-open trade is matched
against ITS specific Bybit order via ``account_order_status``; the
aggregate ``account_open_positions`` view is only the secondary cross-
check used to disambiguate "filled, position still open" vs. "filled,
position flat".

The grace window (RECONCILER_GRACE_SECONDS) and the
``_exchange_position_set`` side-normalisation helper stay in their old
shape — both are exercised below.
"""
from __future__ import annotations

import json
import textwrap
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.runtime.order_monitor import (
    _classify_orphan_close,
    _exchange_position_set,
    _extract_trade_id_from_notes,
    _is_numeric_order_id,
    _is_real_order_id,
    _mark_orphaned,
    _parse_created_at,
    _reconcile_open_trades,
    _sweep_stuck_linked_packages,
    _sweep_unlinked_packages,
    _watchdog_stuck_strategies,
)
from src.units.db.database import Database


_ACCOUNTS_YAML = textwrap.dedent("""\
    accounts:
      bybit_2:
        type: regular
        exchange: bybit
        api_key_env: BYBIT_KEY_2
        mode: live
        strategies: [vwap]
        risk:
          max_dd_pct: 0.05
          daily_usd: 100
          pos_size: 500
          risk_pct: 0.01
          min_balance_usd: 50
      bybit_dry:
        type: regular
        exchange: bybit
        api_key_env: BYBIT_KEY_DRY
        mode: dry_run
        strategies: [vwap]
        risk: {max_dd_pct: 0.05, daily_usd: 100, pos_size: 500, risk_pct: 0.01, min_balance_usd: 50}
""")


# Module-level monotonic counter used by the default fixture so each
# inserted trade gets a unique numeric ``trade_id`` — matches the
# real Bybit V5 orderId shape (digit-only string) and keeps the
# SSOT reconciler's per-orderId path active by default.
_TRADE_ID_COUNTER = [1_842_564_317_108_924_672]


def _next_numeric_trade_id() -> str:
    _TRADE_ID_COUNTER[0] += 1
    return str(_TRADE_ID_COUNTER[0])


def _insert_trade(
    db,
    *,
    account_id="bybit_2",
    symbol="BTCUSDT",
    direction="long",
    status="open",
    notes_pkg_id=None,
    created_at=None,
    age_seconds=None,
    trade_id=None,
):
    """Insert an open trade and return its DB id. Mirrors the shape
    written by ``_log_trade_to_journal`` so the reconciler reads
    realistic rows.

    By default rows are backdated 1 hour into the past so they're
    older than the reconciler's grace window — tests that want to
    pin freshness behaviour pass ``age_seconds`` (or an explicit
    ``created_at``) directly.

    The default ``trade_id`` is a unique numeric string (issue #502
    requires the SSOT reconciler to skip non-numeric ids); pass
    ``trade_id`` explicitly when a test needs the rejection-id shape
    or a specific numeric value.
    """
    notes = {"trade_id": trade_id or _next_numeric_trade_id()}
    if notes_pkg_id:
        notes["order_package_id"] = notes_pkg_id
    if created_at is None:
        secs = 3600 if age_seconds is None else int(age_seconds)
        created_at = (
            datetime.now(timezone.utc) - timedelta(seconds=secs)
        ).isoformat()
    db.insert_trade({
        "timestamp": "2026-05-03T20:00:00+00:00",
        "symbol": symbol,
        "direction": direction,
        "entry_price": 80000.0,
        "stop_loss": 79500.0,
        "take_profit_1": 80500.0,
        "position_size": 0.005,
        "setup_type": "vwap",
        "entry_reason": "vwap signal",
        "status": status,
        "is_backtest": 0,
        "strategy_name": "vwap",
        "account_id": account_id,
        "notes": json.dumps(notes),
        "created_at": created_at,
    })
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT id FROM trades ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return int(row[0])
    finally:
        conn.close()


def _live_status(order_id: str = "stub", **overrides):
    """Build a fake ``account_order_status`` response for a still-live
    order (Bybit ``orderStatus='New'``)."""
    payload = {
        "order_id": order_id, "status": "New",
        "filled_qty": 0.0, "avg_price": 0.0, "exec_time": None,
    }
    payload.update(overrides)
    return payload


def _filled_status(order_id: str = "stub", **overrides):
    """Build a fake ``account_order_status`` response for a fully
    filled order with realistic fill data."""
    payload = {
        "order_id": order_id, "status": "Filled",
        "filled_qty": 0.005, "avg_price": 80123.45,
        "exec_time": "1762620000000",
    }
    payload.update(overrides)
    return payload


def _not_found_status(order_id: str = "stub"):
    return {
        "order_id": order_id, "status": "not_found",
        "filled_qty": 0.0, "avg_price": 0.0, "exec_time": None,
    }


def _insert_package(db, pkg_id="pkg-test-001", linked_trade_id=None):
    db.insert_order_package({
        "order_package_id": pkg_id,
        "strategy_name": "vwap",
        "symbol": "BTCUSDT",
        "direction": "long",
        "entry": 80000.0,
        "sl": 79500.0,
        "tp": 80500.0,
        "confidence": 0.42,
        "status": "open",
        "linked_trade_id": linked_trade_id,
        "meta": {},
    })


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Tmp trade journal + accounts.yaml.

    The reconciler reads accounts.yaml from
    ``<repo>/config/accounts.yaml`` via its own ``_REPO_ROOT`` constant.
    Patch the loader so each test gets an isolated config.
    """
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    db = Database(db_path=str(db_path))

    def _fake_cfg_loader():
        return {
            "bybit_2": {
                "account_id": "bybit_2",
                "exchange": "bybit",
                "api_key_env": "BYBIT_KEY_2",
                "api_secret_env": None,
                "mode": "live",
            },
            "bybit_dry": {
                "account_id": "bybit_dry",
                "exchange": "bybit",
                "api_key_env": "BYBIT_KEY_DRY",
                "api_secret_env": None,
                "mode": "dry_run",
            },
        }

    monkeypatch.setattr(
        "src.runtime.order_monitor._load_account_cfgs_for_reconcile",
        _fake_cfg_loader,
    )
    monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "true")
    return db


def _read_trade(db, trade_id):
    conn = db.connect()
    try:
        conn.row_factory = __import__("sqlite3").Row
        row = conn.execute(
            "SELECT id, status, exit_reason FROM trades WHERE id=?",
            (trade_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _read_trade_full(db, trade_id):
    conn = db.connect()
    try:
        conn.row_factory = __import__("sqlite3").Row
        row = conn.execute(
            "SELECT id, status, exit_reason, exit_price, notes "
            "FROM trades WHERE id=?",
            (trade_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _read_package(db, pkg_id):
    conn = db.connect()
    try:
        conn.row_factory = __import__("sqlite3").Row
        row = conn.execute(
            "SELECT order_package_id, status, close_reason FROM order_packages "
            "WHERE order_package_id=?",
            (pkg_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Legacy contracts — preserved under the SSOT path
# ---------------------------------------------------------------------------


def test_empty_trades_table_is_noop(tmp_db):
    summary = _reconcile_open_trades(tmp_db)
    assert summary["checked"] == 0
    assert summary["orphaned"] == 0


def test_db_open_orderid_not_found_marks_orphaned_and_pings(
    tmp_db, tmp_path, monkeypatch,
):
    """The headline orphan contract migrated to SSOT semantics: a
    DB-open trade whose orderId Bybit denies any record of gets
    re-tagged ``orphaned`` and a diagnostic ping is enqueued.
    """
    pkg_id = "pkg-orphan-001"
    _insert_package(tmp_db, pkg_id=pkg_id)
    trade_id = _insert_trade(tmp_db, notes_pkg_id=pkg_id,
                             trade_id="1900000000000000001")
    tmp_db.update_order_package(pkg_id, {"linked_trade_id": trade_id})

    pings_dir = tmp_path / "pending_pings"
    monkeypatch.setattr(
        "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
        pings_dir,
    )
    with patch(
        "src.units.accounts.clients.account_order_status",
        return_value=_not_found_status("1900000000000000001"),
    ):
        summary = _reconcile_open_trades(tmp_db)

    assert summary["orphaned"] == 1
    trade = _read_trade(tmp_db, trade_id)
    assert trade["status"] == "orphaned"
    assert trade["exit_reason"] == "reconciler"

    pkg = _read_package(tmp_db, pkg_id)
    assert pkg["status"] == "closed"
    assert pkg["close_reason"] == "reconciler"

    queued = sorted(pings_dir.glob("*.json"))
    assert len(queued) == 1
    payload = json.loads(queued[0].read_text())
    body = payload["body"]
    assert payload["priority"] == "high"
    assert "Account: bybit_2" in body
    assert "BTCUSDT" in body
    assert "long" in body
    assert f"DB trade id: {trade_id}" in body
    assert pkg_id in body


def test_db_open_orderid_live_leaves_row_alone(tmp_db):
    """Bybit reports the order is still ``New`` (or ``PartiallyFilled``)
    → the DB row stays ``open``."""
    trade_id = _insert_trade(tmp_db, direction="long")

    with patch(
        "src.units.accounts.clients.account_order_status",
        return_value=_live_status(),
    ):
        summary = _reconcile_open_trades(tmp_db)

    assert summary["orphaned"] == 0
    assert summary["closed"] == 0
    assert _read_trade(tmp_db, trade_id)["status"] == "open"


def test_missing_creds_skips_row_no_orphan(tmp_db):
    """``account_order_status`` returns None on creds-missing /
    exchange-side error → reconciler skips the row this tick instead
    of orphaning it."""
    trade_id = _insert_trade(tmp_db)

    with patch(
        "src.units.accounts.clients.account_order_status",
        return_value=None,
    ):
        summary = _reconcile_open_trades(tmp_db)

    assert summary["orphaned"] == 0
    assert summary["skipped_no_creds"] == 1
    assert _read_trade(tmp_db, trade_id)["status"] == "open"


def test_dry_run_account_is_skipped(tmp_db):
    trade_id = _insert_trade(tmp_db, account_id="bybit_dry")

    with patch(
        "src.units.accounts.clients.account_order_status",
        side_effect=AssertionError("must not call account_order_status for dry-run"),
    ), patch(
        "src.units.accounts.clients.account_open_positions",
        side_effect=AssertionError("must not call account_open_positions for dry-run"),
    ):
        summary = _reconcile_open_trades(tmp_db)

    assert summary["orphaned"] == 0
    assert summary["skipped_dry"] == 1
    assert _read_trade(tmp_db, trade_id)["status"] == "open"


def test_disabled_flag_is_noop(tmp_db, monkeypatch):
    _insert_trade(tmp_db)
    monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "false")

    with patch(
        "src.units.accounts.clients.account_order_status",
        side_effect=AssertionError("reconciler must not run when disabled"),
    ):
        summary = _reconcile_open_trades(tmp_db)

    assert summary == {
        "checked": 0, "orphaned": 0, "closed": 0,
        "skipped_dry": 0, "skipped_no_creds": 0,
        "skipped_no_cfg": 0, "skipped_recent": 0,
        "skipped_non_numeric": 0, "errors": 0,
    }


def test_unset_flag_is_noop(tmp_db, monkeypatch):
    _insert_trade(tmp_db)
    monkeypatch.delenv("MONITOR_RECONCILE_ENABLED", raising=False)

    with patch(
        "src.units.accounts.clients.account_order_status",
        side_effect=AssertionError("reconciler must not run when env var unset"),
    ):
        summary = _reconcile_open_trades(tmp_db)

    assert summary["checked"] == 0
    assert summary["orphaned"] == 0


def test_multiple_orphans_in_same_account_all_swept(
    tmp_db, tmp_path, monkeypatch,
):
    ids = [
        _insert_trade(tmp_db, symbol="BTCUSDT"),
        _insert_trade(tmp_db, symbol="ETHUSDT"),
        _insert_trade(tmp_db, symbol="SOLUSDT"),
    ]

    pings_dir = tmp_path / "pings"
    monkeypatch.setattr(
        "src.runtime.execution_diagnostics.PENDING_PINGS_DIR", pings_dir,
    )
    with patch(
        "src.units.accounts.clients.account_order_status",
        side_effect=lambda cfg, oid: _not_found_status(str(oid)),
    ):
        summary = _reconcile_open_trades(tmp_db)

    assert summary["orphaned"] == 3
    for tid in ids:
        assert _read_trade(tmp_db, tid)["status"] == "orphaned"
    assert len(list(pings_dir.glob("*.json"))) == 3


def test_per_orderid_dedup_long_orphaned_short_kept(tmp_db, tmp_path, monkeypatch):
    """Two DB rows on the same ``(symbol, side=long)`` are no longer
    forced to share a verdict — under SSOT each row carries its own
    Bybit orderId and is reconciled independently. Here the long
    side's orderId is ``not_found`` while the short side's orderId is
    still ``New`` on Bybit; only the long row gets orphaned.
    """
    monkeypatch.setattr(
        "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
        tmp_path / "pings",
    )
    long_id = _insert_trade(
        tmp_db, symbol="BTCUSDT", direction="long",
        trade_id="1900000000000001000",
    )
    short_id = _insert_trade(
        tmp_db, symbol="BTCUSDT", direction="short",
        trade_id="1900000000000001001",
    )

    def fake_status(cfg, order_id):
        if order_id == "1900000000000001000":
            return _not_found_status(order_id)
        return _live_status(order_id)

    with patch(
        "src.units.accounts.clients.account_order_status",
        side_effect=fake_status,
    ):
        summary = _reconcile_open_trades(tmp_db)

    assert summary["orphaned"] == 1
    assert _read_trade(tmp_db, long_id)["status"] == "orphaned"
    assert _read_trade(tmp_db, short_id)["status"] == "open"


def test_two_consecutive_runs_idempotent(tmp_db, tmp_path, monkeypatch):
    trade_id = _insert_trade(tmp_db)
    monkeypatch.setattr(
        "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
        tmp_path / "pings",
    )

    with patch(
        "src.units.accounts.clients.account_order_status",
        side_effect=lambda cfg, oid: _not_found_status(str(oid)),
    ):
        s1 = _reconcile_open_trades(tmp_db)
        s2 = _reconcile_open_trades(tmp_db)

    assert s1["orphaned"] == 1
    assert s2["orphaned"] == 0
    assert s2["checked"] == 0
    assert _read_trade(tmp_db, trade_id)["status"] == "orphaned"


# ---------------------------------------------------------------------------
# SSOT decision matrix (issue #502)
# ---------------------------------------------------------------------------


class TestSSOTReconciler:
    """Issue #502 decision matrix — each row pins one cell:

      | Bybit response                       | Verdict                     |
      |--------------------------------------|-----------------------------|
      | order open / partially filled        | leave DB row 'open'         |
      | order filled, position open          | leave DB row 'open'         |
      | order filled, position closed        | mark 'closed' + real exit   |
      | order not found                      | mark 'orphaned'             |
      | terminal w/ zero fills (Cancelled)   | mark 'orphaned' (same as    |
      |                                      |   not_found — no position   |
      |                                      |   ever opened)              |
      | API read failure                     | skip (conservative)         |
      | non-numeric trade_id                 | skip (never live)           |
    """

    def test_orderid_still_open_no_change(self, tmp_db):
        trade_id = _insert_trade(tmp_db, trade_id="2000000000000000001")
        with patch(
            "src.units.accounts.clients.account_order_status",
            return_value=_live_status("2000000000000000001"),
        ), patch(
            "src.units.accounts.clients.account_open_positions",
            side_effect=AssertionError(
                "live order must not trigger a position cross-check"),
        ):
            summary = _reconcile_open_trades(tmp_db)
        assert summary["orphaned"] == 0
        assert summary["closed"] == 0
        assert _read_trade(tmp_db, trade_id)["status"] == "open"

    def test_orderid_filled_position_flat_marks_closed_without_exit_price(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """When the reconciler sees the entry order filled + position
        flat, mark the trade ``closed`` but DO NOT write
        ``exit_price`` — the lookup returned the entry order's
        avg_price, not the close fill (Bybit's broker-side SL fires
        a separate close order the bot does not currently track).
        ``exit_price`` stays NULL and ``notes.exit_price_source``
        flags it as unreliable so PnL consumers can filter.

        Pre-2026-05-16 this test asserted ``exit_price = mocked
        avg_price``, but that was only safe because
        ``_is_numeric_order_id`` rejected UUID-format orderIds so
        the path never actually fired in production. Now that the
        gate is fixed, the close-fill recovery is a follow-up PR
        (Bybit V5 closed-pnl / execution-list integration).
        """
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            tmp_path / "pings",
        )
        trade_id = _insert_trade(tmp_db, trade_id="2000000000000000002")

        with patch(
            "src.units.accounts.clients.account_order_status",
            return_value=_filled_status(
                "2000000000000000002", avg_price=80123.45,
                exec_time="1762620000000",
            ),
        ), patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[],
        ):
            summary = _reconcile_open_trades(tmp_db)

        assert summary["closed"] == 1
        assert summary["orphaned"] == 0
        row = _read_trade_full(tmp_db, trade_id)
        assert row["status"] == "closed"
        assert row["exit_reason"] == "reconciler_filled"
        assert row["exit_price"] is None
        notes = json.loads(row["notes"])
        assert notes["closed_by"] == "monitor_reconciler"
        assert notes["closed_at"] == "1762620000000"
        assert notes["exit_price_source"] == "entry_order_avg_price_unreliable"

    def test_uuid_format_trade_id_is_reconciled(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """Smoking-gun regression: a Bybit V5 UUID-format trade_id
        (the shape that produced the 11/11 vwap orphan cluster on
        bybit_2 since 2026-05-15) must flow through the reconciler
        instead of being silently dropped as ``skipped_non_numeric``.
        """
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            tmp_path / "pings",
        )
        bybit_uuid = "bbfcde38-82db-4621-b400-9b9a7fa0b313"
        trade_id = _insert_trade(tmp_db, trade_id=bybit_uuid)

        with patch(
            "src.units.accounts.clients.account_order_status",
            return_value=_filled_status(bybit_uuid, avg_price=80000.0),
        ), patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[],
        ):
            summary = _reconcile_open_trades(tmp_db)

        assert summary["skipped_non_numeric"] == 0
        assert summary["closed"] == 1
        assert _read_trade(tmp_db, trade_id)["status"] == "closed"

    def test_orderid_filled_position_open_leaves_row_open(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            tmp_path / "pings",
        )
        trade_id = _insert_trade(
            tmp_db, trade_id="2000000000000000003", direction="long",
        )
        with patch(
            "src.units.accounts.clients.account_order_status",
            return_value=_filled_status("2000000000000000003"),
        ), patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[{
                "symbol": "BTCUSDT", "side": "Buy", "size": 0.005,
                "entry_price": 80100.0, "unrealised_pnl": 0,
            }],
        ):
            summary = _reconcile_open_trades(tmp_db)
        assert summary["closed"] == 0
        assert summary["orphaned"] == 0
        assert _read_trade(tmp_db, trade_id)["status"] == "open"

    def test_orderid_not_found_marks_orphaned(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            tmp_path / "pings",
        )
        trade_id = _insert_trade(tmp_db, trade_id="2000000000000000004")
        with patch(
            "src.units.accounts.clients.account_order_status",
            return_value=_not_found_status("2000000000000000004"),
        ), patch(
            "src.units.accounts.clients.account_open_positions",
            side_effect=AssertionError(
                "not_found must not need a position cross-check"),
        ):
            summary = _reconcile_open_trades(tmp_db)
        assert summary["orphaned"] == 1
        assert _read_trade(tmp_db, trade_id)["status"] == "orphaned"

    def test_non_numeric_trade_id_is_skipped(self, tmp_db):
        trade_id = _insert_trade(
            tmp_db, trade_id="rejected-deadbeefcafe",
        )
        with patch(
            "src.units.accounts.clients.account_order_status",
            side_effect=AssertionError(
                "must not look up non-numeric trade_id"),
        ):
            summary = _reconcile_open_trades(tmp_db)
        assert summary["skipped_non_numeric"] == 1
        assert summary["orphaned"] == 0
        assert _read_trade(tmp_db, trade_id)["status"] == "open"

    def test_dry_synthesised_trade_id_is_skipped(self, tmp_db):
        """``dry-<hex>`` ids written by the dry-run path of
        ``_log_trade_to_journal`` are non-numeric and must not be
        reconciled."""
        trade_id = _insert_trade(tmp_db, trade_id="dry-abc123def456")
        with patch(
            "src.units.accounts.clients.account_order_status",
            side_effect=AssertionError(
                "must not look up dry-run trade_id"),
        ):
            summary = _reconcile_open_trades(tmp_db)
        assert summary["skipped_non_numeric"] == 1
        assert _read_trade(tmp_db, trade_id)["status"] == "open"

    def test_api_read_failure_skips_row(self, tmp_db):
        trade_id = _insert_trade(tmp_db, trade_id="2000000000000000005")
        with patch(
            "src.units.accounts.clients.account_order_status",
            return_value=None,
        ):
            summary = _reconcile_open_trades(tmp_db)
        assert summary["skipped_no_creds"] == 1
        assert summary["orphaned"] == 0
        assert summary["closed"] == 0
        assert _read_trade(tmp_db, trade_id)["status"] == "open"

    def test_terminal_cancelled_no_fills_treated_as_orphan(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """Bybit reports a terminal Cancelled state with cumExecQty=0
        — no real position ever opened, so the row is orphaned the
        same as ``not_found``.
        """
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            tmp_path / "pings",
        )
        trade_id = _insert_trade(tmp_db, trade_id="2000000000000000006")
        with patch(
            "src.units.accounts.clients.account_order_status",
            return_value={
                "order_id": "2000000000000000006", "status": "Cancelled",
                "filled_qty": 0.0, "avg_price": 0.0, "exec_time": None,
            },
        ):
            summary = _reconcile_open_trades(tmp_db)
        assert summary["orphaned"] == 1
        assert _read_trade(tmp_db, trade_id)["status"] == "orphaned"

    def test_partial_fill_live_status_is_left_open(self, tmp_db):
        trade_id = _insert_trade(tmp_db, trade_id="2000000000000000007")
        with patch(
            "src.units.accounts.clients.account_order_status",
            return_value={
                "order_id": "2000000000000000007",
                "status": "PartiallyFilled",
                "filled_qty": 0.002, "avg_price": 80050.0,
                "exec_time": None,
            },
        ), patch(
            "src.units.accounts.clients.account_open_positions",
            side_effect=AssertionError(
                "partially-filled live order must not trigger a position "
                "cross-check"),
        ):
            summary = _reconcile_open_trades(tmp_db)
        assert summary["closed"] == 0
        assert summary["orphaned"] == 0
        assert _read_trade(tmp_db, trade_id)["status"] == "open"

    def test_position_cross_check_is_cached_per_account(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """The reconciler must call ``account_open_positions`` at most
        once per account per tick, even when N filled orders need the
        cross-check (issue #502 perf invariant: a sweep with N open
        trades is at most N+1 API calls, not N×2).
        """
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            tmp_path / "pings",
        )
        for i in range(3):
            _insert_trade(
                tmp_db,
                trade_id=f"200000000000010000{i}",
                symbol=f"PAIR{i}USDT",
            )

        positions_calls = []

        def fake_positions(cfg):
            positions_calls.append(cfg)
            return []

        with patch(
            "src.units.accounts.clients.account_order_status",
            side_effect=lambda cfg, oid: _filled_status(str(oid)),
        ), patch(
            "src.units.accounts.clients.account_open_positions",
            side_effect=fake_positions,
        ):
            summary = _reconcile_open_trades(tmp_db)
        assert summary["closed"] == 3
        assert len(positions_calls) == 1

    def test_position_read_failure_after_filled_skips_row(self, tmp_db):
        """Order is filled but the cross-check ``account_open_positions``
        returns None (couldn't read) → skip the row this tick rather
        than mark it closed on a half-known view."""
        trade_id = _insert_trade(tmp_db, trade_id="2000000000000000010")
        with patch(
            "src.units.accounts.clients.account_order_status",
            return_value=_filled_status("2000000000000000010"),
        ), patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=None,
        ):
            summary = _reconcile_open_trades(tmp_db)
        assert summary["closed"] == 0
        assert summary["orphaned"] == 0
        assert summary["skipped_no_creds"] == 1
        assert _read_trade(tmp_db, trade_id)["status"] == "open"


# ---------------------------------------------------------------------------
# Position-set side normalisation helper (preserved from legacy reconciler;
# still exercised by the SSOT cross-check path)
# ---------------------------------------------------------------------------


class TestExchangePositionSet:
    """``_exchange_position_set`` must normalise bybit's Buy/Sell to
    the trade-journal's long/short so the (symbol, side) match is
    representation-invariant."""

    def test_bybit_buy_normalised_to_long(self):
        out = _exchange_position_set([
            {"symbol": "BTCUSDT", "side": "Buy"},
        ])
        assert out == {("BTCUSDT", "long")}

    def test_bybit_sell_normalised_to_short(self):
        out = _exchange_position_set([
            {"symbol": "BTCUSDT", "side": "Sell"},
        ])
        assert out == {("BTCUSDT", "short")}

    def test_already_canonical_short_passes_through(self):
        out = _exchange_position_set([
            {"symbol": "ETHUSDT", "side": "short"},
        ])
        assert out == {("ETHUSDT", "short")}

    def test_unknown_side_dropped_silently(self):
        out = _exchange_position_set([
            {"symbol": "BTCUSDT", "side": "weird"},
            {"symbol": "ETHUSDT", "side": "Buy"},
        ])
        assert out == {("ETHUSDT", "long")}

    def test_empty_or_none_returns_empty_set(self):
        assert _exchange_position_set([]) == set()
        assert _exchange_position_set(None) == set()


# ---------------------------------------------------------------------------
# Numeric-id + notes-extract helpers
# ---------------------------------------------------------------------------


class TestRealOrderIdAndNotesExtraction:
    """Regression coverage for ``_is_real_order_id`` — the gate the
    SSOT reconciler uses to decide whether ``notes.trade_id`` is a
    lookup key Bybit can resolve.

    Pre-2026-05-16 this gate was named ``_is_numeric_order_id`` and
    accepted only ``.isdigit()`` strings, silently rejecting every
    valid Bybit V5 UUID-format orderId. The current contract:
    accept anything that doesn't begin with a known-synthetic
    prefix (``dry-``, ``rejected-``, ``exchange_rejected-``, …).
    The legacy name is preserved as an alias for any out-of-tree
    caller.
    """

    def test_digit_only_bybit_orderid_accepted(self):
        assert _is_real_order_id("1842564317108924672") is True

    def test_uuid_format_bybit_orderid_accepted(self):
        # Real shape observed on bybit_2 vwap entries (linear perp,
        # diag #1252) — Bybit V5 stamps these for some flows.
        assert _is_real_order_id("bbfcde38-82db-4621-b400-9b9a7fa0b313") is True

    def test_rejected_prefix_skipped(self):
        assert _is_real_order_id("rejected-deadbeefcafe") is False

    def test_exchange_rejected_prefix_skipped(self):
        assert _is_real_order_id("exchange_rejected-deadbeef1234") is False

    def test_dry_prefix_skipped(self):
        assert _is_real_order_id("dry-abc123def456") is False
        assert _is_real_order_id("dry-bybit-abc1234567") is False
        assert _is_real_order_id("dry-velotrade-abc1234567") is False

    def test_open_closed_fallback_prefix_skipped(self):
        # ``_log_trade_to_journal`` writes ``{status}-<hex>`` when
        # the trade_id arg is None (legacy callers); the reconciler
        # must skip those.
        assert _is_real_order_id("open-abc123def456") is False
        assert _is_real_order_id("closed-abc123def456") is False

    def test_empty_string_rejected(self):
        assert _is_real_order_id("") is False
        assert _is_real_order_id("   ") is False

    def test_legacy_alias_resolves_to_same_callable(self):
        assert _is_numeric_order_id is _is_real_order_id

    def test_extract_trade_id_happy_path(self):
        notes = json.dumps({"trade_id": "1900000000000000001"})
        assert _extract_trade_id_from_notes(notes) == "1900000000000000001"

    def test_extract_trade_id_missing_key(self):
        notes = json.dumps({"other": "x"})
        assert _extract_trade_id_from_notes(notes) is None

    def test_extract_trade_id_handles_garbage_json(self):
        assert _extract_trade_id_from_notes("not-json") is None
        assert _extract_trade_id_from_notes("") is None
        assert _extract_trade_id_from_notes(None) is None


# ---------------------------------------------------------------------------
# BUG-049: _sweep_unlinked_packages (unchanged by issue #502)
# ---------------------------------------------------------------------------


class TestSweepUnlinkedPackages:
    """_sweep_unlinked_packages marks open packages with no linked_trade_id
    as orphaned (BUG-049 fix). Only affects rows older than 5 minutes."""

    def _insert_pkg(self, db, pkg_id, *, linked_trade_id=None,
                    strategy="vwap", status="open", age_minutes=10):
        """Insert a package with a synthetic created_at in the past."""
        conn = db.connect()
        try:
            conn.execute(
                "INSERT INTO order_packages "
                "(order_package_id, strategy_name, symbol, direction, "
                "entry, sl, tp, confidence, status, linked_trade_id, "
                "created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?, "
                "datetime('now', ? || ' minutes'), "
                "datetime('now', ? || ' minutes'))",
                (pkg_id, strategy, "BTCUSDT", "long",
                 80000.0, 79500.0, 80500.0, 0.42, status, linked_trade_id,
                 f"-{age_minutes}", f"-{age_minutes}"),
            )
            conn.commit()
        finally:
            conn.close()

    def test_old_unlinked_package_marked_orphaned(
            self, tmp_db, monkeypatch):
        monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "true")
        self._insert_pkg(tmp_db, "pkg-old-unlinked", age_minutes=10)
        affected = _sweep_unlinked_packages(tmp_db)
        assert affected == 1
        rows = tmp_db.get_order_packages_by_strategy("vwap")
        assert rows[0]["status"] == "orphaned"

    def test_recent_unlinked_package_not_swept(
            self, tmp_db, monkeypatch):
        """A package created less than 5 minutes ago with no linked trade
        is still being dispatched — do not orphan it prematurely."""
        monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "true")
        self._insert_pkg(tmp_db, "pkg-new-unlinked", age_minutes=1)
        affected = _sweep_unlinked_packages(tmp_db)
        assert affected == 0
        rows = tmp_db.get_order_packages_by_strategy("vwap")
        assert rows[0]["status"] == "open"

    def test_linked_open_package_not_swept(self, tmp_db, monkeypatch):
        """A linked open package (real broker position) must never be
        touched by the unlinked sweep."""
        monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "true")
        self._insert_pkg(tmp_db, "pkg-linked", linked_trade_id=7,
                         age_minutes=60)
        affected = _sweep_unlinked_packages(tmp_db)
        assert affected == 0
        rows = tmp_db.get_order_packages_by_strategy("vwap")
        assert rows[0]["status"] == "open"

    def test_noop_when_reconcile_disabled(self, tmp_db, monkeypatch):
        monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "false")
        self._insert_pkg(tmp_db, "pkg-disabled", age_minutes=60)
        affected = _sweep_unlinked_packages(tmp_db)
        assert affected == 0
        rows = tmp_db.get_order_packages_by_strategy("vwap")
        assert rows[0]["status"] == "open"


# ---------------------------------------------------------------------------
# Grace window — race against Bybit's order-create response side
# (sibling of S-053; preserved as a backstop under the SSOT path)
# ---------------------------------------------------------------------------


class TestReconcilerGraceWindow:
    """A trade with ``created_at`` newer than the grace threshold must
    not be looked up against Bybit yet. The SSOT path is consistent on
    the order-create response side so this window can soak down to ~5 s
    eventually, but it is preserved as a backstop while the rollout
    settles.
    """

    def test_recent_trade_skipped_when_orderid_not_found(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """Headline contract: a 1-second-old trade must NOT be orphaned
        even when Bybit denies the orderId — grace window shields it."""
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            tmp_path / "pings",
        )
        trade_id = _insert_trade(tmp_db, age_seconds=1)

        with patch(
            "src.units.accounts.clients.account_order_status",
            side_effect=AssertionError(
                "fresh row must not reach the order-status lookup"),
        ):
            summary = _reconcile_open_trades(tmp_db)

        assert summary["orphaned"] == 0
        assert summary["skipped_recent"] == 1
        assert _read_trade(tmp_db, trade_id)["status"] == "open"

    def test_old_trade_still_orphaned_when_orderid_not_found(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """A trade older than the grace threshold remains eligible for
        orphan-stamping — the grace window is targeted, not a wholesale
        disable."""
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            tmp_path / "pings",
        )
        trade_id = _insert_trade(
            tmp_db, age_seconds=300,
            trade_id="2100000000000000001",
        )

        with patch(
            "src.units.accounts.clients.account_order_status",
            return_value=_not_found_status("2100000000000000001"),
        ):
            summary = _reconcile_open_trades(tmp_db)

        assert summary["orphaned"] == 1
        assert summary["skipped_recent"] == 0
        assert _read_trade(tmp_db, trade_id)["status"] == "orphaned"

    def test_grace_window_env_override_tightens_window(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """RECONCILER_GRACE_SECONDS=10 makes a 30 s old row eligible."""
        monkeypatch.setenv("RECONCILER_GRACE_SECONDS", "10")
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            tmp_path / "pings",
        )
        trade_id = _insert_trade(
            tmp_db, age_seconds=30, trade_id="2100000000000000002",
        )

        with patch(
            "src.units.accounts.clients.account_order_status",
            return_value=_not_found_status("2100000000000000002"),
        ):
            summary = _reconcile_open_trades(tmp_db)

        assert summary["orphaned"] == 1
        assert _read_trade(tmp_db, trade_id)["status"] == "orphaned"

    def test_grace_window_env_override_widens_window(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """RECONCILER_GRACE_SECONDS=600 shields a 5 min old row."""
        monkeypatch.setenv("RECONCILER_GRACE_SECONDS", "600")
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            tmp_path / "pings",
        )
        trade_id = _insert_trade(tmp_db, age_seconds=300)

        with patch(
            "src.units.accounts.clients.account_order_status",
            side_effect=AssertionError(
                "row inside extended grace must not be looked up"),
        ):
            summary = _reconcile_open_trades(tmp_db)

        assert summary["orphaned"] == 0
        assert summary["skipped_recent"] == 1
        assert _read_trade(tmp_db, trade_id)["status"] == "open"

    def test_grace_window_zero_disables_protection(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """Operator escape hatch: setting the env var to 0 reverts to
        pre-fix behaviour for debugging."""
        monkeypatch.setenv("RECONCILER_GRACE_SECONDS", "0")
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            tmp_path / "pings",
        )
        trade_id = _insert_trade(
            tmp_db, age_seconds=1, trade_id="2100000000000000003",
        )

        with patch(
            "src.units.accounts.clients.account_order_status",
            return_value=_not_found_status("2100000000000000003"),
        ):
            summary = _reconcile_open_trades(tmp_db)

        assert summary["orphaned"] == 1
        assert _read_trade(tmp_db, trade_id)["status"] == "orphaned"

    def test_invalid_env_falls_back_to_default(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """A garbage env value must not turn off the protection — fall
        back to the 60 s default."""
        monkeypatch.setenv("RECONCILER_GRACE_SECONDS", "not-a-number")
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            tmp_path / "pings",
        )
        trade_id = _insert_trade(tmp_db, age_seconds=1)

        with patch(
            "src.units.accounts.clients.account_order_status",
            side_effect=AssertionError(
                "fresh row must not reach the order-status lookup"),
        ):
            summary = _reconcile_open_trades(tmp_db)

        assert summary["orphaned"] == 0
        assert summary["skipped_recent"] == 1
        assert _read_trade(tmp_db, trade_id)["status"] == "open"

    def test_recent_row_does_not_trigger_exchange_call(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """If every row is fresh, the reconciler should never reach the
        exchange — saves an API hit on every tick after a fresh trade.
        """
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            tmp_path / "pings",
        )
        _insert_trade(tmp_db, age_seconds=1)

        with patch(
            "src.units.accounts.clients.account_order_status",
            side_effect=AssertionError(
                "exchange must not be called when all rows are within grace"
            ),
        ), patch(
            "src.units.accounts.clients.account_open_positions",
            side_effect=AssertionError(
                "exchange must not be called when all rows are within grace"
            ),
        ):
            summary = _reconcile_open_trades(tmp_db)

        assert summary["skipped_recent"] == 1
        assert summary["orphaned"] == 0


class TestParseCreatedAt:
    """``_parse_created_at`` must accept both formats the trades schema
    produces — SQLite's ``CURRENT_TIMESTAMP`` (space-separated, no tz)
    and explicit ISO 8601 with tz suffix.
    """

    def test_sqlite_default_format(self):
        dt = _parse_created_at("2026-05-08 08:42:23")
        assert dt is not None
        assert dt.tzinfo is timezone.utc
        assert dt.year == 2026 and dt.month == 5 and dt.day == 8

    def test_iso_with_tz(self):
        dt = _parse_created_at("2026-05-08T08:42:23.284050+00:00")
        assert dt is not None
        assert dt.tzinfo is not None
        assert dt.utcoffset() == timedelta(0)

    def test_none_returns_none(self):
        assert _parse_created_at(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_created_at("") is None
        assert _parse_created_at("   ") is None

    def test_garbage_returns_none(self):
        assert _parse_created_at("not-a-date") is None


# ---------------------------------------------------------------------------
# Orphan-cascade hardening — retry + sticky audit row when the package
# update keeps failing. Without this the strategy-monocle gate at
# pipeline.py::_has_open_package_for_strategy stays stuck open and every
# future signal for the strategy is silently blocked.
# ---------------------------------------------------------------------------


class _FlakyDB:
    """Wraps a real Database, intercepting ``update_order_package`` to
    fail a configured number of times before delegating through. Lets
    the cascade-retry tests exercise both the success-on-retry and the
    permanent-failure paths without monkeypatching production code.
    """

    def __init__(self, real_db, fail_first_n: int):
        self._real = real_db
        self._fail_remaining = fail_first_n
        self.update_order_package_calls = 0

    # Pass-through everything except update_order_package.
    def __getattr__(self, name):
        return getattr(self._real, name)

    def update_order_package(self, pkg_id, updates):
        self.update_order_package_calls += 1
        if self._fail_remaining > 0:
            self._fail_remaining -= 1
            raise RuntimeError("simulated package-update failure")
        return self._real.update_order_package(pkg_id, updates)


class TestMarkOrphanedCascadeRetry:
    """``_mark_orphaned`` must:
      1. Retry the package cascade once on transient failure.
      2. Write a sticky ``orphan_cascade_failed`` audit row when both
         attempts fail — without it the strategy gate stays stuck.
    """

    def test_cascade_retry_succeeds_on_second_attempt(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        pkg_id = "pkg-flaky-001"
        _insert_package(tmp_db, pkg_id=pkg_id)
        trade_id = _insert_trade(tmp_db, notes_pkg_id=pkg_id)
        tmp_db.update_order_package(pkg_id, {"linked_trade_id": trade_id})

        # Redirect the audit JSONL writer at a temp dir so we can
        # assert on it without polluting the repo runtime_logs.
        audit_dir = tmp_path / "runtime_logs"
        audit_dir.mkdir()
        audit_file = audit_dir / "signal_audit.jsonl"
        monkeypatch.setattr(
            "src.utils.signal_audit_logger.SIGNAL_FILE", audit_file,
        )

        flaky = _FlakyDB(tmp_db, fail_first_n=1)
        # Read the trade row in the same shape _reconcile_open_trades
        # would hand it to _mark_orphaned.
        conn = tmp_db.connect()
        try:
            conn.row_factory = __import__("sqlite3").Row
            row = dict(conn.execute(
                "SELECT id, account_id, symbol, direction, notes, created_at "
                "FROM trades WHERE id=?",
                (trade_id,),
            ).fetchone())
        finally:
            conn.close()

        _mark_orphaned(flaky, row)

        # Two attempts: first fails, second succeeds.
        assert flaky.update_order_package_calls == 2
        # Trade row marked orphaned (the trade-side update is wrapped
        # by neither attempt — it's a separate single write).
        assert _read_trade(tmp_db, trade_id)["status"] == "orphaned"
        # Package row got the cascade on retry.
        pkg = _read_package(tmp_db, pkg_id)
        assert pkg["status"] == "closed"
        assert pkg["close_reason"] == "reconciler"
        # No sticky audit row — the retry succeeded.
        if audit_file.exists():
            lines = [
                json.loads(ln) for ln in audit_file.read_text().splitlines()
                if ln.strip()
            ]
            assert not any(
                e.get("action") == "orphan_cascade_failed" for e in lines
            )

    def test_cascade_fails_twice_writes_sticky_audit_row(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        pkg_id = "pkg-flaky-002"
        _insert_package(tmp_db, pkg_id=pkg_id)
        trade_id = _insert_trade(
            tmp_db, notes_pkg_id=pkg_id, symbol="BTCUSDT", direction="long",
        )
        tmp_db.update_order_package(pkg_id, {"linked_trade_id": trade_id})

        audit_dir = tmp_path / "runtime_logs"
        audit_dir.mkdir()
        audit_file = audit_dir / "signal_audit.jsonl"
        monkeypatch.setattr(
            "src.utils.signal_audit_logger.SIGNAL_FILE", audit_file,
        )
        # Disable the SQL dual-write so the test stays focused on the
        # JSONL audit row and doesn't fight Database init paths.
        monkeypatch.setenv("SIGNAL_DUAL_WRITE_DISABLED", "true")

        flaky = _FlakyDB(tmp_db, fail_first_n=2)
        conn = tmp_db.connect()
        try:
            conn.row_factory = __import__("sqlite3").Row
            row = dict(conn.execute(
                "SELECT id, account_id, symbol, direction, notes, created_at "
                "FROM trades WHERE id=?",
                (trade_id,),
            ).fetchone())
        finally:
            conn.close()

        _mark_orphaned(flaky, row)

        # Two attempts, both failed → no third retry.
        assert flaky.update_order_package_calls == 2
        # Trade row is still orphaned (cascade failure does not undo
        # the trade-side update).
        assert _read_trade(tmp_db, trade_id)["status"] == "orphaned"
        # Package row stuck open — that's the cascade leak the audit
        # row exists to flag.
        pkg = _read_package(tmp_db, pkg_id)
        assert pkg["status"] == "open"

        # Sticky audit row written — operator-greppable.
        assert audit_file.exists(), "audit JSONL must be created"
        events = [
            json.loads(ln) for ln in audit_file.read_text().splitlines()
            if ln.strip()
        ]
        cascade_failed = [
            e for e in events if e.get("action") == "orphan_cascade_failed"
        ]
        assert len(cascade_failed) == 1
        evt = cascade_failed[0]
        assert evt["status"] == "failed"
        assert evt["order_package_id"] == pkg_id
        assert evt["db_trade_id"] == trade_id
        assert evt["symbol"] == "BTCUSDT"
        assert evt["direction"] == "long"
        assert evt["account_id"] == "bybit_2"
        assert "simulated package-update failure" in evt["error"]


# ---------------------------------------------------------------------------
# _sweep_stuck_linked_packages — the second line of defence against the
# strategy-monocle gate getting stuck. Sweeps order_packages that are
# status='open' AND linked to a terminally-statused trade.
# ---------------------------------------------------------------------------


class TestSweepStuckLinkedPackages:
    def _insert_linked_pkg(
        self, db, *, pkg_id, linked_trade_id, status="open", strategy="vwap",
    ):
        """Insert a package linked to ``linked_trade_id`` at the given
        status. Mirrors the shape ``insert_order_package`` produces.
        """
        conn = db.connect()
        try:
            conn.execute(
                "INSERT INTO order_packages "
                "(order_package_id, strategy_name, symbol, direction, "
                "entry, sl, tp, confidence, status, linked_trade_id, "
                "created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,"
                "datetime('now'), datetime('now'))",
                (pkg_id, strategy, "BTCUSDT", "long",
                 80000.0, 79500.0, 80500.0, 0.42, status, linked_trade_id),
            )
            conn.commit()
        finally:
            conn.close()

    def test_sweep_force_closes_stuck_package_with_orphaned_trade(
        self, tmp_db, monkeypatch,
    ):
        monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "true")
        # Trade in terminal 'orphaned' state but the linked package is
        # still 'open' — the cascade-leak scenario.
        trade_id = _insert_trade(tmp_db, status="orphaned")
        self._insert_linked_pkg(
            tmp_db, pkg_id="pkg-stuck-001", linked_trade_id=trade_id,
        )

        affected = _sweep_stuck_linked_packages(tmp_db)
        assert affected == 1

        pkg = _read_package(tmp_db, "pkg-stuck-001")
        assert pkg["status"] == "closed"
        assert pkg["close_reason"] == "stuck_cascade_recovered"

    def test_sweep_skips_package_whose_linked_trade_is_open(
        self, tmp_db, monkeypatch,
    ):
        """The defence-in-depth must NOT touch packages whose linked
        trade is still status='open' — that's the live-position case
        and force-closing it would lose track of a real exchange
        position.
        """
        monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "true")
        trade_id = _insert_trade(tmp_db, status="open")
        self._insert_linked_pkg(
            tmp_db, pkg_id="pkg-live-001", linked_trade_id=trade_id,
        )

        affected = _sweep_stuck_linked_packages(tmp_db)
        assert affected == 0

        pkg = _read_package(tmp_db, "pkg-live-001")
        assert pkg["status"] == "open"
        assert pkg["close_reason"] is None

    def test_sweep_handles_each_terminal_status(
        self, tmp_db, monkeypatch,
    ):
        """Pin the full terminal-status set the sweep must handle.
        Drift in this list is the most likely way the gate gets stuck
        again, so it's worth pinning.
        """
        monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "true")
        for i, terminal in enumerate(
            ("orphaned", "exchange_rejected", "closed", "rejected",
             "rejected_too_small")
        ):
            trade_id = _insert_trade(tmp_db, status=terminal)
            self._insert_linked_pkg(
                tmp_db, pkg_id=f"pkg-term-{i}", linked_trade_id=trade_id,
            )

        affected = _sweep_stuck_linked_packages(tmp_db)
        assert affected == 5
        for i in range(5):
            pkg = _read_package(tmp_db, f"pkg-term-{i}")
            assert pkg["status"] == "closed"
            assert pkg["close_reason"] == "stuck_cascade_recovered"

    def test_sweep_is_idempotent(self, tmp_db, monkeypatch):
        monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "true")
        trade_id = _insert_trade(tmp_db, status="orphaned")
        self._insert_linked_pkg(
            tmp_db, pkg_id="pkg-idem-001", linked_trade_id=trade_id,
        )

        first = _sweep_stuck_linked_packages(tmp_db)
        second = _sweep_stuck_linked_packages(tmp_db)
        assert first == 1
        # Once the package is closed the SQL filter no longer matches.
        assert second == 0

    def test_sweep_skips_unlinked_packages(self, tmp_db, monkeypatch):
        """Defence boundary: an open package with no linked_trade_id is
        the ``_sweep_unlinked_packages`` jurisdiction, not this one.
        """
        monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "true")
        self._insert_linked_pkg(
            tmp_db, pkg_id="pkg-unlinked", linked_trade_id=None,
        )
        affected = _sweep_stuck_linked_packages(tmp_db)
        assert affected == 0

    def test_sweep_noop_when_reconcile_disabled(self, tmp_db, monkeypatch):
        monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "false")
        trade_id = _insert_trade(tmp_db, status="orphaned")
        self._insert_linked_pkg(
            tmp_db, pkg_id="pkg-disabled", linked_trade_id=trade_id,
        )

        affected = _sweep_stuck_linked_packages(tmp_db)
        assert affected == 0
        # Package row untouched.
        assert _read_package(tmp_db, "pkg-disabled")["status"] == "open"


# ---------------------------------------------------------------------------
# Manual-close detection — distinguish "operator manually closed via
# exchange UI / exchange-side risk action" from "fill anomaly /
# unknown" so the operator knows whether to investigate or just
# acknowledge the orphan ping.
#
# Field reality, 2026-05-08: the live trader (bybit_2) is
# market_type=spot-margin, where Bybit's set_trading_stop is
# unsupported. Spot-margin orphans are *guaranteed* to be operator
# manual or exchange risk action — there's no SL/TP fire path.
# ---------------------------------------------------------------------------


class TestClassifyOrphanClose:
    def test_spot_margin_classified_as_external_close(self):
        cfg = {"account_id": "bybit_2", "market_type": "spot-margin"}
        out = _classify_orphan_close(cfg)
        assert out["classification"] == "spot_margin_external_close"
        assert "no exchange-side SL/TP path" in out["note"]
        assert "exchange UI" in out["note"]

    def test_linear_derivatives_classified_unknown(self):
        cfg = {"account_id": "bybit_3", "market_type": "linear"}
        out = _classify_orphan_close(cfg)
        assert out["classification"] == "unknown"
        assert "derivatives" in out["note"]

    def test_inverse_derivatives_classified_unknown(self):
        cfg = {"account_id": "bybit_4", "market_type": "inverse"}
        out = _classify_orphan_close(cfg)
        assert out["classification"] == "unknown"

    def test_cash_spot_classified_unknown(self):
        """Cash spot doesn't borrow and doesn't have spot-margin's
        guaranteed-no-SL/TP-fire property — fall through to unknown.
        """
        cfg = {"account_id": "bybit_1", "market_type": "spot"}
        out = _classify_orphan_close(cfg)
        assert out["classification"] == "unknown"

    def test_missing_market_type_classified_unknown(self):
        out = _classify_orphan_close({"account_id": "bybit_x"})
        assert out["classification"] == "unknown"

    def test_market_type_case_insensitive(self):
        """``market_type: SPOT-MARGIN`` (operator typo) must still
        classify as spot-margin so the operator gets the right hint.
        """
        cfg = {"account_id": "bybit_2", "market_type": "SPOT-MARGIN"}
        out = _classify_orphan_close(cfg)
        assert out["classification"] == "spot_margin_external_close"

    def test_market_type_with_whitespace(self):
        """Defensive: trailing whitespace in YAML must not flip the
        classification to unknown.
        """
        cfg = {"account_id": "bybit_2", "market_type": " spot-margin "}
        out = _classify_orphan_close(cfg)
        assert out["classification"] == "spot_margin_external_close"


class TestOrphanReconcilerEmitsClassification:
    """Wiring contract: _reconcile_open_trades must pass the
    classification + note through to enqueue_orphan_reconciliation
    so the body the operator sees has the manual-vs-anomaly hint.
    """

    def test_spot_margin_orphan_ping_carries_external_close_tag(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        # The fixture's _fake_cfg_loader has bybit_2 as a regular
        # account; for this test override the cfg loader to mark
        # bybit_2 as spot-margin.
        monkeypatch.setattr(
            "src.runtime.order_monitor._load_account_cfgs_for_reconcile",
            lambda: {
                "bybit_2": {
                    "account_id": "bybit_2",
                    "exchange": "bybit",
                    "api_key_env": "BYBIT_KEY_2",
                    "api_secret_env": None,
                    "mode": "live",
                    "market_type": "spot-margin",
                },
            },
        )
        # Under the SSOT model an "orphan close" surfaces as the
        # ``filled, position flat`` verdict (Bybit confirmed the
        # fill but the position is gone — exchange closed it). The
        # classification ping piggybacks on that verdict, so mock
        # the per-orderId lookup to return Filled and the positions
        # endpoint to return an empty list.
        order_id = "2100000000000000001"
        trade_id = _insert_trade(
            tmp_db, account_id="bybit_2", trade_id=order_id,
        )

        pings_dir = tmp_path / "pings"
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            pings_dir,
        )
        with patch(
            "src.units.accounts.clients.account_order_status",
            return_value=_filled_status(order_id),
        ), patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[],
        ):
            summary = _reconcile_open_trades(tmp_db)

        assert summary["closed"] == 1
        assert _read_trade(tmp_db, trade_id)["status"] == "closed"

        queued = sorted(pings_dir.glob("*.json"))
        assert len(queued) == 1
        body = json.loads(queued[0].read_text())["body"]
        assert "Classification: spot_margin_external_close" in body
        assert "no exchange-side SL/TP path" in body

    def test_derivatives_orphan_ping_carries_unknown_classification(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        monkeypatch.setattr(
            "src.runtime.order_monitor._load_account_cfgs_for_reconcile",
            lambda: {
                "bybit_2": {
                    "account_id": "bybit_2",
                    "exchange": "bybit",
                    "api_key_env": "BYBIT_KEY_2",
                    "api_secret_env": None,
                    "mode": "live",
                    "market_type": "linear",
                },
            },
        )
        order_id = "2100000000000000002"
        _insert_trade(tmp_db, account_id="bybit_2", trade_id=order_id)

        pings_dir = tmp_path / "pings"
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            pings_dir,
        )
        with patch(
            "src.units.accounts.clients.account_order_status",
            return_value=_filled_status(order_id),
        ), patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[],
        ):
            _reconcile_open_trades(tmp_db)

        queued = sorted(pings_dir.glob("*.json"))
        assert len(queued) == 1
        body = json.loads(queued[0].read_text())["body"]
        assert "Classification: unknown" in body
        # The note must guide the operator to the right action.
        assert "derivatives" in body


# ---------------------------------------------------------------------------
# Stuck-strategy watchdog — final fallback when the orphan reconciler
# AND the linked-package sweep both missed a stuck row. Force-closes
# the package, cascades the trade row to orphaned, and emits a
# high-priority operator alert. Operator-confirmed full automatic
# reset is approved (2026-05-08).
# ---------------------------------------------------------------------------


class TestStuckStrategyWatchdog:
    """``_watchdog_stuck_strategies`` finds packages with
    ``status='open' AND linked_trade_id IS NOT NULL`` whose
    ``updated_at`` is older than the configured threshold (default
    30 min, env ``STUCK_STRATEGY_THRESHOLD_MINUTES``).
    """

    def _insert_pkg_with_age(
        self, db, *, pkg_id, linked_trade_id, age_minutes,
        strategy="vwap", status="open", meta=None,
    ):
        """Insert a package with a backdated ``updated_at``."""
        meta_json = json.dumps(meta or {})
        conn = db.connect()
        try:
            conn.execute(
                "INSERT INTO order_packages "
                "(order_package_id, strategy_name, symbol, direction, "
                " entry, sl, tp, confidence, status, linked_trade_id, "
                " meta, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?, "
                " datetime('now', ? || ' minutes'), "
                " datetime('now', ? || ' minutes'))",
                (pkg_id, strategy, "BTCUSDT", "long",
                 80000.0, 79500.0, 80500.0, 0.42, status, linked_trade_id,
                 meta_json, f"-{age_minutes}", f"-{age_minutes}"),
            )
            conn.commit()
        finally:
            conn.close()

    def test_force_clears_package_older_than_threshold(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """Headline contract: a package stuck > 30 min with a still-
        open linked trade AND no matching exchange-side position is
        force-closed, the trade is orphaned, and a high-priority
        alert is emitted.
        """
        monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "true")
        trade_id = _insert_trade(tmp_db, status="open")
        self._insert_pkg_with_age(
            tmp_db, pkg_id="pkg-stuck-watchdog-1",
            linked_trade_id=trade_id, age_minutes=45,
        )

        pings_dir = tmp_path / "pings"
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            pings_dir,
        )
        # Position cross-check (2026-05-09): mock empty list so
        # the watchdog reaches the genuine-orphan branch and
        # force-clears as before.
        with patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[],
        ):
            summary = _watchdog_stuck_strategies(tmp_db)

        assert summary["checked"] == 1
        assert summary["auto_cleared"] == 1
        assert summary["alerted"] == 1
        assert summary["errors"] == 0

        pkg = _read_package(tmp_db, "pkg-stuck-watchdog-1")
        assert pkg["status"] == "closed"
        assert pkg["close_reason"] == "stuck_strategy_watchdog"

        trade = _read_trade(tmp_db, trade_id)
        assert trade["status"] == "orphaned"
        assert trade["exit_reason"] == "stuck_strategy_watchdog"

        # High-priority Telegram-ready alert with the watchdog body.
        queued = sorted(pings_dir.glob("*.json"))
        assert len(queued) == 1
        evt = json.loads(queued[0].read_text())
        assert evt["priority"] == "high"
        assert "Stuck-strategy watchdog" in evt["body"]
        assert "pkg-stuck-watchdog-1" in evt["body"]
        assert "force-cleared" in evt["body"]

    def test_recent_package_not_touched(self, tmp_db, monkeypatch):
        """Defence boundary: a package stuck only 5 min (under the
        30 min default) must NOT be touched. The orphan reconciler
        + stuck-linked sweep are the first lines of defence; the
        watchdog only fires after they've had ample time to act.
        """
        monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "true")
        trade_id = _insert_trade(tmp_db, status="open")
        self._insert_pkg_with_age(
            tmp_db, pkg_id="pkg-fresh", linked_trade_id=trade_id,
            age_minutes=5,
        )
        summary = _watchdog_stuck_strategies(tmp_db)
        assert summary["checked"] == 0
        assert summary["auto_cleared"] == 0
        assert summary["alerted"] == 0
        assert _read_package(tmp_db, "pkg-fresh")["status"] == "open"
        assert _read_trade(tmp_db, trade_id)["status"] == "open"

    def test_unlinked_package_not_touched(self, tmp_db, monkeypatch):
        """Defence boundary: packages with NULL ``linked_trade_id``
        belong to ``_sweep_unlinked_packages``, not the watchdog.
        Even when they are stuck >> threshold, the watchdog does not
        match them — the strategy gate isn't blocked by them
        anyway (the gate's WHERE requires ``linked_trade_id IS NOT
        NULL``).
        """
        monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "true")
        self._insert_pkg_with_age(
            tmp_db, pkg_id="pkg-unlinked-old",
            linked_trade_id=None, age_minutes=120,
        )
        summary = _watchdog_stuck_strategies(tmp_db)
        assert summary["checked"] == 0
        assert summary["auto_cleared"] == 0

    def test_already_closed_package_not_touched(self, tmp_db, monkeypatch):
        """A package that's already ``status='closed'`` is past the
        watchdog's job — natural idempotency on the SQL match.
        """
        monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "true")
        trade_id = _insert_trade(tmp_db, status="closed")
        self._insert_pkg_with_age(
            tmp_db, pkg_id="pkg-already-closed",
            linked_trade_id=trade_id, age_minutes=60,
            status="closed",
        )
        summary = _watchdog_stuck_strategies(tmp_db)
        assert summary["checked"] == 0

    def test_terminal_linked_trade_does_not_re_orphan(
        self, tmp_db, monkeypatch,
    ):
        """If the linked trade is ALREADY in a terminal status
        (orphaned / closed / rejected_too_small / etc.), the
        watchdog still force-closes the package but does NOT
        rewrite the trade's status (which would lose information).
        Only ``status='open'`` trades get cascaded.
        """
        monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "true")
        trade_id = _insert_trade(tmp_db, status="orphaned")
        self._insert_pkg_with_age(
            tmp_db, pkg_id="pkg-trade-already-orphaned",
            linked_trade_id=trade_id, age_minutes=60,
        )
        with patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[],
        ):
            summary = _watchdog_stuck_strategies(tmp_db)
        assert summary["auto_cleared"] == 1
        # Package force-closed.
        assert _read_package(
            tmp_db, "pkg-trade-already-orphaned",
        )["status"] == "closed"
        # Trade status preserved as 'orphaned' — not overwritten.
        trade = _read_trade(tmp_db, trade_id)
        assert trade["status"] == "orphaned"

    def test_threshold_env_override(self, tmp_db, monkeypatch):
        """Operator can lower the threshold via env var without a
        trader restart. With a 5 min override, a 10 min old package
        becomes eligible.
        """
        monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "true")
        monkeypatch.setenv("STUCK_STRATEGY_THRESHOLD_MINUTES", "5")
        trade_id = _insert_trade(tmp_db, status="open")
        self._insert_pkg_with_age(
            tmp_db, pkg_id="pkg-tight-threshold",
            linked_trade_id=trade_id, age_minutes=10,
        )
        with patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[],
        ):
            summary = _watchdog_stuck_strategies(tmp_db)
        assert summary["auto_cleared"] == 1
        assert _read_package(
            tmp_db, "pkg-tight-threshold",
        )["status"] == "closed"

    def test_invalid_env_falls_back_to_default(self, tmp_db, monkeypatch):
        """A garbage env value must NOT lower the threshold — fall
        back to 30 min so a typo can't accidentally trigger an
        aggressive sweep.
        """
        monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "true")
        monkeypatch.setenv("STUCK_STRATEGY_THRESHOLD_MINUTES", "not-a-number")
        trade_id = _insert_trade(tmp_db, status="open")
        # 10 min is well under the 30 min default — should NOT fire.
        self._insert_pkg_with_age(
            tmp_db, pkg_id="pkg-garbage-env",
            linked_trade_id=trade_id, age_minutes=10,
        )
        summary = _watchdog_stuck_strategies(tmp_db)
        assert summary["checked"] == 0
        assert _read_package(tmp_db, "pkg-garbage-env")["status"] == "open"

    def test_noop_when_reconcile_disabled(self, tmp_db, monkeypatch):
        monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "false")
        trade_id = _insert_trade(tmp_db, status="open")
        self._insert_pkg_with_age(
            tmp_db, pkg_id="pkg-disabled-flag",
            linked_trade_id=trade_id, age_minutes=120,
        )
        summary = _watchdog_stuck_strategies(tmp_db)
        assert summary == {
            "checked": 0, "alerted": 0, "auto_cleared": 0,
            "deferred_position_alive": 0,
            "released_alive": 0,
            "skipped_position_read_failed": 0,
            "errors": 0,
        }
        assert _read_package(
            tmp_db, "pkg-disabled-flag",
        )["status"] == "open"

    def test_idempotent_across_consecutive_ticks(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """The first tick force-closes the package + emits the
        alert. The second tick must be a complete no-op — the
        package is now ``status='closed'`` so the WHERE no longer
        matches, AND no fresh alert ping is queued.
        """
        monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "true")
        trade_id = _insert_trade(tmp_db, status="open")
        self._insert_pkg_with_age(
            tmp_db, pkg_id="pkg-idem", linked_trade_id=trade_id,
            age_minutes=45,
        )
        pings_dir = tmp_path / "pings"
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            pings_dir,
        )

        with patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[],
        ):
            first = _watchdog_stuck_strategies(tmp_db)
            second = _watchdog_stuck_strategies(tmp_db)

        assert first["alerted"] == 1
        assert first["auto_cleared"] == 1
        assert second == {
            "checked": 0, "alerted": 0, "auto_cleared": 0,
            "deferred_position_alive": 0,
            "released_alive": 0,
            "skipped_position_read_failed": 0,
            "errors": 0,
        }
        # Only ONE ping queued across both ticks.
        assert len(list(pings_dir.glob("*.json"))) == 1

    def test_position_alive_defers_force_clear(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """The 2026-05-09 fix (#582 — vwap orphan churn): when the
        exchange reports the package's (symbol, direction) is still
        alive, the watchdog must NOT force-clear the package — the
        trade is patient (e.g. vwap mean-reversion waiting for
        price to reach VWAP), not stuck. Operator gets a single
        alert with auto_cleared=False so they know we backed off.
        """
        monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "true")
        trade_id = _insert_trade(tmp_db, status="open")
        self._insert_pkg_with_age(
            tmp_db, pkg_id="pkg-position-alive",
            linked_trade_id=trade_id, age_minutes=45,
        )
        pings_dir = tmp_path / "pings"
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            pings_dir,
        )
        # Exchange reports a long BTCUSDT position — matches the
        # package's symbol+direction (the test fixture inserts long).
        with patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[
                {"symbol": "BTCUSDT", "side": "long", "size": 0.001},
            ],
        ):
            summary = _watchdog_stuck_strategies(tmp_db)

        assert summary["checked"] == 1
        assert summary["deferred_position_alive"] == 1
        assert summary["auto_cleared"] == 0
        assert summary["alerted"] == 1
        assert summary["errors"] == 0

        # Package still open — NOT force-cleared.
        pkg = _read_package(tmp_db, "pkg-position-alive")
        assert pkg["status"] == "open"
        # Trade still open — NOT cascaded.
        trade = _read_trade(tmp_db, trade_id)
        assert trade["status"] == "open"

        # One alert fired with auto_cleared=False so the operator
        # sees we backed off rather than nuking the position.
        queued = sorted(pings_dir.glob("*.json"))
        assert len(queued) == 1
        evt = json.loads(queued[0].read_text())
        assert "force-cleared" not in evt["body"]

    def test_position_alive_alert_idempotent_across_ticks(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """Position-alive defer path: the meta-stamp on the first
        tick bumps ``updated_at`` so the row falls out of the
        watchdog's WHERE for one full threshold period (30 min by
        default). Back-to-back ticks therefore see the row exactly
        once: first tick defers + alerts, second tick is a complete
        no-op. The alert fires only ONCE in the pings dir.

        After the threshold elapses, the row re-enters the WHERE
        and the watchdog re-checks position state. If the position
        is still alive, defer again (no alert this time —
        ``stuck_alert_emitted_at`` is sticky). If the position has
        gone flat, force-clear as a true orphan.
        """
        monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "true")
        trade_id = _insert_trade(tmp_db, status="open")
        self._insert_pkg_with_age(
            tmp_db, pkg_id="pkg-defer-idem",
            linked_trade_id=trade_id, age_minutes=45,
        )
        pings_dir = tmp_path / "pings"
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            pings_dir,
        )
        with patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[
                {"symbol": "BTCUSDT", "side": "long", "size": 0.001},
            ],
        ):
            first = _watchdog_stuck_strategies(tmp_db)
            second = _watchdog_stuck_strategies(tmp_db)

        assert first["alerted"] == 1
        assert first["deferred_position_alive"] == 1
        # Second tick: meta-stamp bumped updated_at so the row no
        # longer matches WHERE (>30 min stale). Complete no-op.
        assert second["checked"] == 0
        assert second["alerted"] == 0
        assert second["deferred_position_alive"] == 0
        # Only ONE ping queued across both ticks.
        assert len(list(pings_dir.glob("*.json"))) == 1

    def test_position_read_failure_defers_conservatively(
        self, tmp_db, monkeypatch,
    ):
        """If ``account_open_positions`` returns None (read failure
        — creds, network, exchange error), the watchdog must defer
        force-clearing rather than nuke the package on a half-known
        view of exchange state.
        """
        monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "true")
        trade_id = _insert_trade(tmp_db, status="open")
        self._insert_pkg_with_age(
            tmp_db, pkg_id="pkg-read-failure",
            linked_trade_id=trade_id, age_minutes=45,
        )
        with patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=None,
        ):
            summary = _watchdog_stuck_strategies(tmp_db)

        assert summary["checked"] == 1
        assert summary["skipped_position_read_failed"] == 1
        assert summary["auto_cleared"] == 0
        assert summary["deferred_position_alive"] == 0
        # Package + trade left untouched — wait for the next tick
        # when the read might succeed.
        assert _read_package(tmp_db, "pkg-read-failure")["status"] == "open"
        assert _read_trade(tmp_db, trade_id)["status"] == "open"

    def test_position_alive_releases_package_after_release_threshold(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """RELEASE_STUCK_PKG_MINUTES contract (PR claude/watchdog-cadence-fix-JZkeL):
        when the exchange-side position is still alive and the
        package has been silent for at least the release threshold,
        force-close the **package row alone** so the
        strategy_monocle gate reopens for new dispatches. The trade
        row stays ``status='open'`` — the monitor + per-trade
        reconciler keep tracking the live position to its real
        close.
        """
        monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "true")
        monkeypatch.setenv("RELEASE_STUCK_PKG_MINUTES", "90")
        trade_id = _insert_trade(tmp_db, status="open")
        self._insert_pkg_with_age(
            tmp_db, pkg_id="pkg-release-after-90",
            linked_trade_id=trade_id, age_minutes=95,
        )
        pings_dir = tmp_path / "pings"
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            pings_dir,
        )
        with patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[
                {"symbol": "BTCUSDT", "side": "long", "size": 0.004},
            ],
        ):
            summary = _watchdog_stuck_strategies(tmp_db)

        assert summary["deferred_position_alive"] == 1
        assert summary["released_alive"] == 1
        assert summary["auto_cleared"] == 0  # NOT the orphan path
        assert summary["alerted"] == 1
        assert summary["errors"] == 0

        # Package row force-closed with the new close_reason.
        pkg = _read_package(tmp_db, "pkg-release-after-90")
        assert pkg["status"] == "closed"
        assert pkg["close_reason"] == "watchdog_released_alive"

        # Trade row left ALIVE — the live position is still on
        # Bybit, the existing reconciler will close it for real.
        trade = _read_trade(tmp_db, trade_id)
        assert trade["status"] == "open"
        assert trade["exit_reason"] is None

        # Alert fires with auto_cleared=True because the
        # strategy_monocle gate WAS reopened by the package
        # release — that's the observable system change the
        # operator cares about.
        queued = sorted(pings_dir.glob("*.json"))
        assert len(queued) == 1
        evt = json.loads(queued[0].read_text())
        assert "force-cleared" in evt["body"]

    def test_position_alive_below_release_threshold_just_defers(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """Below RELEASE_STUCK_PKG_MINUTES the watchdog must keep
        the pre-2026-05-16 defer+alert-once behaviour — neither
        the package nor the trade row is touched beyond a meta
        stamp.
        """
        monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "true")
        monkeypatch.setenv("RELEASE_STUCK_PKG_MINUTES", "90")
        trade_id = _insert_trade(tmp_db, status="open")
        self._insert_pkg_with_age(
            tmp_db, pkg_id="pkg-alive-50",
            linked_trade_id=trade_id, age_minutes=50,
        )
        pings_dir = tmp_path / "pings"
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            pings_dir,
        )
        with patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[
                {"symbol": "BTCUSDT", "side": "long", "size": 0.004},
            ],
        ):
            summary = _watchdog_stuck_strategies(tmp_db)

        assert summary["deferred_position_alive"] == 1
        assert summary["released_alive"] == 0
        assert _read_package(tmp_db, "pkg-alive-50")["status"] == "open"
        assert _read_trade(tmp_db, trade_id)["status"] == "open"

    def test_position_alive_release_disabled_when_env_zero(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """``RELEASE_STUCK_PKG_MINUTES=0`` opts out of the release
        path entirely — the watchdog reverts to the pre-2026-05-16
        defer-forever behaviour for position-alive packages even
        well past the release window.
        """
        monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "true")
        monkeypatch.setenv("RELEASE_STUCK_PKG_MINUTES", "0")
        trade_id = _insert_trade(tmp_db, status="open")
        self._insert_pkg_with_age(
            tmp_db, pkg_id="pkg-release-disabled",
            linked_trade_id=trade_id, age_minutes=240,  # 4h
        )
        pings_dir = tmp_path / "pings"
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            pings_dir,
        )
        with patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[
                {"symbol": "BTCUSDT", "side": "long", "size": 0.004},
            ],
        ):
            summary = _watchdog_stuck_strategies(tmp_db)

        assert summary["deferred_position_alive"] == 1
        assert summary["released_alive"] == 0
        assert _read_package(tmp_db, "pkg-release-disabled")["status"] == "open"
        assert _read_trade(tmp_db, trade_id)["status"] == "open"
