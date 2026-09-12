#!/usr/bin/env python3
"""Which `blocked_on` edges name a decision that has ALREADY been answered?

`docs/claude/work/` makes `blocked_on` a TYPED edge so the constraint can be
COMPUTED rather than judged. CLAUDE.md states the hazard in terms: *"an invented
edge is read by the constraint computation as a real blocker, and a false
blocker is worse than a missing one."*

A **SPENT** edge is that same defect arrived at by decay instead of invention,
and it is worse in one way: an invented edge was never true, whereas a spent one
WAS true and therefore carries a plausible basis paragraph that still reads as
current. The mechanism is simple and was measured rather than guessed
(`BL-20260912-A-WORK-OBJECT-IS-PARKED-WAITING-ON-AN-OPERATOR-DECISION-THAT-WAS-ANSWERED-THREE-DAYS-EARLIER-AND-NOTHING-CLEARS-A-SPENT-BLOCKED-ON-EDGE`):
**answering and unblocking are two separate writes and only the first has any
machinery.** Measured 2026-09-12 over all 179 objects: of 12 `operator_decision`
edges, ELEVEN were spent, ZERO were live, and one named prose instead of an id.

⚠️ **THIS REPORTS AND CHANGES NOTHING.** An object's `lifecycle` and its edges
are its owner's or the manager's call, and flipping `waiting` to `ready` changes
what the WIP ceiling and the constraint readout compute over. That is the row's
own stated boundary and it is the whole reason this is a reporter: clause (b) of
its resolution criteria asks that something *"DERIVES the edge's liveness from
the answer … **or a check reports the disagreement**"*.

⚠️ **AN ANSWER IS NOT ALWAYS A CHOICE, and collapsing the two would send a
reader to delete an edge that should be RE-POINTED.** `DEC-20260909-STRATEGY-REVIEW-WINDOW-FLOOR`
carries `chosen: null` with a free_text reading *"REJECTED AS MALFORMED BY THE
OPERATOR"*, and its context names the request that SUPERSEDES it. The question
was disposed of, so the edge is spent — but the object may still be genuinely
blocked, on the successor. Hence `answered_no_choice` beside `answered_chosen`.

⚠️ **GRADE ON BOTH `answered_at` AND `answered_on`.** The schema uses both (14
and 11 respectively across 26 answer blocks, measured 2026-09-12), and the first
pass of the sweep that motivated this module printed `answered_at: None` for five
substantive operator answers and nearly reported them as empty stubs. An
`answered_at`-only probe UNDERSTATES the spent set — i.e. errs toward "nothing to
see", the direction nobody re-checks.

Run:
    python3 scripts/ops/spent_decision_edges.py            # report
    python3 scripts/ops/spent_decision_edges.py --json
    python3 scripts/ops/spent_decision_edges.py --self-test
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
OBJECTS = REPO / "docs" / "claude" / "work" / "objects"

#: How an edge's referent resolves. FOUR states, never collapsed — the two
#: `unresolvable` ones are *we could not look*, and folding either into LIVE
#: would manufacture a blocker while folding it into SPENT would erase one.
EDGE_SPENT = "spent"
EDGE_LIVE = "live"
EDGE_REQUEST_NOT_FOUND = "request_not_found"
EDGE_REF_MALFORMED = "ref_malformed"
EDGE_STATES = (EDGE_SPENT, EDGE_LIVE, EDGE_REQUEST_NOT_FOUND, EDGE_REF_MALFORMED)

#: How a request was disposed of. `answered_no_choice` is NOT a lesser
#: `answered_chosen`: a rejected or superseded question releases the edge and may
#: require RE-POINTING it at the successor rather than deleting it.
ANSWER_CHOSEN = "answered_chosen"
ANSWER_NO_CHOICE = "answered_no_choice"
ANSWER_NONE = "unanswered"
ANSWER_STATES = (ANSWER_CHOSEN, ANSWER_NO_CHOICE, ANSWER_NONE)

#: The edge kinds this module grades. Deliberately narrow: a `pr` or `soak` edge
#: is discharged by something this module cannot read, and reporting it as
#: unresolvable would be a confident wrong answer about a healthy edge.
GRADED_KINDS = frozenset({"operator_decision"})

#: A decision-request id. Anything else in `ref` is prose, which no consumer can
#: resolve — a typed edge whose ref is prose is untyped.
_REF_RE = re.compile(r"^[A-Z]{2,}-\d{8}-[A-Z0-9-]+$")

#: Keys that mean "this request was disposed of", inside the `answer` block or
#: beside it (the store carries both shapes: 26 nested, 3 flat).
_ANSWER_KEYS = ("chosen", "free_text", "answered_by", "answered_at",
                "answered_on", "verdict", "committed_by")

#: `chosen` values that are an answer WITHOUT being a choice.
_NON_CHOICES = frozenset({"none_of_the_above", "rejected", "malformed", ""})


def answer_state(request: dict[str, Any]) -> str:
    """Was this request disposed of, and was the disposal a CHOICE?

    A pure function, so the policy is arguable in tests rather than against the
    live store.
    """
    if not isinstance(request, dict):
        return ANSWER_NONE
    block = request.get("answer")
    block = block if isinstance(block, dict) else {}
    # BOTH shapes and BOTH date keys — see the module docstring on why an
    # `answered_at`-only probe understates the spent set.
    present = any(
        (block.get(k) is not None) or (request.get(k) is not None)
        for k in _ANSWER_KEYS
    )
    if not present:
        return ANSWER_NONE
    chosen = block.get("chosen", request.get("chosen"))
    if chosen is None or (isinstance(chosen, str)
                          and chosen.strip().lower() in _NON_CHOICES):
        return ANSWER_NO_CHOICE
    return ANSWER_CHOSEN


def answered_when(request: dict[str, Any]) -> str | None:
    """The disposal timestamp, from EITHER key, or None when none is recorded.

    `None` is *not recorded*, never *not answered* — `answer_state` owns that
    question and a caller must not infer one from the other.
    """
    block = request.get("answer")
    block = block if isinstance(block, dict) else {}
    for k in ("answered_at", "answered_on"):
        for src in (block, request):
            v = src.get(k)
            if v:
                return str(v)
    return None


def edge_state(ref: Any, requests: dict[str, dict[str, Any]]) -> str:
    """Grade one `blocked_on` ref against the store's request index."""
    if not isinstance(ref, str) or not _REF_RE.match(ref.strip()):
        return EDGE_REF_MALFORMED
    req = requests.get(ref.strip())
    if req is None:
        return EDGE_REQUEST_NOT_FOUND
    return EDGE_LIVE if answer_state(req) == ANSWER_NONE else EDGE_SPENT


