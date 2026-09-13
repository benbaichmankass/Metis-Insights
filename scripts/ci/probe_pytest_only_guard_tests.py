#!/usr/bin/env python3
"""Do the `pytest_only` guards' tests actually BIND to their guard's behaviour?

`check_guard_selftest_coverage.py` answers *does a file under tests/ name this
guard?* and is deliberately careful to call that population `pytest_only` —
UNPROVEN, never added to the covered count. This probe answers the next
question, which only an executed run can answer: **break the guard and does
that test go red?**

It exists because `BL-20260909-21-GUARDS-REST-THEIR-ONLY-FAILURE-PATH-EVIDENCE-ON-A-PYTEST-FILE-NOBODY-HAS-EXECUTED-AGAINST-A-PLANT`
recorded a *static* reading of those tests — 21 of 21 appeared to plant a
violation — and said plainly that a reading of source is not an observation of
a red test. The session that filed it had no pytest in its container.

⚠️ IT DOES NOT RE-DERIVE THE POPULATION. The list comes from
`check_guard_selftest_coverage.assess()`, imported. A second copy of "which
guards are pytest_only" is exactly the drifting-second-registry class this repo
keeps paying for, and it would let the two instruments disagree about the
denominator while both reported confidently.

⚠️ IT IS NOT WIRED INTO `run_guards.py`, DELIBERATELY. It mutates guard source
on disk and runs pytest per guard (~50s for the whole population). A per-PR gate
that rewrites files in the working tree would race any concurrent job and, on a
crash, leave the tree mutated — the cure being worse than the disease. Run it on
demand, the way `--all` audits are run.

THE PLANTS, tried in order, because these tests reach their guard through two
different channels and one plant can only see one of them:

  T (detector)  every top-level guard function THE TEST CALLS is made to return
                []. The detector finds nothing, ever. This reaches a test that
                asserts on a findings list, which is how most are written.
  E (exit code) every bare `return 1` / `sys.exit(1)` / `raise SystemExit(1)`
                inside a function becomes its 0 counterpart. The guard can no
                longer refuse. This reaches a test that runs the guard as a
                subprocess or asserts on `main()`.

⚠️ PLANT T FINDS THE FUNCTIONS BY SCANNING THE TEST FOR `name(` AND INTERSECTING
   WITH THE GUARD'S OWN TOP-LEVEL FUNCTION NAMES — never by parsing
   `from <mod> import ...`. That narrower version was tried first and produced a
   FALSE ANSWER: six of these tests load the guard through
   `importlib.spec_from_file_location` or `import_module` and then call module
   attributes, so the import regex matched nothing, plant T never applied, and
   all six graded ESCAPED. A plant that does not plant the defect is
   indistinguishable from one that escapes. Measured across three successive
   versions of this probe the same population graded 6, then 12, then 22 CAUGHT
   — the subject never moved; only the plant's reach did.

STATES, never collapsed:

  CAUGHT     a plant landed and the test refused it. Carries WHY it went red:
             `assertion` — the test noticed the planted violation going
             unflagged, the strong form; or `error` — the test could not run
             against a mutilated module, a real refusal but a weaker claim.
             ⚠️ Do not pool those two in prose.
  ESCAPED    a plant landed, the test stayed green. THE FINDING this probe
             exists to surface.
  NO_PLANT   neither plant has a site in this guard. Grades NOTHING — it is not
             an escape, and reporting it as one would be a false accusation.
  PRE_RED    the test file was already failing before any plant, so this run
             says nothing about the plant. Usually a missing dependency in the
             container rather than a repo defect: `fastapi` had to be installed
             for two of them, and both are green in CI.

⚠️ A SECOND LIMIT, IN PLANT E: it rewrites only a BARE `return 1` statement,
   `sys.exit(1)` and `raise SystemExit(1)`. A guard that reports failure through
   a conditional expression (`return 1 if bad else 0`) or a computed exit code
   gives plant E no site, and the guard grades NO_PLANT unless plant T reaches
   it. NO_PLANT is therefore *we could not plant*, never *the test is fine* —
   which is why it is a state of its own rather than folded into either answer.

⚠️ STATED LIMIT, and it is the one that matters. The plant is TOTAL — the
   detector returns nothing at all. A pass here shows the test binds to the
   guard's behaviour; it does NOT show the test would catch a SUBTLE
   regression, such as one weakened pattern out of eight. That is a different
   and harder question and this probe does not answer it.
"""
# wiring: manual-only - it REWRITES guard source in the working tree and runs
# pytest per guard (~47s over the population). Every other runner here is one a
# session invokes locally as well as in CI -- `run_guards.py` most of all -- and
# a step that mutates files under a developer's feet would race any concurrent
# job and, if it died mid-run, leave the tree dirty with a planted defect in a
# GUARD. That is strictly worse than the gap it closes.
#
# ⚠️ THIS IS A DECLARED CHOICE, NOT AN OVERSIGHT, AND ITS COST IS STATED: an
#    instrument nobody runs is the carried-by-nothing class this repo keeps
#    paying for, so a NEW `pytest_only` guard whose test does not bind will not
#    be caught by anything until someone runs this by hand. What would give it a
#    carrier is a SCHEDULED workflow on an ephemeral runner, where mutating the
#    checkout is harmless -- deliberately not added here, because a cron whose
#    red nobody reads is the desensitised alarm one level up, and this repo has
#    measured its schedules firing late and erratically. Wiring it needs a
#    reader first.
from __future__ import annotations

