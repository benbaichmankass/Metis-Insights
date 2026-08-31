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

_FETCH = Path("scripts/ops/diag_fetch.sh")

EXIT_PASS, EXIT_FAIL, EXIT_COULD_NOT_LOOK = 0, 1, 2


def _die_unlooked(msg: str) -> int:
    # Note the wording: never "no rows matched". This path never establishes that.
    print(f"probe-soak: COULD NOT LOOK — {msg}")
    return EXIT_COULD_NOT_LOOK


# ── predicate ──────────────────────────────────────────────────────────────

def _walk(row, path: str):
    """Yield every value at a dotted path. `[]` fans out over a list.

    `legs[].position_idx` yields one value per leg, so "any leg" and "this
    specific field" are both expressible without a query language.
    """
    cur = [row]
    for part in path.split("."):
        nxt = []
        fan = part.endswith("[]")
        key = part[:-2] if fan else part
        for c in cur:
            if not isinstance(c, dict) or key not in c:
                continue
            v = c[key]
            if fan:
                if isinstance(v, list):
                    nxt.extend(v)
            else:
                nxt.append(v)
        cur = nxt
    return cur


def _coerce(s: str):
    low = s.lower()
    if low in {"true", "false"}:
        return low == "true"
    if low == "null":
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return s


def parse_condition(spec: str):
    """`path=value` (equals) or `path~a,b,c` (membership). Returns a callable."""
    if "~" in spec and ("=" not in spec or spec.index("~") < spec.index("=")):
        path, raw = spec.split("~", 1)
        wanted = {_coerce(x.strip()) for x in raw.split(",") if x.strip() != ""}
        if not wanted:
            raise ValueError(f"empty membership set in {spec!r}")
        return lambda row: any(v in wanted for v in _walk(row, path.strip()))
    if "=" in spec:
        path, raw = spec.split("=", 1)
        want = _coerce(raw.strip())
        return lambda row: any(v == want for v in _walk(row, path.strip()))
    raise ValueError(f"condition {spec!r} must be `path=value` or `path~a,b`")


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

    rows: list[dict] = []
    for r in raw:
        if isinstance(r, dict):
            rows.append(r)
        elif isinstance(r, str):
            try:
                obj = json.loads(r)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows, f"read {len(rows)} row(s) from {path}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--path", help="diag path, e.g. /api/diag/log_file?name=pairs_soak&lines=1000")
    ap.add_argument("--require", action="append", default=[],
                    help="condition `path=value` or `path~a,b`; ALL must hold on ONE row. Repeatable.")
    ap.add_argument("--root", default=".")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()
    if not args.path or not args.require:
        ap.error("--path and at least one --require are mandatory")

    try:
        conds = [parse_condition(c) for c in args.require]
    except ValueError as exc:
        return _die_unlooked(str(exc))

    rows, note = fetch_rows(args.path, Path(args.root))
    if rows is None:
        return _die_unlooked(note)

    hits = [r for r in rows if all(c(r) for c in conds)]
    if hits:
        print(f"probe-soak: PASS — {len(hits)} of {len(rows)} row(s) match "
              f"{args.require} ({note})")
        return EXIT_PASS
    # The denominator is the finding when it is zero.
    print(f"probe-soak: FAIL — 0 of {len(rows)} row(s) match {args.require} ({note}). "
          f"{'A ZERO DENOMINATOR IS NOT A NEGATIVE — the log read empty.' if not rows else ''}")
    return EXIT_FAIL


def _self_test() -> int:
    fired = 0

    def ok(cond, label):
        nonlocal fired
        assert cond, f"control FAILED: {label}"
        fired += 1

    row = {"a": 1, "state": "exceeds_cushion", "applied": True,
           "legs": [{"position_idx": 0}, {"position_idx": 2}]}
    ok(parse_condition("state=exceeds_cushion")(row), "equals matches")
    ok(not parse_condition("state=within_cushion")(row), "equals rejects")
    ok(parse_condition("applied=true")(row), "`true` coerces to bool, not the string 'true'")
    ok(not parse_condition("applied=false")(row), "bool coercion is not truthiness of a string")
    ok(parse_condition("legs[].position_idx~1,2")(row),
       "`[]` fans out — ANY leg satisfying the set is a match")
    ok(not parse_condition("legs[].position_idx~1")(row),
       "the fan-out does not match a value no leg carries")
    ok(not parse_condition("missing.key=1")(row), "an absent path never matches")
    ok(not parse_condition("legs[].position_idx=9")(row), "absent value in a fan-out")

    try:
        parse_condition("garbage")
        ok(False, "an unparseable condition raises")
    except ValueError:
        ok(True, "an unparseable condition raises")

    saved = os.environ.pop("DIAG_READ_TOKEN", None)
    try:
        rows, note = fetch_rows("/api/diag/log_file?name=x", Path("."))
        ok(rows is None and "DIAG_READ_TOKEN" in note,
           "no bearer is could-not-look, NEVER an empty row list")
    finally:
        if saved is not None:
            os.environ["DIAG_READ_TOKEN"] = saved

    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = _die_unlooked("planted")
    said = buf.getvalue()
    ok(rc == EXIT_COULD_NOT_LOOK, "the could-not-look path returns its own code")
    ok("COULD NOT LOOK" in said and "match" not in said.lower(),
       "and it words itself as an unread, never as a negative result — a reader "
       "must not be able to mistake it for 'nothing matched'")
    ok(EXIT_COULD_NOT_LOOK != EXIT_FAIL,
       "could_not_look and fail are different exit codes — the whole point")

    print(f"probe-soak: self-test OK — {fired} planted controls all fire")
    return 0


if __name__ == "__main__":
    sys.exit(main())
