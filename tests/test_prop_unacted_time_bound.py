"""A fill cannot act on a ticket that did not yet exist.

``find_unacted_tickets`` matches a fill to a ticket two ways: an explicit
``ticket_id`` link, and a ``(account, symbol, direction)`` FALLBACK. The
fallback used to carry no time bound, so ONE historical fill masked EVERY
future un-acted ticket on that symbol+direction, permanently.

MEASURED 2026-08-23 on the live prop journal: **17 of 17** `emitted` tickets —
32 h to 62 DAYS past their `valid_until`, none carrying a fill — were hidden by
it, so ``/api/bot/prop/reconcile`` reported ``unacted_count: 0`` while 17 sat
stuck. The drift alert built to catch exactly this reported clean over a
population it had excluded.

Not merely cosmetic: ``breakout_executor`` suppresses a fresh ticket while an
``outstanding_ticket:emitted`` exists (7 of 25 suppressions), and
``prop_expiry_prompt`` builds on this function — so an invisible ticket is also
un-promptable, and silently blocks its own symbol+direction forever.

Row: ``BL-20260823-PROP-UNACTED-MASKED-BY-UNBOUNDED-FILL-MATCH``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.prop import prop_reconcile

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def _ticket(tid, *, emitted, valid_h=2, symbol="ETHUSDT", direction="long",
            account="breakout_1", status="emitted"):
    return {
        "ticket_id": tid, "account_id": account, "symbol": symbol,
        "direction": direction, "status": status,
        "signal_time": emitted.isoformat(),
        "created_at": emitted.isoformat(),
        "valid_until": (emitted + timedelta(hours=valid_h)).isoformat(),
    }


def _fill(*, at, ticket_id=None, symbol="ETHUSDT", direction="long",
          account="breakout_1"):
    return {"ticket_id": ticket_id, "account_id": account, "symbol": symbol,
            "direction": direction, "opened_at": at.isoformat() if at else None}


@pytest.fixture
def patched(monkeypatch):
    def _apply(tickets, fills):
        monkeypatch.setattr(prop_reconcile.prop_journal, "list_tickets",
                            lambda **kw: tickets)
        monkeypatch.setattr(prop_reconcile.prop_journal, "list_fills",
                            lambda **kw: fills)
    return _apply


def test_a_fill_predating_the_ticket_does_not_mask_it(patched):
    """THE control. This is the exact live shape: an old fill, a new ticket."""
    old_fill = _fill(at=NOW - timedelta(days=40))
    stuck = _ticket("T-NEW", emitted=NOW - timedelta(days=2))
    patched([stuck], [old_fill])
    out = prop_reconcile.find_unacted_tickets(now=NOW)
    assert [t["ticket_id"] for t in out] == ["T-NEW"], (
        "a fill recorded 40 days BEFORE the ticket existed cannot be that "
        "ticket's fill — masking it is how 17 live tickets went invisible"
    )


def test_a_fill_after_the_ticket_still_masks_it(patched):
    """The fallback must keep working — this is not a removal."""
    t_at = NOW - timedelta(days=2)
    patched([_ticket("T", emitted=t_at)],
            [_fill(at=t_at + timedelta(minutes=30))])
    assert prop_reconcile.find_unacted_tickets(now=NOW) == []


def test_an_explicit_ticket_id_link_is_unbounded(patched):
    """An explicit link is explicit, whenever it was recorded."""
    t_at = NOW - timedelta(days=2)
    patched([_ticket("T", emitted=t_at)],
            [_fill(at=NOW - timedelta(days=40), ticket_id="T")])
    assert prop_reconcile.find_unacted_tickets(now=NOW) == []


def test_an_undateable_fill_keeps_the_old_masking(patched):
    """FAIL-SAFE: a parse failure must never MANUFACTURE a drift alert."""
    patched([_ticket("T", emitted=NOW - timedelta(days=2))],
            [_fill(at=None)])
    assert prop_reconcile.find_unacted_tickets(now=NOW) == [], (
        "an undateable fill must fall back to masking, not to alerting"
    )


def test_a_ticket_still_inside_its_window_is_not_stale(patched):
    patched([_ticket("T", emitted=NOW - timedelta(minutes=10), valid_h=2)], [])
    assert prop_reconcile.find_unacted_tickets(now=NOW) == []


def test_cross_account_isolation_survives(patched):
    """A fill on account A must not mask a ticket on account B."""
    t_at = NOW - timedelta(days=2)
    patched([_ticket("T", emitted=t_at, account="breakout_1")],
            [_fill(at=t_at + timedelta(minutes=5), account="breakout_2")])
    assert [t["ticket_id"] for t in prop_reconcile.find_unacted_tickets(now=NOW)] == ["T"]


def test_direction_isolation_survives(patched):
    t_at = NOW - timedelta(days=2)
    patched([_ticket("T", emitted=t_at, direction="long")],
            [_fill(at=t_at + timedelta(minutes=5), direction="short")])
    assert [t["ticket_id"] for t in prop_reconcile.find_unacted_tickets(now=NOW)] == ["T"]


def test_the_live_shape_end_to_end(patched):
    """17 stuck tickets behind one old fill — the measured 2026-08-23 state."""
    old = _fill(at=NOW - timedelta(days=45))
    stuck = [_ticket(f"T{i}", emitted=NOW - timedelta(days=2 + i))
             for i in range(17)]
    patched(stuck, [old])
    out = prop_reconcile.find_unacted_tickets(now=NOW)
    assert len(out) == 17, (
        f"expected all 17 to surface, got {len(out)} — the unbounded match is back"
    )
