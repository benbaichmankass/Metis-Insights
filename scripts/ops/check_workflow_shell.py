#!/usr/bin/env python3
"""Fail when a workflow `run:` block is not valid shell — BEFORE it burns a runner.

WHY
---
2026-07-30: the `econ-event-study` re-run against real survey consensus computed **every
scorecard successfully** (553 natgas / 575 crude / 136 cpi releases, price_bars ~2911) and
then **threw all of it away**, because the step's summary heredoc was malformed:

    /home/runner/...sh: line 43: warning: here-document ... (wanted `SUMEOF')
    /home/runner/...sh: line 44: syntax error: unexpected end of file
    ##[error]Process completed with exit code 2

The commit step runs AFTER the summary step, so a reporting-side syntax error discarded real
results. `yaml.safe_load` passed — the file was valid YAML the whole time. Nothing in CI looks
at whether the *shell inside* a run block parses.

THE INDENTATION TRAP, specifically
----------------------------------
YAML strips a block scalar's COMMON indentation, so a line at the block's base indent lands at
**column 0** in the script the runner executes. That matters twice over:

  * bash accepts a heredoc terminator ONLY at column 0 (``<<-`` strips TABS, not spaces), and
  * a Python heredoc body must start at column 0 or it is an IndentationError at module level.

Nesting a heredoc inside a ``{ ... }`` group adds indentation, pushing the terminator to column
2 — bash then reads to EOF and dies. The failure is invisible to YAML linting and to review;
it shows up only at runtime, after the expensive part has already run.

WHAT THIS CHECKS
----------------
Every `run:` block in every workflow, through ``bash -n`` (parse only — nothing is executed).
Steps declaring a non-bash ``shell:`` (python, pwsh, node, ...) are skipped, since ``bash -n``
would be the wrong parser rather than a real finding.

Stdlib + PyYAML only.

Usage:
  python scripts/ops/check_workflow_shell.py
  python scripts/ops/check_workflow_shell.py --dir .github/workflows
"""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DEFAULT_DIR = ".github/workflows"

# A step may declare a different interpreter; `bash -n` on those would be a false alarm.
_BASH_SHELLS = {"bash", "sh", "bash -e {0}", ""}


def _is_bash(step: dict, job_default: str) -> bool:
    shell = str(step.get("shell") or job_default or "").strip().lower()
    if not shell:
        return True  # GitHub's default on Linux runners is bash
    return shell.split()[0] in {"bash", "sh"}


def check_run_blocks(path: pathlib.Path) -> list[str]:
    """Return a list of human-readable failures for one workflow file."""
    try:
        import yaml
    except ImportError:  # pragma: no cover - CI installs pyyaml
        return [f"{path}: PyYAML not available"]
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — invalid YAML is another guard's job
        return [f"{path}: unparseable YAML ({exc})"]
    if not isinstance(doc, dict):
        return []

    out: list[str] = []
    for job_name, job in (doc.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        job_default = str(((job.get("defaults") or {}).get("run") or {}).get("shell") or "")
        for idx, step in enumerate(job.get("steps") or []):
            if not isinstance(step, dict):
                continue
            run = step.get("run")
            if not isinstance(run, str) or not run.strip():
                continue
            if not _is_bash(step, job_default):
                continue
            name = step.get("name") or f"step[{idx}]"
            tmp = None
            try:
                with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
                    fh.write(run)
                    tmp = fh.name
                res = subprocess.run(["bash", "-n", tmp], capture_output=True, text=True)
            finally:
                if tmp and os.path.exists(tmp):
                    os.unlink(tmp)
            if res.returncode != 0:
                detail = (res.stderr or "").strip().splitlines()
                msg = detail[-1] if detail else f"bash -n exited {res.returncode}"
                # Strip the temp path so the message names the workflow, not /tmp/xyz.sh
                msg = msg.split(": ", 1)[-1] if msg.startswith("/tmp/") else msg
                out.append(f"{path}: job '{job_name}' step '{name}': {msg}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo-root", default=str(REPO))
    ap.add_argument("--dir", default=DEFAULT_DIR)
    args = ap.parse_args(argv)

    root = pathlib.Path(args.repo_root) / args.dir
    if not root.is_dir():
        print(f"::error::no workflow directory at {root}")
        return 1

    files = sorted(list(root.glob("*.yml")) + list(root.glob("*.yaml")))
    failures: list[str] = []
    for f in files:
        failures.extend(check_run_blocks(f))

    if not failures:
        print(f"OK — every `run:` block in {len(files)} workflow(s) is valid shell.")
        return 0

    print("::error::a workflow `run:` block is not valid shell. This does NOT show up in "
          "YAML linting or review — it fails at RUNTIME, after the expensive work has "
          "already run. On 2026-07-30 it discarded a completed event study (553/575/136 "
          "releases, price_bars ~2911) because a summary heredoc terminator sat at column 2.")
    for f in failures:
        print(f"  {f}")
    print("")
    print("Common cause: YAML strips a block scalar's COMMON indent, so base-indent lines "
          "land at column 0 — which is the ONLY place bash accepts a heredoc terminator "
          "(`<<-` strips TABS, not spaces) and the only place a Python heredoc body can "
          "start. Nesting a heredoc inside a `{ ... }` group breaks both.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
