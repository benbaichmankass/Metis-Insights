"""Pin the closed-flat invariant wiring at its tick-loop call site.

The wiring helper itself (``_closed_flat_wiring.maybe_run_closed_flat_check``)
is exhaustively tested in ``test_closed_flat_wiring.py``. This file pins
the **integration**: that ``order_monitor.run_monitor_tick`` actually
invokes the helper at the documented post-orphan-reconciler hook.

**BASELINE (2026-06-17): the invariant check is UNCONDITIONAL.** The
default-OFF ``CLOSED_FLAT_INVARIANT_ENABLED`` gate was removed, so the
check runs every tick regardless of env (a leftover env value is ignored).

Two contracts:

1. No env (or a leftover ``CLOSED_FLAT_INVARIANT_ENABLED=false``) →
   ``closed_flat_invariant.check`` is STILL called (the env is a no-op).
2. With a callable resolver wired through, ``check`` is called exactly
   once per tick with that resolver.

If a future refactor accidentally drops the call site or moves it before
the orphan reconcilers, these tests fail loudly.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from src.runtime import order_monitor as om


def test_call_site_invokes_check_with_no_env(tmp_path, monkeypatch):
    """No env → run_monitor_tick STILL calls check() (baseline, env is a
    no-op)."""
    monkeypatch.delenv("CLOSED_FLAT_INVARIANT_ENABLED", raising=False)
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    monkeypatch.setattr(
        "src.runtime.order_monitor._load_account_cfgs_for_reconcile",
        lambda: {"bybit_2": {"account_id": "bybit_2"}},
    )

    fake_check = MagicMock(return_value=[])
    monkeypatch.setattr(
        "src.runtime.closed_flat_invariant.check", fake_check,
    )

    om.run_monitor_tick(strategies=["vwap"])

    assert fake_check.call_count == 1, (
        "closed_flat_invariant.check must be invoked once per tick, "
        "unconditionally (the gate was removed)"
    )


def test_call_site_invokes_check_ignoring_legacy_false_env(tmp_path, monkeypatch):
    """A leftover ``CLOSED_FLAT_INVARIANT_ENABLED=false`` is ignored →
    run_monitor_tick STILL calls check() exactly once with a callable
    resolver."""
    monkeypatch.setenv("CLOSED_FLAT_INVARIANT_ENABLED", "false")
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))
    monkeypatch.setattr(
        "src.runtime.order_monitor._load_account_cfgs_for_reconcile",
        lambda: {"bybit_2": {"account_id": "bybit_2"}},
    )

    fake_check = MagicMock(return_value=[])
    monkeypatch.setattr(
        "src.runtime.closed_flat_invariant.check", fake_check,
    )

    om.run_monitor_tick(strategies=["vwap"])

    assert fake_check.call_count == 1, (
        "closed_flat_invariant.check must be invoked once per tick "
        "regardless of the legacy env value"
    )
    _, kwargs = fake_check.call_args
    resolver = kwargs.get("account_resolver")
    assert callable(resolver), "wiring must pass a callable resolver"
    assert resolver("bybit_2") == {"account_id": "bybit_2"}
    assert resolver("unknown") is None
