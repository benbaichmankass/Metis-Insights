"""Strategy-monocle gate (PR 1 of 3) — one open package per strategy
globally.

Operator directive 2026-05-03: each strategy may have at most one
open ``order_packages`` row at a time, regardless of how many
accounts follow it. Once a package is logged, the strategy focuses
on monitoring + updating that package via the order-monitor loop
until SL/TP hits or the strategy decides to close (PRs 2 + 3 of
this sprint wire the close path).

Pre-fix every actionable VWAP tick stacked a new package into
``trade_journal.db::order_packages`` — the operator's snapshot on
2026-05-03 22:38 showed 10+ open VWAP packages with no linked
trades, and a second wave of 5+ ``below_min_balance`` /
``skipped_not_assigned`` rejection rows landed each tick (PR #382's
new contract).

This PR puts the gate at the dispatch boundary in
``src/runtime/pipeline.py``. The strategy unit's signal-builder is
unchanged; the pipeline consults
``Database.get_order_packages_by_strategy(strategy, status='open')``
before calling ``_signal_to_order_package``, and skips dispatch
with ``status='skipped'`` / ``reason='open_package_exists'`` if any
match exists.

Three contracts under test:

1. **No open package → dispatch proceeds.** The helper returns
   ``None`` for an empty DB; the existing pipeline path runs.
2. **Open package exists → dispatch skipped.** The helper returns
   the package id; the pipeline emits a ``skipped`` outcome with
   ``reason='open_package_exists'``.
3. **Closed package does not block.** A ``status='closed'`` row
   for the same strategy must NOT block a new package — the gate
   is open-only.
"""
from __future__ import annotations

import pytest

from src.runtime.pipeline import _has_open_package_for_strategy
from src.units.db.database import Database


@pytest.fixture
def tmp_journal(tmp_path, monkeypatch):
    db_path = tmp_path / "trade_journal.db"
    monkeypatch.setenv("TRADE_JOURNAL_DB", str(db_path))
    db = Database(db_path=str(db_path))
    return db


def _insert_pkg(db, *, pkg_id, strategy="vwap", status="open",
                linked_trade_id=None, symbol="BTCUSDT"):
    db.insert_order_package({
        "order_package_id": pkg_id,
        "strategy_name": strategy,
        "symbol": symbol,
        "direction": "long",
        "entry": 80_000.0,
        "sl": 79_500.0,
        "tp": 80_500.0,
        "confidence": 0.42,
        "status": status,
        "linked_trade_id": linked_trade_id,
        "meta": {},
    })


# ---------------------------------------------------------------------------
# Contract 1: no open package → ``None`` (dispatch proceeds)
# ---------------------------------------------------------------------------


def test_returns_none_when_no_open_package(tmp_journal):
    assert _has_open_package_for_strategy("vwap") is None


def test_returns_none_for_unknown_strategy(tmp_journal):
    _insert_pkg(tmp_journal, pkg_id="pkg-vwap-1", strategy="vwap")
    assert _has_open_package_for_strategy("turtle_soup") is None


def test_returns_none_for_missing_strategy_name():
    """A signal whose ``meta.strategy_name`` is unset bypasses the
    gate — there's no canonical attribution to scope the query."""
    assert _has_open_package_for_strategy(None) is None
    assert _has_open_package_for_strategy("") is None


# ---------------------------------------------------------------------------
# Contract 2: open package exists → returns its id
# ---------------------------------------------------------------------------


def test_returns_open_package_id(tmp_journal):
    """A linked open package (trade at the broker) must block dispatch."""
    _insert_pkg(tmp_journal, pkg_id="pkg-vwap-open-001", strategy="vwap",
                linked_trade_id=1)
    result = _has_open_package_for_strategy("vwap")
    assert result == "pkg-vwap-open-001"


def test_returns_id_of_first_match_when_multiple_open(tmp_journal):
    """Multiple linked open packages — gate returns the first match."""
    _insert_pkg(tmp_journal, pkg_id="pkg-1", strategy="vwap",
                linked_trade_id=10)
    _insert_pkg(tmp_journal, pkg_id="pkg-2", strategy="vwap",
                linked_trade_id=11)
    result = _has_open_package_for_strategy("vwap")
    assert result in {"pkg-1", "pkg-2"}


# ---------------------------------------------------------------------------
# Contract 3: closed package does not block
# ---------------------------------------------------------------------------


def test_closed_package_does_not_block(tmp_journal):
    _insert_pkg(tmp_journal, pkg_id="pkg-vwap-closed", strategy="vwap",
                status="closed")
    assert _has_open_package_for_strategy("vwap") is None


def test_orphaned_status_does_not_block(tmp_journal):
    """The reconciler (BUG-042 PR 2) writes status='orphaned' to the
    *trades* table, but the *order_packages* row is cascaded to
    ``status='closed'``. So an orphaned trade should not leave the
    package in a state that blocks new signals — pin the contract.
    """
    _insert_pkg(tmp_journal, pkg_id="pkg-vwap-cascaded", strategy="vwap",
                status="closed")
    assert _has_open_package_for_strategy("vwap") is None


# ---------------------------------------------------------------------------
# Best-effort: DB read failure returns None (does not crash dispatch)
# ---------------------------------------------------------------------------