def _load_objects(root: Path) -> tuple[list[tuple[str, dict]], list[str]]:
    """(parsed, unparseable). Unparseable files are RETURNED, never skipped —
    a parser quietly dropping a file understates every count downstream, which
    is why the sweep that motivated this module stated its 0 as a control."""
    import yaml

    parsed: list[tuple[str, dict]] = []
    bad: list[str] = []
    for p in sorted(root.glob("*.yaml")):
        try:
            doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            bad.append(p.name)
            continue
        if not isinstance(doc, dict):
            bad.append(p.name)
            continue
        parsed.append((p.name, doc))
    return parsed, bad


def sweep(root: Path = OBJECTS) -> dict[str, Any]:
    """Every graded `blocked_on` edge in the store, with its state."""
    parsed, unparseable = _load_objects(root)
    requests: dict[str, dict[str, Any]] = {}
    for _name, doc in parsed:
        for r in (doc.get("decision_requests") or []):
            if isinstance(r, dict) and isinstance(r.get("id"), str):
                requests[r["id"].strip()] = r

    rows: list[dict[str, Any]] = []
    for name, doc in parsed:
        for e in (doc.get("blocked_on") or []):
            if not isinstance(e, dict) or e.get("kind") not in GRADED_KINDS:
                continue
            ref = e.get("ref")
            st = edge_state(ref, requests)
            req = requests.get(str(ref).strip()) if isinstance(ref, str) else None
            rows.append({
                "object": doc.get("id") or name,
                "file": name,
                "lifecycle": doc.get("lifecycle"),
                "ref": ref,
                "state": st,
                "answer_state": answer_state(req) if req else None,
                "answered": answered_when(req) if req else None,
                "since": e.get("since"),
            })

    counts = {s: sum(1 for r in rows if r["state"] == s) for s in EDGE_STATES}
    parked = [r for r in rows
              if r["state"] == EDGE_SPENT and r["lifecycle"] == "waiting"]
    # ⚠️ A SEPARATE LIST, NOT A WIDENING OF THE ONE ABOVE, because the two have
    # OPPOSITE remedies. A spent edge is discharged by re-grading or re-pointing
    # it; an UNRESOLVABLE one can never be discharged by anything that matches on
    # ids, so the object is parked forever and no consumer can even say on what.
    # Pooling them would let a reader apply "the decision was already answered"
    # to an edge whose referent nobody has ever found.
    stranded = [r for r in rows
                if r["state"] in (EDGE_REQUEST_NOT_FOUND, EDGE_REF_MALFORMED)
                and r["lifecycle"] == "waiting"]
    return {
        "population": {
            "objects_parsed": len(parsed),
            "objects_unparseable": len(unparseable),
            "unparseable_files": unparseable,
            "decision_requests_indexed": len(requests),
            "graded_edges": len(rows),
        },
        "counts": counts,
        # The subset that is ACTIVELY parked: a spent edge on a `done` object is
        # residue, a spent edge on a `waiting` one is work that is not happening.
        "parked_on_a_spent_edge": parked,
        "parked_on_an_unresolvable_edge": stranded,
        "rows": rows,
    }


