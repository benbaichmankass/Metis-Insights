"""`pytest-run` scope selection (CI-DIET, 2026-10-13).

`scripts/ci/pytest_run_scope.py` lets a DATA-ONLY diff run the test files that
read the data trees instead of the whole suite.  The danger is the one
`tests/test_pytest_run_filter.py` documents four times over: a required check
that reports green having not run what could have caught the change.  These
tests pin the direction of every failure: an unknown path must run MORE tests,
never fewer.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "pytest-run.yml"

_spec = importlib.util.spec_from_file_location(
    "pytest_run_scope", REPO / "scripts" / "ci" / "pytest_run_scope.py"
)
scope = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scope)

RELEVANT = scope.relevance_regex()


def _mode(*files: str) -> str:
    return scope.classify(list(files), RELEVANT)[0]


@pytest.mark.parametrize(
    "files",
    [
        ["research/results/RQ-1.json"],
        ["research/queue/RQ-20261013-01.yaml", ".github/merge-slots/x.json"],
        ["docs/claude/work/checklist/CI-DIET.json"],
        ["docs/claude/work/checklist/_header.json"],
        ["docs/claude/work/PIPELINE.jsonl"],
        ["comms/reports/x.json", ".github/pr-landing/a.json", ".github/pr-automerge-requests/a.txt"],
    ],
)
def test_data_only_diffs_get_the_data_subset(files):
    assert _mode(*files) == "data"


@pytest.mark.parametrize(
    "path",
    [
        # code / config / workflows / tests / scripts always get the full suite
        "src/main.py", "scripts/ops/x.sh", "scripts/ci/x.py", "tests/test_x.py",
        "config/accounts.yaml", ".github/workflows/guards.yml", ".github/actions/x/action.yml",
        "ml/x.py", "deploy/x.service", "requirements.txt", ".claude/skills/manager/SKILL.md",
        # comms/schema is validated by the suite -> NOT data
        "comms/schema/request.schema.json",
        # non-yaml research files and the python under research/ are NOT data
        "research/queue/helper.py", "research/other.py",
        # the docs the suite reads as-committed stay full (test_pytest_run_filter.COVERED)
        "docs/ARCHITECTURE-CANONICAL.md", "docs/CLAUDE-RULES-CANONICAL.md",
        "docs/api-tier-policy.md", "docs/claude/system-actions.md",
        "docs/sprint-logs/S-1.md", "docs/research/exit-refinement-coverage.json",
        "docs/claude/SUNSET-DISPOSITIONS.json", "docs/claude/diag-relay.md",
    ],
)
def test_everything_else_the_suite_reads_still_gets_the_full_suite(path):
    assert _mode(path) == "full"


def test_one_non_data_file_makes_the_whole_diff_full():
    assert _mode("research/results/a.json", "scripts/x.py") == "full"
    assert _mode("docs/claude/work/checklist/A.json", "config/strategies.yaml") == "full"


def test_a_file_the_suite_never_reads_does_not_force_full():
    # README-class files are irrelevant to the suite; with a data file the diff is data.
    assert not re.search(RELEVANT, "README.md")
    assert _mode("research/results/a.json", "README.md") == "data"


def test_the_allowlist_is_explicit_and_pinned():
    assert scope.DATA_ALLOWLIST == (
        r"^docs/claude/work/checklist/",
        r"^docs/claude/work/pipeline/",
        r"^docs/claude/work/PIPELINE\.jsonl$",
        r"^research/queue/[^/]+\.ya?ml$",
        # GH-CAPACITY-PR-CI-FANOUT: parked units; their readers name research/queue
        r"^research/queue/blocked/[^/]+\.ya?ml$",
        r"^research/results/",
        r"^comms/(?!schema/)",
        r"^\.github/pr-landing/",
        r"^\.github/pr-automerge-requests/",
        r"^\.github/merge-slots/",
    ), "widening the data allowlist widens what skips the full suite - state why in the PR"


def test_every_data_tree_has_a_reader_in_the_selected_subset():
    sel = set(scope.select_tests())
    # Known readers of the real trees must be selected.
    for t in (
        "tests/test_pipeline.py",
        "tests/ops/test_merge_json_register.py",
        "tests/test_pytest_run_filter.py",
        "tests/test_pytest_run_scope.py",
    ):
        assert t in sel, f"{t} reads a data tree but is not selected"
    assert "tests/test_main_loop.py" not in sel


def test_selection_is_by_content_not_by_a_hand_list():
    # Every test file mentioning a token is selected (over-select, never under).
    sel = set(scope.select_tests())
    for p in (REPO / "tests").rglob("test_*.py"):
        rel = p.relative_to(REPO).as_posix()
        if rel == "tests/test_main_loop.py":
            continue
        if scope.TEST_TOKENS.search(p.read_text(errors="replace")):
            assert rel in sel


def test_the_workflow_is_wired_and_keeps_its_required_check_name():
    text = WORKFLOW.read_text()
    wf = yaml.safe_load(text)
    # Branch protection requires the context "pytest-run": job id and name must not move.
    assert list(wf["jobs"]) == ["pytest-run"]
    assert "scripts/ci/pytest_run_scope.py" in text
    assert "--no-renames" in text, "a rename OUT of a suite-read tree must be seen"
    # Exactly one 'full' step and one 'data' step, mutually exclusive.
    steps = {s.get("name", ""): s for s in wf["jobs"]["pytest-run"]["steps"]}
    full = steps["Run tests"]["if"]
    data = next(s["if"] for n, s in steps.items() if n.startswith("Run tests (data-only"))
    affected = next(s["if"] for n, s in steps.items() if n.startswith("Run tests (pull_request code diff"))
    assert "mode != 'data'" in full and "mode == 'data'" in data
    # GH-CAPACITY-PR-CI-FANOUT: the affected step is a third, mutually exclusive branch.
    assert "mode != 'affected'" in full and "mode == 'affected'" in affected
    # Only github.event_name decides whether affected mode is even considered.
    assert '--event "${{ github.event_name }}"' in text
    # The first grep -Eq in the file is still the relevance filter (test_pytest_run_filter parses it).
    assert text.index("grep -Eq '") < text.index("python3 scripts/ci/pytest_run_scope.py --event")


def test_untrusted_file_names_never_reach_a_shell_inline():
    text = WORKFLOW.read_text()
    assert "${{ steps.changed.outputs.reason }}" in text  # allowed only as an env value
    for line in text.splitlines():
        if "steps.changed.outputs.reason" in line:
            assert line.strip().startswith("SCOPE_REASON:"), line


def test_suite_runs_in_parallel_without_deselecting_anything():
    """CI-DIET: xdist (-n auto --dist loadfile) cuts the full suite from 33-46 min serial to ~12-14.
    It must stay a PURE scheduling change: no -k / -m / --deselect beyond what the workflow always
    carried, and the dependency must be installed by the workflow."""
    wf = yaml.safe_load(WORKFLOW.read_text())
    runs = [
        s["run"] for s in wf["jobs"]["pytest-run"]["steps"]
        if s.get("name", "").startswith("Run tests")
    ]
    assert len(runs) == 3, "one full-mode, one data-mode and one affected-mode step"
    for run in runs:
        cmd = " ".join(ln for ln in run.splitlines() if not ln.strip().startswith("#"))
        assert "-n auto --dist loadfile" in cmd
        assert "--ignore=tests/test_main_loop.py" in cmd
        for forbidden in ("--deselect", " -k ", " -m ", "--lf", "--collect-only"):
            assert forbidden not in cmd, f"pytest-run must not narrow the suite with {forbidden!r}"
    assert 'pip install "pytest-xdist>=3.5,<4"' in WORKFLOW.read_text()
