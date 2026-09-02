"""Tests for the sub-session registry detectors and the spawn coupling.

The two detectors are only evidence if they are shown to fire on a planted
defect AND to stay quiet on a clean input. Every detector below is asserted in
BOTH directions; a one-directional assertion proves a guard runs, not that it
discriminates.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "ops"))
import session_registry as sr  # noqa: E402

REG = {"sessions": [{"session_id": "session_01AAAAAAAAAAAAAAAAAAAA", "title": "a"},
                    {"session_id": "session_01BBBBBBBBBBBBBBBBBBBB", "title": "b"}]}
LIVE = [{"id": "session_01AAAAAAAAAAAAAAAAAAAA"}, {"id": "session_01BBBBBBBBBBBBBBBBBBBB"}]
STRANGER = {"id": "session_01ZZZZZZZZZZZZZZZZZZZZ"}


def test_self_test_passes():
    assert sr.main(["--self-test"]) == 0


# --------------------------------------------------------------------------- #
# reconcile — the LIVE detector
# --------------------------------------------------------------------------- #
def test_planted_unregistered_live_session_is_detected():
    assert sr.reconcile(REG, True, LIVE + [STRANGER])["state"] == "unregistered"


def test_clean_registry_is_quiet():
    """The other direction. Without this the detector could be a constant alarm."""
    assert sr.reconcile(REG, True, LIVE)["state"] == "reconciled"


@pytest.mark.parametrize("observation", [None, {"nope": 1}, [], "not json at all"])
def test_absent_or_unusable_observation_is_never_reconciled(observation):
    """`not_observed` is the whole enforcement mechanism: a registry nobody
    compared against anything must not grade clean."""
    assert sr.reconcile(REG, True, observation)["state"] == "not_observed"


def test_unreadable_registry_is_not_reconciled():
    assert sr.reconcile(None, False, LIVE)["state"] == "unreadable"


def test_archived_session_cannot_be_orphaned():
    obs = LIVE + [dict(STRANGER, session_status="SESSION_STATUS_ARCHIVED")]
    assert sr.reconcile(REG, True, obs)["state"] == "reconciled"


def test_unknown_status_is_graded_live_so_the_alarm_fails_safe():
    """An entry with no status must alarm. The fail-safe direction for an alarm
    about LOSING work is to alarm, not to assume the session is finished."""
    assert sr.reconcile(REG, True, LIVE + [STRANGER])["state"] == "unregistered"


def test_manager_is_not_an_orphan_of_itself():
    me = "session_01MGRMGRMGRMGRMGRMGR"
    assert sr.reconcile(REG, True, LIVE + [{"id": me}], me)["state"] == "reconciled"


def test_another_managers_child_is_excluded_but_an_unattributed_one_alarms():
    me = "session_01MGRMGRMGRMGRMGRMGR"
    foreign = dict(STRANGER, parent_session_id="session_01OTHEROTHEROTHEROTHER")
    assert sr.reconcile(REG, True, LIVE + [foreign], me)["state"] == "reconciled"
    assert sr.reconcile(REG, True, LIVE + [STRANGER], me)["state"] == "unregistered"


def test_population_is_reported_so_a_count_never_stands_alone():
    v = sr.reconcile(REG, True, LIVE + [STRANGER])
    pop = v["population"]
    assert pop["observed"] == 3 and pop["graded"] == 3 and pop["registered_rows"] == 2


# --------------------------------------------------------------------------- #
# The observation parser — regression tests for a MEASURED false-positive class
# --------------------------------------------------------------------------- #
def test_mcp_ccr_envelope_is_parsed_rather_than_regex_harvested(tmp_path):
    """⚠️ REGRESSION. The first live run reported `session_context`,
    `session_status` and `session_inbound` as unregistered sessions: the MCP
    result arrives inside an `<other-session>` wrapper, `json.loads` failed, and
    the free-text fallback matched JSON KEY NAMES. 3 of 32 findings were
    nonexistent."""
    payload = {"ccr": {"data": [{"id": "session_01AAAAAAAAAAAAAAAAAAAA",
                                 "session_context": {"model": "x"},
                                 "session_status": "SESSION_STATUS_IDLE"}]}}
    f = tmp_path / "obs.txt"
    f.write_text('<other-session nonce="x" untrusted="true">\nblurb\n'
                 + json.dumps(payload) + "\n</other-session>\n", encoding="utf-8")
    entries = sr.normalise_observation(sr._load_observation(str(f)))
    assert [e["session_id"] for e in entries] == ["session_01AAAAAAAAAAAAAAAAAAAA"]


def test_text_fallback_does_not_match_json_key_names():
    ids = sr._TEXT_HARVEST_RE.findall(
        'session_context session_status cross_session_inbound '
        'session_01AAAAAAAAAAAAAAAAAAAA')
    assert ids == ["session_01AAAAAAAAAAAAAAAAAAAA"]


# --------------------------------------------------------------------------- #
# cross_check — the OFFLINE detector (this is what CI can actually run)
# --------------------------------------------------------------------------- #
def _ck(state, owner):
    return {"items": [{"id": "MI-01", "state": state, "owner": owner}]}


def test_unregistered_owner_on_an_in_flight_item_is_detected():
    v = sr.cross_check(REG, True, _ck("in_flight", "session_01ZZZZZZZZZZZZZZZZZZZZ"), True)
    assert v["state"] == "owner_unregistered"
    assert v["findings"][0]["session_id"] == "session_01ZZZZZZZZZZZZZZZZZZZZ"


def test_registered_owner_is_quiet():
    assert sr.cross_check(REG, True, _ck("in_flight", "session_01AAAAAAAAAAAAAAAAAAAA"),
                          True)["state"] == "consistent"


def test_abbreviated_id_in_prose_is_not_a_false_finding():
    """⚠️ MEASURED: treating an abbreviation as a distinct session inflated the
    2026-09-02 census from 5 to 7."""
    ck = _ck("in_flight", "drains #4 (session_01AAAAAAAA) + #5 running")
    assert sr.cross_check(REG, True, ck, True)["state"] == "consistent"


def test_prefix_tolerance_runs_one_way_only():
    """A registered id may EXTEND the candidate; the reverse must still alarm,
    or a short registered id would swallow every longer unknown one."""
    ck = _ck("in_flight", "session_01AAAAAAAAAAAAAAAAAAAAEXTRA")
    assert sr.cross_check(REG, True, ck, True)["state"] == "owner_unregistered"


def test_non_enforced_state_is_censused_but_not_a_finding():
    v = sr.cross_check(REG, True, _ck("done", "session_01ZZZZZZZZZZZZZZZZZZZZ"), True)
    assert v["state"] == "consistent"
    assert len(v["census"]) == 1 and v["findings"] == []
    assert v["population"]["absent_total"] == 1 and v["population"]["absent_enforced"] == 0


def test_a_checklist_naming_no_session_is_no_owners_not_consistent():
    assert sr.cross_check(REG, True, _ck("in_flight", "manager"), True)["state"] == "no_owners"


@pytest.mark.parametrize("reg_ok,ck_ok", [(False, True), (True, False), (False, False)])
def test_an_unreadable_input_is_never_consistent(reg_ok, ck_ok):
    assert sr.cross_check(REG if reg_ok else None, reg_ok,
                          _ck("in_flight", "session_01AAAAAAAAAAAAAAAAAAAA") if ck_ok else None,
                          ck_ok)["state"] == "unreadable"


# --------------------------------------------------------------------------- #
# structural
# --------------------------------------------------------------------------- #
def test_duplicate_and_missing_ids_are_structural_findings():
    dup = {"sessions": [{"session_id": "session_01AAAAAAAAAAAAAAAAAAAA"},
                        {"session_id": "session_01AAAAAAAAAAAAAAAAAAAA"}]}
    assert sr.structural(dup, True)["state"] == "malformed"
    assert sr.structural({"sessions": [{"title": "nameless"}]}, True)["state"] == "malformed"


def test_a_spawn_pending_row_with_a_key_is_legitimate():
    doc = {"sessions": [{"title": "planned", "state": "spawn_pending",
                         "registry_key": "pending-x", "session_id": None}]}
    assert sr.structural(doc, True)["state"] == "well_formed"


def test_the_live_registry_is_structurally_clean():
    """Runs against the REAL file. If this ever fails the registry itself is
    broken, which is worth knowing regardless of any PR's diff."""
    doc, ok = sr.read_json(sr.REGISTRY_PATH)
    assert ok, "SESSIONS.json does not parse"
    assert sr.structural(doc, ok)["state"] == "well_formed"


