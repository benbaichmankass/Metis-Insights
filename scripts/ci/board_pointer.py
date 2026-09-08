#!/usr/bin/env python3
"""Resolve WHICH GitHub issue is the live Claude Coordination Board.

WHY THIS MODULE EXISTS
----------------------
Issue #6927 hit GitHub's hard 2500-comment cap on 2026-09-07T11:28:17Z. Writes
403'd; **reads kept succeeding**, returning a board permanently frozen at that
instant. Retiring it required editing **13 references across 6 files** (measured
2026-09-08 over every literal ``6927`` under ``.github/workflows/`` and
``scripts/``: board-post.yml x6, scope-overlap-audit.yml x2,
merge-claim-audit.yml x2, session_registry.py x1, trainer-vm-diag.yml x1,
trainer-diag-relay.yml x1). A sweep that wide is one a session finishes halfway,
and the half it skips is invisible -- which is exactly what happened.

So the number lives in **one** file, ``docs/claude/board-pointer.json``, and
everything resolves through here. Rotation becomes one JSON edit.

THE FAILURE THIS IS SHAPED AGAINST
----------------------------------
**A frozen board that READS SUCCESSFULLY is indistinguishable from a live, quiet
one.** Every board consumer therefore gets three states and never two:

    live            -- pointer names an issue and that issue is writable
    unprovisioned   -- no live board exists  -> could_not_check, never "clean"
    could_not_check -- we did not look (pointer unreadable/malformed)

``unprovisioned`` is emphatically NOT "no collisions found". A consumer that
renders a missing board as a clean bill of health is the unasserted-denominator
failure (diagnostic-provenance sub-class C) that this whole change exists to
remove.

USAGE
-----
    python3 scripts/ci/board_pointer.py --number      # bare issue number, or exit 3
    python3 scripts/ci/board_pointer.py --json        # the whole resolved pointer
    python3 scripts/ci/board_pointer.py --self-test

Exit codes: 0 resolved · 3 unprovisioned · 4 could_not_check (unreadable/malformed).
They are distinct so a shell caller cannot collapse them either.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

POINTER_PATH = "docs/claude/board-pointer.json"

RESOLVED, UNPROVISIONED, COULD_NOT_CHECK = 0, 3, 4

#: Fields a pointer MUST carry. A pointer missing any of them is malformed
#: rather than defaulted -- a silently-defaulted threshold is how a cap monitor
#: ends up monitoring nothing.
REQUIRED = ("board_issue", "board_state", "cap", "warn_at", "rotate_at",
            "heartbeat_every_hours", "stale_after_hours")

VALID_STATES = ("unprovisioned", "live", "retired")


class PointerError(Exception):
    """The pointer could not be read or does not mean anything. Never 'clean'."""


def _repo_root(start: Path | None = None) -> Path:
    here = (start or Path(__file__).resolve()).parent
    for cand in [here, *here.parents]:
        if (cand / POINTER_PATH).exists():
            return cand
    return Path.cwd()


def load(path: str | os.PathLike | None = None) -> dict:
    """Read and VALIDATE the pointer. Raises `PointerError`; never returns junk."""
    p = Path(path) if path else _repo_root() / POINTER_PATH
    try:
        raw = json.loads(Path(p).read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise PointerError(f"{p} does not exist — the board cannot be resolved") from e
    except json.JSONDecodeError as e:
        raise PointerError(f"{p} is not valid JSON: {e}") from e

    if not isinstance(raw, dict):
        raise PointerError(f"{p} must be a JSON object, got {type(raw).__name__}")

    missing = [k for k in REQUIRED if k not in raw]
    if missing:
        raise PointerError(f"{p} is missing required field(s): {', '.join(missing)}")

    state = raw["board_state"]
    if state not in VALID_STATES:
        raise PointerError(
            f"{p} board_state={state!r} is not one of {VALID_STATES} — refusing to "
            f"guess, because every guess here renders as a clean board")

    num = raw["board_issue"]
    if state == "live":
        if not isinstance(num, int) or isinstance(num, bool) or num <= 0:
            raise PointerError(
                f"{p} board_state='live' but board_issue={num!r} is not a positive "
                f"integer — a live board that names no issue is the frozen-board "
                f"failure with extra steps")
    elif num is not None:
        raise PointerError(
            f"{p} board_state={state!r} must carry board_issue=null, got {num!r}")

    # Thresholds must be ordered, or the monitor warns after it is too late.
    cap, warn, rot = raw["cap"], raw["warn_at"], raw["rotate_at"]
    if not (0 < warn < rot < cap):
        raise PointerError(
            f"{p} thresholds must satisfy 0 < warn_at({warn}) < rotate_at({rot}) "
            f"< cap({cap}) — otherwise the escalation ladder fires out of order "
            f"or never")

    beat, stale = raw["heartbeat_every_hours"], raw["stale_after_hours"]
    if not (0 < beat < stale):
        raise PointerError(
            f"{p} needs 0 < heartbeat_every_hours({beat}) < stale_after_hours"
            f"({stale}) — a staleness window shorter than the beat interval "
            f"declares a healthy board dead on every quiet cycle")

    return raw


def resolve(path=None) -> tuple[int, int | None, dict | None, str]:
    """Return `(exit_code, issue_number_or_None, pointer_or_None, human_reason)`."""
    try:
        ptr = load(path)
    except PointerError as e:
        return COULD_NOT_CHECK, None, None, f"could_not_check — {e}"
    if ptr["board_state"] != "live":
        return (UNPROVISIONED, None, ptr,
                f"unprovisioned — board_state={ptr['board_state']!r}; there is no "
                f"live coordination board. This is NOT 'no collisions'.")
    return RESOLVED, int(ptr["board_issue"]), ptr, "live"


# --------------------------------------------------------------------------- #
# self-test
# --------------------------------------------------------------------------- #
def _self_test() -> int:
    import tempfile
    fails = []

    def ok(cond, label):
        if not cond:
            fails.append(label)

    def write(obj):
        fd = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(obj, fd)
        fd.close()
        return fd.name

    base = {"board_issue": 123, "board_state": "live", "cap": 2500,
            "warn_at": 2000, "rotate_at": 2400,
            "heartbeat_every_hours": 6, "stale_after_hours": 18}

    code, num, _, _ = resolve(write(base))
    ok(code == RESOLVED and num == 123, "a live pointer resolves to its number")

    unp = dict(base, board_state="unprovisioned", board_issue=None)
    code, num, _, why = resolve(write(unp))
    ok(code == UNPROVISIONED and num is None, "unprovisioned resolves to no number")
    ok("NOT 'no collisions'" in why,
       "unprovisioned SAYS it is not a clean result — the sub-class C guard")

    code, _, _, _ = resolve("/nonexistent/board-pointer.json")
    ok(code == COULD_NOT_CHECK, "a missing pointer is could_not_check, NOT unprovisioned")

    ok(UNPROVISIONED != COULD_NOT_CHECK != RESOLVED,
       "the three exit codes are distinct so a shell caller cannot collapse them")

    for bad, label in [
        (dict(base, board_state="live", board_issue=None), "live with a null issue"),
        (dict(base, board_state="live", board_issue=0), "live with issue 0"),
        (dict(base, board_state="live", board_issue=True), "live with a bool issue"),
        (dict(base, board_state="unprovisioned"), "unprovisioned still naming an issue"),
        (dict(base, board_state="banana"), "an unknown board_state"),
        (dict(base, warn_at=2450), "warn_at above rotate_at"),
        (dict(base, rotate_at=2600), "rotate_at above the cap"),
        (dict(base, stale_after_hours=3), "a staleness window shorter than the beat"),
    ]:
        code, _, _, _ = resolve(write(bad))
        ok(code == COULD_NOT_CHECK, f"refused: {label}")

    for k in REQUIRED:
        short = {kk: vv for kk, vv in base.items() if kk != k}
        code, _, _, _ = resolve(write(short))
        ok(code == COULD_NOT_CHECK, f"refused a pointer missing {k!r}")

    # The shipping pointer must itself parse. It may be unprovisioned (that is a
    # legitimate mid-rotation state) but it may never be malformed.
    code, _, _, why = resolve()
    ok(code in (RESOLVED, UNPROVISIONED),
       f"the SHIPPING pointer parses ({why})")

    for f in fails:
        print(f"FAIL: {f}", file=sys.stderr)
    print(f"board_pointer self-test: {'FAILED' if fails else 'ok'} "
          f"({len(fails)} failure(s))", file=sys.stderr)
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--number", action="store_true",
                    help="print the live board issue number, or exit 3/4")
    ap.add_argument("--json", action="store_true", help="print the resolved pointer")
    ap.add_argument("--path", help="pointer file (default: the repo's)")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        return _self_test()

    code, num, ptr, why = resolve(a.path)
    if a.json:
        print(json.dumps({"state": ("live" if code == RESOLVED
                                    else "unprovisioned" if code == UNPROVISIONED
                                    else "could_not_check"),
                          "board_issue": num, "reason": why,
                          "pointer": ptr}, indent=2))
        return code
    if code == RESOLVED:
        print(num)
    else:
        print(why, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
