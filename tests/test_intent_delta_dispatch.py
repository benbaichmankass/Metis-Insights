"""Intent-aware dispatch delta wiring — follow-up to PR #1125.

Covers two new pieces:

1. ``src/runtime/positions.py::current_net_position_qty`` — signed net
   position read from ``trade_journal.db``.
2. ``src/core/coordinator.py::Coordinator.multi_account_execute`` —
   intent-mode branch that swaps the binary ``_has_open_position`` guard
   for the ``compute_execution_delta`` computer.

The dispatch tests stub ``execute_pkg`` and the exchange-client
factories so we exercise the coordinator's decision logic without
touching the live exchange.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import textwrap
import types
from unittest import mock

import pytest


if "matplotlib" not in sys.modules:
    _mpl_stub = types.ModuleType("matplotlib")
    _mpl_stub.pyplot = mock.MagicMock()
    sys.modules["matplotlib"] = _mpl_stub
    sys.modules["matplotlib.pyplot"] = mock.MagicMock()


from src.core.coordinator import Coordinator, OrderPackage  # noqa: E402
from src.runtime.intents import (  # noqa: E402
    INTENT_MODE_META_KEY,
    INTENT_MODE_META_VALUE,
    compute_execution_delta_for_package,
    package_is_intent_mode,
    resolve_flip_policy,
)
from src.runtime.positions import current_net_position_qty  # noqa: E402


# ---------------------------------------------------------------------------
# Schema helper
# ---------------------------------------------------------------------------


def _init_trade_journal(path: str) -> None:
    """Create a minimal ``trades`` table at *path*.

    Schema mirrors ``src/units/db/database.py::create_tables`` for the
    columns this helper reads.
    """
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                stop_loss REAL,
                take_profit_1 REAL,
                position_size REAL NOT NULL,
                status TEXT DEFAULT 'open',
                is_backtest INTEGER DEFAULT 0,
                strategy_name TEXT,
                account_id TEXT NOT NULL DEFAULT 'live'
            )
            """
        )


def _insert_trade(
    path: str,
    *,
    account_id: str,
    symbol: str,
    direction: str,
    position_size: float,
    status: str = "open",
    is_backtest: int = 0,
    strategy_name: str | None = None,
) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "INSERT INTO trades (timestamp, symbol, direction, entry_price, "
            "position_size, status, is_backtest, account_id, strategy_name) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "2026-05-14T00:00:00Z", symbol, direction, 50_000.0,
                position_size, status, is_backtest, account_id, strategy_name,
            ),
        )


# ---------------------------------------------------------------------------
# current_net_position_qty
# ---------------------------------------------------------------------------


class TestCurrentNetPositionQty:
    def test_no_db_returns_zero(self, tmp_path):
        # Missing trade_journal.db is a fresh-deploy condition — treat as flat.
        path = str(tmp_path / "trade_journal.db")
        assert not os.path.exists(path)
        assert current_net_position_qty("bybit_2", "BTCUSDT", db_path=path) == 0.0

    def test_no_open_rows_returns_zero(self, tmp_path):
        path = str(tmp_path / "trade_journal.db")
        _init_trade_journal(path)
        assert current_net_position_qty("bybit_2", "BTCUSDT", db_path=path) == 0.0

    def test_single_long_row(self, tmp_path):
        path = str(tmp_path / "trade_journal.db")
        _init_trade_journal(path)
        _insert_trade(
            path, account_id="bybit_2", symbol="BTCUSDT",
            direction="long", position_size=0.03,
        )
        assert current_net_position_qty("bybit_2", "BTCUSDT", db_path=path) == pytest.approx(0.03)

    def test_single_short_row_is_negative(self, tmp_path):
        path = str(tmp_path / "trade_journal.db")
        _init_trade_journal(path)
        _insert_trade(
            path, account_id="bybit_2", symbol="BTCUSDT",
            direction="short", position_size=0.05,
        )
        assert current_net_position_qty("bybit_2", "BTCUSDT", db_path=path) == pytest.approx(-0.05)

    def test_sums_signed(self, tmp_path):
        """Two same-account, same-symbol open rows sum signed."""
        path = str(tmp_path / "trade_journal.db")
        _init_trade_journal(path)
        _insert_trade(
            path, account_id="bybit_2", symbol="BTCUSDT",
            direction="long", position_size=0.04,
        )
        _insert_trade(
            path, account_id="bybit_2", symbol="BTCUSDT",
            direction="long", position_size=0.02,
        )
        assert current_net_position_qty("bybit_2", "BTCUSDT", db_path=path) == pytest.approx(0.06)

    def test_excludes_closed_rows(self, tmp_path):
        path = str(tmp_path / "trade_journal.db")
        _init_trade_journal(path)
        _insert_trade(
            path, account_id="bybit_2", symbol="BTCUSDT",
            direction="long", position_size=0.03, status="closed",
        )
        assert current_net_position_qty("bybit_2", "BTCUSDT", db_path=path) == 0.0

    def test_excludes_backtest_rows(self, tmp_path):
        path = str(tmp_path / "trade_journal.db")
        _init_trade_journal(path)
        _insert_trade(
            path, account_id="bybit_2", symbol="BTCUSDT",
            direction="long", position_size=0.03, is_backtest=1,
        )
        assert current_net_position_qty("bybit_2", "BTCUSDT", db_path=path) == 0.0

    def test_other_account_does_not_leak(self, tmp_path):
        path = str(tmp_path / "trade_journal.db")
        _init_trade_journal(path)
        _insert_trade(
            path, account_id="bybit_1", symbol="BTCUSDT",
            direction="long", position_size=0.07,
        )
        assert current_net_position_qty("bybit_2", "BTCUSDT", db_path=path) == 0.0

    def test_other_symbol_does_not_leak(self, tmp_path):
        path = str(tmp_path / "trade_journal.db")
        _init_trade_journal(path)
        _insert_trade(
            path, account_id="bybit_2", symbol="ETHUSDT",
            direction="long", position_size=1.0,
        )
        assert current_net_position_qty("bybit_2", "BTCUSDT", db_path=path) == 0.0


# ---------------------------------------------------------------------------
# Package bridge helper
# ---------------------------------------------------------------------------