import argparse
import ast
import importlib.util
import pathlib
import re
import subprocess
import sys
import time
from collections import Counter
from typing import Dict, Iterable, List, Sequence, Set, Tuple

REPO = pathlib.Path(__file__).resolve().parents[2]
COVERAGE_INSTRUMENT = REPO / "scripts" / "ci" / "check_guard_selftest_coverage.py"

CAUGHT, ESCAPED, NO_PLANT, PRE_RED = "CAUGHT", "ESCAPED", "NO_PLANT", "PRE_RED"


# --------------------------------------------------------------------------
# population — imported, never re-derived
# --------------------------------------------------------------------------
def pytest_only_population(repo: pathlib.Path = REPO) -> Dict[str, List[str]]:
    """{guard path: [test files]} for every guard graded `pytest_only`."""
    spec = importlib.util.spec_from_file_location(
        "_guard_selftest_coverage", COVERAGE_INSTRUMENT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    rows = mod.assess(repo)
    return {str(r["guard"]): [str(t) for t in (r["pytest_files"] or [])]
            for r in rows if r["population"] == "pytest_only"}


# --------------------------------------------------------------------------
# the plants
# --------------------------------------------------------------------------
def called_guard_functions(repo: pathlib.Path, guard: str,
                           tests: Sequence[str]) -> Set[str]:
    """Every top-level function of `guard` that appears as `name(` in a test.

    Deliberately a call-site scan rather than an import scan — see the module
    docstring for the false answer the import scan gave.
    """
    try:
        tree = ast.parse((repo / guard).read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    fns = {n.name for n in tree.body
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    called: Set[str] = set()
    for t in tests:
        p = repo / t
        if not p.is_file():
            continue
        blob = p.read_text(encoding="utf-8", errors="replace")
        called |= set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", blob))
    return fns & called


def plant_detector(src: str, names: Iterable[str]) -> Tuple[str, int]:
    """Make each named top-level function return [] immediately."""
    names = set(names)
    lines = src.split("\n")
    planted = 0
    for target in sorted(names):
        tree = ast.parse("\n".join(lines))
        node = next((n for n in tree.body
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and n.name == target), None)
        if node is None:
            continue
        end = node.lineno - 1
        while end < len(lines) and not lines[end].rstrip().endswith(":"):
            end += 1
        if end + 1 >= len(lines):
            continue
        body_line = lines[end + 1]
        indent = " " * (len(body_line) - len(body_line.lstrip()) or 4)
        lines.insert(end + 1, f"{indent}return []  # PLANT-T")
        planted += 1
    return "\n".join(lines), planted


def plant_exit_code(src: str) -> Tuple[str, int]:
    """Make the guard unable to report failure through its exit code."""
    tree = ast.parse(src)
    spans = [(n.lineno, n.end_lineno) for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    lines = src.split("\n")
    planted = 0
    for lo, hi in spans:
        for i in range(lo - 1, min(hi, len(lines))):
            line = lines[i]
            if line.strip() == "return 1":
                lines[i] = line.replace("return 1", "return 0  # PLANT-E")
                planted += 1
            elif "sys.exit(1)" in line:
                lines[i] = line.replace("sys.exit(1)", "sys.exit(0)  # PLANT-E")
                planted += 1
            elif line.strip() == "raise SystemExit(1)":
                lines[i] = line.replace("SystemExit(1)", "SystemExit(0)  # PLANT-E")
                planted += 1
    return "\n".join(lines), planted


# --------------------------------------------------------------------------
# the probe
# --------------------------------------------------------------------------
def _pytest(repo: pathlib.Path, tests: Sequence[str],
            timeout: int = 600) -> Tuple[int, str]:
    cmd = [sys.executable, "-m", "pytest", *tests, "-q", "--no-header",
           "-p", "no:cacheprovider"]
    try:
        r = subprocess.run(cmd, cwd=repo, capture_output=True, text=True,
                           timeout=timeout)
        return r.returncode, (r.stdout + r.stderr)[-2000:]
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    except OSError as exc:
        return 125, f"could not run pytest: {exc}"


def _red_reason(out: str) -> str:
    """Why did it go red? `assertion` is the strong form; `error` is weaker."""
    low = out.lower()
    for hard in ("typeerror", "attributeerror", "valueerror", "importerror",
                 "modulenotfounderror", "indexerror", "keyerror"):
        if hard in low:
            return "error"
    return "assertion" if "assert" in low else "error"


def probe_one(repo: pathlib.Path, guard: str,
              tests: Sequence[str]) -> Dict[str, object]:
    """Grade one guard. Restores the file even on failure, and verifies it."""
    gp = repo / guard
    original = gp.read_text(encoding="utf-8")
    row: Dict[str, object] = {"guard": guard, "tests": list(tests)}

    rc0, out0 = _pytest(repo, tests)
    if rc0 != 0:
        row.update(state=PRE_RED, by="", reason="", detail=out0.strip().splitlines()[-1:])
        return row

    names = called_guard_functions(repo, guard, tests)
    attempts = [("T", plant_detector(original, names)), ("E", plant_exit_code(original))]
    row["sites"] = {tag: n for tag, (_, n) in attempts}

    for tag, (mutated, n) in attempts:
        if n == 0 or mutated == original:
            continue
        try:
            ast.parse(mutated)
        except SyntaxError:
            continue  # a plant that will not parse grades nothing
        try:
            gp.write_text(mutated, encoding="utf-8")
            rc1, out1 = _pytest(repo, tests)
        finally:
            gp.write_text(original, encoding="utf-8")
        if gp.read_text(encoding="utf-8") != original:
            raise RuntimeError(f"RESTORE FAILED for {guard} — the tree is dirty")
        if rc1 != 0:
            row.update(state=CAUGHT, by=tag, reason=_red_reason(out1))
            return row

    planted = sum(row["sites"].values())  # type: ignore[union-attr]
    row.update(state=NO_PLANT if planted == 0 else ESCAPED, by="", reason="")
    return row


def probe(repo: pathlib.Path,
          population: Dict[str, List[str]] | None = None,
          verbose: bool = True) -> List[Dict[str, object]]:
    pop = population if population is not None else pytest_only_population(repo)
    rows: List[Dict[str, object]] = []
    for guard, tests in sorted(pop.items()):
        row = probe_one(repo, guard, tests)
        rows.append(row)
        if verbose:
            by = f"{row['by']}:{row['reason']}" if row.get("by") else "-"
            print(f"  {guard:<48} {row['state']:<9} by={by}", flush=True)
    return rows


def report(rows: List[Dict[str, object]]) -> int:
    counts = Counter(r["state"] for r in rows)
    reasons = Counter(r["reason"] for r in rows if r["state"] == CAUGHT)
    print()
    print(f"population: {len(rows)} guard(s) graded `pytest_only`")
    for state in (CAUGHT, ESCAPED, NO_PLANT, PRE_RED):
        if counts.get(state):
            print(f"  {state:<9} {counts[state]}")
    if counts.get(CAUGHT):
        print(f"    of the CAUGHT: {reasons.get('assertion', 0)} by a failed ASSERTION "
              f"(the test noticed the planted violation going unflagged) and "
              f"{reasons.get('error', 0)} by an ERROR (a refusal, but a weaker claim — "
              f"do not pool them)")
    escaped = [r["guard"] for r in rows if r["state"] == ESCAPED]
    if escaped:
        print("\nESCAPED — the guard was broken and its test stayed green:")
        for g in escaped:
            print(f"  {g}")
        print("\n  A pytest file that names a guard is not evidence the guard's "
              "green can turn red.\n  Give it a control that plants a violation "
              "and asserts the guard REFUSES it.")
        return 1
    for state, note in ((NO_PLANT, "no plantable site — grades NOTHING, not an escape"),
                        (PRE_RED, "already failing before any plant — grades NOTHING "
                                  "(usually a missing dependency here, not a repo defect)")):
        named = [r["guard"] for r in rows if r["state"] == state]
        if named:
            print(f"\n{state} ({note}):")
            for g in named:
                print(f"  {g}")
    print("\nprobe-pytest-only-guard-tests: OK — every gradeable guard's test "
          "refused a broken guard.")
    return 0


# --------------------------------------------------------------------------
# self-test
# --------------------------------------------------------------------------
def self_test() -> int:
    """Plant against SYNTHETIC guards, so every state is argued here.

    The bar is not that these print. It is that the probe returns the RIGHT
    STATE for a guard whose test does not bind — because a probe that can only
    say CAUGHT is the same unexercised instrument as the tests it grades.
    """
    import shutil
    import tempfile

    fails: List[str] = []

    def check(name: str, got, want) -> None:
        if got == want:
            print(f"  PASS - {name}")
        else:
            fails.append(f"  FAIL - {name}: got {got!r}, want {want!r}")

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="probe-selftest-"))
    try:
        (tmp / "scripts").mkdir()
        (tmp / "tests").mkdir()

        # A guard that finds a violation, and a test that asserts it does.
        (tmp / "scripts" / "g_ok.py").write_text(
            "def find(text):\n"
            "    return ['bad'] if 'bad' in text else []\n"
            "def main(argv=None):\n"
            "    return 1 if find('bad') else 0\n", encoding="utf-8")
        (tmp / "tests" / "test_g_ok.py").write_text(
            "import importlib.util, pathlib\n"
            "_s = importlib.util.spec_from_file_location('g', "
            "pathlib.Path(__file__).resolve().parents[1] / 'scripts' / 'g_ok.py')\n"
            "g = importlib.util.module_from_spec(_s); _s.loader.exec_module(g)\n"
            "def test_planted_violation_is_flagged():\n"
            "    assert g.find('bad') == ['bad']\n", encoding="utf-8")
        r = probe_one(tmp, "scripts/g_ok.py", ["tests/test_g_ok.py"])
        check("a test that asserts on the findings CATCHES a neutered detector",
              r["state"], CAUGHT)
        check("...and the red is reported as an ASSERTION, not an error",
              r["reason"], "assertion")

        # A guard whose test asserts nothing about it — the finding.
        # ⚠️ `main` here uses a BARE `return 1` on its own line, deliberately.
        #    The first version of this fixture wrote `return 1 if find(...) else 0`
        #    and the probe graded it NO_PLANT rather than ESCAPED — correctly,
        #    because plant E matches only a bare `return 1` statement and a
        #    conditional expression gave it no site. That is a real limit of
        #    plant E and it is stated in the module docstring; the fixture must
        #    exercise the ESCAPED state rather than tripping over the limit.
        (tmp / "scripts" / "g_blind.py").write_text(
            "def find(text):\n"
            "    return ['bad'] if 'bad' in text else []\n"
            "def main(argv=None):\n"
            "    if find('bad'):\n"
            "        return 1\n"
            "    return 0\n", encoding="utf-8")
        (tmp / "tests" / "test_g_blind.py").write_text(
            "def test_something_unrelated():\n"
            "    assert 1 + 1 == 2\n", encoding="utf-8")
        r = probe_one(tmp, "scripts/g_blind.py", ["tests/test_g_blind.py"])
        check("a test that never touches the guard ESCAPES, and is reported",
              r["state"], ESCAPED)

        # A guard with no plantable site at all.
        (tmp / "scripts" / "g_nosite.py").write_text(
            "VALUE = 3\n", encoding="utf-8")
        (tmp / "tests" / "test_g_nosite.py").write_text(
            "def test_ok():\n    assert True\n", encoding="utf-8")
        r = probe_one(tmp, "scripts/g_nosite.py", ["tests/test_g_nosite.py"])
        check("no plantable site is NO_PLANT, never ESCAPED", r["state"], NO_PLANT)

        # A test that is already red — grades nothing.
        (tmp / "scripts" / "g_prered.py").write_text(
            "def find(t):\n    return []\n", encoding="utf-8")
        (tmp / "tests" / "test_g_prered.py").write_text(
            "def test_broken():\n    assert False\n", encoding="utf-8")
        r = probe_one(tmp, "scripts/g_prered.py", ["tests/test_g_prered.py"])
        check("a test that was already failing is PRE_RED, never ESCAPED",
              r["state"], PRE_RED)

        # The exit-code channel, reached only by plant E.
        (tmp / "scripts" / "g_rc.py").write_text(
            "import sys\n"
            "def main(argv=None):\n"
            "    return 1\n"
            "if __name__ == '__main__':\n"
            "    sys.exit(main())\n", encoding="utf-8")
        (tmp / "tests" / "test_g_rc.py").write_text(
            "import subprocess, sys, pathlib\n"
            "def test_guard_refuses():\n"
            "    p = pathlib.Path(__file__).resolve().parents[1] / 'scripts' / 'g_rc.py'\n"
            "    assert subprocess.run([sys.executable, str(p)]).returncode == 1\n",
            encoding="utf-8")
        r = probe_one(tmp, "scripts/g_rc.py", ["tests/test_g_rc.py"])
        check("a subprocess test is caught by the EXIT-CODE plant", r["state"], CAUGHT)
        check("...and it is attributed to plant E, not T", r["by"], "E")

        # The guard file must be byte-identical after every probe above.
        check("the guard is restored byte-for-byte",
              (tmp / "scripts" / "g_ok.py").read_text(encoding="utf-8"),
              "def find(text):\n"
              "    return ['bad'] if 'bad' in text else []\n"
              "def main(argv=None):\n"
              "    return 1 if find('bad') else 0\n")

        # The call-site scan must see a function reached through importlib.
        names = called_guard_functions(tmp, "scripts/g_ok.py", ["tests/test_g_ok.py"])
        check("plant T finds a function called via importlib, not just via `from X import`",
              "find" in names, True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if fails:
        print("\n".join(fails))
        print("\nSELF-TEST FAILED")
        return 1
    print("\nALL PASS")
    return 0


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true",
                    help="run the planted controls against synthetic guards and exit")
    ap.add_argument("--list", action="store_true",
                    help="print the pytest_only population and exit")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()

    pop = pytest_only_population(REPO)
    if args.list:
        for guard, tests in sorted(pop.items()):
            print(f"{guard}\t{','.join(tests)}")
        return 0

    print(f"probing {len(pop)} `pytest_only` guard(s) — each is broken, then its "
          f"test is run and required to notice")
    t0 = time.time()
    rows = probe(REPO, pop)
    print(f"\nelapsed {time.time() - t0:.0f}s")
    return report(rows)


if __name__ == "__main__":
    raise SystemExit(main())
