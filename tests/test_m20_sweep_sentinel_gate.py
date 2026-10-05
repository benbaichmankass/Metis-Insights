"""PI-20261003-ILZMCTFQ-0001: the sweep must not fire on an inherited sentinel."""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "ci"))

from m20_sweep_sentinel_gate import decide  # noqa: E402

WF = REPO / ".github" / "workflows" / "m20-exit-lever-sweep.yml"


def test_inherited_via_merge_is_skipped():
    assert decide("push", "abc", "abc")[0] is False


def test_edited_on_branch_proceeds():
    assert decide("push", "abc", "def")[0] is True


def test_introduced_on_branch_proceeds():
    assert decide("push", "abc", "")[0] is True


def test_missing_on_branch_skips():
    assert decide("push", "", "abc")[0] is False


def test_dispatch_always_proceeds():
    assert decide("workflow_dispatch", "abc", "abc")[0] is True


def test_workflow_jobs_are_gated_on_proceed():
    jobs = yaml.safe_load(WF.read_text())["jobs"]
    assert "proceed" in jobs["plan"]["outputs"]
    for name in ("sweep", "corpus", "research_result"):
        assert "needs.plan.outputs.proceed == 'true'" in jobs[name]["if"], name
        assert "plan" in (jobs[name]["needs"] if isinstance(jobs[name]["needs"], list) else [jobs[name]["needs"]])
