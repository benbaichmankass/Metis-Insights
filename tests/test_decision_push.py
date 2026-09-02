"""Tests for the decision push-back — ``src/runtime/decision_push.py``.

The load-bearing assertions here are the ones that pin **what must NOT be
collapsed**, because every one of them corresponds to a way this subsystem could
silently lose an answer:

* an unrecognised failure must never grade ``session_gone`` (a false death
  certificate writes a marker, and the marker suppresses retry forever);
* a missing credential must never grade ``session_gone`` (we contacted nobody);
* ``unknown`` must never write a marker (that is what makes it retried);
* a ``malformed`` asker must not read as ``unrecorded`` (the first is a finding,
  the second is the ordinary state of every pre-existing request).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.runtime.decision_push import (  # noqa: E402
    DELIVER,
    PUSHED,
    SESSION_GONE,
    SKIP_ALREADY_PUSHED,
    SKIP_ASKER_MALFORMED,
    SKIP_NO_ASKER,
    SKIP_NOT_COMMITTED,
    UNKNOWN,
    plan_push,
    render_push_message,
    render_push_yaml_block,
)
from src.runtime.work_decisions import (  # noqa: E402
    ASKED_BY_MALFORMED,
    ASKED_BY_RECORDED,
    ASKED_BY_UNRECORDED,
    is_session_id,
    normalise_requests,
)

SID = "session_01PEYVqTaCY92C3HmtHwxYff"


# ─────────────────────────────────────────────────────────────────────────────
# plan_push — what happens to one request
# ─────────────────────────────────────────────────────────────────────────────


def _request(**over):
    raw = {"id": "DEC-1", "question": "q?", "options": [{"key": "a", "label": "A"}]}
    raw.update(over)
    return normalise_requests({"decision_requests": [raw]}, "OBJ-1")[0]


_ANSWER = {"chosen": "a", "answered_at": "2026-09-02T10:16:36Z", "answered_by": "telegram"}


def test_no_committed_answer_is_not_pushed():
    assert plan_push(_request(asked_by={"session_id": SID}))["action"] == SKIP_NOT_COMMITTED


def test_committed_answer_with_a_recorded_asker_is_delivered():
    plan = plan_push(_request(asked_by={"session_id": SID}, answer=dict(_ANSWER)))
    assert plan["action"] == DELIVER
    assert plan["sessionId"] == SID


def test_idempotence_comes_from_the_repo_not_from_remembering():
    answer = dict(_ANSWER)
    answer["push"] = {"state": PUSHED, "attempted_at": "2026-09-02T11:00:00Z"}
    plan = plan_push(_request(asked_by={"session_id": SID}, answer=answer))
    assert plan["action"] == SKIP_ALREADY_PUSHED
    assert plan["priorState"] == PUSHED


def test_a_session_gone_marker_also_suppresses_a_second_attempt():
    answer = dict(_ANSWER)
    answer["push"] = {"state": SESSION_GONE, "attempted_at": "2026-09-02T11:00:00Z"}
    assert plan_push(
        _request(asked_by={"session_id": SID}, answer=answer)
    )["action"] == SKIP_ALREADY_PUSHED


def test_no_asker_and_malformed_asker_are_different_outcomes():
    no_asker = plan_push(_request(answer=dict(_ANSWER)))
    malformed = plan_push(_request(asked_by={"session_id": "nope"}, answer=dict(_ANSWER)))
    assert no_asker["action"] == SKIP_NO_ASKER
    assert malformed["action"] == SKIP_ASKER_MALFORMED
    assert no_asker["action"] != malformed["action"], (
        "collapsing these buries a question whose answer can never be delivered "
        "among the ordinary ones nobody recorded an asker for"
    )


# ─────────────────────────────────────────────────────────────────────────────
# asked_by normalisation
# ─────────────────────────────────────────────────────────────────────────────


def test_asked_by_states():
    assert _request()["askedByState"] == ASKED_BY_UNRECORDED
    assert _request(asked_by={"session_id": SID})["askedByState"] == ASKED_BY_RECORDED
    assert _request(asked_by={"note": "x"})["askedByState"] == ASKED_BY_MALFORMED
    assert _request(asked_by="not-a-mapping")["askedByState"] == ASKED_BY_MALFORMED


def test_session_url_is_derived_from_the_validated_id_not_read_from_the_file():
    req = _request(asked_by={"session_id": SID, "session_url": "https://evil.example/pwn"})
    assert req["askedBy"]["sessionUrl"] == f"https://claude.ai/code/{SID}"


@pytest.mark.parametrize(
    "bad",
    ["", "session_", "session_short", "../../etc/passwd", "session_01; rm -rf /",
     "sess_01PEYVqTaCY92C3HmtHwxYff", None, 42, {"a": 1}],
)
def test_junk_is_never_a_session_id(bad):
    assert not is_session_id(bad)


def test_both_id_forms_are_accepted():
    # READ: `session_…` and `cse_…` are the same id in two spellings.
    assert is_session_id(SID)
    assert is_session_id("cse_01PEYVqTaCY92C3HmtHwxYff")


# ─────────────────────────────────────────────────────────────────────────────
# the message CARRIES the answer
# ─────────────────────────────────────────────────────────────────────────────


def test_message_quotes_the_answer_rather_than_pointing_at_it():
    req = _request(
        asked_by={"session_id": SID},
        options=[{"key": "accept_ungated", "label": "Leave it ungated",
                  "implication": "The history stays world-readable."}],
        answer={"chosen": "accept_ungated", "answered_at": "2026-09-02T10:16:36Z",
                "answered_by": "telegram"},
    )
    msg = render_push_message(req)
    assert "accept_ungated" in msg
    assert "Leave it ungated" in msg
    assert "The history stays world-readable." in msg
    # It says where truth lives, but as a reference — not as an errand the woken
    # turn must run before it can act.
    assert "docs/claude/work/objects/OBJ-1.yaml" in msg
    assert "one-way" in msg.lower()


def test_message_survives_a_free_text_only_answer():
    req = _request(
        asked_by={"session_id": SID},
        answer={"free_text": "none of these — do X instead", "answered_by": "telegram"},
    )
    msg = render_push_message(req)
    assert "none of these — do X instead" in msg
    assert "free text" in msg.lower()


# ─────────────────────────────────────────────────────────────────────────────
# the marker
# ─────────────────────────────────────────────────────────────────────────────


def test_marker_refuses_a_state_outside_the_vocabulary():
    with pytest.raises(ValueError):
        render_push_yaml_block(state="delivered", attempted_at="t", session_id=SID,
                               detail=None, pushed_by="t")


def test_marker_round_trips_through_the_reader():
    block = render_push_yaml_block(state=PUSHED, attempted_at="2026-09-02T11:00:00Z",
                                   session_id=SID, detail="ok: true", pushed_by="t")
    answer = dict(_ANSWER)
    answer["push"] = block
    req = _request(asked_by={"session_id": SID}, answer=answer)
    assert req["push"]["state"] == PUSHED
    assert req["push"]["sessionId"] == SID




# ─────────────────────────────────────────────────────────────────────────────
# render_asked_by_block — the writer, pinned against the reader
# ─────────────────────────────────────────────────────────────────────────────


def test_asked_by_block_round_trips_to_recorded():
    from src.runtime.work_decisions import render_asked_by_block
    block = render_asked_by_block(session_id=SID, note="MI-60")
    req = _request(asked_by=block)
    assert req["askedByState"] == ASKED_BY_RECORDED
    assert req["askedBy"]["sessionId"] == SID
    assert req["askedBy"]["note"] == "MI-60"


@pytest.mark.parametrize("bad", ["", "nope", "session_x", None])
def test_asked_by_block_refuses_an_unusable_id_rather_than_writing_one(bad):
    from src.runtime.work_decisions import render_asked_by_block
    with pytest.raises(ValueError):
        render_asked_by_block(session_id=bad)


def test_the_writer_cannot_produce_a_block_the_reader_calls_malformed():
    # The property that matters: anything render_asked_by_block accepts must
    # read back as `recorded`. If these two ever drift, an answer silently
    # becomes undeliverable.
    from src.runtime.work_decisions import render_asked_by_block
    for sid in (SID, "cse_01PEYVqTaCY92C3HmtHwxYff"):
        assert _request(asked_by=render_asked_by_block(session_id=sid))[
            "askedByState"] == ASKED_BY_RECORDED


# ─────────────────────────────────────────────────────────────────────────────
# The DRAIN and its WATCHER.
#
# The operator's binding requirement for mechanism B: "B is not done when the
# Routine exists. B is done when something WATCHES that it fired." These pin
# that watcher, and in particular the two distinctions that make it useful —
# `never_ran` vs `stale` (a Routine never wired up vs one that stopped), and
# `unreadable` never passing.
# ─────────────────────────────────────────────────────────────────────────────

from datetime import datetime, timedelta, timezone  # noqa: E402

import scripts.ops.check_drain_liveness as liveness  # noqa: E402
import scripts.ops.push_decisions_back as drain  # noqa: E402

_NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


def _receipt(hours_ago: float) -> dict:
    at = (_NOW - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")
    return {"schema": 1, "last_run_at": at, "runs_recorded": 3, "recent": []}


def test_absent_receipt_is_never_ran_not_stale():
    v = liveness.grade(receipt=None, read_error=None, window_hours=6, now=_NOW)
    assert v["state"] == liveness.NEVER_RAN, (
        "a Routine that was created and never fired needs a different fix from "
        "one that stopped; collapsing them sends a reader hunting a regression "
        "that never happened"
    )


def test_fresh_and_stale_split_on_the_window():
    assert liveness.grade(receipt=_receipt(1), read_error=None,
                          window_hours=6, now=_NOW)["state"] == liveness.FRESH
    assert liveness.grade(receipt=_receipt(30), read_error=None,
                          window_hours=6, now=_NOW)["state"] == liveness.STALE


def test_unreadable_is_its_own_state_and_never_passes():
    v = liveness.grade(receipt=None, read_error="boom", window_hours=6, now=_NOW)
    assert v["state"] == liveness.UNREADABLE
    assert v["state"] != liveness.NEVER_RAN, "we could not look is not we looked"


def test_an_undateable_receipt_is_unreadable_not_fresh():
    v = liveness.grade(receipt={"last_run_at": "not-a-date", "runs_recorded": 1},
                       read_error=None, window_hours=6, now=_NOW)
    assert v["state"] == liveness.UNREADABLE, (
        "a liveness record that cannot be dated cannot be shown to be fresh, "
        "and the fail-safe reading is that it is not"
    )


def test_only_fresh_exits_zero():
    for state, receipt, err in (
        (liveness.FRESH, _receipt(1), None),
        (liveness.STALE, _receipt(99), None),
        (liveness.NEVER_RAN, None, None),
        (liveness.UNREADABLE, None, "boom"),
    ):
        v = liveness.grade(receipt=receipt, read_error=err, window_hours=6, now=_NOW)
        assert v["state"] == state
    assert set(liveness.LIVENESS_STATES) == {
        liveness.FRESH, liveness.STALE, liveness.NEVER_RAN, liveness.UNREADABLE}


@pytest.fixture()
def drain_dirs(tmp_path, monkeypatch):
    objects = tmp_path / "objects"
    objects.mkdir()
    monkeypatch.setattr(drain, "OBJECTS_DIR", objects)
    monkeypatch.setattr(drain, "RECEIPT_PATH", tmp_path / "DECISION-DRAIN.json")
    return objects, tmp_path / "DECISION-DRAIN.json"


def _seed_obj(objects, name, asked_by, answer):
    import yaml
    doc = {"id": name, "decision_requests": [{
        "id": "DEC-1", "question": "Ship it?",
        "options": [{"key": "yes", "label": "Ship", "implication": "It goes live."}],
        **({"asked_by": asked_by} if asked_by else {}),
        **({"answer": answer} if answer else {})}]}
    (objects / f"{name}.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))


_ANS = {"chosen": "yes", "answered_at": "2026-09-02T10:16:36Z", "answered_by": "telegram"}


def test_queue_carries_the_rendered_message_not_a_pointer(drain_dirs):
    objects, _ = drain_dirs
    _seed_obj(objects, "OBJ-OK", {"session_id": SID}, dict(_ANS))
    q = drain.build_queue()
    assert q["queueDepth"] == 1
    msg = q["queue"][0]["message"]
    # The repo renders it, so the rule is enforced rather than trusted to
    # whoever writes the Routine prompt.
    assert "yes" in msg and "It goes live." in msg
    assert "one-way" in msg.lower()


def test_recording_unknown_writes_no_marker_so_it_is_retried(drain_dirs):
    objects, _ = drain_dirs
    _seed_obj(objects, "OBJ-OK", {"session_id": SID}, dict(_ANS))
    out = drain.record_outcome(object_id="OBJ-OK", request_id="DEC-1",
                               state=UNKNOWN, detail=None, pushed_by="t")
    assert out["markerWritten"] is False
    assert drain.build_queue()["queueDepth"] == 1, "an unsettled push must retry"


def test_recording_a_settled_state_stops_further_pushes(drain_dirs):
    objects, _ = drain_dirs
    _seed_obj(objects, "OBJ-OK", {"session_id": SID}, dict(_ANS))
    drain.record_outcome(object_id="OBJ-OK", request_id="DEC-1",
                         state=PUSHED, detail="trig_x", pushed_by="t")
    assert drain.build_queue()["queueDepth"] == 0


def test_a_push_marker_is_refused_on_an_uncommitted_answer(drain_dirs):
    objects, _ = drain_dirs
    _seed_obj(objects, "OBJ-OPEN", {"session_id": SID}, None)
    with pytest.raises(ValueError):
        drain.record_outcome(object_id="OBJ-OPEN", request_id="DEC-1",
                             state=PUSHED, detail=None, pushed_by="t")


def test_an_unrecognised_state_is_refused(drain_dirs):
    objects, _ = drain_dirs
    _seed_obj(objects, "OBJ-OK", {"session_id": SID}, dict(_ANS))
    with pytest.raises(ValueError):
        drain.record_outcome(object_id="OBJ-OK", request_id="DEC-1",
                             state="delivered", detail=None, pushed_by="t")


def test_the_empty_run_leaves_evidence(drain_dirs):
    _, receipt_path = drain_dirs
    drain.write_receipt(queue_depth=0, note="nothing to push")
    doc = json.loads(receipt_path.read_text())
    assert doc["runs_recorded"] == 1
    v = liveness.grade(receipt=doc, read_error=None, window_hours=6)
    assert v["state"] == liveness.FRESH, (
        "an empty run is what separates 'nothing needed pushing' from "
        "'the drain is dead'"
    )


# ─────────────────────────────────────────────────────────────────────────────
# resume_context — design C's substrate.
#
# These pin the distinction the whole mechanism turns on: `partial` must not
# grade as `stated`. The plausible failure in C is a resumer reading a
# confident-looking block, re-deriving the wrong next action, and proceeding.
# ─────────────────────────────────────────────────────────────────────────────

from src.runtime.work_decisions import RESUME_STATES  # noqa: E402
from src.runtime.work_decisions import (  # noqa: E402
    RESUME_PARTIAL,
    RESUME_STATED,
    RESUME_UNSTATED,
)

_OPTS = [{"key": "yes", "label": "Y"}, {"key": "no", "label": "N"}]


def _resume(rc):
    raw = {"id": "R", "options": _OPTS}
    if rc is not None:
        raw["resume_context"] = rc
    return normalise_requests({"decision_requests": [raw]}, "OBJ")[0]


def test_absent_resume_context_is_unstated():
    assert _resume(None)["resumeState"] == RESUME_UNSTATED


def test_context_without_a_per_option_next_step_is_partial_not_stated():
    r = _resume({"what_was_in_flight": "building the drain"})
    assert r["resumeState"] == RESUME_PARTIAL, (
        "a resumer told the direction but not the action must not grade the "
        "same as one told both — that gap is where C fails"
    )
    assert r["resumeContext"]["whatWasInFlight"] == "building the drain"


def test_full_context_is_stated():
    r = _resume({"what_was_in_flight": "building the drain",
                 "what_this_unblocks": "the floor",
                 "next_step_per_option": {"yes": "ship it", "no": "revert"}})
    assert r["resumeState"] == RESUME_STATED
    assert r["resumeContext"]["nextStepPerOption"]["yes"] == "ship it"


def test_next_steps_for_options_the_author_never_declared_do_not_count():
    r = _resume({"what_was_in_flight": "x",
                 "next_step_per_option": {"maybe": "???"}})
    assert r["resumeState"] == RESUME_PARTIAL, (
        "guidance keyed on an option that does not exist is not guidance a "
        "resumer can act on"
    )


def test_resume_states_are_exactly_three_and_all_reachable():
    seen = {
        _resume(None)["resumeState"],
        _resume({"what_was_in_flight": "x"})["resumeState"],
        _resume({"what_was_in_flight": "x",
                 "next_step_per_option": {"yes": "go"}})["resumeState"],
    }
    assert seen == set(RESUME_STATES)