def _intent_pkg(
    direction: str = "long",
    aggregated_target_qty: float = 0.0,
    strategy: str = "turtle_soup",
) -> OrderPackage:
    # `strategy` is overridable so a test that needs a LIVE-executing strategy
    # (e.g. to assert a real flatten dispatches) isn't broken by turtle_soup's
    # 2026-07-07 demote to execution: shadow (#5850) — a shadow strategy folds
    # into effective_dry and never dispatches a live order/close.
    return OrderPackage(
        strategy=strategy,
        symbol="BTCUSDT",
        direction=direction,
        entry=50_000.0,
        sl=49_500.0 if direction == "long" else 50_500.0,
        tp=51_500.0 if direction == "long" else 48_500.0,
        meta={
            INTENT_MODE_META_KEY: INTENT_MODE_META_VALUE,
            "aggregated_target_qty": aggregated_target_qty,
            "contributing_strategies": [strategy, "vwap"],
        },
    )


class TestComputeExecutionDeltaForPackage:
    def test_target_zero_falls_back_to_risk_sized_qty(self):
        """Default sentinel: aggregator picks side, RiskManager picks qty."""
        pkg = _intent_pkg(direction="long", aggregated_target_qty=0.0)
        delta = compute_execution_delta_for_package(
            pkg, current_signed_qty=0.0, risk_sized_qty=0.02,
        )
        assert delta.action == "open"
        assert delta.side == "long"
        assert delta.qty_delta == 0.02

    def test_aggregated_target_caps_risk_qty(self):
        """If aggregator hints a smaller target, the smaller wins."""
        pkg = _intent_pkg(direction="long", aggregated_target_qty=0.01)
        delta = compute_execution_delta_for_package(
            pkg, current_signed_qty=0.0, risk_sized_qty=0.05,
        )
        assert delta.qty_delta == 0.01

    def test_risk_qty_caps_aggregated_target(self):
        """Conversely: aggregator's hint can't override the risk cap."""
        pkg = _intent_pkg(direction="long", aggregated_target_qty=0.10)
        delta = compute_execution_delta_for_package(
            pkg, current_signed_qty=0.0, risk_sized_qty=0.02,
        )
        assert delta.qty_delta == 0.02

    def test_current_at_target_noop(self):
        pkg = _intent_pkg(direction="long")
        delta = compute_execution_delta_for_package(
            pkg, current_signed_qty=0.02, risk_sized_qty=0.02,
        )
        assert delta.action == "noop"

    def test_current_below_target_increases_by_delta(self):
        pkg = _intent_pkg(direction="long")
        delta = compute_execution_delta_for_package(
            pkg, current_signed_qty=0.01, risk_sized_qty=0.03,
        )
        assert delta.action == "increase"
        assert delta.qty_delta == pytest.approx(0.02, abs=1e-9)

    def test_current_short_long_desired_flips_under_reverse_policy(self):
        """Explicit ``flip_policy="reverse"`` still flips. The post-2026-05-31
        default is ``hold`` (see TestFlipPolicy); the reverse-flip mechanics
        are still wired as the rollback path."""
        pkg = _intent_pkg(direction="long")
        delta = compute_execution_delta_for_package(
            pkg, current_signed_qty=-0.02, risk_sized_qty=0.03,
            flip_policy="reverse",
        )
        assert delta.action == "flip"
        assert delta.side == "long"


class TestFlipPolicy:
    """Conflict-resolution policy on an opposite net vote.

    Default since 2026-05-31 (PR #2451) is ``hold`` — keep the current
    position; the position-owner's monitor()/SL/TP exits. The walk-forward
    audit (docs/audits/walkforward-flip-policy-2026-05-30.md) verified
    PASS on both pre-agreed criteria (24 cells = 2 anchored folds × 2
    halves × 2 rosters × 3 policies). ``reverse`` (legacy close-and-reopen)
    is the rollback path; ``flat`` closes without re-opening (never the
    best policy in any walk-forward cell).
    """

    def test_resolver_default_is_hold(self, monkeypatch):
        monkeypatch.delenv("FLIP_POLICY", raising=False)
        assert resolve_flip_policy() == "hold"

    def test_resolver_reads_env(self, monkeypatch):
        monkeypatch.setenv("FLIP_POLICY", "reverse")
        assert resolve_flip_policy() == "reverse"

    def test_resolver_settings_overrides_env(self, monkeypatch):
        monkeypatch.setenv("FLIP_POLICY", "reverse")
        assert resolve_flip_policy({"FLIP_POLICY": "flat"}) == "flat"

    def test_resolver_unknown_falls_back_to_hold(self, monkeypatch):
        monkeypatch.setenv("FLIP_POLICY", "garbage")
        assert resolve_flip_policy() == "hold"

    def test_default_param_holds_on_opposite_vote(self, monkeypatch):
        """No explicit policy + no env → default ``hold`` → action='noop'.

        This is the post-walk-forward live behaviour. The previous default
        (``reverse`` → action='flip') is covered by the explicit
        ``flip_policy="reverse"`` test in TestComputeExecutionDeltaForPackage.
        """
        monkeypatch.delenv("FLIP_POLICY", raising=False)
        pkg = _intent_pkg(direction="long")
        delta = compute_execution_delta_for_package(
            pkg, current_signed_qty=-0.02, risk_sized_qty=0.03,
        )
        assert delta.action == "noop"
        assert "hold_policy" in delta.reason

    def test_hold_policy_suppresses_flip_as_noop(self):
        pkg = _intent_pkg(direction="long")
        delta = compute_execution_delta_for_package(
            pkg, current_signed_qty=-0.02, risk_sized_qty=0.03,
            flip_policy="hold",
        )
        assert delta.action == "noop"
        assert delta.qty_delta == 0.0
        assert "hold_policy" in delta.reason

    def test_reverse_policy_still_flips_when_explicitly_requested(self):
        """Operator rollback path: explicit ``reverse`` flips as before."""
        pkg = _intent_pkg(direction="long")
        delta = compute_execution_delta_for_package(
            pkg, current_signed_qty=-0.02, risk_sized_qty=0.03,
            flip_policy="reverse",
        )
        assert delta.action == "flip"
        assert delta.side == "long"

    def test_flat_policy_closes_without_reopen(self):
        pkg = _intent_pkg(direction="long")
        delta = compute_execution_delta_for_package(
            pkg, current_signed_qty=-0.02, risk_sized_qty=0.03,
            flip_policy="flat",
        )
        assert delta.action == "close"
        # Closing a short → buy-side reduce-only leg.
        assert delta.side == "long"
        assert delta.qty_delta == pytest.approx(0.02, abs=1e-9)
        assert delta.target_qty == 0.0

    def test_env_rollback_drives_default_call(self, monkeypatch):
        """With ``FLIP_POLICY=reverse`` on the VM (the rollback), the
        coordinator's call site (which passes no explicit flip_policy)
        resolves to reverse and the dispatcher emits a flip."""
        monkeypatch.setenv("FLIP_POLICY", "reverse")
        pkg = _intent_pkg(direction="long")
        delta = compute_execution_delta_for_package(
            pkg, current_signed_qty=-0.02, risk_sized_qty=0.03,
        )
        assert delta.action == "flip"

    def test_hold_does_not_affect_same_direction(self):
        """hold only governs the opposite-vote branch — same-side still tops up."""
        pkg = _intent_pkg(direction="long")
        delta = compute_execution_delta_for_package(
            pkg, current_signed_qty=0.01, risk_sized_qty=0.03,
            flip_policy="hold",
        )
        assert delta.action == "increase"


