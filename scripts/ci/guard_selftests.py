#!/usr/bin/env python3
"""Failure-path self-tests for the CI guards that carry one.

A guard whose failure path is never exercised is indistinguishable from a guard
that always passes — the "green that checked nothing" this repo already has a
rule about (`docs/CLAUDE-RULES-CANONICAL.md` § "Green is not evidence"). Five
guards therefore feed themselves a known-bad input on every run and fail the job
unless the guard returns non-zero.

Those self-tests used to live as inline heredocs inside five separate workflow
YAML files. They are lifted here **verbatim in behaviour** as part of the CI
fan-out consolidation (BL-20260806-CI-FANOUT-AMPLIFIES-ACTIONS-OUTAGES) — same
bad input, same expected exit code, same failure message. Moving them out of
YAML also makes each one runnable locally:

    python3 scripts/ci/guard_selftests.py diagnostic-provenance
    python3 scripts/ci/guard_selftests.py --all

Each self-test cleans up whatever it planted, including on failure, so a run
never leaves a poisoned working tree behind for the next step.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Dict

REPO = Path(__file__).resolve().parents[2]


def _load(path: str, name: str = "g"):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@contextlib.contextmanager
def _planted(path: Path, content: str = ""):
    """Create a file for the duration of the test, then always remove it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    try:
        yield path
    finally:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


def _rc(argv) -> int:
    return subprocess.run(argv, cwd=REPO).returncode


# ---------------------------------------------------------------------------


def selftest_claim_basis() -> None:
    """A backlog claim row with no basis must be flagged."""
    sys.path.insert(0, str(REPO / "scripts"))
    from check_claim_basis import check_new_rows  # noqa: E402

    head = json.dumps(
        {
            "items": [
                {
                    "id": "BL-SELFTEST",
                    "title": "selftest",
                    "description": "the head is 97% calm, promote it",
                }
            ]
        }
    )
    fails = check_new_rows("{}", head, "selftest.json")
    if len(fails) != 1:
        raise SystemExit(
            "::error::guard did NOT flag a basis-less claim row — failure path broken"
        )
    print("failure path verified: basis-less claim row correctly flagged")


def selftest_impossibility_claim() -> None:
    """An 'X cannot be measured' claim naming nothing must be flagged, and a
    `checked:` annotation naming a path that does NOT exist must not rescue it.

    The second half is the load-bearing one: `new-table-wiring-guard` was
    defeated because its marker was presence-only, so the cheapest way to
    silence a real finding was to name a table that did not exist."""
    sys.path.insert(0, str(REPO / "scripts"))
    from check_impossibility_claims import check_lines  # noqa: E402

    # the literal sentence from the 2026-08-07 incident
    bare = "it is the one item that cannot be worked around by writing code"
    if len(check_lines([(1, bare)], "selftest.md", context=bare)) != 1:
        raise SystemExit(
            "::error::guard did NOT flag a bare impossibility claim — "
            "the failure path is broken")

    lying = "this cannot be measured\nchecked: scripts/research/does_not_exist.py"
    if len(check_lines([(1, "this cannot be measured")], "selftest.md",
                       context=lying)) != 1:
        raise SystemExit(
            "::error::guard ACCEPTED a `checked:` annotation naming a "
            "nonexistent path — presence-only marker, cheaper to lie to than "
            "to satisfy")

    honest = ("this cannot be measured\n"
              "checked: scripts/research/backtest_fidelity_calibrate.py")
    if check_lines([(1, "this cannot be measured")], "selftest.md",
                   context=honest):
        raise SystemExit(
            "::error::guard flagged a claim that DID name a real tool — "
            "false positive, the escape hatch is broken")

    # The annotation window is per file TYPE — sparse markdown prose gets a
    # wider reach than a row-dense backlog JSON, where a neighbouring row's
    # annotation must NOT satisfy this row's claim.
    claim = "this cannot be measured"
    spread = "\n".join([claim] + ["filler"] * 9
                       + ["checked: scripts/research/backtest_fidelity_calibrate.py"])
    if check_lines([(1, claim)], "selftest.md", context=spread):
        raise SystemExit(
            "::error::guard rejected a prose annotation 10 lines from its "
            "claim — the markdown window is too tight to annotate a normal "
            "paragraph, which forces authors to reshape text to appease it")
    if len(check_lines([(1, claim)], "selftest.json", context=spread)) != 1:
        raise SystemExit(
            "::error::guard accepted an annotation 10 lines away in a backlog "
            "JSON — that reach spans whole rows, so one row's `checked:` can "
            "silently satisfy another row's claim")

    print("failure path verified: bare claim flagged, fake `checked:` path "
          "rejected, real one accepted, annotation window scoped per file type")


