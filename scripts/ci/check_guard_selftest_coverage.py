#!/usr/bin/env python3
"""Measure guard SELF-TEST COVERAGE over the guard POPULATION, not the registry.

WHY THIS EXISTS
---------------
Every guard in this repo is a claim that some invariant holds. A guard's GREEN
is worth exactly as much as the evidence that it is capable of turning RED — and
until 2026-09-09 nothing here measured that. The concept existed only in prose
(`.claude/skills/full-system-audit/SKILL.md` and two audit documents) and in
ZERO executable checks (F-01, 2026-09-09 full-system audit).

The nearest existing instrument, `scripts/ci/check_selftest_wiring.py`, answers
a DIFFERENT question and says so in its own docstring: its scope is the
`guard_selftests.py::SELFTESTS` registry, because registering there IS the claim
"this control runs in CI". That boundary is chosen, not overlooked, and that
guard is not defective. But it means a guard that never registers is invisible
to it — so `provenance-consumer-guard` sat inside the REQUIRED `guards` merge
context for 41 days with neither a self-test nor a test file, its green never
once shown capable of turning red, having been promoted to required PRECISELY
because a write-only provenance signal let a manufactured-PnL figure accumulate
unnoticed (F-05).

THE DENOMINATOR IS THE FILESYSTEM, AND THAT IS THE WHOLE POINT
--------------------------------------------------------------
This script's population is `glob(scripts/check_*.py) | glob(scripts/ci/check_*.py)`.
Measuring the registry instead of the population is the exact defect this exists
to close: a registry can only ever report on what someone remembered to add to
it, so its ratio can be 100% while the real coverage is anything at all. The
`covered/total` line is printed with BOTH terms so the denominator is visible in
the CI log rather than inferable from it.

THREE POPULATIONS, NEVER COLLAPSED
----------------------------------
Collapsing these is how a coverage number becomes a lie, so they are reported
separately and only the first is called "covered":

  guards_context  the failure path resolves INSIDE the required `guards` merge
                  context, by one of exactly two paths, each RESOLVED and never
                  guessed:
                    A. the guard declares `--self-test` in its own argparse AND
                       `run_guards.py` invokes it with that flag;
                    B. `guard_selftests.py::SELFTESTS` holds an entry whose body
                       references the guard's module AND `run_guards.py` invokes
                       `guard_selftests.py <name>`.
  pytest_only     no guards-context path, but a file under `tests/` names the
                  guard's module. ⚠️ THIS IS NOT COVERAGE AND IS NEVER ADDED TO
                  IT. Naming a module is not planting a violation; the
                  2026-09-09 audit stated this limit explicitly and this script
                  keeps it stated. `pytest-run` IS a required context, so these
                  are UNPROVEN, not disproven — a distinction that disappears
                  the moment the two numbers are summed.
  none            no failure-path evidence anywhere. This is the finding.

REACHABILITY IS MEASURED, NOT DECLARED
--------------------------------------
Both covering paths are resolved by parsing `run_guards.py`'s AST and comparing
argv tokens EXACTLY. Grepping is refused here on measured grounds: F-61 of the
same audit reported four guards as `invoked=0` when all four ARE invoked with
`--self-test`, because their argv is wrapped across two source lines and a
single-line grep cannot see it. That probe had a working positive control (a
guard whose argv fits on one line) and was still wrong — which is precisely why
this instrument reads the syntax tree instead of the text.

THE EXEMPTION FILE CANNOT BE USED TO MAKE A REAL FINDING GO AWAY
-----------------------------------------------------------------
`guard_selftest_exemptions.json` is named, dated and reasoned, and two rules
bound it:

  1. F-01's rule: a guard whose declared `blast_radius` is `accounting` or
     `money-at-risk` may not be exemption-listed.
  2. The rule that makes rule 1 enforceable: a guard REACHABLE from
     `run_guards.py` may not be exemption-listed AT ALL, whatever it declares.

Rule 2 exists because `blast_radius` is written by the same person writing the
exemption, so rule 1 alone is cheaper to lie to than to satisfy — the
`new-table-wiring-guard` presence-only-marker failure this repo has already paid
for. Reachability is a fact about `run_guards.py`, not a field, so the only way
past rule 2 is to remove the guard from CI, which is a separate and visible act.

Exit 0 clean, 1 with findings, 2 on a structural problem. `--self-test` runs the
planted controls — this script may not be the one guard in the repo whose own
failure path is unexercised.
"""
from __future__ import annotations

