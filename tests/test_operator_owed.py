"""The operator-owed register: the grading, and the MEASUREMENT behind part (d).

`BL-20260825-OPERATOR-OWED-ITEMS-HAVE-NO-REGISTER-NO-AGE-AND-NO-ESCALATION`
asks for a check that FAILS when an item is carried across N sessions with no
state change. The carry count is what makes that measured rather than asserted,
so `test_carry_*` build a real git repo and count against it — a grader tested
only on hand-fed integers would leave the measurement itself unverified, which
is the shape of half the rows in this repo's backlog.
"""
from __future__ import annotations

import datetime as _dt
import json
import subprocess

import pytest

from scripts.ci.check_operator_owed import (
    check,
    measure_carries,
    register_commits,
)
from src.runtime.operator_owed import (
    ALL_OWNER_CLASSES,
    ALL_STATES,
    OWNER_DEFAULTED,
    STATE_CARRIED,
    STATE_ESCALATE_AGED,
    STATE_ESCALATE_CARRIED,
    STATE_MOVED,
    STATE_NOT_MEASURABLE,
    STATE_RESOLVED,
    STATE_SNOOZED,
    grade_item,
    is_escalation,
    summarise,
    validate_item,
)

NOW = _dt.datetime(2026, 8, 25, 19, 0, tzinfo=_dt.timezone.utc)
REL = "docs/claude/operator-owed-register.json"


def _item(**over):
    item = {
        "id": "OO-TEST",
        "title": "a test item",
        "opened_at": "2026-08-25T18:00:00+00:00",
        "last_state_change_at": "2026-08-25T18:00:00+00:00",
        "severity": "high",
        "status": "open",
        "owner_class": "judgement",
        "owner_class_basis": "a basis long enough to clear the minimum length bar",
    }
    item.update(over)
    return item


# ---------------------------------------------------------------------------
# the grading
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("carries, expected", [
    (0, STATE_MOVED),
    (1, STATE_CARRIED),
    (2, STATE_ESCALATE_CARRIED),
    (7, STATE_ESCALATE_CARRIED),
])
def test_the_carry_ladder(carries, expected):
    assert grade_item(_item(), carries_unchanged=carries, now=NOW)["state"] == expected


def test_not_measurable_is_not_moved():
    """The collapse this module exists to prevent.

    A brand-new register has no history, so nothing can be said about carry.
    Reporting that as `moved` would make the one state in which the register has
    demonstrated NOTHING read as perfect health.
    """
    grade = grade_item(_item(), carries_unchanged=None, now=NOW)
    assert grade["state"] == STATE_NOT_MEASURABLE
    assert grade["state"] != STATE_MOVED
    assert grade["escalates"] is False


def test_age_is_an_independent_trip_path():
    """Carry under-reports when a session skips the register entirely, so age
    must be able to fire on its own — including when carry is unmeasurable."""
    old = _item(last_state_change_at="2026-08-20T18:00:00+00:00")
    assert grade_item(old, carries_unchanged=0, now=NOW)["state"] == STATE_ESCALATE_AGED
    assert grade_item(old, carries_unchanged=None, now=NOW)["state"] == STATE_ESCALATE_AGED


def test_severity_sets_the_age_budget():
    day_old = _item(last_state_change_at="2026-08-24T12:00:00+00:00")
    assert grade_item(dict(day_old, severity="critical"),
                      carries_unchanged=0, now=NOW)["state"] == STATE_ESCALATE_AGED
    assert grade_item(dict(day_old, severity="medium"),
                      carries_unchanged=0, now=NOW)["state"] == STATE_MOVED


def test_age_falls_back_to_opened_at_and_says_so():
    grade = grade_item(_item(last_state_change_at=None), carries_unchanged=0, now=NOW)
    assert grade["age_basis"] == "opened_at"


def test_an_undateable_item_is_not_given_a_fabricated_age():
    grade = grade_item(_item(last_state_change_at="not a date", opened_at="also not"),
                       carries_unchanged=1, now=NOW)
    assert grade["age_days"] is None
    assert grade["state"] == STATE_CARRIED


