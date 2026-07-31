"""Extract the LAST complete top-level JSON object from a command's output.

`python -m ml train` and `python -m ml build-dataset` print their summary as
MULTI-LINE JSON (`json.dumps(..., indent=2)`). The training cycle used to
recover it with `tail -n 50 | grep -E '^{' | tail -n 1`, which on indented
output captures the bare ``{`` line alone — `json.loads("{")` fails, the
summary silently degrades to ``{}``, and every `manifest_ok` cycle event
logged `model_id: null` (observed live across the whole 2026-07-31 cycle,
trainer-diag #8184; P1.5 of the 2026-07-31 full-system-audit plan). The
build script never read its build's stdout at all and groped the filesystem
instead — the `row_count: 0` lie documented in
`BL-20260731-AUDIT-0731-NEW-FINDINGS` item (7).

Usage:  python scripts/ops/_last_json_object.py <file>
Prints the last top-level JSON object found in <file> as ONE compact line,
or ``{}`` when none parses. ``{}`` means "no summary recovered" — callers
must treat it as honest-null (fields render null), never as a crash and
never as a zero.
"""
from __future__ import annotations

import json
import sys


def last_json_object(text: str) -> dict | None:
    """Return the last top-level JSON object in *text*, or None."""
    lines = text.splitlines(keepends=True)
    # Candidate starts: lines whose first non-space char is '{'. The ml CLIs
    # print the summary object at column 0; nested opens sit at line ends
    # ('"metrics": {'), so they are not candidates. Scanned from the LAST
    # candidate backwards: the final summary wins over any earlier object
    # echoed in the run's output.
    starts = [i for i, ln in enumerate(lines) if ln.lstrip().startswith("{")]
    for i in reversed(starts):
        chunk = "".join(lines[i:]).lstrip()
        try:
            obj, _ = json.JSONDecoder().raw_decode(chunk)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def main(argv: list[str]) -> int:
    try:
        with open(argv[1], encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except (IndexError, OSError):
        print("{}")
        return 0
    obj = last_json_object(text)
    print(json.dumps(obj if obj is not None else {}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
