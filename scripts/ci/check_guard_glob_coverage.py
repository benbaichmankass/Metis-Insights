#!/usr/bin/env python3
"""Is each guard TRIGGERED by every file its check actually READS?

THE DEFECT THIS GENERALISES (measured 2026-08-23). `exit-coverage-matrix-guard`
runs `m20_coverage_rollup.py --check`, which JOINS
`docs/research/exit-refinement-coverage.json` against `config/strategies.yaml`
(the `execution` field must agree). Its `when.globs` listed the matrix and its
two scripts and **not** `config/strategies.yaml` — so the one edit that can make
the matrix stale, flipping a leg's `execution`, was the one edit that would not
run the guard. Demoting `htf_pullback_trend_2h` reported
``SKIP (not relevant to this diff)`` locally and reached CI, where the test that
invokes guards with ``--all`` caught it.

A guard scoped to one side of a two-sided check is quiet exactly when it should
not be. That is the same shape as a search with no denominator: the negative
looks clean because the probe could not have produced a positive.

WHAT THIS CHECKS, precisely: for every guard carrying a `when` predicate, the
repo-relative data files its step scripts open are collected statically, and
each is tested against that guard's OWN predicate via `run_guards.is_relevant`
— imported, never re-implemented, so this file and the runner can never drift
into two answers about what "relevant" means.

⚠️ STATE THE METHOD'S LIMITS, because a clean result here is not proof.
This is a STATIC literal scan. It sees `open("config/x.yaml")` and module-level
path constants; it does NOT see a path built at runtime, read through a helper
in another module, or named only in a config file. So:

  * a FINDING is high-confidence — the literal is right there, and the predicate
    demonstrably does not match it;
  * a CLEAN result means *"no unscoped literal path was found"*, *not* "every
    guard is correctly scoped". Those are different claims and only the first is
    supported.

Read `scanned_paths` beside the finding count — a guard whose scripts yielded
zero data paths was not verified, it was merely not contradicted, and it is
reported as `not_verifiable` rather than folded into the clean count.

READING A FILE IS NOT THE SAME AS BEING STALE-ABLE AGAINST IT, and the first
real run proved it. `exit-mechanism-coverage-guard` reads
`config/lever_reachability.json` (`_reachability()`, reachable from `main`) and
is not triggered by it — a textbook hit for the rule above. But emptying that
file's `levers` list and re-running BOTH of the guard's actual steps
(`--self-test`, `--orphans-only`) produced byte-identical output and the same
exit code: `audit()` computes the reachability map and neither step surfaces it.
Adding the glob would fire the guard on edits it cannot grade, which is the
desensitized-alarm failure this repo treats as its own P1.

So a static finding here is a LEAD, not a verdict. Promote it only by
perturbation: change the file, re-run the guard's own steps, and see the output
or exit code move. `exit-coverage-matrix-guard` passed that bar the hard way —
CI went red. `exit-mechanism-coverage-guard` failed it and is recorded as
cosmetic-for-now rather than "fixed" with a glob.

⚠️ This is a REPORT, not a guard, deliberately. Wiring it into `run_guards.py`
would mean a build that fails on unconfirmed leads.

Exit 0 clean / 1 findings to triage / 2 could not measure at all.
"""
# wiring: manual-only - this reports LEADS, not verdicts. Its one live finding
# is measured cosmetic (perturbing config/lever_reachability.json moves neither
# of exit-mechanism-coverage-guard's steps), so wiring it into run_guards.py
# would fail builds on unconfirmed leads and train everyone to walk past it --
# the desensitized-alarm P1. Run it by hand after editing the GUARDS table; its
# --self-test plants a real defect and is what proves it still detects one.

from __future__ import annotations

import argparse
import ast
import json
import pathlib
import re
import sys

_REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from scripts.ci.run_guards import GUARDS, is_relevant  # noqa: E402

# Extensions that carry DATA a check can be stale against. `.py`/`.sh` are
# excluded deliberately: a guard's own implementation changing is covered by the
# broad `\.py$` predicates, and including them would bury the data findings in
# noise about scripts importing each other.
_DATA_EXT = (".json", ".yaml", ".yml", ".jsonl", ".md", ".csv", ".txt")

