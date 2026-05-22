"""Tests for the strategy_monocle refusal-cooldown gate added in PR 2.

Pre-fix: the dispatcher only blocked re-dispatch when an open package
existed for the strategy. A `sized_qty=0` refusal logged a
`status='rejected'` package row but did NOT block the next tick from
re-firing the same signal — producing 20 rejected rows in 1h on
2026-05-10 (FU-20260510-002).

After: a recent rejected package (within `STRATEGY_REFUSAL_COOLDOWN_SECONDS`,
default 300s) blocks dispatch the same way an open package does, so a
single transient refusal doesn't cascade.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


class _StubDB:
    """Minimal stand-in for src.units.db.database.Database that returns
    a hard-coded order_packages list. Lets the cooldown helper exercise
    its time-window logic without needing a real SQLite file."""

    def __init__(self, packages: List[Dict[str, Any]]):
        self._packages = packages

    def get_order_packages_by_strategy(
        self, strategy_name: str, *, status: Optional[str] = None,
        limit: Optional[int] = None, linked_only: bool = False,
        symbol: Optional[str] = None,
    ):
        out = [
            p for p in self._packages
            if p.get("strategy_name") == strategy_name
            and (status is None or p.get("status") == status)
            and (symbol is None or p.get("symbol") == symbol)
        ]
        # Newest-first by updated_at, mirroring the real method.
        out.sort(key=lambda p: p.get("updated_at", ""), reverse=True)
        return out[:limit] if limit else out


def _patch_db(monkeypatch, packages):
    """Install a stub Database class so `_recent_refusal_for_strategy`
    sees the test's package list instead of trying to open
    `trade_journal.db`."""
    import src.units.db.database as _db_module
    monkeypatch.setattr(_db_module, "Database", lambda **_: _StubDB(packages))


def _iso(dt):
    return dt.replace(tzinfo=timezone.utc).isoformat() if dt.tzinfo is None else dt.isoformat()


def test_no_refusal_returns_none(monkeypatch):
    from src.runtime.pipeline import _recent_refusal_for_strategy
    _patch_db(monkeypatch, packages=[])
    assert _recent_refusal_for_strategy("vwap") is None


def test_recent_refusal_within_window_blocks(monkeypatch):
    from src.runtime.pipeline import _recent_refusal_for_strategy

    now = datetime.now(timezone.utc)
    packages = [
        {
            "order_package_id": "pkg-recent",
            "strategy_name": "vwap",
            "status": "rejected",
            "updated_at": _iso(now - timedelta(seconds=60)),
        },
    ]
    _patch_db(monkeypatch, packages)

    res = _recent_refusal_for_strategy("vwap", cooldown_seconds=300)
    assert res is not None
    assert res["order_package_id"] == "pkg-recent"
    assert 50 <= res["age_seconds"] <= 70
    assert res["cooldown_seconds"] == 300


def test_old_refusal_outside_window_does_not_block(monkeypatch):
    from src.runtime.pipeline import _recent_refusal_for_strategy

    now = datetime.now(timezone.utc)
    packages = [
        {
            "order_package_id": "pkg-stale",
            "strategy_name": "vwap",
            "status": "rejected",
            "updated_at": _iso(now - timedelta(seconds=600)),  # 10 min ago
        },
    ]
    _patch_db(monkeypatch, packages)

    assert _recent_refusal_for_strategy("vwap", cooldown_seconds=300) is None


def test_only_closed_packages_does_not_block(monkeypatch):
    """A closed (filled) trade should not trigger the cooldown — only
    `status='rejected'` packages do. The open-package gate already
    handles outstanding live positions."""
    from src.runtime.pipeline import _recent_refusal_for_strategy

    now = datetime.now(timezone.utc)
    packages = [
        {
            "order_package_id": "pkg-closed",
            "strategy_name": "vwap",
            "status": "closed",
            "updated_at": _iso(now - timedelta(seconds=60)),
        },
    ]
    _patch_db(monkeypatch, packages)

    assert _recent_refusal_for_strategy("vwap") is None


def test_other_strategy_refusal_does_not_block(monkeypatch):
    """The cooldown is per-strategy. A turtle_soup refusal must not
    block a vwap dispatch."""
    from src.runtime.pipeline import _recent_refusal_for_strategy

    now = datetime.now(timezone.utc)
    packages = [
        {
            "order_package_id": "pkg-other",
            "strategy_name": "turtle_soup",
            "status": "rejected",
            "updated_at": _iso(now - timedelta(seconds=60)),
        },
    ]
    _patch_db(monkeypatch, packages)

    assert _recent_refusal_for_strategy("vwap") is None


def test_zero_cooldown_disables_gate(monkeypatch):
    """Setting `STRATEGY_REFUSAL_COOLDOWN_SECONDS=0` (or 0 explicit)
    bypasses the gate entirely. Useful for quick recovery in
    emergencies."""
    from src.runtime.pipeline import _recent_refusal_for_strategy

    now = datetime.now(timezone.utc)
    packages = [
        {
            "order_package_id": "pkg",
            "strategy_name": "vwap",
            "status": "rejected",
            "updated_at": _iso(now - timedelta(seconds=10)),
        },
    ]
    _patch_db(monkeypatch, packages)

    assert _recent_refusal_for_strategy("vwap", cooldown_seconds=0) is None


def test_env_override_changes_window(monkeypatch):
    """`STRATEGY_REFUSAL_COOLDOWN_SECONDS` env var overrides the
    default (`_DEFAULT_REFUSAL_COOLDOWN_SECONDS=300`)."""
    from src.runtime.pipeline import _recent_refusal_for_strategy, _refusal_cooldown_seconds

    now = datetime.now(timezone.utc)
    packages = [
        {
            "order_package_id": "pkg",
            "strategy_name": "vwap",
            "status": "rejected",
            "updated_at": _iso(now - timedelta(seconds=400)),
        },
    ]
    _patch_db(monkeypatch, packages)

    # Default 300 → 400-second-old refusal does NOT block.
    monkeypatch.delenv("STRATEGY_REFUSAL_COOLDOWN_SECONDS", raising=False)
    assert _refusal_cooldown_seconds() == 300
    assert _recent_refusal_for_strategy("vwap") is None

    # Bumped to 600 → same refusal DOES block.
    monkeypatch.setenv("STRATEGY_REFUSAL_COOLDOWN_SECONDS", "600")
    assert _refusal_cooldown_seconds() == 600
    res = _recent_refusal_for_strategy("vwap")
    assert res is not None and res["cooldown_seconds"] == 600


def test_unknown_strategy_returns_none(monkeypatch):
    """When `strategy_name` is None / empty (multiplexer pre-resolution),
    the gate is bypassed — there's no canonical name to scope by."""
    from src.runtime.pipeline import _recent_refusal_for_strategy
    _patch_db(monkeypatch, packages=[])
    assert _recent_refusal_for_strategy(None) is None
    assert _recent_refusal_for_strategy("") is None


def test_db_failure_returns_none(monkeypatch):
    """A SQLite hiccup must NOT refuse every dispatch — same fail-open
    contract as the open-package helper."""
    from src.runtime.pipeline import _recent_refusal_for_strategy

    class _BoomDB:
        def __init__(self, **_):
            raise RuntimeError("disk full")

    import src.units.db.database as _db_module
    monkeypatch.setattr(_db_module, "Database", _BoomDB)

    assert _recent_refusal_for_strategy("vwap") is None


def test_malformed_timestamp_returns_none(monkeypatch):
    """If the DB row's `updated_at` is unparseable, treat the row as
    if it didn't exist — we can't compute its age."""
    from src.runtime.pipeline import _recent_refusal_for_strategy

    packages = [
        {
            "order_package_id": "pkg-broken",
            "strategy_name": "vwap",
            "status": "rejected",
            "updated_at": "not a real timestamp",
        },
    ]
    _patch_db(monkeypatch, packages)

    assert _recent_refusal_for_strategy("vwap") is None
