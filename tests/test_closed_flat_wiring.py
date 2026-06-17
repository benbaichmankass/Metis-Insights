"""Regression for ``src/runtime/_closed_flat_wiring.py`` (S-067 fu A).

**BASELINE (2026-06-17): the invariant check is UNCONDITIONAL.** The
default-OFF ``CLOSED_FLAT_INVARIANT_ENABLED`` gate was removed — a safety
invariant must not sit behind a default-off flag (Prime-Directive
anti-pattern). The check now runs every tick regardless of env; a leftover
env value is ignored. The check stays **alert-only** (logs/Telegrams a
violation, never mutates a position).

Contracts under test:

1. **No env / explicit false** — the env is now a no-op: the check still
   RUNS. With no violations it returns ``[]`` so the helper returns ``None``
   and does not mutate ``summaries`` (``None`` means "ran, found nothing",
   NOT "gated off").
2. **No violations** — check IS called, returns ``[]``, helper returns
   ``None`` and does not mutate ``summaries``.
3. **Violations present** — check returns a list of ``InvariantViolation``;
   helper returns the summary entry ``{"violations": N, "phase":
   "alert_only"}`` and writes it into ``summaries["__closed_flat_invariant__"]``.

Plus the never-raise contract (4) and the resolver-shape contract (5):

4. ``check()`` raising → helper catches + logs + returns ``None``.
5. The resolver passed to ``check`` is a callable that returns the
   account cfg dict for a known id and ``None`` for an unknown id.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from src.runtime import _closed_flat_wiring as wiring


# ---------------------------------------------------------------------------
# Contract 1 — env is a no-op: check runs unconditionally (BASELINE)
# ---------------------------------------------------------------------------


def test_check_runs_with_no_env(monkeypatch):
    """No ``CLOSED_FLAT_INVARIANT_ENABLED`` env → check still RUNS (baseline).

    With no violations it returns ``[]`` so the helper returns ``None`` and
    does not mutate ``summaries`` — ``None`` means "ran, found nothing", NOT
    "gated off"."""
    monkeypatch.delenv("CLOSED_FLAT_INVARIANT_ENABLED", raising=False)
    monkeypatch.setattr(
        "src.runtime.order_monitor._load_account_cfgs_for_reconcile",
        lambda: {"bybit_2": {"account_id": "bybit_2"}},
    )
    fake_check = MagicMock(return_value=[])
    monkeypatch.setattr(
        "src.runtime.closed_flat_invariant.check", fake_check,
    )
    summaries: dict = {}
    result = wiring.maybe_run_closed_flat_check(db=object(), summaries=summaries)
    assert result is None, "no violations → None (ran, found nothing)"
    assert "__closed_flat_invariant__" not in summaries
    fake_check.assert_called_once()


def test_check_runs_with_legacy_false_env(monkeypatch):
    """Leftover ``CLOSED_FLAT_INVARIANT_ENABLED=false`` is ignored → check
    still RUNS (the gate was removed; the env value is a no-op)."""
    monkeypatch.setenv("CLOSED_FLAT_INVARIANT_ENABLED", "false")
    monkeypatch.setattr(
        "src.runtime.order_monitor._load_account_cfgs_for_reconcile",
        lambda: {"bybit_2": {"account_id": "bybit_2"}},
    )
    fake_check = MagicMock(return_value=[])
    monkeypatch.setattr(
        "src.runtime.closed_flat_invariant.check", fake_check,
    )
    assert wiring.maybe_run_closed_flat_check(db=object()) is None
    fake_check.assert_called_once()


# ---------------------------------------------------------------------------
# Contract 2 — no violations
# ---------------------------------------------------------------------------


def test_check_runs_no_violations(monkeypatch):
    """check returns [] → helper returns None, summaries untouched."""
    monkeypatch.setattr(
        "src.runtime.order_monitor._load_account_cfgs_for_reconcile",
        lambda: {"bybit_2": {"account_id": "bybit_2"}},
    )
    fake_check = MagicMock(return_value=[])
    monkeypatch.setattr(
        "src.runtime.closed_flat_invariant.check", fake_check,
    )
    summaries: dict = {}
    result = wiring.maybe_run_closed_flat_check(db=object(), summaries=summaries)
    assert result is None
    assert "__closed_flat_invariant__" not in summaries
    fake_check.assert_called_once()


# ---------------------------------------------------------------------------
# Contract 3 — violations present
# ---------------------------------------------------------------------------


def test_violations_recorded_in_summaries(monkeypatch):
    """Violations → helper writes summary entry + returns it."""
    monkeypatch.setattr(
        "src.runtime.order_monitor._load_account_cfgs_for_reconcile",
        lambda: {"bybit_2": {"account_id": "bybit_2"}},
    )
    fake_violations = [object(), object(), object()]  # 3 violations
    monkeypatch.setattr(
        "src.runtime.closed_flat_invariant.check",
        lambda db, account_resolver=None, **kw: fake_violations,
    )
    summaries: dict = {}
    result = wiring.maybe_run_closed_flat_check(db=object(), summaries=summaries)
    assert result == {"violations": 3, "phase": "alert_only"}
    assert summaries["__closed_flat_invariant__"] == {
        "violations": 3, "phase": "alert_only",
    }


# ---------------------------------------------------------------------------
# Contract 4 — never-raise
# ---------------------------------------------------------------------------


def test_check_raising_is_swallowed(monkeypatch):
    """If ``check()`` raises, helper catches + returns None (never-raise)."""
    monkeypatch.setattr(
        "src.runtime.order_monitor._load_account_cfgs_for_reconcile",
        lambda: {},
    )

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated check failure")

    monkeypatch.setattr(
        "src.runtime.closed_flat_invariant.check", _boom,
    )
    # Must NOT raise.
    assert wiring.maybe_run_closed_flat_check(db=object()) is None


def test_cfg_loader_raising_is_swallowed(monkeypatch):
    """If the cfg loader raises, helper catches + returns None."""

    def _boom():
        raise RuntimeError("cfg load failure")

    monkeypatch.setattr(
        "src.runtime.order_monitor._load_account_cfgs_for_reconcile",
        _boom,
    )
    fake_check = MagicMock()
    monkeypatch.setattr(
        "src.runtime.closed_flat_invariant.check", fake_check,
    )
    assert wiring.maybe_run_closed_flat_check(db=object()) is None
    fake_check.assert_not_called()


# ---------------------------------------------------------------------------
# Contract 5 — resolver shape
# ---------------------------------------------------------------------------


def test_resolver_returns_cfg_for_known_id(monkeypatch):
    """The resolver passed to ``check`` returns the cfg dict for known ids
    and ``None`` for unknown ones."""
    cfg_map = {
        "bybit_1": {"account_id": "bybit_1", "exchange": "bybit"},
        "bybit_2": {"account_id": "bybit_2", "exchange": "bybit"},
    }
    monkeypatch.setattr(
        "src.runtime.order_monitor._load_account_cfgs_for_reconcile",
        lambda: cfg_map,
    )
    captured_resolver = []

    def _capture(db, account_resolver=None, **kw):
        captured_resolver.append(account_resolver)
        return []

    monkeypatch.setattr(
        "src.runtime.closed_flat_invariant.check", _capture,
    )
    wiring.maybe_run_closed_flat_check(db=object())
    assert len(captured_resolver) == 1
    resolver = captured_resolver[0]
    assert callable(resolver)
    assert resolver("bybit_1") == cfg_map["bybit_1"]
    assert resolver("bybit_2") == cfg_map["bybit_2"]
    assert resolver("nonexistent") is None
