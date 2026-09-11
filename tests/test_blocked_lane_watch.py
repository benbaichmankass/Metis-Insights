"""MI-235 — a lane blocked on a manager action must be TOLD when it clears.

⚠️ WHAT A GREEN RUN HERE DOES AND DOES NOT ESTABLISH. These tests exercise the
grading policy, the refusals, and the workflow wiring. **A harness cannot reach
a blocked cloud session, and waking one is the criterion this row closes on**
(`OI-20260911-BLOCKED-LANE-WATCH-...`). So green here is necessary and is not
the observation.

The load-bearing properties are MUTATION-CHECKED at the bottom: each is broken
on a copy of the module and the test that should catch it is asserted to fail.
A property no test actually pins is a comment, not a guarantee.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "scripts" / "ops" / "blocked_lane_watch.py"
WORKFLOW = REPO / ".github" / "workflows" / "pr-queue-watch.yml"


def _load(path: Path = MODULE, name: str = "blw"):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


B = _load()

WORLD = {
    "this_repo": "o/r",
    "branches": {"claude/live"}, "branches_readable": True,
    "merged_prs": {"900"}, "merged_prs_readable": True,
    "pr_states": {"o/r#4": "open", "o/r#5": "closed", "o/r#6": "merged"},
    "pr_states_repos": {"o/r"}, "pr_states_readable": True,
    "paths_readable": True,
}


def grade(**blocker):
    return B.grade_blocker(blocker, WORLD)["state"]


# ── the module's own policy self-test is the primary suite ───────────────────
def test_module_self_test_passes():
    assert B._self_test() == 0


def test_session_registry_self_test_still_passes():
    """The `blocked-on` subcommand must not disturb the registry's own suite."""
    out = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "ops" / "session_registry.py"),
         "--self-test"], capture_output=True, text=True, cwd=str(REPO))
    assert out.returncode == 0, out.stdout + out.stderr


# ── the three measured incidents, as fixtures ────────────────────────────────
def test_mi222_closed_pr_clears():
    assert grade(kind="pull_request", ref="#5",
                 clears_when="closed_or_merged") == B.BLOCKER_CLEARED


def test_mi222_undeleted_branch_still_blocks():
    assert grade(kind="branch", ref="claude/live",
                 clears_when="deleted") == B.BLOCKER_STILL_BLOCKING


def test_mi238_cross_repo_pr_is_never_silently_cleared():
    """MI-238 was blocked on ict-trader-dashboard#215.

    ⚠️ THIS IS THE BUG THE BUILD'S OWN SELF-TEST CAUGHT. A single-repo open-PR
    listing omits every foreign PR BY CONSTRUCTION, so the
    absent-therefore-closed inference must not fire for one. Grading it
    `cleared` would wake a lane on a PR nobody looked at.
    """
    assert grade(kind="pull_request", ref="other/dash#4",
                 clears_when="closed_or_merged") == B.BLOCKER_COULD_NOT_LOOK


def test_a_listing_that_covers_the_other_repo_does_settle_it():
    w = dict(WORLD, pr_states_repos={"o/r", "other/dash"})
    assert B.grade_blocker({"kind": "pull_request", "ref": "other/dash#4",
                            "clears_when": "closed_or_merged"},
                           w)["state"] == B.BLOCKER_CLEARED


def test_our_mains_history_never_settles_another_repos_number():
    assert grade(kind="pull_request", ref="other/dash#900",
                 clears_when="closed_or_merged") == B.BLOCKER_COULD_NOT_LOOK
    assert grade(kind="pull_request", ref="#900",
                 clears_when="closed_or_merged") == B.BLOCKER_CLEARED


# ── the four states never collapse ───────────────────────────────────────────
@pytest.mark.parametrize("blind_key", [
    "branches_readable", "merged_prs_readable", "pr_states_readable"])
def test_a_failed_read_is_never_cleared_and_never_still_blocking(blind_key):
    w = dict(WORLD, **{blind_key: False})
    if blind_key == "pr_states_readable":
        w["pr_states"] = {}
        got = B.grade_blocker({"kind": "pull_request", "ref": "#4",
                               "clears_when": "merged"}, w)["state"]
    elif blind_key == "merged_prs_readable":
        w["merged_prs"] = set()
        w["pr_states"], w["pr_states_readable"] = {}, False
        got = B.grade_blocker({"kind": "pull_request", "ref": "#900",
                               "clears_when": "merged"}, w)["state"]
    else:
        got = B.grade_blocker({"kind": "branch", "ref": "claude/live",
                               "clears_when": "deleted"}, w)["state"]
    assert got == B.BLOCKER_COULD_NOT_LOOK


