"""The decision inbox must see BOTH recording shapes (MI-254).

OPERATOR-REPORTED 2026-09-10: *"i don't think all these decisions are still
waiting for me"*. They were right. The "Waiting on you" panel showed three
requests as `not_submitted` that the operator had settled at 07:52Z that
morning.

THE CAUSE: there are TWO ways an answer is recorded on this system and the
inbox knew one. ``POST /api/bot/work/decision`` writes an ``answer`` block; an
answer given IN CONVERSATION — how the overwhelming majority of decisions here
are actually given — is written by hand as ``verdict`` + ``chosen`` +
``answered_at`` + ``answered_by``. Grading only the first made *nobody has
answered* and *answered through the other channel* render IDENTICALLY: a
collapsed state on the surface built to prevent collapsed states.

⚠️ **THE REGRESSION POPULATION IS THE REAL OBJECT STORE, NOT A FIXTURE.** A
harness that never sees a `verdict`-without-`answer` row is exactly the gap
that let this ship, so the last test in this module reads
``docs/claude/work/objects/*.yaml`` directly.
"""
from __future__ import annotations

import glob

import pytest
import yaml

from src.runtime import work_decisions as wd


def _req(**kw):
    """One raw decision request as it appears in a work object."""
    base = {"id": "DR-x", "question": "?"}
    base.update(kw)
    return base


def _norm(raw):
    return wd.normalise_requests({"decision_requests": [raw]}, "WO-x")[0]


# ── the two shapes ───────────────────────────────────────────────────────────

def test_an_answer_block_still_grades_committed():
    """The route channel is untouched — this is the control."""
    r = _norm(_req(answer={"chosen": "a", "submitted_at": "2026-09-10T00:00:00Z"}))
    assert wd.grade_answer_state(r, None, "read") == wd.COMMITTED


def test_a_conversational_verdict_is_no_longer_invisible():
    """THE BUG. Before MI-254 this graded `not_submitted`."""
    r = _norm(_req(verdict="approved", chosen="add_it",
                   answered_at="2026-09-10T07:52:00Z", answered_by="operator"))
    assert r["answer"] is None, "it carries no answer block — that is the point"
    assert wd.grade_answer_state(r, None, "read") == wd.ANSWERED_IN_CONVERSATION


def test_reframed_not_answered_is_NOT_folded_into_answered():
    """The distinction that matters most.

    Calling this answered would HIDE a genuinely open decision — worse than the
    bug being fixed.
    """
    r = _norm(_req(verdict="reframed_not_answered",
                   answered_at="2026-09-10T07:52:00Z"))
    assert wd.grade_answer_state(r, None, "read") == wd.ENGAGED_NOT_SETTLED
    assert wd.ENGAGED_NOT_SETTLED not in wd.SETTLED_STATES


def test_an_unrecognised_verdict_is_refused_not_guessed():
    """A grader that shrugs at what it does not understand IS the collapse."""
    r = _norm(_req(verdict="mumble", answered_at="2026-09-10T07:52:00Z"))
    assert wd.grade_answer_state(r, None, "read") == wd.VERDICT_UNRECOGNISED
    assert wd.VERDICT_UNRECOGNISED not in wd.SETTLED_STATES


def test_a_row_with_neither_shape_still_grades_not_submitted():
    """The positive control: the original meaning survives, so `not_submitted`
    now genuinely means nobody has answered through any channel."""
    assert wd.grade_answer_state(_norm(_req()), None, "read") == wd.NOT_SUBMITTED


