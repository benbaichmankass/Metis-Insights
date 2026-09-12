"""Every `commit-to-main` caller must confirm its rows LANDED, and must budget
enough time to wait for that.

WHY THIS IS GENERIC AND NOT ELEVEN ONE-LINE OPT-INS
(`BL-20260830-TWELVE-OF-THIRTEEN-COMMIT-TO-MAIN-CALLERS-NEVER-CHECK-THEIR-ROWS-LANDED`).

The row's own framing was "each is a one-line opt-in PLUS a `timeout-minutes`
check". Doing exactly that would have left the real defect in place, and the
proof is that it had ALREADY bitten by the time this file was written:

`gld-compat-matrix` shipped `timeout-minutes: 30` when the action's wait was 18
— a genuine 12-minute margin. The wait default was then widened 18 -> 30 in a
later change, and `30 > 30` is no margin at all: that job would be killed at
exactly the wait's deadline and report a timeout for a merge that was about to
land, which is verbatim the failure the backlog row warns about. Nobody edited
gld-compat-matrix. Widening a SHARED DEFAULT silently consumed every caller's
margin at once, and the change's author checked only the caller being edited.

So the invariant belongs here, where one edit to the action is checked against
ALL of its callers, rather than in eleven places that each looked fine.
"""
from __future__ import annotations

import glob
import os

import yaml

ACTION = ".github/actions/commit-to-main/action.yml"
USES = "./.github/actions/commit-to-main"

#: Minimum slack between a caller's budget and the wait, so a caller has room to
#: do its actual WORK. `budget > wait` alone is not enough: a job whose budget
#: merely exceeds the wait has zero time left for the thing it exists to do.
#:
#: ⚠️ THIS IS A NECESSARY CONDITION, NOT A SUFFICIENT ONE, and reading a pass
#: here as "every budget is adequate" is exactly the over-trust this repo keeps
#: paying for. The invariant that actually matters is `budget > work + wait`,
#: and `work` is not knowable from the YAML — so a job doing 40 minutes of work
#: under a 45-minute budget passes this test and still dies mid-wait.
#: `econ-calendar-survey-backfill` was precisely that case: 45 cleared this bar
#: and was still raised to 80, because its 45 had been sized for the WORK before
#: any wait existed. When adding a caller, size the budget as work + wait +
#: slack; this test only catches the floor.
MIN_SLACK_MIN = 5

#: GitHub's own default when a job declares no `timeout-minutes`.
GITHUB_DEFAULT_TIMEOUT_MIN = 360


def _wait_minutes() -> int:
    d = yaml.safe_load(open(ACTION).read())
    return int(d["inputs"]["verify-timeout-minutes"]["default"])


def _caller_jobs():
    """(workflow basename, job name, job dict) for every commit-to-main caller."""
    for f in sorted(glob.glob(".github/workflows/*.yml")):
        text = open(f).read()
        if f"uses: {USES}" not in text:
            continue
        doc = yaml.safe_load(text)
        for job_name, job in (doc.get("jobs") or {}).items():
            steps = job.get("steps") or []
            if any(s.get("uses") == USES for s in steps):
                yield os.path.basename(f), job_name, job


def test_there_are_callers_to_check():
    """A positive control on the FINDER. Every assertion below iterates this
    generator, so if the `uses:` string were renamed they would all pass
    vacuously over an empty list — green while checking nothing, which is the
    exact shape this whole backlog row is about."""
    found = list(_caller_jobs())
    assert len(found) >= 10, (
        f"only {len(found)} commit-to-main callers found — the detector is "
        f"probably broken, not the repo suddenly clean"
    )


def test_every_caller_verifies_its_rows_merged():
    """`verify-merged` absent means the step exits 0 when the PR OPENS, so a
    push that never merges reads as a success and the rows are unreadable."""
    missing = []
    for wf, job_name, job in _caller_jobs():
        for s in job["steps"]:
            if s.get("uses") != USES:
                continue
            if str((s.get("with") or {}).get("verify-merged", "")).lower() != "true":
                missing.append(f"{wf}:{job_name}")
    assert not missing, (
        "these callers land rows and never confirm they arrived: "
        + ", ".join(missing)
    )


