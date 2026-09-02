#!/usr/bin/env python3
# wiring: .github/workflows/work-digest.yml, step "Fetch the standing
# close-wedge ledger from the live VM"
"""Unwrap a `/api/diag/log_file?name=close_wedge_standing` envelope to disk — MI-34.

WHY THIS IS A SCRIPT AND NOT FOUR LINES OF YAML
-----------------------------------------------
It decides, from a network response, which of THREE operator-visible states the
digest will report — and two of them look alike from a distance:

  * the fetch failed / the payload was unusable  -> write NOTHING, so the digest
    reports ``not_fetched`` ("we did not look")
  * the VM answered ``present: false``            -> write an EMPTY ledger, so the
    digest reports ``read``, 0 entries ("we looked; nothing is wedged")
  * the VM returned a ledger                      -> write it

⚠️ **THE SECOND AND FIRST MUST NOT BE COLLAPSED, AND THE CHEAP VERSION COLLAPSES
THEM.** ``curl … || echo '{}'`` — the idiom this repo has paid for more than
once — turns every failure into "nothing is wedged". Here that would take an
operator-approved downgrade and convert it into silence: the pager has been told
to stand down for these items, so the digest is the ONLY place they appear, and
a digest that says "clean" because a token expired is worse than no digest.

Logic this shaped belongs somewhere it can be tested. ``--self-test`` exercises
every branch, including the failure ones.

Usage::

    scripts/ops/diag_fetch.sh 'log_file?name=close_wedge_standing&lines=2000' \\
      | python3 scripts/ops/fetch_standing_wedges.py --out runtime_logs/close_wedge_standing.json
    python3 scripts/ops/fetch_standing_wedges.py --self-test

Exit codes are the verdict, and mirror ``probe_soak.py``'s discipline:
    0  a ledger (possibly empty) was written — we looked
    2  we could not look; nothing was written, and the caller must NOT
       interpret that as a clean fleet
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional, Tuple

#: What a `present: false` VM answer becomes on disk. A REAL empty read.
EMPTY_LEDGER: dict = {"schema": 1, "wedges": {}}


def unwrap(payload: object) -> Tuple[Optional[dict], str]:
    """Envelope -> ``(ledger_or_None, reason)``.

    ``None`` means **we could not look** and the caller must write nothing.
    An empty-but-present ledger is a dict, not ``None`` — that distinction is
    the entire point of this function.
    """
    if not isinstance(payload, dict):
        return (None, "response was not a JSON object")
    if payload.get("error"):
        return (None, f"VM reported a read error: {payload['error']}")
    if payload.get("present") is False:
        # The VM answered, and the answer is "no ledger file here". On the
        # trader that means no wedge has ever been recorded — a real, reportable
        # observation, and NOT the same as our failing to ask.
        return (dict(EMPTY_LEDGER), "VM reports no ledger file — nothing has ever wedged")
    lines = payload.get("lines")
    if not isinstance(lines, list):
        return (None, "envelope carried no `lines` array")
    text = "\n".join(str(ln) for ln in lines).strip()
    if not text:
        # ⚠️ An EMPTY body from a present:true file is NOT an empty ledger. The
        # file exists and we got nothing from it; that is a truncated or racing
        # read, and guessing "no wedges" from it is the failure this module is
        # about.
        return (None, "file reported present but its body came back empty")
    try:
        ledger = json.loads(text)
    except ValueError as exc:
        return (None, f"body is not valid JSON: {exc}")
    if not isinstance(ledger, dict) or "wedges" not in ledger:
        return (None, "body parsed but is not a wedge ledger (no `wedges` key)")
    return (ledger, f"{len(ledger.get('wedges') or {})} standing wedge(s)")


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="runtime_logs/close_wedge_standing.json")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()

    try:
        payload: Any = json.load(sys.stdin)
    except ValueError as exc:
        print(f"fetch-standing-wedges: stdin is not JSON ({exc}) — writing NOTHING; "
              f"the digest will report NOT EXAMINED, which is correct.")
        return 2

    ledger, reason = unwrap(payload)
    if ledger is None:
        print(f"fetch-standing-wedges: could not look ({reason}) — writing NOTHING. "
              f"This is NOT 'nothing is wedged'.")
        return 2
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(ledger), encoding="utf-8")
    print(f"fetch-standing-wedges: wrote {out} — {reason}")
    return 0


def _self_test() -> int:
    ok = True

    def check(n: int, label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= passed
        print(f"  self-test {n} ({label}): {'PASS' if passed else f'FAIL {detail}'}")

    led, _ = unwrap({"present": True, "lines": ['{"schema":1,"wedges":{"a|GLD|sell":{}}}']})
    check(1, "a real ledger unwraps", led is not None and len(led["wedges"]) == 1, str(led))

    led, why = unwrap({"present": False})
    check(2, "present:false is an EMPTY READ, not a failure",
          led is not None and led["wedges"] == {}, f"{led} / {why}")

    for n, (label, payload) in enumerate([
        ("not an object", "nope"),
        ("read error", {"present": True, "error": "EACCES", "lines": []}),
        ("no lines array", {"present": True}),
        ("present but empty body", {"present": True, "lines": []}),
        ("body not JSON", {"present": True, "lines": ["<html>502</html>"]}),
        ("JSON but not a ledger", {"present": True, "lines": ['{"ok":true}']}),
    ], start=3):
        led, why = unwrap(payload)
        check(n, f"{label} -> could-not-look", led is None, f"got {led!r} ({why})")

    # 9: THE COLLAPSE THIS EXISTS TO PREVENT. A failure and a genuinely-empty
    # fleet must not produce the same bytes on disk.
    fail, _ = unwrap({"present": True, "lines": ["<html>502</html>"]})
    empty, _ = unwrap({"present": False})
    check(9, "a failed fetch and an empty fleet are NOT the same result",
          fail is None and empty is not None)

    print("fetch-standing-wedges self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
