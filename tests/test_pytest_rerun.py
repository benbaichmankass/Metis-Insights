"""AUTO-PYTEST-RERUN: the decision reruns infrastructure deaths once and never a real failure."""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pytest_rerun", REPO / "scripts/ci/pytest_rerun.py")
pr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr)

SHA = "a" * 40
T = pr.timeout_minutes()
NOW = datetime.now(timezone.utc)          # relative times only: no date bombs


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def run(**kw):
    base = {"run_attempt": 1, "conclusion": "cancelled", "head_sha": SHA}
    base.update(kw)
    return base


def job(minutes, tests=None, conclusion="cancelled", steps=None):
    start = NOW - timedelta(minutes=minutes)
    st = steps if steps is not None else ([{"name": "Run tests", "conclusion": tests}] if tests else [])
    return [{"name": "pytest-run", "conclusion": conclusion, "started_at": iso(start),
             "completed_at": iso(NOW), "steps": st}]


def dec(r=None, j=None, head=SHA, seen=False):
    return pr.decide(r or run(), j, head, seen, T)


def test_timeout_is_read_from_the_workflow_not_hardcoded():
    assert T >= 30 and f"timeout-minutes: {T}" in (REPO / ".github/workflows/pytest-run.yml").read_text()


def test_cancelled_at_the_timeout_reruns():
    ok, why = dec(j=job(T + 0.5, "cancelled"))
    assert ok and "timeout" in why


def test_cancelled_early_is_a_supersede_or_manual_cancel_and_not_rerun():
    assert not dec(j=job(5, "cancelled"))[0]


def test_real_test_failure_is_never_rerun():
    ok, why = dec(r=run(conclusion="failure"), j=job(T, "failure", conclusion="failure"))
    assert not ok and "real failure" in why


def test_failure_even_after_a_long_run_stays_failure():
    assert not dec(r=run(conclusion="failure"), j=job(T + 5, "failure", "failure"))[0]


def test_died_before_tests_ran_reruns():
    steps = [{"name": "Checkout", "conclusion": "success"},
             {"name": "Install runtime + test dependencies", "conclusion": "failure"},
             {"name": "Run tests", "conclusion": "skipped"}]
    ok, why = dec(r=run(conclusion="failure"), j=job(2, conclusion="failure", steps=steps))
    assert ok and "Install runtime" in why


def test_run_tests_absent_and_job_failed_reruns():
    steps = [{"name": "Checkout", "conclusion": "failure"}]
    assert dec(r=run(conclusion="failure"), j=job(1, conclusion="failure", steps=steps))[0]


def test_only_attempt_one_and_only_once_per_sha():
    j = job(T + 1, "cancelled")
    assert not dec(r=run(run_attempt=2), j=j)[0]
    assert not dec(j=j, seen=True)[0]


def test_superseded_head_or_unknown_head_is_not_rerun():
    j = job(T + 1, "cancelled")
    assert not dec(j=j, head="b" * 40)[0]
    assert not dec(j=j, head=None)[0]


def test_success_and_unknown_shapes_are_not_rerun():
    assert not dec(r=run(conclusion="success"), j=job(T, "success", "success"))[0]
    assert not dec(j=[])[0]
    assert not dec(j=job(T + 1, "success", conclusion="failure"))[0]   # post-step failure, tests passed


def test_cancelled_with_unreadable_duration_is_not_rerun():
    j = [{"name": "pytest-run", "conclusion": "cancelled", "started_at": None, "completed_at": None,
          "steps": [{"name": "Run tests", "conclusion": "cancelled"}]}]
    assert not dec(j=j)[0]


def test_workflow_file_is_wired_safely():
    wf = (REPO / ".github/workflows/pytest-rerun.yml").read_text()
    assert "workflow_run" in wf and "ref: main" in wf and "actions: write" in wf
    # No untrusted name in the shell: a branch name is attacker-chosen. It IS allowed as the
    # concurrency-group key (GH-CAPACITY-INFRA-FIRST, 2026-10-15) -- that is an expression GitHub
    # evaluates, never shell text -- so the check is on every step's `run:` and `env:`, not the file.
    import yaml
    doc = yaml.safe_load(wf)
    for job in doc["jobs"].values():
        for step in job.get("steps", []):
            assert "head_branch" not in str(step.get("run", "")), step.get("name")
            assert "head_branch" not in str(step.get("env", {})), step.get("name")
    assert "github.event.workflow_run.head_branch" in str(doc["concurrency"]["group"])
    assert doc["concurrency"]["cancel-in-progress"] is True
