#!/usr/bin/env python3
"""THE OPEN-PR HANDOFF RECORD — is `docs/claude/work/OPEN-PRS.json` complete,
current, and does it carry the CONDITIONS attached to its approvals?

This is the second half of the manager handoff. `session_registry.py` answers
*"which sub-sessions would a successor lose?"*; this answers *"which PRs, and
what did the operator actually say about them?"*

⚠️ THE DANGEROUS CASE IS NOT A FORGOTTEN PR — IT IS A FORGOTTEN CONDITION
------------------------------------------------------------------------
`#10746` carries a Tier-2 approval that is **conditional**: stage on `bybit_1`
(demo) ONLY, explicitly not a fleet-wide flip, with the operator having accepted
that real-money `bybit_2` stays exposed during the soak.

  * a successor that knows **nothing** about that approval stalls and re-asks —
    wasteful, and safe.
  * a successor that knows **"approved"** but not the **condition** could merge
    it fleet-wide onto a real-money account.

**Only the half-informed case is dangerous**, and an unstructured record
produces exactly that. So "every open PR has a row" is too weak a check: a row
recording a verdict without its condition is WORSE than a missing row, because
it reads as complete.

⚠️ WHAT CANNOT BE DETECTED FROM INSIDE THE REPO, STATED PLAINLY
---------------------------------------------------------------
The brief asked for a check that refuses when a row "records a verdict with no
condition where one was given." **The `where one was given` half is not
mechanically detectable and this module does not pretend otherwise.** Knowing
that the operator attached a condition requires knowing what the operator said,
which lives outside this file — the file IS the record. A checker reading the
old free-text `operator_decision` could only match English for "approved" and
"only"/"not fleet-wide", which is UNPROVENANCED DIAGNOSTIC OUTPUT sub-class A
(the repo's own reason for deferring C4: *"the changelog's execution verdict
exists only as PROSE, so a guard matching English for it is diagnostic-
provenance sub-class A"*).

**What IS detectable, and is what this enforces:** make the author state whether
conditions existed, in a FIELD rather than in prose, and then enforce that a
declared condition is actually recorded. `operator_decision.verdict` is a closed
vocabulary; `approved_with_conditions` carrying no `condition` and no `scope` is
a mechanical contradiction and fails.

⚠️ **THE RESIDUAL, NAMED RATHER THAN HIDDEN:** an author who writes
`verdict: approved` when the operator actually said *approved with conditions*
defeats this, and nothing inside the repo can catch it. The check moves the
failure from *"a condition silently absent from prose nobody parses"* to *"a
verdict field a reader can compare against `text`"* — which is why every typed
decision **must** keep the operator's original wording verbatim in `text`. That
is a narrowing, not a closure.

⚠️ THIS FILE IS NOT A SECOND COPY OF GITHUB
--------------------------------------------
It records OWNERSHIP, INTENT and DECISIONS — things GitHub does not carry. It is
**not** authoritative for CI or mergeability, and nothing here re-derives them.
Completeness is graded by COMPARING the record against a live observation of
what is open; the observation is never stored.

⚠️ AND THE OBSERVATION CANNOT BE MADE FROM THIS CONTAINER ON A ROUTINE-WOKEN
TURN — `mcp__github__*` is not available there, and `curl https://api.github.com`
returns 403 at the sandbox proxy (CLAUDE.md § PM-side session capabilities). So
the live list must come from somewhere credentials exist — an interactive
session's `list_pull_requests`, or a workflow — and is passed in with
`--open-prs`. Without it the verdict is `not_observed`, **never** `recorded`.

⚠️ TWO POPULATIONS, ONE OF WHICH IS NEVER GRADED AGAINST THE LIVE LIST
----------------------------------------------------------------------
As of schema_version 3 the record holds `open_prs[]` AND `settled_prs[]`, and
**only `open_prs[]` is compared to what is open.** They were one key until
MI-57, and conflating them is what made the record destroy the thing it exists
to hold:

  * `open_prs[]`   an IN-FLIGHT CLAIM — "this PR is open, here is its owner and
                   its blocker". Legitimately graded against a live observation,
                   and legitimately stale the moment the PR merges.
  * `settled_prs[]` the DURABLE DECISION RECORD — "here is what the operator
                   said about #X, verbatim". This is HISTORY. It is *more*
                   load-bearing after the merge than before, and comparing it to
                   the open list would mark every row of it stale by
                   construction.

Under the old shape, satisfying the freshness rule meant DELETING a settled
row — and #10746's row carries a Tier-2 approval conditional on `bybit_1`
(demo) ONLY, with real-money `bybit_2` explicitly accepted as exposed. Pruning
is now a MOVE. Nothing in this module deletes a row, and `settled_prs[]` is
never passed to the completeness comparison.

STATES, NEVER COLLAPSED
-----------------------
`completeness` (over `open_prs[]` ALONE):
  ``recorded``      every open PR has a row and every row is still open.
  ``unrecorded``    an OPEN PR has no row. A successor cannot see it.
  ``stale_row``     a row names a PR that is no longer open, AND the reconciler
                    has run since the last merge — so this is a row a SESSION
                    left behind, not a dead automation.
  ``reconciler_not_run``
                    a row names a PR that is no longer open, and
                    `last_reconciled_sha` does not match the current `main`.
                    ⚠️ A DIFFERENT FINDING, deliberately not folded into
                    `stale_row`. A merged, enabled, syntactically correct
                    workflow in this repo is NOT evidence that it fires:
                    `due-list.yml` has no scheduled run, and two Claude Routines
                    sit `enabled: true` with `next_run_at: 0001-01-01`.
                    ⚠️ `probes.yml` is deliberately NOT cited here as a dead
                    workflow — CLAUDE.md records that it HAS since fired on cron
                    (run #34, 2026-09-01T10:12:17Z), and repeating the older
                    "zero scheduled runs" claim would be quoting a correction
                    that has already been made. What its run actually supports is
                    the weaker and more useful point: its cron is `20 5 * * *`
                    and it fired at 10:12Z, roughly 4h50m late, ONCE rather than
                    daily. So correct cron syntax is not evidence of a run, and a
                    run is not evidence of a cadence — read the run history.
                    "Someone forgot to prune" and "the thing that prunes is dead"
                    have different remedies, and a shape that reports the second
                    as the first is how a dead reconciler stays dead.
  ``not_observed``  no live list was supplied. ⚠️ WE DID NOT LOOK.
  ``unreadable``    the record could not be parsed.

`settled`:
  ``settled_graded``  every settled row carries a recognised `terminal` and, if
                      it ends `closed_unmerged`, a stated `disposition`.
  ``undispositioned`` a row was CLOSED UNMERGED and nobody said why. ⚠️ This is
                      the finding that keeps the check's teeth after the
                      mechanical regress is gone: an abandoned PR with no stated
                      reason IS real lost knowledge, and it is the one thing in
                      the settled population a successor genuinely cannot
                      reconstruct.
  ``unreadable``      the record could not be parsed.

`decisions`:
  ``graded``               every row carries a typed decision and none contradicts.
  ``verdict_without_condition``  a row declares conditions and records none. THE FINDING.
  ``prose_ungradeable``    a row still carries the free-text form. ⚠️ WE COULD NOT
                           LOOK — emphatically not a pass, for the reason above.
  ``unreadable``           the record could not be parsed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
RECORD_PATH = REPO_ROOT / "docs" / "claude" / "work" / "OPEN-PRS.json"

#: The closed verdict vocabulary. A verdict outside it is a finding rather than
#: a silently-tolerated value — an unrecognised verdict cannot be graded, and a
#: grader that shrugs at what it does not understand is the collapse.
VERDICTS = ("approved", "approved_with_conditions", "not_required",
            "pending", "none_recorded")

#: The verdicts that ASSERT the operator attached conditions. These are the only
#: ones a missing `condition`/`scope` can contradict — `approved` is a positive
#: claim of UNconditional approval, and failing it would force authors to invent
#: a condition to satisfy the guard, which is worse than the gap.
CONDITIONAL_VERDICTS = ("approved_with_conditions",)

#: How a settled PR ENDED. A closed vocabulary for the same reason `VERDICTS`
#: is one — a terminal a grader does not recognise cannot be graded.
#:
#: ⚠️ `unknown_not_reconstructible` is NOT a general escape hatch. It exists for
#: rows MIGRATED from the pre-MI-57 destructive prune, which kept a one-line
#: note and threw the rest away: for those, whether the PR merged or was
#: abandoned is genuinely not recoverable from the record, and writing either
#: one would be inventing it. The reconciler may never emit it — it either saw a
#: terminal or it `could_not_look` and moved nothing — and `grade_settled`
#: FAILS a row that claims it while naming the reconciler as its source, so the
#: hatch cannot be used to launder a failed look into a move.
TERMINAL_MERGED = "merged"
TERMINAL_CLOSED_UNMERGED = "closed_unmerged"
TERMINAL_UNKNOWN = "unknown_not_reconstructible"
TERMINALS = (TERMINAL_MERGED, TERMINAL_CLOSED_UNMERGED, TERMINAL_UNKNOWN)


def read_record(path: Path = RECORD_PATH) -> Tuple[Optional[Any], bool]:
    if not path.is_file():
        return None, True
    try:
        return json.loads(path.read_text(encoding="utf-8")), True
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None, False


def _rows(doc: Optional[Any], key: str) -> List[Dict[str, Any]]:
    if not isinstance(doc, dict):
        return []
    r = doc.get(key)
    return [x for x in r if isinstance(x, dict)] if isinstance(r, list) else []


def rows(doc: Optional[Any]) -> List[Dict[str, Any]]:
    """The IN-FLIGHT population, and the only one graded against the live open
    list. ⚠️ Deliberately does NOT include `settled_prs[]`: those rows name PRs
    that are *supposed* to be closed, so folding them in would report the
    decision record's entire history as stale."""
    return _rows(doc, "open_prs")


