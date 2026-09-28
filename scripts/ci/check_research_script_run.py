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
from typing import List

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from scripts.research import script_run  # noqa: E402

WORKFLOW = _REPO / ".github" / "workflows" / script_run.WORKFLOW
ALLOWED_INPUTS = {"research_unit", "power_state"}
_SECRET_RE = re.compile(r"secrets\.([A-Za-z_][A-Za-z0-9_]*)")


def check(queue_dir: Path = _REPO / "research" / "queue", workflow: Path = WORKFLOW,
          repo: Path = _REPO) -> List[str]:
    import yaml
    problems: List[str] = []

    # R1 — every unit targeting the runner plans cleanly.
    units = script_run.units_targeting_runner(queue_dir)
    for unit in units:
        p = script_run.plan(unit, run_id="guard", queue_dir=queue_dir, repo=repo)
        for e in p.errors:
            problems.append(f"R1 {unit}: {e}")

    # R2/R3 — the workflow's own surface.
    try:
        wf = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return problems + [f"R2 {workflow.name}: unreadable — {exc}"]
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
        if name != "GITHUB_TOKEN":
            problems.append(f"R3 {workflow.name}: the `run` job references secrets.{name} — "
                            "the job that executes the unit's command may hold nothing beyond "
                            "GITHUB_TOKEN")
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
        assert check(qdir, WORKFLOW, repo) == [], check(qdir, WORKFLOW, repo)

        # R1 turns red on a unit naming a script outside the families.
        unit(qdir, "RQ-20260101-002", ["python3", "scripts/ops/bad.py"])
        probs = check(qdir, WORKFLOW, repo)
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
              f"{len(units)} unit(s) targeting {script_run.WORKFLOW}")
        return 1
    print(f"research-script-run-guard: OK — {len(units)} unit(s) target {script_run.WORKFLOW} "
          f"({', '.join(units) or 'none'}); workflow inputs = {sorted(ALLOWED_INPUTS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