import argparse
import ast
import datetime as _dt
import json
import re
import sys
import tempfile
from textwrap import dedent as _dedent
from pathlib import Path
from typing import Dict, List, Set, Tuple

REPO = Path(__file__).resolve().parents[2]
RUN_GUARDS = REPO / "scripts" / "ci" / "run_guards.py"
SELFTEST_MODULE = REPO / "scripts" / "ci" / "guard_selftests.py"
EXEMPTIONS = REPO / "scripts" / "ci" / "guard_selftest_exemptions.json"
TESTS_DIR = REPO / "tests"

# The RATCHET. It may rise and may never fall: a PR that reduces the number of
# guards with a resolved failure path fails here, naming the loss. Raise it in
# the same PR that earns it -- the script prints the value to set.
COVERAGE_FLOOR = 58

_MONEY_BLAST_RADII = {"accounting", "money-at-risk", "money_at_risk"}

# TWO PROPERTIES OF A SELF-TEST, AND ONLY THE WEAKER ONE IS A GATE.
#
# GATED -- `asserts_a_verdict`: the self-test must EXERCISE something (a call)
# and COMPARE the answer (a comparison, assert or raise). This is what separates
# a planted-failure test from a script that prints "PASS" and exits 0 whatever
# happens -- the presence-only failure this repo already paid for with
# `new-table-wiring-guard`'s marker.
#
# REPORTED, NOT GATED -- `asserts_on_main_or_rc`: F-04's stricter form, an
# assertion on `main([...])`'s return or a subprocess return code. It is
# deliberately NOT a gate: measured 2026-09-09 over all 56 wired self-tests,
# only 17 meet it, because the house idiom asserts on the guard's own decision
# FUNCTIONS instead. That idiom is legitimate -- it plants a violation and
# requires the detector to flag it -- and failing 39 guards on day one would
# red every PR in the repo, which is how a guard gets DISABLED rather than
# fixed. So the number is printed on every run and left to ratchet.
_STRICT_RETURN = re.compile(
    r"\bmain\s*\(|\breturncode\b|\bsubprocess\.run\b|\b_rc\b|\b_rc_out\b"
    r"|\bcheck_output\b")


# ---------------------------------------------------------------------------
# population


def discover_guards(repo: Path) -> List[Path]:
    """The DENOMINATOR: every guard script on disk, found by glob.

    Not a registry, not a list in a doc, not `run_guards.py`'s own table -- all
    three can only report what someone remembered to add.
    """
    found: Set[Path] = set()
    for pat in ("scripts/check_*.py", "scripts/ci/check_*.py"):
        found.update(repo.glob(pat))
    return sorted(found)


def _rel(repo: Path, p: Path) -> str:
    return p.relative_to(repo).as_posix()


# ---------------------------------------------------------------------------
# path A -- the guard's own --self-test, invoked by run_guards


