#!/usr/bin/env python3
"""A green `pytest-run` reports `N skipped` and NOTHING about which N.

MEASURED, 2026-09-12. `tests/test_automerge_request_gate.py` carries a
module-level ``skipif(shutil.which("node") is None)`` over **all 14** of its
tests — the entire test suite for `check_automerge_trigger.py`, which is
LANDING_MACHINERY. Locally: node hidden -> `14 skipped`; node present ->
`14 passed`. Four separate CI `pytest-run` jobs each reported exactly
**`16xxx passed, 14 skipped`**.

⚠️ AND I COULD NOT TELL WHICH IT WAS. Three attempts, all inconclusive: no
`pytest-run` run old enough to predate the file survives in the last 100; the
other 50-odd conditionally-skipping modules could account for 14 between them;
and no CI log echoes a PATH. **That ambiguity is the defect.** A guard's whole
test module going dark is, from the CI summary, indistinguishable from routine
skips — which is the "green that checked nothing" class
`docs/CLAUDE-RULES-CANONICAL.md` names, arriving through the one door nobody
watches.

The report already exists: `pytest-run` writes `--junitxml=pytest-report.xml`
and parses it for FAILURES, `if: failure()`. Skips are never read. So this
costs no extra runtime and adds no dependency — it reads the same file.

⚠️ THE LOAD-BEARING DISTINCTION, and the reason this is not just a nicer log
line: **a module where EVERY collected test skipped is a different fact from a
module where some did.** The first means that file contributed nothing at all
and its subject is unguarded; the second is ordinary conditional coverage.
Collapsing them would reproduce the very ambiguity this exists to remove, so
`fully_dark` and `partial` are never pooled.

Run: ``python3 scripts/ci/summarize_pytest_skips.py --self-test``
"""
from __future__ import annotations

import argparse
import glob
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

# Four states, never collapsed. `UNREADABLE` is *we could not look* and is
# emphatically not `NO_SKIPS` — a report we failed to parse tells us nothing
# about what ran, and reporting it as a clean sheet is the exact substitution
# this module exists to refuse.
FULLY_DARK = "fully_dark"
PARTIAL = "partial"
NO_SKIPS = "no_skips"
UNREADABLE = "unreadable"
ALL_STATES = (FULLY_DARK, PARTIAL, NO_SKIPS, UNREADABLE)


def _module_of(testcase: ET.Element) -> str:
    """The module a testcase belongs to.

    ⚠️ REAL pytest EMITS NO ``file`` ATTRIBUTE — only a dotted ``classname``
    like ``tests.test_foo`` (or ``tests.test_foo.TestBar`` for a class-based
    test). The first version of this function fell back to
    ``classname.split(".")[0]``, which returns ``tests`` for EVERY test in the
    repo and collapses 16,500 tests into one pseudo-module — hiding a fully
    dark file inside a partial aggregate, i.e. committing the exact collapse
    this module exists to refuse. It passed a hand-written fixture that set
    ``file=`` and failed on the first REAL report. The self-test now generates
    its reports by running pytest, so a fixture can no longer diverge from what
    pytest actually writes.

    ``file`` is still preferred when present, since some junit families do emit
    it. Otherwise the dotted classname is used IN FULL — never a prefix. A
    trailing component in CapWords is dropped as a class name, which is a
    HEURISTIC on PEP8 and is stated as one; if it misfires the result is that
    one file reports as two modules, which over-reports darkness. That is the
    safe direction: splitting can never hide a dark file, and collapsing can.
    """
    f = testcase.get("file")
    if f:
        return f
    cn = (testcase.get("classname") or "").strip()
    if not cn:
        return "(unknown)"
    parts = cn.split(".")
    if len(parts) > 1 and parts[-1][:1].isupper():
        parts = parts[:-1]
    return ".".join(parts)


