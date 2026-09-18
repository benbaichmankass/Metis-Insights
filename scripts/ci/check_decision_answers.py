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

────────────────────────────────────────────────────────────────────────────
R4, ADDED 2026-09-18 (MI-308) — THE SHAPE R1 RETURNS EARLY ON
────────────────────────────────────────────────────────────────────────────

R1 skips a request whose ``answer`` key is ``None``, commented *"genuinely
unanswered — not this guard's business"*. MEASURED over every object file
(population: **188 files, 0 parse failures, 33 gradeable requests**) that
comment was true of **4** of the 5 rows grading ``not_submitted`` and **false**
of the fifth.

``WO-20260912-DECIDE-THE-LOUD-FLAG-TRIAGE :: DEC-20260912-LOUD-FLAG-TRIAGE``
carried ``answer: null`` *and* ``answered_at: '2026-09-16'``, ``answered_by``,
``status: answered_with_a_redirect_not_an_option`` and the operator's verbatim
words — and the inbox told the operator **"Nobody has answered this through any
channel"** for four days.

**The two writers are not equally safe, and that is the finding.**
``commit_work_decisions.py::_write_answer`` and ``normalise_answer`` share one
shape helper (``render_answer_yaml_block``), so the AUTOMATED path cannot
drift — reader and writer provably agree. The HAND path, a manager recording an
answer given in conversation, has no owned shape and nothing validating it:
3 of its 4 instances used ``verdict``; **1 of 4 used keys nothing reads.**

⚠️ THE REMEDY IS A GUARD AND NOT A THIRD READER KEY. Teaching
``grade_answer_state`` to read ``status`` would put a second definition of
"answered" exactly where ``work_decisions``'s own docstring forbids one — and
``status`` measurably carries three unrelated vocabularies across its three
rows, so keying on it would fold a supersession and a hand-copied grade in with
an answer.
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
    NOT_SUBMITTED,
    grade_answer_state,
    normalise_answer,
    normalise_requests,
)

