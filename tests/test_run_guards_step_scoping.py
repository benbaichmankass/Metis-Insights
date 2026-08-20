"""Per-step relevance in the guard harness (`scripts/ci/run_guards.py`).

BL-20260809-GUARD-STEP-WHEN-SKIPS-ON-PUSH. `run_guards.py --all` (push /
workflow_dispatch) disables relevance at the GUARD level but leaves per-STEP
`when` clauses evaluated against `changed`, which is empty there — so a
step-gated scan never ran on the events `guards.yml` claimed ran everything.

The obvious fix was to force those steps to run. **Measurement killed it:**
every step carrying a `when` today consumes `{pr_diff}`, and on push that file
is empty, so forcing them makes `check_diagnostic_provenance.py` print
"OK — every scanned diagnostic states what it computed" and exit 0 having
scanned nothing. Substituting the whole-tree `--all` equivalent is no better:
it exits 1 on 52 pre-existing grandfathered sites and would redden `main`.

So the skip is CORRECT and stays. What was wrong is that it was
indistinguishable from an ordinary not-relevant skip. These tests pin the
distinction, because "the skip is deliberate" is only true while something
keeps saying so.
"""
from __future__ import annotations

import importlib.util
import pathlib
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

_spec = importlib.util.spec_from_file_location(
    "run_guards", os.path.join(REPO, "scripts", "ci", "run_guards.py")
)
rg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rg)

CTX = {"base_ref": "main", "event_name": "pull_request",
       "pr_diff": "/dev/null", "changed_files": ""}
NOOP = ["python3", "-c", "pass"]
FAIL = ["python3", "-c", "raise SystemExit(1)"]


def _guard(steps):
    return {"name": "test-guard", "when": None, "steps": steps}


class TestNoDiffToScopeBy:
    """Push / workflow_dispatch: a diff-consuming step must NOT run."""

    def test_gated_step_is_skipped_and_recorded(self):
        unscoped = []
        # FAIL would fail the guard if the step ran; it must not run.
        guard = _guard([{"argv": FAIL, "when": {"globs": ["src/**"]}}])
        reason = rg.run_guard(guard, CTX, [], no_diff_scope=True, unscoped=unscoped)
        assert reason is None, "the gated step ran on an event with no diff"
        assert unscoped == ["test-guard: " + " ".join(FAIL)], (
            "a step skipped for lack of a diff must be RECORDED — an unreported "
            "skip is what made this read as coverage for three months"
        )

    def test_ungated_step_still_runs(self):
        # The escape hatch a guard uses for real push-time coverage.
        unscoped = []
        guard = _guard([FAIL])
        reason = rg.run_guard(guard, CTX, [], no_diff_scope=True, unscoped=unscoped)
        assert reason is not None, "an UNGATED step must run on push"
        assert unscoped == []


class TestDiffScoped:
    """Pull request: ordinary relevance, and no false 'not scanned' report."""

    def test_matching_step_runs(self):
        unscoped = []
        guard = _guard([{"argv": FAIL, "when": {"globs": ["src/**"]}}])
        reason = rg.run_guard(
            guard, CTX, ["src/x.py"], no_diff_scope=False, unscoped=unscoped
        )
        assert reason is not None, "a relevant step must run"
        assert unscoped == []

    def test_non_matching_step_is_skipped_but_not_recorded_as_unscanned(self):
        """The distinction that matters.

        "not relevant to this diff" is a real answer about a real diff. "no
        diff to scope by" is the absence of the question. Collapsing them is
        what hid the gap; a PR-time skip must not inflate the push-time
        not-scanned report.
        """
        unscoped = []
        guard = _guard([{"argv": FAIL, "when": {"globs": ["src/**"]}}])
        reason = rg.run_guard(
            guard, CTX, ["docs/x.md"], no_diff_scope=False, unscoped=unscoped
        )
        assert reason is None
        assert unscoped == []


class TestTheAffectedPopulationIsStated:
    """Every step-gated scan in the registry consumes `{pr_diff}`.

    That is the premise the whole decision rests on: if a future step carries a
    `when` but scans the whole tree, skipping it on push loses real coverage
    for no reason, and the reasoning above stops applying to it. This test
    fails when that premise stops holding, so the next author re-derives the
    decision instead of inheriting it.
    """

    def test_every_step_gated_command_consumes_the_pr_diff(self):
        offenders = []
        for guard in rg.GUARDS:
            for step in guard["steps"]:
                if not isinstance(step, dict) or "when" not in step:
                    continue
                if not any("{pr_diff}" in a for a in step["argv"]):
                    offenders.append(f"{guard['name']}: {' '.join(step['argv'])}")
        assert offenders == [], (
            "a step-gated command that does NOT consume {pr_diff} would be "
            "skipped on push while being perfectly able to run there. Re-read "
            "BL-20260809-GUARD-STEP-WHEN-SKIPS-ON-PUSH and decide deliberately: "
            + "; ".join(offenders)
        )

    def test_the_population_is_small_enough_to_have_been_checked_by_hand(self):
        # Guards against the reasoning silently scaling past what was measured.
        gated = [
            g["name"] for g in rg.GUARDS
            if any(isinstance(s, dict) and "when" in s for s in g["steps"])
        ]
        assert set(gated) == {
            "api-tier-policy-guard",
            "diagnostic-provenance-guard",
            "test-schema-fidelity-guard",
        }, (
            f"the step-gated population changed to {sorted(gated)} — the "
            "push-time coverage decision was measured against exactly three "
            "guards; re-measure before assuming it still holds"
        )


