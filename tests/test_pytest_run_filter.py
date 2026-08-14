"""`pytest-run`'s short-circuit must not skip a tree the suite asserts over.

`BL-20260813-PYTEST-RUN-SHORTCIRCUITS-SO-MAIN-MERGES-UNVERIFIED`.

pytest-run skips the pip-install + test steps when a PR's diff touches nothing
"relevant", to save CI minutes (MB-20260706-CI-MINUTES). The saving is real. The
hazard is that a skipped run reports the SAME green tick as an executed one, so
at the merge button they are indistinguishable — "green is not evidence".

That makes the filter's completeness load-bearing, and it has now failed THREE
times, each time by omitting a tree the suite reads:

  1. config/            BL-20260707-FASTCI-CONFIG-DRIFT — #5850 and #5851 both
                        merged green while reddening main's full suite.
  2. .github/workflows/ same fix.
  3. scripts/           2026-08-13 — PR #8994 changed ONLY
                        scripts/ops/purge_vm_runner.sh, short-circuited to a
                        NINE-SECOND green pytest-run (a real run is 7-9 min over
                        ~10,600 tests) and merged to main having executed no
                        tests. deploy/ and comms/ were open the same way.

Enumerating the trees by hand is what keeps failing, so this test asserts the
filter against the directories the tests demonstrably read. A new tree of
assertions must either be covered or be excluded ON PURPOSE, here, with a
reason.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "pytest-run.yml"


def _filter_regex() -> str:
    """The `grep -Eq '...'` pattern that decides relevance."""
    text = WORKFLOW.read_text()
    m = re.search(r"grep -Eq '([^']+)'", text)
    assert m, "could not find the relevance grep in pytest-run.yml"
    return m.group(1)


def _matches(path: str) -> bool:
    return bool(re.search(_filter_regex(), path))


# Trees the suite ASSERTS OVER using non-.py files. A change to any of these can
# redden the suite, so a diff touching them must not short-circuit.
COVERED = {
    "scripts/ops/purge_vm_runner.sh":
        "tests/ops/test_system_actions_workflow.py reads scripts/ops/*.sh (OPS_DIR)",
    "deploy/ict-trader-live.service":
        "tests/test_s012_service_consolidation.py asserts the deploy/*.service set",
    "comms/schema/health_review_response.template.json":
        "the suite validates against the comms/ schema templates",
    "config/strategies.yaml":
        "test_strategy_execution_gate reads config/strategies.yaml",
    ".github/workflows/system-actions.yml":
        "test_system_actions_workflow reads the workflow itself",
    "docs/research/exit-refinement-coverage.json":
        "test_exit_head_per_leg reads THE REAL matrix (not a fixture); PR #9208 "
        "changed only this file, short-circuited to a TEN-SECOND green "
        "pytest-run, merged, and left main red",
    "src/runtime/order_monitor.py": "python",
    "requirements.txt": "dependency pin",
}

# Excluded ON PURPOSE — large, change on nearly every PR, and their assertions
# belong to the separate `guards` job, which does NOT short-circuit.
DELIBERATELY_EXCLUDED = {
    # VERIFIED 2026-08-14, not assumed: every test that touches this path writes
    # it under a `tmp_path` fixture (test_check_allow_degraded,
    # test_check_backlog_refs, test_check_allow_degraded) or only names it in a
    # docstring — none reads the committed file. So the "guards owns it"
    # premise genuinely holds HERE. It did NOT hold for the exit-coverage
    # matrix, which is why that file moved to COVERED above; the premise is
    # per-file and re-checking it is the point of this table.
    "docs/claude/health-review-backlog.json": "guards job owns doc coherence",
    "data/some_fixture.csv": "bulk data, not asserted structurally by pytest",
    # The rest of docs/research/ stays excluded: the matrix is the one file
    # there the suite reads as-committed, and widening to the tree would pull in
    # every research memo for no assertion.
    "docs/research/exit-refinement-notes.md":
        "prose; no suite assertion reads it",
}


@pytest.mark.parametrize("path,why", sorted(COVERED.items()))
def test_filter_covers_every_tree_the_suite_asserts_over(path, why):
    assert _matches(path), (
        f"pytest-run would SHORT-CIRCUIT on a diff touching {path!r} — but {why}. "
        "A green tick from a run that executed nothing is indistinguishable from "
        "a real pass at the merge button. Add the tree to the grep in "
        ".github/workflows/pytest-run.yml."
    )


@pytest.mark.parametrize("path,why", sorted(DELIBERATELY_EXCLUDED.items()))
def test_deliberate_exclusions_stay_excluded(path, why):
    """Pins the trade-off so it stays a DECISION.

    If someone widens the filter to these, CI minutes regress sharply and the
    short-circuit stops paying for itself. Failing here means: state the new
    reasoning, don't drift into it.
    """
    assert not _matches(path), (
        f"{path!r} is now matched by the pytest-run filter, but it was excluded "
        f"deliberately ({why}). Widening here costs CI minutes on nearly every "
        "PR — if that is intended, update this test with the new reasoning."
    )


def test_the_exact_regression_case_would_now_run():
    """PR #8994's real diff — the one that merged on a 9-second green."""
    assert _matches("scripts/ops/purge_vm_runner.sh")


def test_short_circuit_is_still_possible_at_all():
    """Negative control: if EVERYTHING matched, this test file would pass while
    the CI-minutes saving had been silently destroyed. A guard that cannot fail
    proves nothing."""
    assert not _matches("README.md"), (
        "nothing short-circuits any more — the filter has been widened to "
        "everything, so MB-20260706-CI-MINUTES's saving is gone"
    )


def test_workflow_still_parses():
    yaml.safe_load(WORKFLOW.read_text())
