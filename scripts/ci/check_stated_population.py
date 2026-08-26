#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py (stated-population-guard)
"""A quantitative claim in a doc or a backlog row must state its population.

GATE 0 item **G4**. `docs/CLAUDE-RULES-CANONICAL.md` § "Always state the
population" is binding prose with no enforcement; this is the enforcement.

WHY IT IS WORTH A GUARD (operator's own test, 2026-08-26: *"if it's affecting
things that are being read or filed before they're actually merged, then that's
worth keeping"*). A number without a denominator does not stay in the PR that
wrote it — it lands in a backlog row or a doc and is then READ by later sessions
as established fact. Measured on this repo's own history:

* ``+$247,683.78`` of "fabricated PnL" was quoted for months; restricted to the
  rows any consumer aggregates the figure is **negative**. The sign flips on the
  filter choice.
* 2026-08-26 a row was filed reading *"71 of 72 past-stop are bybit_2"*. The
  CLEAN rows are also bybit_2 — the counts described the population, not a
  defect — and bybit is the only venue that CAN produce a measured exit price.
* the same day, *"180 sites"* was filed from a checker whose metric was wrong;
  the real guard reports clean.

None of those was caught by review. Each was caught later, by someone
re-deriving the number.

WHAT IT FLAGS. An ADDED line in a watched path carrying a bare percentage or a
"N of the M" style count, with **no denominator marker anywhere nearby**
(``n=``, ``/``, ``of``, ``out of``, ``rows``, ``population``, ``sample``,
``window``, ``measured``, ``total``). Nearby means the same line or the two
before it, because a table row's denominator legitimately sits in its header.

⚠️ **DELIBERATELY NARROW.** It cannot judge whether a stated denominator is the
RIGHT one — that is the actual skill and no regex has it. It catches the case
where none is stated at all, which is the cheap half. A guard that tried to
judge correctness would fire constantly and be switched off within a day, which
is this repo's documented alarm-fatigue failure.

KNOWN FALSE NEGATIVE, MEASURED AND DELIBERATELY NOT FIXED (2026-08-26). Any
incidental integer in the context suppresses the check, because the denominator
test is "is there a second number". So these three shapes pass while stating no
population::

    Coverage is 42.9% and the win rate moves 9 points.   # an unrelated count
    Measured on 2026-08-26, coverage is 42.9%.           # a DATE
    Coverage is 42.9% (see BL-20260817 for context).     # a BACKLOG ID

That reads alarming — dates and backlog ids are everywhere in these documents —
and the intuition is WRONG, which is why the number is recorded here rather
than the worry. **Population: all 875 watched files at 2026-08-26, 3,549 lines
containing a percentage. Exactly 9 (0.25%) have a date/backlog-id/PR-number as
their ONLY denominator**, and inspecting those 9, most are legitimate (a year
used as a data label — ``2011 (3.327%)`` in a regime analysis — or a citation
year, or a config threshold). ⚠️ That is a corpus measurement over EXISTING
lines used as a proxy for what this diff-scoped guard would see on ADDED ones;
it is the right shape of text, not the literal population the guard scans.

Tightening to exclude year-shaped and id-shaped integers would fire on
``2011 (3.327%)`` — a correct line — and this repo's documented failure mode is
the alarm nobody reads, not the miss nobody noticed. 0.25% is below the rate at
which a stricter rule pays for itself. Pinned by
``test_stated_population_known_limits.py`` so the behaviour stays deliberate
rather than becoming accidental.

Escape hatch: ``<!-- population-ok: <reason> -->`` on or just above the line,
for a number that genuinely has no population (a threshold, a config value, a
version). The reason is visible in the diff so a reviewer can see it.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

WATCHED_SUFFIXES = (".md",)
WATCHED_PREFIXES = ("docs/",)
WATCHED_FILES = (
    "docs/claude/health-review-backlog.json",
    "docs/claude/performance-review-backlog.json",
    "docs/claude/ml-review-backlog.json",
    "docs/claude/RECURRENCE-LEDGER.json",
    "docs/claude/OPEN-ITEMS.json",
)

#: A percentage. This is the claim shape that needs a population.
_PCT = re.compile(r"\b\d{1,3}(?:\.\d+)?\s?%")

#: A DENOMINATOR IS A NUMBER, NOT A WORD. The first draft of this guard
#: suppressed on descriptive tokens (`of`, `across`, `measured`, `window`,
#: `total`) and consequently caught only 2 of 5 real bad claims from
#: 2026-08-26: "22.7% of MEASURED exits" and "42.9% ACROSS the journal" both
#: matched a word and sailed through, while stating no population whatever.
#: Requiring an actual second number is simpler and strictly stronger, and it
#: is what the rule means: a population is a count.
_BARE_INT = re.compile(r"(?<![\d.])\d{1,12}(?![\d.]*\s?%)(?![\d.])")
#: `n=440` is a stated population even where the number reads as part of a word.
_EXPLICIT_N = re.compile(r"\bn\s*=\s*\d", re.IGNORECASE)

_OVERRIDE = re.compile(r"population-ok:\s*\S")


def watched(path: str) -> bool:
    p = path.replace("\\", "/")
    if p in WATCHED_FILES:
        return True
    return p.startswith(WATCHED_PREFIXES) and p.endswith(WATCHED_SUFFIXES)


def added_lines(diff_text: str) -> list[tuple[str, int, str]]:
    out, cur, ln = [], None, 0
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            cur = line[6:].strip()
        elif line.startswith("@@"):
            try:
                ln = int(line.split("+")[1].split(",")[0].split()[0])
            except (IndexError, ValueError):
                ln = 0
        elif cur and line.startswith("+") and not line.startswith("+++"):
            out.append((cur, ln, line[1:]))
            ln += 1
        elif cur and not line.startswith("-"):
            ln += 1
    return out


def findings(diff_text: str) -> list[str]:
    adds = added_lines(diff_text)
    by_file: dict[str, dict[int, str]] = {}
    for f, n, t in adds:
        by_file.setdefault(f, {})[n] = t
    out: list[str] = []
    for f, n, text in adds:
        if not watched(f):
            continue
        if not _PCT.search(text):
            continue
        context = "\n".join(
            by_file[f].get(n - k, "") for k in (2, 1, 0))
        if _OVERRIDE.search(context):
            continue
        # A population is a COUNT. Suppress only when the context carries an
        # actual number that is not itself the percentage -- `65/147`,
        # `65 of 147`, `n=221`, or a table row's adjacent integers.
        if _EXPLICIT_N.search(context) or _BARE_INT.search(context):
            continue
        out.append(f"{f}:{n}: percentage with no stated population — {text.strip()[:120]!r}")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("diff", nargs="?", default="/tmp/pr.diff")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    p = Path(a.diff)
    if not p.is_file():
        print(f"stated-population-guard: no diff at {p} — nothing scanned "
              f"(this is 'we did not look', not a pass)")
        return 0
    hits = findings(p.read_text(encoding="utf-8", errors="replace"))
    if hits:
        print("::error::a quantitative claim was added to a doc or backlog row with no stated "
              "population. These get READ by later sessions as established fact — this repo has "
              "a figure whose SIGN flips on the filter choice. State n, or the denominator, or "
              "mark it `<!-- population-ok: <reason> -->`:")
        for h in hits:
            print(f"  - {h}")
        return 1
    print("stated-population-guard: OK — every added percentage in a watched path "
          "states its population.")
    return 0


def _self_test() -> int:
    """Built from claims this session actually made — the bad ones must fail."""
    def diff(path: str, *lines: str) -> str:
        body = "".join(f"+{ln}\n" for ln in lines)
        return f"--- a/{path}\n+++ b/{path}\n@@ -1,0 +1,{len(lines)} @@\n{body}"

    cases = [
        # REAL bad claims from 2026-08-26
        ("a bare percentage in a backlog row is caught",
         diff("docs/claude/health-review-backlog.json",
              '"detail": "32.6% of closes land beyond the stop"'), True),
        ("a bare percentage in a doc is caught",
         diff("docs/claude/WORKPLAN-x.md", "vwap overshoots on 44.2% of closes"), True),
        # REAL good claims from the same session
        ("the corrected form, with n, passes",
         diff("docs/claude/health-review-backlog.json",
              '"detail": "65 of 147 measured closes (44.2%) landed past the stop"'), False),
        ("a slash denominator passes",
         diff("docs/claude/WORKPLAN-x.md", "bybit_2/vwap 65/147 = 44.2%"), False),
        ("a denominator on the line ABOVE passes (table headers)",
         diff("docs/claude/WORKPLAN-x.md",
              "| strategy | past | n | rate |", "| vwap | 65 | 147 | 44.2% |"), False),
        # scope + escape hatch
        ("a percentage in code is NOT watched",
         diff("src/runtime/thing.py", "RATE = 0.25  # 25% haircut"), False),
        ("the override suppresses, so the guard is satisfiable",
         diff("docs/claude/WORKPLAN-x.md",
              "<!-- population-ok: a configured threshold, not a measurement -->",
              "the cap is set at 5%"), False),
        ("a line with no percentage at all is quiet",
         diff("docs/claude/WORKPLAN-x.md", "the netting hypothesis is refuted"), False),
    ]
    ok = True
    for label, d, want in cases:
        got = bool(findings(d))
        good = got == want
        ok &= good
        print(f"  self-test ({label}): {'PASS' if good else 'FAIL'}")
    print("stated-population-guard self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
