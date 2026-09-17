#!/usr/bin/env python3
"""A NEW backlog row must be WORKABLE: what done looks like, how much it
matters, and who may fix it.

WHY THIS EXISTS. Two high-severity rows were found finished-but-open on
2026-08-12, and both had the same cause — no ``resolution_criteria``:

* ``BL-20260808-TREND-HARNESS-FORK-SPLITS-FIDELITY-FROM-EVIDENCE`` — the fork had been closed by convergence
  (the 15 levers ported into the canonical harness, the other copy reduced to a
  shim) and the row sat ``open``/``high`` for four days after the fix landed.
* ``BL-20260806-DUPLICATE-PNL-NETTED-SIBLING-ROWS`` — 29 of 33 suspect rows had
  already been marked, i.e. the substance was done, and the row still read as
  live work.

A row nobody can *tell* is finished never gets closed. It then accumulates with
its peers until the backlog reads as noise, which this repo already names as a
P1 in its own right (the desensitized-alarm rule): an alarm routinely walked
past is worse than no alarm, because it trains everyone to walk past the real
ones too. The cost is not tidiness — it is that a genuine finding filed into a
noisy backlog is indistinguishable from the stale rows around it.

SCOPE — deliberately diff-scoped, and that is not timidity. Applied to the whole
tree this fails on the large pre-existing population immediately, and a guard
that fails everywhere on day one gets switched off or routed around, which is
strictly worse than no guard. Grandfathering the past and holding the FUTURE to
the rule is what makes it survivable. ``--all`` is the standing advisory census
so the debt stays visible rather than forgotten.

NOT CHEAP TO LIE TO. The direct lesson from ``new-table-wiring-guard``, whose
presence-only marker made the cheapest way to silence a real finding *naming a
table that does not exist*: a guard easier to fool than to satisfy is worse than
no guard. So a placeholder (``TBD``, ``n/a``, ``see above``, ``unknown``) is
rejected, and criteria must be long enough to name an observable condition. It
is still trivially possible to write a *bad* criterion — this guard cannot judge
quality, and does not pretend to. It only refuses the empty and the obviously
vacuous, which is exactly the failure that produced the two incidents above.

EXTENDED 2026-08-13 (operator-directed) from one field to three, after the
backlog was measured rather than described. Over 269 open rows: **38% had no
``resolution_criteria``, 44% no ``severity``, 24% no ``tier``** — and the
backlog grew **+129 net in 30 days** while **25% of it sat >=45 days old having
never once been advanced by evidence**. Those are not three tidiness problems,
they are three different ways for a row to be un-workable:

* no criteria  -> the row can only be RE-READ, never closed
* no severity  -> it cannot be sorted, so it is picked by recency, not importance
* no tier      -> nothing says whether a session may fix it or must ask, so it
  is safest to do neither, and neither is what happens

The corroborating measurement: **zero** Tier-1 high/critical rows were older
than 14 days. When a row says it matters AND says a session may act, it gets
fixed. The rot is entirely in rows that say neither.

``snoozed_until`` is validated but never required — it is the DEFER path
(set on 2 of 269 rows at the time of writing, i.e. effectively unused), and a
row genuinely blocked on accrual belongs behind a date rather than in front of
every review pass forever.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from typing import Any, Iterable

# `scripts/ops` is on sys.path when this file is RUN directly, but not when it
# is imported by a harness from elsewhere. Add it explicitly so the guard cannot
# fail on an ImportError that depends on how it was invoked.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _backlog import (  # noqa: E402
    DATE_FIELDS, DATE_STATED, UnsupportedCriteriaShape, criteria_text, row_date,
)
from accrual_clock import (  # noqa: E402
    ACCRUAL_LEGS_FIELD,
    CAN_RUN,
    accrual_legs,
    clock_state,
    exit_text,
    is_accrual_shaped,
    load_config,
)

BACKLOGS = (
    "docs/claude/health-review-backlog.json",
    "docs/claude/performance-review-backlog.json",
    "docs/claude/ml-review-backlog.json",
)

#: The four review backlogs. ``BACKLOGS`` above is the THREE the criteria check
#: has always covered; the kept_open check below spans all four because the
#: class it enforces was measured across all four and the number must stay
#: comparable. research-review-backlog.json held 0 kept_open rows when this
#: landed, so widening the set changed no count — it removes a future blind spot.
ALL_BACKLOGS = BACKLOGS + ("docs/claude/research-review-backlog.json",)

#: Every field name under which a backlog row states an EXIT CONDITION.
#:
#: NOT a fresh taxonomy — this is the predicate
#: ``BL-20260825-KEPT-OPEN-ROWS-WITH-NO-EXIT-CONDITION-CAN-NEVER-BE-RETIRED``
#: measured itself with, transcribed from that row's own ``detail`` so the guard
#: and the metric cannot drift into two different questions. Until now it existed
#: ONLY as prose in that row and was re-derived by hand on every pass (2026-08-25,
#: then again 2026-09-01), which is why criterion 4 ("re-measure and report the
#: count") had no mechanical way to be satisfied.
#:
#: POSITIVE CONTROL, and it is the reason this list is trustworthy rather than
#: plausible: run over the corpus at 943a7192 this predicate reproduces the
#: 2026-09-01 Phase C measurement EXACTLY — health 24 / ml 10 / performance 5 /
#: research 0 = 39, against the 39 that row records. A list that merely looked
#: right would not have landed on the same four numbers.
EXIT_CONDITION_FIELDS = (
    "resolution_criteria",
    "close_condition",
    "trigger_condition",
    "what_to_check",
    "next_action",
    "next_step",
    "action",
    "remaining_work",
    "disposition_when_verified",
    "snoozed_until",
    "proposed_fix",
    "suggested_fix",
    "recommended_fix",
    "recommendation",
    "suggested_next_step",
)

#: ``snoozed_until`` is the one exit condition that is a DATE, not prose, so it
#: is graded by :data:`_ISO_DATE_RE` rather than by the length floor. A row
#: parked behind a real date has a stated exit ("come back then"); one parked
#: behind "soon" has not, and that is already why `_verdict` refuses it.
_DATE_FIELDS = frozenset({"snoozed_until"})

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _field_text(row: dict[str, Any], field: str) -> str:
    """This field's prose, shape-tolerantly.

    Mirrors :func:`_backlog.criteria_text` (newline-joined, never ``repr``) so a
    list-shaped field is measured on its PROSE and not on its bracket-and-quote
    punctuation — the accidental pass that `criteria_text` exists to prevent.
    A dict or any other non-scalar yields "" rather than a stringified blob that
    would clear the floor while saying nothing.
    """
    raw = row.get(field)
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, (int, float)):
        return str(raw).strip()
    if isinstance(raw, list):
        parts = [str(x).strip() for x in raw if isinstance(x, (str, int, float))]
        return "\n".join(p for p in parts if p)
    return ""


def exit_condition_fields(row: dict[str, Any]) -> list[str]:
    """The fields under which *row* states a usable exit condition.

    Empty list == this row states NO condition under which it would ever stop
    being carried. That is the permanent-resident class, and it is a real and
    distinct state from "the row is malformed" — hence a list, not a bool.
    """
    found: list[str] = []
    for field in EXIT_CONDITION_FIELDS:
        text = _field_text(row, field)
        if not text or text.casefold() in _PLACEHOLDERS:
            continue
        if field in _DATE_FIELDS:
            if _ISO_DATE_RE.match(text):
                found.append(field)
            continue
        if len(text) >= _MIN_LEN:
            found.append(field)
    return found

#: Values that are present but say nothing. Compared case-folded and stripped.
_PLACEHOLDERS = {
    "", "tbd", "tba", "n/a", "na", "none", "null", "-", "--", "?", "???",
    "see above", "see below", "unknown", "todo", "to do", "pending", "wip",
}

#: A criterion has to name an observable condition. Anything shorter than this
#: cannot ("fixed", "works", "green" are not criteria). Chosen to be permissive
#: — the point is to catch the empty and the vacuous, not to police prose.
_MIN_LEN = 40


#: How a backlog file read. THREE STATES, NEVER COLLAPSED, on BOTH sides of the
#: diff — `absent` and `unreadable` are opposite facts and neither is "no rows".
ROWS_READ, ROWS_ABSENT, ROWS_UNREADABLE = "read", "absent", "unreadable"


def _rows_of(text: str | None) -> tuple[str, list[dict[str, Any]]]:
    """`(state, rows)` for a backlog file's TEXT. The ONE parser for both sides.

    ⚠️ **A TOP-LEVEL SHAPE THAT IS NEITHER A LIST NOR AN ``items`` DICT IS
    `unreadable`, NOT EMPTY.** The old code returned `[]` for it, so a file
    whose array had been renamed away read as a backlog with no rows. All four
    live backlogs are `{"items": [...]}` (measured 2026-09-13), so nothing
    legitimate lands here.
    """
    if text is None:
        return ROWS_ABSENT, []
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        return ROWS_UNREADABLE, []
    if isinstance(doc, list):
        return ROWS_READ, doc
    if isinstance(doc, dict) and isinstance(doc.get("items"), list):
        return ROWS_READ, doc["items"]
    return ROWS_UNREADABLE, []


def _read_file(path: pathlib.Path) -> tuple[str, list[dict[str, Any]]]:
    """`(state, rows)` for a backlog file ON DISK — the HEAD side of the diff."""
    try:
        return _rows_of(path.read_text())
    except FileNotFoundError:
        return ROWS_ABSENT, []
    except OSError:
        return ROWS_UNREADABLE, []


def _load(path: pathlib.Path) -> list[dict[str, Any]]:
    """Rows only. Safe ONLY because `main` refuses before any caller gets here.

    ⚠️ **DO NOT REINSTATE THIS AS THE WHOLE STORY.** Until 2026-09-13 this WAS
    the whole story — a bare `except (OSError, json.JSONDecodeError): return []`
    — and it made the guard state a confident falsehood. MEASURED on
    `origin/main` @`7f832e8c0`, with a conflict marker inserted into
    `docs/claude/health-review-backlog.json` (**1574 rows**), all three checks
    printed OK and the run exited 0:

        backlog-criteria guard: OK — every NEW backlog row states what done looks like.
        kept-open-exit-condition guard: OK — 0 row(s) entered kept_open in this diff...
        accrual-clock guard: OK — 0 accrual-gated row(s) entered or were filed...

    Three OK lines about a file none of them could read. The head-side
    precondition in `main` is what makes this helper honest: by the time any
    check calls it, every declared backlog on disk has been shown to parse.
    """
    return _read_file(path)[1]


sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "ci"))
import _git_base  # noqa: E402  -- path shim above; ONE owner for base resolution


def _load_at_ref(ref: str, rel: str) -> tuple[str, list[dict[str, Any]]]:
    """The file's rows as of the FORK POINT with *ref*. Absent at base = new.

    ⚠️ **THE FORK POINT, NOT THE TIP, AND THE DISTINCTION IS NOT COSMETIC.**
    Both checks that use this ask *"did THIS DIFF do X?"*, which is only the
    diff's doing when the comparison is against the branch's ANCESTOR. Read at
    the base TIP it also differs when the BASE moved ahead -- the normal state
    of every working branch -- and then:

      * `_check_new_rows` grandfathers by id (`if rid in before: continue`), so
        a larger `before` UNDER-enforces: a row this diff really did add is
        skipped because main added one with the same id meanwhile.
      * `_check_kept_open_transitions` FALSE-BLAMES: if main moved a row OUT of
        `kept_open` after the fork, the base reads `resolved` while this
        untouched branch still reads `kept_open`, so the branch is reported as
        moving a row INTO `kept_open` that it never touched.

    Two opposite failure directions from one wrong reference, which is why this
    was worth fixing rather than tolerating.

    ⚠️ **AND NOT EVERY GUARD THAT READS THE TIP IS WRONG — the audit matters
    more than the count.** Measured 2026-09-12: 10 scripts read file content at
    `--base`; 7 read the tip. Auditing those 7 INDIVIDUALLY rather than treating
    the shape as a defect:

      * WRONG (false blame): `check_backlog_unresolve.py`,
        `render_session_brief.py`, and this file's kept-open half.
      * WRONG but UNDER-enforcing: `check_claim_basis.py`, and this file's
        new-rows half.
      * **RIGHT BY DESIGN, DO NOT "FIX" THESE:** `check_register_ids.py` asks
        *"does my id collide with what main HAS?"* -- the tip is exactly right,
        and the fork point would MISS a collision with a row main gained after
        the branch was cut, which is the likeliest collision there is.
        `work_digest.py` and `work_phase_ping.py` take BOTH `--base` and
        `--head` and compare two arbitrary refs over a WINDOW; they are not
        diff-scopers and a merge base would be meaningless to them.
        `check_backlog_refs.py` -- classified 2026-09-17, the last of the five
        tip-readers to be graded either way. Its base read is
        `_refs_anywhere_at`, a FALLBACK that fires only for a path ABSENT at the
        base, and it asks *"is this id cited anywhere in the tree I am merging
        INTO?"* The tree being merged into is the TIP, so the tip is the
        question's own subject rather than an approximation of it. MEASURED on
        `origin/main` 2026-09-17: 1,856 ids cited at the tip, 86 of them
        dangling -- and of the ids cited ONLY in the last 6 / 20 / 60 commits,
        i.e. the whole population where tip and fork point can disagree, **0
        are dangling at every window.** The choice has therefore changed no
        finding. ⚠️ AND THE DIRECTION MATTERS MORE THAN THE COUNT: switching it
        to the fork point would make a guard that runs on EVERY PR blame a diff
        for a dangling id a CONCURRENT branch introduced -- false blame, the
        direction this repo has just paid for. The reverse error, under-
        enforcing when both branches cite the same dangling id, routes that id
        to `BL-20260730-CITED-BUT-UNFILED-BACKLOG-IDS`, which this guard's own
        docstring names as the declared home for pre-existing debt.

    ⚠️ The fallback is to the ref AS GIVEN when no merge base can be computed --
    i.e. today's behaviour -- so this can only ever remove a false failure.

    ⚠️ **AND THE THIRD STATE IS WHY THIS RETURNS A PAIR.** Until 2026-09-13 it
    returned a bare list and a JSON PARSE failure at a perfectly readable ref
    collapsed into the same `[]` as a file that is genuinely new. MEASURED on
    `origin/main` @`7f832e8c0` by corrupting the base copy of
    `health-review-backlog.json` and leaving the head copy intact: **393
    pre-existing rows were blamed** on a diff that touched none of them, under a
    headline about rows "that are not WORKABLE". That is the same defect `main`
    below refuses for an unresolvable REF, one layer down — and the comment
    there said in terms that this case was deliberately left because it had no
    measurement behind it. It has one now.
    """
    base_ref, _state = _git_base.resolve_base(ref)
    try:
        out = subprocess.run(
            ["git", "show", f"{base_ref}:{rel}"],
            capture_output=True, text=True, check=False,
        )
    except OSError:
        return ROWS_UNREADABLE, []
    if out.returncode != 0:
        # git reports a bad REF and a path missing at a good ref identically,
        # and `main` has already established the ref resolves — so this is the
        # file being absent at the base, i.e. genuinely new. Not a failure.
        return ROWS_ABSENT, []
    return _rows_of(out.stdout)


#: The four severities a new row may declare. Deliberately NOT accepting the
#: historical variants (`P1`/`P2`/`P3`, `low-medium`, `medium-high`,
#: `needs-triage`): five spellings of "medium" is why 44% of the open backlog
#: could not be sorted at all. Old rows are grandfathered; new ones normalise.
_SEVERITIES = {"critical", "high", "medium", "low"}

#: Tier must resolve to 1/2/3. A trailing annotation is allowed and useful
#: ("1 (research; promotion past candidate is Tier-3)") — what is refused is a
#: value from which no tier can be read at all, because that is the field that
#: decides whether a session may fix the row itself or must ask the operator.
_TIER_RE = re.compile(r"^\s*(?:tier[\s\-_]*)?([123])\b", re.I)


def _verdict(row: dict[str, Any]) -> str | None:
    """Return a human reason this row fails, or None when it passes.

    THREE FIELDS, one question each, and a row missing any of them is
    structurally un-workable rather than merely untidy:

      resolution_criteria  what does DONE look like?   -> without it the row can
                           only ever be RE-READ, never closed
      severity             how much does this matter?  -> without it the row
                           cannot be sorted, so it is picked by recency
      tier                 who is allowed to fix it?   -> without it the row
                           cannot be routed autonomous-vs-operator

    Measured 2026-08-13 over the 269 open rows: 38% had no criteria, 44% no
    severity, 24% no tier. The consequence is arithmetic, not aesthetic — the
    backlog grew +129 net over 30 days while 25% of it sat >=45 days old having
    never once been advanced. `severity`/`tier` were added to this guard that
    day (it already held the criteria line since 2026-08-12).
    """
    try:
        text = criteria_text(row).strip()
    except UnsupportedCriteriaShape as exc:
        # A dict / an int / a nested list stringifies happily to something that
        # clears the length floor, so the OLD `str(raw)` accepted it. That was a
        # pass by accident, not by design — reject the shape explicitly.
        return str(exc)
    if not text:
        if row.get("resolution_criteria") is None:
            return "no resolution_criteria field"
        return "resolution_criteria is empty"
    if text.casefold() in _PLACEHOLDERS:
        return f"resolution_criteria is a placeholder ({text!r})"
    if len(text) < _MIN_LEN:
        return (
            f"resolution_criteria is {len(text)} chars, under the {_MIN_LEN}-char "
            f"floor — too short to name an observable condition ({text!r})"
        )

    sev = row.get("severity")
    if sev is None or str(sev).strip().casefold() in _PLACEHOLDERS:
        return "no severity — the row cannot be sorted, so it gets picked by recency"
    if str(sev).strip().casefold() not in _SEVERITIES:
        return (
            f"severity {str(sev)!r} is not one of {sorted(_SEVERITIES)} — five "
            f"spellings of 'medium' is why 44% of the open backlog could not be "
            f"sorted at all"
        )

    tier = row.get("tier")
    if tier is None or str(tier).strip().casefold() in _PLACEHOLDERS:
        return (
            "no tier — nothing can tell whether a session may fix this itself or "
            "must ask the operator, so it is safest to do neither (and nobody does)"
        )
    if not _TIER_RE.match(str(tier)):
        return (
            f"tier {str(tier)[:60]!r} does not begin with 1, 2 or 3 — a trailing "
            f"annotation is fine, an unreadable tier is not"
        )

    # `snoozed_until` is OPTIONAL — but when set it must be a real date, because
    # its whole job is to drop the row out of review passes until then. An
    # unparseable value would silently either hide the row forever or not at all,
    # and the reader could not tell which.
    # WHEN WAS THIS ROW OPENED? The fourth structural field, and the one that
    # was missing until 2026-09-12 --
    # BL-20260823-BACKLOG-TRIAGE-SEES-47-PERCENT-OF-THE-LIVE-ROWS criterion 4.
    # A row nobody can date cannot be AGED, so it never surfaces in a
    # stale-work pass however long it sits: it is invisible in exactly the
    # direction that matters.
    #
    # !! THIS IS GRANDFATHERED BY THE SAME ID-SCOPING AS EVERYTHING ELSE HERE,
    #    AND THAT IS LOAD-BEARING, NOT TIMIDITY. MEASURED 2026-09-12 over the
    #    982 LIVE rows across the three backlogs: 40 already carry no
    #    recognised date key at all. A whole-tree gate would red every PR in
    #    the repo on day one, which is how a guard gets switched off instead of
    #    satisfied -- the position check_digest_liveness.py takes on
    #    `never_ran` for precisely this reason. `_check_new_rows` holds only
    #    NEW rows to the rule; `_census` keeps the standing 40 visible and
    #    advisory, so the debt cannot be forgotten either.
    #
    # !! AND IT COSTS CURRENT PRACTICE NOTHING, measured rather than hoped:
    #    of the 249 rows ADDED to the health backlog since origin/main~600,
    #    ZERO would fail this clause. It is not enforcing a new convention, it
    #    is making an existing habit checkable -- `backlog_append.py`, the
    #    mandated filing path, neither stamps nor requires a date, so until now
    #    nothing would have caught the first author who forgot.
    when = row_date(row)
    if when.state != DATE_STATED:
        if when.field is not None:
            return (
                f"{when.field} is {str(when.raw)[:40]!r}, which will not parse as "
                f"a date -- the row cannot be aged, so it never surfaces in a "
                f"stale-work pass however long it sits"
            )
        return (
            f"no date field -- none of {list(DATE_FIELDS)} is present, so nothing "
            f"can tell how long this row has been waiting and it is invisible to "
            f"every ageing pass"
        )

    snooze = row.get("snoozed_until")
    if snooze is not None and str(snooze).strip():
        if not re.match(r"^\d{4}-\d{2}-\d{2}", str(snooze).strip()):
            return (
                f"snoozed_until {str(snooze)!r} is not an ISO date — a snooze that "
                f"cannot be parsed hides the row forever or not at all, and nothing "
                f"reveals which"
            )
    return None


def _report(bad: list[tuple[str, str, str]], *, advisory: bool) -> int:
    if not bad:
        return 0
    lead = "::warning::" if advisory else "::error::"
    print(
        f"{lead}backlog row(s) that are not WORKABLE — missing or unusable "
        f"resolution_criteria / severity / tier. A row nobody can tell is FINISHED "
        f"never gets closed; a row with no severity cannot be sorted; a row with no "
        f"tier cannot be routed. Measured 2026-08-13: the backlog grew +129 net in "
        f"30 days with 25% of it >=45 days old and never once advanced."
    )
    for path, rid, why in bad:
        print(f"  {path}: {rid} — {why}")
    print(
        "\nFix: (1) `resolution_criteria` naming the OBSERVABLE condition that ends "
        "the row — 'the bug is fixed' is not one, 'endpoint X returns field Y for a "
        "rotated log, verified on the live fleet' is; (2) `severity` in "
        "critical|high|medium|low; (3) `tier` starting 1, 2 or 3 (a trailing "
        "annotation is fine). If the row is real but blocked on accrual, set "
        "`snoozed_until` to an ISO date instead of leaving it in every review pass."
    )
    return 0 if advisory else 1


def _check_new_rows(base_ref: str) -> int:
    bad: list[tuple[str, str, str]] = []
    skipped: list[str] = []
    for rel in BACKLOGS:
        path = pathlib.Path(rel)
        if not path.exists():
            continue
        state, base_rows = _load_at_ref(base_ref, rel)
        if state == ROWS_UNREADABLE:
            # ⚠️ THE BASE COPY DID NOT PARSE — *we could not look*, and grading
            # on it is the 393-row false blame in `_load_at_ref`'s docstring:
            # with no `before` set every pre-existing row reads as one this diff
            # just added. SKIPPED rather than REFUSED, deliberately. A corrupt
            # base is main's problem, not this branch's, and the likeliest PR to
            # meet it is the one REPAIRING it — refusing would block the repair.
            # Under-enforcing for one file is the lesser harm; doing it silently
            # is not, which is why it is announced.
            skipped.append(rel)
            print(
                f"backlog-criteria guard: SKIPPING {rel} — its copy at "
                f"{base_ref} did NOT PARSE, so a pre-existing row cannot be "
                f"told from a new one. Its rows are NOT checked by this run. "
                f"This is 'we could not look', not 'the base had no rows'."
            )
            continue
        before = {str(r.get("id")) for r in base_rows if r.get("id")}
        for row in _load(path):
            rid = str(row.get("id") or "")
            if not rid or rid in before:
                continue  # pre-existing rows are grandfathered, by design
            why = _verdict(row)
            if why:
                bad.append((rel, rid, why))
    if not bad:
        # ⚠️ THE OK LINE MUST NOT CLAIM COVERAGE IT DOES NOT HAVE. An
        # unqualified "every NEW backlog row" over a run that skipped a file is
        # the same unprovenanced verdict this whole change exists to remove.
        if skipped:
            print(
                "backlog-criteria guard: OK for the files it could grade — "
                f"{len(skipped)} SKIPPED (see above), so this is NOT a verdict "
                f"over every new row: {', '.join(skipped)}"
            )
        else:
            print("backlog-criteria guard: OK — every NEW backlog row states what done looks like.")
    return _report(bad, advisory=False)


def _kept_open_status_by_id(ref: str, rel: str) -> tuple[str, dict[str, str]]:
    """`(state, {row id: status})` as of *ref*. A row absent at the base is new.

    The state rides along because the two callers below already skip on an empty
    map, and until 2026-09-13 they could not tell WHY it was empty: a file that
    is genuinely new and one whose base copy is CORRUPT both arrived as `{}` and
    were announced with the same words, "absent or empty at {base}".
    """
    state, rows = _load_at_ref(ref, rel)
    return state, {
        str(r.get("id")): str(r.get("status") or "")
        for r in rows
        if r.get("id")
    }


def _check_kept_open_transitions(base_ref: str) -> int:
    """Refuse a row ENTERING ``kept_open`` with no exit condition.

    CRITERION 3 of
    ``BL-20260825-KEPT-OPEN-ROWS-WITH-NO-EXIT-CONDITION-CAN-NEVER-BE-RETIRED``.

    WHY A SEPARATE CHECK FROM `_check_new_rows`. That one grandfathers by ID —
    ``if rid in before: continue`` — so an EXISTING row could be flipped to
    ``kept_open`` with nothing said about what would ever retire it, and this
    guard said nothing. The class is 39 rows and it kept regenerating through
    exactly that hole.

    WHY IT POLICES THE TRANSITION AND NOT FILING IN GENERAL. Measured by the
    2026-09-01 Phase C pass and reproduced here at 943a7192: widening the
    population from ``kept_open`` to ALL carried rows (open + kept_open, 576
    across four backlogs) leaves the count at 39 — it adds **zero**. Every
    ``open`` row states an exit condition under some field name. So the leak is
    not filing; it is specifically what happens when a row is moved INTO
    ``kept_open``, which is a far narrower and more checkable thing than "the
    backlog".

    DIFF-SCOPED, for the reason in this module's header: the standing 39 are
    grandfathered. A guard that fails on day one over a pre-existing population
    gets switched off, and a switched-off guard is worse than none. ``--all``
    reports the stock advisorily so it stays visible.

    THE EXIT CONDITION MAY LIVE UNDER ANY OF :data:`EXIT_CONDITION_FIELDS`,
    not just ``resolution_criteria``. Demanding that one field would fail rows
    that legitimately state their exit under ``trigger_condition`` (361 rows in
    the corpus) or ``what_to_check`` (96) — and would police a different
    question from the one the class row measures, which is how a guard and its
    metric drift apart.
    """
    bad: list[tuple[str, str, str]] = []
    checked = 0
    for rel in ALL_BACKLOGS:
        path = pathlib.Path(rel)
        if not path.exists():
            continue
        base_state, before = _kept_open_status_by_id(base_ref, rel)
        if base_state == ROWS_UNREADABLE:
            print(
                f"kept-open-exit-condition guard: SKIPPING {rel} — its copy at "
                f"{base_ref} did NOT PARSE. Its rows are NOT checked by this "
                f"run. This is 'we could not look', a DIFFERENT fact from the "
                f"absent case below."
            )
            continue
        if not before:
            # The file did not exist (or held no rows) at the base. Every row in
            # it would then read as "filed directly as kept_open", so a new
            # backlog file — or a SPLIT that moves rows out of an existing one,
            # which is exactly what criterion 2 of the class row asks for —
            # would be blocked wholesale. That is the wall shape this guard is
            # scoped to avoid, and a wall gets disabled.
            #
            # ANNOUNCED, never silent: a guard that quietly declines to run is
            # the "green that checked nothing" run_guards.py has a rule about.
            print(
                f"kept-open-exit-condition guard: SKIPPING {rel} — absent or "
                f"empty at {base_ref}, so a genuinely new row cannot be told "
                f"from a moved one. Its rows are NOT checked by this run."
            )
            continue
        for row in _load(path):
            rid = str(row.get("id") or "")
            if not rid or str(row.get("status") or "") != "kept_open":
                continue
            # Already kept_open at the base -> part of the grandfathered stock.
            if before.get(rid) == "kept_open":
                continue
            checked += 1
            if not exit_condition_fields(row):
                prior = before.get(rid)
                how = (
                    "filed directly as kept_open"
                    if prior is None
                    else f"transitioned {prior!r} -> 'kept_open'"
                )
                bad.append((rel, rid, how))

    if not bad:
        print(
            f"kept-open-exit-condition guard: OK — {checked} row(s) entered "
            f"kept_open in this diff, every one of them states an exit condition."
        )
        return 0

    print(
        "::error::row(s) moved INTO kept_open stating NO condition under which "
        "they would ever be retired. This is the permanent-resident class from "
        "BL-20260825-KEPT-OPEN-ROWS-WITH-NO-EXIT-CONDITION-CAN-NEVER-BE-RETIRED: "
        "every /system-review must triage every carried row, so a row with no "
        "exit condition is re-read forever and can only ever be re-deferred."
    )
    for rel, rid, how in bad:
        print(f"  {rel}: {rid} — {how}, with no exit condition")
    print(
        "\nFix: state, under ANY of "
        f"{', '.join(EXIT_CONDITION_FIELDS)}, the OBSERVABLE condition that "
        "retires this row — what measurement, deploy or decision. If it is "
        "blocked on accrual, `snoozed_until` an ISO date is a valid exit "
        "condition. If nothing would ever retire it, it is not a kept_open "
        "backlog row: re-disposition it (resolved / wont_fix / superseded) or "
        "move it to its real home (ROADMAP.md for a milestone PROGRAM).\n"
        "Do NOT satisfy this with boilerplate. That row is explicit: 'a "
        "criterion nobody would ever check is the same permanent resident "
        "wearing a field.'"
    )
    return 1



def _check_accrual_rows(base_ref: str) -> int:
    """Refuse a NEW accrual-gated row whose clock cannot tick.

    THE CLASS, measured 2026-09-02 over the eleven accrual-gated rows in
    ``performance-review-backlog.json`` at trader sha ``68e73de8``:
    **not one was in the state its own text implied.** Five had passed their
    threshold months earlier (375 closed trades sat unread behind rows that say
    "waiting"); four named a leg that CANNOT produce a trade —
    ``tqqq_trend_long_1d`` and ``splg_trend_long_1d`` have zero journal rows
    ever, ``eth_pullback_prop_2h`` and ``htf_pullback_trend_2h`` are
    ``execution: shadow``, the declared no-order gate.

    ``_check_kept_open_transitions`` above cannot see any of this: an accrual
    threshold IS an exit condition under its predicate, so it passes. This is
    the same permanent-resident class one level down — the field is present and
    VACUOUS rather than absent, which is strictly harder to spot by reading.

    WHAT IS ENFORCED, and it is deliberately the offline half only. Whether a
    leg HAS accrued needs the live journal and cannot run in CI. Whether it
    CAN accrue is a pure read of ``config/strategies.yaml`` +
    ``config/accounts.yaml``. So an accrual-shaped row must (a) name its legs in
    ``accrual_legs`` and (b) have every named leg resolve to ``can_run``.

    WHY A STRUCTURED FIELD RATHER THAN PARSING THE PROSE. A guard that scraped
    strategy names out of criteria text would be cheaper to fool than to satisfy
    — rename the leg in the sentence and the guard goes quiet while the row
    stays dead. That is the ``new-table-wiring-guard`` lesson this module's own
    header cites. A list of keys is resolvable, and a wrong key fails LOUDLY as
    ``absent_from_config`` rather than silently.

    DIFF-SCOPED and grandfathered, for the reason in the module header: the
    standing stock is annotated by hand, and a guard that fails on day one over
    a pre-existing population gets switched off.
    """
    strategies, accounts = load_config()
    if not strategies or not accounts:
        # ANNOUNCED, never silent — a guard that quietly declines to run is the
        # "green that checked nothing" failure `run_guards.py` has a rule about.
        print(
            "accrual-clock guard: SKIPPING — config/strategies.yaml or "
            "config/accounts.yaml could not be read, so no leg can be resolved. "
            "No row is checked by this run."
        )
        return 0

    bad: list[tuple[str, str, str]] = []
    checked = 0
    for rel in ALL_BACKLOGS:
        path = pathlib.Path(rel)
        if not path.exists():
            continue
        base_state, before = _kept_open_status_by_id(base_ref, rel)
        if base_state == ROWS_UNREADABLE:
            print(
                f"accrual-clock guard: SKIPPING {rel} — its copy at {base_ref} "
                f"did NOT PARSE. Its rows are NOT checked by this run. This is "
                f"'we could not look', a DIFFERENT fact from the absent case."
            )
            continue
        if not before:
            print(
                f"accrual-clock guard: SKIPPING {rel} — absent or empty at "
                f"{base_ref}. Its rows are NOT checked by this run."
            )
            continue
        for row in _load(path):
            rid = str(row.get("id") or "")
            status = str(row.get("status") or "")
            if not rid or status not in {"open", "kept_open"}:
                continue
            # Grandfathered: already carried at the base under the same status.
            if before.get(rid) == status:
                continue
            if not is_accrual_shaped(exit_text(row)):
                continue
            checked += 1
            legs = accrual_legs(row)
            if not legs:
                bad.append((
                    rel, rid,
                    f"waits for trades to accrue but names no `{ACCRUAL_LEGS_FIELD}`, "
                    f"so nothing can check whether its clock runs",
                ))
                continue
            for leg in legs:
                state = clock_state(leg, strategies, accounts)
                if state != CAN_RUN:
                    bad.append((rel, rid, f"waits on leg {leg!r} whose clock reads {state} — no trade will arrive"))

    if not bad:
        print(
            f"accrual-clock guard: OK — {checked} accrual-gated row(s) entered "
            f"or were filed in this diff, every one names legs that can trade."
        )
        return 0

    print(
        "::error::accrual-gated backlog row(s) whose clock CANNOT TICK. A row "
        "waiting for trades on a leg that is shadow-gated, disabled, unrouted or "
        "absent from config is a permanent resident wearing a field: its exit "
        "condition is present and vacuous. Measured 2026-09-02 over the eleven "
        "accrual rows in performance-review-backlog.json — NOT ONE was in the "
        "state its own text implied."
    )
    for rel, rid, why in bad:
        print(f"  {rel}: {rid} — {why}")
    print(
        f"\nFix: add `{ACCRUAL_LEGS_FIELD}: [<strategy keys from "
        f"config/strategies.yaml>]`, and make sure each one is enabled, "
        f"`execution: live`, and routed to a `mode: live` account. If the leg "
        f"is deliberately shadow/unrouted, the row is NOT waiting for accrual — "
        f"re-state its exit condition as the DECISION or MEASUREMENT it is "
        f"really waiting on, or close it."
    )
    return 1


def _census() -> int:
    bad: list[tuple[str, str, str]] = []
    total = 0
    for rel in BACKLOGS:
        path = pathlib.Path(rel)
        if not path.exists():
            continue
        for row in _load(path):
            rid = str(row.get("id") or "")
            if not rid:
                continue
            # Only OPEN rows matter: a closed row's criteria are moot.
            #
            # THROUGH THE CANONICAL PREDICATE, NOT A LOCAL SET
            # (BL-20260825-TWO-DEFINITIONS-OF-OPEN-DISAGREE-ABOUT-SEVEN-ROWS --
            # kept on one line so the id stays greppable). This was a hand-rolled
            # `{"resolved", "closed", "wontfix"}`, a SECOND definition of "is
            # this row open" living beside `backlog_counts.is_open_status`, and
            # the two disagreed. It missed `superseded`, `invalid`, `duplicate`,
            # `fixed`, `measured_no_action` and the `resolved_*` family — and,
            # precisely, it spelled the one closed status it DID try to handle
            # as `wontfix` while the corpus writes `wont_fix`, so the underscore
            # form fell straight through. That is the `WARNING` vs `WARN` shape
            # this repo has already paid for once.
            #
            # Measured 2026-08-25: SEVEN terminal rows (4 `wont_fix`, 2
            # `superseded`, 1 `invalid`) were being demanded to carry an exit
            # condition they will never need — 6.4% of a 109-finding census,
            # inflating the number a reader uses to judge whether the backlog is
            # improving.
            if not is_open_status(row.get("status")):
                continue
            total += 1
            why = _verdict(row)
            if why:
                bad.append((rel, rid, why))
    print(f"backlog-criteria census: {len(bad)} of {total} OPEN row(s) lack usable criteria.")
    rc = _report(bad, advisory=True)
    _kept_open_census()
    return rc


def _kept_open_census() -> None:
    """Re-measure the permanent-resident class, per criterion 4 of
    ``BL-20260825-KEPT-OPEN-ROWS-WITH-NO-EXIT-CONDITION-CAN-NEVER-BE-RETIRED``:
    *"Re-measure with the same predicate and report the count; the number, not a
    claim, closes this row."*

    Advisory and printed, never gating — the stock is grandfathered. What this
    changes is that the number is now produced by the SAME predicate the guard
    enforces, on demand, instead of being re-derived by hand on each pass (it
    was, twice: 2026-08-25 and 2026-09-01). A metric and its guard that are two
    separate derivations are free to disagree, and nobody would know which was
    right.
    """
    print("\nkept_open no-exit-condition census "
          "(BL-20260825-KEPT-OPEN-ROWS-WITH-NO-EXIT-CONDITION-CAN-NEVER-BE-RETIRED, criterion 4):")
    total_no_exit = 0
    for rel in ALL_BACKLOGS:
        path = pathlib.Path(rel)
        if not path.exists():
            print(f"  {rel}: ABSENT — not measured (this is 'we could not look', not zero)")
            continue
        rows = _load(path)
        kept = [r for r in rows if str(r.get("status") or "") == "kept_open"]
        no_exit = [r for r in kept if not exit_condition_fields(r)]
        total_no_exit += len(no_exit)
        print(f"  {rel}: kept_open {len(kept):4d} -> {len(no_exit):3d} with no exit condition")
    print(f"  TOTAL {total_no_exit}  "
          f"(39 when last measured by hand, 2026-09-01 Phase C; "
          f"42 on 2026-08-25 when the class was filed)")


def _load_is_open_status():
    """The ONE predicate for "is this backlog row still open".

    Imported from `scripts/reports/backlog_counts.py` rather than re-derived:
    that module's own header carries the incident record for why the token sets
    are shaped the way they are, and a copy here would be free to drift from it
    (it already had, silently).

    RAISES rather than falling back to a local approximation. A guard that
    quietly degrades to a weaker predicate when its canonical one is missing is
    the "cheaper to lie to than to satisfy" failure this file's own header
    names — the census would keep printing a number while measuring something
    else.
    """
    import importlib.util

    root = pathlib.Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "_backlog_counts", root / "scripts" / "reports" / "backlog_counts.py")
    if spec is None or spec.loader is None:  # pragma: no cover - unreachable
        raise RuntimeError(
            "check_backlog_criteria: cannot load the canonical open-status "
            "predicate from scripts/reports/backlog_counts.py. Refusing to "
            "re-derive it locally — that is how the two definitions drifted."
        )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.is_open_status


is_open_status = _load_is_open_status()


#: A date every ACCEPT fixture below carries, because `_verdict` now also
#: demands one. It is a fixture constant rather than inlined so the date
#: clause has exactly one place to be turned off, and the reject case that
#: omits it reads as deliberate rather than as an oversight.
_GOOD_DATE = "2026-09-12"

_GOOD_CRIT = ("Endpoint /api/bot/x returns field y for a rotated log, verified "
              "on the live fleet via the diag relay.")


def _self_test() -> int:
    """Prove the guard fails CLOSED on known-bad input, and passes a good row.

    A guard is only worth its green if it has been shown to go red. Both
    directions are asserted so a future edit that neuters `_verdict` is caught
    here rather than by the next row that slips through.
    """
    cases = [
        ({"id": "X"}, True, "missing field"),
        ({"id": "X", "resolution_criteria": ""}, True, "empty"),
        ({"id": "X", "resolution_criteria": "TBD"}, True, "placeholder"),
        ({"id": "X", "resolution_criteria": "  n/a  "}, True, "placeholder w/ whitespace"),
        ({"id": "X", "resolution_criteria": "fixed"}, True, "too short"),
        # `severity`/`tier` added 2026-08-13. NOTE the case below used to be the
        # "good row" and now correctly REJECTS — criteria alone is no longer
        # enough. Keeping it as a reject case is the regression test that the two
        # new fields are actually load-bearing and not merely declared.
        (
            {"id": "X", "resolution_criteria":
                "A relay shadow_stats read carries soak_start_basis per row and the "
                "registry_soak_source envelope, verified on the live fleet."},
            True, "good criteria but NO severity/tier",
        ),
        ({"id": "X", "resolution_criteria": _GOOD_CRIT, "tier": 1},
         True, "criteria+tier but no severity"),
        # SHAPE CASES (added 2026-08-25, closing criterion 2 of
        # BL-20260823-RESOLUTION-CRITERIA-HAS-TWO-TYPES-AND-ITERATING-IT-YIELDS-CHARACTERS).
        # `_verdict` used to do `str(raw)`, which is type-agnostic BY LUCK: a
        # dict or an int stringifies to something that clears the 40-char floor,
        # so a malformed row passed the guard whose whole job is malformed rows.
        ({"id": "X", "resolution_criteria": {"a": _GOOD_CRIT},
          "severity": "high", "tier": 1},
         True, "criteria is a DICT — must be rejected, not stringified"),
        ({"id": "X", "resolution_criteria": 12345678901234567890123456789012345678901234,
          "severity": "high", "tier": 1},
         True, "criteria is an INT long enough to clear the floor as a repr"),
        ({"id": "X", "resolution_criteria": [{"a": 1}],
          "severity": "high", "tier": 1},
         True, "criteria LIST containing a dict"),
        # THE ACCIDENTAL-PASS CASE, and the reason criteria_text joins on a
        # newline instead of using repr: the PROSE here is 21 chars, under the
        # 40-char floor, but `str(['too', 'short', 'x'])` is 26 and the older
        # bracket-and-quote punctuation is what a repr-based floor was counting.
        ({"id": "X", "resolution_criteria": ["too short", "also short"],
          "severity": "high", "tier": 1},
         True, "list whose PROSE is under the floor"),
        # ...and the list shape still passes on real prose, so the fix did not
        # simply outlaw the minority shape.
        ({"id": "X", "opened_at": _GOOD_DATE, "resolution_criteria": [_GOOD_CRIT, "And a second one."],
          "severity": "high", "tier": 1},
         False, "LIST of real criteria is accepted"),
        ({"id": "X", "opened_at": _GOOD_DATE, "resolution_criteria": [None, "", _GOOD_CRIT],
          "severity": "high", "tier": 1},
         False, "list with empty/None entries dropped, real prose kept"),
        ({"id": "X", "resolution_criteria": _GOOD_CRIT, "severity": "high"},
         True, "criteria+severity but no tier"),
        ({"id": "X", "resolution_criteria": _GOOD_CRIT, "severity": "P1", "tier": 1},
         True, "legacy severity spelling (P1) is refused on NEW rows"),
        ({"id": "X", "resolution_criteria": _GOOD_CRIT, "severity": "low-medium", "tier": 1},
         True, "hyphenated severity refused — five spellings of medium is the defect"),
        ({"id": "X", "resolution_criteria": _GOOD_CRIT, "severity": "high", "tier": "mixed"},
         True, "tier from which no 1/2/3 can be read"),
        ({"id": "X", "resolution_criteria": _GOOD_CRIT, "severity": "high", "tier": 1,
          "snoozed_until": "soon"}, True, "unparseable snoozed_until"),
        # DATE CASES (added 2026-09-12, closing criterion 4 of
        # BL-20260823-BACKLOG-TRIAGE-SEES-47-PERCENT-OF-THE-LIVE-ROWS). A row
        # nobody can date cannot be AGED, so it never surfaces in a stale-work
        # pass however long it sits.
        ({"id": "X", "resolution_criteria": _GOOD_CRIT, "severity": "high", "tier": 1},
         True, "otherwise-complete row with NO date field at all"),
        ({"id": "X", "resolution_criteria": _GOOD_CRIT, "severity": "high", "tier": 1,
          "opened_at": "sometime last week"},
         True, "date present but unparseable — we looked and it is broken"),
        # The SECOND schema is accepted, not merely tolerated: 352 live rows key
        # on `opened` and refusing them would be a migration disguised as a guard.
        ({"id": "X", "resolution_criteria": _GOOD_CRIT, "severity": "high", "tier": 1,
          "opened": "2026-09-12"},
         False, "the `opened` key is a first-class date, not a typo"),
        ({"id": "X", "resolution_criteria": _GOOD_CRIT, "severity": "high", "tier": 1,
          "filed_at": "2026-09-12T08:00:00+00:00"},
         False, "a long-tail key with a full ISO timestamp is accepted"),
        # ── accepts ────────────────────────────────────────────────────────
        ({"id": "X", "opened_at": _GOOD_DATE, "resolution_criteria": _GOOD_CRIT, "severity": "high", "tier": 1},
         False, "complete row"),
        ({"id": "X", "opened_at": _GOOD_DATE, "resolution_criteria": _GOOD_CRIT, "severity": "MEDIUM", "tier": "Tier-2"},
         False, "case-insensitive severity + Tier- prefixed tier"),
        ({"id": "X", "opened_at": _GOOD_DATE, "resolution_criteria": _GOOD_CRIT, "severity": "low",
          "tier": "1 (research; any promotion past candidate is Tier-3/operator)"},
         False, "trailing tier annotation is allowed — only unreadable is refused"),
        ({"id": "X", "opened_at": _GOOD_DATE, "resolution_criteria": _GOOD_CRIT, "severity": "high", "tier": 1,
          "snoozed_until": "2026-09-30"}, False, "valid ISO snooze"),
        ({"id": "X", "opened_at": _GOOD_DATE, "resolution_criteria": _GOOD_CRIT, "severity": "high", "tier": 1,
          "snoozed_until": None}, False, "snoozed_until null is fine — the field is optional"),
    ]
    # ── the kept_open exit-condition predicate (criterion 3) ─────────────
    # Asserted in BOTH directions. A guard is only worth its green once it has
    # been SHOWN to go red, and the reject cases below are what stop a future
    # edit from quietly neutering `exit_condition_fields` into a pass-through.
    exit_cases = [
        ({"id": "X"}, False, "no fields at all -> NO exit condition"),
        ({"id": "X", "title": "something is broken"}, False,
         "a title is not an exit condition"),
        ({"id": "X", "resolution_criteria": "TBD"}, False, "placeholder"),
        ({"id": "X", "next_action": "fix it"}, False, "under the length floor"),
        ({"id": "X", "resolution_criteria": {"a": _GOOD_CRIT}}, False,
         "dict shape yields no prose — must NOT stringify into a pass"),
        ({"id": "X", "resolution_criteria": ["too short", "also short"]}, False,
         "list whose PROSE is under the floor (repr punctuation must not count)"),
        ({"id": "X", "snoozed_until": "soon"}, False,
         "unparseable snooze is not an exit condition"),
        ({"id": "X", "resolution_criteria": _GOOD_CRIT}, True,
         "resolution_criteria"),
        ({"id": "X", "trigger_condition": _GOOD_CRIT}, True,
         "trigger_condition — 361 corpus rows state their exit here, not in "
         "resolution_criteria"),
        ({"id": "X", "what_to_check": _GOOD_CRIT}, True, "what_to_check"),
        ({"id": "X", "next_action": _GOOD_CRIT}, True, "next_action"),
        ({"id": "X", "snoozed_until": "2026-09-30"}, True,
         "a real ISO snooze IS an exit condition — come back then"),
        ({"id": "X", "resolution_criteria": [_GOOD_CRIT]}, True, "list shape"),
    ]
    failures = 0
    for row, should_have, label in exit_cases:
        got = bool(exit_condition_fields(row))
        ok = got == should_have
        print(f"  [{'PASS' if ok else 'FAIL'}] exit-condition {label}: "
              f"expected {'stated' if should_have else 'ABSENT'}, got "
              f"{'stated' if got else 'ABSENT'}")
        failures += 0 if ok else 1

    for row, should_fail, label in cases:
        got = _verdict(row) is not None
        ok = got == should_fail
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}: "
              f"expected {'reject' if should_fail else 'accept'}, got "
              f"{'reject' if got else 'accept'}")
        failures += 0 if ok else 1

    # THE TWO DATE REASONS MUST BE TELLABLE APART IN THE OUTPUT, not just in
    # the verdict. The case table above grades reject-vs-accept only, so a
    # change that folded `unparseable` into `unstated` would keep every row
    # rejected and slip through it -- MEASURED: a planted `pass` on the
    # unparseable branch left all cases green because the outer `return` still
    # fired. A reader told "no date field" about a row that HAS a broken date
    # goes and adds a second one. `we looked and it is broken` and `we did not
    # look` are different repairs, so they get different sentences.
    _unstated = _verdict({"id": "X", "resolution_criteria": _GOOD_CRIT,
                          "severity": "high", "tier": 1}) or ""
    _unparseable = _verdict({"id": "X", "resolution_criteria": _GOOD_CRIT,
                             "severity": "high", "tier": 1,
                             "opened_at": "sometime last week"}) or ""
    if "no date field" not in _unstated:
        print(f"  [FAIL] a row with NO date key must SAY so; got {_unstated[:80]!r}")
        failures += 1
    if "will not parse" not in _unparseable or "no date field" in _unparseable:
        print("  [FAIL] an UNPARSEABLE date must not be reported as an ABSENT one; "
              f"got {_unparseable[:80]!r}")
        failures += 1
    if _unstated == _unparseable:
        print("  [FAIL] the two date reasons are byte-identical — collapsed")
        failures += 1

    # ── THE BASE MUST BE RESOLVED BEFORE ANY ROW IS BLAMED ──────────────────
    # Measured 2026-09-13: an unresolvable base produced 547 findings naming
    # individual rows, under a headline about rows not being WORKABLE. The base
    # was the whole cause and the headline never mentioned it.
    import contextlib
    import io
    import os
    import tempfile

    _repo = pathlib.Path(__file__).resolve().parents[2]

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc_bad = main(["--base", "no-such-ref-anywhere-000-selftest"])
    out_bad = buf.getvalue()
    if rc_bad == 0:
        print("  [FAIL] an unresolvable base returned 0 — a verdict over nothing")
        failures += 1
    if "could not be resolved" not in out_bad:
        print("  [FAIL] an unresolvable base did not SAY the base was the cause; "
              f"got {out_bad[:120]!r}")
        failures += 1
    # THE ONE THAT MATTERS: it must grade NOTHING, not grade everything badly.
    blamed = [ln for ln in out_bad.splitlines()
              if ln.startswith("  docs/claude/")]
    if blamed:
        print(f"  [FAIL] an unresolvable base still blamed {len(blamed)} row(s); "
              "refusing means grading nothing, not failing everything")
        failures += 1
    if not failures:
        print("  [ok]   an unresolvable base is REFUSED, names the ref, and "
              "blames no row")

    # POSITIVE CONTROL. Without it, a guard that refused EVERY base would pass
    # all three assertions above and be strictly worse than the defect.
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        rc_ok = main(["--base", "HEAD"])
    out_ok = buf2.getvalue()
    if "could not be resolved" in out_ok:
        print("  [FAIL] a REAL base was refused — the refusal is not keyed on "
              "the ref being unresolvable")
        failures += 1
    elif rc_ok != 0:
        print(f"  [FAIL] a real base did not grade cleanly (rc={rc_ok})")
        failures += 1
    else:
        print("  [ok]   a REAL base still grades, so the refusal is not "
              "'refuse everything'")

    # AND THE TEST IS `ref_exists`, NOT "is the file there". A file absent at a
    # REAL base is legitimately new and its rows MUST be graded — that is the
    # case this whole guard exists for.
    #
    # ⚠️ THE FIRST VERSION OF THIS CONTROL ONLY EXERCISED THE PRIMITIVE, and a
    # planted defect keying the refusal on `_load_at_ref(...)` being empty
    # sailed past it: at HEAD the file is present so nothing refused, and at a
    # bogus ref it is empty so the refusal fired — behaviour identical to the
    # correct implementation, because no input told the two apart. Discriminating
    # them needs a ref that EXISTS and genuinely lacks the file, so this builds
    # one: a real commit over the EMPTY TREE.
    empty_tree = subprocess.run(
        ["git", "hash-object", "-t", "tree", "/dev/null"],
        capture_output=True, text=True, cwd=_repo)
    empty_commit = subprocess.run(
        ["git", "commit-tree", empty_tree.stdout.strip(), "-m", "selftest empty base"],
        capture_output=True, text=True, cwd=_repo,
        env={**os.environ,
             "GIT_AUTHOR_NAME": "selftest", "GIT_AUTHOR_EMAIL": "s@invalid",
             "GIT_COMMITTER_NAME": "selftest", "GIT_COMMITTER_EMAIL": "s@invalid"})
    empty_ref = empty_commit.stdout.strip()
    if not empty_ref:
        print("  [FAIL] could not build an empty-tree commit, so the "
              "ref-vs-file distinction is UNTESTED — not passed")
        failures += 1
    else:
        buf3 = io.StringIO()
        with contextlib.redirect_stdout(buf3):
            main(["--base", empty_ref])
        out_empty = buf3.getvalue()
        # The ref EXISTS, so it must NOT be refused. Grading every row as new is
        # the correct answer against a genuinely empty base, so the assertion is
        # on the REFUSAL and deliberately not on the exit code.
        if "could not be resolved" in out_empty:
            print("  [FAIL] a REAL ref that merely lacks the file was refused — "
                  "the refusal is keyed on the FILE, not the REF, and a "
                  "genuinely new register would be rejected")
            failures += 1
        else:
            print("  [ok]   a REAL ref lacking the file is GRADED, not refused "
                  "— the refusal keys on the ref")

    # ── FILE-STATE CONTROLS: a backlog that does not PARSE ──────────────────
    # MEASURED on origin/main @7f832e8c0 before any of this was written, with a
    # conflict marker inserted into docs/claude/health-review-backlog.json:
    #   HEAD copy corrupt -> three OK lines and rc=0, over 1574 unread rows
    #   BASE copy corrupt -> rc=1 blaming 393 PRE-EXISTING rows this diff never
    #                        touched, under a headline about rows "not WORKABLE"
    # Both directions from one collapse, so both get a control, and each has its
    # positive control beside it — a guard that only demonstrates its failures
    # cannot show it is not simply always-red.
    _cwd = os.getcwd()
    try:
        with tempfile.TemporaryDirectory() as _d:
            _root = pathlib.Path(_d)
            (_root / "docs/claude").mkdir(parents=True)
            _target = _root / ALL_BACKLOGS[0]
            os.chdir(_root)

            def _run_main(args: list[str]) -> tuple[int, str]:
                _b = io.StringIO()
                with contextlib.redirect_stdout(_b):
                    _rc = main(args)
                return _rc, _b.getvalue()

            # (A) HEAD side: present and unparseable -> REFUSED, nothing graded.
            _target.write_text('{"items": [{"id": "A"} THIS IS NOT JSON')
            rc_a, out_a = _run_main(["--base", "irrelevant-the-refusal-precedes-it"])
            if rc_a == 0 or "does NOT parse" not in out_a:
                print("  [FAIL] a backlog that is PRESENT and does not parse "
                      f"was graded rather than refused (rc={rc_a}, and the "
                      "refusal message is absent) — the 1574-row vacuous OK")
                failures += 1
            elif "guard: OK" in out_a:
                print("  [FAIL] the run refused AND still printed a 'guard: OK' "
                      "line — the verdict is what a human reads")
                failures += 1
            else:
                print("  [ok]   an unparseable backlog on disk REFUSES, and "
                      "prints no OK line")

            # ...and --all goes through the same door. The census had the right
            # instinct for an ABSENT file and the wrong one for a corrupt one:
            # it printed `kept_open 0 -> 0` for a file it could not parse.
            rc_all, out_all = _run_main(["--all"])
            if rc_all == 0 or "does NOT parse" not in out_all:
                print(f"  [FAIL] --all graded an unparseable backlog (rc={rc_all})")
                failures += 1
            else:
                print("  [ok]   --all refuses it too")

            # (B) POSITIVE CONTROL: a VALID but EMPTY backlog is not a parse
            # failure and must NOT be refused. Asserted on the MESSAGE, not the
            # exit code — the bogus ref that follows legitimately refuses for a
            # different reason, and conflating the two is what this file is for.
            _target.write_text('{"items": []}')
            _, out_b = _run_main(["--base", "no-such-ref-anywhere-000"])
            if "does NOT parse" in out_b:
                print("  [FAIL] an EMPTY but valid backlog was reported as "
                      "unparseable — empty is a real reading, not a failure")
                failures += 1
            else:
                print("  [ok]   an empty-but-valid backlog is not a parse failure")
    finally:
        os.chdir(_cwd)

    # (C) BASE side: the copy at the base does not parse. Needs a REAL repo with
    # a REAL history, because the whole point is a ref that resolves and a file
    # that is there and corrupt AT IT — nothing weaker tells the cases apart.
    _cwd = os.getcwd()
    try:
        with tempfile.TemporaryDirectory() as _d:
            _root = pathlib.Path(_d)
            (_root / "docs/claude").mkdir(parents=True)
            _target = _root / ALL_BACKLOGS[0]
            _env = {**os.environ,
                    "GIT_AUTHOR_NAME": "selftest", "GIT_AUTHOR_EMAIL": "s@invalid",
                    "GIT_COMMITTER_NAME": "selftest", "GIT_COMMITTER_EMAIL": "s@invalid"}

            def _git(*a: str) -> None:
                subprocess.run(["git", *a], cwd=_root, env=_env,
                               capture_output=True, text=True, check=False)

            _git("init", "-q")
            _target.write_text('{"items": [{"id": "A"} NOT JSON')
            _git("add", "-A")
            _git("commit", "-qm", "base: corrupt")
            # The head copy is FINE and carries one row that would FAIL grading
            # if it were treated as new. On the old code it was, 393 times over.
            _target.write_text(json.dumps({"items": [{"id": "A", "status": "open"}]}))
            _git("add", "-A")
            _git("commit", "-qm", "head: repaired")
            os.chdir(_root)
            _b = io.StringIO()
            with contextlib.redirect_stdout(_b):
                rc_c = main(["--base", "HEAD~"])
            out_c = _b.getvalue()
            if "SKIPPING" not in out_c or "did NOT PARSE" not in out_c:
                print("  [FAIL] an unparseable BASE copy was not announced as "
                      f"skipped (rc={rc_c}) — see the 393-row false blame")
                failures += 1
            elif ": A —" in out_c or rc_c != 0:
                print("  [FAIL] rows were BLAMED against a base that could not "
                      f"be read (rc={rc_c})")
                failures += 1
            elif "every NEW backlog row" in out_c:
                print("  [FAIL] the OK line claimed coverage over every new row "
                      "on a run that skipped a file")
                failures += 1
            else:
                print("  [ok]   an unparseable BASE copy is SKIPPED loudly, "
                      "blames nothing, and the OK line says so")
    finally:
        os.chdir(_cwd)

    if failures:
        print("self-test FAILED — the guard does not fail closed.")
        return 1
    print("self-test OK — rejects empty/placeholder/too-short criteria, missing or\n           legacy severity, unreadable tier, and unparseable snooze; accepts a\n           complete row.")
    return 0


def _backlogs_that_do_not_parse() -> list[str]:
    """Declared backlogs that are PRESENT on disk and do NOT parse.

    ⚠️ **THE HEAD SIDE IS A REFUSAL, NOT A SKIP, AND THE ASYMMETRY WITH
    `_check_new_rows` IS DELIBERATE.** On the BASE side an unparseable copy is
    main's problem and the PR in front of it may well be the repair, so skipping
    is the lesser harm. On the HEAD side there is no benign reading: the file
    this run was asked to GRADE cannot be read, so every row in it is ungraded,
    and the old code said so by printing OK.

    ⚠️ **AN EMPTY BACKLOG IS NOT A PARSE FAILURE** — `{"items": []}` reads
    `ROWS_READ` with no rows and is waved through, which is the positive control
    in the self-test. Only a file that does not parse, or whose top-level shape
    holds no rows array at all, lands here.

    ⚠️ **CI GOING RED ANYWAY IS NOT A REASON TO SKIP THIS.** As of 2026-09-13
    `register-id-guard` also fails on an unparseable register, so the PR would
    be red either way — but this guard would still have PRINTED a verdict over
    rows it never read, and the verdict is what a human reads. The fix is about
    this guard not stating a falsehood, not only about the build colour.
    """
    broken: list[str] = []
    for rel in ALL_BACKLOGS:
        path = pathlib.Path(rel)
        if not path.exists():
            continue
        if _read_file(path)[0] == ROWS_UNREADABLE:
            broken.append(rel)
    return broken


def _refuse_unparseable(broken: list[str]) -> None:
    print("::error::backlog-criteria: a declared backlog is PRESENT on disk and "
          "does NOT parse, so NOTHING was graded. This is 'we could not look' — "
          "it is not 'every row is fine', which is what this guard printed for "
          "this case until 2026-09-13 (measured: three OK lines over a 1574-row "
          "file nothing had read).")
    for rel in broken:
        print(f"  - {rel}")
    print("\nFix: restore the file — `git checkout` the last parseable copy, or "
          "resolve the conflict row-aware via scripts/ops/merge_json_register.py "
          "— and re-run. Do NOT hand-resolve a register conflict line-by-line.")


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", help="git ref to diff against; only NEW rows are checked")
    ap.add_argument("--all", action="store_true", help="advisory census over all OPEN rows")
    ap.add_argument("--self-test", action="store_true", help="prove the guard fails closed")
    args = ap.parse_args(list(argv) if argv is not None else None)
    # The verdict below is about the COMMITTED tree. Say so when that is
    # not the tree you edited. See
    # BL-20260917-THE-DIRTY-TREE-NOTICE-LIVES-ONLY-IN-RUN-GUARDS-SO-ALL-18-DIRECTLY-INVOCABLE-DIFF-SCOPED-GUARDS-STILL-GRADE-THE-WRONG-TREE-SILENTLY
    import pathlib  # noqa: PLC0415 — local, so importing this module stays free
    import sys as _sys  # noqa: PLC0415
    _sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "ci"))
    import _dirty_tree  # noqa: E402,PLC0415 — path shim above
    _dirty_tree.warn()


    if args.self_test:
        return _self_test()

    # ── THE FILES THIS RUN GRADES MUST BE READABLE BEFORE ANYTHING IS GRADED ──
    # Applies to BOTH --all and --base: the census reads the same files through
    # the same `_load`, and printed `kept_open 0 -> 0` for a file it could not
    # parse, beside a line for a MISSING file that correctly says "this is 'we
    # could not look', not zero". The census had the right instinct for absence
    # and the wrong one for corruption.
    if args.all or args.base:
        broken = _backlogs_that_do_not_parse()
        if broken:
            _refuse_unparseable(broken)
            return 2

    if args.all:
        return _census()
    if args.base:
        # ── THE BASE MUST BE RESOLVABLE BEFORE ANY ROW IS GRADED ────────────
        # MEASURED 2026-09-13: `--base no-such-ref-anywhere-000` emitted **547
        # findings**, each naming an individual backlog row, under the headline
        # "backlog row(s) that are not WORKABLE — missing or unusable
        # resolution_criteria / severity / tier". Not one word of that headline
        # is about the base, and the base was the entire cause: `_load_at_ref`
        # returns [] for an unresolvable ref exactly as it does for a file that
        # is genuinely new, so every pre-existing row read as one this diff had
        # just added.
        #
        # ⚠️ THIS IS UNPROVENANCED DIAGNOSTIC OUTPUT, SUB-CLASS A — a failure
        # message naming a cause no code path tested. The repo's own remedy for
        # it is to branch on the actual failure STAGE rather than reword the
        # label, which is what this does: the ref is checked once, before
        # anything is graded, and a bad one is REFUSED rather than described as
        # 547 bad rows.
        #
        # ⚠️ `ref_exists` IS THE RIGHT TEST AND "the file is absent" IS NOT.
        # A file absent at a REAL base is legitimately new, and grading its rows
        # is correct — that is the case this guard exists for. Only an
        # unresolvable REF means *we could not look*. `_git_base.read_at` keeps
        # those two apart for the same reason.
        #
        # ⚠️ THAT DEFERRAL IS DISCHARGED — this comment used to end "KNOWN, AND
        # DELIBERATELY NOT FIXED HERE: `_load_at_ref` still collapses a JSON
        # PARSE failure at a readable ref ... that case has not been observed
        # and has no measurement behind it". It is observed and measured now
        # (2026-09-13, on `origin/main` @`7f832e8c0`): a corrupt BASE copy blamed
        # **393 pre-existing rows**, and a corrupt HEAD copy produced three
        # "OK" lines over **1574 rows** nothing had read. Both are fixed —
        # `_load_at_ref` returns a state and the base side SKIPS loudly;
        # `_backlogs_that_do_not_parse` below refuses on the head side.
        if not _git_base.ref_exists(args.base):
            print(f"::error::backlog-criteria: the base ref {args.base!r} could "
                  "not be resolved, so NOTHING was graded. This is 'we could "
                  "not look', not 'every row is fine' and not 'every row is "
                  "broken' — without a base every existing row would read as "
                  "one this diff just added.")
            return 2

        # BOTH diff-scoped checks under one invocation, and the return codes are
        # OR-ed rather than short-circuited so a PR sees every failing row at
        # once instead of re-running to find the next one (the reason
        # run_guards.py exists at all).
        rc_new = _check_new_rows(args.base)
        rc_kept = _check_kept_open_transitions(args.base)
        rc_accrual = _check_accrual_rows(args.base)
        return rc_new or rc_kept or rc_accrual
    ap.error("one of --base, --all or --self-test is required")
    return 2


if __name__ == "__main__":
    sys.exit(main())