# ---------------------------------------------------------------------------
# Coordinator multi_account_execute — intent-mode branch
# ---------------------------------------------------------------------------


ACCOUNTS_YAML = textwrap.dedent("""\
    accounts:
      bybit_2:
        type: regular
        exchange: bybit
        api_key_env: BYBIT_API_KEY_2
        mode: live
        market_type: linear
        strategies: [turtle_soup, vwap]
        risk:
          max_dd_pct: 0.05
          daily_usd: 100
          pos_size: 500
          risk_pct: 0.01
          min_balance_usd: 50
          leverage: 3
""")


@pytest.fixture()
def accounts_yaml(tmp_path):
    p = tmp_path / "accounts.yaml"
    p.write_text(ACCOUNTS_YAML)
    return str(p)


@pytest.fixture()
def trade_db(tmp_path, monkeypatch):
    """Tmp trade_journal.db; both the position helper and the
    coordinator's ``_has_open_position`` honour the TRADE_JOURNAL_DB
    env override.
    """
    path = str(tmp_path / "trade_journal.db")
    _init_trade_journal(path)
    monkeypatch.setenv("TRADE_JOURNAL_DB", path)
    # Also stub credential resolution so the live-mode client gate
    # doesn't reject "not configured" before we get to the delta branch.
    monkeypatch.setenv("BYBIT_API_KEY_2", "test-key")
    monkeypatch.setenv("BYBIT_API_SECRET_2", "test-secret")
    return path


@pytest.fixture()
def coord(tmp_path):
    """Coordinator with a no-op units.yaml — multi_account_execute
    doesn't read units.yaml so an empty one is fine."""
    units_path = tmp_path / "units.yaml"
    units_path.write_text("units: {}\n")
    return Coordinator(units_path=str(units_path))


def _patched_execute_pkg(captured: list):
    """Stub for ``execute_pkg`` — records calls and returns a fake trade_id."""
    def _impl(pkg, account_cfg, *, exchange_client=None, balance_usdt=None,
             dry_run=None, qty_override=None, reduce_only=False):
        captured.append({
            "pkg": pkg,
            "account_cfg": account_cfg,
            "qty_override": qty_override,
            "dry_run": dry_run,
            "reduce_only": reduce_only,
            "direction": pkg.direction,
        })
        return f"test-trade-{len(captured)}"
    return _impl


def _patch_dispatch_deps(monkeypatch, captured):
    """Patch the imports multi_account_execute pulls in at runtime.

    ``execute_pkg`` is imported inside the loop body, so we patch it on
    the module it's resolved from. Same for the bybit client factory —
    return a non-None placeholder so the live-mode path doesn't refuse.
    """
    import src.units.accounts.execute as execute_mod
    monkeypatch.setattr(execute_mod, "execute_pkg", _patched_execute_pkg(captured))

    import src.units.accounts.clients as clients_mod
    monkeypatch.setattr(clients_mod, "bybit_client_for", lambda cfg: object())

    # ``log_rejection_to_journal`` is called on the noop/reduce paths;
    # short-circuit so the per-test tmp DB doesn't grow rejection rows.
    monkeypatch.setattr(execute_mod, "log_rejection_to_journal", lambda *a, **k: True)
    # ``_log_new_order_package`` writes to the journal too — stub.
    import src.core.coordinator as coord_mod
    monkeypatch.setattr(coord_mod, "_log_new_order_package", lambda pkg: None)
    monkeypatch.setattr(coord_mod, "_emit_execution_failure_ping", lambda **k: None)
    # NOTE: ``_has_open_position`` is intentionally NOT patched here. The
    # intent-mode branch never calls it, and tests that exercise the
    # legacy fall-through path want it live so the binary refusal fires.