def settled_rows(doc: Optional[Any]) -> List[Dict[str, Any]]:
    """The DURABLE population. Never compared to what is open."""
    return _rows(doc, "settled_prs")


def _v(state: str, message: str, **extra: Any) -> Dict[str, Any]:
    return dict(state=state, message=message, **extra)


_PR_NUM_RE = re.compile(r"\b(\d{2,7})\b")


def normalise_open_prs(raw: Any) -> Optional[List[int]]:
    """Read a live list of OPEN pull-request numbers out of what a caller has.

    Returns ``None`` when nothing usable was found — and the caller must grade
    that ``not_observed``, never "no PRs are open". Tolerated: a list of ints; a
    list of dicts with ``number``/``pr``; a wrapper dict; and, as a last resort,
    ``#1234``-shaped tokens in pasted text (bare numbers are deliberately NOT
    harvested — a date or a row count would sail straight in).
    """
    if isinstance(raw, str):
        found = [int(n) for n in re.findall(r"#(\d{2,7})\b", raw)]
        return sorted(set(found)) or None
    for _ in range(4):
        if not isinstance(raw, dict):
            break
        for key in ("open_prs", "pull_requests", "sessions", "data", "results",
                    "items", "ccr"):
            inner = raw.get(key)
            if isinstance(inner, (list, dict)):
                raw = inner
                break
        else:
            break
        if isinstance(raw, list):
            break
    if not isinstance(raw, list):
        return None
    out: List[int] = []
    for e in raw:
        if isinstance(e, bool):
            continue
        if isinstance(e, int):
            out.append(e)
        elif isinstance(e, dict):
            for k in ("number", "pr", "pullNumber"):
                if isinstance(e.get(k), int):
                    out.append(e[k])
                    break
        elif isinstance(e, str):
            m = _PR_NUM_RE.search(e)
            if m:
                out.append(int(m.group(1)))
    return sorted(set(out)) or None