def test_every_caller_budget_outlasts_the_merge_wait():
    """THE ONE THAT CATCHES A WIDENED DEFAULT.

    Read from the action rather than hardcoded, so raising
    `verify-timeout-minutes` fails here instead of silently killing callers
    mid-wait.
    """
    wait = _wait_minutes()
    too_tight = []
    for wf, job_name, job in _caller_jobs():
        budget = job.get("timeout-minutes", GITHUB_DEFAULT_TIMEOUT_MIN)
        if int(budget) < wait + MIN_SLACK_MIN:
            too_tight.append(f"{wf}:{job_name} budget={budget}m wait={wait}m")
    assert not too_tight, (
        f"budget must be >= wait + {MIN_SLACK_MIN}m of slack for the job's own "
        f"work; these would be killed mid-wait and report a timeout for a merge "
        f"that was about to land: " + "; ".join(too_tight)
    )


def test_no_caller_overrides_the_wait_with_its_own_timeout():
    """The action owns the wait. A caller passing its own
    `verify-timeout-minutes` re-creates the per-caller drift this file exists to
    prevent — and would not be re-checked when the shared default moves."""
    overriders = []
    for wf, job_name, job in _caller_jobs():
        for s in job["steps"]:
            if s.get("uses") == USES and "verify-timeout-minutes" in (s.get("with") or {}):
                overriders.append(f"{wf}:{job_name}")
    assert not overriders, (
        "these callers override the shared wait: " + ", ".join(overriders)
    )


# ─────────────────────────────────────────────────────────────────────────────
# R13: ARMING TAKES THE MERGE SLOT — and this action arms for every producer.
#
# `MI-208`. The action wrote the landing declaration (R11) and the arming file
# (R6) and stopped there, so every PR it opened failed `pr-landing-guard` R13 —
# a REQUIRED check — and could never merge. Measured 2026-09-09: 47 open PRs, 46
# automation, PR #11487's guards job reading `PASS 53 · FAIL 1` on this guard.
#
# ⚠️ THE BLAST RADIUS IS WHY THE TEST IS HERE AND NOT IN A CALLER. The open
# queue showed ELEVEN distinct `automation/*` prefixes, which reads like eleven
# bugs; they are `branch-prefix` inputs to this ONE action, which 27 workflows
# call. And the damage was not confined to the queue: an unlanded receipt makes
# `check_digest_liveness` grade `stale`, and that guard is time-based and
# repo-wide, so it then reds EVERY open PR — trading PRs included.
# ─────────────────────────────────────────────────────────────────────────────

SESSION_BOARD = "docs/claude/session-board.json"
CLAIM_SCRIPT = "scripts/ops/claim_merge_slot.py"


