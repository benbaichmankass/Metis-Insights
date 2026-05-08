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
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.runtime.order_monitor import (
    _exchange_position_set,
    _mark_orphaned,
    _parse_created_at,
    _reconcile_open_trades,
    _sweep_stuck_linked_packages,
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
):
    """Insert an open trade and return its DB id. Mirrors the shape
    written by ``_log_trade_to_journal`` so the reconciler reads
    realistic rows.

    By default rows are backdated 1 hour into the past so they're
    older than the reconciler's grace window — tests that want to
    pin freshness behaviour pass ``age_seconds`` (or an explicit
    ``created_at``) directly.
    """
    notes = {"trade_id": "t-stub"}
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
        "skipped_no_cfg": 0, "skipped_recent": 0,
        "errors": 0,
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


# ---------------------------------------------------------------------------
# BUG-049: _sweep_unlinked_packages
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
# Grace window — race against Bybit's open-positions index lag
# (sibling of S-053; see docs/claude/debug-memory.md 2026-05-08 entry
# "monitor reconciler races freshly-placed trades")
# ---------------------------------------------------------------------------


class TestReconcilerGraceWindow:
    """A trade with ``created_at`` newer than the grace threshold must not
    be orphan-stamped, even when ``account_open_positions()`` returns an
    empty list. Bybit's market-order index lag (38 ms – several seconds)
    would otherwise false-positive freshly-placed real positions.
    """

    def test_recent_trade_skipped_when_exchange_flat(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """Headline contract: a 1-second-old trade must NOT be orphaned
        when the exchange returns no positions."""
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            tmp_path / "pings",
        )
        # Default grace = 60 s; a 1 s old row is well inside the window.
        trade_id = _insert_trade(tmp_db, age_seconds=1)

        with patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[],
        ):
            summary = _reconcile_open_trades(tmp_db)

        assert summary["orphaned"] == 0
        assert summary["skipped_recent"] == 1
        assert _read_trade(tmp_db, trade_id)["status"] == "open"

    def test_old_trade_still_orphaned_when_exchange_flat(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """A trade older than the grace threshold remains eligible for
        orphan-stamping — the fix is targeted, not a wholesale disable."""
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            tmp_path / "pings",
        )
        # 5 minutes > default 60 s window.
        trade_id = _insert_trade(tmp_db, age_seconds=300)

        with patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[],
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
        trade_id = _insert_trade(tmp_db, age_seconds=30)

        with patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[],
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
            "src.units.accounts.clients.account_open_positions",
            return_value=[],
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
        trade_id = _insert_trade(tmp_db, age_seconds=1)

        with patch(
            "src.units.accounts.clients.account_open_positions",
            return_value=[],
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
            "src.units.accounts.clients.account_open_positions",
            return_value=[],
        ):
            summary = _reconcile_open_trades(tmp_db)

        assert summary["orphaned"] == 0
        assert summary["skipped_recent"] == 1
        assert _read_trade(tmp_db, trade_id)["status"] == "open"

    def test_recent_row_does_not_trigger_exchange_call(
        self, tmp_db, tmp_path, monkeypatch,
    ):
        """If every row is fresh, the reconciler should never reach the
        exchange — saves an API hit on every tick after a fresh trade."""
        monkeypatch.setattr(
            "src.runtime.execution_diagnostics.PENDING_PINGS_DIR",
            tmp_path / "pings",
        )
        _insert_trade(tmp_db, age_seconds=1)

        with patch(
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
