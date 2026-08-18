"""Tests for the fills-aware prop equity reconstruction.

The scenario is the one MEASURED on breakout_1, 2026-08-18
(BL-20260818-PROP-RULE-DISTANCE-IGNORES-THE-FILLS-STREAM): a 694h-old snapshot
of 4825.61 with two closed fills reported since it, against a $4700 floor.
"""
from __future__ import annotations

import pytest

from src.prop import prop_reconcile

SNAP = {"account_id": "breakout_1", "balance": 4825.61, "equity": 4825.61,
        "reported_at": "2026-07-20T08:28:32.433918+00:00"}


def _fills(rows):
    return [{"pnl": p, "reported_at": t} for t, p in rows]


def test_fills_after_the_snapshot_are_applied(monkeypatch):
    monkeypatch.setattr(prop_reconcile.prop_journal, "list_fills",
                        lambda **kw: _fills([("2026-07-28T10:13:27+00:00", -18.06),
                                             ("2026-08-13T22:14:53+00:00", -50.55)]))
    r = prop_reconcile.reconstruct_equity("breakout_1", SNAP)
    assert r["balance_basis"] == "snapshot_plus_fills"
    assert r["fills_applied"] == 2
    assert r["equity_used_usd"] == pytest.approx(4757.00, abs=0.01)
    assert r["equity_provenance"] == "estimated"


def test_reconstruction_moves_the_cushion_by_the_measured_amount(monkeypatch):
    """The panel said $125.61; the reconstruction must say $57.00."""
    monkeypatch.setattr(prop_reconcile.prop_journal, "list_fills",
                        lambda **kw: _fills([("2026-07-28T10:13:27+00:00", -18.06),
                                             ("2026-08-13T22:14:53+00:00", -50.55)]))
    r = prop_reconcile.reconstruct_equity("breakout_1", SNAP)
    assert r["equity_used_usd"] - 4700.0 == pytest.approx(57.00, abs=0.01)
    assert SNAP["equity"] - 4700.0 == pytest.approx(125.61, abs=0.01)


def test_fills_before_the_snapshot_are_ignored(monkeypatch):
    """The snapshot already embodies everything realized before it."""
    monkeypatch.setattr(prop_reconcile.prop_journal, "list_fills",
                        lambda **kw: _fills([("2026-07-12T04:58:11+00:00", -18.49)]))
    r = prop_reconcile.reconstruct_equity("breakout_1", SNAP)
    assert r["balance_basis"] == "snapshot"
    assert r["fills_applied"] == 0
    assert r["equity_used_usd"] == pytest.approx(4825.61)


def test_open_fills_carry_no_realized_pnl_and_are_skipped(monkeypatch):
    monkeypatch.setattr(prop_reconcile.prop_journal, "list_fills",
                        lambda **kw: [{"pnl": None,
                                       "reported_at": "2026-08-13T16:59:26+00:00"}])
    r = prop_reconcile.reconstruct_equity("breakout_1", SNAP)
    assert r["balance_basis"] == "snapshot"


def test_an_unreadable_fills_stream_is_unavailable_not_snapshot(monkeypatch):
    """`we could not look` must never present as `we looked and found none`."""
    def boom(**kw):
        raise RuntimeError("db locked")
    monkeypatch.setattr(prop_reconcile.prop_journal, "list_fills", boom)
    r = prop_reconcile.reconstruct_equity("breakout_1", SNAP)
    assert r["balance_basis"] == "unavailable"
    assert r["fills_applied"] is None
    assert r["equity_provenance"] is None


def test_every_declared_basis_state_is_reachable(monkeypatch):
    seen = set()
    monkeypatch.setattr(prop_reconcile.prop_journal, "list_fills", lambda **kw: [])
    seen.add(prop_reconcile.reconstruct_equity("a", SNAP)["balance_basis"])
    monkeypatch.setattr(prop_reconcile.prop_journal, "list_fills",
                        lambda **kw: _fills([("2026-08-13T00:00:00+00:00", -1.0)]))
    seen.add(prop_reconcile.reconstruct_equity("a", SNAP)["balance_basis"])

    def boom(**kw):
        raise RuntimeError("x")
    monkeypatch.setattr(prop_reconcile.prop_journal, "list_fills", boom)
    seen.add(prop_reconcile.reconstruct_equity("a", SNAP)["balance_basis"])
    assert seen == set(prop_reconcile.BALANCE_BASIS_STATES)


def test_no_snapshot_anchor_is_not_graded_unavailable(monkeypatch):
    """No anchor to reconstruct FROM is not the same as failing to look."""
    monkeypatch.setattr(prop_reconcile.prop_journal, "list_fills", lambda **kw: [])
    r = prop_reconcile.reconstruct_equity("a", {"account_id": "a"})
    assert r["balance_basis"] == "snapshot"
    assert r["equity_used_usd"] is None
