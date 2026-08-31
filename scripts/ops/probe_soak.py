#!/usr/bin/env python3
# wiring: docs/claude/OPEN-ITEMS.json `probe.cmd` on monitoring rows; run by scripts/ops/run_probes.py
"""Probe a live diag soak log for a row that satisfies a declared predicate — W2.

WHY
---
Most `monitoring` rows in `docs/claude/OPEN-ITEMS.json` clear on the same shape
of observation: *"a row appears in soak log X with fields A, B and C"*. Today
each of those looks costs a session a hand-driven diag pull, and that is the
work a review drops first when budget runs out. Operator directive 2026-08-31:

    "instead of the session review having to do a whole pull of the live VM, we
     can just see that a test that we set up that definitely verifies what we're
     checking for passed or didn't pass."

THE CONTRACT
------------
**This reports. It clears nothing.** A pass means *the row exists*; whether that
row satisfies the FULL `clears_when` (which usually has clauses a predicate
cannot express) is a session's judgement. Every probe declaration therefore
carries an `is_not` naming what a pass still does not establish.

EXIT CODES ARE THE VERDICT
--------------------------
    0  the predicate matched at least one row      → pass
    1  the log was READ and NOTHING matched        → fail (a real negative)
    2  we could not look                           → could_not_run

Code 2 covers: the fetch failed, no bearer, the payload was not the expected
envelope, an unparseable predicate. It is emphatically NOT code 1. A soak that
could not be read rendering as "nothing matched" is the `curl … || echo '{}'`
defect this repo has paid for twice — and it is *worse* here, because "nothing
matched" is the state these rows are ALREADY in, so the wrong answer is
indistinguishable from the expected one.

THE DENOMINATOR IS ALWAYS PRINTED
---------------------------------
A `fail` prints how many rows were scanned. A predicate that matched nothing
over 0 rows and one that matched nothing over 4,000 are different findings, and
the first is usually a fetch that quietly returned an empty page.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import probe_lib  # noqa: E402

_FETCH = Path("scripts/ops/diag_fetch.sh")

# ⚠️ THE PREDICATE ENGINE AND THE EXIT CONTRACT LIVE IN `probe_lib`, NOT HERE.
# They were local to this file until 2026-08-31, when a second and third probe
# SOURCE arrived. Copying them would have given the repo two definitions of what
# `legs[].position_idx~1,2` means, free to drift — the argument CLAUDE.md makes
# for `provenance.py` and for `_regime_score_semantics.py`, where two probes
# re-derived one answer independently and both got it wrong on the same day.
# The names below are re-exported so the three probe declarations already in
# OPEN-ITEMS.json, and this file's own tests, keep working unchanged.
EXIT_PASS = probe_lib.EXIT_PASS
EXIT_FAIL = probe_lib.EXIT_FAIL
EXIT_COULD_NOT_LOOK = probe_lib.EXIT_COULD_NOT_LOOK
_walk = probe_lib.walk
_coerce = probe_lib.coerce
parse_condition = probe_lib.parse_condition
_die_unlooked = probe_lib.die_unlooked


# ── fetch ──────────────────────────────────────────────────────────────────

def fetch_rows(path: str, root: Path) -> tuple[list[dict] | None, str]:
    """Return (rows, note). `None` rows means we could not look — never []."""
    script = root / _FETCH
    if not script.exists():
        return None, f"{_FETCH} is absent"
    if not os.environ.get("DIAG_READ_TOKEN"):
        return None, "DIAG_READ_TOKEN is unset — no bearer, so nothing was read"
    try:
        proc = subprocess.run(  # noqa: S603
            ["bash", str(script), path], cwd=root,
            capture_output=True, text=True, timeout=90,
        )
    except subprocess.TimeoutExpired:
        return None, "diag_fetch timed out"
    except OSError as exc:
        return None, f"diag_fetch could not run: {exc}"
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or ["(no stderr)"]
        return None, f"diag_fetch exit {proc.returncode}: {tail[0][:200]}"
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return None, f"response was not JSON: {exc}"

    # Accept both envelope shapes the diag surface uses. An unrecognised shape
    # is could-not-look, NOT an empty log: guessing a key here would invent a
    # zero denominator.
    for key in ("lines", "records", "rows"):
        if isinstance(payload.get(key), list):
            raw = payload[key]
            break
    else:
        return None, f"unrecognised envelope (keys={sorted(payload)[:8]})"

    rows = probe_lib.normalise_rows(raw)
    return rows, f"read {len(rows)} row(s) from {path}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--path", help="diag path, e.g. /api/diag/log_file?name=pairs_soak&lines=1000")
    ap.add_argument("--require", action="append", default=[],
                    help="condition `path=value` or `path~a,b`; ALL must hold on ONE row. Repeatable.")
    ap.add_argument("--positive-control",
                    help="a condition that DOES hold in this log today. If it does not "
                         "fire, the verdict is could_not_look — a reader proven blind "
                         "must not emit a confident negative (RULE ONE: show the probe "
                         "can find a positive before trusting that it is quiet).")
    ap.add_argument("--root", default=".")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()
    if not args.path or not args.require:
        ap.error("--path and at least one --require are mandatory")

    try:
        conds = [parse_condition(c) for c in args.require]
        control = (parse_condition(args.positive_control)
                   if args.positive_control else None)
    except ValueError as exc:
        return _die_unlooked(str(exc))

    rows, note = fetch_rows(args.path, Path(args.root))
    if rows is None:
        return _die_unlooked(note)

    return probe_lib.report(rows, conds, args.require, note,
                            control, args.positive_control or "")


def _self_test() -> int:
    # The predicate + exit-contract controls now live with the code they guard.
    probe_lib.self_test()
    fired = 0

    def ok(cond, label):
        nonlocal fired
        assert cond, f"control FAILED: {label}"
        fired += 1

    # This file still owns the FETCH half, so those controls stay here.
    saved = os.environ.pop("DIAG_READ_TOKEN", None)
    try:
        rows, note = fetch_rows("/api/diag/log_file?name=x", Path("."))
        ok(rows is None and "DIAG_READ_TOKEN" in note,
           "no bearer is could-not-look, NEVER an empty row list")
    finally:
        if saved is not None:
            os.environ["DIAG_READ_TOKEN"] = saved

    ok(EXIT_COULD_NOT_LOOK != EXIT_FAIL,
       "could_not_look and fail are different exit codes — the whole point")
    ok(parse_condition is probe_lib.parse_condition,
       "the engine is the SHARED one, not a local copy that could drift")

    print(f"probe-soak: self-test OK — {fired} planted controls all fire")
    return 0


if __name__ == "__main__":
    sys.exit(main())
