#!/usr/bin/env python3
"""An operator's WRITTEN answer must be READABLE by the grader.

WHY THIS EXISTS — two instances of one class in a single day, 2026-09-04.

``grade_answer_state()`` reads ``request["answer"]`` and nothing else, and
``normalise_answer()`` returns ``None`` unless that block carries a non-empty
``chosen`` or ``free_text``. Every other key in the block — however carefully
written, however much reasoning it carries — is invisible to it. So an answer
can be fully recorded in the repo and still grade ``not_submitted``, which means
the inbox keeps listing it and the sweep keeps re-prompting the operator with
the very options they already refused.

That is not hypothetical and it is not cosmetic:

* ``DEC-20260903-SUNSET-DISPOSITION-POLICY`` had its answer in a TOP-LEVEL
  ``answer:`` block on the object. The grader never looks there.
* ``DEC-20260902-LOCAL-LLM-WEIGHT`` had a rich in-request block with
  ``chosen: null`` and no ``free_text``. **This is why the operator was asked
  the same question repeatedly** — they answered on 09-02, and were re-asked on
  09-04 because nothing could see it.

⚠️ THIS IS A GUARD RATHER THAN A THIRD FIX ON PURPOSE. Both instances were
found by hand, by running the real normaliser over the files. A reminder to
"remember to fill in ``chosen``" is the non-mechanism this repo has already paid
for twice on MI-15; the write is invisible precisely at the moment somebody is
concentrating on the prose.

⚠️ WHAT IT DOES NOT CHECK. It cannot tell whether an answer is the RIGHT one,
nor whether the free text says what the operator meant. It checks exactly one
thing: that a block a human wrote as an answer will be READ as one.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
OBJECTS = REPO / "docs" / "claude" / "work" / "objects"

sys.path.insert(0, str(REPO))

from src.runtime.work_decisions import (  # noqa: E402
    grade_answer_state,
    normalise_answer,
    normalise_requests,
)


def _findings_for(obj_id: str, data: Any) -> list[str]:
    """Both failure shapes, from one parsed object."""
    out: list[str] = []
    if not isinstance(data, dict):
        return out

    raw_requests = data.get("decision_requests")
    if not isinstance(raw_requests, list):
        return out

    # R1 — an in-request `answer:` block the normaliser cannot read.
    for raw in raw_requests:
        if not isinstance(raw, dict):
            continue
        rid = raw.get("id") or "<unnamed request>"
        block = raw.get("answer")
        if block is None:
            continue  # genuinely unanswered — not this guard's business
        if not isinstance(block, dict):
            out.append(
                f"{obj_id} :: {rid} — `answer:` is {type(block).__name__}, not a mapping, "
                f"so normalise_answer() returns None and the request grades not_submitted."
            )
            continue
        if normalise_answer(block) is None:
            keys = ", ".join(sorted(str(k) for k in block)) or "(empty)"
            out.append(
                f"{obj_id} :: {rid} — an `answer:` block is written and the GRADER CANNOT SEE IT. "
                f"normalise_answer() needs a non-empty `chosen` or `free_text`; this block has: "
                f"{keys}. The operator will be re-prompted with options they may have already "
                f"refused. Add the two readable keys; keep the prose fields beside them."
            )

    # R2 — an answer filed at OBJECT level and NOWHERE the grader reads.
    # This is where DEC-20260903's answer lived.
    #
    # ⚠️ It fires ONLY when no request on the object carries a readable nested
    # answer. An object whose request 1 is answered-and-nested while request 2
    # is genuinely open ALSO carries a top-level block — the historical record of
    # request 1 — and that is CORRECT, not a misfiling. Measured: the pre-fix
    # SUNSET object was exactly that shape, and an untightened R2 called it a
    # finding. A guard that fires on the correct state is how a guard gets
    # ignored.
    any_readable_nested = any(
        isinstance(r, dict) and normalise_answer(r.get("answer")) is not None
        for r in raw_requests
    )
    if isinstance(data.get("answer"), dict) and not any_readable_nested:
        try:
            graded = normalise_requests({"decision_requests": raw_requests}, obj_id)
        except Exception as exc:  # a parse fault is a finding, never a pass
            out.append(f"{obj_id} — decision_requests could not be normalised: {exc}")
            graded = []
        unanswered = [
            r.get("id") or "<unnamed request>"
            for r in graded
            if grade_answer_state(r, None, "absent") == "not_submitted"
        ]
        if unanswered:
            out.append(
                f"{obj_id} — a TOP-LEVEL `answer:` block sits on the object while "
                f"{', '.join(unanswered)} still grades not_submitted. grade_answer_state() reads "
                f"request['answer'] and nothing else, so an answer recorded here is invisible: "
                f"nest it under the request it answers."
            )

    return out


def _self_test() -> int:
    """Exercise BOTH branches, so the teeth are known to work on a clean tree.

    Without this the guard is only ever observed passing, which is the state a
    guard is least useful in.
    """
    failures: list[str] = []

    unreadable = {
        "decision_requests": [
            {"id": "DEC-X", "question": "q", "answer": {"verdict": "refused", "chosen": None}}
        ]
    }
    if not _findings_for("SELFTEST-UNREADABLE", unreadable):
        failures.append("R1 did not fire on a `chosen: null` answer block")

    readable = {
        "decision_requests": [
            {"id": "DEC-X", "question": "q", "answer": {"chosen": "opt_a"}}
        ]
    }
    if _findings_for("SELFTEST-READABLE", readable):
        failures.append("R1 fired on a readable answer block")

    free_text_only = {
        "decision_requests": [
            {"id": "DEC-X", "question": "q", "answer": {"free_text": "none of these"}}
        ]
    }
    if _findings_for("SELFTEST-FREETEXT", free_text_only):
        failures.append("R1 fired on a free-text-only answer, which IS an answer")

    misfiled = {
        "answer": {"verdict": "none_of_the_above"},
        "decision_requests": [{"id": "DEC-X", "question": "q"}],
    }
    if not _findings_for("SELFTEST-MISFILED", misfiled):
        failures.append("R2 did not fire on an object-level answer over an unanswered request")

    both_ok = {
        "answer": {"verdict": "recorded"},
        "decision_requests": [
            {"id": "DEC-X", "question": "q", "answer": {"chosen": "opt_a"}}
        ],
    }
    if _findings_for("SELFTEST-BOTH-OK", both_ok):
        failures.append("R2 fired while every request was readably answered")

    unanswered = {"decision_requests": [{"id": "DEC-X", "question": "q"}]}
    if _findings_for("SELFTEST-UNANSWERED", unanswered):
        failures.append("fired on a genuinely unanswered request")

    # The false-positive shape R2 was tightened for: one request answered and
    # nested, a SECOND genuinely open, and a top-level block recording the first.
    historical = {
        "answer": {"verdict": "recorded for DEC-1"},
        "decision_requests": [
            {"id": "DEC-1", "question": "q1", "answer": {"chosen": "opt_a"}},
            {"id": "DEC-2", "question": "q2"},
        ],
    }
    if _findings_for("SELFTEST-HISTORICAL", historical):
        failures.append("R2 fired on an open request beside an answered, nested one")

    if failures:
        for f in failures:
            print(f"decision-answers SELF-TEST FAILED: {f}", file=sys.stderr)
        return 1
    print("decision-answers: self-test OK (7 cases)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    if not OBJECTS.is_dir():
        print(f"decision-answers: {OBJECTS} is not a directory", file=sys.stderr)
        return 1

    paths = sorted(OBJECTS.glob("*.yaml"))
    findings: list[str] = []
    unparsed = 0
    for path in paths:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            unparsed += 1
            findings.append(f"{path.name} — could not parse: {exc}")
            continue
        findings.extend(_findings_for(path.stem, data))

    # State the population, always — a clean result over zero files is not a pass.
    print(f"decision-answers: read {len(paths)} object file(s), {unparsed} unparseable")
    if not paths:
        print("decision-answers: NO object files found — refusing to report OK", file=sys.stderr)
        return 1

    if findings:
        print("", file=sys.stderr)
        print("decision-answers: FAIL — a written answer is unreadable to the grader", file=sys.stderr)
        for f in findings:
            print(f"  - {f}", file=sys.stderr)
        print("", file=sys.stderr)
        return 1

    print("decision-answers: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
