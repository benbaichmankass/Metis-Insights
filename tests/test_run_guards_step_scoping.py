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
        assert set(gated) == {"api-tier-policy-guard", "diagnostic-provenance-guard"}, (
            f"the step-gated population changed to {sorted(gated)} — the "
            "push-time coverage decision was measured against exactly two "
            "guards; re-measure before assuming it still holds"
        )
