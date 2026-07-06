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
    _cascade_close_linked_package,
    _classify_orphan_close,
    _exchange_position_set,
    _extract_package_id,
    _extract_trade_id_from_notes,
    _is_numeric_order_id,
    _is_real_order_id,
    _mark_orphaned,
    _parse_created_at,
    _reconcile_open_trades,
    _resolve_linked_package_id,
    _stuck_threshold_for_package,
    _sweep_stuck_linked_packages,
    _sweep_unlinked_packages,
    _timeframe_to_minutes,
    _watchdog_stuck_strategies,
)
from src.units.db.database import Database


def _reconcile_to_close(db):
    """Drive ``_reconcile_open_trades`` to a settled close.

    BASELINE (2026-06-17): the netting-guard close-confirm is unconditional —
    a filled trade reading net-flat is never closed on the FIRST observation;
    it must read flat across a second observation
    (``RECONCILER_CLOSE_CONFIRM_SECONDS`` apart) before the close lands. Tests
    that assert the eventual close state therefore need two observations. This
    helper sets the confirm window to 0 (so the second observation alone
    confirms), runs two ticks, and returns the SECOND summary (the one that
    performed the close). Use it in place of a single
    ``_reconcile_open_trades(db)`` call wherever the test asserts a flat trade
    ends up closed.
    """
    import os

    from src.runtime import order_monitor as _om

    prev = os.environ.get("RECONCILER_CLOSE_CONFIRM_SECONDS")
    os.environ["RECONCILER_CLOSE_CONFIRM_SECONDS"] = "0"
    _om._PENDING_CLOSE_CONFIRM.clear()
    try:
        _om._reconcile_open_trades(db)          # 1st flat → arms pending
        return _om._reconcile_open_trades(db)   # 2nd flat → confirms + closes
    finally:
        if prev is None:
            os.environ.pop("RECONCILER_CLOSE_CONFIRM_SECONDS", None)
        else:
            os.environ["RECONCILER_CLOSE_CONFIRM_SECONDS"] = prev


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
            "SELECT id, status, exit_reason, exit_price, entry_price, "
            "pnl, pnl_percent, notes "
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

    queued = sorted(p for p in pings_dir.glob("*.json")
                    if not p.name.endswith("-orphanflag.json"))
    assert len(queued) == 1
    # The orphan red-flag (operator directive 2026-06-24) fires alongside the
    # reconciliation ping — durable log + a loud /system-review call-to-action.
    flags = list(pings_dir.glob("*-orphanflag.json"))
    assert len(flags) == 1
    assert "/system-review" in json.loads(flags[0].read_text())["body"]
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
    # One reconciliation ping per orphan + one orphan red-flag per orphan.
    assert len([p for p in pings_dir.glob("*.json")
                if not p.name.endswith("-orphanflag.json")]) == 3
    assert len(list(pings_dir.glob("*-orphanflag.json"))) == 3


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
            summary = _reconcile_to_close(tmp_db)

        assert summary["closed"] == 1
        assert summary["orphaned"] == 0
        row = _read_trade_full(tmp_db, trade_id)
        assert row["status"] == "closed"
        assert row["exit_reason"] == "reconciler_filled"
        assert row["exit_price"] is None
        notes = json.loads(row["notes"])
        assert notes["closed_by"] == "monitor_reconciler"
        # closed_at is normalised from Bybit's epoch-ms exec_time to ISO at the
        # writer (BL-20260620-RECONCILER-CLOSEDAT-MS) — was "1762620000000".
        assert notes["closed_at"] == "2025-11-08T16:40:00+00:00"
        assert notes["exit_price_source"] == "entry_order_avg_price_unreliable"
        # 2026-05-19 entry_price backfill: the trade was opened at the
        # intent (80000.0 — see _insert_trade) but Bybit reported the
        # actual fill at 80123.45 in `avg_price`. The reconciler now
        # writes that back so the execution_quality_labels dataset
        # gets real signed slippage instead of an all-zero label.
        assert row["entry_price"] == pytest.approx(80123.45)

    def test_entry_price_backfill_skipped_when_already_matches(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        # When the recorded entry_price already equals the avg_price
        # (no slippage), the reconciler doesn't rewrite the column —
        # the `if _entry_avg_price != _entry_current` guard.
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            tmp_path / "pings",
        )
        trade_id = _insert_trade(tmp_db, trade_id="2000000000000000099")
        # Default _insert_trade plants entry_price=80000.0; tell Bybit
        # the avg_price is exactly that.
        with patch(
            "src.units.accounts.clients.account_order_status",
            return_value=_filled_status(
                "2000000000000000099", avg_price=80000.0,
            ),
        ), patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[],
        ):
            _reconcile_open_trades(tmp_db)
        row = _read_trade_full(tmp_db, trade_id)
        assert row["entry_price"] == pytest.approx(80000.0)

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
            summary = _reconcile_to_close(tmp_db)

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

        # BASELINE close-confirm: the first net-flat tick arms the pending
        # close; the second confirms it. The per-account caching invariant is
        # PER TICK, so assert the call count on a SINGLE tick.
        from src.runtime import order_monitor as _om
        monkeypatch.setenv("RECONCILER_CLOSE_CONFIRM_SECONDS", "0")
        _om._PENDING_CLOSE_CONFIRM.clear()
        with patch(
            "src.units.accounts.clients.account_order_status",
            side_effect=lambda cfg, oid: _filled_status(str(oid)),
        ), patch(
            "src.units.accounts.clients.account_open_positions",
            side_effect=fake_positions,
        ):
            s1 = _reconcile_open_trades(tmp_db)   # 1st flat → all 3 pending
            assert len(positions_calls) == 1, "one positions call on tick 1"
            assert s1["closed"] == 0 and s1["pending_close"] == 3
            positions_calls.clear()
            summary = _reconcile_open_trades(tmp_db)  # 2nd flat → confirms close
        assert summary["closed"] == 3
        assert len(positions_calls) == 1, "one positions call on tick 2"

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
        assert _is_real_order_id("dry-breakout-abc1234567") is False

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
        self._insert_pkg(tmp_db, "pkg-old-unlinked", age_minutes=10)
        affected = _sweep_unlinked_packages(tmp_db)
        assert affected == 1
        rows = tmp_db.get_order_packages_by_strategy("vwap")
        assert rows[0]["status"] == "orphaned"

    def test_recent_unlinked_package_not_swept(
            self, tmp_db, monkeypatch):
        """A package created less than 5 minutes ago with no linked trade
        is still being dispatched — do not orphan it prematurely."""
        self._insert_pkg(tmp_db, "pkg-new-unlinked", age_minutes=1)
        affected = _sweep_unlinked_packages(tmp_db)
        assert affected == 0
        rows = tmp_db.get_order_packages_by_strategy("vwap")
        assert rows[0]["status"] == "open"

    def test_linked_open_package_not_swept(self, tmp_db, monkeypatch):
        """A linked open package (real broker position) must never be
        touched by the unlinked sweep."""
        self._insert_pkg(tmp_db, "pkg-linked", linked_trade_id=7,
                         age_minutes=60)
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
        self._insert_linked_pkg(
            tmp_db, pkg_id="pkg-unlinked", linked_trade_id=None,
        )
        affected = _sweep_stuck_linked_packages(tmp_db)
        assert affected == 0


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
            summary = _reconcile_to_close(tmp_db)

        assert summary["closed"] == 1
        assert _read_trade(tmp_db, trade_id)["status"] == "closed"

        queued = sorted(pings_dir.glob("*.json"))
        assert len(queued) == 1
        body = json.loads(queued[0].read_text())["body"]
        # Trade has no linked order package → genuinely untracked orphan headline.
        # The old spot-margin-vs-derivatives distinction was in _classify_orphan_close;
        # the new logic keys on linked_package_id, not market_type.
        assert "Classification: unlinked_orphan" in body
        assert "Orphaned trade" in body

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
            _reconcile_to_close(tmp_db)

        queued = sorted(pings_dir.glob("*.json"))
        assert len(queued) == 1
        body = json.loads(queued[0].read_text())["body"]
        # Trade has no linked order package → unlinked_orphan (the alarming case).
        # The old "unknown" classification was the _classify_orphan_close fallback
        # for all derivatives; the new code uses exit_reason + linked_package_id
        # instead so "unknown" is never emitted.
        assert "Classification: unlinked_orphan" in body
        assert "no package link" in body


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
        # BL-20260620-WATCHDOGORPHAN (watchdog half): position confirmed flat +
        # broker closed-pnl unmatched (the per-leg case on a netting account) →
        # finalise CLOSED (local-compute pnl next tick), NOT orphaned.
        assert trade["status"] == "closed"
        assert trade["exit_reason"] == "stuck_strategy_watchdog"
        assert summary["closed_local_unmatched"] == 1

        # High-priority Telegram-ready stuck alert still fires. The linked trade
        # is now a clean close, so NO orphan red-flag ping is queued.
        queued = sorted(p for p in pings_dir.glob("*.json")
                        if not p.name.endswith("-orphanflag.json"))
        assert len(queued) == 1
        assert len(list(pings_dir.glob("*-orphanflag.json"))) == 0
        evt = json.loads(queued[0].read_text())
        assert evt["priority"] == "high"
        assert "Stuck-strategy watchdog" in evt["body"]
        assert "pkg-stuck-watchdog-1" in evt["body"]
        assert "force-cleared" in evt["body"]

    def test_position_flat_recovers_close_from_broker_pnl_not_orphan(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """BL-20260620-WATCHDOGORPHAN: when the position is FLAT at a
        broker-truth exchange (Bybit) and Bybit's closed-pnl confirms a
        real close, the watchdog must finalise the trade as
        ``status='closed'`` with the recovered exit_price + pnl + closed_at
        — NOT orphan it with NULL pnl (which strands the close out of
        /api/bot/trades/closed and unmatched against Bybit's own ledger).
        The package is still force-closed (gate must clear).
        """
        trade_id = _insert_trade(tmp_db, status="open")
        self._insert_pkg_with_age(
            tmp_db, pkg_id="pkg-watchdog-recover",
            linked_trade_id=trade_id, age_minutes=45,
        )

        pings_dir = tmp_path / "pings"
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            pings_dir,
        )

        with patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[],
        ), patch(
            "src.units.accounts.clients.account_has_broker_pnl_reader",
            return_value=True,
        ), patch(
            "src.units.accounts.clients.account_closed_pnl_for_trade",
            return_value={
                "avg_exit_price": 80500.0,
                "avg_entry_price": 80000.0,
                "closed_pnl": 0.9795,
                "qty": 0.005,
                "side": "Sell",
                "closed_at": "1762620000000",
            },
        ):
            summary = _watchdog_stuck_strategies(tmp_db)

        assert summary["auto_cleared"] == 1
        assert summary["recovered_closed"] == 1
        assert summary["errors"] == 0

        # Package force-closed (gate clears) as before.
        pkg = _read_package(tmp_db, "pkg-watchdog-recover")
        assert pkg["status"] == "closed"

        # Trade FINALISED as a real close — not orphaned.
        trade = _read_trade_full(tmp_db, trade_id)
        assert trade["status"] == "closed"
        # 2026-06-23: the watchdog-recovery close is now classified from the
        # recovered exit price vs the package bracket (entry 80000 / sl 79500 /
        # tp 80500). The recovered exit 80500 is at/above the long tp → 'tp'
        # (was the generic 'reconciler_filled' before the close-labeling fix).
        assert trade["exit_reason"] == "tp"
        assert abs(trade["exit_price"] - 80500.0) < 1e-6
        assert abs(trade["pnl"] - 0.9795) < 1e-6
        notes = json.loads(trade["notes"])
        assert notes["exit_price_source"] == "bybit_closed_pnl"
        assert notes["closed_by"] == "stuck_strategy_watchdog"
        assert notes["closed_at"]

    def test_position_flat_no_broker_record_finalizes_closed_local(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """When the position is flat but Bybit's closed-pnl has NO matching
        record (lookup returns None — the EXPECTED per-leg case on a one-way
        NETTING account, where N strategy rows share one net position), the
        watchdog now finalises the row ``status='closed'`` (local-compute pnl
        filled by ``_sweep_local_pnl_for_unpriced`` next tick) instead of
        orphaning it with a red-flag ping. The position is confirmed flat → the
        leg DID close; only broker-truth PnL is unavailable, so this is a normal
        local-compute close, not an orphan (BL-20260620-WATCHDOGORPHAN watchdog
        half — the recurring bybit_2 BTCUSDT orphan noise).
        """
        trade_id = _insert_trade(tmp_db, status="open")
        self._insert_pkg_with_age(
            tmp_db, pkg_id="pkg-watchdog-norec",
            linked_trade_id=trade_id, age_minutes=45,
        )

        pings_dir = tmp_path / "pings"
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            pings_dir,
        )

        with patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[],
        ), patch(
            "src.units.accounts.clients.account_has_broker_pnl_reader",
            return_value=True,
        ), patch(
            "src.units.accounts.clients.account_closed_pnl_for_trade",
            return_value=None,
        ):
            summary = _watchdog_stuck_strategies(tmp_db)

        assert summary["auto_cleared"] == 1
        assert summary["recovered_closed"] == 0
        assert summary["closed_local_unmatched"] == 1
        assert summary["errors"] == 0

        trade = _read_trade_full(tmp_db, trade_id)
        assert trade["status"] == "closed"
        assert trade["exit_reason"] == "stuck_strategy_watchdog"
        # pnl is still None HERE (the local-pnl sweep runs as its own tick pass);
        # the point is the row is a clean close, not an orphan with a red flag.
        assert trade["pnl"] is None
        # No orphan red-flag ping fired.
        assert len(list(pings_dir.glob("*-orphanflag.json"))) == 0

    def test_recent_package_not_touched(self, tmp_db, monkeypatch):
        """Defence boundary: a package stuck only 5 min (under the
        30 min default) must NOT be touched. The orphan reconciler
        + stuck-linked sweep are the first lines of defence; the
        watchdog only fires after they've had ample time to act.
        """
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

    def test_idempotent_across_consecutive_ticks(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """The first tick force-closes the package + emits the
        alert. The second tick must be a complete no-op — the
        package is now ``status='closed'`` so the WHERE no longer
        matches, AND no fresh alert ping is queued.
        """
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
            "recovered_closed": 0,
            "closed_local_unmatched": 0,
            "deferred_position_alive": 0,
            "deferred_below_timeframe": 0,
            "skipped_position_read_failed": 0,
            "errors": 0,
        }
        # Only ONE stuck-watchdog ping across both ticks (idempotent). The row is
        # finalised CLOSED on tick 1 (no orphan red-flag ping) and is no longer
        # status='open' on tick 2, so it isn't re-touched.
        assert len([p for p in pings_dir.glob("*.json")
                    if not p.name.endswith("-orphanflag.json")]) == 1
        assert len(list(pings_dir.glob("*-orphanflag.json"))) == 0

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
        # A position-alive deferral is benign — the ping must be the
        # informational variant, NOT the "investigate a reconciler skip"
        # wording (that text falsely reads like a bug for a healthy,
        # patiently-held trend trade).
        assert evt["priority"] == "normal"
        assert "informational" in evt["body"].lower()
        assert "CONFIRMED ALIVE" in evt["body"]
        assert "Investigate" not in evt["body"]
        assert "reconciler skip" not in evt["body"]

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

    def test_position_alive_defers_indefinitely_for_long_silent_pkg(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """Position-alive defer is indefinite (2026-06-07): a package
        silent for hours never gets force-closed by the watchdog when
        the exchange still reports the position as alive. The strategy
        keeps owning the trade via its ``monitor()`` hook, the gate
        stays closed (one open package per strategy), and only the
        per-trade reconciler / strategy verdict can close the package.

        Replaces the 2026-05-16 ``RELEASE_STUCK_PKG_MINUTES`` knob,
        which closed the package after 90 min on a wrong premise
        (run_monitor_tick scans ``status='open'`` only, so closing
        stranded the trade with no strategy monitoring).
        """
        trade_id = _insert_trade(tmp_db, status="open")
        # 4h silent, well past any historic release threshold.
        self._insert_pkg_with_age(
            tmp_db, pkg_id="pkg-alive-long-silent",
            linked_trade_id=trade_id, age_minutes=240,
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
        assert summary["auto_cleared"] == 0
        assert summary["alerted"] == 1
        assert summary["errors"] == 0
        # Package row stays OPEN — strategy monitor keeps running.
        assert _read_package(tmp_db, "pkg-alive-long-silent")["status"] == "open"
        # Trade row untouched.
        assert _read_trade(tmp_db, trade_id)["status"] == "open"
        # Alert fires with auto_cleared=False — the gate is still closed.
        queued = sorted(pings_dir.glob("*.json"))
        assert len(queued) == 1
        evt = json.loads(queued[0].read_text())
        assert "force-cleared" not in evt["body"]

    # -- Timeframe-aware quiet window (2026-05-25) -----------------------

    def test_timeframe_aware_2h_below_threshold_not_alerted(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """A 2h strategy's package is silent for 50 min — past the flat
        30-min floor but well under its timeframe-scaled threshold
        (3 x 120 = 360 min). With the position alive at the exchange it
        is NOT a stuck trade (the Chandelier trail just hasn't ratcheted
        within one bar), so the watchdog must skip it silently: no alert,
        no meta churn, package + trade untouched.
        """
        monkeypatch.delenv("STUCK_STRATEGY_THRESHOLD_MINUTES", raising=False)
        monkeypatch.delenv("STUCK_STRATEGY_TIMEFRAME_MULT", raising=False)
        trade_id = _insert_trade(tmp_db, status="open")
        self._insert_pkg_with_age(
            tmp_db, pkg_id="pkg-2h-quiet", linked_trade_id=trade_id,
            age_minutes=50, strategy="trend_donchian",
            meta={"timeframe": "2h"},
        )
        pings_dir = tmp_path / "pings"
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR", pings_dir,
        )
        with patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[{"symbol": "BTCUSDT", "side": "long", "size": 0.001}],
        ):
            summary = _watchdog_stuck_strategies(tmp_db)

        assert summary["deferred_below_timeframe"] == 1
        assert summary["deferred_position_alive"] == 0
        assert summary["alerted"] == 0
        assert summary["auto_cleared"] == 0
        assert _read_package(tmp_db, "pkg-2h-quiet")["status"] == "open"
        assert _read_trade(tmp_db, trade_id)["status"] == "open"
        assert not list(pings_dir.glob("*.json"))

    def test_timeframe_aware_2h_above_threshold_alerts(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """Once a 2h package has been silent past its timeframe-scaled
        threshold (>360 min) the watchdog DOES fire (position-alive
        defer + one alert), and the alert reports the per-package
        threshold (360 min), not the flat 30-min floor.
        """
        monkeypatch.delenv("STUCK_STRATEGY_THRESHOLD_MINUTES", raising=False)
        monkeypatch.delenv("STUCK_STRATEGY_TIMEFRAME_MULT", raising=False)
        trade_id = _insert_trade(tmp_db, status="open")
        self._insert_pkg_with_age(
            tmp_db, pkg_id="pkg-2h-stuck", linked_trade_id=trade_id,
            age_minutes=400, strategy="trend_donchian",
            meta={"timeframe": "2h"},
        )
        pings_dir = tmp_path / "pings"
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR", pings_dir,
        )
        with patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[{"symbol": "BTCUSDT", "side": "long", "size": 0.001}],
        ):
            summary = _watchdog_stuck_strategies(tmp_db)

        assert summary["deferred_below_timeframe"] == 0
        assert summary["deferred_position_alive"] == 1
        assert summary["alerted"] == 1
        queued = sorted(pings_dir.glob("*.json"))
        assert len(queued) == 1
        body = json.loads(queued[0].read_text())["body"]
        assert "360 min" in body  # timeframe-aware threshold, not 30
        assert "force-cleared" not in body

    def test_timeframe_aware_5m_unaffected_trips_at_floor(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """A 5m strategy keeps the flat 30-min floor (3 x 5 = 15 < 30),
        so a 45-min-silent position-alive package still alerts — the
        change must not blind the watchdog on short timeframes.
        """
        monkeypatch.delenv("STUCK_STRATEGY_THRESHOLD_MINUTES", raising=False)
        monkeypatch.delenv("STUCK_STRATEGY_TIMEFRAME_MULT", raising=False)
        trade_id = _insert_trade(tmp_db, status="open")
        self._insert_pkg_with_age(
            tmp_db, pkg_id="pkg-5m-stuck", linked_trade_id=trade_id,
            age_minutes=45, strategy="ict_scalp_5m",
            meta={"timeframe": "5m"},
        )
        pings_dir = tmp_path / "pings"
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR", pings_dir,
        )
        with patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[{"symbol": "BTCUSDT", "side": "long", "size": 0.001}],
        ):
            summary = _watchdog_stuck_strategies(tmp_db)

        assert summary["deferred_below_timeframe"] == 0
        assert summary["deferred_position_alive"] == 1
        assert summary["alerted"] == 1

    def test_timeframe_aware_orphan_still_caught_at_floor(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """The timeframe gate only quiets BENIGN (position-alive) alerts.
        A genuine orphan (position flat at the exchange) on a 2h strategy
        must still be force-cleared at the flat floor (45 min here) — NOT
        delayed to the 360-min timeframe threshold — so orphan cleanup
        stays fast and the strategy gate doesn't stay stuck.
        """
        monkeypatch.delenv("STUCK_STRATEGY_THRESHOLD_MINUTES", raising=False)
        monkeypatch.delenv("STUCK_STRATEGY_TIMEFRAME_MULT", raising=False)
        trade_id = _insert_trade(tmp_db, status="open")
        self._insert_pkg_with_age(
            tmp_db, pkg_id="pkg-2h-orphan", linked_trade_id=trade_id,
            age_minutes=45, strategy="trend_donchian",
            meta={"timeframe": "2h"},
        )
        pings_dir = tmp_path / "pings"
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR", pings_dir,
        )
        with patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[],  # position flat → genuine orphan
        ):
            summary = _watchdog_stuck_strategies(tmp_db)

        assert summary["deferred_below_timeframe"] == 0
        assert summary["auto_cleared"] == 1
        assert _read_package(tmp_db, "pkg-2h-orphan")["status"] == "closed"
        # Position flat + broker closed-pnl unmatched → finalised CLOSED
        # (local-compute pnl), not orphaned (BL-20260620-WATCHDOGORPHAN).
        assert _read_trade(tmp_db, trade_id)["status"] == "closed"


