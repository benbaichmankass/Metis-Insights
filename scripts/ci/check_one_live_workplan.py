#!/usr/bin/env python3
"""EXACTLY ONE work plan may declare itself live, and every other plan must
carry an explicit TERMINAL status in its own header.

WHY THIS EXISTS
---------------
On 2026-09-07 a manager session gave the operator a roadmap status read that was
WRONG, because it reasoned from ``docs/research/WORKPLAN-2026-08-14.md`` — a
document that still declares ``Status: ACTIVE`` while the plan of record had
moved to ``docs/claude/`` on 2026-08-21 and passed through two further
generations. The operator caught it. Operator directive, verbatim:

    "we need to make sure that there's only one open work plan at a time even
    if the last one wasn't finished. It can be marked, like, closed and
    finished or something like that."

The rule as prose already existed in spirit and did not hold, which is why this
is a detector and not a fourth restatement. ``CLAUDE.md``'s RECURRENCE axis is
explicit: every recorded repeated-mistake class gets an executable prevention.

MEASURED AT THE TIME OF WRITING (state the population): the discovery rule below
finds **13** plan documents — ``git ls-files | grep -iE "workplan|work-plan"``
returns 24 paths, of which 10 are ``docs/sprint-logs/`` execution records and 1
is ``.claude/skills/workplan-vs-architecture/SKILL.md``. **TWO** of those 13
declared themselves live: ``WORKPLAN-2026-08-14.md`` and
``POST-VALUE-PIVOT-WORKPLAN-2026-07-27.md``. So this guard had a REAL positive
to fire on the day it was written, independent of its planted controls.

WHAT IT CHECKS
--------------
R1  Exactly one plan declares ``live``.
    ⚠️ ZERO is a FAILURE TOO, and deliberately not folded into R1's message.
    "no plan is live" and "two plans are live" are different faults with
    different fixes, and a guard that only counted upward would grade a repo
    with no plan of record as healthy.

R2  Every discovered plan document declares a status at all. An undeclared file
    is the ACTUAL failure mode this guard exists for — 08-14 misled a reader
    precisely because nothing in it said it was finished. A NEW plan document
    added tomorrow fails this rule until it declares one, which is what makes
    the discovery rule (rather than a hand-maintained list) load-bearing.

R3  ``superseded`` names its successor, and that successor path EXISTS. A
    supersession pointing at a deleted file is a dangling terminal state: it
    reads as resolved and leads nowhere.

R4  ``closed_unfinished`` carries a non-trivial ``What was left:`` line.
    ⚠️ THIS IS THE RULE THAT KEEPS THE TWO TERMINAL STATES APART. The operator
    asked specifically that a plan whose work was OVERTAKEN stay
    distinguishable from one whose work was ABANDONED mid-flight, because
    collapsing them loses what was left undone. Without R4, ``closed_unfinished``
    degrades into a synonym for ``superseded`` — the same collapse
    ``docs/CLAUDE-RULES-CANONICAL.md`` § "Collapsed states" names as the bug.
    The line may legitimately record *"we did not look"*: an unaudited residual
    is a real state and is accepted. What is refused is SILENCE.

R5  The status token is from the closed vocabulary. An invented state is
    refused rather than ignored, so a typo cannot silently exempt a file.

THE HEADER FORMAT, and why it is visible markdown rather than a comment: a
reader who opens one of these files ALONE must not be misled. That is the whole
incident. An HTML comment would satisfy the parser and fail the human.

    > **PLAN STATUS: `superseded`**
    > **Superseded by:** `docs/claude/WORKPLAN-2026-08-29.md`

Self-test: ``python3 scripts/ci/check_one_live_workplan.py --self-test``
(registered in ``guard_selftests.COVERED_BY_CHECKER``, which
``check_selftest_wiring.py`` verifies rather than takes on trust).
"""
from __future__ import annotations

import argparse
import contextlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]

