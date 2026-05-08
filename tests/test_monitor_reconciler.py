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
    _exchange_position_set,
    _extract_trade_id_from_notes,
    _is_numeric_order_id,
    _parse_created_at,
    _reconcile_open_trades,
    _sweep_unlinked_packages,
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

    def test_orderid_filled_position_flat_marks_closed_with_real_exit(
        self, tmp_db, tmp_path, monkeypatch,
    ):
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
        assert abs(row["exit_price"] - 80123.45) < 1e-6
        notes = json.loads(row["notes"])
        assert notes["closed_by"] == "monitor_reconciler"
        assert notes["closed_at"] == "1762620000000"

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


class TestNumericOrderIdAndNotesExtraction:
    def test_real_bybit_orderid_is_numeric(self):
        assert _is_numeric_order_id("1842564317108924672") is True

    def test_rejected_prefix_is_non_numeric(self):
        assert _is_numeric_order_id("rejected-deadbeefcafe") is False

    def test_exchange_rejected_prefix_is_non_numeric(self):
        assert _is_numeric_order_id("exchange_rejected-deadbeef1234") is False

    def test_dry_prefix_is_non_numeric(self):
        assert _is_numeric_order_id("dry-abc123def456") is False

    def test_empty_string_is_non_numeric(self):
        assert _is_numeric_order_id("") is False

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