class TestTimeframeAwareThresholdHelpers:
    """Unit coverage for the timeframe parsing + per-package threshold."""

    @pytest.mark.parametrize("tf,exp", [
        ("5m", 5.0), ("15m", 15.0), ("1h", 60.0), ("2h", 120.0),
        ("4h", 240.0), ("1d", 1440.0), ("120", 120.0),
        ("", None), (None, None), ("bogus", None), ("0h", None),
    ])
    def test_timeframe_to_minutes(self, tf, exp):
        assert _timeframe_to_minutes(tf) == exp

    def test_threshold_defaults(self, monkeypatch):
        monkeypatch.delenv("STUCK_STRATEGY_THRESHOLD_MINUTES", raising=False)
        monkeypatch.delenv("STUCK_STRATEGY_TIMEFRAME_MULT", raising=False)
        assert _stuck_threshold_for_package({"timeframe": "5m"}) == 30.0
        assert _stuck_threshold_for_package({"timeframe": "2h"}) == 360.0
        assert _stuck_threshold_for_package({"timeframe": "4h"}) == 720.0
        assert _stuck_threshold_for_package({}) == 30.0
        assert _stuck_threshold_for_package(None) == 30.0

    def test_threshold_respects_mult_env(self, monkeypatch):
        monkeypatch.delenv("STUCK_STRATEGY_THRESHOLD_MINUTES", raising=False)
        monkeypatch.setenv("STUCK_STRATEGY_TIMEFRAME_MULT", "2")
        assert _stuck_threshold_for_package({"timeframe": "2h"}) == 240.0

    def test_threshold_floor_dominates_for_short_tf(self, monkeypatch):
        monkeypatch.setenv("STUCK_STRATEGY_THRESHOLD_MINUTES", "60")
        monkeypatch.delenv("STUCK_STRATEGY_TIMEFRAME_MULT", raising=False)
        # 3 x 5m = 15 < 60 floor → floor wins.
        assert _stuck_threshold_for_package({"timeframe": "5m"}) == 60.0