class TestIntentModeDispatchBranch:
    def _balance_fetcher(self, account):
        return 10_000.0  # generous balance so risk gates don't refuse

    def test_intent_mode_open_uses_risk_sized_qty(
        self, coord, accounts_yaml, trade_db, monkeypatch,
    ):
        """Flat account + intent-mode long pkg → execute_pkg gets the risk-sized qty."""
        captured = []
        _patch_dispatch_deps(monkeypatch, captured)

        pkg = _intent_pkg(direction="long", aggregated_target_qty=0.0)
        results = coord.multi_account_execute(
            pkg, accounts_path=accounts_yaml,
            balance_fetcher=self._balance_fetcher,
        )
        assert len(results) == 1
        assert results[0]["error"] is None
        assert len(captured) == 1
        # Risk-sized qty for balance=10_000, risk_pct=0.01, distance=500 → 0.2.
        assert captured[0]["qty_override"] == pytest.approx(0.2, abs=1e-3)
        # Delta logged on pkg.meta for audit.
        assert pkg.meta["execution_delta"]["action"] == "open"

    def test_intent_mode_at_target_emits_noop_skip(
        self, coord, accounts_yaml, trade_db, monkeypatch,
    ):
        """Existing open position equal to risk-sized target → noop (no execute_pkg call)."""
        captured = []
        _patch_dispatch_deps(monkeypatch, captured)

        # Seed an open long position at the risk-sized qty so the
        # delta computer returns "noop".
        _insert_trade(
            trade_db, account_id="bybit_2", symbol="BTCUSDT",
            direction="long", position_size=0.2,
        )

        pkg = _intent_pkg(direction="long", aggregated_target_qty=0.0)
        results = coord.multi_account_execute(
            pkg, accounts_path=accounts_yaml,
            balance_fetcher=self._balance_fetcher,
        )

        assert len(results) == 1
        assert captured == [], "execute_pkg must not be called on noop"
        assert results[0]["trade_id"] is None
        assert "intent_noop" in results[0]["error"]
        assert pkg.meta["execution_delta"]["action"] == "noop"

    def test_intent_mode_reinforcement_only_sends_delta(
        self, coord, accounts_yaml, trade_db, monkeypatch,
    ):
        """Existing long below target → execute_pkg gets ONLY the delta, not full risk qty.

        This is the spec's key invariant: same-direction reinforcement
        must not double-count exposure.
        """
        captured = []
        _patch_dispatch_deps(monkeypatch, captured)

        # Risk-sized qty = 0.2 at balance=10k. Seed an existing 0.05 long.
        # Delta must be 0.15, not 0.2.
        _insert_trade(
            trade_db, account_id="bybit_2", symbol="BTCUSDT",
            direction="long", position_size=0.05,
        )

        pkg = _intent_pkg(direction="long")
        coord.multi_account_execute(
            pkg, accounts_path=accounts_yaml,
            balance_fetcher=self._balance_fetcher,
        )
        assert len(captured) == 1
        assert captured[0]["qty_override"] == pytest.approx(0.15, abs=1e-3)
        assert pkg.meta["execution_delta"]["action"] == "increase"

    def test_intent_mode_flip_dispatches_close_then_open(
        self, coord, accounts_yaml, trade_db, monkeypatch,
    ):
        """Existing short + intent-mode long pkg under the legacy/rollback
        ``FLIP_POLICY=reverse`` policy → two legs:
        (1) reduce-only close of the short, (2) regular open of the long.

        The live default since 2026-05-31 is ``hold`` (see TestFlipPolicy
        + the companion hold-default test below); the reverse-flip
        mechanics this test exercises stay wired as the rollback path.
        """
        monkeypatch.setenv("FLIP_POLICY", "reverse")
        captured = []
        _patch_dispatch_deps(monkeypatch, captured)

        # Seed an open short of 0.03; intent says long, risk sizes 0.2.
        _insert_trade(
            trade_db, account_id="bybit_2", symbol="BTCUSDT",
            direction="short", position_size=0.03,
        )

        pkg = _intent_pkg(direction="long")
        results = coord.multi_account_execute(
            pkg, accounts_path=accounts_yaml,
            balance_fetcher=self._balance_fetcher,
        )
        assert len(captured) == 2, "flip must dispatch close + open in sequence"
        # Leg 1: close the existing short — direction flipped to "long"
        # (the Buy side that reduces a short), reduce_only=True, qty=0.03.
        assert captured[0]["direction"] == "long"
        assert captured[0]["reduce_only"] is True
        assert captured[0]["qty_override"] == pytest.approx(0.03, abs=1e-6)
        # Leg 2: open the new long at the risk-sized qty (delta.qty_delta).
        assert captured[1]["direction"] == "long"
        assert captured[1]["reduce_only"] is False
        assert captured[1]["qty_override"] == pytest.approx(0.2, abs=1e-3)
        # The result's primary trade_id is the LAST leg (the open).
        assert results[0]["trade_id"] == "test-trade-2"
        assert results[0]["leg_trade_ids"] == ["test-trade-1", "test-trade-2"]
        assert results[0]["error"] is None
        # Audit record on pkg.meta carries the flip action.
        assert pkg.meta["execution_delta"]["action"] == "flip"

    def test_intent_mode_flip_holds_under_default_policy(
        self, coord, accounts_yaml, trade_db, monkeypatch,
    ):
        """Same seeded state as the flip test above, but under the
        post-2026-05-31 default ``FLIP_POLICY=hold``: the coordinator must
        NOT dispatch any leg, must record action=noop on pkg.meta with the
        hold_policy reason, and must leave the seeded short open for its
        own monitor()/SL/TP to close. This is the live behaviour the
        walk-forward verdict licenses (PR #2451)."""
        # No setenv — relies on the function-default "hold".
        monkeypatch.delenv("FLIP_POLICY", raising=False)
        captured = []
        _patch_dispatch_deps(monkeypatch, captured)

        _insert_trade(
            trade_db, account_id="bybit_2", symbol="BTCUSDT",
            direction="short", position_size=0.03,
        )

        pkg = _intent_pkg(direction="long")
        results = coord.multi_account_execute(
            pkg, accounts_path=accounts_yaml,
            balance_fetcher=self._balance_fetcher,
        )
        assert captured == [], (
            "hold (the default) must not dispatch close+open legs of a flip"
        )
        assert pkg.meta["execution_delta"]["action"] == "noop"
        assert "hold_policy" in pkg.meta["execution_delta"]["reason"]
        # The dispatcher emits a per-account result row carrying the
        # rejection reason so dashboards / reviews can see the held tick.
        assert len(results) == 1
        assert results[0]["sized_qty"] == 0.0
        assert "intent_noop:flip_suppressed_hold_policy" in (results[0]["error"] or "")

    def test_legacy_mode_still_uses_binary_open_guard(
        self, coord, accounts_yaml, trade_db, monkeypatch,
    ):
        """Non-intent-mode package + open position → legacy binary refusal.

        This pins the back-compat contract: the new branch only fires
        for packages produced by the intent multiplexer.
        """
        captured = []
        _patch_dispatch_deps(monkeypatch, captured)

        _insert_trade(
            trade_db, account_id="bybit_2", symbol="BTCUSDT",
            direction="long", position_size=0.01,
        )

        # Legacy (no INTENT_MODE marker): the binary block must fire.
        pkg = OrderPackage(
            strategy="turtle_soup",
            symbol="BTCUSDT",
            direction="long",
            entry=50_000.0,
            sl=49_500.0,
            tp=51_500.0,
            meta={},
        )
        results = coord.multi_account_execute(
            pkg, accounts_path=accounts_yaml,
            balance_fetcher=self._balance_fetcher,
        )
        assert captured == [], "legacy path must still refuse second order"
        # Exception message from the legacy block. Stable phrasing so
        # operator-facing logs / pings stay intact.
        assert "already has an open" in (results[0]["error"] or "")
        # The new audit key must NOT be present on a legacy package.
        assert "execution_delta" not in (pkg.meta or {})

    def test_intent_mode_min_qty_dust_is_noop(
        self, coord, accounts_yaml, trade_db, monkeypatch,
    ):
        """Position within one min-lot of target → noop (avoid dust orders)."""
        captured = []
        _patch_dispatch_deps(monkeypatch, captured)

        # Seed slightly below the risk target so the computed delta is
        # smaller than min_qty (default 0.001 BTC).
        _insert_trade(
            trade_db, account_id="bybit_2", symbol="BTCUSDT",
            direction="long", position_size=0.1999,
        )

        pkg = _intent_pkg(direction="long")
        results = coord.multi_account_execute(
            pkg, accounts_path=accounts_yaml,
            balance_fetcher=self._balance_fetcher,
        )
        assert captured == []
        assert "intent_sub_min_qty_delta" in (results[0]["error"] or "")


