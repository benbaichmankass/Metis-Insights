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
import json
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


BASELINE_PATH = REPO / "docs/claude/impossibility-claim-baseline.json"


def _per_file_counts(failures: list[str]) -> dict:
    """Bucket findings by the file they were reported against.

    PER-FILE, not a single total, deliberately. A bare total is satisfied by
    CHURN — annotate one row, add an unannotated one somewhere else, and the
    count is unchanged while the corpus got no better. A per-file map fails the
    file that grew, which is the thing a reader needs to know.
    """
    counts: dict = {}
    for f in failures:
        path = f.split(":", 1)[0]
        counts[path] = counts.get(path, 0) + 1
    return counts


def _load_baseline() -> dict | None:
    """Read the committed baseline, or None when we could not look.

    None and {} are DIFFERENT: {} asserts a clean corpus, None says the file is
    missing or unreadable. Returning {} here would turn a deleted baseline into
    "every file regressed", which is a confident wrong answer.
    """
    try:
        return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))["per_file"]
    except (OSError, ValueError, KeyError):
        return None


def _ratchet(counts: dict) -> tuple[list[str], list[str]]:
    """Compare live per-file counts to the baseline. Returns (regressions, improvements)."""
    base = _load_baseline()
    if base is None:
        # `relative_to` RAISES when the path is outside the repo, and this is the
        # error path — a reporter that crashes while reporting a failure turns a
        # legible "could not grade" into an opaque traceback.
        try:
            where = BASELINE_PATH.relative_to(REPO).as_posix()
        except ValueError:
            where = str(BASELINE_PATH)
        return ([f"baseline unreadable at {where} — "
                 "cannot grade regression. This is an ABSENT result, not a clean one; "
                 "regenerate with --update-baseline."], [])
    regressions, improvements = [], []
    for path, n in sorted(counts.items()):
        was = base.get(path, 0)
        if n > was:
            regressions.append(
                f"{path}: {was} -> {n} unsubstantiated impossibility claim(s). "
                "A NEW one was committed here. Add `checked: <path>` naming the tool "
                "you actually ran, or annotate the row.")
    for path, was in sorted(base.items()):
        n = counts.get(path, 0)
        if n < was:
            improvements.append(f"{path}: {was} -> {n}")
    return regressions, improvements


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", help="base ref; scan only lines added vs it")
    ap.add_argument("--all", action="store_true", help="standing audit over the whole corpus")
    ap.add_argument("--ratchet", action="store_true",
                    help="with --all: fail only where a file EXCEEDS its committed baseline")
    ap.add_argument("--update-baseline", action="store_true",
                    help="with --all: rewrite the baseline from the current corpus")
    args = ap.parse_args()
    if not args.base and not args.all:
        ap.error("pass --base <ref> or --all")
    if (args.ratchet or args.update_baseline) and not args.all:
        ap.error("--ratchet / --update-baseline require --all")

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

    if args.update_baseline:
        counts = _per_file_counts(failures)
        BASELINE_PATH.write_text(json.dumps(
            {"_comment": (
                "Standing per-file count of unsubstantiated impossibility claims. "
                "The diff-scoped guard cannot police lines nobody edits, so this "
                "ratchet is what keeps already-committed rows visible. Counts may "
                "only go DOWN: regenerate with "
                "`python3 scripts/check_impossibility_claims.py --all --update-baseline`."),
             "per_file": dict(sorted(counts.items()))},
            indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"impossibility-claim-guard: baseline written, "
              f"{sum(counts.values())} claim(s) across {len(counts)} file(s).")
        return 0

    if args.ratchet:
        counts = _per_file_counts(failures)
        regressions, improvements = _ratchet(counts)
        for line in improvements:
            print(f"::notice::impossibility-claim-guard improved — {line}")
        for line in regressions:
            print(f"::error::impossibility-claim-guard REGRESSION — {line}")
        total = sum(counts.values())
        print(f"impossibility-claim-guard (--all --ratchet): {scanned} file(s) scanned, "
              f"{total} standing claim(s), {len(regressions)} regression(s), "
              f"{len(improvements)} improvement(s).")
        if scanned == 0:
            print("::error::scanned NOTHING — ABSENT, not clean.")
            return 2
        if improvements and not regressions:
            print("::notice::baseline can be tightened: "
                  "run `--all --update-baseline` and commit.")
        return 1 if regressions else 0

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
