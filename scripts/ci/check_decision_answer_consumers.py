#!/usr/bin/env python3
"""Anything that asks "is this decision answered?" must ask the ONE owner.

WHY THIS EXISTS
---------------
``decision_requests`` answers are recorded in TWO shapes and both are live on
``main``: a nested ``answer:`` block (written by ``POST /api/bot/work/decision``)
and a FLAT ``verdict`` + ``chosen`` + ``answered_at`` triple (written by hand
when the operator answers in conversation, which is how most decisions here are
actually given). ``src/runtime/work_decisions.py`` owns both —
``normalise_conversational_answer`` grades the flat shape into a closed
vocabulary and ``grade_answer_state`` folds it in.

A consumer that tests ``req.get("answer")`` and nothing else therefore reports
**settled decisions as open**. That is not hypothetical and it is not cheap:

* ``BL-20260911-DECISION-REQUESTS-CARRY-TWO-INCOMPATIBLE-ANSWER-SCHEMAS-SO-A-PROBE-KEYED-ON-ONE-REPORTS-ANSWERED-DECISIONS-AS-OPEN``
  records a session telling the operator that two ALREADY-ANSWERED Tier-2/3
  decisions had never been written back — including one whose ``scope`` warns
  that a wider reading must not be taken. A manager acting on it could have
  re-asked settled decisions.
* ``scripts/ops/blocked_lane_watch.py`` carried the same test until 2026-09-13,
  so a lane whose blocking decision had been answered in conversation graded
  ``still_blocking``.
* MEASURED 2026-09-13 over all 183 work objects: 29 requests, **3 flat, 25
  nested, 1 genuinely unanswered**. A nested-only probe reports **four**.

⚠️ **THE RULE IS STRUCTURAL, NOT LEXICAL-SEMANTIC.** It does not try to judge
whether a given test is *correct* — it cannot, and guessing would be the
UNPROVENANCED DIAGNOSTIC class this repo has a guard for. It asks one
answerable question: *does this file both touch ``decision_requests`` AND test
answeredness inline, while importing nothing from the owner?*

⚠️ **READING ``decision_requests`` IS NOT ITSELF A VIOLATION.**
``src/runtime/decision_subject.py`` iterates them and never asks whether one is
answered; flagging it would be a false positive that teaches the guard is noise.
BOTH conditions must hold.

Exit codes: 0 clean · 1 finding · 2 could not measure (an ABSENT result).
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: The one module allowed to define answeredness. Anything else must import it.
OWNER = "src/runtime/work_decisions.py"

#: This file, exempt from its own scan — see the check below.
_SELF = "scripts/ci/check_decision_answer_consumers.py"

#: Proof that a consumer went through the owner: an actual IMPORT statement.
#:
#: ⚠️ THIS WAS A BARE TOKEN TEST (`"work_decisions" in text`) FOR ABOUT TEN
#: MINUTES AND IT DID NOT WORK. Checked against the REAL pre-fix
#: `blocked_lane_watch.py` — import stripped, inline test restored — the guard
#: read OK, because the file still MENTIONED `work_decisions` in a comment. All
#: six planted controls passed throughout. That is the presence-only marker this
#: repo calls *cheaper to lie to than to satisfy* (`new-table-wiring-guard`),
#: reproduced inside the guard written to stop a related defect. A guard whose
#: controls pass while the real case does not fire is worse than no guard.
_IMPORTS_OWNER = re.compile(
    r"^\s*(?:from\s+[\w.]*work_decisions\s+import\b"
    r"|import\s+[\w.]*work_decisions\b)",
    re.MULTILINE,
)

SCAN_DIRS = ("src", "scripts")

#: A file is a CONSUMER only if it touches the requests at all.
_TOUCHES = "decision_requests"

#: An inline answeredness test: one of the three key names that decide the
#: question, read off a mapping.
_INLINE = re.compile(
    r"""\.get\(\s*["'](answer|verdict|chosen)["']|\[\s*["'](answer|verdict|chosen)["']\s*\]"""
)

#: How near a `decision_requests` mention an inline test must sit to count.
#:
#: ⚠️ PROXIMITY IS LOAD-BEARING AND WAS ADDED AFTER A MEASURED FALSE POSITIVE.
#: `verdict` and `chosen` are ordinary words here — `render_due_list.py` reads
#: `verdict` for a SUNSET retire-candidate (line ~870) and for the due list's
#: OWN envelope (~2219), more than a thousand lines from where it mentions
#: decision requests, and it delegates answeredness to
#: `check_decision_answers.py` on purpose. A whole-file test flagged it, which
#: is a false positive that teaches a reader the guard is noise. Two co-located
#: facts are the claim; two facts anywhere in one file are not.
_NEAR_LINES = 40

#: Going through the owner TRANSITIVELY also counts. `render_due_list.py`
#: imports `check_decision_answers.py` precisely so it does not own a grader,
#: and its own comment says asserting against a local restatement "would stay
#: green after the two definitions diverged".
_DELEGATES = re.compile(r"check_decision_answers")

#: Files that touch and test inline WITHOUT the owner, and are accepted anyway.
#: Every entry needs a reason; an unexplained exemption is the marker this repo
#: calls cheaper to lie to than to satisfy. Empty today, deliberately.
ALLOWED: dict[str, str] = {}


def violations(repo: Path = REPO) -> list[str]:
    out: list[str] = []
    for d in SCAN_DIRS:
        for p in sorted((repo / d).rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            rel = p.relative_to(repo).as_posix()
            if rel == OWNER or rel == _SELF:
                # ⚠️ THE GUARD EXEMPTS ITSELF, and must. Its docstring quotes the
                # defect and its planted controls WRITE the offending pattern, so
                # a guard that scanned itself would fail on its own evidence —
                # and the only way to fix that would be to stop quoting the bug.
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError as exc:                      # pragma: no cover
                out.append(f"::error::could not read {rel}: {exc}")
                continue
            if _TOUCHES not in text:
                continue
            lines = text.splitlines()
            touch_at = [i for i, ln in enumerate(lines) if _TOUCHES in ln]
            inline_at = [i for i, ln in enumerate(lines) if _INLINE.search(ln)]
            if not any(abs(i - j) <= _NEAR_LINES for i in inline_at for j in touch_at):
                # Either it never asks whether one is answered, or the two facts
                # are far apart and unrelated. See `_NEAR_LINES`.
                continue
            if _IMPORTS_OWNER.search(text) or _DELEGATES.search(text):
                continue
            if rel in ALLOWED:
                continue
            out.append(
                f"::error::{rel} reads `decision_requests` and tests answeredness "
                f"inline without importing `{OWNER}`. An answer given in "
                f"conversation is recorded as a FLAT verdict/chosen pair with no "
                f"`answer` block, so this reports settled decisions as OPEN. "
                f"Import `normalise_conversational_answer` / `grade_answer_state` "
                f"from `src.runtime.work_decisions` instead of re-deriving the test."
            )
    return out


def _self_test() -> int:
    """Planted controls, in BOTH directions."""
    import tempfile

    fails: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "scripts").mkdir(parents=True)
        (root / "src/runtime").mkdir(parents=True)
        (root / OWNER).write_text("# the owner\ndecision_requests\n.get('answer')\n")
        f = root / "scripts" / "c.py"

        # 1. touches + tests inline + no owner import -> MUST fire
        f.write_text('d = doc.get("decision_requests")\nif r.get("answer"): pass\n')
        if not violations(root):
            fails.append("control 1: a bare answer-test consumer did not fire")

        # 2. same file, importing the owner -> must NOT fire
        f.write_text('from src.runtime.work_decisions import grade_answer_state\n'
                     'd = doc.get("decision_requests")\nif r.get("answer"): pass\n')
        if violations(root):
            fails.append("control 2: a consumer that imports the owner still fires")

        # 3. NEGATIVE CONTROL — touches the requests and never asks about
        #    answeredness. Without this the guard would flag decision_subject.py.
        f.write_text('for req in doc.get("decision_requests") or []:\n    print(req["id"])\n')
        if violations(root):
            fails.append("control 3: a non-answeredness reader was flagged — false positive")

        # 4. NEGATIVE CONTROL — tests answeredness but never touches the
        #    requests. Out of scope; flagging it would make the guard noise.
        f.write_text('if row.get("verdict") == "approved": pass\n')
        if violations(root):
            fails.append("control 4: a file that never touches decision_requests was flagged")

        # 5. the OWNER itself is exempt, or the guard fails on its own definition
        f.unlink()
        if violations(root):
            fails.append("control 5: the owner module flagged itself")

        # 6. the FLAT shape counts as an inline test too, not just `answer`
        f.write_text('d = doc.get("decision_requests")\nif r.get("verdict"): pass\n')
        if not violations(root):
            fails.append("control 6: a flat-shape inline test did not fire")

        # 7. THE CONTROL THAT CAUGHT THE FIRST VERSION OF THIS GUARD. A file
        #    that merely NAMES the owner in a comment has not gone through it.
        #    Without this, `"work_decisions" in text` passes every control above
        #    and lets the real regression straight through — verified against the
        #    actual pre-fix blocked_lane_watch.py, which mentions the module in a
        #    comment explaining why it imports it.
        f.write_text('# see src/runtime/work_decisions.py for the owner\n'
                     'd = doc.get("decision_requests")\nif r.get("answer"): pass\n')
        if not violations(root):
            fails.append("control 7: a MENTION of the owner satisfied the guard — "
                         "presence-only marker, not an import")

    if fails:
        for x in fails:
            print(f"::error::self-test: {x}")
        return 1
    print("decision-answer-consumers: self-test OK — 7 planted controls all fire")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        return _self_test()
    try:
        found = violations()
    except OSError as exc:
        print(f"::error::decision-answer-consumers: could not measure ({exc})")
        return 2
    if found:
        for x in found:
            print(x)
        return 1
    print("decision-answer-consumers: OK — every `decision_requests` consumer that "
          "tests answeredness goes through src/runtime/work_decisions.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