def grade_completeness(doc: Optional[Any], readable: bool,
                       observed_open: Optional[List[int]],
                       head_sha: Optional[str] = None) -> Dict[str, Any]:
    """Grade `open_prs[]` — and ONLY `open_prs[]` — against a live observation.

    ⚠️ `settled_prs[]` is deliberately absent from every set below. Those rows
    name PRs that are SUPPOSED to be closed; comparing them to the open list
    would report the entire decision history as stale, which is precisely the
    pressure that used to get it deleted.

    `head_sha`, when supplied, separates the two causes of a stale row: a
    reconciler that never ran, and a session that left a row behind. Without it
    the two cannot be told apart and the message says so rather than picking.
    """
    if not readable:
        return _v("unreadable",
                  "OPEN-PRS.json could not be parsed. WE DID NOT LOOK — this is "
                  "not evidence that every open PR is recorded.")
    if observed_open is None:
        return _v("not_observed",
                  "no live open-PR list was supplied, so nothing was compared. "
                  "⚠️ WE DID NOT LOOK. GitHub is the source of truth for what is "
                  "open and this container cannot reach it on a Routine-woken "
                  "turn — pass it with --open-prs. This is NOT `recorded`.")
    recorded = {r.get("pr") for r in rows(doc) if isinstance(r.get("pr"), int)}
    obs = set(observed_open)
    unrecorded = sorted(obs - recorded)
    stale = sorted(recorded - obs)
    pop = {"observed_open": len(obs), "rows": len(recorded),
           "unrecorded": len(unrecorded), "stale_rows": len(stale)}
    if unrecorded:
        return _v("unrecorded",
                  f"{len(unrecorded)} open PR(s) have no row: "
                  f"{', '.join('#%d' % n for n in unrecorded)} (of {len(obs)} open, "
                  f"against {len(recorded)} recorded). A successor cannot see them, "
                  f"nor any operator decision attached to them.",
                  unrecorded=unrecorded, stale_rows=stale, population=pop)
    if stale:
        names = ", ".join("#%d" % n for n in stale)
        last = doc.get("last_reconciled_sha") if isinstance(doc, dict) else None
        if head_sha and last != head_sha:
            return _v("reconciler_not_run",
                      f"{len(stale)} row(s) name a PR that is no longer open "
                      f"({names}), and THE RECONCILER HAS NOT RUN SINCE THE LAST "
                      f"MERGE — `last_reconciled_sha` is {last!r}, `main` is at "
                      f"{head_sha!r}. ⚠️ This is NOT 'a session forgot to prune': "
                      f"the automation that moves settled rows is not keeping up, "
                      f"or is not firing at all. Check the reconcile-open-prs "
                      f"workflow actually RAN — a merged, enabled, syntactically "
                      f"correct workflow in this repo is not evidence that it "
                      f"fires (probes.yml and due-list.yml have never once fired "
                      f"on cron). Pruning by hand would hide the dead automation.",
                      unrecorded=[], stale_rows=stale, population=pop,
                      last_reconciled_sha=last, head_sha=head_sha)
        why = ("the reconciler HAS run against this sha, so this is a row left "
               "behind rather than a dead automation"
               if head_sha else
               "⚠️ no `head_sha` was supplied, so the reconciler-liveness half "
               "was NOT checked — this message does not claim to know which of "
               "the two causes it is")
        return _v("stale_row",
                  f"{len(stale)} row(s) name a PR that is no longer open: "
                  f"{names}. A settled PR belongs in `settled_prs[]`, MOVED there "
                  f"with its operator decision intact — never deleted. {why}.",
                  unrecorded=[], stale_rows=stale, population=pop,
                  last_reconciled_sha=last, head_sha=head_sha)
    return _v("recorded",
              f"all {len(obs)} open PR(s) have a row, and no row names a closed "
              f"one ({len(recorded)} rows).",
              unrecorded=[], stale_rows=[], population=pop)