# ---------------------------------------------------------------------------
# Package cascade by canonical link (PR claude/cascade-fix-by-linked-trade-id)
# ---------------------------------------------------------------------------


class TestPackageCascadeByLinkedTradeId:
    """Regression coverage for the cascade misroute discovered in
    diag #1292 (2026-05-16).

    Pre-2026-05-16 production trade rows did NOT carry
    ``order_package_id`` in their ``notes`` JSON (the live writer
    in ``_log_trade_to_journal`` only stamps ``trade_id``). The
    cascade paths in ``_close_trade_from_order_status`` and
    ``_mark_orphaned`` looked the package id up via
    ``_extract_package_id(row.notes)``, which fell back to
    ``notes.trade_id`` (the Bybit UUID) when ``order_package_id``
    was absent — that UUID was then passed to
    ``db.update_order_package`` and silently matched zero rows.

    ``_sweep_stuck_linked_packages`` was doing the cascade work in
    a second pass and stamping every recovered row with
    ``close_reason='stuck_cascade_recovered'``, hiding the latent
    bug behind a "recovered" label that suggested something
    abnormal had happened.

    The fix: route the cascade through ``_resolve_linked_package_id``
    (a ``WHERE linked_trade_id = ?`` lookup on the packages table),
    so the direct cascade actually fires and stamps the correct
    ``close_reason='reconciler_filled'`` (or ``'reconciler'`` for the
    orphan path) on the same tick.
    """

    def test_resolve_linked_package_id_happy_path(self, tmp_db):
        trade_id = _insert_trade(tmp_db)
        _insert_package(tmp_db, pkg_id="pkg-link-1", linked_trade_id=trade_id)
        resolved = _resolve_linked_package_id(tmp_db, trade_id)
        assert resolved == "pkg-link-1"

    def test_resolve_linked_package_id_returns_none_when_no_package(
        self, tmp_db,
    ):
        trade_id = _insert_trade(tmp_db)
        assert _resolve_linked_package_id(tmp_db, trade_id) is None

    def test_resolve_linked_package_id_handles_none_trade_id(self, tmp_db):
        assert _resolve_linked_package_id(tmp_db, None) is None

    def test_extract_package_id_no_longer_falls_back_to_trade_id(self):
        """Pre-fix this returned ``notes.trade_id`` when
        ``order_package_id`` was absent — the silent cascade bug.
        """
        notes = json.dumps({"trade_id": "1842564317108924672"})
        assert _extract_package_id(notes) is None

    def test_extract_package_id_still_reads_explicit_field(self):
        notes = json.dumps({
            "trade_id": "1842564317108924672",
            "order_package_id": "pkg-explicit-1",
        })
        assert _extract_package_id(notes) == "pkg-explicit-1"

    def test_cascade_close_succeeds_without_notes_pkg_id(self, tmp_db):
        """The production scenario: trade.notes carries only
        trade_id (no order_package_id). The cascade must still find
        and close the package via linked_trade_id.
        """
        trade_id = _insert_trade(tmp_db)  # no notes_pkg_id
        _insert_package(
            tmp_db, pkg_id="pkg-cascade-prod",
            linked_trade_id=trade_id,
        )
        ok = _cascade_close_linked_package(
            tmp_db, trade_id,
            close_reason="reconciler_filled",
            caller="test_cascade_prod",
        )
        assert ok is True
        pkg = _read_package(tmp_db, "pkg-cascade-prod")
        assert pkg["status"] == "closed"
        assert pkg["close_reason"] == "reconciler_filled"

    def test_cascade_close_returns_false_when_no_link(self, tmp_db):
        trade_id = _insert_trade(tmp_db)
        ok = _cascade_close_linked_package(
            tmp_db, trade_id,
            close_reason="reconciler_filled",
            caller="test_no_link",
        )
        assert ok is False

    def test_reconciler_close_path_stamps_reconciler_filled_directly(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """End-to-end: a trade whose notes carry only trade_id (the
        production shape) flows through ``_reconcile_open_trades``;
        on a filled-order + position-flat verdict the linked
        package row is closed with ``close_reason='reconciler_filled'``
        in the same tick — NOT ``'stuck_cascade_recovered'``.
        """
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            tmp_path / "pings",
        )
        bybit_uuid = "eac9d644-fb24-44f8-889b-c0ec6a98363e"
        trade_id = _insert_trade(tmp_db, trade_id=bybit_uuid)
        _insert_package(
            tmp_db, pkg_id="pkg-prod-shape",
            linked_trade_id=trade_id,
        )

        with patch(
            "src.units.accounts.clients.account_order_status",
            return_value=_filled_status(bybit_uuid),
        ), patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[],
        ):
            _reconcile_to_close(tmp_db)

        pkg = _read_package(tmp_db, "pkg-prod-shape")
        assert pkg["status"] == "closed"
        assert pkg["close_reason"] == "reconciler_filled"

    def test_orphan_cascade_stamps_reconciler_directly(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """Same as above but for ``_mark_orphaned`` — when Bybit
        says ``not_found`` the trade is orphaned and the linked
        package closes with ``close_reason='reconciler'`` in the
        same tick, not via the sweep.
        """
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            tmp_path / "pings",
        )
        trade_id = _insert_trade(tmp_db, trade_id="1900000000001230001")
        _insert_package(
            tmp_db, pkg_id="pkg-orphan-prod",
            linked_trade_id=trade_id,
        )

        with patch(
            "src.units.accounts.clients.account_order_status",
            return_value=_not_found_status("1900000000001230001"),
        ):
            _reconcile_open_trades(tmp_db)

        pkg = _read_package(tmp_db, "pkg-orphan-prod")
        assert pkg["status"] == "closed"
        assert pkg["close_reason"] == "reconciler"


