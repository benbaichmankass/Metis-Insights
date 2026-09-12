#!/usr/bin/env python3
"""Every workflow step that renders the due-list must carry `GITHUB_TOKEN`.

THE DEFECT THIS EXISTS TO STOP RECURRING
----------------------------------------
`scripts/ops/render_due_list.py` collects NINE sources, and two of them —
``src_red_crons`` and ``src_unlanded_automation`` — read the GitHub Actions API.
With no token they return ``could_not_read``, which is the correct and honest
behaviour (``src_red_crons``'s own docstring: *"Without a token we say so; we
never report 'no red crons'"*).

**But the artifact is COMMITTED TO `main`, and the last writer wins.** Three
workflows run ``render_due_list.py --write``, and one of them shipped with no
``env:`` block at all — so a token-less render overwrote a token-carrying one and
landed a due-list whose two GitHub-backed sources read ``could_not_read``.

MEASURED 2026-09-12, POPULATION = the last 60 commits to ``docs/claude/DUE.json``
on ``main``:

    47 (78.3%)  error-feed-digest.yml   <- NO token, cron "35 * * * *" (HOURLY)
     6 (10.0%)  due-list.yml            <- token,    daily
     5 ( 8.3%)  probes.yml              <- token,    daily
     2 ( 3.3%)  other / manual

The token-less writer runs hourly against the others' daily crons, so it is not
merely one writer among three — it is the one that wins, and the live artifact
at 06:27:30Z duly read ``red_crons: could_not_read — no GITHUB_TOKEN``.

⚠️ WHY THAT MATTERS BEYOND TIDINESS. ``red_crons`` is the **F1 class** its own
docstring names — *"a nightly nobody is waiting on"* — and it is precisely what
would have surfaced ``replay-pregate-nightly``'s **77 consecutive red scheduled
runs over 2.5 months** (68 failure / 9 cancelled / 0 success ever), which reached
no register, no backlog row and no page. The instrument existed, was correct, and
was blind in production.

⚠️ AND THE ONE-LINE FIX IS NOT THE FIX. Adding the missing ``env:`` repairs
today's three writers and does nothing about the fourth, which is how this
happened: nothing tied "renders the due-list" to "must carry a token". A guard is
the only thing that survives the next workflow being added.

WHAT IS CHECKED
---------------
For every ``.github/workflows/*.yml`` step whose ``run:`` invokes
``render_due_list.py`` in a WRITING or CHECKING mode, the step must resolve
``GITHUB_TOKEN`` — from its own ``env:``, its job's, or the workflow's.

⚠️ ``--self-test`` IS DELIBERATELY EXEMPT. It collects nothing and touches no
source, so requiring a token there would train authors to paste one in wherever
the guard complains — which is how a guard becomes cheaper to satisfy than to
understand.

⚠️ THE VALUE IS NOT INSPECTED, ONLY THE BINDING. This guard cannot tell a real
token from ``GITHUB_TOKEN: ""``, and says so rather than implying otherwise: it
establishes that the step was WIRED, which is the thing an author forgets, and
never that the token WORKS. That is the honest half — the live half is the
``sources.red_crons.state`` field in the committed ``DUE.json``.

Run standalone, or ``--self-test`` to plant each defect and prove it fails.
"""
from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW_GLOB = ".github/workflows/*.yml"
RENDERER = "render_due_list.py"
TOKEN = "GITHUB_TOKEN"

# A step that only self-tests collects nothing — see the docstring.
_EXEMPT_RE = re.compile(r"render_due_list\.py\s+--self-test\b")
# Anything else that runs the renderer is a collecting invocation.
_INVOKES_RE = re.compile(r"render_due_list\.py\b")


def _env_names(*blocks) -> set:
    names = set()
    for b in blocks:
        if isinstance(b, dict):
            names |= {str(k) for k in b}
    return names


