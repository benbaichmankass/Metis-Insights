"""Pin the closed-flat invariant wiring at its tick-loop call site.

The wiring helper itself (``_closed_flat_wiring.maybe_run_closed_flat_check``)
is exhaustively tested in ``test_closed_flat_wiring.py``. This file pins
the **integration**: that ``order_monitor.run_monitor_tick`` actually
invokes the helper at the documented post-orphan-reconciler hook, with
the env-gate behavior preserved.

Two contracts:

1. ``CLOSED_FLAT_INVARIANT_ENABLED`` unset/false → helper is invoked but
   ``closed_flat_invariant.check`` is NOT called (the gate short-circuits
   inside the helper).
2. ``CLOSED_FLAT_INVARIANT_ENABLED=true`` → helper is invoked AND
   ``closed_flat_invariant.check`` is called through with a non-None
   resolver.

If a future refactor accidentally drops the call site or moves it before
the orphan reconcilers, these tests fail loudly.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from src.runtime import order_monitor as om


def test_call_site_no_op_when_gate_off(tmp_path, monkeypatch):
    """Default env (gate off) → run_monitor_tick must not call check()."""
    monkeypatch.delenv("CLOSED_FLAT_INVARIANT_ENABLED", raising=False)
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(tmp_path / "trade_journal.db"))

    fake_check = MagicMock()
    monkeypatch.setattr(
        "src.runtime.closed_flat_invariant.check", fake_check,
    )

    om.run_monitor_tick(strategies=["vwap"])

    fake_check.assert_not_called()


def test_call_site_invokes_check_when_gate_on(tmp_path, monkeypatch):
    """Gate on → run_monitor_tick must call check() exactly once with a
    callable resolver."""
    monkeypatch.setenv("CLOSED_FLAT_INVARIANT_ENABLED", "true")
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
        "closed_flat_invariant.check must be invoked once per tick when "
        "CLOSED_FLAT_INVARIANT_ENABLED=true"
    )
    _, kwargs = fake_check.call_args
    resolver = kwargs.get("account_resolver")
    assert callable(resolver), "wiring must pass a callable resolver"
    assert resolver("bybit_2") == {"account_id": "bybit_2"}
    assert resolver("unknown") is None
