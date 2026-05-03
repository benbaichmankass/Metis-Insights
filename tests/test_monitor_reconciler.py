"""BUG-042 PR 2 — monitor-loop write-back reconciler.

Pins the contract for ``src.runtime.order_monitor._reconcile_open_trades``:
the always-on automated equivalent of
``notebooks/operator/cleanup_ghost_trades.ipynb``. Each tick compares
``trades.status='open'`` against the exchange's ``account_open_positions``
list per account; any DB-open / exchange-flat row gets re-tagged
``status='orphaned'`` with ``exit_reason='reconciler'`` and one
diagnostic ping is enqueued (capped per tick + a single roll-up for
the rest).

Ten contracts under test (one per sprint-plan bullet):

1. Empty ``trades`` table → no-op.
2. DB-open + exchange-flat → ``status='orphaned'``, package
   cascaded, ping enqueued with the pinned payload shape.
3. DB-open + exchange-open → no change (untouched row stays
   ``status='open'``).
4. Account with missing creds (``account_open_positions`` returns
   ``None``) → skip account, do NOT orphan its rows.
5. Dry-run account (``mode='dry_run'`` in accounts.yaml) → skip.
6. ``MONITOR_RECONCILE_ENABLED=false`` (default) → reconciler is a
   no-op even with stale rows present.
7. ``MONITOR_RECONCILE_ENABLED=true`` → full happy path.
8. Symbol-side dedup: same symbol, both long + short rows in DB,
   only short open on exchange → long gets orphaned, short stays.
9. Idempotency: running the reconciler twice in a row with no
   state change between calls → second call is a no-op.
10. Ping payload shape pinned: account_id, symbol, side,
    db_trade_id, linked_package_id, reason='reconciler'.
"""
from __future__ import annotations

import json
import textwrap
from unittest.mock import patch

import pytest

from src.runtime.order_monitor import (
    _exchange_position_set,
    _reconcile_open_trades,
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


def _insert_trade(
    db,
    *,
    account_id="bybit_2",
    symbol="BTCUSDT",
    direction="long",
    status="open",
    notes_pkg_id=None,
):
    """Insert an open trade and return its DB id. Mirrors the shape
    written by ``_log_trade_to_journal`` so the reconciler reads
    realistic rows."""
    notes = {"trade_id": "t-stub"}
    if notes_pkg_id:
        notes["order_package_id"] = notes_pkg_id
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
    })
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT id FROM trades ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return int(row[0])
    finally:
        conn.close()


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

    # Patch the YAML loader to return our test accounts dict so we
    # don't have to fight the real config/accounts.yaml.
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
# Contracts 1-2: no-op + happy path orphan
# ---------------------------------------------------------------------------


def test_empty_trades_table_is_noop(tmp_db):
    summary = _reconcile_open_trades(tmp_db)
    assert summary["checked"] == 0
    assert summary["orphaned"] == 0


def test_db_open_exchange_flat_marks_orphaned_and_pings(tmp_db, tmp_path, monkeypatch):
    """The headline contract: a DB-open trade whose exchange-side
    counterpart is no longer present gets re-tagged ``orphaned`` and
    a diagnostic ping is enqueued.
    """
    pkg_id = "pkg-orphan-001"
    _insert_package(tmp_db, pkg_id=pkg_id)
    trade_id = _insert_trade(tmp_db, notes_pkg_id=pkg_id)
    # Link the package to the trade so the cascade has work to do.
    tmp_db.update_order_package(pkg_id, {"linked_trade_id": trade_id})

    pings_dir = tmp_path / "pending_pings"
    monkeypatch.setattr(
        "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
        pings_dir,
    )
    # Exchange returns an empty positions list → no live (sym, side).
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[],
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


# ---------------------------------------------------------------------------
# Contract 3: DB-open + exchange-open → no change
# ---------------------------------------------------------------------------


def test_db_open_exchange_open_leaves_row_alone(tmp_db):
    trade_id = _insert_trade(tmp_db, direction="long")

    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[{
            "symbol": "BTCUSDT", "side": "Buy",
            "size": 0.005, "entry_price": 80000.0, "unrealised_pnl": 0,
        }],
    ):
        summary = _reconcile_open_trades(tmp_db)

    assert summary["orphaned"] == 0
    assert _read_trade(tmp_db, trade_id)["status"] == "open"


# ---------------------------------------------------------------------------
# Contract 4: missing creds → skip account, do NOT orphan
# ---------------------------------------------------------------------------


def test_missing_creds_skips_account_no_orphan(tmp_db):
    trade_id = _insert_trade(tmp_db)

    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=None,  # missing creds / exchange error
    ):
        summary = _reconcile_open_trades(tmp_db)

    assert summary["orphaned"] == 0
    assert summary["skipped_no_creds"] == 1
    assert _read_trade(tmp_db, trade_id)["status"] == "open"


