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


# ── Report time is not event time (BL-20260828 / re-measured 2026-08-31) ──────
#
# The manual bridge has no broker feed, so the operator reads a balance off the
# terminal and types the fill in minutes later. Selecting on `reported_at`
# alone therefore re-applies a close the snapshot ALREADY held.

SNAP_0831 = {"account_id": "breakout_1", "balance": 4787.34, "equity": 4787.34,
             "reported_at": "2026-08-30T19:33:29.584285+00:00"}


def test_a_later_reported_gain_with_no_event_time_does_not_inflate_the_cushion(
    monkeypatch,
):
    """THE MEASURED 2026-08-31 CASE, and the dangerous direction.

    Snapshot 18 -> 19 moved +33.34; the SOLUSDT close reported 5.5 min after
    snapshot 19 carries +35.28 gross of a -2.08 commission = +33.20, matching to
    $0.14 -- so the snapshot plainly already embodied it. Applying it again put
    `equity_used_usd` at 4822.62 and the cushion to the $4,700 floor at 122.62
    when it was 87.34.

    That is the number `prop_risk_gate` caps against, so an inflated cushion
    does not merely mislead a panel -- it makes the gate AUTHORISE a ticket that
    breaches the floor.
    """
    monkeypatch.setattr(
        prop_reconcile.prop_journal, "list_fills",
        lambda **kw: [{"pnl": 35.28, "closed_at": None,
                       "reported_at": "2026-08-30T19:39:00.972519+00:00"}])
    r = prop_reconcile.reconstruct_equity("breakout_1", SNAP_0831)
    assert r["fills_applied"] == 0
    assert r["fills_withheld_unplaceable_gain"] == 1
    assert r["equity_used_usd"] == pytest.approx(4787.34)
    assert r["equity_used_usd"] - 4700.0 == pytest.approx(87.34, abs=0.01)


def test_an_unplaceable_LOSS_is_still_applied(monkeypatch):
    """The asymmetry, asserted rather than left to the reader.

    Only 4 of the 19 pnl-carrying fills on the live table have `closed_at`, so
    a rule that required an event time would silently drop 79% of the stream --
    worse than the bug. A loss we cannot place may SHRINK a safety cushion; a
    gain we cannot place may not GROW one. False pessimism costs a refused
    trade, false optimism costs the account.
    """
    monkeypatch.setattr(
        prop_reconcile.prop_journal, "list_fills",
        lambda **kw: [{"pnl": -40.00, "closed_at": None,
                       "reported_at": "2026-08-30T19:39:00.972519+00:00"}])
    r = prop_reconcile.reconstruct_equity("breakout_1", SNAP_0831)
    assert r["fills_applied"] == 1
    assert r["equity_used_usd"] == pytest.approx(4747.34, abs=0.01)


def test_a_known_event_time_places_a_gain_exactly_in_both_directions(monkeypatch):
    """When `closed_at` IS present we need neither the asymmetry nor a guess."""
    before = [{"pnl": 50.0, "closed_at": "2026-08-30T18:00:00+00:00",
               "reported_at": "2026-08-30T19:39:00+00:00"}]
    after = [{"pnl": 50.0, "closed_at": "2026-08-30T20:00:00+00:00",
              "reported_at": "2026-08-30T20:05:00+00:00"}]
    monkeypatch.setattr(prop_reconcile.prop_journal, "list_fills",
                        lambda **kw: before)
    r = prop_reconcile.reconstruct_equity("breakout_1", SNAP_0831)
    assert r["fills_applied"] == 0, "a gain that closed BEFORE the snapshot is in it"
    assert r["equity_used_usd"] == pytest.approx(4787.34)

    monkeypatch.setattr(prop_reconcile.prop_journal, "list_fills",
                        lambda **kw: after)
    r = prop_reconcile.reconstruct_equity("breakout_1", SNAP_0831)
    assert r["fills_applied"] == 1, "a gain that closed AFTER it is genuinely new"
    assert r["equity_used_usd"] == pytest.approx(4837.34, abs=0.01)
