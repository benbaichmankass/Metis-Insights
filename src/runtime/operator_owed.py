"""Is this operator-owed item MOVING, and does it genuinely need a human?

WHY THIS EXISTS
===============
`BL-20260825-OPERATOR-OWED-ITEMS-HAVE-NO-REGISTER-NO-AGE-AND-NO-ESCALATION`.
Every session ended by listing "operator-owed" items in a board comment and a
sprint log, and that was the ONLY mechanism. STATE THE POPULATION: the three
sessions that ran on 2026-08-25 — `01X2zMCh` (13:33Z), `qhpxyh` (14:27Z) and
`018aKyS3` (16:56Z) — each closed by handing forward the SAME FOUR items, with
**zero state change on any of them**. Three sessions, one day, n=3 hand-offs of
one item set. A `grep` for `operator-owed` / `operator_pending` /
`awaiting_operator` across `src/`, `scripts/` and `docs/claude/*.md` returned
**zero**: the register did not exist.

Prose in a board comment has no age, so nothing rots visibly; it has no owner
class, so an item that merely DEFAULTED to a human is indistinguishable from
one that genuinely needs one; and it has no exit, so the list only grows. That
is the desensitised-alarm P1 in its slowest form, and it puts the operator in
the loop BY DEFAULT — which the autonomy mandate explicitly forbids.

WHY A COMMITTED JSON FILE, and not the two alternatives
=======================================================
The filing row deliberately proposed no design, so the choice is made here and
recorded rather than assumed.

* **Not a field on the existing backlog rows.** The backlogs hold DEFECTS
  (`CLAUDE-RULES-CANONICAL` § "Backlog governance", rule 2). Most operator-owed
  items are not defects — a token rotation, a balance report, a sizing
  judgement — and admitting them would re-create the un-workable-row problem
  that rule exists to stop.
* **Not a labelled-issue queue.** Instant visibility, but it cannot be read
  deterministically by CI, and — decisively — it cannot MEASURE carry. The
  requirement is a check that fails when an item is carried across N sessions
  **without a state change**; a comment thread can only be asserted about.
* **A committed JSON file** gives age, owner class, an exit, and — the part
  that makes requirement (d) measurable rather than asserted — a **git
  history**. Each commit that touches the register in which an item's own
  content did NOT change is one carry of that item, derived from
  `git log`, not from anybody's self-report.

⚠️ WHAT THE CARRY COUNT CANNOT SEE, stated because a denominator nobody states
is how this class of thing rots. A session that never touches the register at
all produces no commit, so it is invisible to the count — the measurement
UNDER-reports and can never over-report. That residual is exactly why age is a
SECOND, independent trip path below rather than a nicer way of saying the same
thing. It follows `silent_refusal_alert.CAUSE_MIN_ROWS`: an additional trip
path can only ADD escalation, never suppress one.

STATES, never collapsed
=======================
`moved`              the item's own content changed in the newest register commit
`carried`            unchanged across >= 1 and < the limit register commits
`escalate_carried`   carried unchanged across >= the limit — the (d) failure
`escalate_aged`      no state change for longer than its severity allows
`not_measurable`     the register carries no commit covering this item yet, so
                     no carry EXISTS to count. **We did not look.** Emphatically
                     not `moved`, and never a pass — a register whose every item
                     reads `not_measurable` is an unproven register, which is
                     precisely what the filing row means by "treat a green with
                     zero items ever moved as unproven, not as success".
`snoozed`            deferred behind a date AND a named trigger event
`resolved`           terminal

`not_measurable` is the state this module exists to keep separate. Collapsing
it into `moved` would make a brand-new register — the one state in which it has
demonstrated nothing — report perfect health.

OWNER CLASS — the distinction that is the whole point
=====================================================
`genuinely_human`   the work needs a person: `secret_origination` (only a human
                    may mint the value), `physical_or_broker` (an action at a
                    venue/console we hold no API for), `judgement` (a decision
                    the system may not take for itself).
`defaulted_to_human` a wire exists, or could, and nobody built it.
`unclassified`      nobody said. **Not a synonym for genuinely-human** — an
                    unclassified item silently reads as somebody else's problem,
                    which is how the MHG over-cover sat in the "human" column
                    while `cancel-ib-order` and `attach-ib-target` had shipped
                    as system-actions months earlier.

⚠️ "WE AUTOMATED IT ONCE AND GOT IT WRONG, SO A HUMAN OWNS IT FOREVER" IS THE
ANTI-PATTERN THIS MODULE NAMES. A `defaulted_to_human` item whose
`cannot_automate_reason` rests on a failed remediation must ALSO name a
`tested_decision_function` — a real file — because a failed attempt distrusts
the SELECTION, not the mechanism, and this repo already has the remedy in
`src/runtime/protection_reassert.py`: make the decision a pure function with
non-collapsed states "so the policy is arguable in tests rather than against a
live position".

This module is the DECISION only, and pure. It reads no file, runs no git, and
opens no socket; `scripts/ci/check_operator_owed.py` supplies the measured
carry count and enforces the verdict.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional, Sequence

__all__ = [
    "STATE_MOVED", "STATE_CARRIED", "STATE_ESCALATE_CARRIED",
    "STATE_ESCALATE_AGED", "STATE_NOT_MEASURABLE", "STATE_SNOOZED",
    "STATE_RESOLVED", "ALL_STATES",
    "OWNER_SECRET", "OWNER_PHYSICAL", "OWNER_JUDGEMENT",
    "OWNER_DEFAULTED", "OWNER_UNCLASSIFIED",
    "GENUINELY_HUMAN_CLASSES", "ALL_OWNER_CLASSES",
    "DEFAULT_CARRY_LIMIT", "AGE_LIMIT_DAYS_BY_SEVERITY",
    "OPEN_STATUSES", "TERMINAL_STATUSES",
    "grade_item", "validate_item", "is_escalation",
]

#: The filing row, spelled ONCE so it can never be split across a wrapped
#: string literal -- a truncated id is invisible to `check_backlog_refs`
#: and is a lapse this repo has recorded before.
_ROW_ID = (
    "BL-20260825-OPERATOR-OWED-ITEMS-HAVE-NO-REGISTER-NO-AGE-AND-NO-ESCALATION"
)

STATE_MOVED = "moved"
STATE_CARRIED = "carried"
STATE_ESCALATE_CARRIED = "escalate_carried"
STATE_ESCALATE_AGED = "escalate_aged"
STATE_NOT_MEASURABLE = "not_measurable"
STATE_SNOOZED = "snoozed"
STATE_RESOLVED = "resolved"

ALL_STATES = (
    STATE_MOVED, STATE_CARRIED, STATE_ESCALATE_CARRIED, STATE_ESCALATE_AGED,
    STATE_NOT_MEASURABLE, STATE_SNOOZED, STATE_RESOLVED,
)

_ESCALATIONS = (STATE_ESCALATE_CARRIED, STATE_ESCALATE_AGED)

OWNER_SECRET = "secret_origination"
OWNER_PHYSICAL = "physical_or_broker"
OWNER_JUDGEMENT = "judgement"
OWNER_DEFAULTED = "defaulted_to_human"
OWNER_UNCLASSIFIED = "unclassified"

#: The three ways an item may genuinely need a person. Anything else is either
#: `defaulted_to_human` or nobody has said.
GENUINELY_HUMAN_CLASSES = (OWNER_SECRET, OWNER_PHYSICAL, OWNER_JUDGEMENT)
ALL_OWNER_CLASSES = GENUINELY_HUMAN_CLASSES + (OWNER_DEFAULTED, OWNER_UNCLASSIFIED)

#: Register commits an item may be carried unchanged before it escalates.
#:
#: ⚠️ A CHOSEN value with a measured basis, not a tuned one. n=1 incident:
#: 3 of 3 sessions on 2026-08-25 carried the same four items with zero state
#: change. A limit of 2 fires on the SECOND carry — inside the observed
#: incident rather than after it. A limit of 3 would have graded that entire
#: day green, which is the failure being fixed.
DEFAULT_CARRY_LIMIT = 2

#: Days without a state change before an item escalates on age alone, by
#: severity. The SECOND, independent trip path (see the module header): carry
#: count under-reports whenever a session skips the register entirely, and this
#: one needs no session to touch anything. Chosen, not tuned — the observed
#: incident ran its full three carries inside ONE day, so `critical` is set at
#: one day to fire within a repeat of it.
AGE_LIMIT_DAYS_BY_SEVERITY = {
    "critical": 1.0,
    "high": 3.0,
    "medium": 7.0,
    "low": 14.0,
}
_DEFAULT_AGE_LIMIT_DAYS = 3.0

OPEN_STATUSES = ("open", "dispatched", "snoozed")
TERMINAL_STATUSES = ("resolved", "withdrawn")

#: Present but saying nothing. Same list as the backlog guard's, and for the
#: same reason: a guard cheaper to lie to than to satisfy is worse than none.
_PLACEHOLDERS = {
    "", "tbd", "tba", "n/a", "na", "none", "null", "-", "--", "?", "???",
    "see above", "see below", "unknown", "todo", "to do", "pending", "wip",
}

_MIN_REASON_CHARS = 40


def _is_placeholder(value: Any) -> bool:
    if value is None:
        return True
    return str(value).strip().casefold() in _PLACEHOLDERS


def parse_ts(value: Any) -> Optional[_dt.datetime]:
    """ISO-8601 -> aware UTC datetime, or ``None`` when it cannot be read.

    ``None`` means *we could not read this timestamp*, and every caller here
    keeps that apart from "it is old" — an undateable item is graded
    `not_measurable` on the age axis rather than given a fabricated age.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.astimezone(_dt.timezone.utc)


