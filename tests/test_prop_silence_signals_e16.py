"""E16 — the two operator-facing changes that came out of the 2026-09-21
``breakout_1`` silence, pinned as tests.

WHY THESE TWO AND NOT ONE. The operator reported two symptoms that look
opposite and share one root shape — **a terminal state read as a live one**:

1. *"I keep getting the prop status request notifications even though we said
   we don't need that anymore."* Ticket ``prop-manual-5c4694c96819`` expired
   unanswered on 2026-09-13 at 07:55Z; ``expired`` was not excluded from
   :data:`prop_status_request.ASK_TICKET_SINCE_SNAPSHOT`, and the arm's test
   was ``created_at > snapshot.reported_at`` — a comparison only an operator
   report can clear. So a ticket that took **no** exposure asked every 12h,
   forever, for the report it was asking for.
2. *"I haven't gotten any trade notifications there in a suspiciously long
   time."* The sizing gate CAN block that account indefinitely and, until this
   change, said so only to a journalctl line in a unit with ~30 minutes of
   retention.

⚠️ **NEITHER TEST CLAIMS THE PING WAS DELIVERED.** They pin the branch and the
cadence; which channel the message actually reaches is an OBSERVATION on the
live host and is stated as such in the PR, never inferred from green here
(``BL-20260909-…`` says so in terms).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.prop import prop_balance as pb
from src.prop import prop_status_request as psr

NOW = datetime(2026, 9, 21, 20, 0, tzinfo=timezone.utc)
SNAP = "2026-08-30T19:33:29.584285+00:00"   # the real breakout_1 snapshot


def _ticket(status, *, created="2026-09-13T06:55:35.696603+00:00",
            valid_until=None):
    return {"status": status, "created_at": created, "valid_until": valid_until}


# ─────────────────────────── the ask's trigger ───────────────────────────

def test_the_expired_ticket_that_asked_forever_no_longer_asks():
    """THE REGRESSION, with the live row that produced the complaint.

    ``prop-manual-5c4694c96819``: created 2026-09-13T06:55:35Z, 1h validity,
    went ``expired`` unanswered. It is NEWER than the 2026-08-30 snapshot and
    would have been counted as exposure on every tick for the following 8 days.
    """
    state, detail = psr.assess_activity(
        snapshot_reported_at=SNAP,
        open_positions=[],
        tickets=[_ticket("expired",
                         valid_until="2026-09-13T07:55:35.983351+00:00")],
        now=NOW,
    )
    assert state == psr.QUIESCENT, detail
    assert detail["tickets_since_snapshot"] == 0


def test_a_placeable_ticket_still_asks():
    """THE NEGATIVE CONTROL — the probe can find a positive.

    Without this, the test above proves only that ``assess_activity`` returns
    QUIESCENT, which a function that always returned QUIESCENT would also pass.
    """
    state, detail = psr.assess_activity(
        snapshot_reported_at=SNAP,
        open_positions=[],
        tickets=[_ticket("emitted", created=(NOW - timedelta(minutes=5)).isoformat(),
                         valid_until=(NOW + timedelta(minutes=55)).isoformat())],
        now=NOW,
    )
    assert state == psr.ASK_TICKET_SINCE_SNAPSHOT
    assert detail["tickets_since_snapshot"] == 1


def test_an_emitted_ticket_leaves_the_population_by_the_clock():
    """The LATCH is what this change removes: a ticket must be able to fall out
    of the asking population with NO operator report, or the ask can only ever
    be cleared by the thing it is asking for."""
    live = _ticket("emitted", created=(NOW - timedelta(hours=2)).isoformat(),
                   valid_until=(NOW + timedelta(minutes=1)).isoformat())
    assert psr.ticket_can_take_exposure(live, NOW) is True
    # Same row, one minute later. Nothing was reported; the clock alone moved.
    assert psr.ticket_can_take_exposure(live, NOW + timedelta(minutes=2)) is False


@pytest.mark.parametrize("status", sorted(psr.SETTLED_TICKET_STATUSES
                                          | psr.NON_EXPOSING_TICKET_STATUSES))
def test_settled_and_never_exposed_statuses_do_not_ask(status):
    assert psr.ticket_can_take_exposure(_ticket(status), NOW) is False


@pytest.mark.parametrize("status", ["placed", "expiry_prompted",
                                    "awaiting_report", "invalidated_prompted",
                                    "some_status_nobody_has_written_yet", ""])
def test_unknown_and_mid_dialog_statuses_still_ask(status):
    """FAIL-SAFE POLARITY, and it is the opposite of an order gate's.

    *We asked and were not answered* is **we do not know**, never *nothing
    happened*. An unrecognised status must never be able to silence a safety
    prompt — that is how a status added later would quietly disarm this.
    """
    assert psr.ticket_can_take_exposure(_ticket(status), NOW) is True


def test_an_open_position_still_outranks_everything():
    """The 2026-08-14 concern is NOT relaxed by the 2026-09-21 decision:
    unrealized drawdown is invisible to the cushion, so an open position asks
    whatever the tickets say."""
    state, _ = psr.assess_activity(
        snapshot_reported_at=SNAP,
        open_positions=[{"symbol": "ETHUSDT"}],
        tickets=[_ticket("expired")],
        now=NOW,
    )
    assert state == psr.ASK_POSITION_OPEN


def test_an_unreadable_ticket_read_never_becomes_quiescent():
    state, _ = psr.assess_activity(
        snapshot_reported_at=SNAP, open_positions=[], tickets=None, now=NOW)
    assert state == psr.UNKNOWN


# ────────────────────── the sizing-refusal page ──────────────────────

@pytest.fixture()
def _isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr("src.utils.paths.runtime_logs_dir", lambda: tmp_path)
    return tmp_path


def _pages(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "src.runtime.execution_diagnostics.enqueue_prop_sizing_refusal",
        lambda **kw: sent.append(kw) or None)
    return sent


def test_a_stale_refusal_pages_once_not_once_per_tick(_isolated_runtime,
                                                      monkeypatch):
    """THE BUG THIS CADENCE EXISTS TO PREVENT is the one the fix could easily
    have introduced: the ``stale`` reason embeds the snapshot's AGE
    (``"snapshot 528.6h old"``), so de-duplicating on the MESSAGE is a pager
    that fires every tick for the life of the condition — the desensitised
    alarm this repo files as a P1.
    """
    sent = _pages(monkeypatch)
    for age in (528.6, 528.7, 528.8):   # three ticks, three different messages
        pb.note_refusal("stale", "breakout_1",
                        {"reason": f"snapshot {age}h old", "max_age_hours": 24.0})
    assert len(sent) == 1, sent
    assert "breakout_1" in sent[0]["reason"]


def test_a_recovery_makes_the_next_refusal_a_fresh_occurrence(_isolated_runtime,
                                                              monkeypatch):
    sent = _pages(monkeypatch)
    pb.note_refusal("stale", "breakout_1", {"reason": "snapshot 30h old"})
    pb.note_refusal("ok", "breakout_1", {})          # operator sent a balance
    pb.note_refusal("stale", "breakout_1", {"reason": "snapshot 25h old"})
    assert len(sent) == 2


def test_a_different_fault_pages_again(_isolated_runtime, monkeypatch):
    """``stale`` and ``error`` call for opposite operator actions (send a
    balance vs fix the reader), so they are not one occurrence."""
    sent = _pages(monkeypatch)
    pb.note_refusal("stale", "breakout_1", {"reason": "snapshot 30h old"})
    pb.note_refusal("error", "breakout_1", {"reason": "OSError: boom"})
    assert len(sent) == 2


def test_a_persistent_refusal_repings_so_an_indefinite_stall_cannot_go_quiet(
        _isolated_runtime, monkeypatch):
    """The whole point of ``BL-20260909-…``: an account can stop trading
    INDEFINITELY. One page at the transition and then silence would re-create
    that, just more slowly."""
    now = datetime.now(timezone.utc)
    assert pb.should_page({"state": "stale",
                           "notified_at": (now - timedelta(hours=25)).isoformat()},
                          "stale", now, 24.0) is True
    assert pb.should_page({"state": "stale",
                           "notified_at": (now - timedelta(hours=2)).isoformat()},
                          "stale", now, 24.0) is False
    # `<= 0` disables the re-ping (transition-only), and says so.
    assert pb.should_page({"state": "stale",
                           "notified_at": (now - timedelta(days=9)).isoformat()},
                          "stale", now, 0.0) is False


def test_an_undateable_stamp_pages_rather_than_assuming_we_paged_recently(
        _isolated_runtime):
    now = datetime.now(timezone.utc)
    assert pb.should_page({"state": "stale", "notified_at": "not-a-date"},
                          "stale", now, 24.0) is True


def test_note_refusal_never_raises_into_the_order_path(_isolated_runtime,
                                                       monkeypatch):
    """It runs inside the balance fetcher. A diagnostic must never be able to
    strand a trade."""
    def _boom(**kw):
        raise RuntimeError("telegram inbox on fire")
    monkeypatch.setattr(
        "src.runtime.execution_diagnostics.enqueue_prop_sizing_refusal", _boom)
    assert pb.note_refusal("stale", "breakout_1", {"reason": "x"}) is False


def test_the_refusal_page_names_the_account_and_not_an_order(_isolated_runtime):
    """The wording matters: this is NOT a dropped order, it is an account that
    can place none. Calling it an order failure would name a cause no code
    path tested."""
    from src.runtime import execution_diagnostics as ed

    path = ed.enqueue_prop_sizing_refusal(
        account="breakout_1", reason="prop_balance_stale: breakout_1 …")
    assert path is not None
    body = json.loads(path.read_text())["body"]
    assert "breakout_1" in body
    assert "Order execution failed" not in body