def parse(path: str) -> Optional[Dict[str, Tuple[int, int, List[str]]]]:
    """``{module: (skipped, total, [reasons])}``, or ``None`` if unreadable.

    ``None`` is *we could not look*. An empty dict is *we looked and the report
    had no testcases*, which is a different and also abnormal fact.
    """
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None
    agg: Dict[str, List] = defaultdict(lambda: [0, 0, []])
    for tc in root.iter("testcase"):
        mod = _module_of(tc)
        agg[mod][1] += 1
        sk = tc.find("skipped")
        if sk is not None:
            agg[mod][0] += 1
            reason = (sk.get("message") or sk.get("type") or "").strip()
            if reason and reason not in agg[mod][2]:
                agg[mod][2].append(reason)
    return {m: (v[0], v[1], v[2]) for m, v in agg.items()}


def grade(skipped: int, total: int) -> str:
    """Which of the four states a module is in."""
    if total <= 0:
        return UNREADABLE
    if skipped == 0:
        return NO_SKIPS
    return FULLY_DARK if skipped == total else PARTIAL


def render(parsed: Optional[Dict[str, Tuple[int, int, List[str]]]]) -> str:
    if parsed is None:
        return ("### pytest-run skips\n\n_No readable JUnit report — **we could "
                "not look**. This is not a report of zero skips._\n")
    if not parsed:
        return ("### pytest-run skips\n\n_The JUnit report listed no testcases "
                "at all — abnormal, and not a clean sheet._\n")

    dark, partial = [], []
    for mod, (sk, tot, reasons) in sorted(parsed.items()):
        st = grade(sk, tot)
        if st == FULLY_DARK:
            dark.append((mod, sk, tot, reasons))
        elif st == PARTIAL:
            partial.append((mod, sk, tot, reasons))

    total_sk = sum(v[0] for v in parsed.values())
    out = ["### pytest-run skips\n"]
    if not total_sk:
        out.append("_No tests were skipped._\n")
        return "\n".join(out)

    out.append(f"**{total_sk} skipped** across {len(dark) + len(partial)} "
               f"module(s) of {len(parsed)} that reported testcases.\n")
    if dark:
        out.append("#### ⚠️ Fully dark — every collected test skipped\n")
        out.append("These files contributed **nothing**. Whatever they guard "
                   "was not exercised by this run, and a green here is not "
                   "evidence about their subject.\n")
        out.append("| module | skipped / total | reason |")
        out.append("|---|---|---|")
        for mod, sk, tot, reasons in dark:
            out.append(f"| `{mod}` | **{sk} / {tot}** | {'; '.join(reasons) or '_none recorded_'} |")
        out.append("")
    if partial:
        out.append("#### Partial — ordinary conditional coverage\n")
        out.append("| module | skipped / total | reason |")
        out.append("|---|---|---|")
        for mod, sk, tot, reasons in partial:
            out.append(f"| `{mod}` | {sk} / {tot} | {'; '.join(reasons) or '_none recorded_'} |")
        out.append("")
    return "\n".join(out)


# ── self-test ───────────────────────────────────────────────────────────────
# ⚠️ THE REPORTS BELOW ARE GENERATED BY RUNNING PYTEST, NOT HAND-WRITTEN.
# The first version of this file used hand-written XML that set a `file`
# attribute. Real pytest does not emit one, so the fixture passed while the
# code collapsed every module in the repo into `tests`. A fixture that does not
# reproduce the producer is not a control — it is a second implementation of
# the thing under test, and it agreed with the bug.

_SRC_DARK = """import pytest
pytestmark = pytest.mark.skipif(True, reason="planted: tool absent")
def test_a(): pass
def test_b(): pass
"""
_SRC_MIXED = """import pytest
def test_ok(): pass
@pytest.mark.skipif(True, reason="planted: one condition")
def test_sk(): pass
"""
_SRC_FINE = """def test_x(): pass
def test_y(): pass
"""
_SRC_CLASSES = """import pytest
class TestOne:
    @pytest.mark.skipif(True, reason="planted: cls")
    def test_a(self): pass
class TestTwo:
    @pytest.mark.skipif(True, reason="planted: cls")
    def test_b(self): pass
"""


