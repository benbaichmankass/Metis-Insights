#!/usr/bin/env python3
"""Find CAPABILITIES that were built and never wired to anything that runs them.

WHY THIS EXISTS
---------------
Operator directive 2026-08-20: *"we don't keep building things out half way and
then leaving them to rust while the system chugs along with bad structure."*

This repo has the pattern repeatedly, and each instance was found by accident
months later rather than by a check:

  * `scripts/ops/trainer_dataset_gc.py` — the retention tool for a 12 G dataset
    tree. No caller in `run_training_cycle.sh`, no timer, **0 mentions across
    7,442 cycle-log rows**, while the trainer disk sat at **93 %**
    (measured 2026-08-20).
  * `exchange_fills_ib.closed_pnl_from_fills` — **zero production callers**, so
    IBKR's own realizedPNL is pulled hourly and never read; 0 of 33 `ib_paper`
    closed trades ever carried a broker-sourced pnl
    (`BL-20260818-IB-BROKER-PNL-READER-HAS-NO-CALLER`).
  * `RiskManager.report()["exposure"]` — emitted always so an operator could see
    the multiple; shipped with **no reader** at all (#8665).
  * `exit_price_source` — written in 12 files, branched on in one, and the gap
    produced a "−$6,358 exit leak" that did not exist.

`provenance-consumer-guard` already catches this for **declared provenance
keys** and `check_selftest_wiring.py` for **registered guard self-tests**. This
generalises the same question to **executable tools**: is there anything that
actually runs this?

WHAT COUNTS AS WIRED
--------------------
A script is wired if it is referenced by something that can execute it: a
workflow, a systemd unit, another script, `src/`, `run_guards.py`, `Makefile`,
or a documented runbook. A script referenced ONLY by itself and its tests is
presumed a corpse **or must be justified in writing** — the audit skill's
disposition flip.

THE ESCAPE HATCH IS DELIBERATE AND NARROW
-----------------------------------------
A tool that is genuinely manual-only declares it in its own docstring:

    # wiring: manual-only — <why, and who runs it when>

Presence of the marker is not enough; it must carry a reason after the dash.
This repo learned that lesson from `new-table-wiring-guard`, whose
presence-only marker made the cheapest way to silence a real finding *naming a
table that does not exist*.

    python3 scripts/ci/check_unwired_artifacts.py --self-test
    python3 scripts/ci/check_unwired_artifacts.py            # standing audit
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MARKER = re.compile(r"#[ \t]*wiring:[ \t]*manual-only[ \t]*[-—][ \t]*(\S[^\n]{9,})", re.I)

# Places that can actually RUN a script.
RUNNER_GLOBS = (
    ".github/workflows/*.yml", ".github/workflows/*.yaml",
    "deploy/**/*.service", "deploy/**/*.timer", "deploy/**/*.sh",
    "scripts/**/*.sh", "scripts/**/*.py", "src/**/*.py",
    "Makefile", "*.md", "docs/**/*.md", ".claude/skills/**/*.md",
)


_PY_DOCSTRING = re.compile(r'("""|\'\'\')(?:.|\n)*?\1')
_HASH_COMMENT = re.compile(r"#[^\n]*")


def _strip_noncode(text: str, suffix: str) -> str:
    """Remove comments/docstrings so a MENTION is not mistaken for WIRING.

    This checker was defeated by its own documentation TWICE. First its own
    docstring cited `trainer_dataset_gc.py` as the motivating example, which
    made that tool read as wired. Excluding this file fixed that instance and
    not the class: the next commit added a comment in
    `render_system_report.py` citing the same tool, and it read as wired again.

    A reference only counts if it is in EXECUTABLE position. Prose about a tool
    — a comment explaining why it matters, a docstring citing it as an example —
    is exactly what a heavily-documented repo produces, and it is not a runner.

    Approximate by design: a `#` inside a string literal is stripped too. That
    errs toward FLAGGING (fewer references seen ⇒ more findings), which is the
    safe direction for a corpse-hunter — a false positive costs a look, a false
    negative is a capability rusting unnoticed. Markdown is left intact; prose
    is the whole point of a doc, and the docs-only branch handles it.
    """
    if suffix == ".md":
        return text
    if suffix == ".py":
        text = _PY_DOCSTRING.sub(" ", text)
    return _HASH_COMMENT.sub(" ", text)


def _runner_corpus(root: Path):
    """(text, path) for everything that could reference a tool."""
    seen, out = set(), []
    for g in RUNNER_GLOBS:
        for f in root.glob(g):
            if not f.is_file() or f in seen:
                continue
            seen.add(f)
            try:
                out.append((_strip_noncode(f.read_text(errors="ignore"), f.suffix), f))
            except Exception:
                pass
    return out