# Discovery: the same probe the incident was measured with, so the guard's
# population and the finding's population cannot drift apart.
_NAME_RE = re.compile(r"workplan|work-plan", re.IGNORECASE)
_EXCLUDE_PREFIXES = (
    "docs/sprint-logs/",   # execution RECORDS of a session, not plans of record
    ".claude/skills/",     # the workplan-vs-architecture SKILL, not a plan
)

# Token charset deliberately includes `-` so a plausible typo
# (`closed-unfinished` for `closed_unfinished`) is CAPTURED and then
# rejected by R5 with a message naming the vocabulary — rather than
# failing to parse and surfacing as R2 "no status", which points the
# reader at the wrong fix.
_STATUS_RE = re.compile(r"PLAN\s+STATUS:\s*`([a-z_-]+)`")
_SUPERSEDED_BY_RE = re.compile(r"\*\*Superseded by:\*\*\s*`([^`]+)`")
_WHAT_WAS_LEFT_RE = re.compile(r"\*\*What was left:\*\*\s*(.+)")
# R6: the LEGACY self-declaration these files used before `PLAN STATUS:` existed.
# `WORKPLAN-2026-08-14.md` carried `**Status:** ACTIVE` and that single line is what
# misled a manager session. Stamping a terminal `PLAN STATUS:` on top while leaving
# the old line intact would leave the file contradicting itself in its own header —
# and a reader believes whichever they hit first. The strikethrough form
# (`~~ACTIVE~~`) is deliberately NOT matched: preserving the struck-through original
# is how the incident stays legible.
_LEGACY_ACTIVE_RE = re.compile(r"\*\*Status:\*\*\s*ACTIVE", re.IGNORECASE)

LIVE = "live"
TERMINAL_STATES = ("superseded", "closed_finished", "closed_unfinished", "historical")
VALID_STATES = (LIVE,) + TERMINAL_STATES

# A residual must actually say something. 24 chars rules out "n/a", "-", "TBD"
# without demanding an essay; the point is that a human wrote a sentence.
_MIN_RESIDUAL_CHARS = 24


def discover(root: Path) -> List[str]:
    """Plan documents, by the same rule the incident was measured with.

    Falls back to a filesystem walk when git is unavailable (the self-test
    plants into a temporary tree that is not a git repo).
    """
    paths: List[str] = []
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.splitlines()
    except (subprocess.CalledProcessError, FileNotFoundError):
        out = [
            str(p.relative_to(root))
            for p in root.rglob("*.md")
            if ".git" not in p.parts
        ]
    for rel in out:
        if not rel.endswith(".md"):
            continue
        if not _NAME_RE.search(rel):
            continue
        if any(rel.startswith(p) for p in _EXCLUDE_PREFIXES):
            continue
        paths.append(rel)
    return sorted(paths)


def parse(root: Path, rel: str) -> Dict[str, Optional[str]]:
    text = (root / rel).read_text(encoding="utf-8", errors="replace")
    # Scan the header only. A status buried 600 lines down does not reach the
    # reader who opens the file, which is the failure being prevented.
    head = "\n".join(text.splitlines()[:40])
    m = _STATUS_RE.search(head)
    sup = _SUPERSEDED_BY_RE.search(head)
    left = _WHAT_WAS_LEFT_RE.search(head)
    return {
        "path": rel,
        "state": m.group(1) if m else None,
        "superseded_by": sup.group(1).strip() if sup else None,
        "what_was_left": left.group(1).strip() if left else None,
        "legacy_active": bool(_LEGACY_ACTIVE_RE.search(head)),
    }