def declares_self_test_flag(path: Path) -> bool:
    """True when `--self-test` is a real argparse argument, per the AST.

    A mention in a docstring or a comment does not count, and neither does a
    `"--self-test" in sys.argv` scan -- so both idioms are recognised, but only
    from code, never from prose.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"
                and any(isinstance(a, ast.Constant) and a.value == "--self-test"
                        for a in node.args)):
            return True
        # The `if "--self-test" in sys.argv[1:]` idiom, used by several guards.
        if (isinstance(node, ast.Compare)
                and isinstance(node.left, ast.Constant)
                and node.left.value == "--self-test"
                and any(isinstance(op, ast.In) for op in node.ops)):
            return True
    return False


def argv_literals(path: Path) -> List[Tuple[str, ...]]:
    """Every all-string list/tuple literal in a module, as a tuple of tokens.

    Read from the AST so a command wrapped across source lines is one argv --
    the exact failure that made F-61 report four correctly-wired guards as
    unwired. Tokens are then matched EXACTLY (membership, not substring), so a
    path that merely appears inside some longer string cannot count as an
    invocation.
    """
    out: List[Tuple[str, ...]] = []
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)):
            vals = [e.value for e in node.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if vals and len(vals) == len(node.elts):
                out.append(tuple(vals))
    return out


# ---------------------------------------------------------------------------
# path B -- guard_selftests.py registry, invoked by run_guards


def registry_functions(module: Path) -> Dict[str, str]:
    """name -> source of the registered self-test function.

    The mapping is read from `SELFTESTS`'s AST rather than by regex so a
    reformat of that dict cannot silently drop entries from this measurement.
    """
    src = module.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)
    bodies = {n.name: (ast.get_source_segment(src, n) or "")
              for n in tree.body if isinstance(n, ast.FunctionDef)}
    mapping: Dict[str, str] = {}
    for node in tree.body:
        targets = []
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
            value = node.value
        elif isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        else:
            continue
        if "SELFTESTS" not in targets or not isinstance(value, ast.Dict):
            continue
        for k, v in zip(value.keys, value.values):
            if isinstance(k, ast.Constant) and isinstance(v, ast.Name):
                mapping[str(k.value)] = bodies.get(v.id, "")
    return mapping


_SELFTEST_NAMES = ("self_test", "_self_test", "selftest", "_selftest")


def self_test_body(path: Path) -> str:
    """The self-test's source, PLUS the module helpers it calls.

    The helpers are not optional and their omission was a measured false
    positive on this very script's first run: `check_one_live_workplan.py` and
    `check_automerge_trigger.py` both plant real defects and assert on them, but
    through an `_expect(...)` / plant-table helper rather than an inline
    comparison inside `self_test` itself. Reading only the self-test's own body
    reported four correctly-armed guards as decoration -- the same shape as the
    F-61 miss this script exists to avoid, arrived at from the other direction.
    An assertion is no less an assertion for living one call away.

    One level of closure, deliberately: it covers the `_expect` / `check(...)`
    idiom every guard here uses, and a self-test whose verdict is buried deeper
    than that is not legible enough to count as evidence anyway.
    """
    src = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return ""
    funcs = {n.name: n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    roots = [n for name, n in funcs.items() if name in _SELFTEST_NAMES]
    if not roots:
        # A guard may run its controls INLINE under `if args.self_test:` inside
        # `main()` rather than in a function of its own -- measured on
        # `check_matrix_corpus_agreement.py`, whose controls are a block of bare
        # `assert`s with no `self_test` function anywhere. Requiring a
        # particular function NAME would have reported that guard as
        # decoration, which is a claim about our extractor rather than about the
        # guard. Fall back to whichever function actually branches on the flag.
        roots = [n for n in funcs.values()
                 if re.search(r"\bself_test\b|--self-test",
                              ast.get_source_segment(src, n) or "")]
    if not roots:
        return ""
    called: Set[str] = set()
    for r in roots:
        for node in ast.walk(r):
            if isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Name):
                    called.add(fn.id)
                elif isinstance(fn, ast.Attribute):
                    called.add(fn.attr)
    parts = [ast.get_source_segment(src, r) or "" for r in roots]
    parts += [ast.get_source_segment(src, funcs[c]) or ""
              for c in sorted(called) if c in funcs and c not in _SELFTEST_NAMES]
    return "\n".join(parts)


def asserts_a_verdict(body: str) -> bool:
    """THE GATE: the self-test exercises something and compares the answer.

    Read from the AST, not by regex, so a comparison inside a docstring or a
    comment cannot satisfy it -- the same reason `check_selftest_wiring.py`
    resolves its declarations instead of grepping for them.
    """
    if not body:
        return False
    try:
        tree = ast.parse(_dedent(body))
    except SyntaxError:
        return False
    has_call = any(isinstance(n, ast.Call) for n in ast.walk(tree))
    has_verdict = any(isinstance(n, (ast.Compare, ast.Assert, ast.Raise))
                      for n in ast.walk(tree))
    return has_call and has_verdict


def asserts_on_main_or_rc(body: str) -> bool:
    """REPORTED ONLY: F-04's stricter form. See the note beside _STRICT_RETURN."""
    return bool(body) and bool(_STRICT_RETURN.search(body)) and asserts_a_verdict(body)