def test_undeclared_is_not_could_not_look():
    """*Nobody wrote one down* and *we tried and failed* have opposite remedies.

    Measured 2026-09-11: 159 of 159 non-terminal rows were `undeclared`, so
    pooling them would page on every row from the first run.
    """
    assert B.grade_row({"session_id": "s"}, WORLD)["lane_state"] == B.BLOCKER_UNDECLARED
    assert B.BLOCKER_UNDECLARED != B.BLOCKER_COULD_NOT_LOOK


def test_an_unanswered_decision_is_still_blocking_not_could_not_look():
    objs = {"WO-C": {"decision_requests": [{"id": "D2"}]}}
    got = B.grade_blocker({"kind": "operator_decision", "ref": "WO-C::D2",
                           "clears_when": "answered"}, WORLD,
                          object_reader=objs.get)["state"]
    assert got == B.BLOCKER_STILL_BLOCKING


def test_every_declared_state_is_reachable():
    seen = {
        grade(kind="pull_request", ref="#6", clears_when="merged"),
        grade(kind="pull_request", ref="#4", clears_when="merged"),
        grade(kind="vibes", ref="x", clears_when="y"),
        B.grade_row({"session_id": "s"}, WORLD)["lane_state"],
    }
    assert seen == set(B.ALL_BLOCKER_STATES)


# ── fail toward waking ───────────────────────────────────────────────────────
def test_any_cleared_blocker_frees_the_lane():
    row = {"session_id": "s", "blocked_on": [
        {"kind": "branch", "ref": "claude/live", "clears_when": "deleted"},
        {"kind": "pull_request", "ref": "#6", "clears_when": "merged"}]}
    assert B.grade_row(row, WORLD)["lane_state"] == B.BLOCKER_CLEARED


def test_an_ungradeable_blocker_pages_rather_than_reading_as_blocked():
    row = {"session_id": "s", "blocked_on": [
        {"kind": "pull_request", "ref": "other/d#1", "clears_when": "merged"}]}
    rep = B.assess([row], WORLD)
    assert [w["session_id"] for w in rep["wake_list"]] == ["s"]


# ── the latch ────────────────────────────────────────────────────────────────
def test_the_latch_pages_once_and_a_DIFFERENT_lane_still_pages():
    """⚠️ NAME CORRECTED 2026-09-11. This asserted that a DIFFERENT session with a
    DIFFERENT blocker still pages, while reading as though it covered the same
    blocker changing state — the property it does NOT test, and the one that was
    actually broken. The transition is pinned by the test below it."""
    rows = [{"session_id": "free", "state": "blocked", "blocked_on": [
        {"kind": "pull_request", "ref": "#6", "clears_when": "merged"}]}]
    first = B.assess(rows, WORLD)
    assert [w["session_id"] for w in first["wake_list"]] == ["free"]
    assert B.assess(rows, WORLD, already_paged=first["latch"])["wake_list"] == []
    rows.append({"session_id": "free2", "state": "blocked", "blocked_on": [
        {"kind": "branch", "ref": "claude/gone", "clears_when": "deleted"}]})
    again = B.assess(rows, WORLD, already_paged=first["latch"])
    assert [w["session_id"] for w in again["wake_list"]] == ["free2"]


def test_a_could_not_look_that_LATER_CLEARS_still_pages():
    """THE TRANSITION THE WHOLE MECHANISM EXISTS FOR, and it was broken.

    `_latch_key` omitted the blocker STATE while `assess` latches `cleared` and
    `could_not_look` through that one key, so a blocker that paged once as *we
    could not look* could never page again — including when it later CLEARED.
    That is MI-238's own cross-repo case: the lane stays blocked forever and the
    watcher stays quiet. Found by the MI-235 review lane; no test saw it.

    The third assertion is the positive control: the same world with an EMPTY
    latch DOES wake, so a failure here is the latch and not the grading.
    """
    row = [{"session_id": "x", "state": "RUNNING", "blocked_on": [
        {"kind": "pull_request", "ref": "other/dash#215",
         "clears_when": "closed_or_merged"}]}]
    blind = dict(WORLD, pr_states_readable=False)
    seeing = dict(WORLD, pr_states_repos={"o/r", "other/dash"})

    assert B.grade_row(row[0], blind)["lane_state"] == B.BLOCKER_COULD_NOT_LOOK
    assert B.grade_row(row[0], seeing)["lane_state"] == B.BLOCKER_CLEARED

    first = B.assess(row, blind)
    assert len(first["wake_list"]) == 1, "the blind read must page once"
    after = B.assess(row, seeing, already_paged=first["latch"])
    assert len(after["wake_list"]) == 1, (
        "the clear MUST page even though the same blocker already paged as "
        "could_not_look — this is the bug the review lane found")
    assert len(B.assess(row, seeing, already_paged=[])["wake_list"]) == 1

    # ...and the anti-fatigue property is intact: an UNCHANGED state is silent.
    assert B.assess(row, seeing, already_paged=after["latch"])["wake_list"] == []
    assert B.assess(row, blind, already_paged=first["latch"])["wake_list"] == []