def check(root: Path) -> Tuple[int, List[str], List[str]]:
    """Return (rc, failures, census_lines)."""
    failures: List[str] = []
    census: List[str] = []
    records = [parse(root, rel) for rel in discover(root)]

    if not records:
        # A discovery rule that finds nothing is not a pass. A search returning
        # nothing is not proof of absence (CLAUDE.md § RULE ONE).
        return 1, [
            "DISCOVERY FOUND ZERO PLAN DOCUMENTS. That is a broken probe, not a "
            "clean repo — this guard's own population must never be empty."
        ], census

    undeclared = [r for r in records if r["state"] is None]
    bad_token = [r for r in records if r["state"] and r["state"] not in VALID_STATES]
    live = [r for r in records if r["state"] == LIVE]

    for r in records:
        census.append(f"  {r['state'] or '<NO STATUS>':<18} {r['path']}")

    # R2
    for r in undeclared:
        failures.append(
            f"R2 NO STATUS: {r['path']} declares no `PLAN STATUS:` in its first 40 "
            f"lines. Every plan document must say, in its own header, whether it "
            f"is live or terminal — a reader who opens it alone must not have to "
            f"infer it. Valid: {', '.join('`%s`' % s for s in VALID_STATES)}."
        )

    # R5
    for r in bad_token:
        failures.append(
            f"R5 UNKNOWN STATE: {r['path']} declares `{r['state']}`, which is not "
            f"in the closed vocabulary {VALID_STATES}. A typo must not silently "
            f"exempt a file from R1."
        )

    # R1 — both directions, reported as different faults
    if len(live) > 1:
        failures.append(
            "R1 MORE THAN ONE LIVE PLAN — this is the recurrence this guard "
            "exists for. Exactly one plan may declare `live`; these "
            f"{len(live)} do:\n"
            + "\n".join(f"    - {r['path']}" for r in live)
            + "\n    Give every plan but the current one a terminal status "
              "(`superseded` / `closed_finished` / `closed_unfinished` / "
              "`historical`) in ITS OWN header."
        )
    elif len(live) == 0 and not undeclared:
        # Only meaningful once everything declares; otherwise R2 is the real
        # message and this would be noise on top of it.
        failures.append(
            "R1 NO LIVE PLAN — zero documents declare `live`. This is a "
            "DIFFERENT fault from 'two are live' and has a different fix: the "
            "repo has no plan of record. If that is deliberate, say so by "
            "marking the intended plan `live`; do not leave it implicit."
        )

    # R3
    for r in records:
        if r["state"] != "superseded":
            continue
        if not r["superseded_by"]:
            failures.append(
                f"R3 SUPERSEDED BY NOTHING: {r['path']} is `superseded` but names "
                f"no successor. Add a `**Superseded by:** \\`<path>\\`` line — a "
                f"supersession that does not say what replaced it sends the "
                f"reader nowhere."
            )
        elif not (root / r["superseded_by"]).exists():
            failures.append(
                f"R3 DANGLING SUCCESSOR: {r['path']} says it is superseded by "
                f"`{r['superseded_by']}`, which does not exist. A terminal state "
                f"pointing at a deleted file reads as resolved and is not."
            )

    # R4 — the rule that keeps closed_unfinished from collapsing into superseded
    for r in records:
        if r["state"] != "closed_unfinished":
            continue
        left = r["what_was_left"] or ""
        if len(left) < _MIN_RESIDUAL_CHARS:
            failures.append(
                f"R4 NO RESIDUAL: {r['path']} is `closed_unfinished` but carries "
                f"no substantive `**What was left:**` line "
                f"(found {len(left)} chars, need >= {_MIN_RESIDUAL_CHARS}). "
                f"`closed_unfinished` and `superseded` are DIFFERENT facts — one "
                f"was overtaken, the other was abandoned with work outstanding — "
                f"and dropping the residual is what collapses them. "
                f"'Residual not audited this session' is an acceptable answer; "
                f"silence is not."
            )

    # R6
    for r in records:
        if r["state"] and r["state"] != LIVE and r["legacy_active"]:
            failures.append(
                f"R6 CONTRADICTS ITSELF: {r['path']} declares a terminal "
                f"`PLAN STATUS: `{r['state']}`` AND still carries a legacy "
                f"`**Status:** ACTIVE` line in the same header. A reader believes "
                f"whichever they reach first — which is exactly how "
                f"WORKPLAN-2026-08-14.md misled a manager session for 24 days. "
                f"Strike the old line through (`~~ACTIVE~~`) rather than deleting "
                f"it, so the record survives."
            )

    return (1 if failures else 0), failures, census


