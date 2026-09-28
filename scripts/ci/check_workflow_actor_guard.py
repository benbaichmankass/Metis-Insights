#!/usr/bin/env python3
"""CI guard: an `issues:`-triggered, secret-holding workflow must gate on WHO
opened the issue, not on the label alone (FIX-CA-16 / `CA-A13-gpu-burst-actor-guard`).

WHY THIS EXISTS
----------------
This repo's dispatch-by-labelled-issue convention lets any GitHub account that
can open an issue on the (public) repo trigger a workflow run — the label gate
alone restricts nothing, because opening an issue with any label is something
every visitor can do. ~27 issues-triggered workflows that hold a secret beyond
the ambient `GITHUB_TOKEN` (`system-actions.yml`, `vm-ib-gateway-live-login-
test.yml`, …) AND the label check with an actor-identity clause in the job's
own `if:` — `github.event.issue.user.login == github.repository_owner` (or
`'github-actions[bot]'`) — so only the repo owner or the bot's own automation
can actually spend the secret. `gpu-burst-train.yml` was confirmed 2026-09-27
to hold five provider/VM secrets (`RUNPOD_API_KEY`, `VAST_API_KEY`,
`RUNPOD_SSH_KEY`, `VM_SSH_KEY`, `BRANCH_PROTECTION_TOKEN`) behind an
`issues: opened` trigger gated on the label alone — the one file in the
convention's own footprint missing it.

WHAT IT CHECKS
--------------
For every `.github/workflows/*.yml` that declares an `issues:` trigger: for
each job in it that references `secrets.<NAME>` for some NAME other than
`GITHUB_TOKEN` (anywhere in the job body — env, step env, or inline
expression), the job's own `if:` must contain one of `issue.user.login`,
`repository_owner`, or `github.actor`. A job with no `issues:`-reachable
`secrets.` reference beyond `GITHUB_TOKEN` is not required to carry the guard
(a job with nothing to steal has nothing this check protects).

Exit 0 = every issues-triggered, secret-holding job carries an actor-identity
clause. Exit 1 = at least one does not.

Usage:
    python3 scripts/ci/check_workflow_actor_guard.py            # the standing audit
    python3 scripts/ci/check_workflow_actor_guard.py --self-test  # planted-failure control
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO / ".github" / "workflows"

_ACTOR_GUARD_MARKERS = (
    "issue.user.login",
    "repository_owner",
    "github.actor",
)

_SECRET_RE = re.compile(r"secrets\.([A-Za-z0-9_]+)")


def _on_block(doc: Dict[str, Any]) -> Any:
    # YAML 1.1 parses the bare key `on:` as the boolean `True` — the same
    # gotcha `check_workflow_trigger_reachability.py::push_branches` handles.
    return doc.get("on") if "on" in doc else doc.get(True)


def has_issues_trigger(doc: Dict[str, Any]) -> bool:
    on = _on_block(doc) or {}
    if isinstance(on, str):
        return on == "issues"
    if isinstance(on, list):
        return "issues" in on
    if isinstance(on, dict):
        return "issues" in on
    return False


def job_references_privileged_secret(job: Dict[str, Any]) -> bool:
    """True if `secrets.<NAME>` for NAME != GITHUB_TOKEN appears anywhere in the job."""
    try:
        text = yaml.safe_dump(job)
    except yaml.YAMLError:
        text = str(job)
    return any(name != "GITHUB_TOKEN" for name in _SECRET_RE.findall(text))


def job_has_actor_guard(job: Dict[str, Any]) -> bool:
    if_expr = job.get("if")
    if not isinstance(if_expr, str):
        return False
    return any(marker in if_expr for marker in _ACTOR_GUARD_MARKERS)


def job_needs(job: Dict[str, Any]) -> List[str]:
    needs = job.get("needs")
    if isinstance(needs, str):
        return [needs]
    if isinstance(needs, list):
        return [n for n in needs if isinstance(n, str)]
    return []


def job_bypasses_needs_protection(job: Dict[str, Any], needs: List[str]) -> bool:
    """True if `job`'s own `if:` defeats the default needs-skip propagation.

    By default a job is skipped when a job it `needs` was skipped, which is
    exactly what happens when an upstream job's actor-guard `if:` evaluates
    false — so a downstream job with NO override inherits the guard for free.
    `always()` is the one thing that overrides that default, so a job using it
    only stays protected if it re-checks every dependency's result itself
    (`needs.<dep>.result != 'skipped'`, the pattern
    `research-exit-head-build.yml::research_result` and
    `research-exit-head-replay-trainer.yml::land` both use).
    """
    if_expr = job.get("if")
    if not isinstance(if_expr, str) or "always()" not in if_expr:
        return False
    return any(f"needs.{dep}.result" not in if_expr for dep in needs)


def _effectively_guarded(jobs: Dict[str, Any]) -> Dict[str, bool]:
    """Per-job guard status, propagated through `needs:` (see docstring above)."""
    cache: Dict[str, bool] = {}

    def resolve(name: str, seen: frozenset) -> bool:
        if name in cache:
            return cache[name]
        if name in seen:
            cache[name] = False
            return False
        job = jobs.get(name)
        if not isinstance(job, dict):
            cache[name] = False
            return False
        if job_has_actor_guard(job):
            cache[name] = True
            return True
        needs = job_needs(job)
        if not needs or job_bypasses_needs_protection(job, needs):
            cache[name] = False
            return False
        result = all(resolve(dep, seen | {name}) for dep in needs)
        cache[name] = result
        return result

    return {name: resolve(name, frozenset()) for name in jobs}


def check_doc(doc: Dict[str, Any]) -> List[str]:
    """Return the list of job names in `doc` that need but lack an actor guard."""
    if not isinstance(doc, dict) or not has_issues_trigger(doc):
        return []
    jobs = doc.get("jobs") or {}
    if not isinstance(jobs, dict):
        return []
    guarded = _effectively_guarded(jobs)
    missing = []
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        if job_references_privileged_secret(job) and not guarded.get(job_name, False):
            missing.append(job_name)
    return missing


def check_file(path: Path) -> List[str]:
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        # Parse failures are the catalog/lint guards' concern, not this one.
        return []
    return [f"{path.name}::{job}" for job in check_doc(doc)]


def run() -> int:
    violations: List[str] = []
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        violations.extend(check_file(path))
    for path in sorted(WORKFLOWS_DIR.glob("*.yaml")):
        violations.extend(check_file(path))

    if violations:
        print("[workflow-actor-guard] FAIL — issues-triggered jobs holding a "
              "privileged secret with no actor-identity clause in their `if:`:")
        for v in violations:
            print(f"  - {v}")
        print("\nAdd `(github.event.issue.user.login == github.repository_owner "
              "|| github.event.issue.user.login == 'github-actions[bot]')` "
              "AND-ed onto the job's label check (mirrors "
              "vm-ib-gateway-live-login-test.yml / system-actions.yml).")
        return 1

    print("[workflow-actor-guard] OK — every issues-triggered, secret-holding "
          "job carries an actor-identity clause.")
    return 0


def self_test() -> int:
    """Planted-failure control: an unguarded secret-holding job must be flagged,
    a guarded one must not, and a secret-free job must never be required to carry
    the clause at all (the negative that keeps this guard from over-triggering).
    """
    fails: List[str] = []

    def check(label: str, got, want) -> None:
        if got != want:
            fails.append(f"  FAIL — {label}: got {got!r}, want {want!r}")
        else:
            print(f"  PASS — {label}")

    unguarded = yaml.safe_load("""