def scan(root: Path, targets):
    """One pass over the corpus, not one pass per target.

    The naive form is O(targets x corpus) — ~400 tools against ~1,900 runner
    files timed out past 120 s, which is not shippable as a CI guard. Instead
    build a single {stem -> referencing files} index by scanning each runner
    once for every tool stem it mentions.
    """
    self_path = Path(__file__).resolve()
    by_stem = {}
    for t in targets:
        by_stem.setdefault(t.stem, []).append(t)
    stems = set(by_stem)
    # one alternation over every stem, matched as `<stem>.py`
    if not stems:
        return []
    pat = re.compile(r"\b(" + "|".join(sorted(map(re.escape, stems))) + r")\.py\b")

    refs = {st: [] for st in stems}
    for text, f in _runner_corpus(root):
        if f.resolve() == self_path:
            continue    # belt-and-braces; _strip_noncode is what actually fixes this
        fr = f.relative_to(root).as_posix()
        if fr.startswith("tests/"):
            continue                       # a test is not a runner
        own_stems = {t.stem for t in by_stem.get(f.stem, []) if t == f}
        for m in set(pat.findall(text)):
            if m in own_stems:
                continue                   # its own file never counts
            refs[m].append(fr)

    findings = []
    for t in targets:
        rel = t.relative_to(root).as_posix()
        try:
            own = t.read_text(errors="ignore")
        except Exception:
            continue
        if MARKER.search(own):
            continue                       # declared manual-only WITH a reason
        r = [x for x in refs.get(t.stem, []) if x != rel]
        if not r:
            findings.append((rel, "no runner references it at all"))
        elif all(x.endswith(".md") for x in r):
            findings.append((rel, "referenced ONLY by docs (%s) — documented, "
                                  "but nothing runs it" % ", ".join(sorted(r)[:3])))
    return findings


def added_targets(base: str, root: Path, dirs) -> list:
    """`*.py` files this change ADDS under *dirs* — the diff-scoped population.

    ADDED, not merely changed. The whole reason this guard shipped
    self-test-only is that the repo already carries ~161 unwired tools, and a
    guard that fails every PR for pre-existing debt is the desensitized alarm
    this repo names as a P1 in its own right. Judging only what a change
    INTRODUCES makes it blockable without that: the debt stays visible in the
    `--dir` standing audit and stops GROWING here.

    Returns [] when git cannot answer (a shallow clone, an unknown ref). That is
    fail-OPEN and it is the honest direction for a diff-scoper: treating "we
    could not read the diff" as "everything is new" would fail every PR on a CI
    misconfiguration, and treating it as a finding would claim evidence we do
    not have.
    """
    import subprocess
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "diff", "--name-status",
             "--diff-filter=A", f"{base}...HEAD"],
            capture_output=True, text=True, timeout=60,
        )
    except Exception:
        return []
    if out.returncode != 0:
        return []
    added = []
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        rel = parts[-1]
        if not rel.endswith(".py") or rel.endswith("__init__.py"):
            continue
        if not any(rel.startswith(f"{d.rstrip('/')}/") for d in dirs):
            continue
        p = root / rel
        if p.is_file():
            added.append(p)
    return added


