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
    parse_rows,
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


def _item(**over):
    """A well-formed item. Defaults to `defaulted_to_human` DELIBERATELY.

    The carry axis applies only to that class (a genuinely-human item is graded
    on age — no session can move it), so a fixture defaulting to `judgement`
    would silently exempt every carry-ladder test from the thing it is testing.
    """
    item = {
        "id": "OO-TEST",
        "title": "a test item",
        "opened_at": "2026-08-25T18:00:00+00:00",
        "last_state_change_at": "2026-08-25T18:00:00+00:00",
        "severity": "high",
        "status": "open",
        "owner_class": OWNER_DEFAULTED,
        "owner_class_basis": "a basis long enough to clear the minimum length bar",
        "automation_path": "scripts/ops/some_wire.py",
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


def test_carry_does_not_apply_to_a_genuinely_human_item():
    """Carry counts SESSIONS that came and went. No session can mint a secret,
    read a broker terminal, or take a Tier-3 judgement — so counting sessions
    against such an item would leave the session it fires on with exactly one
    available response, a snooze, which is a mute button manufactured by the
    mechanism meant to prevent mute buttons.

    Found by the guard firing on its own register hours after it shipped.
    """
    for owner_class in ("secret_origination", "physical_or_broker", "judgement"):
        grade = grade_item(_item(owner_class=owner_class),
                           carries_unchanged=99, now=NOW)
        assert grade["state"] == STATE_CARRIED, owner_class
        assert grade["escalates"] is False, owner_class
        assert grade["carry_axis_applies"] is False, owner_class


def test_the_carry_exemption_does_not_weaken_escalation():
    """A genuinely-human item still escalates — on AGE, at its own budget.

    This is the half that makes the exemption a correction rather than a
    softening: the pressure moves axis, it does not go away.
    """
    old = _item(owner_class="secret_origination", severity="critical",
                last_state_change_at="2026-08-23T18:00:00+00:00")
    grade = grade_item(old, carries_unchanged=0, now=NOW)
    assert grade["state"] == STATE_ESCALATE_AGED
    assert grade["escalates"] is True


def test_carry_still_applies_to_a_defaulted_item():
    """The axis is kept exactly where it means something: a session COULD have
    built the wire and did not."""
    item = _item(owner_class=OWNER_DEFAULTED,
                 automation_path="scripts/ops/x.py")
    grade = grade_item(item, carries_unchanged=2, now=NOW)
    assert grade["state"] == STATE_ESCALATE_CARRIED
    assert grade["carry_axis_applies"] is True


def test_an_unclassified_item_is_not_granted_the_exemption():
    """`unclassified` must never be the cheap way to buy carry slack — it gets
    the STRICTER treatment, and validate_item refuses it outright besides."""
    grade = grade_item(_item(owner_class="unclassified"),
                       carries_unchanged=2, now=NOW)
    assert grade["state"] == STATE_ESCALATE_CARRIED
    assert grade["carry_axis_applies"] is True


def test_a_genuinely_human_item_with_no_readable_date_is_not_graded_clean():
    grade = grade_item(_item(owner_class="judgement", opened_at="nope",
                             last_state_change_at="nope"),
                       carries_unchanged=1, now=NOW)
    assert grade["state"] == STATE_NOT_MEASURABLE
    assert grade["escalates"] is False


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
        assert validate_item(item) == [], owner_class


def test_a_defaulted_item_needs_a_wire_or_a_reason():
    bare = _item()
    bare.pop("automation_path")
    assert validate_item(bare)
    assert validate_item(_item(automation_path="scripts/ops/x.py")) == []


def test_the_anti_pattern_gate():
    """'One remediation attempt failed' is not sufficient on its own.

    A failed attempt distrusts the SELECTION, and the tested-pure-function
    remedy has a precedent in this repo (src/runtime/protection_reassert.py).
    """
    excuse = _item(
        automation_path=None,
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
        automation_path=None,
        cannot_automate_reason=(
            "no broker API exists for this venue; the integration is a "
            "documented manual bridge by design"),
    )
    assert validate_item(genuine) == []


def test_placeholder_reasons_are_refused():
    for junk in ("TBD", "n/a", "unknown", "  "):
        assert validate_item(_item(automation_path=junk,
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
#
# ⚠️ REWRITTEN 2026-09-22 (E45). These tests used to build a
# `docs/claude/operator-owed-register.json`, which the 2026-09-21 operating
# reset ARCHIVED, and called `check(repo, now=..., path=...)`. The guard is
# re-pointed at `docs/claude/work/PIPELINE.jsonl` — the post-reset home of
# anything owed to the operator (`next_action: "ask_operator"`) — so the
# fixtures are rebuilt on that schema and against a real git history, for the
# original reason: a grader tested only on hand-fed integers leaves the
# MEASUREMENT itself unverified.
#
# The `grade_item` / `validate_item` tests above still pass and still test real
# code, but note what they now cover: `src/runtime/operator_owed.py` is imported
# by nothing outside this file since the re-point. That is filed, not fixed
# here.
# ---------------------------------------------------------------------------

REL = "docs/claude/work/PIPELINE.jsonl"


@pytest.fixture(autouse=True)
def _no_live_baseline(monkeypatch):
    """The shipped debt list describes the LIVE register, not these fixtures.

    Leaving it in place makes every fixture repo report BASELINE STALE for the
    eight real ids, which would drown the property under test. The baseline's
    own behaviour — exemption, its printed count, and the stale-entry failure —
    is planted and asserted in the guard's `--self-test`, run by
    `test_the_shipped_selftest_plants_its_own_violations` below.
    """
    monkeypatch.setattr(
        "scripts.ci.check_operator_owed.BASELINE_2026_09_22", {})


def _owed(rid="OO-TEST", *, action="ask_operator", state="queued",
          due=None, what="a question for the operator"):
    return {
        "id": rid,
        "what": what,
        "origin": {"kind": "session", "ref": "s1", "rerun": "re-ask"},
        "due_when": due or {"kind": "date", "due_date": "2020-01-01"},
        "next_action": action,
        "state": state,
    }


def _repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "docs" / "claude" / "work").mkdir(parents=True)
    return tmp_path


def _write(repo, rows):
    (repo / REL).write_text(
        "// PIPELINE.jsonl — append-only\n"
        + "\n".join(json.dumps(r) for r in rows) + "\n")


def _commit(repo, message):
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)


def _history(repo, *generations):
    """Commit each generation. Each carries a UNIQUE filler row.

    ⚠️ The filler is not padding. `register_commits` reads `git log -- <path>`,
    so a commit that changes nothing in the file is not a register commit at
    all — which is what a carry IS in the real store: someone appended a
    DIFFERENT row and left this one alone. Without it these fixtures would
    measure zero commits and every assertion below would pass vacuously.
    """
    for i, rows in enumerate(generations):
        _write(repo, [*rows, _owed(f"FILL-{i}", action="dispatch_lane")])
        _commit(repo, f"gen{i}")


def test_carry_is_none_before_the_register_has_any_history(tmp_path):
    repo = _repo(tmp_path)
    _write(repo, [_owed()])
    current = {"OO-TEST": _owed()}
    carries, transitions = measure_carries(repo, REL, current,
                                           register_commits(repo, REL))
    assert carries["OO-TEST"] is None, "no commits means no carry EXISTS to count"
    assert transitions["OO-TEST"] == 0


def test_carry_counts_register_commits_that_left_the_row_alone(tmp_path):
    """The core of (d): each register commit that does not change a row is one
    session carrying it forward unmoved."""
    repo = _repo(tmp_path)
    stale = _owed("STALE")
    _history(repo, [stale], [stale], [stale])
    carries, _ = measure_carries(repo, REL, {"STALE": stale},
                                 register_commits(repo, REL))
    assert carries["STALE"] == 2, (
        "three register commits with the row unchanged is two carries — the "
        "`- 1` is the commit that MADE the content")


def test_moving_a_row_clears_its_carry(tmp_path):
    repo = _repo(tmp_path)
    before = _owed("MOVED")
    after = dict(before, what="answered: yes")
    _history(repo, [before], [before], [after])
    carries, transitions = measure_carries(repo, REL, {"MOVED": after},
                                           register_commits(repo, REL))
    assert carries["MOVED"] == 0, "a row edited on the newest commit is moved"
    assert transitions["MOVED"] >= 1, (
        "and the change is OBSERVED — a green with zero observed transitions "
        "is unproven, not success")


def test_an_appended_update_supersedes_the_earlier_line(tmp_path):
    """⚠️ LAST WINS. The store is append-only: a row is updated by appending it
    again. Reading the first occurrence would grade a superseded copy and
    manufacture carries that never happened."""
    repo = _repo(tmp_path)
    old = _owed("DUP", what="unanswered")
    new = _owed("DUP", what="answered")
    _write(repo, [old, new])
    rows = parse_rows((repo / REL).read_text())
    assert rows["DUP"]["what"] == "answered"


def test_the_check_fails_on_a_carried_row_and_says_how_to_clear_it(tmp_path, capsys):
    repo = _repo(tmp_path)
    stale = _owed("STALE")
    _history(repo, [stale], [stale], [stale])
    rc = check(repo, path=REL)
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "STALE" in out
    for way_out in ("ACT on it", "MOVE it", "DEFER it", "KILL it"):
        assert way_out in out, f"the failure must offer {way_out!r}"


def test_a_row_that_is_not_due_is_the_defer_path_not_a_carry(tmp_path, capsys):
    """Grading a row waiting behind its own condition would punish the
    canonical rule's third way out."""
    repo = _repo(tmp_path)
    deferred = _owed("LATER", due={"kind": "date", "due_date": "2099-01-01"})
    _history(repo, [deferred], [deferred], [deferred])
    assert check(repo, path=REL) == 0, capsys.readouterr().out


def test_a_row_nobody_owes_the_operator_is_out_of_population(tmp_path, capsys):
    repo = _repo(tmp_path)
    lane = _owed("LANE", action="dispatch_lane")
    _history(repo, [lane], [lane], [lane])
    assert check(repo, path=REL) == 0, capsys.readouterr().out


def test_a_terminal_row_is_out_of_population(tmp_path, capsys):
    repo = _repo(tmp_path)
    done = dict(_owed("DONE", state="done"), terminal_reason="answered")
    _history(repo, [done], [done], [done])
    assert check(repo, path=REL) == 0, capsys.readouterr().out


def test_zero_observed_transitions_is_reported_as_unproven(tmp_path, capsys):
    repo = _repo(tmp_path)
    row = _owed("QUIET")
    _history(repo, [row], [row])
    check(repo, path=REL)
    assert "UNPROVEN" in capsys.readouterr().out, (
        "a register that has never moved anything must say so rather than "
        "reading as a clean green")


def test_a_missing_register_is_COULD_NOT_LOOK_not_a_pass(tmp_path, capsys):
    """⚠️ EXIT 2, NOT 0 AND NOT 1. This is the exact state the guard sat in from
    the 2026-09-21 reset until the E45 re-point: its register did not exist, so
    it graded nothing. Reading that as a pass is the defect; reading it as a
    finding would blame the PR for it."""
    repo = _repo(tmp_path)
    assert check(repo, path=REL) == 2
    assert "COULD NOT LOOK" in capsys.readouterr().out


def test_an_unparseable_register_is_COULD_NOT_LOOK(tmp_path, capsys):
    repo = _repo(tmp_path)
    (repo / REL).write_text("{not json\n")
    _commit(repo, "broken")
    assert check(repo, path=REL) == 2
    assert "COULD NOT LOOK" in capsys.readouterr().out


def test_the_shipped_selftest_plants_its_own_violations():
    """The guard's own planted controls, run as a test so a broken one is a red
    suite rather than a line nobody reads in a CI log."""
    from scripts.ci.check_operator_owed import _self_test

    assert _self_test() == 0