on:
  issues:
    types: [opened]
jobs:
  burst:
    if: contains(github.event.issue.labels.*.name, 'gpu-burst-train')
    steps:
      - env:
          RUNPOD_API_KEY: ${{ secrets.RUNPOD_API_KEY }}
        run: echo hi
""")
    check("unguarded secret-holding job is flagged",
          check_doc(unguarded), ["burst"])

    guarded = yaml.safe_load("""
on:
  issues:
    types: [opened]
jobs:
  burst:
    if: |
      (github.event.issue.user.login == github.repository_owner ||
       github.event.issue.user.login == 'github-actions[bot]') &&
      contains(github.event.issue.labels.*.name, 'gpu-burst-train')
    steps:
      - env:
          RUNPOD_API_KEY: ${{ secrets.RUNPOD_API_KEY }}
        run: echo hi
""")
    check("guarded secret-holding job passes", check_doc(guarded), [])

    no_secret = yaml.safe_load("""
on:
  issues:
    types: [opened]
jobs:
  triage:
    if: contains(github.event.issue.labels.*.name, 'triage')
    steps:
      - run: echo hi
""")
    check("a job with no privileged secret is never required to guard",
          check_doc(no_secret), [])

    github_token_only = yaml.safe_load("""
on:
  issues:
    types: [opened]
jobs:
  comment:
    if: contains(github.event.issue.labels.*.name, 'comment')
    steps:
      - env:
          GH: ${{ secrets.GITHUB_TOKEN }}
        run: echo hi
""")
    check("GITHUB_TOKEN alone does not count as a privileged secret",
          check_doc(github_token_only), [])

    not_issues_triggered = yaml.safe_load("""
on:
  workflow_dispatch:
jobs:
  deploy:
    steps:
      - env:
          KEY: ${{ secrets.RUNPOD_API_KEY }}
        run: echo hi
""")
    check("a workflow with no issues: trigger is out of scope entirely",
          check_doc(not_issues_triggered), [])

    needs_protected = yaml.safe_load("""
on:
  issues:
    types: [opened]
jobs:
  build:
    if: |
      github.event.issue.user.login == github.repository_owner &&
      contains(github.event.issue.labels.*.name, 'x')
    steps:
      - run: echo hi
  land:
    needs: build
    if: always() && needs.build.result != 'skipped'
    steps:
      - env:
          VM_SSH_KEY: ${{ secrets.VM_SSH_KEY }}
        run: echo hi
""")
    check("a downstream job that re-checks needs.<dep>.result inherits the "
          "upstream guard (research-exit-head-build.yml/…-replay-trainer.yml shape)",
          check_doc(needs_protected), [])

    needs_bypassed = yaml.safe_load("""
on:
  issues:
    types: [opened]
jobs:
  build:
    if: |
      github.event.issue.user.login == github.repository_owner &&
      contains(github.event.issue.labels.*.name, 'x')
    steps:
      - run: echo hi
  land:
    needs: build
    if: always()
    steps:
      - env:
          VM_SSH_KEY: ${{ secrets.VM_SSH_KEY }}
        run: echo hi
""")
    check("a bare always() with no needs.<dep>.result re-check defeats the "
          "inherited guard and must still be flagged",
          check_doc(needs_bypassed), ["land"])

    if fails:
        print("\n".join(fails))
        print(f"\nself-test: {len(fails)} check(s) failed")
        return 1
    print("\nself-test: all checks passed")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