def _self_test(root: Path) -> int:
    import tempfile
    checks = []
    with tempfile.TemporaryDirectory() as d:
        fake = Path(d)
        (fake / "scripts" / "ops").mkdir(parents=True)
        (fake / ".github" / "workflows").mkdir(parents=True)
        # planted ORPHAN — must be flagged
        orphan = fake / "scripts" / "ops" / "orphan_tool.py"
        orphan.write_text("print('nobody calls me')\n")
        # planted WIRED — must NOT be flagged
        wired = fake / "scripts" / "ops" / "wired_tool.py"
        wired.write_text("print('a workflow runs me')\n")
        (fake / ".github" / "workflows" / "w.yml").write_text(
            "run: python3 scripts/ops/wired_tool.py\n")
        # planted MANUAL-ONLY with a reason — must NOT be flagged
        manual = fake / "scripts" / "ops" / "manual_tool.py"
        manual.write_text("# wiring: manual-only - operator runs this during a "
                          "VM migration only\nprint('x')\n")
        # planted MANUAL-ONLY with NO reason — must still be flagged
        bare = fake / "scripts" / "ops" / "bare_tool.py"
        bare.write_text("# wiring: manual-only -\nprint('x')\n")

        got = {r for r, _ in scan(fake, [orphan, wired, manual, bare])}
        checks.append(("planted orphan IS flagged",
                       "scripts/ops/orphan_tool.py" in got))
        checks.append(("workflow-referenced tool is NOT flagged",
                       "scripts/ops/wired_tool.py" not in got))
        checks.append(("manual-only WITH a reason is NOT flagged",
                       "scripts/ops/manual_tool.py" not in got))
        checks.append(("manual-only with NO reason IS still flagged",
                       "scripts/ops/bare_tool.py" in got))

    # A checker must not count ITSELF as a runner. This one cited
    # trainer_dataset_gc.py in its own docstring as the motivating example,
    # which made that tool read as wired -- the guard silencing itself by
    # documenting its own evidence.
    with tempfile.TemporaryDirectory() as d2:
        fake2 = Path(d2)
        (fake2 / "scripts" / "ops").mkdir(parents=True)
        tool = fake2 / "scripts" / "ops" / "cited_tool.py"
        tool.write_text("print('only this checker mentions me')\n")
        checks.append(("a tool named only in THIS checker is still flagged",
                       bool(scan(fake2, [tool]))))

    # A tool mentioned only in a COMMENT of a real runner is still unwired.
    # This is the class that defeated the checker twice.
    with tempfile.TemporaryDirectory() as d3:
        fake3 = Path(d3)
        (fake3 / "scripts" / "ops").mkdir(parents=True)
        (fake3 / "scripts" / "ci").mkdir(parents=True)
        tool = fake3 / "scripts" / "ops" / "mentioned_tool.py"
        tool.write_text("print('only a comment names me')\n")
        (fake3 / "scripts" / "ci" / "some_runner.py").write_text(
            "# see scripts/ops/mentioned_tool.py for the motivating example\n"
            "print('I do not run it')\n")
        checks.append(("a tool named only in a COMMENT is still flagged",
                       bool(scan(fake3, [tool]))))
        # ...but a real call in executable position IS wiring
        (fake3 / "scripts" / "ci" / "real_runner.py").write_text(
            'import subprocess\nsubprocess.run(["python3","scripts/ops/mentioned_tool.py"])\n')
        checks.append(("a tool CALLED in executable position is NOT flagged",
                       not scan(fake3, [tool])))

    # The DIFF SCOPER's own failure path. A scoper that silently returned []
    # on every input would make the blocking mode pass unconditionally —
    # indistinguishable from a clean repo, which is the class this file exists
    # to catch, turned on itself.
    checks.append(("added_targets returns [] on an unresolvable base rather than raising",
                   added_targets("definitely-not-a-ref-9f3a", root, ["scripts"]) == []))
    checks.append(("added_targets filters to the requested dirs",
                   all(str(p).startswith(str(root / "scripts"))
                       for p in added_targets("HEAD~1", root, ["scripts"]))))

    # the probe must find a positive on the real repo, or its silence is meaningless
    real = scan(root, [root / "scripts" / "ops" / "trainer_dataset_gc.py"]) \
        if (root / "scripts" / "ops" / "trainer_dataset_gc.py").exists() else []
    checks.append(("known-unwired trainer_dataset_gc.py is flagged on the real repo",
                   bool(real)))

    ok = sum(1 for _, g in checks if g)
    for n, g in checks:
        if not g:
            print(f"  FAIL  {n}")
    print(f"self-test: {ok}/{len(checks)} passed")
    return 0 if ok == len(checks) else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--dir", default="scripts", help="tree of tools to check")
    ap.add_argument("--base", default=None,
                    help="git ref: judge ONLY the *.py files this change ADDS "
                         "(the blocking CI mode). Without it, --dir is a "
                         "report-only standing audit over the whole tree.")
    ap.add_argument("--dirs", default="scripts,src",
                    help="comma-separated trees the --base mode judges")
    a = ap.parse_args()
    if a.self_test:
        return _self_test(REPO)

    if a.base:
        dirs = [x.strip() for x in a.dirs.split(",") if x.strip()]
        targets = added_targets(a.base, REPO, dirs)
        findings = scan(REPO, targets) if targets else []
        print(f"unwired-artifact (diff-scoped vs {a.base}): {len(targets)} newly "
              f"added tool(s) under {', '.join(d + '/' for d in dirs)}, "
              f"{len(findings)} with no runner")
        if not findings:
            # STATE THE DENOMINATOR. "0 findings" over 0 targets and over 12
            # targets are different statements and must not print the same.
            print("OK — nothing this change ADDS is unwired." if targets
                  else "OK — this change adds no new tool under those trees.")
            return 0
        for rel, why in sorted(findings):
            print(f"  {rel}\n      {why}")
        print("\n::error::this change ADDS a capability nothing runs. Wire it to a "
              "workflow/unit/caller, delete it, or declare "
              "`# wiring: manual-only - <reason>` in the file itself. "
              "Pre-existing unwired tools are NOT judged here — only what you add.")
        return 1

    targets = [f for f in (REPO / a.dir).rglob("*.py")
               if "__pycache__" not in f.parts and f.name != "__init__.py"]
    findings = scan(REPO, targets)
    print(f"unwired-artifact scan: {len(targets)} tool(s) under {a.dir}/, "
          f"{len(findings)} with no runner\n")
    for rel, why in sorted(findings):
        print(f"  {rel}\n      {why}")
    if findings:
        print("\nEach is a corpse to remove, a capability to WIRE, or a tool that "
              "must declare `# wiring: manual-only - <reason>`.")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