class TestIntentModeReduceClose:
    """S-MSE-2 — reduce-only / close / flip wiring for the dispatcher."""

    def _balance_fetcher(self, account):
        return 10_000.0

    def test_reduce_dispatches_reduce_only_opposite_side(
        self, coord, accounts_yaml, trade_db, monkeypatch,
    ):
        """Existing long above target → reduce-only sell for the delta."""
        captured = []
        _patch_dispatch_deps(monkeypatch, captured)

        # Risk-sized target = 0.2, existing = 0.5 → delta = 0.3 reduce.
        _insert_trade(
            trade_db, account_id="bybit_2", symbol="BTCUSDT",
            direction="long", position_size=0.5,
        )

        pkg = _intent_pkg(direction="long")
        results = coord.multi_account_execute(
            pkg, accounts_path=accounts_yaml,
            balance_fetcher=self._balance_fetcher,
        )
        assert len(captured) == 1
        assert captured[0]["direction"] == "short", (
            "reduce leg's order direction must be opposite of current"
        )
        assert captured[0]["reduce_only"] is True
        assert captured[0]["qty_override"] == pytest.approx(0.3, abs=1e-3)
        assert results[0]["error"] is None
        assert pkg.meta["execution_delta"]["action"] == "reduce"

    def test_intent_mode_spot_account_holds_reduce_to_bracket(
        self, tmp_path, monkeypatch,
    ):
        """Reduce/close/flip on a non-derivative (non-reduceOnly) account must
        HOLD — no reducing order is placed, the position rides its own exit
        (broker bracket for alpaca/ib; monitor-loop close for the dormant spot
        path) — and the result is a benign ``intent_noop:`` hold, NOT a refusal
        and NOT a silent buy (BL-20260622-ALPACA-REDUCE-HOLD, operator-approved
        hold-to-bracket; supersedes the old intent_reduce_requires_derivatives
        RiskBreach that spammed the all-accounts-failed alert)."""
        # Custom accounts.yaml with a spot account, otherwise identical
        # to the linear bybit_2 fixture.
        spot_yaml = textwrap.dedent("""\
            accounts:
              bybit_spot:
                type: regular
                exchange: bybit
                api_key_env: BYBIT_API_KEY_2
                mode: live
                market_type: spot
                strategies: [turtle_soup, vwap]
                risk:
                  max_dd_pct: 0.05
                  daily_usd: 100
                  pos_size: 500
                  risk_pct: 0.01
                  min_balance_usd: 50
        """)
        spot_path = tmp_path / "accounts.yaml"
        spot_path.write_text(spot_yaml)

        db_path = tmp_path / "trade_journal.db"
        _init_trade_journal(str(db_path))
        _insert_trade(
            str(db_path), account_id="bybit_spot", symbol="BTCUSDT",
            direction="long", position_size=0.5,
        )
        monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
        monkeypatch.setenv("BYBIT_API_KEY_2", "test-key")
        monkeypatch.setenv("BYBIT_API_SECRET_2", "test-secret")

        units_path = tmp_path / "units.yaml"
        units_path.write_text("units: {}\n")
        spot_coord = Coordinator(units_path=str(units_path))

        captured = []
        _patch_dispatch_deps(monkeypatch, captured)

        pkg = _intent_pkg(direction="long")
        results = spot_coord.multi_account_execute(
            pkg, accounts_path=str(spot_path),
            balance_fetcher=lambda acc: 10_000.0,
        )
        assert captured == [], "non-derivative reduce must place no order (hold)"
        # Benign hold, not a refusal — classified intent_noop so the
        # all-accounts-failed roll-up does not fire.
        _err = results[0]["error"] or ""
        assert _err.startswith("intent_noop:hold_to_bracket_"), _err
        assert "_non_derivative" in _err
        assert results[0]["trade_id"] is None
        # The benign-noop classifier must treat it as non-failure.
        from src.runtime.execution_diagnostics import enqueue_all_accounts_failed_dispatch  # noqa: F401
        assert _err.startswith("intent_noop:")

    def test_intent_mode_non_derivative_flip_flat_close_flattens(
        self, tmp_path, monkeypatch,
    ):
        """A GENUINE strategy exit on a non-derivative account — an opposing
        vote under FLIP_POLICY=flat (action=close, reason flip_flat_policy:…) —
        is EXECUTED via close_open_position (not held), distinguishing it from
        the sizing-0 artifact close. The close carve-out of hold-to-bracket
        (BL-20260622-ALPACA-REDUCE-HOLD)."""
        # trend_donchian (execution: live) — NOT turtle_soup, which was demoted
        # to shadow 2026-07-07 (#5850) and would fold into effective_dry, so no
        # live close would dispatch and the flatten assertion below would fail.
        spot_yaml = textwrap.dedent("""\
            accounts:
              bybit_spot:
                type: regular
                exchange: bybit
                api_key_env: BYBIT_API_KEY_2
                mode: live
                market_type: spot
                strategies: [trend_donchian, vwap]
                risk:
                  max_dd_pct: 0.05
                  daily_usd: 100
                  pos_size: 500
                  risk_pct: 0.01
                  min_balance_usd: 50
        """)
        spot_path = tmp_path / "accounts.yaml"
        spot_path.write_text(spot_yaml)

        db_path = tmp_path / "trade_journal.db"
        _init_trade_journal(str(db_path))
        # Held LONG; an opposing SHORT vote under flip_policy=flat → close.
        _insert_trade(
            str(db_path), account_id="bybit_spot", symbol="BTCUSDT",
            direction="long", position_size=0.5,
        )
        monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
        monkeypatch.setenv("BYBIT_API_KEY_2", "test-key")
        monkeypatch.setenv("BYBIT_API_SECRET_2", "test-secret")
        monkeypatch.setenv("FLIP_POLICY", "flat")

        units_path = tmp_path / "units.yaml"
        units_path.write_text("units: {}\n")
        spot_coord = Coordinator(units_path=str(units_path))

        captured = []
        _patch_dispatch_deps(monkeypatch, captured)
        # Capture the flatten call instead of hitting a real client.
        closes = []
        import src.units.accounts.execute as execute_mod

        def _fake_close(client, account_cfg, *, symbol, side, qty):
            closes.append({"symbol": symbol, "side": side, "qty": qty})
            return {"ok": True, "exchange_order_id": "close-1"}

        monkeypatch.setattr(execute_mod, "close_open_position", _fake_close)

        pkg = _intent_pkg(direction="short", strategy="trend_donchian")
        results = spot_coord.multi_account_execute(
            pkg, accounts_path=str(spot_path),
            balance_fetcher=lambda acc: 10_000.0,
        )
        # No NEW position opened (no execute_pkg placement) ...
        assert captured == [], "flip_flat close must place no new entry order"
        # ... the existing long was flattened via close_open_position ...
        assert len(closes) == 1, closes
        assert closes[0]["symbol"] == "BTCUSDT"
        assert closes[0]["side"] == "long"  # side of the original entry
        assert closes[0]["qty"] == pytest.approx(0.5, abs=1e-9)
        # ... and a successful flatten is classified benign (no all-failed ping).
        _err = results[0]["error"] or ""
        assert _err == "intent_noop:flip_flat_closed_via_flatten", _err
        assert pkg.meta["execution_delta"]["action"] == "close"

    def test_intent_mode_non_derivative_sizing_zero_never_flattens(
        self, tmp_path, monkeypatch,
    ):
        """A risk-sizing-0 (here: balance below the min-balance floor) must NEVER
        flatten the held position — the only close that flattens is a genuine
        flip_flat exit. A sizing-0 is refused at the zero-qty gate before the
        delta is even computed, so close_open_position is never called and the
        bracket-protected position is left untouched (safety guard for the close
        carve-out, BL-20260622-ALPACA-REDUCE-HOLD)."""
        spot_yaml = textwrap.dedent("""\
            accounts:
              bybit_spot:
                type: regular
                exchange: bybit
                api_key_env: BYBIT_API_KEY_2
                mode: live
                market_type: spot
                strategies: [turtle_soup, vwap]
                risk:
                  max_dd_pct: 0.05
                  daily_usd: 100
                  pos_size: 500
                  risk_pct: 0.01
                  min_balance_usd: 50
        """)
        spot_path = tmp_path / "accounts.yaml"
        spot_path.write_text(spot_yaml)
        db_path = tmp_path / "trade_journal.db"
        _init_trade_journal(str(db_path))
        _insert_trade(
            str(db_path), account_id="bybit_spot", symbol="BTCUSDT",
            direction="long", position_size=0.5,
        )
        monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
        monkeypatch.setenv("BYBIT_API_KEY_2", "test-key")
        monkeypatch.setenv("BYBIT_API_SECRET_2", "test-secret")
        monkeypatch.delenv("FLIP_POLICY", raising=False)

        units_path = tmp_path / "units.yaml"
        units_path.write_text("units: {}\n")
        spot_coord = Coordinator(units_path=str(units_path))
        captured = []
        _patch_dispatch_deps(monkeypatch, captured)
        closes = []
        import src.units.accounts.execute as execute_mod
        monkeypatch.setattr(
            execute_mod, "close_open_position",
            lambda *a, **k: closes.append(k) or {"ok": True},
        )
        # Same-direction long vote whose risk-sizing yields 0 → target 0 →
        # action=close with reason close_existing_… (NOT flip_flat_policy).
        pkg = _intent_pkg(direction="long")
        results = spot_coord.multi_account_execute(
            pkg, accounts_path=str(spot_path),
            balance_fetcher=lambda acc: 0.0,  # forces risk-sizing toward 0
        )
        # Must NOT flatten on a sizing-0 close — hold-to-bracket.
        assert closes == [], "sizing-0 close must hold, never flatten"
        _err = results[0]["error"] or ""
        assert "flip_flat_closed" not in _err