# ---------------------------------------------------------------------------
# assessment


def assess(repo: Path) -> List[dict]:
    guards = discover_guards(repo)
    rg = argv_literals(repo / "scripts" / "ci" / "run_guards.py")
    reg = registry_functions(repo / "scripts" / "ci" / "guard_selftests.py")
    tests: Dict[Path, str] = {}
    tdir = repo / "tests"
    if tdir.is_dir():
        tests = {p: p.read_text(encoding="utf-8", errors="replace")
                 for p in sorted(tdir.rglob("*.py"))}

    rows: List[dict] = []
    for g in guards:
        rel = _rel(repo, g)
        module = g.stem
        word = re.compile(r"\b" + re.escape(module) + r"\b")

        reachable = any(rel in argv for argv in rg)
        declares = declares_self_test_flag(g)
        invoked_a = any(rel in argv and "--self-test" in argv for argv in rg)

        reg_names = [n for n, body in reg.items() if word.search(body)]
        invoked_b = [
            n for n in reg_names
            if any(n in argv and any(t.endswith("guard_selftests.py") for t in argv)
                   for argv in rg)
        ]

        if invoked_a:
            path, body = "A", self_test_body(g)
        elif invoked_b:
            path, body = "B", "\n".join(reg[n] for n in invoked_b)
        else:
            path, body = "", ""

        named_by = [_rel(repo, t) for t, src in tests.items() if word.search(src)]
        if path:
            population = "guards_context"
        elif named_by:
            population = "pytest_only"
        else:
            population = "none"

        rows.append({
            "guard": rel,
            "population": population,
            "path": path,
            "reachable_from_run_guards": reachable,
            "declares_self_test_flag": declares,
            "declared_but_never_invoked": declares and not invoked_a and not invoked_b,
            "registry_names": reg_names,
            "pytest_files": named_by,
            "asserts_a_verdict": asserts_a_verdict(body) if path else False,
            "asserts_on_main_or_rc": asserts_on_main_or_rc(body) if path else False,
        })
    return rows


def load_exemptions(path: Path) -> Tuple[Dict[str, dict], List[str]]:
    """Exemptions plus any STRUCTURAL problems with the file itself.

    A malformed exemption file is a finding, never a silent empty set: reading
    an unparseable file as "nothing is exempt" would flip this guard's verdict
    on every unexempted row and teach the next reader to distrust it.
    """
    problems: List[str] = []
    if not path.exists():
        return {}, problems
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, [f"{path.name} is unreadable: {exc}"]
    entries = raw.get("exemptions")
    if not isinstance(entries, list):
        return {}, [f"{path.name}: 'exemptions' must be a list"]
    out: Dict[str, dict] = {}
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            problems.append(f"{path.name}: exemptions[{i}] is not an object")
            continue
        guard = str(e.get("guard", "")).strip()
        if not guard:
            problems.append(f"{path.name}: exemptions[{i}] names no guard")
            continue
        for field in ("reason", "added", "blast_radius", "tracking_id"):
            if not str(e.get(field, "")).strip():
                problems.append(f"{path.name}: exemption for '{guard}' is missing '{field}'")
        added = str(e.get("added", "")).strip()
        if added:
            try:
                _dt.date.fromisoformat(added)
            except ValueError:
                problems.append(
                    f"{path.name}: exemption for '{guard}' has an unparseable "
                    f"'added' date {added!r} (want YYYY-MM-DD)")
        out[guard] = e
    return out, problems