def is_escalation(state: str) -> bool:
    """Does this state mean the standing check must FAIL?"""
    return state in _ESCALATIONS


def age_limit_days(severity: Any) -> float:
    return AGE_LIMIT_DAYS_BY_SEVERITY.get(
        str(severity or "").strip().casefold(), _DEFAULT_AGE_LIMIT_DAYS)


def grade_item(
    item: Dict[str, Any],
    *,
    carries_unchanged: Optional[int],
    now: _dt.datetime,
    carry_limit: int = DEFAULT_CARRY_LIMIT,
) -> Dict[str, Any]:
    """Grade ONE register item. Pure.

    ``carries_unchanged`` is the number of register commits since this item's
    own content last changed, measured from git by the caller. ``None`` means
    the caller could not measure it — which is `not_measurable`, never zero.
    A zero is a real reading ("the newest register commit changed this item").
    """
    out: Dict[str, Any] = {
        "id": item.get("id"),
        "state": STATE_NOT_MEASURABLE,
        "carries_unchanged": carries_unchanged,
        "carry_limit": carry_limit,
        "age_days": None,
        "age_limit_days": age_limit_days(item.get("severity")),
        "age_basis": None,
        "escalates": False,
        "reason": None,
    }

    status = str(item.get("status") or "").strip().casefold()
    if status in TERMINAL_STATUSES:
        out["state"] = STATE_RESOLVED
        out["reason"] = f"status={status or '(unset)'}"
        return out

    # A snooze is a real disposition, but only with BOTH a date and a named
    # trigger event — the backlog governance rule, applied here for the same
    # reason: a date alone is a mute button.
    snoozed_until = parse_ts(item.get("snoozed_until"))
    if snoozed_until is not None and not _is_placeholder(item.get("snooze_trigger")):
        if snoozed_until > now:
            out["state"] = STATE_SNOOZED
            out["reason"] = f"snoozed until {snoozed_until.isoformat()}"
            return out

    # --- the AGE axis (independent trip path) ---------------------------
    changed_at = parse_ts(item.get("last_state_change_at")) or parse_ts(
        item.get("opened_at"))
    if changed_at is not None:
        out["age_basis"] = (
            "last_state_change_at" if parse_ts(item.get("last_state_change_at"))
            else "opened_at")
        out["age_days"] = max(0.0, (now - changed_at).total_seconds() / 86400.0)

    # --- the CARRY axis -------------------------------------------------
    if carries_unchanged is None:
        # We could not measure carry. Age may still decide, and if it cannot
        # either, the honest answer is `not_measurable`.
        if out["age_days"] is not None and out["age_days"] > out["age_limit_days"]:
            out["state"] = STATE_ESCALATE_AGED
            out["escalates"] = True
            out["reason"] = (
                f"no state change in {out['age_days']:.2f}d "
                f"(limit {out['age_limit_days']:.2f}d for severity "
                f"{item.get('severity')!r}); carry not measurable")
            return out
        out["reason"] = "carry not measurable from git history"
        return out

    if carries_unchanged >= carry_limit:
        out["state"] = STATE_ESCALATE_CARRIED
        out["escalates"] = True
        out["reason"] = (
            f"carried unchanged across {carries_unchanged} register commits "
            f"(limit {carry_limit})")
        return out

    if out["age_days"] is not None and out["age_days"] > out["age_limit_days"]:
        out["state"] = STATE_ESCALATE_AGED
        out["escalates"] = True
        out["reason"] = (
            f"no state change in {out['age_days']:.2f}d (limit "
            f"{out['age_limit_days']:.2f}d for severity {item.get('severity')!r})")
        return out

    if carries_unchanged == 0:
        out["state"] = STATE_MOVED
        out["reason"] = "changed in the newest register commit"
        return out

    out["state"] = STATE_CARRIED
    out["reason"] = (
        f"carried unchanged across {carries_unchanged} register commit(s), "
        f"under the limit of {carry_limit}")
    return out