class TestBuildIntentLegs:
    """Unit tests for the pure ``_build_intent_legs`` helper."""

    def _delta(
        self, action, side, qty_delta=0.02, target_qty=0.02, current_qty=0.0,
    ):
        from src.runtime.intents import ExecutionDelta
        return ExecutionDelta(
            action=action,
            side=side,
            qty_delta=qty_delta,
            target_qty=target_qty,
            current_qty=current_qty,
            reason="test",
        )

    def test_open_returns_single_primary_leg(self):
        from src.core.coordinator import _build_intent_legs
        pkg = _intent_pkg(direction="long")
        legs = _build_intent_legs(
            pkg, self._delta("open", "long", 0.02, 0.02, 0.0),
        )
        assert len(legs) == 1
        assert legs[0]["pkg"] is pkg
        assert legs[0]["qty"] == 0.02
        assert legs[0]["reduce_only"] is False
        assert legs[0]["label"] == "primary"

    def test_reduce_flips_direction_and_sets_reduce_only(self):
        from src.core.coordinator import _build_intent_legs
        pkg = _intent_pkg(direction="long")
        legs = _build_intent_legs(
            pkg, self._delta("reduce", "short", 0.02, 0.03, 0.05),
        )
        assert len(legs) == 1
        assert legs[0]["pkg"].direction == "short"
        assert legs[0]["pkg"] is not pkg, "must be a fresh copy"
        assert legs[0]["reduce_only"] is True

    def test_close_flips_direction_and_sets_reduce_only(self):
        from src.core.coordinator import _build_intent_legs
        pkg = _intent_pkg(direction="long")
        legs = _build_intent_legs(
            pkg, self._delta("close", "short", 0.04, 0.0, 0.04),
        )
        assert len(legs) == 1
        assert legs[0]["pkg"].direction == "short"
        assert legs[0]["reduce_only"] is True

    def test_flip_returns_close_then_open(self):
        from src.core.coordinator import _build_intent_legs
        pkg = _intent_pkg(direction="long")
        legs = _build_intent_legs(
            pkg, self._delta("flip", "long", 0.02, 0.02, -0.03),
        )
        assert len(legs) == 2
        # Close leg: long (Buy) reduces an existing short.
        assert legs[0]["pkg"].direction == "long"
        assert legs[0]["qty"] == pytest.approx(0.03, abs=1e-9)
        assert legs[0]["reduce_only"] is True
        assert legs[0]["label"] == "flip_close"
        # Open leg: new direction = delta.side.
        assert legs[1]["pkg"].direction == "long"
        assert legs[1]["qty"] == 0.02
        assert legs[1]["reduce_only"] is False
        assert legs[1]["label"] == "flip_open"

    def test_unsupported_action_raises(self):
        from src.core.coordinator import _build_intent_legs
        pkg = _intent_pkg(direction="long")
        with pytest.raises(ValueError, match="unsupported delta.action"):
            _build_intent_legs(
                pkg, self._delta("noop", None, 0.0, 0.0, 0.0),
            )