def selftest_diag_unit_allowlist() -> None:
    """A deploy unit missing from the diag allowlist must fail the guard."""
    probe = REPO / "deploy" / "ict-selftest-unlisted.timer"
    with _planted(probe):
        if _rc(["python3", "scripts/check_diag_unit_allowlist.py"]) == 0:
            raise SystemExit(
                "::error::guard did NOT fail on an unlisted deploy unit — "
                "the failure path is broken"
            )
    print("failure path verified: unlisted unit correctly failed the guard")


def selftest_diagnostic_provenance() -> None:
    """`max(proba)` printed under a `P(volatile)` label must be caught."""
    bad_diff = (
        "+++ b/scripts/ml/_selftest_probe.py\n"
        "@@ -0,0 +1,4 @@\n"
        "+def go(rows):\n"
        "+    for r in rows:\n"
        "+        s = r.get(\"score\")\n"
        "+        print(f\"P(volatile) = {s}\")\n"
    )
    src = (
        "def go(rows):\n"
        "    for r in rows:\n"
        "        s = r.get(\"score\")\n"
        "        print(f\"P(volatile) = {s}\")\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False) as fh:
        fh.write(bad_diff)
        diff_path = fh.name
    try:
        with _planted(REPO / "scripts" / "ml" / "_selftest_probe.py", src):
            rc = _rc(["python3", "scripts/check_diagnostic_provenance.py", diff_path])
        if rc != 1:
            raise SystemExit(
                f"::error::SELF-TEST FAILED — the guard returned {rc} on a known-bad "
                "diagnostic (expected 1). Its failure path is broken, so a green from "
                "it means nothing. Fix the guard before trusting this check."
            )
    finally:
        with contextlib.suppress(FileNotFoundError):
            Path(diff_path).unlink()
    print("self-test OK — the guard still fails closed (exit 1 on a known-bad input).")


def selftest_harness_lever_coupling() -> None:
    """An injected unclassified strategy key must be reported as a coupling gap."""
    g = _load("scripts/check_harness_lever_coupling.py")
    strat = {
        "x": {
            "donchian": 20,
            "enabled": True,
            "symbols": ["BTCUSDT"],
            "timeframe": "1h",
            "brand_new_unclassified_lever": 3,
        }
    }
    gaps = g.find_coupling_gaps(strat)
    if gaps != [("x", "trend", "brand_new_unclassified_lever")]:
        raise SystemExit(
            f"::error::self-test FAILED — expected one unclassified-key gap, got {gaps}"
        )
    print("self-test OK — guard detects an injected unclassified key")


def selftest_timestamp_comparison() -> None:
    """A raw string comparison on created_at must be caught."""
    # The bad-input token is assembled from two fragments ON PURPOSE. Inline in
    # a workflow heredoc this sample was invisible to the guard's own whole-tree
    # audit; in a .py file it is not, and a verbatim literal here would make
    # `check_timestamp_comparisons.py --all` flag its own test fixture as a real
    # defect. Splitting the column name keeps the PAYLOAD byte-identical (asserted
    # in tests/ci/test_run_guards.py) without planting a genuine-looking site in
    # the tree. Do NOT "fix" this back into one literal.
    col = "created" + "_at"
    bad = (
        "+++ b/scripts/ml/_ts_selftest.py\n"
        "@@ -0,0 +1,1 @@\n"
        f'+    q = "SELECT * FROM trades WHERE {col} >= ?"\n'
    )
    with tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False) as fh:
        fh.write(bad)
        path = fh.name
    try:
        rc = _rc(["python3", "scripts/check_timestamp_comparisons.py", path])
        if rc != 1:
            raise SystemExit(
                f"::error::SELF-TEST FAILED — guard returned {rc} on a known-bad "
                "comparison (expected 1). Its failure path is broken; a green means "
                "nothing. Fix the guard before trusting this check."
            )
    finally:
        with contextlib.suppress(FileNotFoundError):
            Path(path).unlink()
    print("self-test OK — guard fails closed (exit 1 on known-bad input).")


SELFTESTS: Dict[str, Callable[[], None]] = {
    "claim-basis": selftest_claim_basis,
    "impossibility-claim": selftest_impossibility_claim,
    "diag-unit-allowlist": selftest_diag_unit_allowlist,
    "diagnostic-provenance": selftest_diagnostic_provenance,
    "harness-lever-coupling": selftest_harness_lever_coupling,
    "timestamp-comparison": selftest_timestamp_comparison,
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("name", nargs="?", choices=sorted(SELFTESTS))
    ap.add_argument("--all", action="store_true", help="run every self-test")
    args = ap.parse_args(argv)

    if not args.all and not args.name:
        ap.error("give a self-test name or --all")

    names = sorted(SELFTESTS) if args.all else [args.name]
    for name in names:
        print(f"[selftest] {name}")
        SELFTESTS[name]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
