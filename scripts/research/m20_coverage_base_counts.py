#!/usr/bin/env python3
"""Promote the M20 coverage matrix's base trade counts from PROSE to FIELDS.

WHY
---
Every quantitative claim in ``docs/research/exit-refinement-coverage.json`` lives
in the free-text ``ref`` of its cell. The verdict is a field; the DENOMINATOR
behind it is a sentence. Measured 2026-08-27: of 329 ``honest_negative`` cells
only **96 state a base count at all** — so the matrix's headline is computed over
cells most of which carry no denominator anywhere a reader or a tool can find.

That is the *"always state the population"* rule failing structurally rather than
individually: nobody omitted the number on purpose, there was simply nowhere for
it to go.

WHAT IT DOES **NOT** DO
-----------------------
⚠️ **It does not re-grade anything, and no cell's ``status`` is touched.** The
2026-08-27 audit tested whether base size distinguishes the verdicts and it does
**not**: fleet-wide, ``honest_negative`` median OOS is 33, ``shipped`` is 33 and
``passed_unshipped`` is 36. The population is uniformly thin, so a per-cell
``underpowered`` status would flip essentially the whole matrix — including the
shipped cells — and mean nothing. The power caveat is therefore recorded ONCE, at
the artifact level, in ``known_caveats._headline``.

⚠️ **``base_oos`` is NOT the denominator that matters.** The quantity that decides
whether a verdict is measurable is how many trades the LEVER MODIFIED, and it is
not recorded anywhere (``exit-refinement/SKILL.md``: *"Per-cell fire counts are
not recorded; that gap is open"*). ``base_oos`` is an UPPER BOUND on it. Do not
read a comfortable base as a powered cell.

⚠️ **Prose is not auto-gradeable, and the audit proved it the expensive way.** A
regex for "exactly zero folds" matched **60** cells; reading them showed the
mentions were a comparison to a *different* cell, a description of a *superseded*
verdict, or a note that inert folds are *already excluded* via ``wins_effective``.
Auto-grading would have mis-flipped 60 cells. This tool therefore extracts only
what is stated in a fixed, unambiguous FORM — never an interpretation.

FIELDS ADDED
------------
``base_is`` / ``base_oos``   — ints, when the ref states them in a recognised form.
``denominator_unstated``     — true on a RESOLVED verdict whose ref states no base
                               count. Not a defect claim: the verdict may be
                               right, we simply cannot check it.

Idempotent. ``--check`` writes nothing and exits 1 if a cell's prose states a
count the fields do not carry (so the extraction cannot silently rot).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
MATRIX = _REPO_ROOT / "docs/research/exit-refinement-coverage.json"

#: Recognised FORMS only. Both were verified against the live file; a form not
#: listed here yields no field rather than a guess.
_FORMS = (
    re.compile(r"base\s*n\s*IS\s*=\s*(?P<is>\d+)\s+OOS\s*=\s*(?P<oos>\d+)", re.IGNORECASE),
    re.compile(r"base_trades_OOS\s*=\s*(?P<oos>\d+)\s*\(\s*IS\s*=\s*(?P<is>\d+)\s*\)", re.IGNORECASE),
)

#: A verdict has been reached on these; a missing denominator is meaningful.
#: `pending` / `blocked` / `n/a` have no verdict to support, so they are exempt.
_RESOLVED = ("honest_negative", "shipped", "passed_unshipped", "shipped_gate_failed")


def _counts(ref: str) -> tuple[int, int] | None:
    for pat in _FORMS:
        m = pat.search(ref or "")
        if m:
            return int(m.group("is")), int(m.group("oos"))
    return None


def run(check_only: bool) -> int:
    doc: Any = json.loads(MATRIX.read_text())
    levers = doc["lever_columns"]
    added = drift = unstated = resolved = 0

    for row in doc["rows"]:
        for lever in levers:
            cell = row.get(lever)
            if not isinstance(cell, dict):
                continue
            status = str(cell.get("status") or "")
            got = _counts(cell.get("ref") or "")
            if got:
                is_n, oos_n = got
                if cell.get("base_is") != is_n or cell.get("base_oos") != oos_n:
                    if check_only:
                        print(f"::error::{row.get('strategy')}/{lever}: ref states "
                              f"IS={is_n} OOS={oos_n}; fields say "
                              f"{cell.get('base_is')}/{cell.get('base_oos')}")
                        drift += 1
                    else:
                        cell["base_is"], cell["base_oos"] = is_n, oos_n
                        added += 1
            if status.split(":")[0] in _RESOLVED:
                resolved += 1
                if not got:
                    unstated += 1
                    if not check_only:
                        cell["denominator_unstated"] = True
                elif not check_only:
                    cell.pop("denominator_unstated", None)

    if check_only:
        if drift:
            print(f"m20-coverage-base-counts: {drift} cell(s) whose prose and fields disagree")
            return 1
        print("m20-coverage-base-counts: OK — every stated base count is also a field")
        return 0

    MATRIX.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    pct = (unstated / resolved * 100) if resolved else 0.0
    print(f"m20-coverage-base-counts: {added} cell(s) gained base_is/base_oos; "
          f"{unstated} of {resolved} resolved verdicts ({pct:.1f}%) state NO denominator "
          f"and are flagged denominator_unstated.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="write nothing; fail if prose and fields disagree")
    return run(ap.parse_args().check)


if __name__ == "__main__":
    sys.exit(main())