# The diff is an INPUT every diff-scoped guard reads by design, not a file the
# check can be stale against. Counting it would flag every guard in the table.
_INPUT_PATHS = {"/tmp/pr.diff", "pr.diff"}

_PATH_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*\.[A-Za-z0-9]+$")


def _scripts_of(guard):
    out = []
    for step in guard.get("steps") or []:
        for arg in step:
            if not isinstance(arg, str) or "{" in arg:
                continue
            if arg.endswith(".py") and (_REPO / arg).is_file():
                out.append(arg)
    return out


def _div_segments(node):
    """Flatten a ``REPO / "config" / "strategies.yaml"`` chain to its str parts.

    Segment-wise `pathlib` joins are how this repo actually names files, so a
    scanner that only saw whole-string literals found NOTHING in the very script
    whose defect motivated it — the self-test caught exactly that. Non-Constant
    operands (the `REPO` anchor) are dropped: what is wanted is the repo-relative
    tail, which is what a `when` glob is written against.
    """
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _div_segments(node.left) + _div_segments(node.right)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    return []


def _reachable_nodes(tree):
    """Module-level statements + functions transitively called from `main`.

    Scanning the whole module was the first version's second over-report: the
    readers of `config/accounts.yaml` and the sweep corpus are module-level
    FUNCTIONS, so they were found regardless of whether the guard's step could
    ever call them. A guard is stale-able only against files its OWN invocation
    reads, so reachability is the question, not presence.
    """
    fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    out = [n for n in tree.body if not isinstance(n, ast.FunctionDef)]
    seen, stack = set(), ["main"]
    while stack:
        name = stack.pop()
        if name in seen or name not in fns:
            continue
        seen.add(name)
        node = fns[name]
        out.append(node)
        for c in ast.walk(node):
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name):
                stack.append(c.func.id)
    return out


def _candidates(tree):
    """Every string this script could be naming a file with, on the live path."""
    for root in _reachable_nodes(tree):
      for node in ast.walk(root):
          if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
              segs = _div_segments(node)
              if segs:
                  yield "/".join(segs)
          elif isinstance(node, ast.Constant) and isinstance(node.value, str):
              yield node.value


def _flag_names(step):
    """The long-flag words a step passes, e.g. `--check` -> {"check"}."""
    return {a.lstrip("-").replace("-", "_") for a in step
            if isinstance(a, str) and a.startswith("--")}


def _mentions_other_flag(test, flags):
    """True if this `if` is gated on a CLI flag the step does not pass.

    Entry-point scripts branch on their own arguments (`if args.check:` /
    `if a.render:`), so a path read only under a flag the guard never passes is
    NOT read by that guard. Ignoring this is what made the first version of this
    probe report `config/accounts.yaml` and the sweep corpus against
    `exit-coverage-matrix-guard`: both are real reads of the FILE, and neither
    is reachable from `--check`. Verified by hand before this was written --
    `--check` runs `validate()`, which transitively reaches neither reader.
    """
    seen = {n.attr for n in ast.walk(test) if isinstance(n, ast.Attribute)}
    seen |= {n.id for n in ast.walk(test) if isinstance(n, ast.Name)}
    gating = seen & _ALL_FLAGS_SEEN
    return bool(gating) and not (gating & flags)


def _mentions_our_flag(test, flags):
    seen = {n.attr for n in ast.walk(test) if isinstance(n, ast.Attribute)}
    seen |= {n.id for n in ast.walk(test) if isinstance(n, ast.Name)}
    return bool(seen & flags)


def _prune(tree, flags):
    """Drop `if <other-flag>:` bodies so only the step's own path remains."""
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if not isinstance(block, list):
                continue
            keep, dead = [], False
            for stmt in block:
                if dead:
                    break
                if isinstance(stmt, ast.If) and _mentions_other_flag(stmt.test, flags):
                    continue
                keep.append(stmt)
                # An `if --check: ... return` branch that the step DOES take ends
                # the function for that step, so every later sibling is dead code
                # on this path. Without this, `--check` still "reaches" the render
                # call that sits after the early return, and the probe reports a
                # read that never happens.
                if (isinstance(stmt, ast.If) and stmt.body
                        and isinstance(stmt.body[-1], ast.Return)
                        and _mentions_our_flag(stmt.test, flags)):
                    dead = True
            setattr(node, field, keep)
    return tree


