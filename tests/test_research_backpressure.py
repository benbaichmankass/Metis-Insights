"""Research never starves a safety action (manager review, 2026-09-29 01:27Z).

MEASURED before the change: 67 runs queued repo-wide and 12 research compute
runs in progress after one dispatcher cycle fanned the whole queue out, with
every system-action waiting in the same pool. Two caps, both deferrals (never
failures, never stamps): at most 3 research compute runs queued+in progress,
and no fire at all while > 10 runs are queued repo-wide. Plus: the runner's
results land ONE PR per dispatcher cycle (tests/test_research_script_run.py).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.research import dispatch_queue as dq  # noqa: E402

RESEARCH = {"research-script-run.yml": "research-script-run", "e35-bracket-sweep.yml": "e35-bracket-sweep"}


def _runs(names):
    return [{"databaseId": i, "workflowName": n, "status": "x", "createdAt": ""} for i, n in enumerate(names)]


def test_inflight_counts_only_research_workflows_across_queued_and_in_progress():
    r = dq.backpressure(RESEARCH, max_inflight=3, max_queued=10, runs_by_status={
        "queued": _runs(["pytest-run", "research-script-run"]),
        "in_progress": _runs(["e35-bracket-sweep", "session-reaper"])})
    assert r["inflight_research"] == 2 and r["repo_queued"] == 2 and r["block"] is None


def test_the_research_cap_defers_and_says_so():
    r = dq.backpressure(RESEARCH, max_inflight=3, max_queued=10, runs_by_status={
        "queued": [], "in_progress": _runs(["research-script-run"] * 3)})
    assert r["block"] and "3 research compute" in r["block"]


def test_a_saturated_repo_queue_defers_even_with_no_research_in_flight():
    r = dq.backpressure(RESEARCH, max_inflight=3, max_queued=10, runs_by_status={
        "queued": _runs(["pytest-run"] * 11), "in_progress": []})
    assert r["block"] and "queued repo-wide" in r["block"]


def test_a_count_we_could_not_take_is_a_block_not_a_zero():
    r = dq.backpressure(RESEARCH, max_inflight=3, max_queued=10,
                        runs_by_status={"queued": None, "in_progress": []})
    assert r["block"] and "could not count" in r["block"] and r["inflight_research"] is None


def test_the_measured_saturation_of_2026_09_29_would_have_been_deferred():
    """The exact shape the manager measured at 01:27Z: 67 queued, 12 research in flight."""
    inprog = _runs(["research-script-run"] * 6 + ["research-harness-dispatch"] * 2 + ["m20-exit-lever-sweep"] * 2
                   + ["e35-bracket-sweep", "macro-valuation-backfill", "session-reaper"])
    names = dict(RESEARCH, **{"research-harness-dispatch.yml": "research-harness-dispatch",
                              "m20-exit-lever-sweep.yml": "m20-exit-lever-sweep",
                              "macro-valuation-backfill.yml": "macro-valuation-backfill"})
    r = dq.backpressure(names, max_inflight=3, max_queued=10,
                        runs_by_status={"queued": _runs(["pytest-run"] * 67), "in_progress": inprog})
    assert r["inflight_research"] == 12 and r["repo_queued"] == 67 and r["block"]


def test_research_workflow_names_resolve_from_the_committed_queue():
    from scripts.research.research_queue import load_queue
    jobs, err = load_queue(REPO / "research" / "queue")
    assert err is None
    names = dq.research_workflow_names(jobs)
    assert names["research-script-run.yml"] == "research-script-run"
    assert all(v for v in names.values())
    # a session-bound note is never a workflow
    assert not any(" " in k for k in names)


def test_a_fired_cycle_under_pressure_dispatches_nothing_and_stamps_nothing(tmp_path, monkeypatch):
    """PLANTED: with 3 research runs in flight, --fire must call gh workflow run ZERO times."""
    unit = (REPO / "research/queue/RQ-20260928-012.yaml").read_text().replace("status: done", "status: queued")
    unit = unit.replace("last_dispatched_at: '2026-09-28T21:20:36+00:00'", "last_dispatched_at: null")
    q = tmp_path / "queue"
    q.mkdir()
    (q / "RQ-20260928-012.yaml").write_text(unit)
    calls = []

    def fake_run(cmd, *a, **k):
        calls.append(cmd)
        raise AssertionError(f"gh must not be called under backpressure: {cmd[:3]}")

    monkeypatch.setattr(dq, "gh_runs", lambda status, limit=200: _runs(["research-script-run"] * 3) if status == "in_progress" else [])
    monkeypatch.setattr(dq.subprocess, "run", fake_run)
    rc = dq.main(["--queue-dir", str(q), "--fire", "--ref", "main", "--json"])
    assert rc == 0 and calls == []
    assert "last_dispatched_at: null" in (q / "RQ-20260928-012.yaml").read_text()


def test_self_tests_pass():
    for script in ("scripts/research/collect_runner_results.py",):
        proc = subprocess.run([sys.executable, str(REPO / script), "--self-test"], capture_output=True, text=True, timeout=300)
        assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("flag", ["--max-research-inflight", "--max-repo-queued"])
def test_the_dispatcher_workflow_passes_both_caps(flag):
    assert flag in (REPO / ".github/workflows/research-queue-dispatch.yml").read_text()


# ── stuck runs are not load (measured 2026-09-29 06:56Z, first fired cycle after #13907) ──
def test_stuck_runs_older_than_a_day_count_against_neither_cap():
    """All 36 queued runs on 2026-09-29 06:56Z were created on 2026-05-15 (issue-triggered
    vm-* jobs no runner ever served), and the 3 'research runs in flight' were three
    trainer-vm-diag dispatches from the same morning. They deferred every fire and would
    have done so on every cycle until someone cancelled them by hand."""
    from datetime import datetime, timezone
    now = datetime(2026, 9, 29, 6, 56, tzinfo=timezone.utc)
    zombies = [{"databaseId": i, "workflowName": "vm-web-api-recover", "status": "queued",
                "createdAt": "2026-05-15T07:45:37Z"} for i in range(33)]
    zombies += [{"databaseId": 100 + i, "workflowName": "trainer-vm-diag", "status": "queued",
                 "createdAt": "2026-05-15T07:5%d:00Z" % i} for i in range(3)]
    names = {"trainer-vm-diag.yml": "trainer-vm-diag", "e35-bracket-sweep.yml": "e35-bracket-sweep"}
    r = dq.backpressure(names, max_inflight=3, max_queued=10, now=now,
                        runs_by_status={"queued": zombies, "in_progress": []})
    assert r["block"] is None, r
    assert r["repo_queued"] == 0 and r["inflight_research"] == 0 and r["stale_ignored"] == 36
    assert "36 stuck run(s)" in r["detail"]
    # fresh runs still count, exactly as before
    fresh = [{"databaseId": 200 + i, "workflowName": "Guards", "status": "queued",
              "createdAt": "2026-09-29T06:50:00Z"} for i in range(11)]
    r2 = dq.backpressure(names, max_inflight=3, max_queued=10, now=now,
                         runs_by_status={"queued": zombies + fresh, "in_progress": []})
    assert r2["block"] and r2["repo_queued"] == 11 and r2["stale_ignored"] == 36
    live = [{"databaseId": 300 + i, "workflowName": "e35-bracket-sweep", "status": "in_progress",
             "createdAt": "2026-09-29T06:00:00Z"} for i in range(3)]
    r3 = dq.backpressure(names, max_inflight=3, max_queued=10, now=now,
                         runs_by_status={"queued": zombies, "in_progress": live})
    assert r3["block"] and r3["inflight_research"] == 3


def test_an_unreadable_created_at_counts_as_fresh():
    from datetime import datetime, timezone
    now = datetime(2026, 9, 29, 6, 56, tzinfo=timezone.utc)
    rows = [{"databaseId": 1, "workflowName": "e35-bracket-sweep", "status": "queued", "createdAt": "garbage"},
            {"databaseId": 2, "workflowName": "e35-bracket-sweep", "status": "queued"},
            {"databaseId": 3, "workflowName": "e35-bracket-sweep", "status": "queued", "createdAt": "2026-09-29T06:00:00Z"}]
    r = dq.backpressure({"e.yml": "e35-bracket-sweep"}, max_inflight=3, max_queued=10, now=now,
                        runs_by_status={"queued": rows, "in_progress": []})
    assert r["inflight_research"] == 3 and r["stale_ignored"] == 0 and r["block"]


def test_the_stale_window_is_a_flag_the_workflow_can_pass():
    import subprocess
    import sys
    out = subprocess.run([sys.executable, "scripts/research/dispatch_queue.py", "--help"],
                         capture_output=True, text=True, cwd=str(REPO)).stdout
    assert "--pressure-stale-hours" in out
