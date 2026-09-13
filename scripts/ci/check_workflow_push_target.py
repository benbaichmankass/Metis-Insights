#!/usr/bin/env python3
"""workflow-push-target-guard — a workflow must not push straight at a protected branch.

WHY THIS IS MACHINERY AND NOT ANOTHER COMMENT
---------------------------------------------
`main` is branch-protected, so a workflow's `git push origin HEAD:main` is
declined by the branch-protection hook (GH006) and the run's whole artifact is
discarded. This repo has now fixed that defect **seven separate times, one
workflow at a time** — the fix comments are still in the files:
`session-reaper.yml`, `research-queue-dispatch.yml`, `gpu-burst-train.yml`,
`reconcile-open-prs.yml`, `m20-exit-lever-sweep.yml`,
`trainer-offload-train.yml`, `replay-pregate-nightly.yml` — and an eighth,
`sunset-pass.yml`, was still live on 2026-09-12 and is fixed in the same change
that adds this guard.

⚠️ **TWO HAND-WRITTEN CENSUSES OF THIS CLASS HAVE ALREADY BEEN WRONG**, which is
the actual argument for a guard rather than a ninth careful fix:

  * `session-reaper.yml`'s own comment states it was *"the ONLY workflow in the
    repo pushing straight to main"* on 2026-09-02. `replay-pregate-nightly.yml`
    was doing it on that date and for ten days after.
  * `BL-20260827-EIGHTEEN-EVIDENCE-WORKFLOWS-UPLOAD-AND-LAND-NOTHING` classifies
    `training-rerun-5m` as one of four workflows that **LANDS**, on a predicate
    that matches the PRESENCE of a `git push` idiom. Presence is not landing: a
    push a protected branch declines matches that predicate exactly.

A census re-measured on every PR cannot go stale between the day it is written
and the day it is quoted. That is the whole of the argument.

THE STATES ARE NOT COLLAPSED
----------------------------
`always_default`        the push target is the default branch on EVERY trigger
                        this workflow has — it can never succeed. **FAILS.**
`conditional_default`   the target is the default branch only on SOME triggers
                        (typically `workflow_dispatch`), while the workflow's
                        ordinary path is a feature branch where the push is
                        correct and intended. **REPORTED, never failed** — see
                        the note below.
`retargets_off_default` the workflow TESTS its own target against the default
                        branch and reassigns it. Detected by reading the shell,
                        never by a comment claiming it.
`off_default_only`      the target can never be the default branch.
`ungradeable`           **we could not look** — unparseable YAML, or a push
                        target this script cannot resolve. Never folded into
                        `off_default_only`, which is a positive finding of
                        safety and is not what an unreadable file supports.

⚠️ **WHY `conditional_default` DOES NOT FAIL.** The relay workflows
(`pr-opener`, `board-post`, `ci-settled`, `board-rotate`, …) fire on a push to a
`claude/**` branch and push their result back to that same branch, where
`github.ref_name` is the branch and the push is both legal and the design. The
default-branch case is reachable only by dispatching them from `main`, which is
a path none of them has ever been run on. Failing them would be failing correct
code for a latent path — and a guard that fails correct code is a guard that
gets switched off. They are printed with a count so the class stays visible.

⚠️ **WHAT THIS GUARD DOES NOT ESTABLISH.** It reads the WORKFLOW, not GitHub's
branch-protection settings, so it assumes the default branch is protected. That
assumption is evidenced (two measured GH006 refusals, and 300 of the 300 newest
commits on `main` carry a `(#N)` squash suffix, i.e. zero direct pushes) but it
is an assumption, and if protection were ever removed this guard would report a
defect that is no longer one. It also cannot see a push performed by a composite
action or a third-party action; only `run:` shell is read.

THE OVERRIDE IS VERIFIED, NOT PRESENCE-ONLY
-------------------------------------------
`# push-target-ok: <token> — <reason>` excuses a single push line, and `<token>`
must be an identifier that actually appears ELSEWHERE in the workflow. A marker
naming nothing real is itself a failure. This is the direct lesson from
`new-table-wiring-guard`, whose presence-only marker made the cheapest way to
silence a real finding *naming a table that does not exist*.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

try:
    import yaml
except ModuleNotFoundError:                                   # pragma: no cover
    print("workflow-push-target: PyYAML unavailable — ungradeable, not OK")
    sys.exit(1)

WF_DIR = pathlib.Path(".github/workflows")
DEFAULT_BRANCH = "main"

# Events whose GITHUB_REF / github.ref_name is ALWAYS the default branch.
ALWAYS_DEFAULT_EVENTS = {"schedule"}
# Events that run on the default branch unless explicitly pointed elsewhere.
SOMETIMES_DEFAULT_EVENTS = {
    "workflow_dispatch", "issues", "issue_comment", "repository_dispatch",
    "label", "milestone", "watch", "create", "delete", "gollum",
}

PUSH_RE = re.compile(r"git\s+push\b[^\n]*")
# A target that IS the default branch, literally.
LITERAL_DEFAULT_RE = re.compile(
    rf"HEAD:(refs/heads/)?{DEFAULT_BRANCH}\b|push\s+(-\S+\s+)*origin\s+{DEFAULT_BRANCH}\b")
# A target that RESOLVES to whatever ref the run is on.
REF_NAME_RE = re.compile(r"ref_name|GITHUB_REF_NAME")
# A target held in a shell variable.
VAR_TARGET_RE = re.compile(r"HEAD:\"?(refs/heads/)?\$\{?(\w+)\}?")
# A target held in a GitHub EXPRESSION (`${{ steps.x.outputs.ref }}`,
# `${{ inputs.ref }}`). This script cannot resolve what it evaluates to, so the
# honest grade is UNGRADEABLE unless the step is gated off the default branch.
#
# ⚠️ ADDED AFTER A MEASURED MISS, and the miss is the argument for the whole
# expression branch. The first version of this guard matched `github.ref_name`
# and `$VAR` only, and was tested against the REAL pre-fix
# `replay-pregate-nightly.yml` — one of the two instances that motivated it —
# across six commits of its history. It returned `always_default 0` on every
# one. Its target was `HEAD:${{ steps.params.outputs.ref }}`, which neither
# pattern matched, so the guard read a live GH006 defect as clean.
EXPR_TARGET_RE = re.compile(r"HEAD:\"?(refs/heads/)?\$\{\{\s*([^}]+?)\s*\}\}")
# A step-level `if:` that runs the step ONLY when the ref is not the default
# branch. That is what makes the surviving direct push in
# `replay-pregate-nightly.yml` correct, and it is read from the condition
# rather than from the comment beside it.
STEP_GUARD_OFF_DEFAULT_RE = re.compile(
    rf"!=\s*'{DEFAULT_BRANCH}'|!=\s*\"{DEFAULT_BRANCH}\"")
OVERRIDE_RE = re.compile(r"#\s*push-target-ok:\s*(\S+)\s*—\s*(.+)")

ALWAYS, COND, RETARGET, OFF, UNGRADEABLE = (
    "always_default", "conditional_default", "retargets_off_default",
    "off_default_only", "ungradeable")


def _events(doc) -> set[str]:
    on = doc.get(True, doc.get("on")) if isinstance(doc, dict) else None
    if isinstance(on, str):
        return {on}
    if isinstance(on, list):
        return set(on)
    if isinstance(on, dict):
        return set(on.keys())
    return set()


def _push_branches(doc) -> list[str]:
    on = doc.get(True, doc.get("on")) if isinstance(doc, dict) else None
    if isinstance(on, dict) and isinstance(on.get("push"), dict):
        return list(on["push"].get("branches") or [])
    return []


def _retargets(text: str, var: str) -> bool:
    """Does the shell TEST `var` against the default branch and reassign it?

    Read from the code, never from a comment. The shape both existing remedies
    use is:  if [ "$TARGET" = "${DEFAULT_BRANCH:-main}" ]; then TARGET="claude/..."
    """
    if not var:
        return False
    guard = re.compile(
        rf'\[\s*"?\$\{{?{var}\}}?"?\s*=\s*"?\$\{{DEFAULT_BRANCH:-{DEFAULT_BRANCH}\}}"?\s*\]'
        rf'|\[\s*"?\$\{{?{var}\}}?"?\s*=\s*"?{DEFAULT_BRANCH}"?\s*\]')
    if not guard.search(text):
        return False
    # ...and the branch body must actually REASSIGN it; a test that only warns
    # leaves the push pointed at the protected branch.
    after = text[guard.search(text).end():guard.search(text).end() + 400]
    return re.search(rf'{var}\s*=', after) is not None


def _uncommented_push_lines(text: str) -> list[tuple[int, str]]:
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        m = PUSH_RE.search(line)
        if m:
            out.append((n, m.group(0).strip()))
    return out


def _guarded_push_statements(doc, text: str) -> set[str]:
    """Push statements living in a step whose `if:` runs it only off the default
    branch. Read from the parsed step, never from a nearby comment."""
    guarded: set[str] = set()
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    if not isinstance(jobs, dict):
        return guarded
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        for step in (job.get("steps") or []):
            if not isinstance(step, dict):
                continue
            cond, run = str(step.get("if") or ""), str(step.get("run") or "")
            if not run or not STEP_GUARD_OFF_DEFAULT_RE.search(cond):
                continue
            for line in run.splitlines():
                if line.lstrip().startswith("#"):
                    continue
                m = PUSH_RE.search(line)
                if m:
                    guarded.add(m.group(0).strip())
    return guarded


def grade(path: pathlib.Path) -> tuple[str, list[str]]:
    """Return (state, notes) for one workflow file."""
    text = path.read_text(encoding="utf-8")
    pushes = _uncommented_push_lines(text)
    if not pushes:
        return OFF, []
    try:
        doc = yaml.safe_load(text)
    except Exception as exc:                                  # noqa: BLE001
        return UNGRADEABLE, [f"YAML did not parse: {exc}"]
    if not isinstance(doc, dict):
        return UNGRADEABLE, ["top level is not a mapping"]

    evs = _events(doc)
    if not evs:
        return UNGRADEABLE, ["no `on:` block could be read"]
    always = evs & ALWAYS_DEFAULT_EVENTS
    sometimes = evs & SOMETIMES_DEFAULT_EVENTS
    push_on_default = DEFAULT_BRANCH in _push_branches(doc)
    # A workflow whose triggers ALL put it on the default branch.
    non_default_possible = bool(
        (evs - ALWAYS_DEFAULT_EVENTS - SOMETIMES_DEFAULT_EVENTS)
        or [b for b in _push_branches(doc) if b != DEFAULT_BRANCH]
        or ("push" in evs and not _push_branches(doc)))

    guarded = _guarded_push_statements(doc, text)

    notes: list[str] = []
    worst = OFF
    for lineno, stmt in pushes:
        if stmt in guarded:
            notes.append(f"  L{lineno}: step is gated off `{DEFAULT_BRANCH}` by its own `if:`")
            continue
        ctx = "\n".join(text.splitlines()[max(0, lineno - 25):lineno])
        ov = OVERRIDE_RE.search(ctx)
        if ov:
            token = ov.group(1)
            body = text.replace(ov.group(0), "")
            if re.search(re.escape(token), body):
                notes.append(f"  L{lineno}: excused — {ov.group(2).strip()}")
                continue
            notes.append(f"  L{lineno}: ⚠️ override names `{token}`, which appears "
                         f"nowhere else in this workflow — the marker is not evidence")
            worst = ALWAYS
            continue

        targets_default = False
        why = ""
        if LITERAL_DEFAULT_RE.search(stmt):
            targets_default, why = True, f"pushes literally at `{DEFAULT_BRANCH}`"
        elif REF_NAME_RE.search(stmt) and (always or sometimes or push_on_default):
            targets_default, why = True, "target is `github.ref_name`"
        else:
            em = EXPR_TARGET_RE.search(stmt)
            vm = VAR_TARGET_RE.search(stmt)
            if em and (always or sometimes or push_on_default):
                # We cannot evaluate a GitHub expression from here. `we could
                # not look` is its own state and is NOT folded into a pass.
                notes.append(
                    f"  L{lineno}: target is the expression `{em.group(2)}`, which this "
                    f"script cannot resolve, and the step is not gated off "
                    f"`{DEFAULT_BRANCH}` — ungradeable, not clean")
                worst = UNGRADEABLE
                continue
            if vm and (always or sometimes or push_on_default):
                var = vm.group(2)
                if _retargets(text, var):
                    notes.append(f"  L{lineno}: retargets off the default branch "
                                 f"(`${var}` is tested and reassigned)")
                    if worst == OFF:
                        worst = RETARGET
                    continue
                targets_default, why = True, f"target is `${var}`, never retargeted"
        if not targets_default:
            continue

        if always and not non_default_possible:
            notes.append(f"  L{lineno}: {why}; every trigger ({','.join(sorted(evs))}) "
                         f"runs on `{DEFAULT_BRANCH}` — this push can NEVER succeed")
            worst = ALWAYS
        elif always or sometimes or push_on_default:
            if worst in (OFF, RETARGET):
                worst = COND
            notes.append(f"  L{lineno}: {why}; reachable on `{DEFAULT_BRANCH}` via "
                         f"{','.join(sorted(always | sometimes)) or 'push'}")
    return worst, notes


def run(root: pathlib.Path) -> int:
    wf = root / WF_DIR
    if not wf.is_dir():
        print(f"workflow-push-target: {WF_DIR} not found — ungradeable, not OK")
        return 1
    files = sorted(wf.glob("*.yml")) + sorted(wf.glob("*.yaml"))
    buckets: dict[str, list[tuple[str, list[str]]]] = {
        ALWAYS: [], COND: [], RETARGET: [], UNGRADEABLE: []}
    for f in files:
        state, notes = grade(f)
        if state in buckets:
            buckets[state].append((f.name, notes))

    print(f"workflow-push-target — POPULATION: {len(files)} workflow file(s) under {WF_DIR}")
    for state, label in ((ALWAYS, "ALWAYS pushes at the protected default branch"),
                         (UNGRADEABLE, "UNGRADEABLE (we could not look)"),
                         (COND, "reachable on the default branch only via a dispatch-style trigger"),
                         (RETARGET, "retargets off the default branch (correct)")):
        rows = buckets[state]
        print(f"  {state:22} {len(rows):3}  — {label}")
        if state in (ALWAYS, UNGRADEABLE, COND):
            for name, notes in rows:
                print(f"    {name}")
                for n in notes:
                    print(f"    {n}")

    bad = buckets[ALWAYS] + buckets[UNGRADEABLE]
    if bad:
        print(f"\nworkflow-push-target: FAIL — {len(bad)} workflow(s). "
              f"A push a protected branch declines discards the whole run's output; "
              f"route it through .github/actions/commit-to-main, or retarget off "
              f"the default branch the way trainer-offload-train.yml does.")
        return 1
    print("\nworkflow-push-target: OK — no workflow pushes at a branch that will decline it.")
    return 0


def self_test() -> int:
    """Failure path, with a POSITIVE CONTROL. A guard whose failure branch is
    never exercised is indistinguishable from one that always passes."""
    import tempfile

    def mk(body: str) -> pathlib.Path:
        d = pathlib.Path(tempfile.mkdtemp())
        (d / ".github/workflows").mkdir(parents=True)
        (d / ".github/workflows/x.yml").write_text(body, encoding="utf-8")
        return d

    cases = [
        ("a schedule-only workflow pushing at ref_name is ALWAYS a finding",
         'on:\n  schedule:\n    - cron: "0 5 * * 1"\njobs:\n  j:\n    steps:\n'
         '      - run: |\n          git push origin "HEAD:${GITHUB_REF_NAME}"\n', ALWAYS),
        ("...and a literal HEAD:main is the same finding",
         'on:\n  schedule:\n    - cron: "0 5 * * 1"\njobs:\n  j:\n    steps:\n'
         '      - run: |\n          git push origin HEAD:main\n', ALWAYS),
        ("a relay pushing back to its own claude/** branch is NOT a finding",
         'on:\n  push:\n    branches: ["claude/**"]\njobs:\n  j:\n    steps:\n'
         '      - run: |\n          git push origin "HEAD:${GITHUB_REF_NAME}"\n', OFF),
        ("a dispatch-reachable relay is reported, never failed",
         'on:\n  push:\n    paths: ["automation/x/**"]\n  workflow_dispatch:\njobs:\n'
         '  j:\n    steps:\n      - run: |\n          git push origin "HEAD:${GITHUB_REF_NAME}"\n', COND),
        ("a workflow that tests its target against main and reassigns it is CORRECT",
         'on:\n  workflow_dispatch:\njobs:\n  j:\n    steps:\n      - run: |\n'
         '          TARGET="${GITHUB_REF_NAME}"\n'
         '          if [ "$TARGET" = "${DEFAULT_BRANCH:-main}" ]; then\n'
         '            TARGET="claude/inbox"\n          fi\n'
         '          git push origin "HEAD:${TARGET}"\n', RETARGET),
        ("...but a test that only WARNS and never reassigns is still a finding",
         'on:\n  schedule:\n    - cron: "0 5 * * 1"\njobs:\n  j:\n    steps:\n      - run: |\n'
         '          TARGET="${GITHUB_REF_NAME}"\n'
         '          if [ "$TARGET" = "${DEFAULT_BRANCH:-main}" ]; then\n'
         '            echo "::warning::on main"\n          fi\n'
         '          git push origin "HEAD:${TARGET}"\n', ALWAYS),
        ("a commented-out push is not a finding",
         'on:\n  schedule:\n    - cron: "0 5 * * 1"\njobs:\n  j:\n    steps:\n'
         '      - run: |\n          # git push origin HEAD:main\n          echo hi\n', OFF),
        ("unparseable YAML is UNGRADEABLE, never a pass",
         'on: [schedule\njobs: {{{\n  - run: git push origin HEAD:main\n', UNGRADEABLE),
        ("an override naming a token that appears nowhere is itself a finding",
         'on:\n  schedule:\n    - cron: "0 5 * * 1"\njobs:\n  j:\n    steps:\n      - run: |\n'
         '          # push-target-ok: no_such_thing — trust me\n'
         '          git push origin HEAD:main\n', ALWAYS),
        ("an UNRESOLVABLE expression target is UNGRADEABLE, never a pass "
         "(the real replay-pregate-nightly miss)",
         'on:\n  schedule:\n    - cron: "0 5 * * 1"\njobs:\n  j:\n    steps:\n'
         '      - run: |\n          git push origin "HEAD:${{ steps.params.outputs.ref }}"\n',
         UNGRADEABLE),
        ("...but the SAME push is clean when its step is gated off main",
         'on:\n  schedule:\n    - cron: "0 5 * * 1"\njobs:\n  j:\n    steps:\n'
         "      - if: steps.params.outputs.ref != 'main'\n"
         '        run: |\n          git push origin "HEAD:${{ steps.params.outputs.ref }}"\n',
         OFF),
        ("an override naming something real excuses the line",
         'on:\n  schedule:\n    - cron: "0 5 * * 1"\njobs:\n  j:\n    steps:\n      - run: |\n'
         '          # push-target-ok: SANDBOX_REMOTE — pushes to a fixture, not to origin\n'
         '          SANDBOX_REMOTE=1\n          git push origin HEAD:main\n', OFF),
    ]
    ok = True
    print("workflow-push-target self-test")
    for label, body, expect in cases:
        got, _ = grade(mk(body) / ".github/workflows/x.yml")
        good = got == expect
        ok &= good
        print(f"  {label}: {'PASS' if good else f'FAIL (expected {expect}, got {got})'}")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--root", default=".")
    a = ap.parse_args()
    return self_test() if a.self_test else run(pathlib.Path(a.root))


if __name__ == "__main__":
    raise SystemExit(main())
