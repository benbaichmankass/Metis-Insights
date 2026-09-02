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

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.runtime.decision_push import (  # noqa: E402
    DELIVER,
    DELIVERY_STATES,
    PUSHED,
    SESSION_GONE,
    SKIP_ALREADY_PUSHED,
    SKIP_ASKER_MALFORMED,
    SKIP_NO_ASKER,
    SKIP_NOT_COMMITTED,
    UNKNOWN,
    classify_delivery,
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
# classify_delivery — the three states
# ─────────────────────────────────────────────────────────────────────────────


def test_ok_true_is_the_only_thing_that_means_pushed():
    state, _ = classify_delivery(
        returncode=0, stdout='{"ok": true, "session_id": "' + SID + '"}', stderr=""
    )
    assert state == PUSHED


@pytest.mark.parametrize(
    "stream",
    [
        f"Error: Session not found: {SID}",
        f"Error: failed to send message to cloud session {SID}: cloud session "
        f"{SID} is archived and cannot accept new messages",
    ],
)
def test_the_two_documented_gone_signals_grade_session_gone(stream):
    state, detail = classify_delivery(returncode=1, stdout="", stderr=stream)
    assert state == SESSION_GONE
    # The marker records WHICH signal matched, not a bare verdict.
    assert detail.startswith("platform reported:")


@pytest.mark.parametrize(
    "rc,out,err",
    [
        (1, "", "Error: connect ETIMEDOUT"),
        (1, "", "Error: 529 Overloaded"),
        (1, "", "Error: could not resolve api.anthropic.com"),
        # An `ok:false` whose reason we do not recognise.
        (1, '{"ok": false, "session_id": "x", "error": "something new"}', ""),
        # Exit 0 with nothing parsable — silence is not success.
        (0, "", ""),
        (0, "Sent to cloud session.", ""),
        (None, "", "delivery timed out after 120s"),
        (None, "", "claude CLI not found on PATH"),
    ],
)
def test_everything_unrecognised_grades_unknown_never_session_gone(rc, out, err):
    state, _ = classify_delivery(returncode=rc, stdout=out, stderr=err)
    assert state == UNKNOWN, (
        "an unrecognised failure must never be read as a dead session: the "
        "marker it would write suppresses every future retry"
    )


def test_missing_credential_is_unknown_not_gone_and_not_pushed():
    # Even with output that would otherwise read as success.
    state, detail = classify_delivery(
        returncode=0, stdout='{"ok": true}', stderr="", credential_present=False
    )
    assert state == UNKNOWN
    assert "credential" in detail


def test_a_gone_phrase_in_a_quoted_message_still_requires_the_platform_wording():
    # The narrowness of the gone-patterns is the safety property. A message that
    # merely contains the word "archived" must not be read as a gone-signal.
    state, _ = classify_delivery(
        returncode=1, stdout="", stderr="Error: the archived branch could not be fetched"
    )
    assert state == UNKNOWN


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


def test_every_declared_state_is_reachable_from_the_classifier():
    # A vocabulary with an unreachable member is a dead claim.
    seen = {
        classify_delivery(returncode=0, stdout='{"ok": true}', stderr="")[0],
        classify_delivery(returncode=1, stdout="", stderr="Session not found: x")[0],
        classify_delivery(returncode=1, stdout="", stderr="boom")[0],
    }
    assert seen == set(DELIVERY_STATES)


# ─────────────────────────────────────────────────────────────────────────────
# The runner script, end to end over a real objects directory.
#
# These are the assertions that pin BEHAVIOUR rather than policy: that an
# `unknown` leaves no marker and is therefore retried, that a settled outcome
# does leave one and is therefore not, and that a switched-off channel contacts
# nobody. A pure-function test cannot show any of those.
# ─────────────────────────────────────────────────────────────────────────────


def _seed(tmp_path, name, asked_by, answer):
    import yaml
    doc = {
        "id": name,
        "decision_requests": [
            {
                "id": "DEC-1",
                "question": "Ship it?",
                "options": [{"key": "yes", "label": "Ship", "implication": "It goes live."}],
                **({"asked_by": asked_by} if asked_by else {}),
                **({"answer": answer} if answer else {}),
            }
        ],
    }
    (tmp_path / f"{name}.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))


@pytest.fixture()
def objects_dir(tmp_path, monkeypatch):
    import scripts.ops.push_decisions_back as mod
    monkeypatch.setattr(mod, "OBJECTS_DIR", tmp_path)
    return tmp_path


_A = {"chosen": "yes", "answered_at": "2026-09-02T10:16:36Z", "answered_by": "telegram"}
_GONE_SID = "session_01DEADDEADDEADDEADbb"
_BLIP_SID = "session_01BLIPBLIPBLIPBLIPcc"


def _fake_deliver(calls):
    def deliver(sid, msg):
        calls.append(sid)
        if "DEAD" in sid:
            return 1, "", (f"Error: cloud session {sid} is archived and cannot "
                           f"accept new messages")
        if "BLIP" in sid:
            return 1, "", "Error: connect ETIMEDOUT"
        return 0, '{"ok": true, "session_id": "%s"}' % sid, ""
    return deliver


def test_channel_off_contacts_nobody_and_writes_nothing(objects_dir):
    import scripts.ops.push_decisions_back as mod
    _seed(objects_dir, "OBJ-OK", {"session_id": SID}, dict(_A))
    calls = []
    out = mod.run(apply=True, deliver=_fake_deliver(calls), has_credential=lambda: False)
    assert out["channelState"] == "off_no_credential"
    assert calls == [], "a switched-off channel must not contact anyone"
    assert out["deliveryStateCounts"][UNKNOWN] == 1
    assert out["deliveryStateCounts"][SESSION_GONE] == 0, (
        "an unconfigured channel has observed nothing about the session"
    )
    assert not any(r.get("markerWritten") for r in out["results"])


def test_unknown_leaves_no_marker_and_is_retried_while_settled_outcomes_are_not(objects_dir):
    import scripts.ops.push_decisions_back as mod
    _seed(objects_dir, "OBJ-OK", {"session_id": SID}, dict(_A))
    _seed(objects_dir, "OBJ-GONE", {"session_id": _GONE_SID}, dict(_A))
    _seed(objects_dir, "OBJ-BLIP", {"session_id": _BLIP_SID}, dict(_A))

    calls = []
    first = mod.run(apply=True, deliver=_fake_deliver(calls), has_credential=lambda: True)
    assert sorted(calls) == sorted([SID, _GONE_SID, _BLIP_SID])
    assert first["deliveryStateCounts"] == {PUSHED: 1, SESSION_GONE: 1, UNKNOWN: 1}

    calls.clear()
    second = mod.run(apply=True, deliver=_fake_deliver(calls), has_credential=lambda: True)
    assert calls == [_BLIP_SID], (
        "only the unsettled attempt may be retried; a delivered or "
        "confirmed-gone answer must never be pushed twice"
    )
    assert second["actionCounts"][SKIP_ALREADY_PUSHED] == 2


def test_dry_run_is_the_default_and_sends_nothing(objects_dir):
    import scripts.ops.push_decisions_back as mod
    _seed(objects_dir, "OBJ-OK", {"session_id": SID}, dict(_A))
    calls = []
    out = mod.run(apply=False, deliver=_fake_deliver(calls), has_credential=lambda: True)
    assert calls == []
    assert out["actionCounts"][DELIVER] == 1
    assert not any(r.get("markerWritten") for r in out["results"])


def test_an_unreadable_object_is_reported_not_skipped(objects_dir):
    import scripts.ops.push_decisions_back as mod
    (objects_dir / "BROKEN.yaml").write_text("decision_requests: [oops\n  : :")
    out = mod.run(apply=False, deliver=_fake_deliver([]), has_credential=lambda: True)
    assert out["readErrors"], "we could not read it is not the same as nothing is there"
    assert mod.main([]) == 1, "a finding must fail the run rather than pass quietly"


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
