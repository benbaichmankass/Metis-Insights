#!/usr/bin/env python3
"""research-script-run-guard — the token-free runner executes only what the
allowlist admits, and takes only the unit id as input.

WHY THIS EXISTS (lane RQ-RUN, 2026-09-28)
-----------------------------------------
`.github/workflows/research-script-run.yml` runs a command it reads from a
queue unit's YAML on the checked-out ref. That design keeps the command out
of the dispatch surface (nobody can `gh workflow run` an arbitrary command),
but it moves the trust to two places that a PR can change:

  1. the unit file — `run.command` names the script. The runner
     (`scripts/research/script_run.py`) refuses anything outside
     `scripts/research/` / `scripts/backtest*.py` AT RUN TIME, which means a
     bad unit fails on the runner after a dispatch was spent and after the
     dispatcher stamped it. This guard fails the PR instead.
  2. the workflow file — if someone adds a `command:` input to it, the
     allowlist in (1) is bypassed by construction. This guard reads the
     workflow's `on.workflow_dispatch.inputs` and fails on any input other
     than `research_unit` and `power_state`.

WHAT IT CHECKS
--------------
  R1  every `research/queue/*.yaml` whose `run.workflow` is
      `research-script-run.yml` passes `script_run.plan()` statically
      (allowed script family, script exists, argv shape, placeholders,
      timeout bound, rule id + registered_at, `run.inputs.research_unit`).
  R2  the workflow declares exactly the inputs {research_unit, power_state}.
  R4  every committed `comms/research/<unit>/<run_id>/run-manifest.json` names that same unit and run_id
      (all backends: runner, trainer, kaggle). A manifest that disagrees with its directory means the files are
      another run's -- 2026-10-13, two Kaggle results carried the PREVIOUS run's manifest because the kernel mounted
      a stale dataset version (fixed in kaggle_run.py / kernel_main.py; the two found were corrected in place).
  R3  the workflow's `run` job checks out with `persist-credentials: false`
      and references no `secrets.` other than GITHUB_TOKEN; the PAT lives
      only in the `land` job, which never runs the unit's command.

Population is the filesystem (every unit, every status), never the diff.
`--self-test` plants each violation and asserts the guard turns red on it.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import List, Optional

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from scripts.research import script_run  # noqa: E402

WORKFLOW = _REPO / ".github" / "workflows" / script_run.WORKFLOW
ALLOWED_INPUTS = {"research_unit", "power_state"}
_SECRET_RE = re.compile(r"secrets\.([A-Za-z_][A-Za-z0-9_]*)")


TRAINER_WORKFLOW = _REPO / ".github" / "workflows" / script_run.TRAINER_WORKFLOW
KAGGLE_WORKFLOW = _REPO / ".github" / "workflows" / script_run.KAGGLE_WORKFLOW
DATA_WORKFLOW = _REPO / ".github" / "workflows" / script_run.DATA_WORKFLOW


def check(queue_dir: Path = _REPO / "research" / "queue", workflow: Path = WORKFLOW,
          repo: Path = _REPO, trainer_workflow: Optional[Path] = TRAINER_WORKFLOW,
          kaggle_workflow: Optional[Path] = KAGGLE_WORKFLOW,
          data_workflow: Optional[Path] = DATA_WORKFLOW,
          results_root: Path = _REPO / "comms" / "research") -> List[str]:
    problems: List[str] = []

    # R4 -- population is every committed manifest, whatever backend wrote it.
    import json
    for mf in sorted(results_root.glob("*/*/run-manifest.json")):
        unit, run_id = mf.parent.parent.name, mf.parent.name
        try:
            m = json.loads(mf.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(f"R4 {unit}/{run_id}: run-manifest.json unreadable -- {exc}")
            continue
        for why in script_run.manifest_mismatch(m, unit, run_id):
            problems.append(f"R4 {unit}/{run_id}: {why}")

    # R1 — every unit targeting the runner plans cleanly.
    units = script_run.units_targeting_runner(queue_dir)
    for unit in units:
        p = script_run.plan(unit, run_id="guard", queue_dir=queue_dir, repo=repo)
        for e in p.errors:
            problems.append(f"R1 {unit}: {e}")

    # R2/R3 — each workflow's own surface. The trainer twin holds VM_SSH_KEY (reaching the trainer IS the
    # point) and TABPFN_TOKEN (ML-TABPFN-LICENCE: the Prior Labs weights key, sent over ssh stdin only) and nothing
    # else; the runner holds nothing beyond GITHUB_TOKEN.
    problems += _check_workflow(workflow, {"GITHUB_TOKEN"})
    if trainer_workflow is not None:
        problems += _check_workflow(trainer_workflow, {"GITHUB_TOKEN", "VM_SSH_KEY", "TABPFN_TOKEN"})
    # RESEARCH-ROUTER: the Kaggle GPU twin holds KAGGLE_API_TOKEN (the kernel itself receives no secret).
    if kaggle_workflow is not None:
        problems += _check_workflow(kaggle_workflow, {"GITHUB_TOKEN", "KAGGLE_API_TOKEN"})
    # ML-DATA-OFF-TRAINER: the published-data twin holds KAGGLE_API_TOKEN in its fetch step only.
    if data_workflow is not None:
        problems += _check_workflow(data_workflow, {"GITHUB_TOKEN", "KAGGLE_API_TOKEN"})
    return problems


def _check_workflow(workflow: Path, allowed_secrets) -> List[str]:
    import yaml
    problems: List[str] = []
    try:
        wf = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return [f"R2 {workflow.name}: unreadable — {exc}"]
    trig = wf.get("on", wf.get(True)) or {}
    inputs = set(((trig.get("workflow_dispatch") or {}).get("inputs") or {}).keys())
    if inputs != ALLOWED_INPUTS:
        problems.append(f"R2 {workflow.name}: workflow_dispatch inputs must be exactly "
                        f"{sorted(ALLOWED_INPUTS)}, got {sorted(inputs)} — any other input is a "
                        "command surface the allowlist cannot see")
    for key in trig:
        if key != "workflow_dispatch":
            problems.append(f"R2 {workflow.name}: trigger {key!r} — the runner must be "
                            "dispatch-only so every run traces to a unit id")

    jobs = wf.get("jobs") or {}
    run_job = jobs.get("run") or {}
    steps = run_job.get("steps") or []
    checkout = [s for s in steps if str(s.get("uses", "")).startswith("actions/checkout")]
    if not checkout or any(str((s.get("with") or {}).get("persist-credentials", "true")).lower() != "false"
                           for s in checkout):
        problems.append(f"R3 {workflow.name}: the `run` job's checkout must set "
                        "persist-credentials: false so the unit's script holds no credential")
    run_text = yaml.safe_dump(run_job)
    for name in sorted(set(_SECRET_RE.findall(run_text))):
        if name not in allowed_secrets:
            problems.append(f"R3 {workflow.name}: the `run` job references secrets.{name} — "
                            f"this workflow may hold only {sorted(allowed_secrets)}")
    # R4 (ML-TABPFN-LICENCE): a workflow that handles TABPFN_TOKEN must (a) hand it ONLY to the key-placement and
    # redaction steps -- never to the step that runs the unit, whose script is arbitrary allowlisted code -- and
    # (b) redact it from the outputs BEFORE the manifest/derive step and the artifact upload (job-log masking does
    # not cover artifacts, and this repo is public).
    if "TABPFN_TOKEN" in run_text:
        names = [str(st.get("name", "")) for st in steps]
        for st in steps:
            if "TABPFN_TOKEN" in yaml.safe_dump(st):
                n = str(st.get("name", ""))
                if not (n.startswith("Place the TabPFN key") or n.startswith("Redact TABPFN_TOKEN")):
                    problems.append(f"R4 {workflow.name}: step {n!r} references TABPFN_TOKEN -- only the key-placement "
                                    "and redaction steps may; the unit's own step must never hold the key")
        red = [i for i, n in enumerate(names) if n.startswith("Redact TABPFN_TOKEN")]
        after = [i for i, st in enumerate(steps)
                 if str(st.get("name", "")).startswith("Ensure the run manifest")
                 or str(st.get("uses", "")).startswith("actions/upload-artifact")]
        if not red:
            problems.append(f"R4 {workflow.name}: TABPFN_TOKEN is referenced but there is no 'Redact TABPFN_TOKEN' step")
        elif not after or red[0] > min(after):
            problems.append(f"R4 {workflow.name}: the 'Redact TABPFN_TOKEN' step must run before the manifest/derive "
                            "step and the artifact upload")
        elif str(steps[red[0]].get("if", "")).replace(" ", "") != "always()":
            problems.append(f"R4 {workflow.name}: the 'Redact TABPFN_TOKEN' step must be `if: always()`")
    return problems


def _self_test() -> int:
    import tempfile
    import yaml

    def unit(qdir: Path, uid: str, command) -> None:
        (qdir / f"{uid}.yaml").write_text(yaml.safe_dump({
            "id": uid, "title": "t", "question": "q", "status": "queued", "cadence": "once",
            "run": {"workflow": script_run.WORKFLOW, "command": command,
                    "inputs": {"research_unit": uid}},
            "decision_rule": {"id": "RULE-X", "registered_at": "2026-01-01"},
            "lands": {"store": "research/results/"}}))

    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        (repo / "scripts" / "research").mkdir(parents=True)
        (repo / "scripts" / "research" / "ok.py").write_text("print(1)\n")
        (repo / "scripts" / "ops").mkdir()
        (repo / "scripts" / "ops" / "bad.py").write_text("print(1)\n")
        qdir = repo / "q"
        qdir.mkdir()
        unit(qdir, "RQ-20260101-001", ["python3", "scripts/research/ok.py"])
        # The live workflow file is the positive control for R2/R3.
        assert check(qdir, WORKFLOW, repo, results_root=repo / "none") == [], check(qdir, WORKFLOW, repo, results_root=repo / "none")

        # R4 -- a manifest naming another run is red; the matching one is green.
        rr = repo / "res"
        (rr / "RQ-20260101-001" / "55").mkdir(parents=True)
        (rr / "RQ-20260101-001" / "55" / "run-manifest.json").write_text('{"unit":"RQ-20260101-001","run_id":"55"}')
        assert check(qdir, WORKFLOW, repo, results_root=rr) == [], check(qdir, WORKFLOW, repo, results_root=rr)
        (rr / "RQ-20260101-001" / "55" / "run-manifest.json").write_text('{"unit":"RQ-20260101-002","run_id":"44"}')
        r4 = [x for x in check(qdir, WORKFLOW, repo, results_root=rr) if x.startswith("R4")]
        assert len(r4) == 2, r4

        # R1 turns red on a unit naming a script outside the families.
        unit(qdir, "RQ-20260101-002", ["python3", "scripts/ops/bad.py"])
        probs = check(qdir, WORKFLOW, repo, results_root=repo / "none")
        assert any(p.startswith("R1 RQ-20260101-002") for p in probs), probs
        (qdir / "RQ-20260101-002.yaml").unlink()

        # R2 turns red on an extra input, and on an extra trigger.
        wf = yaml.safe_load(WORKFLOW.read_text())
        trig = wf.get("on", wf.get(True))
        trig["workflow_dispatch"]["inputs"]["command"] = {"description": "x"}
        bad = repo / "wf-input.yml"
        bad.write_text(yaml.safe_dump(wf))
        assert any(p.startswith("R2") and "inputs" in p for p in check(qdir, bad, repo))
        wf = yaml.safe_load(WORKFLOW.read_text())
        wf.get("on", wf.get(True))["issues"] = {"types": ["opened"]}
        bad.write_text(yaml.safe_dump(wf))
        assert any(p.startswith("R2") and "trigger" in p for p in check(qdir, bad, repo))

        # R3 turns red when the run job persists credentials or reaches a PAT.
        wf = yaml.safe_load(WORKFLOW.read_text())
        for s in wf["jobs"]["run"]["steps"]:
            if str(s.get("uses", "")).startswith("actions/checkout"):
                s["with"] = {"token": "${{ secrets.BRANCH_PROTECTION_TOKEN }}"}
        bad.write_text(yaml.safe_dump(wf))
        probs = check(qdir, bad, repo)
        assert any("persist-credentials" in p for p in probs), probs
        assert any("secrets.BRANCH_PROTECTION_TOKEN" in p for p in probs), probs

        # ML-RQ-RUNNER: the trainer twin is held to the same surface, and may hold ONLY VM_SSH_KEY and TABPFN_TOKEN extra.
        twf = yaml.safe_load(TRAINER_WORKFLOW.read_text())
        twf.get("on", twf.get(True))["workflow_dispatch"]["inputs"]["command"] = {"description": "x"}
        tbad = repo / "twf-input.yml"
        tbad.write_text(yaml.safe_dump(twf))
        assert any(p.startswith("R2") and "inputs" in p for p in check(qdir, WORKFLOW, repo, tbad))
        twf = yaml.safe_load(TRAINER_WORKFLOW.read_text())
        twf["jobs"]["run"]["steps"][0]["env"] = {"X": "${{ secrets.BRANCH_PROTECTION_TOKEN }}"}
        tbad.write_text(yaml.safe_dump(twf))
        assert any("BRANCH_PROTECTION_TOKEN" in p for p in check(qdir, WORKFLOW, repo, tbad))

        # R4: the redaction step is mandatory wherever TABPFN_TOKEN is referenced, and the unit's step may not hold it.
        twf = yaml.safe_load(TRAINER_WORKFLOW.read_text())
        tsteps = twf["jobs"]["run"]["steps"]
        twf["jobs"]["run"]["steps"] = [x for x in tsteps if not str(x.get("name", "")).startswith("Redact TABPFN_TOKEN")]
        tbad.write_text(yaml.safe_dump(twf))
        assert any(p.startswith("R4") and "no 'Redact" in p for p in check(qdir, WORKFLOW, repo, tbad))
        twf = yaml.safe_load(TRAINER_WORKFLOW.read_text())
        for x in twf["jobs"]["run"]["steps"]:
            if str(x.get("name", "")).startswith("Run the unit"):
                x.setdefault("env", {})["TABPFN_TOKEN"] = "${{ secrets.TABPFN_TOKEN }}"
        tbad.write_text(yaml.safe_dump(twf))
        assert any(p.startswith("R4") and "unit's own step" in p for p in check(qdir, WORKFLOW, repo, tbad))
        twf = yaml.safe_load(TRAINER_WORKFLOW.read_text())
        st2 = twf["jobs"]["run"]["steps"]
        i = [k for k, x in enumerate(st2) if str(x.get("name", "")).startswith("Redact TABPFN_TOKEN")][0]
        st2.append(st2.pop(i))   # redaction moved AFTER the upload
        tbad.write_text(yaml.safe_dump(twf))
        assert any(p.startswith("R4") and "must run before" in p for p in check(qdir, WORKFLOW, repo, tbad))

        # An unreadable workflow is reported, not skipped.
        bad.write_text(": : :\n[")
        assert any(p.startswith("R2") for p in check(qdir, bad, repo))
    print("research-script-run-guard self-test OK")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    problems = check()
    units = script_run.units_targeting_runner()
    if problems:
        for p in problems:
            print(f"::error::research-script-run-guard: {p}")
        print(f"research-script-run-guard: FAIL — {len(problems)} problem(s) over "
              f"{len(units)} unit(s) targeting {script_run.WORKFLOWS}")
        return 1
    print(f"research-script-run-guard: OK — {len(units)} unit(s) target {script_run.WORKFLOWS}; workflow inputs = {sorted(ALLOWED_INPUTS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