# ---------------------------------------------------------------------------
# Exit-price recovery from Bybit V5 closed-pnl
# (PR claude/exit-price-from-closed-pnl)
# ---------------------------------------------------------------------------


class TestExitPriceFromClosedPnl:
    """The reconciler-close path used to leave ``exit_price=NULL``
    for trades closed via Bybit's broker-side SL/TP — the entry
    order's avg_price is the entry fill, not the exit fill, and
    the actual close lives on a separate orderId the bot doesn't
    track. This PR sources the real exit fill from
    ``/v5/position/closed-pnl`` via
    :func:`account_closed_pnl_for_trade`.

    The contract:
      * lookup succeeds → trade closes with the real ``exit_price``
        and ``notes.exit_price_source='bybit_closed_pnl'``
      * lookup fails or no record → trade closes with
        ``exit_price=NULL`` and the pre-PR
        ``notes.exit_price_source='entry_order_avg_price_unreliable'``
        fallback (gate clears, exit_price is honestly missing)
    """

    _ACCOUNTS_YAML = _ACCOUNTS_YAML  # reuse module fixture text

    def test_close_writes_real_exit_price_when_closed_pnl_available(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            tmp_path / "pings",
        )
        cfg_path = tmp_path / "accounts.yaml"
        cfg_path.write_text(self._ACCOUNTS_YAML)
        monkeypatch.setenv("ACCOUNTS_YAML_PATH", str(cfg_path))

        bybit_uuid = "1900000000000000700"
        trade_id = _insert_trade(tmp_db, trade_id=bybit_uuid)

        closed_pnl_payload = {
            "avg_exit_price": 79235.7,
            "avg_entry_price": 80000.0,
            "closed_pnl": -3.82,
            "qty": 0.005,
            "side": "Sell",
            "closed_at": "1762620000000",
        }

        with patch(
            "src.units.accounts.clients.account_order_status",
            return_value=_filled_status(bybit_uuid, avg_price=80000.0),
        ), patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[],
        ), patch(
            "src.units.accounts.clients.account_closed_pnl_for_trade",
            return_value=closed_pnl_payload,
        ):
            summary = _reconcile_to_close(tmp_db)

        assert summary["closed"] == 1
        row = _read_trade_full(tmp_db, trade_id)
        assert row["status"] == "closed"
        assert row["exit_reason"] == "reconciler_filled"
        assert row["exit_price"] is not None
        assert abs(row["exit_price"] - 79235.7) < 1e-6
        notes = json.loads(row["notes"])
        assert notes["exit_price_source"] == "bybit_closed_pnl"
        assert notes["bybit_closed_pnl"] == -3.82
        # closed_at normalised from Bybit's epoch-ms to ISO at the writer
        # (BL-20260620-RECONCILER-CLOSEDAT-MS) — was "1762620000000".
        assert notes["closed_at"] == "2025-11-08T16:40:00+00:00"

    def test_close_falls_back_to_null_when_closed_pnl_unavailable(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """``account_closed_pnl_for_trade`` returns ``None`` —
        the trade still closes (gate clears) but ``exit_price``
        stays NULL with the unreliable-source flag."""
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            tmp_path / "pings",
        )
        cfg_path = tmp_path / "accounts.yaml"
        cfg_path.write_text(self._ACCOUNTS_YAML)
        monkeypatch.setenv("ACCOUNTS_YAML_PATH", str(cfg_path))

        bybit_uuid = "1900000000000000800"
        trade_id = _insert_trade(tmp_db, trade_id=bybit_uuid)

        with patch(
            "src.units.accounts.clients.account_order_status",
            return_value=_filled_status(bybit_uuid),
        ), patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[],
        ), patch(
            "src.units.accounts.clients.account_closed_pnl_for_trade",
            return_value=None,
        ):
            _reconcile_to_close(tmp_db)

        row = _read_trade_full(tmp_db, trade_id)
        assert row["status"] == "closed"
        assert row["exit_reason"] == "reconciler_filled"
        assert row["exit_price"] is None
        notes = json.loads(row["notes"])
        assert notes["exit_price_source"] == "entry_order_avg_price_unreliable"

    def test_close_falls_back_to_null_when_no_exit_price_in_record(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """Closed-pnl record present but ``avg_exit_price`` is 0 /
        missing — degrade to the NULL fallback rather than write
        a zero. Defends against malformed records."""
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            tmp_path / "pings",
        )
        cfg_path = tmp_path / "accounts.yaml"
        cfg_path.write_text(self._ACCOUNTS_YAML)
        monkeypatch.setenv("ACCOUNTS_YAML_PATH", str(cfg_path))

        bybit_uuid = "1900000000000000900"
        trade_id = _insert_trade(tmp_db, trade_id=bybit_uuid)

        with patch(
            "src.units.accounts.clients.account_order_status",
            return_value=_filled_status(bybit_uuid),
        ), patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[],
        ), patch(
            "src.units.accounts.clients.account_closed_pnl_for_trade",
            return_value={"avg_exit_price": 0.0, "closed_pnl": 0.0,
                          "qty": 0.005, "side": "Sell", "closed_at": None},
        ):
            _reconcile_to_close(tmp_db)

        row = _read_trade_full(tmp_db, trade_id)
        assert row["status"] == "closed"
        assert row["exit_price"] is None
        notes = json.loads(row["notes"])
        assert notes["exit_price_source"] == "entry_order_avg_price_unreliable"

    def test_close_falls_back_when_closed_pnl_lookup_raises(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """If the closed-pnl helper raises (programming error,
        not a SDK-level failure), the close path catches and
        degrades to the NULL fallback rather than re-raise. The
        trade row close is more important than the exit-price
        recovery; an exception here must NOT block the gate clear.
        """
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            tmp_path / "pings",
        )
        cfg_path = tmp_path / "accounts.yaml"
        cfg_path.write_text(self._ACCOUNTS_YAML)
        monkeypatch.setenv("ACCOUNTS_YAML_PATH", str(cfg_path))

        bybit_uuid = "1900000000000001000"
        trade_id = _insert_trade(tmp_db, trade_id=bybit_uuid)

        with patch(
            "src.units.accounts.clients.account_order_status",
            return_value=_filled_status(bybit_uuid),
        ), patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[],
        ), patch(
            "src.units.accounts.clients.account_closed_pnl_for_trade",
            side_effect=RuntimeError("simulated SDK explosion"),
        ):
            _reconcile_to_close(tmp_db)

        row = _read_trade_full(tmp_db, trade_id)
        assert row["status"] == "closed"
        assert row["exit_price"] is None
        notes = json.loads(row["notes"])
        assert notes["exit_price_source"] == "entry_order_avg_price_unreliable"

    def test_select_supplies_entry_price_and_qty_to_closed_pnl_lookup(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """Regression for BL-20260630-RECONCILER-SELECT-MISSING-COLS.

        ``_reconcile_open_trades`` formerly selected only 6 columns:
        ``id, account_id, symbol, direction, notes, created_at``.
        ``entry_price`` and ``position_size`` were absent, so
        ``row.get("entry_price")`` / ``row.get("position_size")`` both
        resolved to ``None`` → ``_safe_float(None)`` → ``0.0`` → the
        ``> 0`` guards in ``_bybit_closed_pnl_lookup`` disabled both
        disambiguation filters, leaving only symbol+side+time-window to
        match the closed-pnl record (wrong record on a busy symbol).

        After the fix the SELECT includes ``entry_price, position_size,
        setup_type``; this test pins that contract by capturing the
        ``qty`` and ``entry_price`` arguments actually forwarded to
        ``account_closed_pnl_for_trade`` and asserting they match the
        values written to the DB (0.005 and 80000.0 from
        ``_insert_trade``).
        """
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            tmp_path / "pings",
        )
        cfg_path = tmp_path / "accounts.yaml"
        cfg_path.write_text(self._ACCOUNTS_YAML)
        monkeypatch.setenv("ACCOUNTS_YAML_PATH", str(cfg_path))

        bybit_uuid = "1900000000000099999"
        _insert_trade(tmp_db, trade_id=bybit_uuid)

        captured_calls: list = []

        def _capture_closed_pnl(account_cfg, *, symbol, direction, qty, entry_price, **kw):
            captured_calls.append({"qty": qty, "entry_price": entry_price})
            return None  # fallback path — trade still closes

        with patch(
            "src.units.accounts.clients.account_order_status",
            return_value=_filled_status(bybit_uuid),
        ), patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[],
        ), patch(
            "src.units.accounts.clients.account_closed_pnl_for_trade",
            side_effect=_capture_closed_pnl,
        ):
            _reconcile_to_close(tmp_db)

        assert len(captured_calls) >= 1, (
            "account_closed_pnl_for_trade was not called — "
            "the reconciler close path may have changed"
        )
        call = captured_calls[0]
        assert call["qty"] == pytest.approx(0.005), (
            "position_size not propagated: got %s (was 0.0 pre-fix)" % call["qty"]
        )
        assert call["entry_price"] == pytest.approx(80000.0), (
            "entry_price not propagated: got %s (was 0.0 pre-fix)" % call["entry_price"]
        )