def render(s: dict[str, Any]) -> str:
    pop, c = s["population"], s["counts"]
    out = [
        "spent decision edges — `blocked_on` refs naming an ALREADY-ANSWERED decision",
        "=" * 74,
        f"population: {pop['objects_parsed']} object(s) parsed, "
        f"{pop['objects_unparseable']} UNPARSEABLE, "
        f"{pop['decision_requests_indexed']} decision request(s) indexed, "
        f"{pop['graded_edges']} graded edge(s) "
        f"(kinds: {', '.join(sorted(GRADED_KINDS))})",
        "",
        f"  {EDGE_SPENT:<20} {c[EDGE_SPENT]:>4}   answered already — the edge is not a blocker",
        f"  {EDGE_LIVE:<20} {c[EDGE_LIVE]:>4}   genuinely unanswered",
        f"  {EDGE_REQUEST_NOT_FOUND:<20} {c[EDGE_REQUEST_NOT_FOUND]:>4}   ref is id-shaped but names no request — WE COULD NOT LOOK, not 'live'",
        f"  {EDGE_REF_MALFORMED:<20} {c[EDGE_REF_MALFORMED]:>4}   ref is prose, not an id — a typed edge whose ref is prose is untyped",
        "",
    ]
    if pop["objects_unparseable"]:
        out += [f"  !! {pop['objects_unparseable']} object(s) did not parse and are "
                f"counted, never skipped: {', '.join(pop['unparseable_files'])}",
                "     Every count above is over the parsed set only.", ""]
    parked = s["parked_on_a_spent_edge"]
    if parked:
        out.append(f"  ACTIVELY PARKED — {len(parked)} object(s) are `waiting` on a "
                   f"decision already taken:")
        for r in sorted(parked, key=lambda x: str(x["object"])):
            note = ("answered, NO CHOICE (rejected/superseded) — RE-POINT it, do not "
                    "just delete the edge"
                    if r["answer_state"] == ANSWER_NO_CHOICE else "answered")
            out.append(f"    {str(r['object']):<48} {str(r['ref'])}")
            out.append(f"        {note} {r['answered'] or '(date not recorded)'}"
                       f"   edge since {r['since']}")
        out += ["",
                "  !! REPORTED, NOT ACTED ON. An object's lifecycle and its edges are",
                "     its owner's or the manager's call — re-grading `waiting` changes",
                "     what the WIP ceiling and the constraint readout compute over.", ""]
    else:
        out += ["  no object is `waiting` on a spent edge "
                "(a real reading over a real population, not an empty one)", ""]
    stranded = s["parked_on_an_unresolvable_edge"]
    if stranded:
        out.append(f"  STRANDED — {len(stranded)} object(s) are `waiting` on an "
                   f"edge whose referent CANNOT BE RESOLVED:")
        for r in sorted(stranded, key=lambda x: str(x["object"])):
            out.append(f"    {str(r['object']):<48} [{r['state']}]")
            out.append(f"        ref: {str(r['ref'])[:96]!r}")
        out += ["",
                "  !! NOT THE SAME AS SPENT AND THE REMEDY IS THE OPPOSITE. A spent",
                "     edge is discharged by re-grading or re-pointing it; an",
                "     unresolvable one can never be discharged by anything that",
                "     matches on ids, so the object is parked with no way out and no",
                "     consumer can even say what it is waiting for.", ""]
    return "\n".join(out)