def grade_structural(doc: Optional[Any], readable: bool) -> Dict[str, Any]:
    """Cheap offline integrity of the record itself.

    Small, but it guards the KEY every other check joins on: a duplicate or
    non-integer `pr` makes the completeness comparison silently wrong rather
    than loud — two rows for one PR let a successor read whichever it hits
    first, and the two can carry different operator decisions.
    """
    if not readable:
        return _v("unreadable", "OPEN-PRS.json could not be parsed.", findings=[])
    findings, seen = [], {}
    for i, r in enumerate(rows(doc)):
        pr = r.get("pr")
        if not isinstance(pr, int) or isinstance(pr, bool) or pr <= 0:
            findings.append({"row": i, "why": f"`pr` is {pr!r}, not a positive int — "
                                              f"every other check joins on this key"})
            continue
        if pr in seen:
            findings.append({"row": i, "why": f"duplicate pr #{pr} (first at row "
                                              f"{seen[pr]}); the two rows may carry "
                                              f"different operator decisions and a "
                                              f"reader gets whichever it hits first"})
        seen.setdefault(pr, i)
    return _v("malformed" if findings else "well_formed",
              f"{len(findings)} structural finding(s) over {len(rows(doc))} row(s)."
              if findings else f"{len(rows(doc))} row(s), all well-formed.",
              findings=findings)