# ---------------------------------------------------------------------------
# Planted-positive controls. A guard whose failure path is never exercised is
# indistinguishable from one that always passes.
# ---------------------------------------------------------------------------

_OK_LIVE = "# P\n\n> **PLAN STATUS: `live`**\n\nbody\n"
_OK_SUP = "# P\n\n> **PLAN STATUS: `superseded`**\n> **Superseded by:** `docs/WORKPLAN-live.md`\n"
_OK_CU = (
    "# P\n\n> **PLAN STATUS: `closed_unfinished`**\n"
    "> **What was left:** Lane 0 (live-capability integrity) never dispositioned.\n"
)


@contextlib.contextmanager
def _tree(files: Dict[str, str]):
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        for rel, content in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        yield root


def _expect(root: Path, want_rc: int, must_mention: str, label: str) -> None:
    rc, failures, _ = check(root)
    blob = "\n".join(failures)
    if rc != want_rc:
        raise AssertionError(
            f"{label}: expected rc={want_rc}, got {rc}.\nfailures:\n{blob}"
        )
    if must_mention and must_mention not in blob:
        # A bare rc!=0 proves only that SOMETHING failed. It must fail ON THE
        # PLANT, or a staging mistake and a working probe are indistinguishable.
        raise AssertionError(
            f"{label}: rc was right but the message did not mention "
            f"{must_mention!r}.\nfailures:\n{blob}"
        )
    print(f"  ok  {label}")