# ---------------------------------------------------------------------------
# Re-derivation for the THIRD member (2026-08-20).
#
# `test-schema-fidelity-guard` joined the step-gated population, so the
# decision above was re-derived rather than inherited — which is exactly what
# this test class exists to force.
#
# It arrived NOT consuming `{pr_diff}`: it resolved its own base with
# `git diff --name-only origin/main...HEAD`. That is worse than the pattern it
# was skipping, twice over. On push HEAD *is* main, so the self-resolved diff
# is empty and the scan reports a clean green over zero files — the same
# "checked nothing" outcome, reached by a mechanism no reader would recognise
# as the documented one. And in a shallow clone `origin/main` does not resolve,
# where it fell back to `--all`, which exits 1 on the 20 pre-existing
# grandfathered files and would redden every PR under a message that says
# nothing about the substitution (sub-class B, implicit input selection).
#
# So the fix was to make it a genuine `{pr_diff}` consumer, not to exempt it:
# the harness generates one diff, every consumer reads that same file, and the
# skip-on-push reasoning above applies to it unchanged and for the same reason.
# The base-resolution path survives for local use with the silent widening
# replaced by a hard error.
#
# The premise the class pins therefore still holds at n=3: every step-gated
# command consumes `{pr_diff}`, and each is skipped on push because an empty
# diff would make it report a green over nothing.


# ---------------------------------------------------------------------------
# The harness must GENERATE the diff it consumes (2026-08-14).
#
# Eight guards take `{pr_diff}` and scan ONLY that file. CI writes it in a
# separate `guards.yml` step and exports GUARDS_PR_DIFF; nothing wrote it
# locally, and the default is a fixed `/tmp/pr.diff` — so a local run rescanned
# whatever STALE diff a previous run had left there and printed "All relevant
# guards passed" over content unrelated to the branch. Measured: three
# consecutive local runs reported diagnostic-provenance-guard PASS on a commit
# where CI failed it, same command, same path.
#
# A stale diff is strictly worse than a missing one: an absent file errors,
# a stale file passes.
# ---------------------------------------------------------------------------

def test_harness_generates_the_pr_diff_when_no_caller_supplied_one() -> None:
    src = pathlib.Path(REPO, "scripts", "ci", "run_guards.py").read_text()
    assert "could not generate the PR diff" in src, (
        "run_guards no longer generates the diff its guards scan; a stale "
        "/tmp/pr.diff will silently produce a false green"
    )
    # The generation must be skipped when a caller (CI) supplies the file,
    # or the harness would clobber the diff the workflow just built.
    assert "GUARDS_PR_DIFF" in src and "explicit_diff" in src, (
        "the caller-supplied-diff escape hatch is gone; CI's own diff would be "
        "overwritten"
    )
    # ...and a failure to produce it must be fatal, never a quiet continue.
    assert "return 2" in src.split("could not generate the PR diff")[1][:400], (
        "failing to build the diff must be a hard error — continuing would scan "
        "a stale file and report a green having checked nothing"
    )


def test_ci_still_supplies_its_own_diff_so_the_two_paths_stay_distinct() -> None:
    """The workflow builds the diff; the harness must not fight it."""
    wf = pathlib.Path(REPO, ".github", "workflows", "guards.yml").read_text()
    assert "GUARDS_PR_DIFF" in wf, (
        "guards.yml no longer exports GUARDS_PR_DIFF, so run_guards would "
        "regenerate the diff CI built — a different range on a merge commit"
    )


# ---------------------------------------------------------------------------
# A guard that RAN is not evidence about work that is not committed.
#
# Every guard is scoped to a commit range — `{pr_diff}` (built from
# `origin/<base>...HEAD`) or its own `--base origin/<base>`. Neither contains
# the working tree. The pre-existing `unchecked` caveat does not cover this:
# it reports guards RELEVANCE skipped, and relevance is a union, so a guard
# already made relevant by a COMMITTED file runs, passes, is counted — and
# never appears in `unchecked` — having scanned a range without your edits.
#
# 2026-08-14: a local run printed "All relevant guards passed" over a truncated
# backlog id sitting in an uncommitted comment; the same commit failed
# artifact-validity-guard in CI minutes later. The sprint log already recorded
# "committing first was necessary and never sufficient"; this is the converse,
# where the commit was skipped and the harness said green regardless.
# ---------------------------------------------------------------------------

def test_a_dirty_tree_caveats_the_green_line() -> None:
    src = pathlib.Path(REPO, "scripts", "ci", "run_guards.py").read_text()
    assert "tree_dirty" in src, (
        "the dirty-worktree caveat is gone; run_guards will again report "
        "'All relevant guards passed' over files no guard scanned"
    )
    head, _, tail = src.partition("caveats = []")
    assert tail, "the caveat block was restructured — re-check this assertion"
    assert "tree_dirty" in tail.split("if caveats:")[0], (
        "tree_dirty is no longer folded into `caveats`, so the green line is "
        "unqualified again"
    )


def test_the_caveat_does_NOT_subtract_the_changed_set() -> None:
    """The subtraction hides exactly the case that bites.

    A file that is BOTH committed-changed and dirty appears in `changed`, so
    subtracting it reports a clean tree while the edits layered on top of the
    commit went unscanned. The first version of this caveat made that mistake
    and stayed silent on the very tree that had produced the false green, so
    the property is pinned rather than left to care.
    """
    src = pathlib.Path(REPO, "scripts", "ci", "run_guards.py").read_text()
    assign = [ln for ln in src.splitlines() if ln.strip().startswith("tree_dirty =")]
    assert assign, "tree_dirty assignment not found"
    joined = " ".join(assign)
    assert "- set(changed)" not in joined, (
        "tree_dirty subtracts the changed set again — a file both committed "
        "and dirty will read as covered while its uncommitted edits are not"
    )