# ---------------------------------------------------------------------------
# Position-netting guard — reconciler half (Option A, BL-20260608-DEMOPNL)
# ---------------------------------------------------------------------------


class TestNettingGuardCloseConfirmation:
    """**BASELINE (2026-06-17): the close-confirm is unconditional.** A filled
    trade that reads net-flat is not closed on the FIRST observation — it must
    read flat across an extra grace tick (a second observation,
    ``RECONCILER_CLOSE_CONFIRM_SECONDS`` apart) before the close lands. A
    transient flat (an intent reduce/flip leg or index lag) that recovers
    to "position open" on a later tick clears the pending confirmation, so
    it can never prematurely close the row and free the strategy-monocle.

    The default-off ``POSITION_NETTING_GUARD_ENABLED`` gate and the
    ``POSITION_NETTING_GUARD_ACCOUNTS`` scope were removed — the close-confirm
    now applies to every account, regardless of env (a leftover env value is
    ignored). ``RECONCILER_CLOSE_CONFIRM_SECONDS`` remains as the timing knob.
    """

    @pytest.fixture(autouse=True)
    def _clear_pending(self):
        from src.runtime import order_monitor as _om
        _om._PENDING_CLOSE_CONFIRM.clear()
        yield
        _om._PENDING_CLOSE_CONFIRM.clear()

    def test_first_flat_observation_defers_close(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("POSITION_NETTING_GUARD_ENABLED", "true")
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            tmp_path / "pings",
        )
        trade_id = _insert_trade(tmp_db, trade_id="2200000000000000001")

        with patch(
            "src.units.accounts.clients.account_order_status",
            return_value=_filled_status("2200000000000000001"),
        ), patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[],
        ):
            summary = _reconcile_open_trades(tmp_db)

        assert summary["closed"] == 0
        assert summary["pending_close"] == 1
        assert _read_trade(tmp_db, trade_id)["status"] == "open"

    def test_second_flat_observation_confirms_close(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        monkeypatch.setenv("POSITION_NETTING_GUARD_ENABLED", "true")
        # Window 0 → the SECOND observation alone confirms (pure extra tick).
        monkeypatch.setenv("RECONCILER_CLOSE_CONFIRM_SECONDS", "0")
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            tmp_path / "pings",
        )
        trade_id = _insert_trade(tmp_db, trade_id="2200000000000000002")

        with patch(
            "src.units.accounts.clients.account_order_status",
            return_value=_filled_status("2200000000000000002"),
        ), patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[],
        ):
            s1 = _reconcile_open_trades(tmp_db)
            s2 = _reconcile_open_trades(tmp_db)

        assert s1["closed"] == 0 and s1["pending_close"] == 1
        assert s2["closed"] == 1
        assert _read_trade(tmp_db, trade_id)["status"] == "closed"

    def test_transient_flat_then_open_clears_pending(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """A single net-flat tick followed by a "position open" tick must
        NOT close the trade — the churn case the guard exists to fix."""
        monkeypatch.setenv("POSITION_NETTING_GUARD_ENABLED", "true")
        monkeypatch.setenv("RECONCILER_CLOSE_CONFIRM_SECONDS", "0")
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            tmp_path / "pings",
        )
        trade_id = _insert_trade(
            tmp_db, trade_id="2200000000000000003", direction="long",
        )

        flat = patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[],
        )
        open_pos = patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[{"symbol": "BTCUSDT", "side": "Buy", "size": 0.005}],
        )
        status = patch(
            "src.units.accounts.clients.account_order_status",
            return_value=_filled_status("2200000000000000003"),
        )

        with status, flat:
            s1 = _reconcile_open_trades(tmp_db)  # first flat → pending
        with status, open_pos:
            s2 = _reconcile_open_trades(tmp_db)  # position back → clears pending
        with status, flat:
            s3 = _reconcile_open_trades(tmp_db)  # flat again → pending, NOT close

        assert s1["pending_close"] == 1 and s1["closed"] == 0
        assert s2["closed"] == 0
        assert s3["closed"] == 0, "transient flat must not have closed the row"
        assert s3["pending_close"] == 1
        assert _read_trade(tmp_db, trade_id)["status"] == "open"

    def test_no_env_still_defers_on_first_flat(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """No env (gate removed) → the close-confirm STILL applies: the first
        net-flat defers (baseline / unconditional)."""
        monkeypatch.delenv("POSITION_NETTING_GUARD_ENABLED", raising=False)
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            tmp_path / "pings",
        )
        trade_id = _insert_trade(tmp_db, trade_id="2200000000000000004")

        with patch(
            "src.units.accounts.clients.account_order_status",
            return_value=_filled_status("2200000000000000004"),
        ), patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[],
        ):
            summary = _reconcile_open_trades(tmp_db)

        assert summary["closed"] == 0
        assert summary["pending_close"] == 1
        assert _read_trade(tmp_db, trade_id)["status"] == "open"

    def test_legacy_scope_env_excluding_account_is_ignored(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """A leftover ``POSITION_NETTING_GUARD_ACCOUNTS=bybit_1`` no longer
        scopes the guard OUT for bybit_2 — the close-confirm STILL applies, so
        the bybit_2 net-flat trade defers (the scope env is a no-op)."""
        monkeypatch.setenv("POSITION_NETTING_GUARD_ENABLED", "true")
        monkeypatch.setenv("POSITION_NETTING_GUARD_ACCOUNTS", "bybit_1")
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            tmp_path / "pings",
        )
        trade_id = _insert_trade(
            tmp_db, account_id="bybit_2", trade_id="2200000000000000005",
        )

        with patch(
            "src.units.accounts.clients.account_order_status",
            return_value=_filled_status("2200000000000000005"),
        ), patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[],
        ):
            summary = _reconcile_open_trades(tmp_db)

        assert summary["closed"] == 0
        assert summary["pending_close"] == 1
        assert _read_trade(tmp_db, trade_id)["status"] == "open"

    def test_defers_close_for_any_account(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """The close-confirm applies to every account unconditionally → a
        bybit_2 first net-flat defers (extra grace tick), not closed."""
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            tmp_path / "pings",
        )
        trade_id = _insert_trade(
            tmp_db, account_id="bybit_2", trade_id="2200000000000000006",
        )

        with patch(
            "src.units.accounts.clients.account_order_status",
            return_value=_filled_status("2200000000000000006"),
        ), patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[],
        ):
            summary = _reconcile_open_trades(tmp_db)

        assert summary["closed"] == 0
        assert summary["pending_close"] == 1
        assert _read_trade(tmp_db, trade_id)["status"] == "open"
