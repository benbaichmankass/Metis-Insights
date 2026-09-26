#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py::action-input-wiring
"""CI guard: every `with:` key a workflow passes to a LOCAL composite action
must be an input that action actually DECLARES.

WHY THIS EXISTS
---------------
GitHub does not fail a step that passes an undeclared `with:` key to a
composite action — it prints `::warning::Unexpected input(s) '<name>'` and
silently drops it. The step still exits 0. Measured on RQ-20260922-003 and
RQ-20260922-004 (`m20-exit-lever-sweep.yml` runs 36239255060 / 36239256148):
`.github/actions/research-result/action.yml` never declared a `records-file`
input, so the workflow's `records-file: rr/records.jsonl` was dropped and the
`emit` step silently fell through to its single-record fallback values
(`verdict: not_applicable`, `read-state: not_attempted`, `n: null`) — landing
every fanned-out per-leg result as that placeholder instead of a computed
verdict. A green job, a merged PR, and a result file that reads as a total
pipeline failure (BL-20260926-RECORDS-FILE-UNDECLARED-SO-GITHUB-SILENTLY-
DROPS-IT).

This guard reads the same two files a human would have to compare by hand —
the workflow's `with:` block and the action's `inputs:` block — and fails
the PR instead of relying on a job log warning nobody greps for.

WHAT IT CHECKS
--------------
For every `uses: ./.github/actions/<name>` step (LOCAL composite actions
only — a third-party action's input contract is not this repo's to enforce)
in every `.github/workflows/*.yml`, every key under that step's `with:` must
appear in `<name>`'s own `action.yml` under `inputs:`. A key that does not is
reported by workflow file, step name, and the unknown key.

NOT CHECKED: required-but-missing inputs (the workflow would fail loudly on
its own dispatch attempt — noisy, not silent) and expression-valued keys
(they still need to resolve to a declared name; the check is on the KEY, not
the value).

Exit 0 = every `with:` key is declared. Exit 1 = at least one is not.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, NamedTuple

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO / ".github" / "workflows"
ACTIONS_DIR = REPO / ".github" / "actions"


class Violation(NamedTuple):
    workflow: str
    step: str
    action: str
    key: str


def _action_inputs(action_dir: Path) -> set:
    for name in ("action.yml", "action.yaml"):
        f = action_dir / name
        if f.exists():
            doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            return set((doc.get("inputs") or {}).keys())
    raise FileNotFoundError(f"no action.yml under {action_dir}")


def _iter_local_action_steps(workflow_doc: dict):
    jobs = workflow_doc.get("jobs") or {}
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps") or []:
            if not isinstance(step, dict):
                continue
            uses = step.get("uses", "")
            if isinstance(uses, str) and uses.startswith("./.github/actions/"):
                yield step, uses


def check_tree(workflows_dir: Path, actions_dir: Path) -> List[Violation]:
    violations: List[Violation] = []
    for wf_path in sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml")):
        doc = yaml.safe_load(wf_path.read_text(encoding="utf-8")) or {}
        for step, uses in _iter_local_action_steps(doc):
            action_rel = uses.split("./.github/actions/", 1)[1].split("@", 1)[0].strip("/")
            action_dir = actions_dir / action_rel
            try:
                declared = _action_inputs(action_dir)
            except FileNotFoundError:
                violations.append(Violation(wf_path.name, step.get("name", uses),
                                             action_rel, "<action.yml not found>"))
                continue
            supplied = (step.get("with") or {}).keys()
            for key in supplied:
                if key not in declared:
                    violations.append(Violation(wf_path.name, step.get("name", uses),
                                                 action_rel, str(key)))
    return violations


def _self_test() -> int:
    import tempfile

    failures = 0

    def case(label: str, ok: bool) -> None:
        nonlocal failures
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            failures += 1

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        wf_dir = root / ".github" / "workflows"
        act_dir = root / ".github" / "actions"
        wf_dir.mkdir(parents=True)
        (act_dir / "good-action").mkdir(parents=True)
        (act_dir / "good-action" / "action.yml").write_text(
            "inputs:\n  known-key:\n    required: false\n"
            "runs:\n  using: composite\n  steps: []\n", encoding="utf-8")

        # Positive: a workflow using only declared inputs is clean.
        (wf_dir / "clean.yml").write_text(
            "jobs:\n  j:\n    steps:\n"
            "      - uses: ./.github/actions/good-action\n"
            "        with:\n          known-key: x\n", encoding="utf-8")
        clean = check_tree(wf_dir, act_dir)
        case("a workflow passing only declared inputs trips nothing", clean == [])

        # Negative control: THE PLANTED BUG. A `with:` key the action never
        # declared must be caught, not silently accepted.
        (wf_dir / "clean.yml").unlink()
        (wf_dir / "buggy.yml").write_text(
            "jobs:\n  j:\n    steps:\n"
            "      - uses: ./.github/actions/good-action\n"
            "        with:\n          known-key: x\n          records-file: rr.jsonl\n",
            encoding="utf-8")
        buggy = check_tree(wf_dir, act_dir)
        case("an undeclared `with:` key (the RQ-003/004 bug, replayed) is caught",
             any(v.key == "records-file" for v in buggy))

    # And the guard's OWN purpose: the real repo tree must currently be clean,
    # in particular the exact action this bug was found in.
    real = check_tree(WORKFLOWS_DIR, ACTIONS_DIR)
    research_result_clean = not any(v.action == "research-result" for v in real)
    case("research-result/action.yml declares every key m20-exit-lever-sweep.yml "
         "passes it (the actual fix)", research_result_clean)

    print(f"check_action_input_wiring --self-test: {failures} failure(s)")
    return 1 if failures else 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--self-test" in argv:
        return _self_test()

    violations = check_tree(WORKFLOWS_DIR, ACTIONS_DIR)
    if not violations:
        print("action-input-wiring: clean — every local composite-action `with:` "
              "key is a declared input.")
        return 0

    print("::error::action-input-wiring: undeclared composite-action input(s) "
          "found. GitHub does not fail these — it warns and silently drops the "
          "value, which is how RQ-20260922-003/004 landed not_attempted results.")
    for v in violations:
        print(f"::error file={v.workflow}::step {v.step!r} passes undeclared "
              f"input {v.key!r} to ./.github/actions/{v.action}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