def test_db_read_failure_returns_none_silently(monkeypatch):
    """A DB-read exception must not raise — the gate is best-effort.
    Returning None lets the dispatcher proceed (one extra duplicate
    package is preferable to refusing every signal during a DB
    outage)."""
    class _BoomDb:
        def __init__(self, *a, **kw):
            pass

        def get_order_packages_by_strategy(self, *a, **kw):
            raise RuntimeError("simulated DB outage")

    monkeypatch.setattr("src.units.db.database.Database", _BoomDb)
    assert _has_open_package_for_strategy("vwap") is None


# ---------------------------------------------------------------------------
# 2026-05-09 — unlinked open packages now BLOCK the gate
# ---------------------------------------------------------------------------
#
# Reverses the BUG-049 contract above. With ``linked_only=True`` on the
# gate, a multi-account dispatch where every account refused on
# ``zero_exchange_capacity`` left the package row at status='open',
# linked_trade_id=NULL — and the next tick's gate query filtered it out,
# so dispatch retried every minute. Production trades 1003–1045 on
# 2026-05-09 are 50+ rejection rows from that loop. Treating any open
# row (linked or not) as gate-blocking caps the rejection cadence at
# 1 per ``_sweep_unlinked_packages`` cycle (5 min) instead of 1/min.


def test_unlinked_open_package_blocks(tmp_journal):
    """A package with status='open' but ``linked_trade_id IS NULL`` is an
    in-flight dispatch (or a dispatch that just failed across every
    account on ``zero_exchange_capacity``). It MUST block subsequent
    ticks — the +5 min ``_sweep_unlinked_packages`` releases stale
    rows so the gate isn't permanently stuck."""
    _insert_pkg(tmp_journal, pkg_id="pkg-unlinked", strategy="vwap",
                linked_trade_id=None)
    assert _has_open_package_for_strategy("vwap") == "pkg-unlinked"


def test_linked_open_package_still_blocks(tmp_journal):
    """A package with status='open' AND a real linked_trade_id (a trade
    was placed at the broker) must still block new signals."""
    _insert_pkg(tmp_journal, pkg_id="pkg-linked", strategy="vwap",
                linked_trade_id=42)
    result = _has_open_package_for_strategy("vwap")
    assert result == "pkg-linked"


def test_mix_unlinked_and_linked_both_block(tmp_journal):
    """When both unlinked and linked open packages exist, the gate
    returns *some* row id (whichever is newest by updated_at) and the
    dispatch is blocked. Either id is fine — both represent in-flight
    state that the strategy should not pile on top of."""
    _insert_pkg(tmp_journal, pkg_id="pkg-unlinked", strategy="vwap",
                linked_trade_id=None)
    _insert_pkg(tmp_journal, pkg_id="pkg-linked", strategy="vwap",
                linked_trade_id=99)
    result = _has_open_package_for_strategy("vwap")
    assert result in {"pkg-unlinked", "pkg-linked"}


# ---------------------------------------------------------------------------
# Multi-symbol (2026-05-22): "one open package per strategy" is per instrument
# ---------------------------------------------------------------------------
#
# Before symbol-scoping, an open BTCUSDT package suppressed an MES entry for
# the same strategy (and vice versa) — the cross-contamination the multi-
# symbol mirror has to avoid. The gate now scopes by symbol when one is
# passed, while ``symbol=None`` keeps the legacy strategy-global scope.


def test_open_btc_package_does_not_block_mes(tmp_journal):
    """An open BTCUSDT vwap package must NOT block an MES vwap entry."""
    _insert_pkg(tmp_journal, pkg_id="pkg-btc", strategy="vwap",
                linked_trade_id=1, symbol="BTCUSDT")
    assert _has_open_package_for_strategy("vwap", "MES") is None


def test_symbol_scoped_gate_blocks_matching_symbol(tmp_journal):
    """The same open BTCUSDT package still blocks a BTCUSDT entry."""
    _insert_pkg(tmp_journal, pkg_id="pkg-btc", strategy="vwap",
                linked_trade_id=1, symbol="BTCUSDT")
    assert _has_open_package_for_strategy("vwap", "BTCUSDT") == "pkg-btc"


def test_per_symbol_packages_are_independent(tmp_journal):
    """BTC and MES packages for the same strategy are tracked separately."""
    _insert_pkg(tmp_journal, pkg_id="pkg-btc", strategy="vwap",
                linked_trade_id=1, symbol="BTCUSDT")
    _insert_pkg(tmp_journal, pkg_id="pkg-mes", strategy="vwap",
                linked_trade_id=2, symbol="MES")
    assert _has_open_package_for_strategy("vwap", "BTCUSDT") == "pkg-btc"
    assert _has_open_package_for_strategy("vwap", "MES") == "pkg-mes"


def test_symbol_none_keeps_global_scope(tmp_journal):
    """Omitting symbol preserves the legacy strategy-global behaviour."""
    _insert_pkg(tmp_journal, pkg_id="pkg-mes", strategy="vwap",
                linked_trade_id=2, symbol="MES")
    # No symbol → any open package for the strategy blocks (legacy).
    assert _has_open_package_for_strategy("vwap") == "pkg-mes"
