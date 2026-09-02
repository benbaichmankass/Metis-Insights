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

STATES, NEVER COLLAPSED
-----------------------
`completeness`:
  ``recorded``      every open PR has a row and every row is still open.
  ``unrecorded``    an OPEN PR has no row. A successor cannot see it.
  ``stale_row``     a row names a PR that is no longer open — the file's own
                    `_doc` says it "goes stale the moment a PR merges", so this
                    is the staleness signal, mechanical and threshold-free.
  ``not_observed``  no live list was supplied. ⚠️ WE DID NOT LOOK.
  ``unreadable``    the record could not be parsed.

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


def read_record(path: Path = RECORD_PATH) -> Tuple[Optional[Any], bool]:
    if not path.is_file():
        return None, True
    try:
        return json.loads(path.read_text(encoding="utf-8")), True
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None, False


def rows(doc: Optional[Any]) -> List[Dict[str, Any]]:
    if not isinstance(doc, dict):
        return []
    r = doc.get("open_prs")
    return [x for x in r if isinstance(x, dict)] if isinstance(r, list) else []


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
                       observed_open: Optional[List[int]]) -> Dict[str, Any]:
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
        return _v("stale_row",
                  f"{len(stale)} row(s) name a PR that is no longer open: "
                  f"{', '.join('#%d' % n for n in stale)}. The record's own _doc "
                  f"says it goes stale the moment a PR merges — this is that, "
                  f"detected without a wall-clock threshold.",
                  unrecorded=[], stale_rows=stale, population=pop)
    return _v("recorded",
              f"all {len(obs)} open PR(s) have a row, and no row names a closed "
              f"one ({len(recorded)} rows).",
              unrecorded=[], stale_rows=[], population=pop)


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

    # --- the observation parser --------------------------------------------
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
    comp = grade_completeness(doc, ok, normalise_open_prs(_load(a.open_prs))
                              if a.open_prs else None)
    dec = grade_decisions(doc, ok)
    print(f"open-pr-record: completeness={comp['state']} — {comp['message']}")
    print(f"open-pr-record: decisions={dec['state']} — {dec['message']}")
    for f in dec.get("findings", []):
        print(f"  ::FINDING:: #{f['pr']}: {f['why']}")
    if a.json:
        print(json.dumps({"completeness": comp, "decisions": dec}, indent=2,
                         ensure_ascii=False))
    if not a.strict:
        return 0
    bad = dec["state"] in {"verdict_without_condition", "unreadable"}
    if bad:
        print("::error::open-pr-record: REFUSED. A row recording a verdict without "
              "its condition reads as complete, which is the half-informed state "
              "that could merge a demo-only approval onto a real-money account.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
