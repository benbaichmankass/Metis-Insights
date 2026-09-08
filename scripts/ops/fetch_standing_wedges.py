#!/usr/bin/env python3
# wiring: .github/workflows/work-digest.yml, step "Fetch the standing
# close-wedge ledger from the live VM"
"""Unwrap a `/api/diag/log_file?name=close_wedge_standing` envelope to disk — MI-34.

WHY THIS IS A SCRIPT AND NOT FOUR LINES OF YAML
-----------------------------------------------
It decides, from a network response, which operator-visible state the digest
will report — and the two that matter look alike from a distance:

  * the fetch failed, the payload was unusable, **or the VM says the file is not
    there** -> write NOTHING, so the digest reports ``not_fetched``
    ("we did not look")
  * the VM returned a ledger                    -> write it

⚠️ **ONLY THE WRITER CAN SAY "NOTHING IS WEDGED", AND THIS SCRIPT NEVER SAYS IT
ON THE WRITER'S BEHALF.** ``curl … || echo '{}'`` — the idiom this repo has paid
for more than once — turns every failure into "nothing is wedged". Here that
would take an operator-approved downgrade and convert it into silence: the pager
has been told to stand down for these items, so the digest is the ONLY place
they appear, and a digest that says "clean" because a token expired is worse
than no digest.

⚠️ **AND THIS MODULE COMMITTED THAT EXACT SIN UNTIL MI-101 (2026-09-03).** It
had a third branch mapping ``present: false`` to a synthesised empty ledger, on
the reasoning that a missing file meant "no wedge has ever been recorded". That
inference is not available from a missing file: it is equally consistent with
the writer never having run. Measured that day, the writer had in fact NEVER
written the ledger, and twelve consecutive digests reported a clean fleet — in
the words "a real observation, not an absence of one" — over a real-money close
path. **An empty-but-present ledger is now produced by the WRITER**
(``src/runtime/close_wedge_standing`` heartbeats one, with a timestamp a reader
grades), which is the only place the claim can honestly come from.

Logic this shaped belongs somewhere it can be tested. ``--self-test`` exercises
every branch, including the failure ones.

Usage::

    scripts/ops/diag_fetch.sh 'log_file?name=close_wedge_standing&lines=2000' \\
      | python3 scripts/ops/fetch_standing_wedges.py
    python3 scripts/ops/fetch_standing_wedges.py --self-test

Exit codes are the verdict, and mirror ``probe_soak.py``'s discipline:
    0  a ledger the WRITER produced (possibly empty) was written — we looked
    2  we could not look; nothing was written, and the caller must NOT
       interpret that as a clean fleet
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.utils.paths import runtime_logs_dir  # noqa: E402

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
        # ⚠️ **THIS BRANCH USED TO MANUFACTURE AN EMPTY LEDGER, AND THAT WAS THE
        # BUG MI-101 EXISTS FOR.** It returned `EMPTY_LEDGER` on the reasoning
        # that "on the trader that means no wedge has ever been recorded — a
        # real, reportable observation". That inference was never available from
        # this evidence. `present: false` says the FILE IS NOT THERE, which is
        # equally consistent with the writer never having run, having crashed,
        # or writing somewhere else — and on 2026-09-03 the last of those was
        # actually true: the trader had never written the ledger at all, and
        # twelve consecutive digests reported "none (ledger read, 0 entries) — a
        # real observation, not an absence of one" over a real-money close path.
        #
        # That is this module's OWN documented sin — the `curl … || echo '{}'`
        # idiom, which its docstring calls out — committed in Python inside the
        # function written to prevent it. The distinction it is built to keep is
        # between *we could not look* and *we looked and found none*, and only
        # the WRITER can supply the second.
        #
        # Since `close_wedge_standing` heartbeats an empty-but-present ledger, a
        # running trader always leaves a file. So an absent one is now a real
        # signal with a real remedy — check the trader — and it must reach the
        # operator as NOT EXAMINED rather than as a clean fleet.
        return (None, "VM reports NO LEDGER FILE — the writer is not running, "
                      "has never run, or cannot write its data dir. This is NOT "
                      "'nothing is wedged'")
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
    # ⚠️ DEFAULTS THROUGH THE CANONICAL `$DATA_DIR` RESOLVER, never a
    # repo-relative literal. On a GitHub runner (no DATA_DIR) this is
    # byte-identical to the old default; anywhere DATA_DIR is set it is the
    # path the trader and diag both use, so a fetch cannot land beside the
    # file the reader looks for. Resolved lazily so --self-test never has to
    # touch the filesystem.
    ap.add_argument("--out", default=None)
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
    out = Path(a.out) if a.out else (runtime_logs_dir() / "close_wedge_standing.json")
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

    # ⚠️ THE REGRESSION CONTROL FOR MI-101. This asserted the OPPOSITE until
    # 2026-09-03 — that `present: false` is "an EMPTY READ, not a failure" —
    # and the assertion was the defect, stated as a passing test. A missing
    # file is `we could not look`; only the writer can report an empty fleet.
    led, why = unwrap({"present": False})
    check(2, "present:false is COULD-NOT-LOOK, never a clean fleet",
          led is None, f"{led} / {why}")
    check(3, "and it SAYS the writer is not running",
          "writer is not running" in why, why)

    for n, (label, payload) in enumerate([
        ("not an object", "nope"),
        ("read error", {"present": True, "error": "EACCES", "lines": []}),
        ("no lines array", {"present": True}),
        ("present but empty body", {"present": True, "lines": []}),
        ("body not JSON", {"present": True, "lines": ["<html>502</html>"]}),
        ("JSON but not a ledger", {"present": True, "lines": ['{"ok":true}']}),
    ], start=4):
        led, why = unwrap(payload)
        check(n, f"{label} -> could-not-look", led is None, f"got {led!r} ({why})")

    # 10: THE COLLAPSE THIS EXISTS TO PREVENT, restated for the real writer. A
    # genuinely-empty fleet is a ledger the WRITER stamped and this script
    # passes through untouched; nothing else may produce those bytes.
    fail, _ = unwrap({"present": True, "lines": ["<html>502</html>"]})
    absent, _ = unwrap({"present": False})
    written, _ = unwrap({"present": True, "lines": [
        '{"schema":1,"updated_at":"2026-09-03T12:00:00+00:00","wedges":{}}']})
    check(10, "a failed fetch, an ABSENT ledger and a writer-stamped empty one "
              "are three different results",
          fail is None and absent is None
          and written is not None and written["wedges"] == {}
          and bool(written.get("updated_at")),
          f"{fail} / {absent} / {written}")

    print("fetch-standing-wedges self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
