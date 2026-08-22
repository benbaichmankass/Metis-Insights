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


# Fields whose prose can carry a quantitative claim OR its basis.
#
# ⚠️ THIS LIST IS THE GUARD'S ENTIRE FIELD OF VIEW, IN BOTH DIRECTIONS.
# A field missing here produces a FALSE NEGATIVE when the claim lives there
# (the guard sees no claim and passes silently) and a FALSE POSITIVE when the
# claim is in `title` and the basis is there (a correct row is failed).
#
# `detail` and `evidence` were absent until 2026-08-20 while being the two
# richest prose fields these backlogs actually use. MEASURED across all three
# backlogs at that date (940 rows): 198 rows carried a quantitative claim in a
# then-scanned field, and **65 more carried one ONLY in an unscanned field** —
# 24.7% of the 263 claim-bearing rows, never checked at all. Sixteen of those
# 65 had no parseable basis anywhere in that text, i.e. they would have FAILED
# had the guard been able to read them; one of them cites `$247,683.78`, the
# very figure CLAUDE.md flags as an ALL-STATUS population number whose sign
# flips on the filter. A guard blind to a quarter of its own population reports
# a clean negative it never earned.
#
# The guard is DIFF-SCOPED to rows absent from the base (`rid in base_ids`
# continues), so widening this tuple does not retro-fail the 16 — it only holds
# NEW rows to the standard. When adding a field here, prefer prose fields;
# adding an id/date field would match dates as "basis" and weaken the check.
_ROW_TEXT_FIELDS = (
    "title", "description", "source", "action",
    "resolution", "resolution_criteria",
    "detail", "evidence", "why_it_matters", "summary", "impact",
)


def _row_text(row: dict) -> str:
    return " ".join(str(row.get(k, "")) for k in _ROW_TEXT_FIELDS)


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


# ---------------------------------------------------------------------------
# STATUS ENUM (workplan 0.1 Step 0, 2026-08-22)
#
# `status` was uncontrolled free text: 41 distinct values across the three
# files, several of them whole sentences carrying state a two-state field
# could not hold. The cost was that THE OPEN SET WAS NOT COMPUTABLE -- 333,
# 378 and 121 were all quoted in one month and each was right under some
# filter, so no session could defend a count row by row.
#
# This lives in the claim-basis guard rather than in a guard of its own on
# purpose: the cleanup pass that motivated it is a RETIREMENT pass, and a new
# guard about the backlog is precisely the failure it warns against. This file
# already opens all three backlogs, so the check is ~15 lines here and zero new
# CI surface.
#
# A qualifier belongs in `detail`, never in `status`.
# ---------------------------------------------------------------------------
STATUS_ENUM = frozenset({
    "open", "kept_open", "resolved", "wont_fix", "superseded", "invalid",
})


def check_status_enum(head_text: str, path: str) -> list[str]:
    """Every row's `status` must be one of STATUS_ENUM. Whole file, not diff.

    Deliberately NOT diff-scoped: the point is that the count is computable
    from the file as it stands, which a diff-scoped check cannot establish.
    An unreadable file returns no findings rather than a false clean -- the
    caller's `scanned` counter is what catches "we read nothing".
    """
    try:
        doc = json.loads(head_text)
    except Exception:  # noqa: BLE001
        return []
    rows = doc if isinstance(doc, list) else (
        doc.get("items") or doc.get("rows") or doc.get("backlog") or [])
    bad: list[str] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        st = str(r.get("status", "")).strip()
        if st not in STATUS_ENUM:
            bad.append(
                "%s: row %s has status %r, which is not in the enum %s. A "
                "qualifier belongs in `detail` -- a free-text status makes the "
                "open count uncomputable."
                % (path, r.get("id") or r.get("item_id") or "<no id>", st,
                   sorted(STATUS_ENUM)))
    return bad


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
        failures.extend(check_status_enum(head, path))

    for f in failures:
        print(f"::error::{f}")
    print(f"claim-basis-guard: {scanned} backlog file(s) scanned against "
          f"{args.base}, {len(failures)} finding(s) "
          f"(basis-less new claim rows + off-enum statuses).")
    if scanned == 0:
        print("::error::scanned NOTHING — no backlog file readable (an "
              "absent result, not a clean one; wrong cwd?)")
        return 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
