#!/usr/bin/env python3
"""Plant-a-defect proof for ONE CI guard. Machine-records the evidence.

WHY. A guard's self-test, or its docstring, is a claim about the guard. It is
not a measurement. The measurement is: plant the defect the guard says it
catches, run the guard exactly as CI runs it, and see the exit code change.
This tool makes that the ONLY way an evidence row gets written, because the row
is generated here from the git diff and the exit codes it observed -- the
caller supplies the plant and the claim, never the result.

PROTOCOL (per guard), in a scratch worktree off origin/main that is removed at
the end and NEVER pushed:
  1. control_pre   run the guard on the clean tree            -> exit code
  2. plant         run --plant-sh in the worktree, commit it
                   (the row stores `git diff origin/main...HEAD`)
  3. planted       run the guard again, same command          -> exit code
  4. revert        `git reset --hard` to the pre-plant commit
  5. control_post  run the guard again                         -> exit code

The guard is run as CI runs it: `scripts/ci/run_guards.py --only <g> --all`
(--all disables relevance so the guard cannot skip; the PR diff is still
generated from `origin/main...HEAD`, so a diff-scoped guard sees the plant).

VERDICT (computed here, not passed in):
  caught      control_pre == control_post == 0 AND planted FAIL >= 1
  missed      control_pre == control_post == 0 AND planted exit 0
  invalid     a control run was not clean, or the planted run COULD NOT RUN /
              errored without a FAIL for this guard -- NOT evidence either way
`unplantable` is a human judgement recorded with --unplantable "<reason>"; it
carries no exit codes and the row says so.

  python3 scripts/ops/guard_plant.py --guard secret-scan \
      --claim "fails a diff that adds a credential-shaped string" \
      --plant-sh 'echo "x = \"<planted>\"" > src/_plant.py' \
      --out docs/audits/system-audit-2026-09-29/GUARD-PROOF.findings.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
COUNTS = re.compile(r"PASS (\d+) · FAIL (\d+) · COULD-NOT-RUN (\d+) · SKIP (\d+)")


def sh(cmd, cwd, timeout=900):
    p = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), text=True,
                       capture_output=True, timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def run_guard(wt, guard):
    argv = ["python3", "scripts/ci/run_guards.py", "--only", guard, "--all",
            "--base-ref", "main", "--pr-diff", str(Path(wt) / ".plant-pr.diff")]
    p = subprocess.run(argv, cwd=wt, text=True, capture_output=True, timeout=1500)
    out = (p.stdout or "") + (p.stderr or "")
    m = COUNTS.search(out)
    counts = dict(zip(("pass", "fail", "could_not_run", "skip"),
                      map(int, m.groups()))) if m else None
    lines = [l for l in out.splitlines() if "::error::" in l or "FAIL" in l
             or "COULD NOT RUN" in l]
    return {"exit": p.returncode, "counts": counts,
            "excerpt": "\n".join(lines[-12:])[:1800]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--guard", required=True)
    ap.add_argument("--claim", required=True,
                    help="the defect class the guard says it catches, in its own words")
    ap.add_argument("--plant-sh", help="shell run inside the scratch worktree")
    ap.add_argument("--unplantable", help="reason; records no exit codes")
    ap.add_argument("--out", required=True)
    ap.add_argument("--variant", default="", help="label when a guard gets >1 plant")
    a = ap.parse_args()

    if a.unplantable:
        row = {"guard": a.guard, "variant": a.variant, "claim": a.claim,
               "verdict": "unplantable", "reason": a.unplantable}
        with open(a.out, "a") as f:
            f.write(json.dumps(row) + "\n")
        print(json.dumps(row)); return 0
    if not a.plant_sh:
        ap.error("--plant-sh required unless --unplantable")

    wt = tempfile.mkdtemp(prefix=f"plant-{a.guard}-", dir="/tmp/claude-0")
    shutil.rmtree(wt)
    base = subprocess.check_output(["git", "rev-parse", "origin/main"], cwd=REPO, text=True).strip()
    subprocess.check_call(["git", "worktree", "add", "--detach", wt, base], cwd=REPO,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # local ref `main` so `--base-ref main` resolves origin/main in the worktree
        pre = run_guard(wt, a.guard)
        rc, out = sh(a.plant_sh, wt)
        plant_rc = rc
        sh("git add -A && git -c user.email=p@p -c user.name=plant commit -q "
           "--allow-empty -m 'PLANT (scratch, never pushed)'", wt)
        diff = sh("git diff origin/main...HEAD", wt)[1]
        planted = run_guard(wt, a.guard)
        sh(f"git reset -q --hard {base}", wt)
        post = run_guard(wt, a.guard)
    finally:
        subprocess.call(["git", "worktree", "remove", "--force", wt], cwd=REPO,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    clean = (pre["exit"] == 0 and post["exit"] == 0)
    if plant_rc != 0 or not diff.strip():
        verdict = "invalid"
        why = "plant script failed or produced an empty diff"
    elif not clean:
        verdict, why = "invalid", "a control run was not exit 0"
    elif planted["exit"] == 0:
        verdict, why = "missed", "planted run exit 0"
    elif planted["counts"] and planted["counts"]["fail"] >= 1 and planted["counts"]["could_not_run"] == 0:
        verdict, why = "caught", "planted run FAIL>=1"
    else:
        verdict, why = "invalid", "planted run non-zero but not a FAIL of this guard"
    row = {"guard": a.guard, "variant": a.variant, "claim": a.claim,
           "plant_command": a.plant_sh, "planted_diff": diff[:6000],
           "control_pre_exit": pre["exit"], "planted_exit": planted["exit"],
           "control_post_exit": post["exit"],
           "planted_counts": planted["counts"], "planted_excerpt": planted["excerpt"],
           "control_pre_counts": pre["counts"], "control_post_counts": post["counts"],
           "verdict": verdict, "verdict_basis": why,
           "harness": "scripts/ci/run_guards.py --only <guard> --all --base-ref main",
           "base_sha": base}
    with open(a.out, "a") as f:
        f.write(json.dumps(row) + "\n")
    print(a.guard, a.variant, "->", verdict, why, "| exits", pre["exit"], planted["exit"], post["exit"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
