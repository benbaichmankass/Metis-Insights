#!/usr/bin/env python3
# wiring: manual-only - a re-measurable AUDIT, run by a session that needs the
# landing denominator, and by anyone who edits an evidence workflow. Nothing
# schedules it, and that is not the staleness this file exists to fix: the
# defect was a TYPED list that could not be re-derived, and the fix is that
# re-deriving it is now one command that carries its own positive control.
# It is deliberately NOT a CI gate -- it asserts nothing about which workflows
# SHOULD land (that is R1, and an operator decision of 2026-08-27 defers it),
# so a gate here would fail on nothing or, worse, invite someone to make it
# fail on "does_not_land" -- which the backlog row names in capitals as a
# SHAPE, NOT A DEFECT.
"""Re-measure which evidence workflows LAND their results, instead of typing a list.

WHY THIS EXISTS
---------------
``BL-20260827-EIGHTEEN-EVIDENCE-WORKFLOWS-UPLOAD-AND-LAND-NOTHING`` filed a
hand-typed inventory *"as a DENOMINATOR so the next session does not have to
re-derive it"*. **It went stale in ONE DAY, in its own headline case**:
``trainer-offload-train`` was the row's only confirmed wanted-result-lost
example, and R3 (#10368/#10390) made it land on 2026-08-28 — so the next
session had to re-derive the list anyway
(``docs/research/evidence-workflow-landing-triage-2026-08-29.md`` § 1).

A denominator that cannot re-measure itself is a **snapshot**, not a
denominator. This is that triage's own § 4 recommendation, executed.

WHAT THIS IS NOT
----------------
⚠️ **This ASSERTS NOTHING and gates nothing.** It is a measurement.

"Does not land" is a **SHAPE, NOT A DEFECT** — the backlog row says so in
capitals, and there is a recorded **operator decision (2026-08-27)** against
wiring R2 landing assertions into these workflows, because for most of them
nobody has yet decided what store they would assert against. That is R1 (the
results contract), not R2. Adding ``assert_rows_landed`` here would, in the
row's own words, *"answer the wrong question loudly"*.

So this script reports; the judgement about which workflows SHOULD land stays
in the triage doc, where it can be argued with.

STATES ARE NOT COLLAPSED
------------------------
``lands`` · ``does_not_land`` · ``unreadable`` (the file could not be parsed —
**we did not look**, which is emphatically not "it does not land").

THE POSITIVE CONTROL IS THE POINT
---------------------------------
A predicate that silently stops matching would report every workflow as
``does_not_land`` and look like a dramatic finding. So the four workflows known
to land must come back as landing; if any does not, this **exits non-zero and
reports nothing else**, because its silence would otherwise mean nothing.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys
from typing import Dict, List, Tuple

WORKFLOW_DIR = pathlib.Path(".github/workflows")

# The landing predicate, taken VERBATIM from the backlog row so this script and
# the row measure the same thing. Widening it here without widening the row is
# how two "denominators" start disagreeing.
LANDING_RE = re.compile(
    r"git\s+push|git\s+commit|git-auto-commit|add-and-commit|"
    r"create-pull-request|peter-evans|gh\s+pr\s+create",
    re.IGNORECASE,
)

# ⚠️ THIS IS A SUPERSET, DELIBERATELY, AND THE COUNT WILL NOT MATCH THE DOC.
#
# The 2026-08-29 triage counted **22** "evidence workflows"; this predicate
# returns **40**, because "uploads an artifact" is NECESSARY but NOT SUFFICIENT
# for "produces research evidence" -- operational workflows upload artifacts too
# (`get-diag-token`, `prop-report`, `llm-delegate`, `continue-work`,
# `health-snapshot` all match here and are not evidence workflows).
#
# The gap is the finding, not a bug: **the repo has no mechanical definition of
# "evidence workflow"**, so the 22 was hand-curated and is exactly the kind of
# judgement that goes stale. Narrowing this regex until it happened to return 22
# would just be a differently-typed hand list wearing a script's clothes -- the
# thing this file exists to replace. So it reports the superset and says so, and
# the (a)/(b) judgement stays in the triage doc where it can be argued with.
EVIDENCE_RE = re.compile(r"actions/upload-artifact", re.IGNORECASE)

# Known landers. If the predicate stops matching these, it is broken.
CONTROLS = ("e35-bracket-sweep", "gpu-burst-train",
            "m20-exit-lever-sweep", "training-rerun-5m")

LANDS, DOES_NOT_LAND, UNREADABLE = "lands", "does_not_land", "unreadable"


def classify(text: str) -> str:
    return LANDS if LANDING_RE.search(text) else DOES_NOT_LAND


def scan(wf_dir: pathlib.Path) -> Tuple[Dict[str, str], List[str]]:
    """Return {stem: state} for evidence workflows, plus non-evidence stems."""
    states: Dict[str, str] = {}
    skipped: List[str] = []
    for p in sorted(wf_dir.glob("*.y*ml")):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            # We could not look. NOT "it does not land".
            states[p.stem] = UNREADABLE
            continue
        if not EVIDENCE_RE.search(text):
            skipped.append(p.stem)
            continue
        states[p.stem] = classify(text)
    return states, skipped


def _self_test() -> int:
    """Planted controls -- a probe is only trusted once it has been made to fail."""
    ok = True
    cases = [
        ("git push lands", "actions/upload-artifact\n  run: git push", LANDS),
        ("peter-evans lands", "actions/upload-artifact\nuses: peter-evans/create-pull-request", LANDS),
        ("gh pr create lands", "actions/upload-artifact\n  run: gh pr create --fill", LANDS),
        ("upload only does not land", "uses: actions/upload-artifact@v4", DOES_NOT_LAND),
        # ⚠️ A KNOWN LIMITATION, pinned rather than hidden. The predicate is a
        # SUBSTRING match, so prose that merely mentions pushing -- including a
        # COMMENT saying the workflow does not push -- classifies as `lands`.
        # This test asserts the WRONG-BUT-ACTUAL behaviour on purpose, so that
        # anyone tightening the predicate sees it fail and knows why it existed.
        # It can only OVER-report landing, never under-report, so it cannot
        # manufacture a "nothing lands" finding -- which is the direction that
        # would matter. Naming this case after what it CHECKS rather than after
        # what one might wish it checked is the same discipline as
        # BL-20260829-OVER-COVER-ALERT-SAYS-LEGS-PILED-UP-WHEN-THERE-IS-ONE-LEG.
        ("substring match: a COMMENT mentioning 'git push' still reads as lands "
         "(known over-report, pinned)",
         "actions/upload-artifact\n# we are not git pushing here", LANDS),
    ]
    for name, text, want in cases:
        got = classify(text)
        flag = "PASS" if got == want else "FAIL"
        if got != want:
            ok = False
        print(f"  self-test {name}: {flag} (got {got})")
    # A non-evidence file must be SKIPPED, not silently counted as not-landing.
    states, skipped = scan(pathlib.Path("/nonexistent-dir-for-self-test"))
    if states or skipped:
        print("  self-test empty-dir: FAIL (expected nothing)")
        ok = False
    else:
        print("  self-test empty-dir: PASS (no dir -> no rows, not a zero finding)")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--dir", default=str(WORKFLOW_DIR))
    args = ap.parse_args(argv)

    if args.self_test:
        print("evidence-workflow-inventory self-test")
        return _self_test()

    wf_dir = pathlib.Path(args.dir)
    if not wf_dir.is_dir():
        print(f"::error::{wf_dir} is not a directory — we could not look. "
              f"This is NOT 'no workflows land'.")
        return 2

    states, skipped = scan(wf_dir)

    # POSITIVE CONTROL FIRST. Report nothing if the instrument is broken.
    missing = [c for c in CONTROLS if c not in states]
    wrong = [c for c in CONTROLS if states.get(c) not in (None, LANDS)]
    if missing or wrong:
        print("::error::POSITIVE CONTROL FAILED — the landing predicate no longer "
              "matches known landers, so this run's counts mean nothing and are "
              "deliberately not printed.")
        for c in missing:
            print(f"  control absent from the evidence population: {c}")
        for c in wrong:
            print(f"  control classified {states.get(c)!r}, expected {LANDS}: {c}")
        return 1
    print(f"positive control OK — {len(CONTROLS)} known landers all classify as "
          f"{LANDS} ({', '.join(CONTROLS)})")

    lands = sorted(k for k, v in states.items() if v == LANDS)
    nots = sorted(k for k, v in states.items() if v == DOES_NOT_LAND)
    unread = sorted(k for k, v in states.items() if v == UNREADABLE)

    print()
    print(f"POPULATION: {len(states)} workflows that upload an artifact, of "
          f"{len(states) + len(skipped)} workflow files scanned in {wf_dir}.")
    print("⚠️ This is a SUPERSET of the hand-curated 'evidence workflow' set (22 in "
          "the 2026-08-29 triage): uploading an artifact is necessary but not "
          "sufficient for producing research evidence, and operational workflows "
          "upload artifacts too. The repo has NO mechanical definition of "
          "'evidence workflow' — that gap is why the hand list went stale.")
    print(f"  lands          {len(lands)}")
    print(f"  does_not_land  {len(nots)}")
    print(f"  unreadable     {len(unread)}")
    print()
    print("⚠️ 'does_not_land' is a SHAPE, not a defect — see "
          "docs/research/evidence-workflow-landing-triage-2026-08-29.md for which "
          "of these are MEANT to accumulate (a) and which are one-shot probes (b). "
          "This script asserts nothing.")
    print()
    for label, group in ((LANDS, lands), (DOES_NOT_LAND, nots), (UNREADABLE, unread)):
        if not group:
            continue
        print(f"[{label}]")
        for w in group:
            print(f"  {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