def test_every_declared_answer_state_is_reachable():
    """A contract naming a state nothing can produce is a dead claim."""
    produced = {
        wd.grade_answer_state(_norm(_req()), None, "read"),
        wd.grade_answer_state(_norm(_req()), {"submission_id": "s"}, "read"),
        wd.grade_answer_state(_norm(_req(answer={"chosen": "a"})), None, "read"),
        wd.grade_answer_state(_norm(_req()), None, wd.TRANSIT_UNREADABLE),
        wd.grade_answer_state(_norm(_req(verdict="approved")), None, "read"),
        wd.grade_answer_state(_norm(_req(verdict="reframed_not_answered")), None, "read"),
        wd.grade_answer_state(_norm(_req(verdict="mumble")), None, "read"),
    }
    assert produced == set(wd.ANSWER_STATES)


# ── ordering and provenance ──────────────────────────────────────────────────

def test_repo_truth_beats_an_unreadable_transit_log():
    """A decision already made cannot be un-made by a read failure on a channel
    it never travelled through."""
    r = _norm(_req(verdict="approved", answered_at="2026-09-10T07:52:00Z"))
    assert wd.grade_answer_state(r, None, wd.TRANSIT_UNREADABLE) == \
        wd.ANSWERED_IN_CONVERSATION


def test_the_answer_block_wins_when_a_row_somehow_carries_both():
    """Measured: 0 rows carry both today. Pinned so the precedence is a
    decision rather than an accident if one ever does — the route block is the
    channel that round-trips."""
    r = _norm(_req(answer={"chosen": "a"}, verdict="approved"))
    assert wd.grade_answer_state(r, None, "read") == wd.COMMITTED


def test_the_condition_is_carried_never_dropped():
    """`OPEN-PRS.json`'s doctrine: a verdict recorded without its condition is
    WORSE than a missing row, because it reads as complete. Dropping it here
    would put that failure on the operator's own screen."""
    r = _norm(_req(verdict="approved", chosen="add_it", condition="R12 still holds it"))
    ca = r["conversationalAnswer"]
    assert ca["condition"] == "R12 still holds it"
    assert ca["chosen"] == "add_it"


def test_the_key_is_always_present_even_when_there_is_no_verdict():
    """A key that vanishes makes a consumer branch on absence."""
    assert "conversationalAnswer" in _norm(_req())
    assert _norm(_req())["conversationalAnswer"] is None


def test_a_blank_or_non_string_verdict_is_not_an_answer():
    for bad in ("", "   ", None, 5, {"v": 1}):
        assert wd.normalise_conversational_answer(_req(verdict=bad)) is None


# ── the real store: the population that would have caught this ───────────────

def test_the_live_objects_no_longer_show_settled_questions_as_waiting():
    """⚠️ READS THE REAL WORK STORE, not a fixture.

    MEASURED 2026-09-10 over all 21 objects / 26 requests: 23 carry an `answer`
    block, 3 carry a `verdict` and no answer block, 0 carry both. Those three
    are the regression population; a fixture-only suite is what let this ship.
    """
    rows = []
    for path in sorted(glob.glob("docs/claude/work/objects/*.yaml")):
        try:
            data = yaml.safe_load(open(path, encoding="utf-8")) or {}
        except Exception:                      # a malformed object is not this test's subject
            continue
        if isinstance(data, dict):
            rows += wd.normalise_requests(data, str(data.get("id")))
    if not rows:
        pytest.skip("no decision requests in the store here")

    conversational = [r for r in rows if r["conversationalAnswer"]]
    if not conversational:
        pytest.skip("the store currently carries no hand-recorded verdict")

    # POSITIVE CONTROL FIRST: the probe can find the shape it is looking for.
    assert conversational, "probe found no verdict rows — it cannot report on them"

    for r in conversational:
        state = wd.grade_answer_state(r, None, "read")
        assert state != wd.NOT_SUBMITTED, (
            f"{r['id']} carries verdict "
            f"{r['conversationalAnswer']['verdict']!r} and still reads as "
            f"waiting on the operator — this is the MI-254 defect"
        )
    # And the partition holds over the whole real population.
    graded = [wd.grade_answer_state(r, None, "read") for r in rows]
    assert all(g in wd.ANSWER_STATES for g in graded)
    assert len(graded) == len(rows)