def test_a_snooze_needs_a_named_trigger_not_just_a_date():
    """A date alone is a mute button — the backlog governance rule, applied here."""
    dated = _item(snoozed_until="2026-09-30T00:00:00+00:00")
    assert grade_item(dated, carries_unchanged=5, now=NOW)["state"] == STATE_ESCALATE_CARRIED
    triggered = dict(dated, snooze_trigger="alpaca_live is funded")
    assert grade_item(triggered, carries_unchanged=5, now=NOW)["state"] == STATE_SNOOZED


def test_an_expired_snooze_stops_deferring():
    expired = _item(snoozed_until="2026-08-01T00:00:00+00:00",
                    snooze_trigger="a trigger that already happened")
    assert grade_item(expired, carries_unchanged=2, now=NOW)["state"] == STATE_ESCALATE_CARRIED


def test_terminal_items_are_never_escalated():
    for status in ("resolved", "withdrawn"):
        grade = grade_item(_item(status=status), carries_unchanged=99, now=NOW)
        assert grade["state"] == STATE_RESOLVED
        assert grade["escalates"] is False


def test_only_the_two_escalations_fail():
    assert [s for s in ALL_STATES if is_escalation(s)] == [
        STATE_ESCALATE_CARRIED, STATE_ESCALATE_AGED]


def test_summarise_emits_every_state_including_the_zeroes():
    """An omitted bucket makes an absent state indistinguishable from an
    ungradeable one."""
    counts = summarise([grade_item(_item(), carries_unchanged=0, now=NOW)])
    assert set(counts) == set(ALL_STATES)
    assert counts[STATE_MOVED] == 1


# ---------------------------------------------------------------------------
# the structural refusals
# ---------------------------------------------------------------------------

def test_a_wellformed_item_passes():
    assert validate_item(_item()) == []


def test_unclassified_is_refused_and_is_not_a_synonym_for_human():
    problems = validate_item(_item(owner_class="unclassified"))
    assert problems and "unclassified" in problems[0]


def test_every_owner_class_is_accepted():
    for owner_class in ALL_OWNER_CLASSES:
        if owner_class == "unclassified":
            continue
        item = _item(owner_class=owner_class)
        if owner_class == OWNER_DEFAULTED:
            item["automation_path"] = "scripts/ops/something.py"
        assert validate_item(item) == [], owner_class


def test_a_defaulted_item_needs_a_wire_or_a_reason():
    assert validate_item(_item(owner_class=OWNER_DEFAULTED))
    assert validate_item(_item(owner_class=OWNER_DEFAULTED,
                               automation_path="scripts/ops/x.py")) == []


def test_the_anti_pattern_gate():
    """'One remediation attempt failed' is not sufficient on its own.

    A failed attempt distrusts the SELECTION, and the tested-pure-function
    remedy has a precedent in this repo (src/runtime/protection_reassert.py).
    """
    excuse = _item(
        owner_class=OWNER_DEFAULTED,
        cannot_automate_reason=(
            "an auto-remediation cancelled the wrong leg once, so a human owns "
            "this from now on"),
    )
    problems = validate_item(excuse)
    assert any("tested_decision_function" in p for p in problems)
    assert validate_item(dict(excuse,
                              tested_decision_function="src/runtime/x.py")) == []


def test_a_genuine_reason_is_not_caught_by_the_anti_pattern_gate():
    """A reason that does not rest on a failed attempt needs no decision fn."""
    genuine = _item(
        owner_class=OWNER_DEFAULTED,
        cannot_automate_reason=(
            "no broker API exists for this venue; the integration is a "
            "documented manual bridge by design"),
    )
    assert validate_item(genuine) == []


def test_placeholder_reasons_are_refused():
    for junk in ("TBD", "n/a", "unknown", "  "):
        assert validate_item(_item(owner_class=OWNER_DEFAULTED,
                                   cannot_automate_reason=junk))


def test_noncanonical_severity_spellings_are_refused():
    for spelling in ("P1", "medium-high", "low-medium"):
        assert validate_item(_item(severity=spelling))


def test_a_terminal_item_must_say_what_happened():
    assert validate_item(_item(status="resolved"))
    assert validate_item(_item(status="resolved",
                               resolution="the branch was deleted")) == []


# ---------------------------------------------------------------------------
# the MEASUREMENT — part (d)'s basis, against a real git history
# ---------------------------------------------------------------------------

def _repo(tmp_path, items):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "docs" / "claude").mkdir(parents=True)
    _write(tmp_path, items)
    return tmp_path