def _reachable_source(rel_script, flags):
    """Module source with flag-gated branches the step never takes removed."""
    global _ALL_FLAGS_SEEN
    raw = (_REPO / rel_script).read_text()
    tree = ast.parse(raw)
    _ALL_FLAGS_SEEN = {
        n.args[0].value.lstrip("-").replace("-", "_")
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "add_argument"
        and n.args and isinstance(n.args[0], ast.Constant)
        and isinstance(n.args[0].value, str) and n.args[0].value.startswith("--")
    }
    return _prune(ast.parse(raw), flags)


_ALL_FLAGS_SEEN: set = set()


def _literal_paths(rel_script, flags=frozenset()):
    """Repo-relative data paths this script names on the step's OWN path."""
    found = set()
    try:
        tree = _reachable_source(rel_script, set(flags))
    except (OSError, SyntaxError):
        return found
    for raw in _candidates(tree):
        val = raw.strip().lstrip("./")
        if val in _INPUT_PATHS or not val.endswith(_DATA_EXT):
            continue
        if not _PATH_RE.match(val) or val == rel_script:
            continue
        if (_REPO / val).is_file():
            found.add(val)
    return found


def audit(guards=None):
    rows = []
    for g in guards if guards is not None else GUARDS:
        when = g.get("when")
        if when is None:
            continue  # always-runs: cannot be under-scoped
        scripts = _scripts_of(g)
        flags = set()
        for step in g.get("steps") or []:
            flags |= _flag_names(step)
        paths = set()
        for scr in scripts:
            paths |= _literal_paths(scr, flags)
        unscoped = sorted(p for p in paths if not is_relevant(when, [p]))
        rows.append({
            "guard": g.get("name"),
            "scripts": scripts,
            "scanned_paths": sorted(paths),
            "unscoped_paths": unscoped,
            # Never collapsed: "checked and clean" is not "nothing to check".
            "verdict": ("under_scoped_unconfirmed" if unscoped
                        else "clean" if paths
                        else "not_verifiable"),
        })
    return rows


def _self_test():
    """Prove the probe can produce a POSITIVE before its negatives are trusted.

    Plants the exact 2026-08-23 defect — `exit-coverage-matrix-guard` without
    `config/strategies.yaml` in its globs — and requires the audit to flag it.
    A guard that cannot be shown to fail is not evidence when it passes.
    """
    target = next((g for g in GUARDS if g.get("name") == "exit-coverage-matrix-guard"), None)
    if target is None:
        print("self-test: FAIL — exit-coverage-matrix-guard not in the table")
        return 1
    planted = dict(target)
    planted["when"] = {"globs": [g for g in target["when"]["globs"]
                                if g != "config/strategies.yaml"]}
    row = audit([planted])[0]
    if "config/strategies.yaml" not in row["unscoped_paths"]:
        print("self-test: FAIL — planted defect was NOT detected; "
              f"scanned={row['scanned_paths']}")
        return 1
    print("  self-test ok: planted glob removal -> flagged under_scoped")
    live = next(r for r in audit() if r["guard"] == "exit-coverage-matrix-guard")
    if live["verdict"] != "clean":
        print(f"self-test: FAIL — live guard should be clean, got {live}")
        return 1
    print("  self-test ok: the real table's entry reads clean")
    print("self-test: PASS")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return _self_test()

    rows = audit()
    if not rows:
        print("guard-glob-coverage: could not measure — no guard carries a `when`")
        return 2
    bad = [r for r in rows if r["verdict"] == "under_scoped_unconfirmed"]
    unver = [r for r in rows if r["verdict"] == "not_verifiable"]
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for r in bad:
            print(f"::warning::{r['guard']}: reads {', '.join(r['unscoped_paths'])} "
                  f"but its `when` does not match. LEAD, not a verdict — confirm "
                  f"by perturbing that file and re-running the guard's own steps; "
                  f"if the output does not move, the read is not verdict-bearing "
                  f"and a glob would only add noise.")
        print(f"guard-glob-coverage: {len(rows)} scoped guard(s) — "
              f"{len(bad)} under-scoped LEAD(s) · "
              f"{len(rows) - len(bad) - len(unver)} clean · "
              f"{len(unver)} not_verifiable (no literal data path found; "
              f"NOT the same as verified-clean)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
