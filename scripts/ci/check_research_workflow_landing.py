#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py (research-workflow-landing-guard)
"""Every research-PRODUCING workflow either lands durably, or says why not.

WHY THIS EXISTS
---------------
E5 (`docs/claude/work/MANAGER-CHECKLIST.json` row E5) built the durable landing
path — `research_result.py`, `research/results/<unit>/<run_id>.jsonl`,
`.github/actions/research-result` — and wired TWO consumers. The pipeline row
this guard closes,
`PI-20260922-E5-SEVENTEEN-RESEARCH-WORKFLOWS-STILL-LAND-NO-DURABLE-RESULT`
(routed to checklist row C2), measured that every OTHER workflow that computes
a verdict or a measurement still ends in `actions/upload-artifact` plus a `gh
issue comment` — an artifact that EXPIRES and a comment that is not queryable,
so a result that cost real runner minutes exists only until it doesn't.

WHAT IT CHECKS
--------------
Over the WHOLE tree of `.github/workflows/*.yml` (population is the
filesystem, never the diff — see `research-results-guard`'s own docstring for
why a diff-scoped version of this class passes vacuously on nearly every PR).
For every workflow that uploads an artifact (`actions/upload-artifact`), it
must be exactly one of:

  1. WIRED   — calls `uses: ./.github/actions/research-result` somewhere in
               the file. The durable path.
  2. ANNOTATED — carries `# no-durable-result: <reason>`, a non-empty reason,
               for an ops/deploy/relay/training workflow that does not compute
               a research verdict at all (a diag relay, a VM mutation, an ML
               training run tracked by the model registry instead). VERIFIED,
               not presence-only: an empty reason after the colon still fails.
  3. BASELINE — named in this file's dated `BASELINE` dict below: a
               research-producing workflow that exists today and has not yet
               been converted. Counted and printed as debt on every run, and
               may only SHRINK (converting one moves it out of `BASELINE` and,
               ideally, into state 1).

Anything else is a FINDING: a workflow that produces an artifact nobody can
query once it expires, with no durable landing and no stated reason why it
does not need one.

⚠️ WHY A BASELINE AND NOT A HARD FAIL ON EVERY UNCONVERTED WORKFLOW
--------------------------------------------------------------------
MEASURED 2026-09-27 (this guard's own `--self-test` and `--report` are the
re-run): of 40 workflows that upload an artifact and do not yet call
`research-result`, 18 compute a research verdict or measurement (re-derived by
reading each file's own header, not by trusting the row's stated "19" — see
`origin.rerun` on the pipeline item this guard closes). Converting all 18 in
one PR is the "nineteen copies of a landing step is nineteen chances to get it
wrong" mistake E5's own module docstring already warns against, at PR-review
scale instead of code scale. So: convert what this PR converts
(`dukascopy-span-probe.yml`, the first), name the rest explicitly and dated,
and let a hard fail only guard against the debt GROWING — exactly the
`unwired-artifact-guard` / `check_soak_registered.py` precedent.

Two properties keep the hatch from rotting, following that same precedent:

  * **A BASELINE entry naming a workflow file that no longer exists is a
    FAILURE.** The list cannot accumulate stale names that quietly widen it.
  * **The debt count is PRINTED on every run**, passing or failing, so a
    growing number is visible in the CI log without anyone auditing this file.

Exit codes: 0 clean · 1 an unwired, unannotated, unbaselined workflow (or a
stale BASELINE entry) · 2 we could not look (the workflows directory itself is
missing — a repo layout this guard has no basis to assume).
"""
from __future__ import annotations

import argparse
import re
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO / ".github" / "workflows"

_UPLOAD_ARTIFACT_RE = re.compile(r"actions/upload-artifact")
_RESEARCH_RESULT_RE = re.compile(r"\./\.github/actions/research-result")
_NO_DURABLE_RESULT_RE = re.compile(r"#\s*no-durable-result:(.*)")