def validate_item(item: Dict[str, Any]) -> List[str]:
    """Structural problems with one item. Empty list == well-formed.

    This refuses the empty and the obviously vacuous. It does NOT judge quality
    and does not pretend to — the same scope `check_backlog_criteria.py` sets.
    """
    problems: List[str] = []
    item_id = item.get("id") or "(no id)"

    for field in ("id", "title", "opened_at", "severity", "status"):
        if _is_placeholder(item.get(field)):
            problems.append(f"{item_id}: missing or placeholder {field!r}")

    severity = str(item.get("severity") or "").strip().casefold()
    if severity and severity not in AGE_LIMIT_DAYS_BY_SEVERITY:
        problems.append(
            f"{item_id}: severity {item.get('severity')!r} is not one of "
            f"{sorted(AGE_LIMIT_DAYS_BY_SEVERITY)}")

    status = str(item.get("status") or "").strip().casefold()
    if status and status not in OPEN_STATUSES + TERMINAL_STATUSES:
        problems.append(
            f"{item_id}: status {item.get('status')!r} is not one of "
            f"{sorted(OPEN_STATUSES + TERMINAL_STATUSES)}")

    if parse_ts(item.get("opened_at")) is None and not _is_placeholder(
            item.get("opened_at")):
        problems.append(f"{item_id}: opened_at is not readable ISO-8601")

    owner_class = str(item.get("owner_class") or "").strip()
    if owner_class not in ALL_OWNER_CLASSES:
        problems.append(
            f"{item_id}: owner_class {item.get('owner_class')!r} is not one of "
            f"{sorted(ALL_OWNER_CLASSES)}")
        return problems

    if status in TERMINAL_STATUSES:
        if _is_placeholder(item.get("resolution")):
            problems.append(
                f"{item_id}: status={status} needs a non-placeholder "
                f"'resolution' saying what actually happened")
        return problems

    # An item may not sit unclassified: that reads as somebody else's problem,
    # which is the exact miscategorisation this register exists to end.
    if owner_class == OWNER_UNCLASSIFIED:
        problems.append(
            f"{item_id}: owner_class is 'unclassified' — classify it as one of "
            f"{sorted(GENUINELY_HUMAN_CLASSES)} (a person is genuinely "
            f"required) or {OWNER_DEFAULTED!r} (a wire exists or could)")
        return problems

    basis = item.get("owner_class_basis")
    if _is_placeholder(basis) or len(str(basis).strip()) < _MIN_REASON_CHARS:
        problems.append(
            f"{item_id}: owner_class={owner_class!r} needs an "
            f"'owner_class_basis' of at least {_MIN_REASON_CHARS} chars saying "
            f"WHY — the class is the whole point of this register")

    if owner_class == OWNER_DEFAULTED:
        has_path = not _is_placeholder(item.get("automation_path"))
        reason = item.get("cannot_automate_reason")
        has_reason = (not _is_placeholder(reason)
                      and len(str(reason).strip()) >= _MIN_REASON_CHARS)
        if not has_path and not has_reason:
            problems.append(
                f"{item_id}: a 'defaulted_to_human' item must carry either an "
                f"'automation_path' (the wire that does it) or a "
                f"'cannot_automate_reason' of at least {_MIN_REASON_CHARS} "
                f"chars — resolution_criteria (c) of the register row "
                f"{_ROW_ID}")
        # ⚠️ The anti-pattern gate. "One remediation attempt failed" is not a
        # sufficient reason on its own: a failed attempt distrusts the
        # SELECTION, and the tested-pure-function remedy has a precedent in
        # this repo (src/runtime/protection_reassert.py).
        if has_reason and _cites_failed_attempt(reason):
            if _is_placeholder(item.get("tested_decision_function")):
                problems.append(
                    f"{item_id}: cannot_automate_reason rests on a failed "
                    f"remediation, so it must ALSO name a "
                    f"'tested_decision_function' — a pure decision with "
                    f"non-collapsed states, tested against the recorded "
                    f"failure. 'We automated it once and got it wrong, so a "
                    f"human owns it forever' is the anti-pattern this register "
                    f"is named after")

    return problems


_FAILED_ATTEMPT_MARKERS = (
    "remediation", "auto-remediat", "automated attempt", "attempt failed",
    "went wrong", "got it wrong", "cancelled the leg", "previous attempt",
)


def _cites_failed_attempt(reason: Any) -> bool:
    text = str(reason or "").casefold()
    return any(marker in text for marker in _FAILED_ATTEMPT_MARKERS)


def summarise(grades: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """Counts by state. Always emits EVERY state, including the zeroes.

    A summary that omits its empty buckets makes an absent state
    indistinguishable from one that was never gradeable.
    """
    counts = {state: 0 for state in ALL_STATES}
    for grade in grades:
        counts[grade["state"]] = counts.get(grade["state"], 0) + 1
    return counts
