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


def test_the_runner_lands_nothing_itself_and_holds_no_pat():
    """Manager review 2026-09-29: the runner only uploads an artifact (outputs +
    record-inputs.json); landing is batched by the dispatcher's collect step."""
    wf = yaml.safe_load(WF.read_text())
    assert list(wf["jobs"]) == ["run"], list(wf["jobs"])
    jobs_text = yaml.safe_dump(wf["jobs"])   # the jobs, not the header prose
    assert "BRANCH_PROTECTION_TOKEN" not in jobs_text and "commit-to-main" not in jobs_text
    assert "secrets." not in jobs_text
    steps = wf["jobs"]["run"]["steps"]
    derive = [s for s in steps if "record-inputs.json" in str(s.get("run", ""))]
    assert derive and "--derive-record" in derive[0]["run"] and "--power-state" in derive[0]["run"]
    upload = [s for s in steps if str(s.get("uses", "")).startswith("actions/upload-artifact")][0]
    assert upload["with"]["if-no-files-found"] == "error"
    assert str(upload["with"]["name"]).startswith("research-script-run-")


def test_the_dispatcher_lands_the_batch_once_per_cycle_and_asserts_it():
    wf = yaml.safe_load((REPO / ".github/workflows/research-queue-dispatch.yml").read_text())
    steps = wf["jobs"]["dispatch"]["steps"]
    names = [s.get("name", "") for s in steps]
    i_collect = next(i for i, n in enumerate(names) if n.startswith("Collect the runner"))
    i_batch = next(i for i, n in enumerate(names) if n.startswith("Land the batch"))
    i_assert = next(i for i, n in enumerate(names) if n.startswith("Assert every batched"))
    i_grade = names.index("Grade and dispatch")
    assert i_collect < i_batch < i_assert < i_grade
    batch = steps[i_batch]
    assert batch["uses"] == "./.github/actions/commit-to-main"
    assert str(batch["with"]["verify-merged"]).lower() == "true"
    assert batch["with"]["paths"] == "comms/research research/results"
    assert "collect_runner_results.py" in steps[i_collect]["run"]
    assert "--max-research-inflight 3" in steps[i_grade]["run"] and "--max-repo-queued 10" in steps[i_grade]["run"]


def test_record_inputs_cover_every_emit_field():
    """Every field the collector passes to research_result.py --emit is one
    derive_record() writes (plus the two the runner adds), so a record can
    never land with a placeholder for a field the run actually produced."""
    from scripts.research import collect_runner_results as col
    src = (REPO / "scripts/research/script_run.py").read_text()
    for key in col._EMIT_FIELDS:
        assert f'"{key}"' in src, f"{key} is emitted by the collector but never derived"
    argv_src = (REPO / "scripts/research/research_result.py").read_text()
    for key in col._EMIT_FIELDS:
        assert f'"--{key.replace("_", "-")}"' in argv_src, key


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


# --------------------------------------------------------------------------
# the dispatcher sends the runner only the inputs it declares (RQ-RUN, run 36476810004)
# --------------------------------------------------------------------------

def test_the_dispatcher_drops_undeclared_runner_inputs_and_names_them():
    from scripts.research import dispatch_queue as dq
    declared = dq.declared_inputs("research-script-run.yml")
    assert declared == ["power_state", "research_unit"], declared
    entry = {"id": "RQ-20260928-011",
             "run": {"workflow": "research-script-run.yml",
                     "inputs": {"research_unit": "RQ-20260928-011",
                                "script": "scripts/research/prop_ev_grid.py"}}}
    inputs, dropped = dq.dispatch_inputs(entry, power_state="runnable")
    assert inputs == {"research_unit": "RQ-20260928-011", "power_state": "runnable"}
    assert dropped == ["script"]
    # every committed runner unit dispatches with declared inputs only
    for unit in script_run.units_targeting_runner():
        d = yaml.safe_load((REPO / "research" / "queue" / f"{unit}.yaml").read_text())
        sent, _ = dq.dispatch_inputs(d, power_state="runnable")
        assert set(sent) <= set(declared), (unit, sent)


def test_the_dispatcher_keeps_other_workflows_inputs_verbatim_and_reads_absence_honestly(tmp_path):
    from scripts.research import dispatch_queue as dq
    entry = {"id": "X", "run": {"workflow": "e35-bracket-sweep.yml",
                                "inputs": {"only": "eth_pullback_2h", "research_unit": "X"}}}
    inputs, dropped = dq.dispatch_inputs(entry, power_state="runnable")
    assert dropped == [] and inputs["only"] == "eth_pullback_2h" and inputs["power_state"] == "runnable"
    assert dq.declared_inputs("no-such-workflow.yml", repo=tmp_path) is None, \
        "an unreadable workflow is 'could not look', never 'declares nothing'"