def self_test() -> int:
    print("check_one_live_workplan --self-test")

    # Negative control FIRST: a well-formed tree must PASS, or every positive
    # below is meaningless (they would "fail" on an unrelated defect).
    with _tree({
        "docs/WORKPLAN-live.md": _OK_LIVE,
        "docs/WORKPLAN-old.md": _OK_SUP,
        "docs/WORKPLAN-abandoned.md": _OK_CU,
        # Proof the exclusions work: these two are NOT plan documents and must
        # not be graded, even though their names match the discovery regex.
        "docs/sprint-logs/S-WORKPLAN-thing.md": "# no status here at all\n",
        ".claude/skills/workplan-vs-architecture/SKILL.md": "# none here either\n",
    }) as root:
        _expect(root, 0, "", "negative control: one live + terminals passes, "
                             "sprint-logs and the skill excluded")

    # R1 upward — the actual recurrence
    with _tree({
        "docs/WORKPLAN-a.md": _OK_LIVE,
        "docs/WORKPLAN-b.md": _OK_LIVE,
    }) as root:
        _expect(root, 1, "R1 MORE THAN ONE LIVE PLAN", "R1 planted: two live plans")

    # R1 downward — a different fault, deliberately not folded into the above
    with _tree({
        "docs/WORKPLAN-a.md": "# P\n\n> **PLAN STATUS: `superseded`**\n"
                              "> **Superseded by:** `docs/WORKPLAN-b.md`\n",
        "docs/WORKPLAN-b.md": "# P\n\n> **PLAN STATUS: `historical`**\n",
    }) as root:
        _expect(root, 1, "R1 NO LIVE PLAN", "R1 planted: zero live plans")

    # R2 — the file that misled the manager: no status at all
    with _tree({
        "docs/WORKPLAN-live.md": _OK_LIVE,
        "docs/research/WORKPLAN-undeclared.md": "# Work plan\n\n> **Status:** ACTIVE\n",
    }) as root:
        _expect(root, 1, "R2 NO STATUS", "R2 planted: undeclared plan document")

    # R3 — dangling successor
    with _tree({
        "docs/WORKPLAN-live.md": _OK_LIVE,
        "docs/WORKPLAN-x.md": "# P\n\n> **PLAN STATUS: `superseded`**\n> **Superseded by:** `docs/GONE.md`\n",
    }) as root:
        _expect(root, 1, "R3 DANGLING SUCCESSOR", "R3 planted: successor does not exist")

    # R3 — superseded naming nobody
    with _tree({
        "docs/WORKPLAN-live.md": _OK_LIVE,
        "docs/WORKPLAN-y.md": "# P\n\n> **PLAN STATUS: `superseded`**\n",
    }) as root:
        _expect(root, 1, "R3 SUPERSEDED BY NOTHING", "R3 planted: no successor named")

    # R4 — THE collapse the operator asked to prevent
    with _tree({
        "docs/WORKPLAN-live.md": _OK_LIVE,
        "docs/WORKPLAN-z.md": "# P\n\n> **PLAN STATUS: `closed_unfinished`**\n> **What was left:** n/a\n",
    }) as root:
        _expect(root, 1, "R4 NO RESIDUAL",
                "R4 planted: closed_unfinished with no residual "
                "(the superseded/closed_unfinished collapse)")

    # R4 — and that an HONEST 'we did not look' residual is ACCEPTED, not
    # refused. The guard demands a sentence, never a verdict.
    with _tree({
        "docs/WORKPLAN-live.md": _OK_LIVE,
        "docs/WORKPLAN-w.md": "# P\n\n> **PLAN STATUS: `closed_unfinished`**\n"
                              "> **What was left:** Residual not audited this session.\n",
    }) as root:
        _expect(root, 0, "", "R4 control: an unaudited residual is a valid answer")

    # R5 — invented token must not silently exempt
    with _tree({
        "docs/WORKPLAN-live.md": _OK_LIVE,
        "docs/WORKPLAN-q.md": "# P\n\n> **PLAN STATUS: `closed-unfinished`**\n",
    }) as root:
        _expect(root, 1, "R5 UNKNOWN STATE", "R5 planted: token outside the vocabulary")

    # R6 — the stamped-but-still-contradicting case
    with _tree({
        "docs/WORKPLAN-live.md": _OK_LIVE,
        "docs/WORKPLAN-r.md": "# P\n\n> **PLAN STATUS: `closed_unfinished`**\n"
                              "> **What was left:** Lane 0 was never dispositioned.\n"
                              "> **Status:** ACTIVE. built from a review\n",
    }) as root:
        _expect(root, 1, "R6 CONTRADICTS ITSELF",
                "R6 planted: terminal status beside a legacy ACTIVE line")

    # R6 control — the STRUCK-THROUGH original must be accepted, or the honest
    # fix (preserve the record) would be the one thing the guard refuses.
    with _tree({
        "docs/WORKPLAN-live.md": _OK_LIVE,
        "docs/WORKPLAN-s.md": "# P\n\n> **PLAN STATUS: `closed_unfinished`**\n"
                              "> **What was left:** Lane 0 was never dispositioned.\n"
                              "> **Status:** ~~ACTIVE~~ - closed 2026-09-07\n",
    }) as root:
        _expect(root, 0, "", "R6 control: a struck-through ACTIVE is accepted")

    # Discovery cannot be silently empty.
    with _tree({"docs/README.md": "# nothing matching\n"}) as root:
        _expect(root, 1, "DISCOVERY FOUND ZERO", "discovery control: empty population fails")

    print("check_one_live_workplan: self-test OK (12 controls)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true",
                    help="run the planted-positive controls and exit")
    ap.add_argument("--verbose", action="store_true", help="print the full census")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()

    rc, failures, census = check(REPO)
    print(f"one-live-workplan: {len(census)} plan document(s) discovered")
    if args.verbose or rc:
        print("\n".join(census))
    if rc:
        print("\nFAILURES:\n")
        for f in failures:
            print(f"  {f}\n")
        return 1
    print("one-live-workplan: OK — exactly one live plan, every other terminal.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
