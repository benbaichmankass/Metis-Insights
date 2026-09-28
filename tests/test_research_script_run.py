"""The token-free research runner: allowlist, inputs, and the record it derives.

The runner (`scripts/research/script_run.py`) executes a command it reads from a
queue unit's YAML. Its safety property is that the set of things it will run is
bounded STATICALLY — by script family, argv shape and the workflow's own input
surface — so the tests here plant each escape and assert it is refused, and
plant a green-but-ungraded run and assert it is NOT landed as `measured`.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.ci import check_research_script_run as guard  # noqa: E402
from scripts.research import script_run  # noqa: E402

WF = REPO / ".github" / "workflows" / "research-script-run.yml"

# collapsed-state: not_applicable — THIS FILE BRANCHES ON NO POWER STATE AT
# ALL. `power_state` appears here only as the name of the workflow's
# pass-through input, and the `not_applicable` it asserts is a member of the
# research-result VERDICTS set, not a power state (same token collision
# scripts/research/research_result.py documents on its own annotation).


def test_self_tests_pass():
    for script in ("scripts/research/script_run.py", "scripts/ci/check_research_script_run.py"):
        proc = subprocess.run([sys.executable, str(REPO / script), "--self-test"],
                              capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_workflow_takes_only_the_unit_id_and_the_power_label():
    wf = yaml.safe_load(WF.read_text())
    trig = wf.get("on", wf.get(True))
    assert set(trig) == {"workflow_dispatch"}
    assert set(trig["workflow_dispatch"]["inputs"]) == {"research_unit", "power_state"}
    assert trig["workflow_dispatch"]["inputs"]["research_unit"]["required"] is True


def test_the_run_job_holds_no_secret_and_persists_no_credential():
    wf = yaml.safe_load(WF.read_text())
    run = wf["jobs"]["run"]
    text = yaml.safe_dump(run)
    assert "BRANCH_PROTECTION_TOKEN" not in text
    checkout = [s for s in run["steps"] if str(s.get("uses", "")).startswith("actions/checkout")]
    assert checkout and all(s["with"]["persist-credentials"] is False for s in checkout)


def test_the_land_job_uses_the_shared_landing_actions():
    """Outputs through commit-to-main, the record through research-result —
    the two owners of "how a workflow gets a file onto protected main"."""
    wf = yaml.safe_load(WF.read_text())
    uses = [s.get("uses") for s in wf["jobs"]["land"]["steps"]]
    assert "./.github/actions/commit-to-main" in uses
    assert "./.github/actions/research-result" in uses
    c2m = [s for s in wf["jobs"]["land"]["steps"] if s.get("uses") == "./.github/actions/commit-to-main"][0]
    assert str(c2m["with"]["verify-merged"]).lower() == "true"


def test_every_with_key_passed_to_research_result_is_declared():
    action = yaml.safe_load((REPO / ".github/actions/research-result/action.yml").read_text())
    wf = yaml.safe_load(WF.read_text())
    step = [s for s in wf["jobs"]["land"]["steps"] if s.get("uses") == "./.github/actions/research-result"][0]
    undeclared = set(step["with"]) - set(action["inputs"])
    assert not undeclared, undeclared
    # and every output the workflow reads from --derive-record is one it emits
    derived = {k for k in step["with"] if "steps.derive.outputs." in str(step["with"][k])}
    # cheap structural check: the keys named in the YAML exist in derive_record's dict
    src = (REPO / "scripts/research/script_run.py").read_text()
    for key in derived:
        out_key = str(step["with"][key]).split("steps.derive.outputs.")[1].rstrip(" }")
        assert f'"{out_key}"' in src, f"{out_key} is read by the workflow but never emitted"


@pytest.mark.parametrize("argv", [
    ["bash", "-c", "echo"],
    ["python3", "-c", "print(1)"],
    ["python3", "-m", "scripts.research.prop_ev_sim"],
    ["python3", "scripts/ops/pipeline.py"],
    ["python3", "src/main.py"],
    ["python3", "scripts/research/../ops/pipeline.py"],
    ["python3", "scripts/research/prop_ev_sim.py", "--out", "../../x"],
    ["python3", "scripts/research/does_not_exist_xyz.py"],
])
def test_escapes_are_refused_statically(tmp_path, argv):
    qdir = tmp_path
    (qdir / "RQ-20260101-001.yaml").write_text(yaml.safe_dump({
        "id": "RQ-20260101-001", "title": "t", "question": "q", "status": "queued", "cadence": "once",
        "run": {"workflow": script_run.WORKFLOW, "command": argv,
                "inputs": {"research_unit": "RQ-20260101-001"}},
        "decision_rule": {"id": "R", "registered_at": "2026-01-01"},
        "lands": {"store": "research/results/"}}))
    p = script_run.plan("RQ-20260101-001", run_id="t", queue_dir=qdir, repo=REPO)
    assert not p.ok
    assert not p.commands or p.errors


def test_the_real_research_family_is_admitted(tmp_path):
    (tmp_path / "RQ-20260101-001.yaml").write_text(yaml.safe_dump({
        "id": "RQ-20260101-001", "title": "t", "question": "q", "status": "queued", "cadence": "once",
        "run": {"workflow": script_run.WORKFLOW,
                "command": ["python3", "scripts/research/prop_ev_sim.py", "--self-test"],
                "inputs": {"research_unit": "RQ-20260101-001"}},
        "decision_rule": {"id": "R", "registered_at": "2026-01-01"},
        "lands": {"store": "research/results/"}}))
    p = script_run.plan("RQ-20260101-001", run_id="t", queue_dir=tmp_path, repo=REPO)
    assert p.ok, p.errors


def test_every_committed_unit_targeting_the_runner_plans_cleanly():
    """The guard's R1 over the live queue — the same check CI runs."""
    problems = [p for p in guard.check() if p.startswith("R1")]
    assert problems == [], problems


def test_a_green_run_without_a_verdict_is_not_measured(tmp_path):
    (tmp_path / "scripts" / "research").mkdir(parents=True)
    (tmp_path / "scripts" / "research" / "s.py").write_text("print('ok')\n")
    q = tmp_path / "q"
    q.mkdir()
    (q / "RQ-20260101-001.yaml").write_text(yaml.safe_dump({
        "id": "RQ-20260101-001", "title": "t", "question": "q", "status": "queued", "cadence": "once",
        "run": {"workflow": script_run.WORKFLOW, "command": ["python3", "scripts/research/s.py"],
                "inputs": {"research_unit": "RQ-20260101-001"}},
        "decision_rule": {"id": "R", "registered_at": "2026-01-01"},
        "lands": {"store": "research/results/"}}))
    p = script_run.plan("RQ-20260101-001", run_id="t", queue_dir=q, repo=tmp_path)
    assert p.ok, p.errors
    assert script_run.execute(p, repo=tmp_path)["all_ok"]
    rec = script_run.derive_record(p, repo=tmp_path)
    assert rec["read_state"] == "producer_failed"
    assert rec["verdict"] == "not_applicable"
    assert rec["n"] == "null"
    manifest = json.loads((tmp_path / p.out_dir / "run-manifest.json").read_text())
    assert manifest["all_ok"] is True