class TestNettingGuardMonocle:
    """Position-netting guard — monocle half (Option A, BL-20260608-DEMOPNL).

    **BASELINE (2026-06-17): the guard is unconditional.** A same-direction
    ADD (delta action ``open``/``increase``) for a ``(strategy, account,
    symbol)`` that already holds an open trade is ALWAYS suppressed — no
    pyramiding, restoring per-trade=per-position — for every account,
    regardless of env. The default-off ``POSITION_NETTING_GUARD_ENABLED``
    gate and the ``POSITION_NETTING_GUARD_ACCOUNTS`` scope were removed (a
    required correctness capability must not sit behind a default-off flag);
    any leftover env value is ignored. Reduce/close/flip
    (position-management) deltas are never blocked, and cross-strategy adds
    are not blocked (the multiplexer aggregates those).
    """

    def _balance_fetcher(self, account):
        return 10_000.0

    def test_guard_on_suppresses_same_strategy_increase(
        self, coord, accounts_yaml, trade_db, monkeypatch,
    ):
        monkeypatch.setenv("POSITION_NETTING_GUARD_ENABLED", "true")
        captured = []
        _patch_dispatch_deps(monkeypatch, captured)

        # Existing 0.05 long for turtle_soup; risk target 0.2 → would
        # otherwise increase by 0.15. Guard must suppress the add.
        _insert_trade(
            trade_db, account_id="bybit_2", symbol="BTCUSDT",
            direction="long", position_size=0.05, strategy_name="turtle_soup",
        )

        pkg = _intent_pkg(direction="long")
        results = coord.multi_account_execute(
            pkg, accounts_path=accounts_yaml,
            balance_fetcher=self._balance_fetcher,
        )
        assert captured == [], "guard must suppress the netted add"
        assert results[0]["trade_id"] is None
        assert results[0]["sized_qty"] == 0.0
        assert "reentry_suppressed_netting_guard:increase" in (
            results[0]["error"] or ""
        )
        # The delta is still computed + logged for audit.
        assert pkg.meta["execution_delta"]["action"] == "increase"

    def test_guard_on_allows_first_entry_when_flat(
        self, coord, accounts_yaml, trade_db, monkeypatch,
    ):
        """Flat account → action 'open' with no existing strategy trade is
        NOT a re-entry, so the guard lets the first entry through."""
        monkeypatch.setenv("POSITION_NETTING_GUARD_ENABLED", "true")
        captured = []
        _patch_dispatch_deps(monkeypatch, captured)

        pkg = _intent_pkg(direction="long", aggregated_target_qty=0.0)
        results = coord.multi_account_execute(
            pkg, accounts_path=accounts_yaml,
            balance_fetcher=self._balance_fetcher,
        )
        assert len(captured) == 1, "first entry must dispatch"
        assert results[0]["error"] is None
        assert pkg.meta["execution_delta"]["action"] == "open"

    def test_no_env_still_suppresses_increase(
        self, coord, accounts_yaml, trade_db, monkeypatch,
    ):
        """No env (gate removed) → the same-direction add is STILL suppressed
        (the guard is baseline / unconditional)."""
        monkeypatch.delenv("POSITION_NETTING_GUARD_ENABLED", raising=False)
        captured = []
        _patch_dispatch_deps(monkeypatch, captured)

        _insert_trade(
            trade_db, account_id="bybit_2", symbol="BTCUSDT",
            direction="long", position_size=0.05, strategy_name="turtle_soup",
        )

        pkg = _intent_pkg(direction="long")
        results = coord.multi_account_execute(
            pkg, accounts_path=accounts_yaml,
            balance_fetcher=self._balance_fetcher,
        )
        assert captured == [], "guard is unconditional → add suppressed"
        assert results[0]["trade_id"] is None
        assert results[0]["sized_qty"] == 0.0
        assert "reentry_suppressed_netting_guard:increase" in (
            results[0]["error"] or ""
        )

    def test_guard_on_does_not_block_reduce(
        self, coord, accounts_yaml, trade_db, monkeypatch,
    ):
        """Reduce (position-management) is never blocked by the guard."""
        monkeypatch.setenv("POSITION_NETTING_GUARD_ENABLED", "true")
        captured = []
        _patch_dispatch_deps(monkeypatch, captured)

        # Existing 0.5 long > target 0.2 → reduce by 0.3.
        _insert_trade(
            trade_db, account_id="bybit_2", symbol="BTCUSDT",
            direction="long", position_size=0.5, strategy_name="turtle_soup",
        )

        pkg = _intent_pkg(direction="long")
        results = coord.multi_account_execute(
            pkg, accounts_path=accounts_yaml,
            balance_fetcher=self._balance_fetcher,
        )
        assert len(captured) == 1, "reduce must still dispatch"
        assert captured[0]["reduce_only"] is True
        assert pkg.meta["execution_delta"]["action"] == "reduce"
        assert results[0]["error"] is None

    def test_guard_on_does_not_block_other_strategy_increase(
        self, coord, accounts_yaml, trade_db, monkeypatch,
    ):
        """The gate is (strategy, account, symbol)-scoped: an open trade
        owned by a DIFFERENT strategy does not suppress this strategy's
        add (cross-strategy aggregation is the multiplexer's job)."""
        monkeypatch.setenv("POSITION_NETTING_GUARD_ENABLED", "true")
        captured = []
        _patch_dispatch_deps(monkeypatch, captured)

        # Open trade belongs to vwap; pkg is turtle_soup.
        _insert_trade(
            trade_db, account_id="bybit_2", symbol="BTCUSDT",
            direction="long", position_size=0.05, strategy_name="vwap",
        )

        pkg = _intent_pkg(direction="long")  # strategy="turtle_soup"
        coord.multi_account_execute(
            pkg, accounts_path=accounts_yaml,
            balance_fetcher=self._balance_fetcher,
        )
        assert len(captured) == 1, "different-strategy open trade must not gate"
        assert pkg.meta["execution_delta"]["action"] == "increase"

    def test_legacy_scope_env_excluding_account_is_ignored(
        self, coord, accounts_yaml, trade_db, monkeypatch,
    ):
        """A leftover ``POSITION_NETTING_GUARD_ACCOUNTS=bybit_1`` no longer
        scopes the guard OUT for bybit_2 — there is no scoping now, so the
        bybit_2 add is STILL suppressed (the scope env is a no-op)."""
        monkeypatch.setenv("POSITION_NETTING_GUARD_ENABLED", "true")
        monkeypatch.setenv("POSITION_NETTING_GUARD_ACCOUNTS", "bybit_1")
        captured = []
        _patch_dispatch_deps(monkeypatch, captured)

        _insert_trade(
            trade_db, account_id="bybit_2", symbol="BTCUSDT",
            direction="long", position_size=0.05, strategy_name="turtle_soup",
        )

        pkg = _intent_pkg(direction="long")
        results = coord.multi_account_execute(
            pkg, accounts_path=accounts_yaml,
            balance_fetcher=self._balance_fetcher,
        )
        assert captured == [], (
            "scope env is ignored → guard applies to bybit_2 unconditionally"
        )
        assert "reentry_suppressed_netting_guard" in (results[0]["error"] or "")

    def test_guard_suppresses_add_for_any_account(
        self, coord, accounts_yaml, trade_db, monkeypatch,
    ):
        """The guard applies to every account unconditionally → a bybit_2
        same-direction add is suppressed."""
        captured = []
        _patch_dispatch_deps(monkeypatch, captured)

        _insert_trade(
            trade_db, account_id="bybit_2", symbol="BTCUSDT",
            direction="long", position_size=0.05, strategy_name="turtle_soup",
        )

        pkg = _intent_pkg(direction="long")
        results = coord.multi_account_execute(
            pkg, accounts_path=accounts_yaml,
            balance_fetcher=self._balance_fetcher,
        )
        assert captured == [], "guard must suppress the bybit_2 add"
        assert "reentry_suppressed_netting_guard" in (results[0]["error"] or "")