def _write(repo, items):
    (repo / REL).write_text(json.dumps(
        {"schema_version": 1, "carry_limit": 2, "items": items}, indent=2))


def _commit(repo, message):
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)


def test_carry_is_none_before_the_register_has_any_history(tmp_path):
    repo = _repo(tmp_path, [_item()])
    current = {"OO-TEST": _item()}
    carries, transitions = measure_carries(repo, REL, current, register_commits(repo, REL))
    assert carries["OO-TEST"] is None, "no commits means no carry EXISTS to count"
    assert transitions["OO-TEST"] == 0


def test_carry_counts_register_commits_that_left_the_item_alone(tmp_path):
    """The core of (d): each register commit that does not change an item is
    one session carrying it forward unmoved."""
    stale = _item(id="STALE")
    repo = _repo(tmp_path, [stale, _item(id="OTHER")])
    _commit(repo, "register the items")

    def carry_of(item_id):
        current = {i["id"]: i for i in json.loads((repo / REL).read_text())["items"]}
        carries, _ = measure_carries(repo, REL, current, register_commits(repo, REL))
        return carries[item_id]

    # The commit that MADE the current content is not itself a carry.
    assert carry_of("STALE") == 0

    # A later register commit that touches only OTHER carries STALE once.
    _write(repo, [stale, _item(id="OTHER", title="edited once")])
    _commit(repo, "move OTHER")
    assert carry_of("STALE") == 1
    assert carry_of("OTHER") == 0

    # And again -> at the limit of 2, which is the escalation.
    _write(repo, [stale, _item(id="OTHER", title="edited twice")])
    _commit(repo, "move OTHER again")
    assert carry_of("STALE") == 2
    grade = grade_item(stale, carries_unchanged=2, now=NOW)
    assert grade["state"] == STATE_ESCALATE_CARRIED and grade["escalates"]


def test_moving_an_item_clears_its_carry(tmp_path):
    stale = _item(id="STALE")
    repo = _repo(tmp_path, [stale, _item(id="OTHER")])
    _commit(repo, "register")
    _write(repo, [stale, _item(id="OTHER", title="edited")])
    _commit(repo, "move OTHER")

    _write(repo, [_item(id="STALE", status="resolved", resolution="done"),
                  _item(id="OTHER", title="edited")])
    _commit(repo, "resolve STALE")
    current = {i["id"]: i for i in json.loads((repo / REL).read_text())["items"]}
    carries, transitions = measure_carries(repo, REL, current, register_commits(repo, REL))
    assert carries["STALE"] == 0
    assert transitions["STALE"] >= 1, "a real content change is an observed transition"


def test_an_uncommitted_edit_reads_as_in_flight_not_as_a_carry(tmp_path):
    stale = _item(id="STALE")
    repo = _repo(tmp_path, [stale])
    _commit(repo, "register")
    _write(repo, [_item(id="STALE", title="being edited right now")])
    current = {i["id"]: i for i in json.loads((repo / REL).read_text())["items"]}
    carries, _ = measure_carries(repo, REL, current, register_commits(repo, REL))
    assert carries["STALE"] == 0


def test_the_check_fails_on_a_carried_item_and_says_how_to_clear_it(tmp_path, capsys):
    stale = _item(id="STALE")
    repo = _repo(tmp_path, [stale, _item(id="OTHER")])
    _commit(repo, "register")
    for n in range(2):
        _write(repo, [stale, _item(id="OTHER", title=f"edit {n}")])
        _commit(repo, f"move OTHER {n}")

    assert check(repo, now=NOW, path=REL) == 1
    out = capsys.readouterr().out
    assert "escalate_carried" in out and "STALE" in out
    # The escalation must name the ways OUT, since re-listing is what it replaces.
    assert "snoozed_until" in out and "resolution" in out


def test_the_check_passes_a_register_whose_items_are_moving(tmp_path, capsys):
    repo = _repo(tmp_path, [_item(id="A"), _item(id="B")])
    _commit(repo, "register")
    assert check(repo, now=NOW, path=REL) == 0
    assert "UNPROVEN" in capsys.readouterr().out, (
        "a register that has never moved anything must say so rather than "
        "reading as a clean green")


def test_a_missing_register_fails_rather_than_passing_vacuously(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    assert check(tmp_path, now=NOW, path=REL) == 1
