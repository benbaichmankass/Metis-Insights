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


# ── same-workflow fires are serialized: one per workflow file per cycle ──────
def _unit_text(uid: str, workflow: str) -> str:
    """A real, power-graded, routable unit from the committed queue (RQ-20260928-016, e35,
    monthly) re-pointed at `workflow`, unstamped and cadence once."""
    import yaml
    d = yaml.safe_load((REPO / "research/queue/RQ-20260928-016.yaml").read_text())
    d.update(id=uid, status="queued", cadence="once", last_dispatched_at=None)
    d.pop("grading", None)
    d["run"] = {"workflow": workflow, "inputs": {"research_unit": uid}}
    return yaml.safe_dump(d, sort_keys=False)


def _fired_cycle(tmp_path, monkeypatch, units, extra_args=()):
    """Run --fire with gh mocked: no pressure, every `gh workflow run` succeeds. Returns the dispatched ids in order."""
    q = tmp_path / "queue"
    q.mkdir()
    for uid, wf in units:
        (q / f"{uid}.yaml").write_text(_unit_text(uid, wf))
    fired = []

    def fake_run(cmd, *a, **k):
        class P:
            returncode = 0
            stdout = ""
            stderr = ""
        if cmd[:3] == ["gh", "workflow", "run"]:
            fired.append(cmd)
        return P()

    monkeypatch.setattr(dq, "gh_runs", lambda status, limit=200: [])
    monkeypatch.setattr(dq, "declared_inputs", lambda *a, **k: {"research_unit", "power_state"})
    from scripts.research import script_run
    monkeypatch.setattr(script_run, "plan", lambda *a, **k: type("P", (), {"ok": True, "errors": []})())
    monkeypatch.setattr(dq.subprocess, "run", fake_run)
    rc = dq.main(["--queue-dir", str(q), "--fire", "--ref", "main", "--json", "--max-research-inflight", "9",
                  *extra_args])
    assert rc == 0
    stamped = sorted(p.stem for p in q.glob("*.yaml") if "last_dispatched_at: null" not in p.read_text())
    return fired, stamped


def test_three_e35_units_due_in_one_cycle_fire_one_and_defer_two(tmp_path, monkeypatch, capsys):
    """PLANTED (2026-09-29 00:53Z): three e35-bracket-sweep fires in one cycle -- the
    concurrency group cancelled the middle one and the survivors' corpus PRs collided."""
    units = [("RQ-20300101-001", "e35-bracket-sweep.yml"), ("RQ-20300101-002", "e35-bracket-sweep.yml"),
             ("RQ-20300101-003", "e35-bracket-sweep.yml"), ("RQ-20300101-004", "m20-exit-lever-sweep.yml")]
    fired, stamped = _fired_cycle(tmp_path, monkeypatch, units)
    assert len(fired) == 2, fired                       # one e35 + one m20
    assert stamped == ["RQ-20300101-001", "RQ-20300101-004"]
    out = capsys.readouterr().out
    assert out.count("already fired 1 unit(s) this cycle") == 2, out


def test_the_token_free_runner_is_exempt_from_serialization(tmp_path, monkeypatch):
    units = [(f"RQ-20300101-00{i}", "research-script-run.yml") for i in range(1, 4)]
    fired, stamped = _fired_cycle(tmp_path, monkeypatch, units)
    assert len(fired) == 3 and len(stamped) == 3


def test_the_per_workflow_cap_is_a_flag(tmp_path, monkeypatch):
    units = [("RQ-20300101-001", "e35-bracket-sweep.yml"), ("RQ-20300101-002", "e35-bracket-sweep.yml")]
    fired, stamped = _fired_cycle(tmp_path, monkeypatch, units, extra_args=("--max-fires-per-workflow", "2"))
    assert len(fired) == 2 and len(stamped) == 2
