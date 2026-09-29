#!/usr/bin/env python3
"""Fail a workflow that pushes back to its triggering ref while triggered on an
unfiltered `push`.

WHY (PI-20260929-SASEC-0003): training-rerun-5m.yml triggered on `push` with a
`paths:` filter that included the workflow's own file and no `branches:`
filter, and its last step ran `git push origin HEAD:${{ github.ref_name }}`.
Any PR branch that edited the file (or merged main after #14180 did) fired a
60-minute job that pushed a results commit onto the PR.

RULE: if `on.push` has no `branches:` filter and any step's `run` does a
`git push` targeting the triggering ref (github.ref_name / github.ref /
github.head_ref / GITHUB_REF_NAME), then every `on.push.paths` entry must sit
under `automation/`. Those are the request-file relays (board-post, pr-opener,
...) that are DESIGNED to run on the branch that carries the request. Anything
else must add `branches: [main]`.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / ".github" / "workflows"
_PUSH = re.compile(r"git\s+push\b[^\n]*", re.I)
_REF = re.compile(r"github\.ref_name|github\.ref\b|github\.head_ref|GITHUB_REF_NAME|GITHUB_REF\b")


def check_doc(doc: dict, name: str) -> list[str]:
    on = doc.get(True, doc.get("on"))
    if on == "push":
        push: dict | None = {}
    elif isinstance(on, list):
        push = {} if "push" in on else None
    elif isinstance(on, dict) and "push" in on:
        push = on["push"] or {}
    else:
        return []
    if push is None or push.get("branches"):
        return []
    pushes_back = False
    for job in (doc.get("jobs") or {}).values():
        for step in (job or {}).get("steps") or []:
            for m in _PUSH.finditer(str(step.get("run", ""))):
                if _REF.search(m.group(0)):
                    pushes_back = True
    if not pushes_back:
        return []
    paths = push.get("paths") or []
    if paths and all(str(x).startswith("automation/") for x in paths):
        return []
    return [f"{name}: on.push has no `branches:` filter, but a step git-pushes to the "
            f"triggering ref (paths={paths or 'ANY'}). Add `branches: [main]` "
            f"(PI-20260929-SASEC-0003)."]


def check_file(path: Path) -> list[str]:
    return check_doc(yaml.safe_load(path.read_text()) or {}, path.name)


def self_test() -> int:
    base = """
on:
  push:
    {extra}
    paths: {paths}
jobs:
  j:
    steps:
      - run: git push origin HEAD:${{{{ github.ref_name }}}}
"""
    cases = [
        ("PLANTED: unfiltered push + push-back + non-automation path", "", "['src/x.py']", 1),
        ("branches: [main] passes", "branches: [main]", "['src/x.py']", 0),
        ("automation/ relay path passes", "", "['automation/board-posts/**']", 0),
        ("no paths at all is flagged", "", "null", 1),
    ]
    bad = 0
    for label, extra, paths, want in cases:
        got = len(check_doc(yaml.safe_load(base.format(extra=extra, paths=paths)), "planted.yml"))
        ok = got == want
        bad += not ok
        print(f"  {'PASS' if ok else 'FAIL'} — {label} (violations={got}, want {want})")
    return 1 if bad else 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    out = [v for p in sorted([*WORKFLOWS_DIR.glob("*.yml"), *WORKFLOWS_DIR.glob("*.yaml")])
           for v in check_file(p)]
    for v in out:
        print("FAIL —", v)
    print(f"push-back-trigger: {'FAIL' if out else 'PASS'} ({len(out)} violation(s))")
    return 1 if out else 0


if __name__ == "__main__":
    sys.exit(main())
