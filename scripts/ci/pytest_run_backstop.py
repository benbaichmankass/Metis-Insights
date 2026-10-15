#!/usr/bin/env python3
"""Bounded post-merge backstop for `pytest-run` on main (GH-CAPACITY-PR-CI-FANOUT).

Since PRs run only the import-graph-affected tests, the FULL suite on main is
what catches a cross-module break that a PR's selection missed.  Push-to-main
runs do not reliably carry it:

  * an auto-merged PR's merge commit is created by GITHUB_TOKEN, and GitHub
    starts no workflow run for it (main-tree-watch.yml header: 0 of 24, 0 of 31);
  * the next push run diffs `github.event.before...sha`, and `before` is the
    ref's previous value -- the token-merged commit itself -- so that code is
    OUTSIDE the next push run's range, and a data-only chore push on top of it
    runs only the data subset.

So `pytest-run` also fires on a schedule (every 30 min), and this script
decides, for main's HEAD, whether that tick must run the full suite:

  covered   -- a COMPLETED run on main (any event) executed the full "Run tests"
               step on sha S, S is an ancestor of HEAD, and every commit in
               S..HEAD is invisible to the suite or data-only (same classifier
               as the workflow).  Skip: HEAD's code is already graded.
  in_flight -- a schedule/dispatch run on HEAD (always full) is queued or running,
               or a run on HEAD has its full "Run tests" step already started.
               Skip: it will finish and page on red.
  run       -- anything else, including "could not look" (API error, no full run
               in the window, S not an ancestor).  The failure direction is
               "runs the suite", never "skips it".

Output (for $GITHUB_OUTPUT): `run=true|false` and `reason=<one line>`.

Worst-case detection of a post-merge break: one tick interval (30 min) plus
the full suite's run time (~25 min) plus queueing for a runner.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FULL_STEP = "Run tests"  # the full-suite step's exact name in pytest-run.yml
TESTED = ("success", "failure")  # a red full run already paged; HEAD was graded


def _load_scope():
    import importlib.util

    spec = importlib.util.spec_from_file_location("pytest_run_scope", REPO / "scripts" / "ci" / "pytest_run_scope.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def full_step(jobs: dict) -> dict | None:
    for job in jobs.get("jobs", []):
        for step in job.get("steps") or []:
            if step.get("name") == FULL_STEP:
                return step
    return None


def range_is_suite_neutral(base: str, head: str, git: Callable[..., str] | None = None) -> tuple[bool, str]:
    """True when every change in base..head is invisible to the suite or data-only."""
    git = git or (lambda *a: subprocess.check_output(["git", *a], text=True, cwd=REPO))
    if base == head:
        return True, "HEAD itself"
    try:
        git("merge-base", "--is-ancestor", base, head)
    except subprocess.CalledProcessError:
        return False, f"{base[:8]} is not an ancestor of HEAD"
    files = git("diff", "--name-only", "--no-renames", f"{base}...{head}").splitlines()
    scope = _load_scope()
    rel = scope.relevance_regex()
    relevant = [f for f in files if f.strip() and re.search(rel, f)]
    if not relevant:
        return True, f"{len(files)} file(s) since {base[:8]}, none read by the suite"
    mode, _ = scope.classify(relevant, rel)
    if mode == "data":
        return True, f"{len(relevant)} suite-read file(s) since {base[:8]}, all data-only"
    return False, f"code changed since the last full run on {base[:8]}"


def decide(
    head: str,
    runs: list[dict],
    jobs_of: Callable[[int], dict],
    neutral: Callable[[str, str], tuple[bool, str]] = range_is_suite_neutral,
) -> tuple[bool, str]:
    """`(run_full_suite, reason)`. `runs` is newest-first, branch main."""
    for r in runs:
        if r.get("head_sha") != head or r.get("status") == "completed":
            continue
        if r.get("event") in ("schedule", "workflow_dispatch"):
            return False, f"in_flight: {r.get('event')} run {r.get('id')} on HEAD runs the full suite"
        step = full_step(jobs_of(r["id"]))
        if step and step.get("status") in ("in_progress", "completed") and step.get("conclusion") != "skipped":
            return False, f"in_flight: run {r.get('id')} on HEAD is executing the full suite"
    for r in runs:
        if r.get("status") != "completed":
            continue
        step = full_step(jobs_of(r["id"]))
        if not step or step.get("conclusion") not in TESTED:
            continue
        ok, why = neutral(r["head_sha"], head)
        if ok:
            return False, f"covered: full suite ran on {r['head_sha'][:8]} (run {r['id']}, {step['conclusion']}); {why}"
        return True, f"run: {why}"
    return True, "run: no completed full-suite run on main in the window"


def _api(path: str) -> dict:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{os.environ['REPO']}/{path}",
        headers={"Authorization": f"Bearer {os.environ['GH_TOKEN']}", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--head", required=True)
    args = ap.parse_args(argv)
    try:
        runs = _api("actions/workflows/pytest-run.yml/runs?branch=main&per_page=100").get("workflow_runs", [])
        run, reason = decide(args.head, runs, lambda rid: _api(f"actions/runs/{rid}/jobs?per_page=100"))
    except Exception as exc:  # noqa: BLE001 -- could not look -> run the suite
        run, reason = True, f"run: could not read run history ({type(exc).__name__})"
    print(f"run={'true' if run else 'false'}")
    print(f"reason={reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