def grade_settled(doc: Optional[Any], readable: bool) -> Dict[str, Any]:
    """The settled population's own integrity — NEVER its freshness.

    ⚠️ WHY THIS CHECK EXISTS AT ALL. Splitting `settled_prs[]` out kills the
    mechanical regress, and a split that ONLY removed a failure mode would have
    removed the check's teeth with it: every row would land somewhere nothing
    grades. So the split ships with the one finding that is real rather than
    mechanical — **a PR that was closed WITHOUT merging and with no stated
    reason.**

    That is genuinely lost knowledge, and it is the asymmetric case. A merged
    row explains itself: the code is on `main`. An ABANDONED row does not. A
    successor reading `terminal: closed_unmerged` with no `disposition` cannot
    tell "superseded by #X", "the operator refused it", and "the author gave up"
    apart — and those imply opposite next actions, one of which is re-opening
    something an operator already turned down.
    """
    if not readable:
        return _v("unreadable",
                  "OPEN-PRS.json could not be parsed, so no settled row could be "
                  "graded. WE DID NOT LOOK.", findings=[])
    findings = []
    for r in settled_rows(doc):
        pr, term = r.get("pr"), r.get("terminal")
        if term not in TERMINALS:
            findings.append({"pr": pr, "why": f"`terminal` is {term!r}, not one of "
                                              f"{list(TERMINALS)} — a terminal the "
                                              f"grader does not recognise cannot be "
                                              f"graded, and a row nothing grades is "
                                              f"where knowledge goes to die"})
            continue
        if term == TERMINAL_UNKNOWN and str(r.get("settled_by") or "").startswith(
                "reconciler"):
            findings.append({"pr": pr, "why": (
                "the RECONCILER may never write "
                f"`{TERMINAL_UNKNOWN}`. It either observed a terminal or it "
                "`could_not_look`, and in that case it moves NOTHING. A row in "
                "this shape is a failed look laundered into a settled move, "
                "which is the exact collapse the three states exist to prevent")})
            continue
        if term in (TERMINAL_CLOSED_UNMERGED, TERMINAL_UNKNOWN) and not str(
                r.get("disposition") or "").strip():
            findings.append({"pr": pr, "why": (
                f"`terminal` is {term!r} and there is no `disposition`. The PR is "
                f"not on `main` and nobody said why — 'superseded', 'the operator "
                f"refused it' and 'the author gave up' are opposite next actions "
                f"and this row cannot tell them apart")})
    pop = {"settled": len(settled_rows(doc)), "findings": len(findings)}
    if findings:
        return _v("undispositioned",
                  f"{len(findings)} settled row(s) record an ending nobody "
                  f"accounted for, over {pop['settled']} row(s). A PR that never "
                  f"reached `main`, with no reason recorded, is real lost "
                  f"knowledge — the mechanical staleness regress is gone, this is "
                  f"not.",
                  findings=findings, population=pop)
    return _v("settled_graded",
              f"all {pop['settled']} settled row(s) carry a recognised terminal, "
              f"and every one that did not merge says why.",
              findings=[], population=pop)