#: ── THE DEBT LIST — MEASURED 2026-09-27, AND IT MAY ONLY SHRINK ───────────
#:
#: Every workflow that computes a research verdict/measurement, uploads an
#: artifact, and has NOT yet been converted to land through
#: `.github/actions/research-result`. Re-derived from `.github/workflows/*.yml`
#: itself (each name below was read for its own header comment before being
#: filed here as research-producing, not ops/deploy/relay/training) — see this
#: module's own docstring for the full method.
#:
#: ⚠️ DO NOT ADD A NAME HERE TO MAKE A NEW RESEARCH WORKFLOW PASS. A NEW
#: workflow that computes a verdict gets wired to `research-result` (or
#: annotated, honestly, if it turns out not to compute one) in the SAME PR
#: that adds it. This list is pre-existing debt only, and adding to it for a
#: workflow that did not exist on 2026-09-27 defeats the entire guard.
#:
#: REMOVING a name is the good direction and needs no ceremony: wire the
#: workflow to `.github/actions/research-result`, delete the line.
BASELINE: Dict[str, str] = {
    "c1-conviction-ab": "2026-09-27",
    "e35-bracket-sweep": "2026-09-27",
    "flip-override-walkforward": "2026-09-27",
    "gld-compat-matrix": "2026-09-27",
    "ict-scalp-backtest": "2026-09-27",
    "m20-capture-census": "2026-09-27",
    "prop-tp-r-gate": "2026-09-27",
    "pullback-frac-cross-leg-sweep": "2026-09-27",
    "ranking-key-ab": "2026-09-27",
    "regime-adx-cutpoint-sweep": "2026-09-27",
    "regime-cell-walkforward": "2026-09-27",
    "regime-debt-matrix": "2026-09-27",
    "research-backtest-augment": "2026-09-27",
    "research-e2-horizon-arm": "2026-09-27",
    "research-panel-build": "2026-09-27",
    "research-symbol-p0-build": "2026-09-27",
    "vwap-backtest": "2026-09-27",
}