# --------------------------------------------------------------------------- #
# The coupling: register writes the row AND yields the prompt
# --------------------------------------------------------------------------- #
def _seed(tmp_path):
    p = tmp_path / "SESSIONS.json"
    p.write_text(json.dumps({"schema_version": 1, "sessions": []}, indent=2) + "\n",
                 encoding="utf-8")
    return p


def test_register_appends_a_row_and_the_prompt_names_it(tmp_path):
    p = _seed(tmp_path)
    row, ref = sr.register(p, title="T", why="W", spawned_by="session_01MGRMGRMGRMGRMGRMGR",
                           session_id="session_01NEWNEWNEWNEWNEWNEW")
    assert ref == "session_01NEWNEWNEWNEWNEWNEW"
    assert json.loads(p.read_text())["sessions"][0]["session_id"] == ref
    assert ref in sr.spawn_prompt("T", "W", ref)


def test_register_without_an_id_writes_a_pending_row_that_confirm_completes(tmp_path):
    p = _seed(tmp_path)
    row, ref = sr.register(p, title="T", why="W", spawned_by="session_01MGRMGRMGRMGRMGRMGR")
    assert row["state"] == "spawn_pending" and row["session_id"] is None
    assert sr.pending_rows(json.loads(p.read_text())) != []
    sr.confirm(p, registry_key=row["registry_key"],
               session_id="session_01NEWNEWNEWNEWNEWNEW")
    doc = json.loads(p.read_text())
    assert doc["sessions"][0]["session_id"] == "session_01NEWNEWNEWNEWNEWNEW"
    assert sr.pending_rows(doc) == []


def test_register_refuses_to_append_over_an_unparseable_registry(tmp_path):
    p = tmp_path / "SESSIONS.json"
    p.write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(SystemExit):
        sr.register(p, title="T", why="W", spawned_by="session_01MGRMGRMGRMGRMGRMGR")
    assert p.read_text() == "{ this is not json", "the broken file was overwritten"


def test_register_preserves_the_files_serialisation(tmp_path):
    """A naive dump re-encodes every non-ASCII line and buries a one-row change
    in a whole-file diff — the lesson backlog_append.py was written for."""
    p = tmp_path / "SESSIONS.json"
    p.write_text(json.dumps({"_comment": "⚠️ keep me", "sessions": []},
                            indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    sr.register(p, title="T", why="W", spawned_by="session_01MGRMGRMGRMGRMGRMGR",
                session_id="session_01NEWNEWNEWNEWNEWNEW")
    text = p.read_text(encoding="utf-8")
    assert "⚠️ keep me" in text, "non-ASCII was re-encoded to \\u escapes"
    assert text.endswith("\n")