def test_MUTATION_a_latch_key_without_the_state_is_caught(tmp_path):
    """Break it back the way it shipped and assert the test above would fail."""
    m = _mutant(tmp_path,
                'return (f"{session_id}|{verdict.get(\'state\')}|{verdict.get(\'kind\')}"\n'
                '            f"|{verdict.get(\'ref\')}|{verdict.get(\'clears_when\')}")',
                'return (f"{session_id}|{verdict.get(\'kind\')}"\n'
                '            f"|{verdict.get(\'ref\')}|{verdict.get(\'clears_when\')}")',
                "latch_key_without_state")
    row = [{"session_id": "x", "state": "RUNNING", "blocked_on": [
        {"kind": "pull_request", "ref": "other/dash#215",
         "clears_when": "closed_or_merged"}]}]
    blind = dict(WORLD, pr_states_readable=False)
    seeing = dict(WORLD, pr_states_repos={"o/r", "other/dash"})
    first = m.assess(row, blind)
    assert len(m.assess(row, seeing, already_paged=first["latch"])["wake_list"]) == 0, (
        "the mutant must reproduce the bug, or this test pins nothing")


def _commands(step) -> str:
    """A workflow step's EXECUTED lines, with comment lines stripped.

    ⚠️ ONE COPY, DELIBERATELY. Two tests ask "is this command invoked here?" and
    a second copy of the answer is how the two drift — the principle this repo
    states as `uniqueness is a property of the SET, so it is checked against the
    set`. Matching a raw `run:` body counts a COMMENT that merely MENTIONS a
    flag as an invocation of it, which is the unprovenanced-diagnostic class:
    prose graded as an executed command. Stripping comment lines can only ever
    remove non-invocations, so it sharpens the match and cannot blind it —
    `test_MUTATION_a_second_selftest_invocation_is_still_caught` pins that.
    """
    return "\n".join(ln for ln in str(step.get("run", "")).splitlines()
                      if not ln.strip().startswith("#"))


def test_MUTATION_a_second_selftest_invocation_is_still_caught():
    """The other direction of the comment-stripping above: sharpening the matcher
    must not have blinded it. Plant a REAL second `--self-test` call into the
    lanes step and assert the wiring test's own rule still rejects it."""
    wf = yaml.safe_load(WORKFLOW.read_text())
    steps = wf["jobs"]["watch"]["steps"]

    lanes = [st for st in steps if st.get("id") == "lanes"][0]
    assert "--self-test" not in _commands(lanes), "baseline: lanes invokes no self-test"

    planted = dict(lanes, run=lanes["run"]
                   + "\npython3 scripts/ops/blocked_lane_watch.py --self-test\n")
    mutated = [planted if st.get("id") == "lanes" else st for st in steps]
    hits = [st for st in mutated
            if "--self-test" in _commands(st)
            and "blocked_lane_watch.py" in _commands(st)]
    assert len(hits) == 2, (
        "the matcher must still SEE a genuine second invocation — if this reads 1, "
        "comment-stripping blinded the check it was meant to sharpen")


def test_an_unreadable_latch_pages_rather_than_suppressing(tmp_path):
    """Failing loud makes a broken latch announce itself as noise, not silence —
    the polarity `target_naked_alert_state.json` had to be corrected to."""
    bad = tmp_path / "receipt.json"
    bad.write_text("{ not json")
    assert B._prior_latch(bad) == []


# ── the terminal filter is counted, never silently dropped ───────────────────
def test_an_archived_rows_declaration_is_skipped_and_counted():
    rows = [{"session_id": "dead", "state": "archived", "blocked_on": [
        {"kind": "pull_request", "ref": "#6", "clears_when": "merged"}]}]
    rep = B.assess(rows, WORLD)
    assert rep["wake_list"] == []
    assert rep["rows_skipped_terminal_with_declaration"] == 1