# ---------------------------------------------------------------------------
# Contract 5: dry-run account → skip
# ---------------------------------------------------------------------------


def test_dry_run_account_is_skipped(tmp_db):
    trade_id = _insert_trade(tmp_db, account_id="bybit_dry")

    # Even though account_open_positions would return [], we should
    # never reach that call for a dry-run account.
    with patch(
        "src.units.accounts.clients.account_open_positions",
        side_effect=AssertionError("must not call account_open_positions for dry-run"),
    ):
        summary = _reconcile_open_trades(tmp_db)

    assert summary["orphaned"] == 0
    assert summary["skipped_dry"] == 1
    assert _read_trade(tmp_db, trade_id)["status"] == "open"


# ---------------------------------------------------------------------------
# Contract 6: feature-flag default off
# ---------------------------------------------------------------------------


def test_disabled_flag_is_noop(tmp_db, monkeypatch):
    _insert_trade(tmp_db)  # would be orphaned if reconciler ran
    monkeypatch.setenv("MONITOR_RECONCILE_ENABLED", "false")

    with patch(
        "src.units.accounts.clients.account_open_positions",
        side_effect=AssertionError("reconciler must not run when disabled"),
    ):
        summary = _reconcile_open_trades(tmp_db)

    assert summary == {
        "checked": 0, "orphaned": 0,
        "skipped_dry": 0, "skipped_no_creds": 0,
        "skipped_no_cfg": 0, "errors": 0,
    }


def test_unset_flag_is_noop(tmp_db, monkeypatch):
    _insert_trade(tmp_db)
    monkeypatch.delenv("MONITOR_RECONCILE_ENABLED", raising=False)

    with patch(
        "src.units.accounts.clients.account_open_positions",
        side_effect=AssertionError("reconciler must not run when env var unset"),
    ):
        summary = _reconcile_open_trades(tmp_db)

    assert summary["checked"] == 0
    assert summary["orphaned"] == 0


# ---------------------------------------------------------------------------
# Contract 7: explicit happy path with the flag on (already covered by
# test_db_open_exchange_flat_marks_orphaned_and_pings, but pin
# end-to-end with multiple rows for the same account).
# ---------------------------------------------------------------------------


def test_multiple_orphans_in_same_account_all_swept(tmp_db, tmp_path, monkeypatch):
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
        "src.units.accounts.clients.account_open_positions",
        return_value=[],
    ):
        summary = _reconcile_open_trades(tmp_db)

    assert summary["orphaned"] == 3
    for tid in ids:
        assert _read_trade(tmp_db, tid)["status"] == "orphaned"
    assert len(list(pings_dir.glob("*.json"))) == 3


# ---------------------------------------------------------------------------
# Contract 8: symbol-side dedup
# ---------------------------------------------------------------------------


def test_symbol_side_dedup_long_orphaned_short_kept(tmp_db, monkeypatch):
    """Same symbol with both long + short DB-open rows; the exchange
    only has the short side open → only the long row gets orphaned.
    """
    long_id = _insert_trade(tmp_db, symbol="BTCUSDT", direction="long")
    short_id = _insert_trade(tmp_db, symbol="BTCUSDT", direction="short")

    monkeypatch.setattr(
        "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
        monkeypatch.delenv.__self__.tmp_path
        if hasattr(monkeypatch.delenv.__self__, "tmp_path") else
        __import__("pathlib").Path("/tmp/pings_x"),
    )
    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[{
            "symbol": "BTCUSDT", "side": "Sell",
            "size": 0.005, "entry_price": 80000.0, "unrealised_pnl": 0,
        }],
    ):
        summary = _reconcile_open_trades(tmp_db)

    assert summary["orphaned"] == 1
    assert _read_trade(tmp_db, long_id)["status"] == "orphaned"
    assert _read_trade(tmp_db, short_id)["status"] == "open"


# ---------------------------------------------------------------------------
# Contract 9: idempotency
# ---------------------------------------------------------------------------


def test_two_consecutive_runs_idempotent(tmp_db, tmp_path, monkeypatch):
    trade_id = _insert_trade(tmp_db)
    monkeypatch.setattr(
        "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
        tmp_path / "pings",
    )

    with patch(
        "src.units.accounts.clients.account_open_positions",
        return_value=[],
    ):
        s1 = _reconcile_open_trades(tmp_db)
        s2 = _reconcile_open_trades(tmp_db)

    assert s1["orphaned"] == 1
    # Second pass: the row is now ``orphaned``, no longer matches
    # the open-trades SELECT, so the reconciler does nothing.
    assert s2["orphaned"] == 0
    assert s2["checked"] == 0
    assert _read_trade(tmp_db, trade_id)["status"] == "orphaned"


# ---------------------------------------------------------------------------
# Contract 10: position-set side normalisation (covered by 8 + this unit
# test on the helper)
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
