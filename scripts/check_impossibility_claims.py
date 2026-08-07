#!/usr/bin/env python3
"""impossibility-claim guard — an "X cannot be done" claim must name what it checked.

`CLAUDE-RULES-CANONICAL.md` § "Green is not evidence", obligation 3:

    An IMPOSSIBILITY claim gets more scepticism than a success claim, not less.
    Before writing that something **cannot be measured / is not replayable /
    needs new tooling**, check the research capability index and grep
    `scripts/research/`. **Say *which* tool you checked.**

    A tool wrongly reporting "measured: OK" wastes a decision. A tool wrongly
    reporting "this cannot be measured" **closes off the work** — nobody
    re-checks a dead end, and the claim propagates into backlog rows and
    operator decisions as settled fact.

That rule existed, was loaded, and was walked past three times in a row.

WHY THIS GUARD EXISTS (the incident, 2026-08-07)
------------------------------------------------
A session ran the backtest↔live trust map, got `insufficient-live` on all three
legs, and concluded that live-trade **accrual** was the binding constraint and
"cannot be worked around by writing code."

Every piece of evidence refuting it was already in the repo:

  * `scripts/research/backtest_fidelity_calibrate.py`'s own docstring says it
    exists *because* "only trust real live trades ... caps every decision at
    reality's clock" — then gates on `MIN_LIVE_N = 30`, reproducing the ceiling
    it was built to remove;
  * the platform design's **P2** row: *"Fidelity becomes structural ... drift ->
    0"*, effort 1-2 sessions, needing **zero** live trades;
  * its **P3** row names the unblock: *"replace 'live-holdout only' with
    'calibrated-OOS-or-live' ... the actual unblock"*.

The claim named **no** tool and checked none. It then propagated unchecked
through the workplan, a session briefing, and operator-facing status — three
restatements, zero re-derivations. Prose did not stop it, so this does.

THE CONTRACT
------------
An added line asserting impossibility/blocked-on-accrual must carry a
machine-checkable annotation naming what was actually consulted::

    checked: scripts/research/backtest_fidelity_calibrate.py

**Verified, not presence-only.** The named path must EXIST in the repo. This is
the direct lesson from `new-table-wiring-guard`, whose presence-only marker made
the cheapest way to silence a real finding *naming a table that does not
exist* — a guard that is cheaper to lie to than to satisfy is worse than no
guard. The annotation is also excluded from its own evidence scan, so
`checked:` inside the claim line cannot self-satisfy.

Scope: the review backlogs + `docs/research/`. Diff-scoped by default (only
lines this change ADDS), so the existing corpus is grandfathered exactly like
`diagnostic-provenance-guard`; `--all` is the standing audit.

Exit codes follow the repo convention: **2 = could not measure** (scanned
nothing — an absent result, never a clean one), **1 = findings**, 0 = clean.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: Files whose ADDED lines are scanned.
SCAN_GLOBS = (
    "docs/claude/health-review-backlog.json",
    "docs/claude/performance-review-backlog.json",
    "docs/claude/ml-review-backlog.json",
)
SCAN_DIRS = ("docs/research",)

#: Assertions that further work is impossible or must wait. Deliberately tight:
#: these are CLAIMS THAT CLOSE OFF WORK, not descriptive vocabulary. A verdict
#: string a tool emits (`insufficient-live`, `insufficient_n`) is NOT here —
#: those are honest measured outcomes, which is the behaviour we want.
CLAIM_PATTERNS = [
    (r"cannot\s+be\s+(?:measured|reconstructed|replayed|backfilled|computed)", "cannot-be-measured"),
    (r"can'?t\s+be\s+(?:measured|reconstructed|replayed|backfilled|computed)", "cannot-be-measured"),
    (r"\b(?:is|are)\s+(?:not|un)\s*measurable\b", "unmeasurable"),
    (r"\bunmeasurable\b", "unmeasurable"),
    (r"\bnot\s+replayable\b", "not-replayable"),
    (r"cannot\s+be\s+worked\s+around", "cannot-work-around"),
    (r"no\s+(?:code|tool|amount\s+of\s+code)\s+can\b", "no-code-can"),
    (r"\bneeds?\s+new\s+tooling\b", "needs-new-tooling"),
    (r"blocked\s+on\s+(?:live\s+data|accrual|more\s+(?:live\s+)?(?:data|trades))", "blocked-on-accrual"),
    (r"wait(?:ing)?\s+(?:for|on)\s+(?:more\s+)?(?:live\s+)?(?:data|trades|rows)\s+to\s+accrue", "waiting-for-accrual"),
    (r"\bcheck\s+back\s+in\s+\w+\s+weeks?\b", "waiting-for-accrual"),
]

#: The escape hatch, and the only one.
ANNOTATION = re.compile(r"checked:\s*([^\s,;)\]\"']+)")

#: Claims may declare themselves REFUTATIONS rather than assertions — a doc that
#: quotes an impossibility claim in order to demolish it is the behaviour we
#: want, not a violation. Still requires `checked:` somewhere in its block.
_REFUTING = re.compile(r"(?i)\b(?:phantom\s+gate|is\s+wrong|WRONG|refut|incorrect|false)\b")


def _annotation_ok(value: str) -> bool:
    """The named tool must actually exist. A path that does not resolve is the
    presence-only lie `new-table-wiring-guard` was defeated by."""
    cleaned = value.strip().strip("`'\"").rstrip(".,;:")
    if not cleaned:
        return False
    return (REPO / cleaned).exists()


#: How many lines either side of a claim the `checked:` annotation may live in.
#: Deliberately LOCAL. Scanning a whole file was the first version's bug: on a
#: 10k-line backlog JSON it harvested unrelated `checked:`-shaped strings
#: (`bybit_2`, `bool`, `bot.log`) from elsewhere in the file and reported them
#: as the claim's annotation — an unasserted-denominator defect in the guard
#: written to catch unasserted claims.
#:
#: The window is per file TYPE because line density differs by an order of
#: magnitude. A backlog JSON is row-structured — one item is ~10 lines, so a
#: tight window is what keeps a neighbouring row's annotation from satisfying
#: this row's claim. Markdown prose is sparse: a blockquote lead-in plus a
#: 4-line bullet already spans 7, so ±6 rejected a correctly-annotated block
#: (`WORKPLAN-2026-08-05.md`, the doc this guard was written alongside).
ANNOTATION_WINDOW = 6
ANNOTATION_WINDOW_PROSE = 14


def _window_for(path: str) -> int:
    return ANNOTATION_WINDOW_PROSE if path.endswith(".md") else ANNOTATION_WINDOW


def check_lines(lines: list[tuple[int, str]], path: str, *,
                context: str = "", body_lines: list[str] | None = None) -> list[str]:
    """Flag impossibility claims lacking a verified `checked:` annotation.

    ``lines`` is (lineno, text) — the lines under scrutiny. The annotation may
    sit within ``_window_for(path)`` lines either side, resolved against
    ``body_lines`` (the full file) when given, else ``context``.
    """
    failures: list[str] = []
    body = body_lines if body_lines is not None else context.splitlines()
    span = _window_for(path)

    def _window(lineno: int) -> str:
        if not body:
            return context
        lo = max(0, lineno - 1 - span)
        hi = min(len(body), lineno + span)
        return "\n".join(body[lo:hi])

    for lineno, raw in lines:
        local = _window(lineno)
        # The annotation must not be its own evidence: strip annotation text
        # before scanning for claims, so `checked: cannot-be-measured.py`
        # cannot satisfy itself.
        ann_values = ANNOTATION.findall(local)
        verified = [v for v in ann_values if _annotation_ok(v)]
        unverified = [v for v in ann_values if not _annotation_ok(v)]
        text = ANNOTATION.sub("", raw)
        for pattern, label in CLAIM_PATTERNS:
            if not re.search(pattern, text, re.IGNORECASE):
                continue
            if verified:
                break
            snippet = raw.strip()[:120]
            if unverified:
                failures.append(
                    f"{path}:{lineno} impossibility claim ({label}) carries a "
                    f"`checked:` annotation naming {unverified!r}, which does NOT "
                    f"exist in the repo. A guard that is cheaper to lie to than "
                    f"to satisfy is worse than no guard — name a real path. "
                    f"| {snippet}")
            elif _REFUTING.search(local):
                failures.append(
                    f"{path}:{lineno} impossibility claim ({label}) appears in a "
                    f"REFUTING block but still names nothing. Even a refutation "
                    f"must say what it checked, or the next reader inherits the "
                    f"claim without the evidence. Add `checked: <path>`. "
                    f"| {snippet}")
            else:
                failures.append(
                    f"{path}:{lineno} impossibility claim ({label}) with no "
                    f"`checked: <path>` annotation. CLAUDE-RULES-CANONICAL "
                    f"§ 'Green is not evidence' obligation 3: an impossibility "
                    f"claim gets MORE scepticism, and must say WHICH tool it "
                    f"checked. Check docs/research/RESEARCH-CAPABILITY-INDEX.md "
                    f"and grep scripts/research/ first. | {snippet}")
            break
    return failures


def _tracked_files() -> list[str]:
    out: list[str] = [g for g in SCAN_GLOBS if (REPO / g).exists()]
    for d in SCAN_DIRS:
        out.extend(str(p.relative_to(REPO)) for p in (REPO / d).rglob("*.md"))
    return sorted(out)


def _added_lines(base: str, path: str) -> list[tuple[int, str]]:
    r = subprocess.run(["git", "diff", "--unified=0", f"{base}...HEAD", "--", path],
                       cwd=REPO, capture_output=True, text=True)
    if r.returncode != 0:
        return []
    added: list[tuple[int, str]] = []
    lineno = 0
    for line in r.stdout.splitlines():
        m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)", line)
        if m:
            lineno = int(m.group(1))
            continue
        if line.startswith("+") and not line.startswith("+++"):
            added.append((lineno, line[1:]))
            lineno += 1
    return added


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", help="base ref; scan only lines added vs it")
    ap.add_argument("--all", action="store_true", help="standing audit over the whole corpus")
    args = ap.parse_args()
    if not args.base and not args.all:
        ap.error("pass --base <ref> or --all")

    failures: list[str] = []
    scanned = 0
    for path in _tracked_files():
        full = REPO / path
        try:
            body = full.read_text(encoding="utf-8")
        except OSError:
            continue
        scanned += 1
        if args.all:
            lines = list(enumerate(body.splitlines(), start=1))
        else:
            lines = _added_lines(args.base, path)
            if not lines:
                continue
        failures.extend(check_lines(lines, path, context=body,
                                    body_lines=body.splitlines()))

    for f in failures:
        print(f"::error::{f}")
    mode = "--all" if args.all else f"added-vs-{args.base}"
    print(f"impossibility-claim-guard: {scanned} file(s) scanned ({mode}), "
          f"{len(failures)} unsubstantiated claim(s).")
    if scanned == 0:
        # Could-not-measure is its own outcome, distinct from clean (exit 2, the
        # `check_workflow_shell.py` convention).
        print("::error::scanned NOTHING — no backlog or research file readable. "
              "That is an ABSENT result, not a clean one (wrong cwd?).")
        return 2
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