def evaluate(rows: List[dict], exemptions: Dict[str, dict],
             floor: int) -> Tuple[List[str], dict]:
    covered = [r for r in rows if r["population"] == "guards_context"]
    pytest_only = [r for r in rows if r["population"] == "pytest_only"]
    none = [r for r in rows if r["population"] == "none"]
    stats = {
        "total": len(rows),
        "guards_context": len(covered),
        "pytest_only": len(pytest_only),
        "none": len(none),
        "asserts_a_verdict": sum(1 for r in covered if r["asserts_a_verdict"]),
        "asserts_on_main_or_rc": sum(1 for r in covered if r["asserts_on_main_or_rc"]),
        "declared_but_never_invoked": [r["guard"] for r in rows
                                       if r["declared_but_never_invoked"]],
    }
    v: List[str] = []

    # 1. Every guard with NO failure-path evidence must be exemption-listed.
    for r in none:
        ex = exemptions.get(r["guard"])
        if ex is None:
            v.append(
                f"[uncovered] {r['guard']} has NO failure-path evidence anywhere "
                f"— no `--self-test` invoked by run_guards.py, no entry in "
                f"guard_selftests.py::SELFTESTS, and no file under tests/ that "
                f"even names it. Its green has never been shown capable of "
                f"turning red. Give it a planted-failure self-test, or add a "
                f"named+dated entry to {EXEMPTIONS.name}.")
            continue
        # 2. F-01's rule: money/accounting blast radius may not be exempted.
        radius = str(ex.get("blast_radius", "")).strip().lower()
        if radius in _MONEY_BLAST_RADII:
            v.append(
                f"[exemption-refused] {r['guard']} declares blast_radius "
                f"'{radius}' and MAY NOT be exemption-listed. A guard whose "
                f"failure would land in the books or on money at risk is the "
                f"one case where an unexercised failure path is not tolerable.")
        # 3. The rule that makes rule 2 enforceable, because `blast_radius` is
        #    written by whoever writes the exemption. Reachability is not.
        if r["reachable_from_run_guards"]:
            v.append(
                f"[exemption-refused] {r['guard']} is REACHABLE from "
                f"run_guards.py, so it runs in the required `guards` merge "
                f"context and may not be exemption-listed whatever it declares "
                f"— a gate that runs on every PR and cannot fail is worse than "
                f"no gate. Give it a planted-failure self-test, or remove it "
                f"from run_guards.py.")

    # 4. An exemption naming a guard that is no longer uncovered is stale, and
    #    a stale exemption is how a real finding gets silently pre-approved.
    live = {r["guard"] for r in none}
    for guard in sorted(exemptions):
        if guard not in live:
            known = any(r["guard"] == guard for r in rows)
            why = ("now HAS a failure path — delete the exemption"
                   if known else "names no guard on disk")
            v.append(f"[stale-exemption] {EXEMPTIONS.name} exempts '{guard}', which {why}.")

    # 5. THE RATCHET.
    if stats["guards_context"] < floor:
        v.append(
            f"[ratchet] guards-context coverage fell to {stats['guards_context']} "
            f"from a floor of {floor}. A PR may raise this floor and may never "
            f"lower it. If a guard was deleted, lower COVERAGE_FLOOR in the same "
            f"commit and say which guard went.")

    # 6. A self-test that prints but never compares anything is decoration.
    for r in covered:
        if not r["asserts_a_verdict"]:
            v.append(
                f"[no-assertion] {r['guard']} has a self-test wired into CI "
                f"(path {r['path']}) that exercises nothing or compares nothing. "
                f"A self-test that prints and exits 0 regardless is the "
                f"presence-only failure this repo already paid for with "
                f"`new-table-wiring-guard`'s marker.")
    return v, stats


