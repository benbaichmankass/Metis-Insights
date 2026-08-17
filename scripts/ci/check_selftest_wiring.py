#!/usr/bin/env python3
"""Every registered guard self-test must actually RUN in CI — proven by
resolving the covering path, never by finding a name somewhere.

WHY THIS EXISTS
---------------
A guard's self-test is the thing that proves the guard can FAIL. Registering
one in `guard_selftests.py::SELFTESTS` makes it *runnable*; it does not make it
*run*. On 2026-08-17 `collapsed-state` was found registered with its planted-
failure suite invoked by nothing — the guard for the canonical "can this field
say we did not look?" rule had never once demonstrated in CI that it catches a
planted break, across all 10 of its contracts. Nothing detected that, because
the registry entry looked exactly like the wired ones.

That is the same defect class the registry itself guards elsewhere: a signal
WRITTEN and never READ (`provenance-consumer-guard`), a control REGISTERED and
never EXECUTED (this). A self-test nobody invokes is worse than a missing one —
a reviewer sees the entry and assumes the failure path is exercised.

THERE ARE TWO COVERING PATHS, AND BOTH MUST BE RESOLVED, NOT GUESSED
--------------------------------------------------------------------
A. `run_guards.py` invokes the suite BY NAME:
       ["python3", "scripts/ci/guard_selftests.py", "<name>"]
B. the checker owns its own `--self-test` and `run_guards.py` runs THAT:
       ["python3", "scripts/ci/check_<x>.py", "--self-test"]

Path B is why the naive check is wrong. A 2026-08-17 session grepped for the
`guard_selftests.py` call site of `matrix-corpus-agreement`, found none, and
concluded the suite was unwired — it was not; the same controls run via its
checker's own flag. Grepping ONE of two wiring paths and reading empty as
absence is precisely the failure RULE ONE names. So Path B is **declared** in
`guard_selftests.py::COVERED_BY_CHECKER` and this guard then VERIFIES the
declaration end to end:

  1. `run_guards.py` really contains a step running that exact script with
     `--self-test`, and
  2. that script really DECLARES the flag (an `add_argument("--self-test")`
     found in its AST) — not merely mentions the string in a comment.

Step 2 is the lesson from `new-table-wiring-guard`, whose presence-only marker
made the cheapest way to silence a real finding *naming a table that does not
exist*. A declaration this guard cannot contradict is decoration. Here, naming
a script that has no such flag FAILS rather than passing.

SCOPE IS THE REGISTRY, AND THAT BOUNDARY IS CHOSEN — NOT OVERLOOKED
-------------------------------------------------------------------
This audits `SELFTESTS`, because registering there IS the claim "this control
runs in CI"; a script that merely owns a `--self-test` makes no such claim.
Measured 2026-08-17 so the boundary rests on a number rather than an intuition:
**14** scripts under `scripts/` declare a `--self-test`, `run_guards.py` runs
**8** of them with the flag, and **6** it never runs — `backtest_pairs.py`,
`backtest_xsec_momentum.py`, `ops/get_env.py`, `research/m20_wf_effective.py`,
`research/pairs_dollar_lots.py`, `research/pairs_universe_scan.py`.

Those 6 are research/ops tooling whose correctness is not a repo invariant, so
conscripting them into every CI run would buy little and add runtime plus
failure surface — the desensitised-alarm shape. They are NOT silently dropped:
filed as BL-20260817-SELFTESTS-DECLARED-BUT-NEVER-RUN-IN-CI for a review
session to triage individually. If one of them is later judged to guard a real
invariant, the fix is to give it a `run_guards.py` entry, at which point this
guard's 8/14 becomes 9/14 on its own.

THREE STATES, NEVER COLLAPSED
-----------------------------
`covered` · `not_covered` · `unverifiable` — a `steps` entry this guard cannot
read statically (a computed argv). `unverifiable` is *"we could not look"*, and
folding it into either neighbour is the bug: into `covered` it hides a real gap,
into `not_covered` it fails CI for a wiring that may be perfectly fine. It is
reported separately and, being an honest gap in the guard's own reach, is
non-fatal but always printed.

Usage:
    python3 scripts/ci/check_selftest_wiring.py
    python3 scripts/ci/check_selftest_wiring.py --self-test
"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SELFTESTS_FILE = ROOT / "scripts/ci/guard_selftests.py"
RUN_GUARDS_FILE = ROOT / "scripts/ci/run_guards.py"
SELFTEST_RUNNER = "scripts/ci/guard_selftests.py"
SELFTEST_FLAG = "--self-test"


def _dict_keys(tree: ast.AST, var: str) -> list:
    """String keys of a module-level dict literal assigned to `var`.

    Returns None when the name is absent or is not a dict literal — a state the
    caller must distinguish from "an empty dict", since one means the registry
    moved and the other means it is genuinely empty.
    """
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for t in targets:
            if isinstance(t, ast.Name) and t.id == var:
                if not isinstance(node.value, ast.Dict):
                    return None
                return [k.value for k in node.value.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)]
    return None


def _dict_items(tree: ast.AST, var: str) -> dict:
    """str->str items of a module-level dict literal. None if absent/not a dict."""
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for t in targets:
            if isinstance(t, ast.Name) and t.id == var:
                if not isinstance(node.value, ast.Dict):
                    return None
                out = {}
                for k, v in zip(node.value.keys, node.value.values):
                    if (isinstance(k, ast.Constant) and isinstance(k.value, str)
                            and isinstance(v, ast.Constant)
                            and isinstance(v.value, str)):
                        out[k.value] = v.value
                return out
    return None


def collect_steps(src: str) -> tuple:
    """Every argv in `run_guards.py::GUARDS`, plus a count we could NOT read.

    A step is either a plain list of string constants or a
    `{"argv": [...], "when": ...}` dict — both shapes are in use, and handling
    only the first would silently under-count coverage.
    """
    tree = ast.parse(src)
    argvs, unreadable = [], 0
    guards = None
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for t in targets:
            if isinstance(t, ast.Name) and t.id == "GUARDS":
                guards = node.value
    if not isinstance(guards, ast.List):
        raise SystemExit("run_guards.py::GUARDS is not a list literal — this "
                         "guard cannot read it, which is NOT a clean pass.")

    def _argv_of(node):
        """-> list[str] | None (None = present but not statically readable)."""
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == "argv":
                    return _argv_of(v)
            return None
        if not isinstance(node, ast.List):
            return None
        parts = []
        for el in node.elts:
            if isinstance(el, ast.Constant) and isinstance(el.value, str):
                parts.append(el.value)
            else:
                return None
        return parts

    for entry in guards.elts:
        if not isinstance(entry, ast.Dict):
            unreadable += 1
            continue
        for k, v in zip(entry.keys, entry.values):
            if not (isinstance(k, ast.Constant) and k.value == "steps"):
                continue
            if not isinstance(v, ast.List):
                unreadable += 1
                continue
            for step in v.elts:
                argv = _argv_of(step)
                if argv is None:
                    unreadable += 1
                else:
                    argvs.append(argv)
    return argvs, unreadable


def declares_selftest_flag(script_rel: str) -> bool:
    """Does this script actually DECLARE `--self-test` in its argparse?

    Deliberately not a substring search: a comment saying "--self-test" would
    satisfy that, and a declaration cheaper to fake than to satisfy is worse
    than no declaration.
    """
    path = ROOT / script_rel
    if not path.is_file():
        return False
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "add_argument"):
            continue
        for a in node.args:
            if isinstance(a, ast.Constant) and a.value == SELFTEST_FLAG:
                return True
    return False


def audit(selftests_src: str, run_guards_src: str) -> dict:
    names = _dict_keys(ast.parse(selftests_src), "SELFTESTS")
    if names is None:
        raise SystemExit("guard_selftests.py::SELFTESTS is not a dict literal — "
                         "this guard cannot read it, which is NOT a clean pass.")
    declared = _dict_items(ast.parse(selftests_src), "COVERED_BY_CHECKER") or {}
    argvs, unreadable = collect_steps(run_guards_src)

    by_name = {a[-1] for a in argvs
               if len(a) >= 2 and SELFTEST_RUNNER in a and a[-1] != SELFTEST_RUNNER}

    def _runs_with_flag(script: str) -> bool:
        """Is there a step running THIS script with the self-test flag?

        Asked directly rather than by collecting 'tokens that look like a
        script', because any such heuristic (an `endswith('.py')` filter, say)
        silently exempts whatever it fails to recognise — the declared path
        would then skip the flag check below and pass for the wrong reason.
        """
        return any(script in a and SELFTEST_FLAG in a for a in argvs)

    covered, not_covered, problems = {}, [], []
    for n in sorted(names):
        if n in by_name:
            covered[n] = f"invoked by name in run_guards.py ({SELFTEST_RUNNER} {n})"
            continue
        script = declared.get(n)
        if not script:
            not_covered.append(
                f"{n}: registered in SELFTESTS but run_guards.py never invokes it "
                f"by name, and it declares no COVERED_BY_CHECKER path. Its "
                f"planted-failure controls do not run in CI.")
            continue
        if not _runs_with_flag(script):
            not_covered.append(
                f"{n}: declares COVERED_BY_CHECKER -> {script}, but run_guards.py "
                f"has no step running that script with {SELFTEST_FLAG}. The "
                f"declaration is unbacked.")
            continue
        if not declares_selftest_flag(script):
            not_covered.append(
                f"{n}: declares COVERED_BY_CHECKER -> {script}, but that script "
                f"does not declare a {SELFTEST_FLAG} argument. Naming a script "
                f"that cannot self-test must fail, not pass.")
            continue
        covered[n] = f"covered by {script} {SELFTEST_FLAG} (declared + verified)"

    # A stale declaration is its own defect: it reads as coverage for a name
    # that no longer exists, so the next reader trusts a mapping to nothing.
    for n in sorted(declared):
        if n not in names:
            problems.append(f"COVERED_BY_CHECKER names '{n}', absent from SELFTESTS.")

    # The reverse direction: run_guards.py invoking a name the registry lacks
    # would die at argparse `choices` — in CI, on an unrelated PR.
    for n in sorted(by_name):
        if n not in names:
            problems.append(
                f"run_guards.py invokes '{SELFTEST_RUNNER} {n}' but SELFTESTS has "
                f"no such name — that step will fail argparse at run time.")

    return {"names": names, "covered": covered, "not_covered": not_covered,
            "problems": problems, "unverifiable": unreadable}


def _report(res: dict) -> int:
    n = len(res["names"])
    print(f"POPULATION: {n} self-test(s) registered in "
          f"guard_selftests.py::SELFTESTS; {len(res['covered'])} covered.")
    if res["unverifiable"]:
        # Stated ALWAYS, pass or fail — a coverage figure over a denominator
        # this guard could not fully read is exactly the unasserted-denominator
        # shape, and hiding it on the happy path is how it stops being read.
        print(f"           {res['unverifiable']} step(s) in run_guards.py are not "
              f"statically readable (computed argv) — 'we could not look', "
              f"counted as neither covered nor uncovered.")
    for k, v in sorted(res["covered"].items()):
        print(f"  ok   {k}: {v}")
    for m in res["not_covered"]:
        print(f"  FAIL {m}")
    for m in res["problems"]:
        print(f"  FAIL {m}")
    bad = len(res["not_covered"]) + len(res["problems"])
    if bad:
        print(f"\n{bad} wiring defect(s). A registered self-test that never runs "
              f"is a control in name only.")
        return 1
    print("\nAll registered self-tests resolve to a covering path that was "
          "verified, not assumed.")
    return 0


def _self_test() -> int:
    good_st = ('SELFTESTS = {"alpha": a, "beta": b}\n'
               'COVERED_BY_CHECKER = {"beta": "scripts/ci/check_selftest_wiring.py"}\n')
    good_rg = ('GUARDS = [\n'
               '  {"name": "g1", "steps": [["python3", '
               '"scripts/ci/guard_selftests.py", "alpha"]]},\n'
               '  {"name": "g2", "steps": [["python3", '
               '"scripts/ci/check_selftest_wiring.py", "--self-test"]]},\n'
               ']\n')
    r = audit(good_st, good_rg)
    assert not r["not_covered"] and not r["problems"], r
    assert set(r["covered"]) == {"alpha", "beta"}, r

    # PLANT 1 — a name wired by neither path must FAIL. This is the exact
    # condition that went unnoticed for `collapsed-state`.
    r = audit('SELFTESTS = {"alpha": a, "orphan": o}\n', good_rg)
    assert any("orphan" in m for m in r["not_covered"]), r

    # PLANT 2 — a DECLARED Path B whose step is absent from run_guards.py must
    # FAIL. Declaring coverage is not having it.
    r = audit('SELFTESTS = {"beta": b}\n'
              'COVERED_BY_CHECKER = {"beta": "scripts/ci/check_selftest_wiring.py"}\n',
              'GUARDS = [{"name": "g", "steps": [["python3", "other.py"]]}]\n')
    assert any("no step running that script" in m for m in r["not_covered"]), r

    # PLANT 3 — a declared script that does NOT declare the flag must FAIL,
    # even though run_guards.py runs it with --self-test. This is the
    # presence-only trap: the mapping must be CONTRADICTABLE, or it is
    # decoration.
    #
    # The plant uses the real `check_collapsed_states.py` on purpose, because
    # this is the exact mistake available today: that checker owns no
    # `--self-test` (its controls live in `guard_selftests.py`), so declaring it
    # as `collapsed-state`'s Path B would be plausible, wrong, and — without
    # this rung — silently accepted.
    r = audit('SELFTESTS = {"collapsed-state": b}\n'
              'COVERED_BY_CHECKER = '
              '{"collapsed-state": "scripts/ci/check_collapsed_states.py"}\n',
              'GUARDS = [{"name": "g", "steps": [["python3", '
              '"scripts/ci/check_collapsed_states.py", "--self-test"]]}]\n')
    assert any("does not declare" in m for m in r["not_covered"]), r

    # PLANT 4 — a stale COVERED_BY_CHECKER key, and an invoked name the
    # registry lacks, are each their own defect.
    r = audit('SELFTESTS = {}\nCOVERED_BY_CHECKER = {"gone": "x.py"}\n', good_rg)
    assert any("absent from SELFTESTS" in m for m in r["problems"]), r
    assert any("no such name" in m for m in r["problems"]), r

    # CONTROL — a computed argv is 'we could not look', never a silent pass or
    # a spurious failure.
    r = audit('SELFTESTS = {"alpha": a}\n',
              'GUARDS = [{"name": "g", "steps": [BUILD_ARGV()]}]\n')
    assert r["unverifiable"] == 1, r
    assert any("alpha" in m for m in r["not_covered"]), r

    print("self-test OK — an unwired name, an unbacked declaration, a declared "
          "script with no such flag, a stale mapping and an unknown invoked "
          "name all FAIL; an unreadable step is reported as unverifiable.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    return _report(audit(SELFTESTS_FILE.read_text(), RUN_GUARDS_FILE.read_text()))


if __name__ == "__main__":
    raise SystemExit(main())