def grade_decisions(doc: Optional[Any], readable: bool) -> Dict[str, Any]:
    if not readable:
        return _v("unreadable",
                  "OPEN-PRS.json could not be parsed, so no decision could be "
                  "graded. WE DID NOT LOOK.", findings=[], prose_rows=[])
    findings, prose, graded = [], [], 0
    for r in rows(doc):
        pr, d = r.get("pr"), r.get("operator_decision")
        if not isinstance(d, dict):
            # The pre-MI-43 free-text form. Ungradeable BY CONSTRUCTION — see
            # the module docstring. Never counted as a pass.
            prose.append(pr)
            continue
        graded += 1
        verdict = d.get("verdict")
        if verdict not in VERDICTS:
            findings.append({"pr": pr, "why": f"verdict {verdict!r} is not one of "
                                              f"{list(VERDICTS)} — an unrecognised "
                                              f"verdict cannot be graded"})
            continue
        if not str(d.get("text") or "").strip():
            findings.append({"pr": pr, "why": "no `text` — the operator's own "
                                              "wording must be preserved verbatim, "
                                              "or the typed verdict cannot be "
                                              "checked against what was said"})
        if verdict in CONDITIONAL_VERDICTS:
            has = any(str(d.get(k) or "").strip() for k in ("condition", "scope"))
            if not has:
                findings.append({"pr": pr, "why": f"verdict {verdict!r} DECLARES that "
                                                  f"conditions were attached and "
                                                  f"records neither `condition` nor "
                                                  f"`scope`. This is the half-informed "
                                                  f"state: it reads as approved."})
    pop = {"rows": len(rows(doc)), "typed": graded, "prose": len(prose),
           "findings": len(findings)}
    if findings:
        return _v("verdict_without_condition",
                  f"{len(findings)} decision finding(s) over {graded} typed row(s). "
                  f"A row recording a verdict without its condition is WORSE than a "
                  f"missing row — it reads as complete.",
                  findings=findings, prose_rows=prose, population=pop)
    if prose:
        return _v("prose_ungradeable",
                  f"{len(prose)} row(s) still carry the free-text `operator_decision` "
                  f"({', '.join('#%s' % p for p in prose)}). ⚠️ WE COULD NOT LOOK: a "
                  f"condition dropped from prose is not mechanically detectable, and "
                  f"matching English for it would be diagnostic-provenance sub-class "
                  f"A. Convert them to the typed form. This is NOT a pass.",
                  findings=[], prose_rows=prose, population=pop)
    return _v("graded",
              f"all {graded} row(s) carry a typed decision; every conditional "
              f"verdict records its condition or scope.",
              findings=[], prose_rows=[], population=pop)