#: R4 — keys whose NAME asserts that an answer exists. Deliberately a tiny,
#: closed set of keys that cannot mean anything else.
#:
#: ⚠️ `status` IS NOT IN HERE, AND THAT IS MEASURED RATHER THAN CAUTIOUS. Across
#: the 3 requests that carry it, `status` holds THREE unrelated vocabularies —
#: `superseded_do_not_re_ask`, `not_submitted` (a hand-copied duplicate of a
#: DERIVED grade) and `answered_with_a_redirect_not_an_option`. Keying on it
#: would fold a supersession and a copied grade in with an answer. The one row
#: where it does assert an answer carries `answered_at` + `answered_by` too, so
#: R4 catches it without the fuzzy match — it buys nothing and costs precision.
#:
#: ⚠️ AND THIS IS EVIDENCE OF A CLAIM, NEVER THE ANSWER ITSELF. R3's docstring
#: records a first pass that keyed on `answered_at` AS the answer and reported
#: five answered requests as stubs. The grading here is still done entirely by
#: `grade_answer_state`; these keys only decide whether the file is CLAIMING
#: something the grader cannot see.
ANSWER_EVIDENCE_KEYS: tuple[str, ...] = ("answered_at", "answered_by")


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

    # R4 — the file SAYS it was answered and the grader reads `not_submitted`.
    #
    # WHY R1 CANNOT COVER THIS. R1 returns early on `if block is None: continue
    # — genuinely unanswered`. That comment was true of 4 of the 5 rows grading
    # `not_submitted` on 2026-09-18 and FALSE of the fifth, which is exactly
    # where a hand-recorded answer hides: `answer: null` *plus* `answered_at`,
    # `answered_by`, a `status` of `answered_with_a_redirect_not_an_option` and
    # the operator's verbatim words. R1 skipped it, and the inbox told the
    # operator "Nobody has answered this through any channel" for four days.
    #
    # THE TWO WRITERS ARE NOT EQUALLY SAFE, which is the whole finding.
    # `commit_work_decisions.py` and `normalise_answer` share ONE shape helper
    # (`render_answer_yaml_block`), so the automated path cannot drift. The
    # HAND path — a manager recording an answer given in conversation — has no
    # owned shape and nothing validating it. Measured over every object file:
    # 3 of its 4 instances used `verdict`, and 1 of 4 used keys nothing reads.
    #
    # ⚠️ IT FAILS RATHER THAN REPORTS, unlike R3, and that is safe on the
    # measurement rather than on optimism: R3 fired on 11 of 12 live edges, so
    # failing would have redded every PR in the repo — the way a guard gets
    # switched off instead of fixed. R4 fires on 1 of 33 before the data fix in
    # this same change and 0 of 33 after it, so it lands green.
    try:
        graded_r4 = normalise_requests({"decision_requests": raw_requests}, obj_id)
    except Exception as exc:  # a parse fault is a finding, never a pass
        out.append(f"{obj_id} — decision_requests could not be normalised for R4: {exc}")
        graded_r4 = []
    by_id = {r.get("id"): r for r in graded_r4}
    for raw in raw_requests:
        if not isinstance(raw, dict):
            continue
        rid = raw.get("id")
        req = by_id.get(rid.strip()) if isinstance(rid, str) else None
        if req is None:
            continue
        # Transit is deliberately `absent`: this guard reads the REPO, which is
        # where committedness lives. An open transit window is not a recorded
        # answer and must not silence this.
        if grade_answer_state(req, None, "absent") != NOT_SUBMITTED:
            continue
        claimed = [
            k for k in ANSWER_EVIDENCE_KEYS
            if str(raw.get(k) or "").strip()
        ]
        if not claimed:
            continue  # genuinely unanswered — R4 has nothing to say
        verdict_note = ""
        if "verdict" in raw:
            verdict_note = (
                f" It carries `verdict: {raw.get('verdict')!r}`, which "
                f"normalise_conversational_answer() could not read."
            )
        out.append(
            f"{obj_id} :: {rid} — the file CLAIMS an answer ({', '.join(claimed)}) and the "
            f"grader reads not_submitted, so the inbox tells the operator \"Nobody has answered "
            f"this through any channel\".{verdict_note} grade_answer_state() reads exactly two "
            f"shapes: a nested `answer:` mapping with a non-empty `chosen`/`free_text`, or a "
            f"top-level `verdict:` from work_decisions.TERMINAL_VERDICTS / NON_TERMINAL_VERDICTS. "
            f"Add whichever is TRUE — and note a non-terminal verdict "
            f"(e.g. `reframed_not_answered`) grades `engaged_not_settled`, which keeps the "
            f"question OPEN and on the operator's list rather than marking it decided."
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


#: `chosen` values that ANSWER the question without CHOOSING an option. The
#: distinction is not cosmetic and it decides the remedy: measured 2026-09-12,
#: all THREE actively-parked edges in the store name a request the operator
#: REJECTED as mis-framed, so the question may still be genuinely open under a
#: SUCCESSOR request. Told to "delete the spent edge", a reader would erase three
#: real blockers; told "re-point it", they would not.
NON_CHOICE_ANSWERS = frozenset({"none_of_the_above", "rejected", "malformed"})

#: Edge states, for a consumer that renders rather than prints. THREE, never
#: collapsed: `unresolvable` is *we could not look* and is neither of the others.
EDGE_SPENT = "spent"
EDGE_UNRESOLVABLE = "unresolvable"


def spent_edge_rows(objects: dict) -> list[dict]:
    """R3's finding as DATA — the one producer both renderings read.

    `_spent_edges` below formats these into the guard's own lines. A second
    consumer (the due list) reads the same rows rather than re-deriving them,
    because two definitions of "is this edge spent?" would be free to drift and
    the drift would be silent — the same argument this file already makes for
    reusing `normalise_answer` instead of keying on a date field.

    ⚠️ `lifecycle` RIDES THE ROW AND IS NOT FILTERED HERE. A spent edge on a
    `done` object is residue; one on a `waiting` object is work that is not
    happening. Which of those is worth a reader's attention is the consumer's
    call, and the guard deliberately reports both.
    """
    answered: dict[str, tuple[str, dict]] = {}
    declared: set[str] = set()
    for obj_id, data in objects.items():
        if not isinstance(data, dict):
            continue
        for raw in (data.get("decision_requests") or []):
            if not isinstance(raw, dict):
                continue
            rid = raw.get("id")
            if not rid:
                continue
            declared.add(rid)
            norm = normalise_answer(raw.get("answer"))
            if norm is not None:
                answered[rid] = (obj_id, norm)

    rows: list[dict] = []
    for obj_id, data in sorted(objects.items()):
        if not isinstance(data, dict):
            continue
        for edge in (data.get("blocked_on") or []):
            if not isinstance(edge, dict) or edge.get("kind") != "operator_decision":
                continue
            ref = edge.get("ref")
            lifecycle = data.get("lifecycle")
            if ref in answered:
                where, norm = answered[ref]
                chosen = norm.get("chosen")
                is_choice = bool(chosen) and str(chosen).strip().lower() \
                    not in NON_CHOICE_ANSWERS
                rows.append({
                    "state": EDGE_SPENT, "object": obj_id, "lifecycle": lifecycle,
                    "ref": ref, "answered_on_object": where,
                    "chosen": chosen, "answer_is_a_choice": is_choice,
                    "answered_at": norm.get("answeredAt"),
                    "since": edge.get("since"),
                })
            elif ref not in declared:
                rows.append({
                    "state": EDGE_UNRESOLVABLE, "object": obj_id,
                    "lifecycle": lifecycle, "ref": ref,
                    "answered_on_object": None, "chosen": None,
                    "answer_is_a_choice": None, "answered_at": None,
                    "since": edge.get("since"),
                })
    return rows


def _spent_edges(objects: dict) -> tuple[list[str], list[str]]:
    """R3 — a `blocked_on` edge naming a decision that has ALREADY been answered.

    ⚠️ REPORTED, NEVER FAILED, and the reason is measured rather than cautious:
    on 2026-09-12 ELEVEN of the twelve `operator_decision` edges in the store
    were already spent and ZERO were live. A rule that FAILED on this would red
    every PR in the repo the day it merged, which is how a guard gets switched
    off — the lesson `workflow-push-target-guard` records beside its own
    `conditional_default` rows.

    WHY IT BELONGS HERE rather than in a new file. This guard already owns the
    question *"is a written answer readable?"*; R3 is the next question in the
    same chain — *"and did anything ACT on it?"*. Answering and unblocking are
    two separate writes, and on that measurement only the first had any
    machinery: the answer lands in the object's YAML and the edge it releases is
    left standing. `CLAUDE.md`: *a false blocker is worse than a missing one*,
    and a SPENT edge is that defect arrived at by decay instead of invention.

    ⚠️ "ANSWERED" IS `normalise_answer`, NOT A DATE FIELD. A first pass at this
    keyed on `answered_at` and reported five genuinely-answered requests as
    unanswered stubs, because the schema also uses `answered_on`. Reusing the
    canonical normaliser — the same one R1 and R2 use — makes the question
    *"would the grader read this as an answer?"*, which is the only definition
    that can agree with the rest of the system.

    Returns (spent, unresolvable) — two lists, never pooled: a spent edge names
    a request that exists and is answered; an unresolvable one names something
    that is not a request id at all, which is a different defect with a
    different fix.
    """
    spent: list[str] = []
    unresolvable: list[str] = []
    for r in spent_edge_rows(objects):
        if r["state"] == EDGE_SPENT:
            note = ("" if r["answer_is_a_choice"] else
                    " THE ANSWER CHOSE NO OPTION (rejected / mis-framed), so the question may "
                    "still be open under a SUCCESSOR request -- RE-POINT the edge, do not "
                    "delete it.")
            spent.append(
                f"{r['object']} (lifecycle: {r['lifecycle']}) is blocked_on {r['ref']}, which "
                f"carries a READABLE answer on {r['answered_on_object']}. The decision was "
                f"taken; the edge was not released.{note}"
            )
        else:
            unresolvable.append(
                f"{r['object']} (lifecycle: {r['lifecycle']}) is blocked_on a ref that is not "
                f"any declared request id: {r['ref']!r}. A typed edge whose ref is prose is "
                f"untyped -- nothing matching on ids can ever discharge it."
            )
    return spent, unresolvable


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

    # R4 — planted BOTH ways. The failing direction is the live instance this
    # change fixes; the passing directions are the four shapes that must NOT
    # fire, because a guard that cries on a correct row gets switched off.
    claimed_unreadable = {
        "decision_requests": [
            {"id": "DEC-X", "question": "q", "answer": None,
             "answered_at": "2026-09-16", "answered_by": "operator",
             "status": "answered_with_a_redirect_not_an_option"}
        ]
    }
    if not any("CLAIMS an answer" in f for f in _findings_for("SELFTEST-R4-CLAIMED", claimed_unreadable)):
        failures.append("R4 did not fire on `answer: null` beside answered_at/answered_by — "
                        "the exact live shape it exists for")

    claimed_and_committed = {
        "decision_requests": [
            {"id": "DEC-X", "question": "q", "answered_at": "2026-09-16",
             "answer": {"chosen": "opt_a"}}
        ]
    }
    if any("CLAIMS an answer" in f for f in _findings_for("SELFTEST-R4-COMMITTED", claimed_and_committed)):
        failures.append("R4 fired on a request whose nested answer IS readable")

    claimed_and_verdict = {
        "decision_requests": [
            {"id": "DEC-X", "question": "q", "answered_at": "2026-09-16",
             "verdict": "approved", "chosen": "opt_a"}
        ]
    }
    if any("CLAIMS an answer" in f for f in _findings_for("SELFTEST-R4-VERDICT", claimed_and_verdict)):
        failures.append("R4 fired on a terminal verdict the grader reads fine")

    # ⚠️ THE NON-TERMINAL CASE, AND IT IS THE ONE MOST AT RISK OF BEING GOT
    # WRONG. `reframed_not_answered` grades `engaged_not_settled` — the operator
    # ENGAGED and did NOT settle. R4 must stay quiet: the grader can see it, and
    # the question is correctly still open. Firing here would push an author
    # toward recording a TERMINAL verdict to silence a guard, which is the
    # forward failure the transit contract exists to refuse.
    claimed_nonterminal = {
        "decision_requests": [
            {"id": "DEC-X", "question": "q", "answered_at": "2026-09-16",
             "verdict": "reframed_not_answered"}
        ]
    }
    if any("CLAIMS an answer" in f for f in _findings_for("SELFTEST-R4-NONTERMINAL", claimed_nonterminal)):
        failures.append("R4 fired on a NON-TERMINAL verdict, which the grader reads as "
                        "engaged_not_settled — it would push authors toward a terminal one")

    # An unrecognised verdict grades `verdict_unrecognised`, which is the grader
    # REFUSING loudly rather than failing to look. R4 is about invisibility.
    claimed_unrecognised = {
        "decision_requests": [
            {"id": "DEC-X", "question": "q", "answered_at": "2026-09-16",
             "verdict": "mumble"}
        ]
    }
    if any("CLAIMS an answer" in f for f in _findings_for("SELFTEST-R4-UNRECOGNISED", claimed_unrecognised)):
        failures.append("R4 fired on an unrecognised verdict, which is already graded loudly")

    # No claim at all — the ordinary open question. Four of the five live
    # `not_submitted` rows are this, and R4 must be silent on every one.
    no_claim = {"decision_requests": [{"id": "DEC-X", "question": "q", "asked_on": "2026-09-12"}]}
    if any("CLAIMS an answer" in f for f in _findings_for("SELFTEST-R4-OPEN", no_claim)):
        failures.append("R4 fired on a genuinely open request — `asked_on` is not `answered_at`")

    # An EMPTY claim is not a claim. A stub `answered_at: ""` left by a template
    # must not red the tree.
    empty_claim = {
        "decision_requests": [{"id": "DEC-X", "question": "q", "answered_at": "", "answered_by": None}]
    }
    if any("CLAIMS an answer" in f for f in _findings_for("SELFTEST-R4-EMPTY", empty_claim)):
        failures.append("R4 fired on empty answered_at/answered_by stubs")

    # R3 — the spent-edge report. Exercised here because on a clean tree it is
    # only ever observed printing a count, and a count nobody plants a control
    # for is a count nobody can trust.
    spent, unresolvable = _spent_edges({
        "OBJ-SPENT": {
            "lifecycle": "waiting",
            "blocked_on": [{"kind": "operator_decision", "ref": "DEC-ANSWERED"}],
            "decision_requests": [{"id": "DEC-ANSWERED", "answer": {"chosen": "opt_a"}}],
        },
        "OBJ-LIVE": {
            "lifecycle": "waiting",
            "blocked_on": [{"kind": "operator_decision", "ref": "DEC-OPEN"}],
            "decision_requests": [{"id": "DEC-OPEN", "question": "q"}],
        },
        "OBJ-PROSE": {
            "lifecycle": "waiting",
            "blocked_on": [{"kind": "operator_decision", "ref": "go and do the thing"}],
        },
        "OBJ-OTHERKIND": {
            "lifecycle": "waiting",
            "blocked_on": [{"kind": "capability", "ref": "DEC-ANSWERED"}],
        },
    })
    if len(spent) != 1:
        failures.append(f"R3 should report exactly one spent edge, got {len(spent)}")
    if len(unresolvable) != 1:
        failures.append(f"R3 should report exactly one unresolvable ref, got {len(unresolvable)}")
    if any("DEC-OPEN" in s for s in spent):
        failures.append("R3 reported a genuinely unanswered decision as spent")
    if any("OBJ-OTHERKIND" in s for s in spent + unresolvable):
        failures.append("R3 graded a non-operator_decision edge")

    # An answer the GRADER CANNOT READ must not count as answered — otherwise R3
    # would call an edge spent on the strength of the very block R1 exists to
    # reject, and the two rules would contradict each other.
    spent2, _ = _spent_edges({
        "OBJ-UNREADABLE": {
            "blocked_on": [{"kind": "operator_decision", "ref": "DEC-U"}],
            "decision_requests": [{"id": "DEC-U", "answer": {"verdict": "refused", "chosen": None}}],
        },
    })
    if spent2:
        failures.append("R3 called an edge spent on an answer R1 grades unreadable")

    # `answer_is_a_choice` — BOTH WAYS, because it decides the REMEDY and a
    # constant would be invisible in the prose. Measured 2026-09-12, every
    # actively-parked edge in the live store names a request the operator
    # REJECTED, so a reader told to "delete the spent edge" would erase a real
    # blocker while one told to RE-POINT it would not.
    rows = {r["object"]: r for r in spent_edge_rows({
        "OBJ-CHOSE": {
            "lifecycle": "waiting",
            "blocked_on": [{"kind": "operator_decision", "ref": "DEC-C"}],
            "decision_requests": [{"id": "DEC-C", "answer": {"chosen": "opt_a"}}],
        },
        "OBJ-REJECTED": {
            "lifecycle": "waiting",
            "blocked_on": [{"kind": "operator_decision", "ref": "DEC-R"}],
            "decision_requests": [{"id": "DEC-R", "answer": {
                "chosen": None, "free_text": "REJECTED AS MALFORMED"}}],
        },
        "OBJ-NONEOF": {
            "lifecycle": "ready",
            "blocked_on": [{"kind": "operator_decision", "ref": "DEC-N"}],
            "decision_requests": [{"id": "DEC-N", "answer": {
                "chosen": "none_of_the_above", "free_text": "all four were wrong"}}],
        },
    })}
    if rows.get("OBJ-CHOSE", {}).get("answer_is_a_choice") is not True:
        failures.append("a chosen option did not grade as a choice")
    if rows.get("OBJ-REJECTED", {}).get("answer_is_a_choice") is not False:
        failures.append("a free-text-only rejection graded as a CHOICE — a reader "
                        "would be told to delete an edge that needs re-pointing")
    if rows.get("OBJ-NONEOF", {}).get("answer_is_a_choice") is not False:
        failures.append("`none_of_the_above` graded as a choice")
    # `lifecycle` must RIDE the row — a consumer cannot separate residue from
    # work-not-happening without it, and dropping it is invisible in the lines.
    if rows.get("OBJ-NONEOF", {}).get("lifecycle") != "ready":
        failures.append("lifecycle did not ride the row")

    # THE LOADER REPORTS AN UNPARSEABLE FILE RATHER THAN SKIPPING IT. A loader
    # that drops one understates every count downstream, in the reassuring
    # direction nobody re-checks.
    import tempfile as _tf
    with _tf.TemporaryDirectory() as _td:
        _root = Path(_td)
        (_root / "good.yaml").write_text("id: G\nlifecycle: ready\n", encoding="utf-8")
        (_root / "bad.yaml").write_text("\tnot: [yaml", encoding="utf-8")
        _objs, _bad = load_objects(_root)
        if len(_bad) != 1 or "bad.yaml" not in _bad[0]:
            failures.append(f"load_objects did not report the unparseable file: {_bad}")
        if "good" not in _objs:
            failures.append("load_objects dropped a parseable file")

    if failures:
        for f in failures:
            print(f"decision-answers SELF-TEST FAILED: {f}", file=sys.stderr)
        return 1
    print("decision-answers: self-test OK (25 cases)")
    return 0


def load_objects(root: Path = OBJECTS) -> tuple[dict, list[str]]:
    """(objects, unparseable_names). Factored out of `main` so a second consumer
    reads the store through the same loader rather than writing its own.

    ⚠️ UNPARSEABLE FILES ARE RETURNED, NEVER SKIPPED. A loader that quietly drops
    a file understates every count downstream, and the sweep that motivated R3
    stated its zero as a positive control for exactly that reason.
    """
    objects: dict = {}
    unparsed: list[str] = []
    for path in sorted(root.glob("*.yaml")):
        try:
            objects[path.stem] = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            unparsed.append(f"{path.name} — could not parse: {exc}")
    return objects, unparsed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    if not OBJECTS.is_dir():
        print(f"decision-answers: {OBJECTS} is not a directory", file=sys.stderr)
        return 1

    objects, unparsed_names = load_objects()
    paths = sorted(OBJECTS.glob("*.yaml"))
    findings: list[str] = list(unparsed_names)
    unparsed = len(unparsed_names)
    for obj_id, data in objects.items():
        findings.extend(_findings_for(obj_id, data))

    # R3 is REPORTED, never failed — see _spent_edges. It is printed BEFORE the
    # pass/fail verdict so it is visible on a green run, which is the only run
    # it will ever appear on until somebody releases the edges.
    spent, unresolvable = _spent_edges(objects)
    print(
        f"decision-answers: {len(spent)} spent operator_decision edge(s), "
        f"{len(unresolvable)} unresolvable ref(s) — REPORTED, not failed"
    )
    for line in spent:
        print(f"  ~ SPENT        {line}")
    for line in unresolvable:
        print(f"  ~ UNRESOLVABLE {line}")

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