# ---------------------------------------------------------------------------
# reporting


def report(rows: List[dict], stats: dict, verbose: bool) -> None:
    total = stats["total"]
    cov = stats["guards_context"]
    pct = (cov / total * 100) if total else 0.0
    # BOTH terms, so the denominator is in the log rather than inferable.
    print(f"guard-selftest-coverage: {cov}/{total} guards ({pct:.1f}%) have a "
          f"failure path that RESOLVES inside the required `guards` context "
          f"(floor {COVERAGE_FLOOR}).")
    print(f"  pytest_only : {stats['pytest_only']:>3}  — a file under tests/ NAMES the "
          f"guard. UNPROVEN, not disproven; never added to the covered count.")
    print(f"  none        : {stats['none']:>3}  — no failure-path evidence anywhere.")
    print(f"  of the {cov} covered, {stats['asserts_a_verdict']} exercise something and "
          f"compare the answer (GATED), and {stats['asserts_on_main_or_rc']} assert on "
          f"`main()`'s return or a subprocess return code (REPORTED, not gated — see "
          f"the note beside _STRICT_RETURN).")
    if stats["declared_but_never_invoked"]:
        print("  declared `--self-test` that run_guards.py never invokes with it:")
        for g in stats["declared_but_never_invoked"]:
            print(f"      {g}")
    if verbose:
        for r in rows:
            if r["population"] != "guards_context":
                print(f"      [{r['population']:<12}] {r['guard']}")


# ---------------------------------------------------------------------------
# self-test


