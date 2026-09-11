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
def test_the_latch_pages_once_and_a_new_clear_still_pages():
    rows = [{"session_id": "free", "state": "blocked", "blocked_on": [
        {"kind": "pull_request", "ref": "#6", "clears_when": "merged"}]}]
    first = B.assess(rows, WORLD)
    assert [w["session_id"] for w in first["wake_list"]] == ["free"]
    assert B.assess(rows, WORLD, already_paged=first["latch"])["wake_list"] == []
    rows.append({"session_id": "free2", "state": "blocked", "blocked_on": [
        {"kind": "branch", "ref": "claude/gone", "clears_when": "deleted"}]})
    again = B.assess(rows, WORLD, already_paged=first["latch"])
    assert [w["session_id"] for w in again["wake_list"]] == ["free2"]


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
    assert "--self-test" in lane[0]["run"], (
        "a watcher whose grading is broken must fail loudly, not write a "
        "confident receipt")
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