# ── the write-time contract ──────────────────────────────────────────────────
def _blocked_on(*args):
    return subprocess.run(
        [sys.executable, str(REPO / "scripts" / "ops" / "session_registry.py"),
         "blocked-on", *args], capture_output=True, text=True, cwd=str(REPO))


def test_registry_refuses_a_kind_with_no_resolver():
    """The refusal is what guarantees `could_not_look` can only mean *we tried
    and failed* — never *there was never a resolver for this*."""
    out = _blocked_on("--session-id", "whatever", "--kind", "vibes",
                      "--ref", "x", "--clears-when", "y")
    assert out.returncode == 5
    assert "no resolver" in out.stdout or "no row for" in out.stdout


def test_every_declared_kind_has_a_working_resolver():
    """A kind in the write-time table with no dispatch arm would grade every
    such blocker `could_not_look` forever, silently."""
    for kind, clears in B.CLEARS_WHEN_BY_KIND.items():
        for cw in clears:
            ref = "x::y" if kind == "operator_decision" else "x"
            got = B.grade_blocker({"kind": kind, "ref": ref, "clears_when": cw},
                                  WORLD, object_reader=lambda _r: None,
                                  path_checker=lambda *_: None)
            assert got["state"] in B.ALL_BLOCKER_STATES


def test_a_clears_when_invalid_for_its_kind_is_refused():
    assert B.grade_blocker({"kind": "branch", "ref": "b", "clears_when": "merged"},
                           WORLD)["state"] == B.BLOCKER_COULD_NOT_LOOK


# ── the carrier wiring ───────────────────────────────────────────────────────
def test_the_watch_is_wired_into_a_workflow_that_demonstrably_fires():
    """⚠️ The carrier is `pr-queue-watch.yml` rather than a new cron BECAUSE a
    new cron nobody has seen fire is the looks-armed-is-not failure this row
    exists to end. Measured 2026-09-11 over that workflow's last 12 receipt
    landings: a continuous dated series 2026-09-06T20:49:05Z → 2026-09-11T05:14:26Z.
    """
    wf = yaml.safe_load(WORKFLOW.read_text())
    steps = wf["jobs"]["watch"]["steps"]
    lane = [s for s in steps if s.get("id") == "lanes"]
    assert len(lane) == 1, "the blocked-lane step must exist exactly once"
    assert "blocked_lane_watch.py" in lane[0]["run"]
    # ⚠️ THE SELF-TEST IS ASSERTED IN THE SHARED SELF-TEST STEP, NOT HERE, AND
    # THE MOVE IS THE POINT (MI-235 review lane, 2026-09-11). Inside the lanes
    # step it was `--self-test || exit 2`, which failed the STEP — so `Land the
    # receipt on main`, `Watch the MANAGER STATE` and `Report the verdict` were
    # all skipped, taking down the PR-queue escalation this workflow exists for,
    # while the step's own comment promised it never fails the job.
    # ⚠️ MATCH INVOCATIONS, NOT MENTIONS (corrected 2026-09-11). This counted any
    # step whose body contained the STRING `--self-test`, so a COMMENT explaining
    # why the self-test moved registered as a second invocation and failed the
    # assertion below. A check that grades prose as an executed command is the
    # unprovenanced-diagnostic class this repo has a guard for; stripping comment
    # lines can only ever REMOVE non-invocations, so it strictly sharpens the
    # matcher rather than weakening it — a real second call is still caught, and
    # `test_MUTATION_a_second_selftest_invocation_is_still_caught` pins that.
    selftest_steps = [st for st in steps
                      if "--self-test" in _commands(st)
                      and "blocked_lane_watch.py" in _commands(st)]
    assert len(selftest_steps) == 1, (
        "the grading policy must be self-tested exactly once per run")
    assert selftest_steps[0].get("id") != "lanes", (
        "a failing self-test here must fail the JOB early, never skip the "
        "later steps that carry the PR-queue escalation")
    assert steps.index(selftest_steps[0]) < steps.index(lane[0]), (
        "self-test BEFORE trusting a live run, not after")
    assert "exit 2" not in lane[0]["run"], (
        "the lanes step must not exit non-zero: every step after it that "
        "lacks `if: always()` would be skipped")
    land = [s for s in steps if "commit-to-main" in str(s.get("uses", ""))]
    assert land and "BLOCKED-LANE-WATCH.json" in land[0]["with"]["paths"], (
        "the receipt IS the paging latch; if it never lands, it never latches")
    assert land[0]["with"].get("verify-merged") == "true"