def _real_report(tmp: str, sources: Dict[str, str]) -> Optional[str]:
    """Run pytest over generated files and return the JUnit path it wrote."""
    import subprocess
    d = f"{tmp}/suite"
    import os
    os.makedirs(d, exist_ok=True)
    for name, body in sources.items():
        open(f"{d}/{name}", "w", encoding="utf-8").write(body)
    out = f"{tmp}/report.xml"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", d, "-q", "-p", "no:cacheprovider",
         f"--junitxml={out}"], capture_output=True, text=True, cwd=tmp)
    import os.path
    return out if os.path.exists(out) else None


def self_test(quiet: bool = False) -> int:
    import tempfile
    bad = []

    def check(cond, label):
        if not cond:
            bad.append(label)
        elif not quiet:
            print(f"  ok    {label}")

    with tempfile.TemporaryDirectory() as td:
        rep = _real_report(td, {"test_dark.py": _SRC_DARK,
                                "test_mixed.py": _SRC_MIXED,
                                "test_fine.py": _SRC_FINE})
        if rep is None:
            print("::error::self-test could not run pytest to produce a REAL "
                  "report — the control cannot be constructed, so this is not "
                  "a pass.")
            return 1
        got = parse(rep)
        check(got is not None, "a real pytest report parses")

        def find(stem):
            hits = [k for k in got if k.endswith(stem)]
            return got[hits[0]] if hits else None

        dark, mixed, fine = find("test_dark"), find("test_mixed"), find("test_fine")
        # ⚠️ THE REGRESSION CONTROL FOR THE COLLAPSE: three files must appear as
        # THREE modules. The bug made them one, keyed `tests`.
        check(len(got) == 3, f"three real files are THREE modules, not one "
                             f"(got {len(got)}: {sorted(got)})")
        check(dark == (2, 2, ["planted: tool absent"]), "a fully-dark file is counted whole")
        check(grade(*dark[:2]) == FULLY_DARK, "…and graded fully_dark")
        check(grade(*mixed[:2]) == PARTIAL, "a partial file is graded partial")
        check(grade(*fine[:2]) == NO_SKIPS, "a file with no skips is no_skips")

        body = render(got)
        check("Fully dark" in body, "the report NAMES the fully-dark section")
        check("test_dark" in body, "…and names the dark module")
        check("planted: tool absent" in body, "…and carries its reason, so it is actionable")
        check("test_fine" not in body, "a healthy module is not listed as skipped")
        check(body.index("Fully dark") < body.index("Partial"),
              "fully-dark is reported BEFORE partial, never pooled with it")

        # Two CLASSES in one file must stay ONE module, or a dark file splits.
        rep2 = _real_report(f"{td}/b", {"test_classes.py": _SRC_CLASSES})
        got2 = parse(rep2) if rep2 else None
        check(got2 is not None and len(got2) == 1,
              f"two classes in one file are ONE module (got {sorted(got2 or [])})")
        if got2:
            only = list(got2.values())[0]
            check(grade(*only[:2]) == FULLY_DARK,
                  "…so an all-skipped class-based file still grades fully_dark")

        # `we could not look` is its own state.
        check(parse(f"{td}/nope.xml") is None, "a missing report is None, not {}")
        open(f"{td}/bad.xml", "w").write("<not xml")
        check(parse(f"{td}/bad.xml") is None, "an unparseable report is None, not {}")
        check("could not look" in render(None), "…and the render SAYS so")
        check("_No tests were skipped._" not in render(None),
              "an unreadable report must never render as a clean sheet")
        check("no testcases" in render({}),
              "a report with no testcases renders as abnormal, not clean")

        rep3 = _real_report(f"{td}/c", {"test_clean.py": _SRC_FINE})
        check(rep3 is not None and "_No tests were skipped._" in render(parse(rep3)),
              "a genuinely skip-free run says so plainly")

    if bad:
        for b in bad:
            print(f"::error::self-test FAILED — {b}")
        return 1
    if not quiet:
        print(f"self-test OK — {len(ALL_STATES)} states, every report generated "
              "by REAL pytest, fully-dark separated from partial")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", default="pytest-report.xml")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    hits = glob.glob(a.report)
    print(render(parse(hits[0]) if hits else None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