ETH_ACCOUNTS_YAML = textwrap.dedent("""\
    accounts:
      bybit_2:
        type: regular
        exchange: bybit
        api_key_env: BYBIT_API_KEY_2
        mode: live
        market_type: linear
        account_class: real_money
        strategies: [eth_pullback_2h, vwap]
        risk:
          max_dd_pct: 0.05
          daily_usd: 100
          pos_size: 500
          risk_pct: 0.01
          min_balance_usd: 50
          leverage: 3
""")


@pytest.fixture()
def eth_accounts_yaml(tmp_path):
    p = tmp_path / "accounts_eth.yaml"
    p.write_text(ETH_ACCOUNTS_YAML)
    return str(p)


def _eth_intent_pkg(direction: str = "long") -> OrderPackage:
    return OrderPackage(
        strategy="eth_pullback_2h",
        symbol="ETHUSDT",
        direction=direction,
        entry=1700.0,
        sl=1660.0 if direction == "long" else 1740.0,
        tp=1780.0 if direction == "long" else 1620.0,
        meta={
            INTENT_MODE_META_KEY: INTENT_MODE_META_VALUE,
            "aggregated_target_qty": 0.0,
            "contributing_strategies": ["eth_pullback_2h"],
        },
    )


class TestIntentModeVenueMinDelta:
    """A netting DELTA below the EXCHANGE lot minimum is a clean noop, not a
    dispatched sub-lot order that trips the ``_submit_order`` pre-flight
    ("below the exchange lot minimum after step-alignment" →
    ``bybit_place_order_failed`` error ping).

    Reproduces the recurring ETHUSDT/bybit_2 ping: the strategy sizes 0.11
    ETH (clears the sized-qty venue-min guard), but the account already
    holds ~0.101, so the intent layer computes a top-up delta of ~0.009 —
    above the account-default ``min_qty`` (0.001) yet below ETHUSDT's real
    Bybit lot (0.01). Pre-fix that 0.009 delta slipped past the
    account-min-only guard, was dispatched, and rejected at pre-flight on
    every top-up/trim signal. The fix folds the venue minimum
    (``venue_min_qty_for``) into the sub-min delta guard so it is treated as
    dust (noop), matching the sized-qty guard (BL-20260619-ETHMIN) and the
    risk.py gap (BL-20260628-CRYPTO-INSTRUMENT-MIN-FLOOR).
    """

    def _balance_fetcher(self, account):
        return 10_000.0

    @pytest.fixture(autouse=True)
    def _clear_lot_cache(self):
        from src.units.accounts import precision
        precision._LOT_CACHE.clear()
        precision._LIVE_CACHE.clear()
        yield
        precision._LOT_CACHE.clear()
        precision._LIVE_CACHE.clear()

    def test_sub_venue_min_eth_delta_is_noop_not_dispatched(
        self, coord, eth_accounts_yaml, trade_db, monkeypatch,
    ):
        captured = []
        _patch_dispatch_deps(monkeypatch, captured)
        # Pin the risk-sized target at 0.11 ETH regardless of sizing math.
        monkeypatch.setattr(
            "src.units.accounts.risk.RiskManager.position_size",
            lambda self, *a, **k: 0.11,
        )
        # Seed an existing 0.101 ETH long owned by a DIFFERENT strategy: the
        # (strategy, account, symbol) netting guard must NOT fire first, but
        # the net-position read still sees 0.101 → increase delta ~0.009.
        _insert_trade(
            trade_db, account_id="bybit_2", symbol="ETHUSDT",
            direction="long", position_size=0.101, strategy_name="vwap",
        )

        pkg = _eth_intent_pkg(direction="long")
        results = coord.multi_account_execute(
            pkg, accounts_path=eth_accounts_yaml,
            balance_fetcher=self._balance_fetcher,
        )
        # The 0.009 delta (< ETHUSDT 0.01 venue lot) must NOT be dispatched
        # to the pre-flight; it is a clean noop.
        assert captured == [], (
            "sub-venue-min delta must be a noop, never dispatched to pre-flight"
        )
        assert "intent_sub_min_qty_delta" in (results[0]["error"] or "")
        assert pkg.meta["execution_delta"]["action"] == "increase"
        assert pkg.meta["execution_delta"]["qty_delta"] == pytest.approx(
            0.009, abs=1e-6,
        )

    def test_btc_sub_account_min_delta_still_noop_regression(
        self, coord, accounts_yaml, trade_db, monkeypatch,
    ):
        """Fallback-path guard: on BTCUSDT (venue lot == account min 0.001)
        the behaviour is unchanged — a delta below 0.001 is still a noop, and
        the folded venue minimum equals the account min (no regression)."""
        captured = []
        _patch_dispatch_deps(monkeypatch, captured)
        _insert_trade(
            trade_db, account_id="bybit_2", symbol="BTCUSDT",
            direction="long", position_size=0.1999, strategy_name="vwap",
        )
        pkg = _intent_pkg(direction="long")
        results = coord.multi_account_execute(
            pkg, accounts_path=accounts_yaml,
            balance_fetcher=self._balance_fetcher,
        )
        assert captured == []
        assert "intent_sub_min_qty_delta" in (results[0]["error"] or "")


class TestPackageIsIntentModeHelper:
    def test_true_when_marker_set(self):
        pkg = _intent_pkg()
        assert package_is_intent_mode(pkg) is True

    def test_false_when_marker_absent(self):
        pkg = OrderPackage(
            strategy="turtle_soup", symbol="BTCUSDT", direction="long",
            entry=50_000.0, sl=49_500.0, tp=51_500.0,
        )
        assert package_is_intent_mode(pkg) is False

    def test_false_when_marker_wrong_value(self):
        pkg = OrderPackage(
            strategy="turtle_soup", symbol="BTCUSDT", direction="long",
            entry=50_000.0, sl=49_500.0, tp=51_500.0,
            meta={INTENT_MODE_META_KEY: "something_else"},
        )
        assert package_is_intent_mode(pkg) is False