def _action_script() -> str:
    with open(ACTION, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    steps = doc["runs"]["steps"]
    return "\n".join(s.get("run", "") for s in steps)


def test_the_action_arms_the_landing_route():
    """A positive control. Every assertion below is of the form 'because it
    arms, it must also X' — so if it ever STOPS arming, those become vacuous
    truths and this file would pass while checking nothing."""
    assert ".github/pr-automerge-requests/" in _action_script(), (
        "the action no longer writes an arming file — the R13 assertions below "
        "have quietly become vacuous; re-derive what this action does.")


def test_the_action_writes_the_merge_slot_claim():
    script = _action_script()
    assert CLAIM_SCRIPT in script, (
        f"the action arms auto-merge but never writes the R13 merge-slot claim. "
        f"Arming IS the merge, so `pr-landing-guard` fails every PR this action "
        f"opens and none of them can ever merge. Call {CLAIM_SCRIPT}.")


def test_the_claim_is_staged_in_the_same_commit_as_the_arming_file():
    """R13 reads the claim out of the branch's OWN DIFF (`_added_or_modified`
    against the base), so a claim written to the working tree but never `git
    add`ed satisfies nothing — and would fail in exactly the way that looks
    like the fix is present."""
    script = _action_script()
    add_block = script.split('git add -- ".github/pr-landing/')[-1].split("git commit")[0]
    assert SESSION_BOARD in add_block, (
        f"{SESSION_BOARD} is not staged alongside the arming file. R13 grades "
        f"the branch's own diff; an unstaged claim is not a claim.")


def test_a_missing_claim_script_refuses_rather_than_opening_a_doomed_pr():
    """The failure mode this whole work item is about: a mechanism reporting
    success for something it did not achieve. If the script is absent the action
    must FAIL, not open a PR that can never merge."""
    script = _action_script()
    assert f"! -f {CLAIM_SCRIPT}" in script, (
        "the action does not check that the claim script exists; without that "
        "check a missing script silently re-creates the landing deadlock.")


def test_the_slot_conflict_is_resolved_not_aborted():
    """R13 has every arming branch rewrite the SAME four lines, and R13 does not
    serialize — so two automation runs in flight collide by construction
    (verified: `CONFLICT (content): Merge conflict in session-board.json`).
    Aborting there would trade the deadlock for a permanent conflict-strand."""
    script = _action_script()
    assert "refreshed_after_slot_conflict" in script, (
        "the stale-branch refresh still aborts on a merge-slot conflict; that "
        "strands the branch permanently on any overlapping pair of runs.")
    assert "--theirs" in script, (
        "a slot conflict must be resolved by taking MAIN's board and "
        "re-asserting our own claim over it, so another branch's claim and any "
        "`active_sessions` edits survive.")


def test_every_caller_checks_out_with_a_pat():
    """A caller that checks out with the default token opens a PR NOTHING CHECKS.

    ⚠️ THIS FAILS SILENTLY AND FOREVER, which is why it belongs beside
    `verify-merged` rather than in a reviewer's head. `commit-to-main` pushes its
    branch with whatever credential the checkout configured, and GitHub does not
    fire workflow triggers for actions taken with the built-in `GITHUB_TOKEN`
    (recursion prevention). So a caller checking out without a PAT opens a PR
    that never receives the `on: pull_request` required checks — and auto-merge
    then waits for checks that will never arrive. The action's own docstring
    states the PAT as a REQUIREMENT of calling it.

    The two failure shapes are different and both are bad: WITHOUT
    `verify-merged` the step exits 0 the moment the PR opens, so the run is
    green and the artifact never lands; WITH it, the job burns its whole merge
    wait and reports a timeout. Neither names the cause.

    MEASURED 2026-09-12: `sunset-pass.yml` was being ported onto
    `commit-to-main` and checked out with a bare `actions/checkout@v4`. The
    existing suite passed on it — every other invariant here is about the
    `uses:` step, and this one is about a step three above it — so the port
    would have swapped a loud weekly failure (a protected-branch push declined
    with GH006, which at least errors) for a silent permanent one.

    ⚠️ IT CHECKS THE BINDING, NOT THE SECRET. A `token:` naming a secret that is
    unset, or one whose PAT lacks the scopes, passes here and fails on the
    runner. That is stated rather than implied: this catches the omission an
    author makes, never a credential problem.
    """
    missing = []
    for wf, job_name, job in _caller_jobs():
        checkouts = [s for s in job["steps"]
                     if str(s.get("uses", "")).startswith("actions/checkout@")]
        if not checkouts:
            # No checkout at all in the job that calls the action — the action
            # could not run. Reported rather than skipped: a silent pass here
            # would be a verdict over an empty population.
            missing.append(f"{wf}:{job_name} (no actions/checkout step at all)")
            continue
        if not any("token" in (s.get("with") or {}) for s in checkouts):
            missing.append(f"{wf}:{job_name}")
    assert not missing, (
        "these callers check out with the default GITHUB_TOKEN, so the PR "
        "`commit-to-main` opens for them never receives the required checks and "
        "auto-merge waits forever: " + ", ".join(missing)
    )


def test_the_caller_population_is_not_empty():
    """A guard with no population is not a clean guard.

    If `USES` is ever renamed, every test above iterates an empty list and the
    whole file goes green while checking nothing — the vacuous-verdict shape
    this repo enforces against elsewhere.
    """
    assert list(_caller_jobs()), (
        f"no workflow calls `{USES}` — either the action was renamed (fix this "
        f"file) or every producer stopped landing rows (a much larger finding). "
        f"Either way the tests above just passed over NOTHING."
    )