def self_test() -> int:
    """Plant a guard with no failure path and require main() to REFUSE it.

    A coverage instrument whose own failure path is unexercised would be the
    finding it exists to report, one level up. Every control runs against a
    throwaway repo in $TMPDIR; nothing under the real tree is written.
    """
    fails: List[str] = []

    def check(label: str, got, want) -> None:
        if got != want:
            fails.append(f"  FAIL - {label}: got {got!r}, want {want!r}")
        else:
            print(f"  PASS - {label}")

    def build(tmp: Path, *, guard_body: str, run_guards_steps: str,
              registry: str = "SELFTESTS = {}\n", tests: str = "") -> Path:
        (tmp / "scripts" / "ci").mkdir(parents=True, exist_ok=True)
        (tmp / "tests").mkdir(parents=True, exist_ok=True)
        (tmp / "scripts" / "ci" / "check_planted.py").write_text(guard_body)
        (tmp / "scripts" / "ci" / "run_guards.py").write_text(
            "GUARDS = [\n" + run_guards_steps + "]\n")
        (tmp / "scripts" / "ci" / "guard_selftests.py").write_text(registry)
        if tests:
            (tmp / "tests" / "test_planted.py").write_text(tests)
        return tmp

    BARE = "import sys\nprint('ok')\nsys.exit(0)\n"
    WIRED = (
        "import argparse, sys\n"
        "def self_test():\n"
        "    if main([]) != 0:\n"
        "        return 1\n"
        "    return 0\n"
        "def main(argv=None):\n"
        "    ap = argparse.ArgumentParser()\n"
        "    ap.add_argument('--self-test', action='store_true')\n"
        "    a = ap.parse_args(argv)\n"
        "    return self_test() if a.self_test else 0\n"
    )
    STEP_PLAIN = '    ["python3", "scripts/ci/check_planted.py"],\n'
    STEP_WRAPPED = ('    ["python3", "scripts/ci/check_planted.py",\n'
                    '     "--self-test"],\n')

    with tempfile.TemporaryDirectory() as td:
        # 1. THE PLANT: a guard in CI with no failure-path evidence at all.
        t = build(Path(td) / "a", guard_body=BARE, run_guards_steps=STEP_PLAIN)
        rows = assess(t)
        check("a guard with no self-test lands in population 'none'",
              [r["population"] for r in rows], ["none"])
        v, _ = evaluate(rows, {}, floor=0)
        check("...and it is a violation", any("[uncovered]" in x for x in v), True)

        # 2. A WRAPPED argv still counts as invoked. This is the F-61 control:
        #    a single-line grep reported four correctly-wired guards as
        #    `invoked=0` because their argv spans two source lines. Reading the
        #    AST is the fix, and this control is what proves the fix holds.
        t = build(Path(td) / "b", guard_body=WIRED, run_guards_steps=STEP_WRAPPED)
        rows = assess(t)
        check("a `--self-test` argv WRAPPED across two source lines counts as invoked",
              [r["population"] for r in rows], ["guards_context"])
        check("...and no violation is raised", evaluate(rows, {}, floor=1)[0], [])

        # 3. A pytest file naming the guard is NOT coverage. Collapsing this
        #    into the covered count is the specific over-credit the 2026-09-09
        #    audit warned about and corrected in its own numbers.
        t = build(Path(td) / "c", guard_body=BARE, run_guards_steps=STEP_PLAIN,
                  tests="import check_planted\n")
        rows = assess(t)
        check("a pytest file that NAMES the guard is 'pytest_only', not covered",
              [r["population"] for r in rows], ["pytest_only"])
        _, st = evaluate(rows, {}, floor=0)
        check("...and it is NOT added to the covered count", st["guards_context"], 0)

        # 4. A declared `--self-test` that nothing invokes is not coverage --
        #    the F-61 class, reported by name so it cannot hide as 'none'.
        t = build(Path(td) / "d", guard_body=WIRED, run_guards_steps=STEP_PLAIN)
        rows = assess(t)
        check("a declared-but-never-invoked `--self-test` is not coverage",
              rows[0]["population"], "none")
        check("...and it is named as declared_but_never_invoked",
              evaluate(rows, {}, floor=0)[1]["declared_but_never_invoked"],
              ["scripts/ci/check_planted.py"])

        # 5. THE EXEMPTION RULES. Each is refused for its OWN reason, and the
        #    reachability rule must bite even when blast_radius reads benign --
        #    that is what stops the exemption from being cheaper to lie to than
        #    to satisfy.
        t = build(Path(td) / "e", guard_body=BARE, run_guards_steps=STEP_PLAIN)
        rows = assess(t)
        ex_money = {"scripts/ci/check_planted.py": {
            "reason": "r", "added": "2026-09-09", "blast_radius": "accounting",
            "tracking_id": "MI-226"}}
        v, _ = evaluate(rows, ex_money, floor=0)
        check("an 'accounting' blast radius may NOT be exemption-listed",
              any("[exemption-refused]" in x and "accounting" in x for x in v), True)
        ex_benign = {"scripts/ci/check_planted.py": {
            "reason": "r", "added": "2026-09-09", "blast_radius": "docs",
            "tracking_id": "MI-226"}}
        v, _ = evaluate(rows, ex_benign, floor=0)
        check("a REACHABLE guard may not be exempted even declaring 'docs'",
              any("REACHABLE" in x for x in v), True)

        # 6. ...and an UNREACHABLE guard IS exemptible, or the rule would be a
        #    blanket ban rather than a rule, and nobody could ever land one.
        t = build(Path(td) / "f", guard_body=BARE, run_guards_steps="")
        rows = assess(t)
        check("an UNREACHABLE guard with a complete exemption is accepted",
              evaluate(rows, ex_benign, floor=0)[0], [])

        # 7. A stale exemption is itself a finding: it silently pre-approves a
        #    guard that may since have regressed, or names nothing at all.
        t = build(Path(td) / "g", guard_body=WIRED, run_guards_steps=STEP_WRAPPED)
        rows = assess(t)
        v, _ = evaluate(rows, ex_benign, floor=1)
        check("an exemption for a now-covered guard is flagged stale",
              any("[stale-exemption]" in x for x in v), True)

        # 8. THE RATCHET only ever moves up.
        check("coverage below the floor is a violation",
              any("[ratchet]" in x for x in evaluate(rows, {}, floor=99)[0]), True)
        check("coverage at the floor is not",
              any("[ratchet]" in x for x in evaluate(rows, {}, floor=1)[0]), False)

        # 9. A self-test that never asserts on a return is decoration.
        PRINTER = (
            "import argparse, sys\n"
            "def self_test():\n"
            "    print('PASS everything is fine')\n"
            "    return 0\n"
            "def main(argv=None):\n"
            "    ap = argparse.ArgumentParser()\n"
            "    ap.add_argument('--self-test', action='store_true')\n"
            "    a = ap.parse_args(argv)\n"
            "    return self_test() if a.self_test else 0\n"
        )
        t = build(Path(td) / "h", guard_body=PRINTER, run_guards_steps=STEP_WRAPPED)
        rows = assess(t)
        check("a print-only self-test is wired but flagged [no-assertion]",
              any("[no-assertion]" in x for x in evaluate(rows, {}, floor=1)[0]), True)

        # 10. A MALFORMED exemption file is a finding, never an empty set --
        #     reading it as "nothing is exempt" would flip the verdict silently.
        bad = Path(td) / "bad.json"
        bad.write_text("{not json")
        check("an unreadable exemption file is reported, not read as empty",
              load_exemptions(bad)[1] != [], True)
        incomplete = Path(td) / "inc.json"
        incomplete.write_text(json.dumps({"exemptions": [{"guard": "g", "reason": "r"}]}))
        check("an exemption missing added/blast_radius/tracking_id is reported",
              len(load_exemptions(incomplete)[1]), 3)
        undated = Path(td) / "undated.json"
        undated.write_text(json.dumps({"exemptions": [
            {"guard": "g", "reason": "r", "added": "soon",
             "blast_radius": "docs", "tracking_id": "MI-226"}]}))
        check("an unparseable 'added' date is reported",
              any("unparseable" in p for p in load_exemptions(undated)[1]), True)

        # 11. THE DENOMINATOR IS THE FILESYSTEM. A guard on disk that appears
        #     in no registry and no run_guards step must still be COUNTED --
        #     measuring the registry rather than the population is the whole
        #     defect this instrument exists to close.
        t = build(Path(td) / "i", guard_body=BARE, run_guards_steps="")
        (t / "scripts" / "check_second.py").write_text(BARE)
        check("a guard reachable from nothing is still in the denominator",
              evaluate(assess(t), {}, floor=0)[1]["total"], 2)

    if fails:
        print("\n".join(fails))
        print("\nSELF-TEST FAILED")
        return 1
    print("\nALL PASS")
    return 0


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--self-test", action="store_true",
                    help="run the planted-failure controls and exit")
    ap.add_argument("--verbose", action="store_true",
                    help="list every guard that is not covered")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()

    if not RUN_GUARDS.exists() or not SELFTEST_MODULE.exists():
        print("::error::guard-selftest-coverage: run_guards.py or "
              "guard_selftests.py is missing — refusing to report coverage over "
              "a population it could not resolve.", file=sys.stderr)
        return 2

    exemptions, structural = load_exemptions(EXEMPTIONS)
    if structural:
        for p in structural:
            print(f"::error::guard-selftest-coverage: {p}")
        return 2

    rows = assess(REPO)
    violations, stats = evaluate(rows, exemptions, COVERAGE_FLOOR)
    report(rows, stats, args.verbose)

    if not violations:
        if stats["guards_context"] > COVERAGE_FLOOR:
            print(f"  note: coverage is {stats['guards_context']}, above the floor of "
                  f"{COVERAGE_FLOOR}. Raise COVERAGE_FLOOR to "
                  f"{stats['guards_context']} to bank it.")
        print("guard-selftest-coverage: OK")
        return 0

    print("")
    for line in violations:
        print(f"::error::guard-selftest-coverage: {line}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
