"""claim-basis-guard: a NEW backlog row asserting quantitative evidence must
carry a parseable denominator (P2.5 of the 2026-07-31 full-system-audit plan;
`BL-20260731-CLAIM-SURFACE-UNGUARDED` preventer P2).

The gap this closes: every CI guard in this repo checks CODE shape; the
NUMBERS that Tier-3 decisions are made from (backlog rows, research claims)
had no preventer — five claim-defects shipped in one session, all self-caught,
none by a repo mechanism. The full rule is
`docs/CLAUDE-RULES-CANONICAL.md` § "Always state the population"; this guard
is its mechanical FLOOR, deliberately scoped to the one surface that is
structured enough to check without crying wolf: **backlog rows**.

Contract (diff-scoped — rows whose id is NEW versus the base ref):
  - A new row in docs/claude/{health,performance,ml}-review-backlog.json whose
    title/description/source asserts evidence-grade figures — a percentage, an
    R-figure (e.g. `+32.66R`), or a $-total >= 1000 — must ALSO contain, in
    the same row, at least one parseable denominator/basis:
      * "N of M" / "N/M" with integer M,
      * "n=N" / "n = N",
      * an explicit row/count basis ("829 rows", "118 closes", "38 dirs"),
      * or a date-window (two ISO dates, or an ISO date + "since"/"→"/"..").
  - The basis must PARSE (integers are integers) — the new-table-wiring-guard
    lesson: a presence-only marker is cheaper to lie to than to satisfy.
  - Verified failure path: this file ships with tests that feed it a
    basis-less claim row and require exit 1.

Rows with no quantitative assertion are untouched. False-positive escape
hatch: none by design at V1 — a genuinely unquantifiable claim should not be
phrased as a number. Widening to research docs is a follow-up once the
false-positive rate here is observed at ~0.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

BACKLOGS = (
    "docs/claude/health-review-backlog.json",
    "docs/claude/performance-review-backlog.json",
    "docs/claude/ml-review-backlog.json",
)

# Evidence-grade figures: percentages, R-figures, $-totals >= 1,000.
_CLAIM_RE = re.compile(
    r"\d+(?:\.\d+)?\s*%"                      # 65.3%
    r"|[+\-−]\d+(?:\.\d+)?\s*R\b"        # +32.66R / -4R
    r"|\$\s?\d{1,3}(?:,\d{3})+(?:\.\d+)?"     # $36,018.60
    r"|\$\s?\d{4,}(?:\.\d+)?"                 # $36018
)

_BASIS_RES = (
    re.compile(r"\b\d+\s+of\s+\d+\b", re.IGNORECASE),          # 206 of 829
    re.compile(r"\b\d+\s*/\s*\d+\b"),                            # 24/327
    re.compile(r"\bn\s*=\s*\d+\b", re.IGNORECASE),               # n=979
    re.compile(r"\b\d+\s+(?:rows?|closes?|trades?|dirs?|files?|"
               r"manifests?|records?|fills?|samples?|folds?)\b",
               re.IGNORECASE),                                    # 829 rows
    re.compile(r"\b20\d\d-\d\d-\d\d\b.*\b20\d\d-\d\d-\d\d\b",
               re.DOTALL),                                        # two dates
    re.compile(r"\b(?:since|from|window|through)\b[^.\n]{0,40}"
               r"\b20\d\d-\d\d-\d\d\b", re.IGNORECASE),           # since <date>
)


def _rows(text: str) -> dict[str, dict]:
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return {}
    items = data.get("items") or data.get("backlog") or []
    return {r["id"]: r for r in items
            if isinstance(r, dict) and isinstance(r.get("id"), str)}


def _row_text(row: dict) -> str:
    return " ".join(str(row.get(k, "")) for k in
                    ("title", "description", "source", "action",
                     "resolution", "resolution_criteria"))


def check_new_rows(base_text: str, head_text: str, path: str) -> list[str]:
    base_ids = set(_rows(base_text))
    failures = []
    for rid, row in _rows(head_text).items():
        if rid in base_ids:
            continue  # diff-scoped: only NEW rows are held to the guard
        text = _row_text(row)
        claims = _CLAIM_RE.findall(text)
        if not claims:
            continue
        if any(rx.search(text) for rx in _BASIS_RES):
            continue
        failures.append(
            f"{path}: NEW row '{rid}' asserts quantitative evidence "
            f"({', '.join(claims[:3])}) with NO parseable basis — state the "
            f"population/denominator/window in the row (canonical rule: "
            f"'Always state the population'). A number without its basis "
            f"is not a finding."
        )
    return failures


def _git_show(ref: str, path: str) -> str:
    r = subprocess.run(["git", "show", f"{ref}:{path}"],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else "{}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", required=True,
                    help="base git ref (e.g. origin/main)")
    args = ap.parse_args()

    failures: list[str] = []
    scanned = 0
    for path in BACKLOGS:
        try:
            head = open(path, encoding="utf-8").read()
        except OSError:
            continue
        scanned += 1
        failures.extend(check_new_rows(_git_show(args.base, path), head, path))

    for f in failures:
        print(f"::error::{f}")
    print(f"claim-basis-guard: {scanned} backlog file(s) scanned against "
          f"{args.base}, {len(failures)} basis-less new claim row(s).")
    if scanned == 0:
        print("::error::scanned NOTHING — no backlog file readable (an "
              "absent result, not a clean one; wrong cwd?)")
        return 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