def _self_test() -> int:
    """The failure paths, because a reporter that cannot go red reports nothing."""
    reqs = {
        "DEC-20260101-CHOSE": {"id": "DEC-20260101-CHOSE",
                               "answer": {"chosen": "a", "answered_at": "2026-01-01"}},
        "DEC-20260101-ONLYON": {"id": "DEC-20260101-ONLYON",
                                "answer": {"chosen": "b", "answered_on": "2026-01-02"}},
        "DEC-20260101-REJECTED": {"id": "DEC-20260101-REJECTED",
                                  "answer": {"chosen": None,
                                             "free_text": "REJECTED AS MALFORMED"}},
        "DEC-20260101-NONEOF": {"id": "DEC-20260101-NONEOF",
                                "answer": {"chosen": "none_of_the_above",
                                           "answered_by": "operator"}},
        "DEC-20260101-FLAT": {"id": "DEC-20260101-FLAT", "chosen": "c",
                              "answered_at": "2026-01-03"},
        "DEC-20260101-OPEN": {"id": "DEC-20260101-OPEN", "question": "?"},
    }
    cases = [
        ("chosen", "DEC-20260101-CHOSE", ANSWER_CHOSEN, EDGE_SPENT),
        # THE UNDERSTATEMENT THIS MODULE EXISTS TO REFUSE: an `answered_at`-only
        # probe grades this one LIVE and the whole sweep reads clean.
        ("answered_on only", "DEC-20260101-ONLYON", ANSWER_CHOSEN, EDGE_SPENT),
        ("rejected", "DEC-20260101-REJECTED", ANSWER_NO_CHOICE, EDGE_SPENT),
        ("none_of_the_above", "DEC-20260101-NONEOF", ANSWER_NO_CHOICE, EDGE_SPENT),
        ("flat schema", "DEC-20260101-FLAT", ANSWER_CHOSEN, EDGE_SPENT),
        ("unanswered", "DEC-20260101-OPEN", ANSWER_NONE, EDGE_LIVE),
    ]
    fails = []
    for label, ref, want_a, want_e in cases:
        got_a, got_e = answer_state(reqs[ref]), edge_state(ref, reqs)
        if (got_a, got_e) != (want_a, want_e):
            fails.append(f"{label}: got {got_a}/{got_e}, want {want_a}/{want_e}")
        print(f"  self-test {label}: {'PASS' if (got_a, got_e) == (want_a, want_e) else 'FAIL'}")
    for label, ref, want in [
        ("prose ref", "originate JWT_SIGNING_KEY, then a Tier-2 set-env",
         EDGE_REF_MALFORMED),
        ("unknown id", "DEC-20990101-NOPE", EDGE_REQUEST_NOT_FOUND),
        ("non-string ref", None, EDGE_REF_MALFORMED),
    ]:
        got = edge_state(ref, reqs)
        if got != want:
            fails.append(f"{label}: got {got}, want {want}")
        print(f"  self-test {label}: {'PASS' if got == want else 'FAIL'}")
    # `answered_when` must say "not recorded" rather than inventing a date.
    if answered_when(reqs["DEC-20260101-REJECTED"]) is not None:
        fails.append("answered_when invented a date for an undated answer")
    if answered_when(reqs["DEC-20260101-ONLYON"]) != "2026-01-02":
        fails.append("answered_when missed `answered_on`")
    print(f"  self-test answered_when: {'PASS' if not fails else 'see failures'}")
    if fails:
        print("spent-decision-edges self-test: FAIL")
        for f in fails:
            print("   " + f)
        return 1
    print("spent-decision-edges self-test: PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 when any object is `waiting` on a spent edge")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    s = sweep()
    if a.json:
        print(json.dumps(s, indent=2, ensure_ascii=False, default=str))
    else:
        print(render(s))
    return 1 if (a.strict and s["parked_on_a_spent_edge"]) else 0


if __name__ == "__main__":
    sys.exit(main())