def test_the_lane_report_cannot_skip_or_mask_the_pr_queue_escalation():
    wf = yaml.safe_load(WORKFLOW.read_text())
    steps = wf["jobs"]["watch"]["steps"]
    rep = [s for s in steps if s.get("name", "").startswith("Report the BLOCKED-LANE")]
    assert len(rep) == 1
    assert rep[0].get("if") == "always()"
    assert "BLOCKED-LANE WAKE DUE" in rep[0]["run"] and "exit 1" in rep[0]["run"]
    # A missing output means the step never ran -- *we did not look* -- and must
    # not render as a clean roster.
    assert 'did not look' in rep[0]["run"]


def test_the_lane_step_does_not_dilute_the_pr_queue_exit_code_capture():
    """`check_pr_queue_watch.py` asserts the token `code=$?` appears EXACTLY
    ONCE, as a SUBSTRING test, and takes the FIRST occurrence's preceding window
    as evidence the assessed command is not a pipeline. A second match anywhere
    — a comment included — breaks a guard that exists because piping made the
    escalation channel dead on arrival."""
    assert WORKFLOW.read_text().count("code=$?") == 1
    out = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "ci" / "check_pr_queue_watch.py"),
         "--self-test"], capture_output=True, text=True, cwd=str(REPO))
    assert out.returncode == 0, out.stdout + out.stderr


# ── the shell of the sweep ───────────────────────────────────────────────────
def test_an_unreadable_register_exits_4_and_never_0(tmp_path):
    out = subprocess.run(
        [sys.executable, str(MODULE), "--registry", str(tmp_path / "nope.json")],
        capture_output=True, text=True, cwd=str(REPO))
    assert out.returncode == 4
    assert "NOT" in out.stderr


def test_a_quiet_sweep_says_it_is_not_evidence_of_a_clean_roster():
    rep = B.assess([{"session_id": "s", "state": "idle"}], WORLD)
    assert "not 'no lane is stuck'" in B.render(rep)


def test_an_open_pr_listing_that_fails_to_parse_is_not_an_empty_one(tmp_path):
    bad = tmp_path / "prs.json"
    bad.write_text("<html>403</html>")
    w = B.collect_world(this_repo="o/r", pr_state_path=bad, git_ok=False)
    assert w["pr_states_readable"] is False
    assert B.grade_blocker({"kind": "pull_request", "ref": "#1",
                            "clears_when": "closed_or_merged"},
                           w)["state"] == B.BLOCKER_COULD_NOT_LOOK


# ── MUTATION CHECKS — a property no test pins is a comment ───────────────────
def _mutant(tmp_path, old: str, new: str, name: str):
    src = MODULE.read_text()
    assert src.count(old) == 1, f"mutation anchor {name!r} is not unique"
    p = tmp_path / f"{name}.py"
    p.write_text(src.replace(old, new))
    return _load(p, name)


def test_MUTATION_dropping_the_cross_repo_guard_is_caught(tmp_path):
    m = _mutant(tmp_path,
                "if not world.get(\"pr_states_readable\") or not covered:",
                "if not world.get(\"pr_states_readable\"):",
                "no_cross_repo_guard")
    assert m.grade_blocker({"kind": "pull_request", "ref": "other/dash#4",
                            "clears_when": "closed_or_merged"},
                           WORLD)["state"] == m.BLOCKER_CLEARED, (
        "the mutant must reproduce the bug, or this test pins nothing")


def test_MUTATION_requiring_ALL_blockers_cleared_is_caught(tmp_path):
    m = _mutant(tmp_path,
                "    if BLOCKER_CLEARED in states:",
                "    if states == {BLOCKER_CLEARED}:",
                "all_not_any")
    row = {"session_id": "s", "blocked_on": [
        {"kind": "branch", "ref": "claude/live", "clears_when": "deleted"},
        {"kind": "pull_request", "ref": "#6", "clears_when": "merged"}]}
    assert m.grade_row(row, WORLD)["lane_state"] != m.BLOCKER_CLEARED


def test_MUTATION_grading_a_failed_read_as_clear_is_caught(tmp_path):
    m = _mutant(tmp_path,
                '        return {"state": BLOCKER_COULD_NOT_LOOK, "reason": REASON_GIT_READ_FAILED,\n'
                '                "why": "origin\'s branch list could not be read"}',
                '        return {"state": BLOCKER_CLEARED, "why": "mutant"}',
                "blind_reads_clear")
    w = dict(WORLD, branches_readable=False)
    assert m.grade_blocker({"kind": "branch", "ref": "claude/live",
                            "clears_when": "deleted"}, w)["state"] == m.BLOCKER_CLEARED