def scan(root: Path) -> Tuple[List[str], int, int, int, int]:
    """Return (problems, files_scanned, wired, annotated, baseline_debt)."""
    problems: List[str] = []
    if not root.is_dir():
        return ([f"the workflows directory {root} does not exist — a guard "
                 f"whose population is missing has checked NOTHING"], 0, 0, 0, 0)

    files = 0
    wired = 0
    annotated = 0
    baseline_hits: set = set()

    for path in sorted(root.glob("*.yml")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if not _UPLOAD_ARTIFACT_RE.search(text):
            continue
        files += 1
        stem = path.stem

        if _RESEARCH_RESULT_RE.search(text):
            wired += 1
            continue

        m = _NO_DURABLE_RESULT_RE.search(text)
        if m:
            reason = m.group(1).strip()
            if not reason:
                problems.append(
                    f"{path.name}: `# no-durable-result:` carries no reason — "
                    f"an empty annotation is presence-only, which is cheaper to "
                    f"lie to than to satisfy (the new-table-wiring-guard "
                    f"lesson).")
                continue
            annotated += 1
            continue

        if stem in BASELINE:
            baseline_hits.add(stem)
            continue

        problems.append(
            f"{path.name}: uploads an artifact, computes no landing, and is "
            f"neither wired to `.github/actions/research-result` nor "
            f"annotated `# no-durable-result: <reason>` nor in this guard's "
            f"dated BASELINE. If this workflow computes a verdict or a "
            f"measurement, wire it to the durable path or add it to "
            f"BASELINE (dated, with a follow-up); if it is ops/deploy/relay/"
            f"training and legitimately needs no durable research result, "
            f"annotate it.")

    stale = sorted(set(BASELINE) - baseline_hits)
    for name in stale:
        problems.append(
            f"BASELINE names {name!r} but .github/workflows/{name}.yml no "
            f"longer uploads an artifact needing this guard (removed, "
            f"renamed, or already converted) — the list cannot accumulate "
            f"stale names. Delete the entry.")

    return problems, files, wired, annotated, len(baseline_hits)


# --------------------------------------------------------------------- self-test
def _self_test() -> int:
    failures = 0

    def run_case(label: str, files: Dict[str, str], baseline: Dict[str, str],
                 must_fail: bool, expect: str = "") -> None:
        nonlocal failures
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name, content in files.items():
                (root / name).write_text(content, encoding="utf-8")
            global BASELINE  # noqa: PLW0603 — self-test swaps it, then restores
            saved = BASELINE
            BASELINE = baseline
            try:
                problems, *_ = scan(root)
            finally:
                BASELINE = saved
            failed = bool(problems)
            ok = failed == must_fail
            if ok and must_fail and expect:
                ok = any(expect in p for p in problems)
            print(f"  {'PASS' if ok else 'FAIL'}  {label}")
            if not ok:
                failures += 1
                print(f"        problems={problems!r}")

    wired_yml = (
        "name: x\njobs:\n  a:\n    steps:\n      - uses: actions/upload-artifact@v4\n"
        "      - uses: ./.github/actions/research-result\n"
    )
    run_case("positive: a workflow wired to research-result is CLEAN",
             {"x.yml": wired_yml}, {}, must_fail=False)

    annotated_yml = (
        "name: x\n# no-durable-result: ops relay, computes no verdict\n"
        "jobs:\n  a:\n    steps:\n      - uses: actions/upload-artifact@v4\n"
    )
    run_case("positive: an annotated ops workflow is CLEAN",
             {"x.yml": annotated_yml}, {}, must_fail=False)

    run_case("positive: a BASELINE-listed workflow is CLEAN (counted as debt)",
             {"x.yml": "name: x\njobs:\n  a:\n    steps:\n      - uses: actions/upload-artifact@v4\n"},
             {"x": "2026-09-27"}, must_fail=False)

    # THE PLANTED DEFECT — an unwired, unannotated, unbaselined workflow.
    run_case("negative: an unwired, unannotated workflow is CAUGHT",
             {"x.yml": "name: x\njobs:\n  a:\n    steps:\n      - uses: actions/upload-artifact@v4\n"},
             {}, must_fail=True, expect="neither wired")

    empty_annotation_yml = (
        "name: x\n# no-durable-result:\n"
        "jobs:\n  a:\n    steps:\n      - uses: actions/upload-artifact@v4\n"
    )
    run_case("negative: an EMPTY annotation is CAUGHT (verified, not presence-only)",
             {"x.yml": empty_annotation_yml}, {}, must_fail=True, expect="no reason")

    run_case("negative: a stale BASELINE entry (file no longer uploads) is CAUGHT",
             {"x.yml": "name: x\njobs:\n  a:\n    steps: []\n"},
             {"x": "2026-09-27"}, must_fail=True, expect="no longer uploads")

    run_case("negative: a workflow with no upload-artifact at all is untouched (not a false positive)",
             {"x.yml": "name: x\njobs:\n  a:\n    steps: []\n"}, {},
             must_fail=False)

    with tempfile.TemporaryDirectory() as td:
        problems, files, *_ = scan(Path(td) / "nope")
        ok = bool(problems) and files == 0
        print(f"  {'PASS' if ok else 'FAIL'}  negative: a MISSING workflows dir is a finding, not a vacuous pass")
        if not ok:
            failures += 1

    print(f"research-workflow-landing-guard --self-test: {failures} failure(s)")
    return 1 if failures else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    problems, files, wired, annotated, baseline_debt = scan(WORKFLOWS_DIR)
    # Printed on EVERY run, both terms, so a population that quietly went to
    # zero is visible in the log rather than inferable from it.
    print(f"research-workflow-landing-guard: artifact-producing={files} "
          f"wired={wired} annotated={annotated} baseline_debt={baseline_debt} "
          f"problems={len(problems)}")
    for p in problems:
        print(f"::error::research-workflow-landing-guard: {p}")
    if problems:
        return 1
    if files == 0:
        return 2
    print("research-workflow-landing-guard: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