def collecting_steps(doc: dict):
    """Yield (job_name, step_index, step, job_env) for collecting invocations."""
    for job_name, job in (doc.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        for i, step in enumerate(job.get("steps") or []):
            if not isinstance(step, dict):
                continue
            run = step.get("run")
            if not isinstance(run, str) or not _INVOKES_RE.search(run):
                continue
            # Strip the exempt invocations; if nothing is left, the step is
            # self-test-only. Done by REMOVAL rather than by "does it contain
            # --self-test", so a step that self-tests AND writes is still caught.
            remainder = _INVOKES_RE.sub(
                lambda m: "", _EXEMPT_RE.sub("", run))
            if RENDERER not in run:
                continue
            if not _EXEMPT_RE.sub("", run).count(RENDERER):
                continue
            yield job_name, i, step, job.get("env")


def check(root: Path) -> list:
    fails = []
    wfs = sorted((root / ".github" / "workflows").glob("*.yml"))
    if not wfs:
        return ["NOTHING WAS CHECKED — no .github/workflows/*.yml found, so this "
                "run's silence is a vacuous verdict over an empty population, "
                "not a pass."]
    seen = 0
    for wf in wfs:
        try:
            doc = yaml.safe_load(wf.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            fails.append(f"{wf.name}: unreadable YAML ({exc}) — we could not "
                         f"look, which is not a pass")
            continue
        if not isinstance(doc, dict):
            continue
        wf_env = doc.get("env")
        for job_name, i, step, job_env in collecting_steps(doc):
            seen += 1
            if TOKEN in _env_names(step.get("env"), job_env, wf_env):
                continue
            name = step.get("name") or f"step #{i}"
            fails.append(
                f"{wf.name}: job `{job_name}` step `{name}` runs "
                f"{RENDERER} without {TOKEN} in scope. The renderer's "
                f"`src_red_crons` and `src_unlanded_automation` read the Actions "
                f"API; with no token they return `could_not_read` — correctly — "
                f"and this step then COMMITS that refusal to main, overwriting a "
                f"token-carrying render. Measured over the last 60 commits to "
                f"docs/claude/DUE.json, 78.3% were written by the one workflow "
                f"that had no token. Add `env:\\n  {TOKEN}: "
                f"${{{{ secrets.{TOKEN} }}}}` to the step.")
    if not seen:
        fails.append(
            f"NOTHING WAS CHECKED — no workflow step invoking {RENDERER} was "
            f"found across {len(wfs)} workflow(s). Either the renderer was "
            f"renamed (fix this guard) or the due-list has no producer at all "
            f"(a much larger finding). A guard with an empty population is not "
            f"a clean guard.")
    return fails


# ─────────────────────────── self-test ────────────────────────────────────────
_BASE = """name: demo
on: {schedule: [{cron: "0 * * * *"}]}
jobs:
  render:
    runs-on: ubuntu-latest
    steps:
      - name: Self-test
        run: python3 scripts/ops/render_due_list.py --self-test
      - name: Render
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: python3 scripts/ops/render_due_list.py --write
"""


def _sandbox(tmp: Path, body: str = _BASE) -> Path:
    root = tmp / "repo"
    (root / ".github/workflows").mkdir(parents=True)
    (root / ".github/workflows/demo.yml").write_text(body, encoding="utf-8")
    return root


def self_test() -> int:
    bad = 0
    with tempfile.TemporaryDirectory() as td:
        clean = check(_sandbox(Path(td)))
        if clean:
            print("::error::self-test FAILED — the clean fixture does not pass:")
            for f in clean:
                print("  - " + f)
            return 1
    print("self-test: clean fixture passes (the positive control)")

    plants = {
        # THE ACTUAL DEFECT: the env block is simply absent.
        "the env block is missing entirely": _BASE.replace(
            "        env:\n          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n", ""),
        # The env exists but carries something else — the near-miss.
        "env present but names a different variable": _BASE.replace(
            "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}",
            "DIAG_READ_TOKEN: ${{ secrets.DIAG_READ_TOKEN }}"),
        # A step that self-tests AND writes must still be caught — otherwise
        # "contains --self-test" would be a free exemption.
        "a step that self-tests AND writes, with no token": _BASE.replace(
            "        env:\n          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n", ""
        ).replace(
            "        run: python3 scripts/ops/render_due_list.py --write",
            "        run: |\n"
            "          python3 scripts/ops/render_due_list.py --self-test\n"
            "          python3 scripts/ops/render_due_list.py --write"),
        # --check collects too; it must not be a loophole.
        "a --check invocation with no token": _BASE.replace(
            "        env:\n          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n", ""
        ).replace("--write", "--check"),
        # The renderer invoked from a multi-line script with other commands.
        "buried in a multi-line run block with no token": _BASE.replace(
            "        env:\n          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n", ""
        ).replace(
            "        run: python3 scripts/ops/render_due_list.py --write",
            "        run: |\n          set -euo pipefail\n          echo hi\n"
            "          python3 scripts/ops/render_due_list.py --write\n"
            "          echo bye"),
        # An unreadable workflow is `we could not look`, never a pass.
        "an unreadable workflow": _BASE + "\n  : [ bad yaml\n",
        # The empty-population case: a guard that checks nothing is not clean.
        "no step invokes the renderer at all": _BASE.replace(
            "render_due_list.py", "something_else.py"),
    }
    for label, body in plants.items():
        with tempfile.TemporaryDirectory() as td:
            fails = check(_sandbox(Path(td), body))
            if not fails:
                print(f"::error::self-test FAILED — planted '{label}' and the "
                      f"guard still passed. Its failure path is broken, so a "
                      f"green means nothing.")
                bad += 1
            else:
                print(f"self-test: '{label}' correctly caught")

    # The two POSITIVE controls that must stay quiet, so the guard is not simply
    # failing everything: a token inherited from the JOB, and from the WORKFLOW.
    for label, body in {
        "token inherited from the job": _BASE.replace(
            "    runs-on: ubuntu-latest\n",
            "    runs-on: ubuntu-latest\n    env:\n      GITHUB_TOKEN: x\n"
        ).replace("        env:\n          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n", ""),
        "token inherited from the workflow": _BASE.replace(
            "jobs:\n", "env:\n  GITHUB_TOKEN: x\njobs:\n"
        ).replace("        env:\n          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n", ""),
        # ⚠️ An EXTRA self-test-only step must not trip the guard — and the
        # collecting step is deliberately LEFT IN. The first version of this
        # control replaced the collecting step with a self-test, which made the
        # fixture's population EMPTY and the guard correctly refused it with
        # "NOTHING WAS CHECKED". The control was wrong, not the guard; recorded
        # because a malformed positive control is how a real one gets deleted.
        "an extra self-test-only step beside a token-carrying render":
            _BASE + "      - name: Another self-test\n"
                    "        run: python3 scripts/ops/render_due_list.py --self-test\n",
    }.items():
        with tempfile.TemporaryDirectory() as td:
            fails = check(_sandbox(Path(td), body))
            if fails:
                print(f"::error::self-test FAILED — the positive control "
                      f"'{label}' was rejected: {fails[0][:160]}")
                bad += 1
            else:
                print(f"self-test: positive control '{label}' stays quiet")

    if bad:
        return 1
    print(f"self-test OK — {len(plants)} planted defects fail the guard and "
          f"3 positive controls stay quiet")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()

    fails = check(REPO)
    if fails:
        print("::error::due-list-token-guard FAILED")
        for f in fails:
            print("  - " + f)
        return 1
    print("due-list-token: OK — every step that renders the due-list carries "
          "GITHUB_TOKEN (the BINDING is checked, never the token's validity)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
