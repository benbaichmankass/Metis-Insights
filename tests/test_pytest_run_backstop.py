"""Post-merge backstop for `pytest-run` (GH-CAPACITY-PR-CI-FANOUT, manager 2026-10-15).

PRs run only the import-graph-affected tests, so the FULL suite on main must
grade every code change within a bounded time.  These tests pin the schedule
and the decision: skip a tick only when HEAD's code is provably already graded
(or a full run on HEAD is in flight); in every other case -- including "could
not look" -- run the full suite.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "pytest-run.yml"
ALERT = REPO / ".github" / "workflows" / "claude-run-failure-alert.yml"

_spec = importlib.util.spec_from_file_location("pytest_run_backstop", REPO / "scripts" / "ci" / "pytest_run_backstop.py")
bs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bs)

HEAD = "h" * 40
OLD = "a" * 40


def _jobs(full_conclusion=None, full_status="completed"):
    steps = [{"name": "Checkout", "status": "completed", "conclusion": "success"}]
    if full_conclusion is not None or full_status != "completed":
        steps.append({"name": "Run tests", "status": full_status, "conclusion": full_conclusion})
    return {"jobs": [{"steps": steps}]}


def _run(rid, sha, event="push", status="completed"):
    return {"id": rid, "head_sha": sha, "event": event, "status": status}


def _neutral(result):
    return lambda base, head: (result, "planted")


# --- the decision ---------------------------------------------------------------

def test_completed_full_run_on_head_skips():
    run, why = bs.decide(HEAD, [_run(1, HEAD)], lambda rid: _jobs("success"), _neutral(True))
    assert not run and why.startswith("covered")


def test_red_full_run_on_head_counts_as_graded():
    # it already paged through claude-run-failure-alert; re-running adds nothing
    run, _ = bs.decide(HEAD, [_run(1, HEAD)], lambda rid: _jobs("failure"), _neutral(True))
    assert not run


def test_full_run_on_an_ancestor_with_only_data_since_skips():
    run, why = bs.decide(HEAD, [_run(1, OLD)], lambda rid: _jobs("success"), _neutral(True))
    assert not run and "covered" in why


def test_code_since_the_last_full_run_runs_the_suite():
    run, why = bs.decide(HEAD, [_run(1, OLD)], lambda rid: _jobs("success"), _neutral(False))
    assert run and why.startswith("run")


def test_data_only_or_affected_runs_do_not_count_as_full():
    # a run whose full step was SKIPPED (data / affected / short-circuit) graded nothing
    runs = [_run(2, HEAD), _run(1, OLD)]
    jobs = {2: _jobs("skipped"), 1: _jobs("success")}
    run, _ = bs.decide(HEAD, runs, jobs.__getitem__, lambda b, h: (b == HEAD, "x"))
    assert run  # falls through to run 1 on OLD, and OLD..HEAD has code


def test_cancelled_full_step_does_not_count():
    run, _ = bs.decide(HEAD, [_run(1, HEAD)], lambda rid: _jobs("cancelled"), _neutral(True))
    assert run


def test_no_full_run_in_the_window_runs_the_suite():
    run, why = bs.decide(HEAD, [], lambda rid: {}, _neutral(True))
    assert run and "no completed full-suite run" in why


@pytest.mark.parametrize("event", ["schedule", "workflow_dispatch"])
def test_in_flight_full_by_construction_run_on_head_skips(event):
    run, why = bs.decide(HEAD, [_run(1, HEAD, event=event, status="queued")], lambda rid: {}, _neutral(False))
    assert not run and why.startswith("in_flight")


def test_in_flight_push_run_counts_only_once_its_full_step_started():
    queued = bs.decide(HEAD, [_run(1, HEAD, status="in_progress")], lambda rid: _jobs(None, "queued"), _neutral(False))
    started = bs.decide(HEAD, [_run(1, HEAD, status="in_progress")], lambda rid: _jobs(None, "in_progress"), _neutral(False))
    assert queued[0] is True  # may still turn out data-only: do not trust it
    assert started[0] is False


def test_could_not_look_runs_the_suite(monkeypatch, capsys):
    def boom(path):
        raise OSError("api down")

    monkeypatch.setattr(bs, "_api", boom)
    bs.main(["--head", HEAD])
    assert "run=true" in capsys.readouterr().out


# --- the range classifier, against real git ----------------------------------

def _git_repo(tmp_path: Path):
    def git(*a):
        return subprocess.check_output(["git", *a], text=True, cwd=tmp_path)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    return git


def test_range_with_only_data_files_is_neutral(tmp_path):
    git = _git_repo(tmp_path)
    base = git("rev-parse", "HEAD").strip()
    (tmp_path / "research" / "results").mkdir(parents=True)
    (tmp_path / "research" / "results" / "r.json").write_text("{}")
    (tmp_path / "README.md").write_text("hi")
    git("add", "-A")
    git("commit", "-qm", "data")
    ok, _ = bs.range_is_suite_neutral(base, git("rev-parse", "HEAD").strip(), git)
    assert ok


def test_range_with_code_is_not_neutral(tmp_path):
    git = _git_repo(tmp_path)
    base = git("rev-parse", "HEAD").strip()
    (tmp_path / "src" / "a.py").write_text("x = 2\n")
    git("commit", "-qam", "code")
    ok, why = bs.range_is_suite_neutral(base, git("rev-parse", "HEAD").strip(), git)
    assert not ok and "code changed" in why


def test_non_ancestor_is_not_neutral(tmp_path):
    git = _git_repo(tmp_path)
    git("checkout", "-qb", "side")
    (tmp_path / "src" / "a.py").write_text("x = 3\n")
    git("commit", "-qam", "side")
    side = git("rev-parse", "HEAD").strip()
    git("checkout", "-q", "main")
    ok, why = bs.range_is_suite_neutral(side, git("rev-parse", "HEAD").strip(), git)
    assert not ok and "not an ancestor" in why


# --- the wiring ---------------------------------------------------------------

def _wf():
    text = WORKFLOW.read_text()
    return text, yaml.safe_load(text)


def test_schedule_is_every_30_minutes():
    _, wf = _wf()
    on = wf.get("on", wf.get(True))
    crons = [c["cron"] for c in on["schedule"]]
    assert crons == ["13,43 * * * *"], "the stated bound (~30 min + run time) depends on this cadence"


def test_schedule_never_cancels_and_shares_a_constant_group():
    _, wf = _wf()
    cc = wf["concurrency"]
    assert "github.event_name != 'schedule'" in cc["cancel-in-progress"]
    assert "github.event_name != 'push'" in cc["cancel-in-progress"]
    # schedule runs have no PR number: the group collapses to pytest-run-schedule-refs/heads/main
    assert cc["group"].startswith("pytest-run-${{ github.event_name }}-")


def test_schedule_branch_calls_the_backstop_and_the_full_step_is_the_one_it_checks():
    text, wf = _wf()
    assert "scripts/ci/pytest_run_backstop.py --head" in text
    names = [s.get("name") for s in wf["jobs"]["pytest-run"]["steps"]]
    assert bs.FULL_STEP in names, "the backstop keys on this exact step name"
    assert wf["permissions"].get("actions") == "read"


def test_backstop_failures_page_through_the_alert_path():
    alert = yaml.safe_load(ALERT.read_text())
    on = alert.get("on", alert.get(True))
    assert "pytest-run" in on["workflow_run"]["workflows"]
    cond = re.sub(r"\s+", " ", alert["jobs"]["alert"]["if"])
    assert "github.event.workflow_run.event == 'schedule'" in cond
    assert "github.event.workflow_run.event == 'push'" in cond
    assert "github.event.workflow_run.head_branch == 'main'" in cond