def _self_test() -> int:
    ok = True

    def check(label, got, want):
        nonlocal ok
        good = got == want
        ok &= good
        print(f"  self-test ({label}): {'PASS' if good else f'FAIL got={got!r} want={want!r}'}")

    def row(pr, **d):
        return {"pr": pr, "operator_decision": dict(d)}

    good = {"open_prs": [row(1, verdict="approved_with_conditions",
                             condition="bybit_1 only", text="APPROVED, bybit_1 ONLY"),
                         row(2, verdict="not_required", text="None required (Tier-1).")]}

    # --- completeness: both directions -------------------------------------
    check("an OPEN PR with no row -> `unrecorded`",
          grade_completeness(good, True, [1, 2, 3])["state"], "unrecorded")
    check("a complete record -> `recorded` (not a wall)",
          grade_completeness(good, True, [1, 2])["state"], "recorded")
    check("a row for a PR no longer open -> `stale_row`",
          grade_completeness(good, True, [1])["state"], "stale_row")
    check("NO live list is `not_observed`, NEVER `recorded`",
          grade_completeness(good, True, None)["state"], "not_observed")
    check("an unreadable record is `unreadable`, not `recorded`",
          grade_completeness(None, False, [1, 2])["state"], "unreadable")
    check("`unrecorded` outranks `stale_row` — an unseen OPEN pr is the worse half",
          grade_completeness(good, True, [1, 3])["state"], "unrecorded")

    # --- decisions: the dangerous case -------------------------------------
    check("THE FINDING: conditions declared, none recorded",
          grade_decisions({"open_prs": [row(1, verdict="approved_with_conditions",
                                            text="approved")]}, True)["state"],
          "verdict_without_condition")
    check("...and a `scope` alone satisfies it (either names the limit)",
          grade_decisions({"open_prs": [row(1, verdict="approved_with_conditions",
                                            scope="bybit_1", text="t")]}, True)["state"],
          "graded")
    check("a plain unconditional `approved` is NOT forced to invent a condition",
          grade_decisions({"open_prs": [row(1, verdict="approved", text="t")]},
                          True)["state"], "graded")
    check("a typed decision with no `text` is a finding (wording must survive)",
          grade_decisions({"open_prs": [row(1, verdict="approved")]}, True)["state"],
          "verdict_without_condition")
    check("an unrecognised verdict is a FINDING, not silently tolerated",
          grade_decisions({"open_prs": [row(1, verdict="lgtm", text="t")]},
                          True)["state"], "verdict_without_condition")
    check("free-text prose is `prose_ungradeable`, NEVER a pass",
          grade_decisions({"open_prs": [{"pr": 1, "operator_decision": "approved, "
                                                                      "bybit_1 only"}]},
                          True)["state"], "prose_ungradeable")
    check("a fully typed record grades", grade_decisions(good, True)["state"], "graded")
    check("an unreadable record is `unreadable`, not `graded`",
          grade_decisions(None, False)["state"], "unreadable")
    check("a real finding OUTRANKS the prose caveat (a defect beats a caveat)",
          grade_decisions({"open_prs": [row(1, verdict="approved_with_conditions",
                                            text="x"),
                                        {"pr": 2, "operator_decision": "prose"}]},
                          True)["state"], "verdict_without_condition")

    # --- the two populations, and the regress they killed -------------------
    settled_only = {"open_prs": [row(1, verdict="approved", text="t")],
                    "settled_prs": [{"pr": 99, "terminal": "merged",
                                     "merge_sha": "abc123"}]}
    check("a SETTLED row is NEVER compared to the live open list — this is the "
          "regress fix; #99 is closed and must not read as stale",
          grade_completeness(settled_only, True, [1])["state"], "recorded")
    check("...and it is not counted as an unrecorded open PR either",
          grade_completeness(settled_only, True, [1])["population"]["rows"], 1)

    # --- the reconciler-liveness split --------------------------------------
    # #1 is still open, #2 has merged and nobody moved its row -> stale, with
    # NO unrecorded PR in the observation, so the two findings stay separable.
    stale_doc = {"open_prs": [row(1, verdict="approved", text="t"),
                              row(2, verdict="approved", text="t")],
                 "last_reconciled_sha": "deadbeef"}
    check("a stale row + a LAGGING reconciler sha is `reconciler_not_run`, "
          "never `stale_row` — different cause, different remedy",
          grade_completeness(stale_doc, True, [1], head_sha="cafe")["state"],
          "reconciler_not_run")
    check("a stale row while the reconciler HAS run against this sha is a row a "
          "session left behind",
          grade_completeness(stale_doc, True, [1], head_sha="deadbeef")["state"],
          "stale_row")
    check("with NO head_sha the liveness half is not checked, so it stays "
          "`stale_row` rather than guessing which cause it is",
          grade_completeness(stale_doc, True, [1])["state"], "stale_row")
    check("an unrecorded OPEN pr still outranks a dead reconciler — a PR nobody "
          "can see is the worse half",
          grade_completeness(stale_doc, True, [1, 3], head_sha="cafe")["state"],
          "unrecorded")

    # --- settled integrity: the finding that keeps the teeth ----------------
    def sr(pr, **k):
        return {"settled_prs": [dict(pr=pr, **k)]}

    check("THE FINDING: closed_unmerged with no disposition — an abandoned PR "
          "nobody accounted for",
          grade_settled(sr(1, terminal="closed_unmerged"), True)["state"],
          "undispositioned")
    check("...and a stated disposition satisfies it",
          grade_settled(sr(1, terminal="closed_unmerged",
                           disposition="superseded by #2"), True)["state"],
          "settled_graded")
    check("a MERGED row needs no disposition — the code is on main, it explains "
          "itself",
          grade_settled(sr(1, terminal="merged", merge_sha="a"), True)["state"],
          "settled_graded")
    check("an unrecognised terminal is a FINDING, not silently tolerated",
          grade_settled(sr(1, terminal="probably_merged"), True)["state"],
          "undispositioned")
    check("the RECONCILER may never write `unknown_not_reconstructible` — that "
          "would launder a failed look into a settled move",
          grade_settled(sr(1, terminal=TERMINAL_UNKNOWN, settled_by="reconciler",
                           disposition="x"), True)["state"], "undispositioned")
    check("...but a MIGRATED row may, when it says why",
          grade_settled(sr(1, terminal=TERMINAL_UNKNOWN,
                           settled_by="session:pre-MI-57-prune",
                           disposition="the old prune kept only a one-line note"),
                        True)["state"], "settled_graded")
    check("an empty settled list grades, rather than erroring",
          grade_settled({"settled_prs": []}, True)["state"], "settled_graded")
    check("an unreadable record is `unreadable`, not `settled_graded`",
          grade_settled(None, False)["state"], "unreadable")

    # --- the observation parser --------------------------------------------
    check("a duplicate pr number is a structural finding",
          grade_structural({"open_prs": [{"pr": 1}, {"pr": 1}]}, True)["state"],
          "malformed")
    check("a non-integer pr is a structural finding",
          grade_structural({"open_prs": [{"pr": "10746"}]}, True)["state"], "malformed")
    check("a clean record is well_formed",
          grade_structural({"open_prs": [{"pr": 1}, {"pr": 2}]}, True)["state"],
          "well_formed")
    check("an unreadable record is `unreadable`, not `well_formed`",
          grade_structural(None, False)["state"], "unreadable")

    check("a list of ints is understood", normalise_open_prs([1, 2]), [1, 2])
    check("list_pull_requests dicts are understood",
          normalise_open_prs([{"number": 7}, {"number": 3}]), [3, 7])
    check("a wrapper dict is descended",
          normalise_open_prs({"ccr": {"data": [{"number": 9}]}}), [9])
    check("`#1234` tokens in pasted text are harvested",
          normalise_open_prs("open: #10766 and #10746"), [10746, 10766])
    check("BARE numbers in text are NOT harvested (a date is not a PR)",
          normalise_open_prs("as of 2026 there are 42 rows"), None)
    check("an empty list yields None, so it cannot read as 'nothing is open'",
          normalise_open_prs([]), None)

    print("open-pr-record self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def _load(spec: Optional[str]) -> Optional[Any]:
    if spec is None:
        return None
    text = sys.stdin.read() if spec == "-" else Path(spec).read_text(encoding="utf-8")
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    ends = [i for i in (text.rfind("}"), text.rfind("]")) if i >= 0]
    for cand in ([text] + ([text[min(starts):max(ends) + 1]] if starts and ends else [])):
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            continue
    return text


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--open-prs", default=None,
                    help="live open-PR list (JSON or pasted text with #NNNN). "
                         "WITHOUT IT completeness grades `not_observed`.")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero on a DECISION finding. Completeness is "
                         "deliberately excluded: it needs live truth CI cannot get.")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()

    doc, ok = read_record()
    st = grade_structural(doc, ok)
    comp = grade_completeness(doc, ok, normalise_open_prs(_load(a.open_prs))
                              if a.open_prs else None)
    dec = grade_decisions(doc, ok)
    settled = grade_settled(doc, ok)
    print(f"open-pr-record: structural={st['state']} — {st['message']}")
    for f in st.get("findings", []):
        print(f"  ::structural:: row {f['row']}: {f['why']}")
    print(f"open-pr-record: completeness={comp['state']} — {comp['message']}")
    print(f"open-pr-record: decisions={dec['state']} — {dec['message']}")
    for f in dec.get("findings", []):
        print(f"  ::FINDING:: #{f['pr']}: {f['why']}")
    print(f"open-pr-record: settled={settled['state']} — {settled['message']}")
    for f in settled.get("findings", []):
        print(f"  ::FINDING:: #{f['pr']}: {f['why']}")
    if a.json:
        print(json.dumps({"structural": st, "completeness": comp,
                          "decisions": dec, "settled": settled}, indent=2,
                         ensure_ascii=False))
    if not a.strict:
        return 0
    # ⚠️ COMPLETENESS stays excluded (it needs live truth CI cannot get), but
    # the SETTLED grade is included: it is a pure offline read of the record,
    # and it is the finding that keeps this check's teeth once the mechanical
    # staleness regress is gone.
    bad = (dec["state"] in {"verdict_without_condition", "unreadable"}
           or settled["state"] in {"undispositioned", "unreadable"}
           or st["state"] != "well_formed")
    if bad:
        print("::error::open-pr-record: REFUSED. A row recording a verdict without "
              "its condition reads as complete, which is the half-informed state "
              "that could merge a demo-only approval onto a real-money account; "
              "a settled PR that never reached `main` with no reason recorded is "
              "knowledge no successor can reconstruct.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
