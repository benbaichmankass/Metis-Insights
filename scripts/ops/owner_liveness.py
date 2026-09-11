#!/usr/bin/env python3
"""Does the register's own evidence support this row's ``in_flight`` claim?

``in_flight`` goes false BY TIME PASSING and nothing decays it. Measured
2026-09-10 by the day manager via ``get_session`` over thirteen owners: six of
nineteen ``in_flight`` checklist rows had owners that were finished, dead or
archived, one of them (``MI-136``) reading ``in_flight`` for FOUR DAYS after its
owner was archived. Same class as ``MI-178``, where all 17 ``SESSIONS.json``
``working`` rows were false. Re-measured on ``main`` 2026-09-11 for this module:
**all six were still `in_flight`** — the one-off state correction never happened,
which is precisely why the row's done-condition forbids a manual sweep.

WHAT THIS GRADES, AND WHAT IT DELIBERATELY DOES NOT
---------------------------------------------------
This module answers ONE question: **does the register's own recorded evidence
support the claim that someone is actively working this row?** It is a statement
about EVIDENCE, never about the WORK.

That distinction is the whole design, and it is what makes the failure polarity
safe. "This row's ``in_flight`` claim is unsupported by the registry" is a true
statement whenever the registry says the owner is not active — it remains true
even if the owner is in fact beavering away and the registry is simply stale. So
surfacing it can never be a false claim about the work, and the usual hazard of
a decay mechanism (marking live work abandoned) does not arise. What a spurious
surface costs is one ``get_session`` check by whoever reads it.

⚠️ **IT NEVER DECIDES WHAT THE ROW BECOMES.** Decaying ``in_flight`` is a claim
about an OWNER, not about the WORK: the correct landing state for a row whose
owner died mid-task is emphatically not ``done``, and guessing it would destroy
the "where do we pick this up" record that is the operator's core mandate. This
module makes staleness VISIBLE AND GRADEABLE; what the row becomes is a separate
call, made by a human or a manager who has read it.

THE OBVIOUS BUILD WAS MEASURED FIRST, AND IT CATCHES 1 OF 6
-----------------------------------------------------------
The natural design is "grade the owner against strictly TERMINAL session states"
— archived / completed / done / failed. **Measured against the six real rows on
``main`` 2026-09-11: that grades 1 of 6.** Five of the six owners sit at
``idle``, which is not terminal — an idle session can be woken, and MI-235's
push-back mechanism exists to wake one.

So the question is not *is the owner dead* but **is the owner working on it**,
and ``idle`` answers that as clearly as ``archived`` does. ``DORMANT`` and
``TERMINAL`` are therefore kept as SEPARATE states — they carry the same verdict
about the claim and completely different remedies (poke it, versus re-route it)
— and both count as unsupported. That takes the catch to 6 of 6.

(The same shape as MI-235, whose lane measured the obvious "grade the existing
``needs_action`` field on a clock" build and found it would have graded 0 of 3.)

WHY THIS CAN RUN IN CI, WHICH IS THE THING THAT HAD TO BE ESTABLISHED FIRST
---------------------------------------------------------------------------
Grading a live session needs ``get_session`` / ``list_sessions`` — ``mcp__*``
tools CI does not hold. That is what makes a "just check the owners" mechanism
look impossible from a guard, and it is the wall MI-235 hit.

It is escapable here because **the registry is a RELIABLE NEGATIVE and an
UNRELIABLE POSITIVE**, and only the negative is needed. Registry ``state``
decays in exactly one direction: a row written ``working`` becomes false as the
session finishes, which is the MI-178 finding. Nothing decays the other way —
a session does not spontaneously un-archive, and an ``idle`` row only becomes
false if somebody deliberately wakes it. So a recorded non-active state is
evidence somebody POSITIVELY OBSERVED the owner not working, while a recorded
``working`` is evidence of nothing. Since this module needs only the first, it
needs no live tool: **the evidence was already in the repo.**

Measured: 198 of 211 ``SESSIONS.json`` rows carry an observation timestamp, and
all six stale owners carried one, 1–3 days old. Nothing read them.

⚠️ **THE COST OF THAT IS STATED RATHER THAN HIDDEN:** a row whose owner died
with nobody recording it grades ``unknown_to_registry`` — *we could not look* —
and this module does nothing about it. That is the MI-15 incompleteness, and it
is a different row's problem. Reporting it as "nothing is stale" would be the
collapsed-state failure this repo has a guard family for, which is why that
verdict is `could_not_establish` and never a pass.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ⚠️ IMPORTED, NEVER RE-DERIVED. `manager_status` already owns "which session
# states mean a lane is doing something" (read off the register's own values,
# not invented) and "where does a row's observation timestamp live" (five
# different key names, measured). A second copy of either would be free to
# drift from the one the Telegram /status readout uses — the exact class this
# repo keeps a guard family for. This module reads them; it does not edit that
# file, which is deliberately left alone for MI-238's live lane.
from src.runtime.manager_status import (  # noqa: E402
    LIVE_SESSION_STATES,
    _declared_vocabulary,
    effective_state,
    OBS_NONE,
    OBS_RECENT,
    OBS_STALE,
    OBS_UNKNOWN,
    observe_session,
)

CHECKLIST_RELPATH = "docs/claude/work/MANAGER-CHECKLIST.json"
SESSIONS_RELPATH = "docs/claude/work/SESSIONS.json"
OBJECTS_RELDIR = "docs/claude/work/objects"

# ═════════════════════════════════════════════════════════════════════════════
# The states
# ═════════════════════════════════════════════════════════════════════════════

ACTIVE = "active"
DORMANT = "dormant"
TERMINAL = "terminal"
UNRECOGNISED_STATE = "unrecognised_state"
UNKNOWN_TO_REGISTRY = "unknown_to_registry"
UNGRADEABLE_OWNER = "ungradeable_owner"
REGISTRY_UNREAD = "registry_unread"

#: Seven states, never collapsed, each with a DIFFERENT remedy:
#:
#: * ``active``              — the registry records a live state. Nothing to do.
#: * ``dormant``             — ``idle``/``idle_delivered``: not working now, CAN
#:                             be woken. Remedy: poke it (MI-235's mechanism).
#: * ``terminal``            — ``archived``/``completed``/``done``/``failed``:
#:                             will not resume. Remedy: RE-ROUTE the row.
#: * ``unrecognised_state``  — the registry carries a state value this module
#:                             does not know. *We could not grade it.* A new
#:                             vocabulary word must NOT fall silently into a
#:                             bucket; remedy is to classify it here.
#: * ``unknown_to_registry`` — a real session id the registry does not carry.
#:                             *We looked and it is not there* — the MI-15
#:                             registration gap, NOT evidence about the owner.
#: * ``ungradeable_owner``   — the row names no session at all (``manager``,
#:                             ``null``, prose with no id). *There is nothing to
#:                             look at.* Remedy: name an owner.
#: * ``registry_unread``     — the registry itself could not be read. *We did
#:                             not look.*
#:
#: Registered with ``collapsed-state-guard`` as ``owner_liveness.owner_activity``.
OWNER_ACTIVITIES = (ACTIVE, DORMANT, TERMINAL, UNRECOGNISED_STATE,
                    UNKNOWN_TO_REGISTRY, UNGRADEABLE_OWNER, REGISTRY_UNREAD)

SUPPORTED = "supported"
UNSUPPORTED = "unsupported"
COULD_NOT_ESTABLISH = "could_not_establish"

#: Three states, never collapsed. ``could_not_establish`` is *we could not
#: establish whether anyone is working this* — it is NOT a pass, and folding it
#: into ``supported`` would report an ungraded row as a checked one, which is
#: the reassuring direction and therefore the dangerous one.
#: Registered with ``collapsed-state-guard`` as ``owner_liveness.claim_support``.
CLAIM_SUPPORTS = (SUPPORTED, UNSUPPORTED, COULD_NOT_ESTABLISH)

#: ``idle`` is NOT terminal and is kept apart from ``TERMINAL_STATES`` for that
#: reason — an idle session is woken by a poke, an archived one is not (its
#: container is released). They share a VERDICT and not a REMEDY.
DORMANT_STATES = frozenset({"idle", "idle_delivered"})

TERMINAL_STATES = frozenset({"archived", "completed", "done", "failed",
                             "failed_wrong_repo"})

#: ⚠️ ``blocked`` is deliberately ACTIVE (it is in `LIVE_SESSION_STATES`): a
#: blocked session is alive and waiting, which is MI-235's subject, not this
#: module's. Grading it unsupported here would double-report one condition and
#: invite re-routing work from a lane that is still holding it.
_ACTIVE_STATES = frozenset(LIVE_SESSION_STATES)

#: Matches a session id ANYWHERE in a string. `manager_status.grade_owner` uses
#: a fully anchored pattern, which is right for RENDERING an owner verbatim and
#: wrong here: measured over the 28 `in_flight` checklist rows on `main`
#: 2026-09-11, three carry a session id inside prose (`"session_01G4Vne… "
#: "(ENGINEERING LANE) — re-laned … inheriting from session_01MQ2Ez… "`), and an
#: anchored match grades all three `not_a_session` and never checks the owner
#: they plainly name.
_SESSION_IN_TEXT = re.compile(r"session_[A-Za-z0-9]{6,}")


def owner_session_id(owner: Any) -> Optional[str]:
    """The session id a row's ``owner`` field names, or ``None``.

    **The FIRST id in the string wins.** Measured over the three prose-owner
    ``in_flight`` rows on ``main`` 2026-09-11, the current owner is first in 3
    of 3, and a later id is provenance — ``MI-238`` reads ``"session_01G4Vne…
    (ENGINEERING LANE) — re-laned … inheriting from session_01MQ2Ez… (idle
    since …)"``, where taking the last id would grade the row against the
    session it was taken AWAY from. n=3 is a small population and is stated
    rather than hidden; the rule is pinned by a test so a counter-example
    changes it deliberately rather than silently.
    """
    if owner is None:
        return None
    text = str(owner).strip()
    # `owner: null` survives YAML->str as the literal "null" on 1 of the 8
    # in_flight objects; it names no session and must not read as one.
    if not text or text.lower() in {"null", "none", "~"}:
        return None
    match = _SESSION_IN_TEXT.search(text)
    return match.group(0) if match else None


@dataclass(frozen=True)
class OwnerGrade:
    """One row's owner, graded against the registry. Never raises."""

    activity: str
    support: str
    session_id: Optional[str] = None
    registry_state: Optional[str] = None
    #: Observation freshness of the REGISTRY ROW the verdict rests on, so a
    #: `dormant` recorded two minutes ago cannot read identically to one
    #: recorded three days ago.
    observation_state: str = OBS_UNKNOWN
    observation_basis: str = OBS_NONE
    observation_at: Optional[str] = None
    observation_age_minutes: Optional[float] = None


def _support_for(activity: str) -> str:
    if activity == ACTIVE:
        return SUPPORTED
    if activity in (DORMANT, TERMINAL):
        return UNSUPPORTED
    return COULD_NOT_ESTABLISH


def grade_owner_activity(
    owner: Any,
    registry: Optional[dict[str, dict[str, Any]]],
    *,
    now: Optional[datetime] = None,
    stale_minutes: float = 90.0,
) -> OwnerGrade:
    """Grade whether the register's evidence supports an ``in_flight`` claim.

    ``registry`` is ``{session_id: row}``, or ``None`` for *we could not read
    the registry* — which is ``registry_unread``, never "no owner is stale".
    Pure: no I/O, no clock beyond the injected ``now``.
    """
    session_id = owner_session_id(owner)
    if session_id is None:
        return OwnerGrade(UNGRADEABLE_OWNER, _support_for(UNGRADEABLE_OWNER))
    if registry is None:
        return OwnerGrade(REGISTRY_UNREAD, _support_for(REGISTRY_UNREAD),
                          session_id=session_id)

    row = registry.get(session_id)
    if not isinstance(row, dict):
        return OwnerGrade(UNKNOWN_TO_REGISTRY, _support_for(UNKNOWN_TO_REGISTRY),
                          session_id=session_id)

    raw_state = row.get("state")
    state = str(raw_state).strip().lower() if raw_state is not None else ""
    if not state:
        # A registry row with no state at all: we looked and it says nothing.
        # Measured 2026-09-11: 8 of 211 rows are in this shape.
        activity = UNRECOGNISED_STATE
    elif state in _ACTIVE_STATES:
        activity = ACTIVE
    elif state in DORMANT_STATES:
        activity = DORMANT
    elif state in TERMINAL_STATES:
        activity = TERMINAL
    else:
        activity = UNRECOGNISED_STATE

    obs = observe_session(row, now=now, stale_minutes=stale_minutes)
    return OwnerGrade(
        activity, _support_for(activity),
        session_id=session_id,
        registry_state=state or None,
        observation_state=obs.state,
        observation_basis=obs.basis,
        observation_at=obs.at,
        observation_age_minutes=obs.age_minutes,
    )


# ═════════════════════════════════════════════════════════════════════════════
# Reading the two registers — which DISAGREE, and that is itself a finding
# ═════════════════════════════════════════════════════════════════════════════

CHECKLIST_REGISTER = "checklist"
OBJECTS_REGISTER = "objects"

#: ⚠️ **TWO REGISTERS, AND ONLY ONE OF THEM BINDS.** Measured on `main`
#: 2026-09-11: `MANAGER-CHECKLIST.json` carries 22 rows whose effective state is
#: `in_flight` (of 267 items) while `docs/claude/work/objects/*.yaml` carries 8
#: (of 159) — and `check_wip_ceiling.py` counts the OBJECTS, at a ceiling of 8,
#: i.e. exactly full. So a stale row in the objects register is not merely a
#: misleading number: it REFUSES a ninth object, which is real work turned away.
#: A stale CHECKLIST row misprices capacity without refusing anything.
#:
#: Both are graded here, by ONE definition, and reported SEPARATELY — pooling
#: them would hide which of the two is holding the ceiling.
REGISTERS = (CHECKLIST_REGISTER, OBJECTS_REGISTER)

IN_FLIGHT = "in_flight"

_LIFECYCLE_RE = re.compile(r"^lifecycle:\s*(.*)$", re.M)
_OWNER_RE = re.compile(r"^owner:\s*(.*)$", re.M)
_ID_RE = re.compile(r"^id:\s*(.*)$", re.M)


@dataclass(frozen=True)
class InFlightRow:
    register: str
    row_id: str
    owner: Any
    grade: OwnerGrade


def read_registry(repo_root: Path) -> Optional[dict[str, dict[str, Any]]]:
    """``{session_id: row}`` from ``SESSIONS.json``, or ``None`` if unreadable.

    ``None`` is load-bearing and must reach the caller as ``registry_unread``:
    *we could not look* is not *no owner is stale*.
    """
    path = repo_root / SESSIONS_RELPATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    rows = data.get("sessions") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return None
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("session_id"):
            out[str(row["session_id"])] = row
    return out


def _yaml_scalar(text: str, pattern: re.Pattern[str]) -> Optional[str]:
    """First scalar for a top-level key. Deliberately NOT a YAML parse.

    `check_wip_ceiling.py` reads `lifecycle` the same line-oriented way and is
    the thing this module must agree with; parsing properly here would let the
    two disagree about which objects are in flight, which is the one thing a
    second reader of the same field must never do.
    """
    match = pattern.search(text)
    if match is None:
        return None
    value = match.group(1).strip()
    if "#" in value:
        value = value.split("#", 1)[0].strip()
    return value.strip("'\"").strip() or None


def in_flight_rows(
    repo_root: Path,
    registry: Optional[dict[str, dict[str, Any]]],
    *,
    now: Optional[datetime] = None,
) -> list[InFlightRow]:
    """Every ``in_flight`` row across BOTH registers, each with its owner grade."""
    out: list[InFlightRow] = []

    # --- the checklist -------------------------------------------------------
    try:
        checklist = json.loads(
            (repo_root / CHECKLIST_RELPATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        checklist = None
    if isinstance(checklist, dict):
        vocabulary = _declared_vocabulary(checklist)
        for item in checklist.get("items") or []:
            if not isinstance(item, dict):
                continue
            # `effective_state` is the ONE owner of the state/status merge, and
            # `state` wins a disagreement. Re-deriving it here would become a
            # SECOND definition of an item's status, free to drift from the one
            # `/status` renders — MI-237's subject, and MI-238's stated rule.
            if effective_state(item, vocabulary=vocabulary).value != IN_FLIGHT:
                continue
            out.append(InFlightRow(
                CHECKLIST_REGISTER, str(item.get("id") or "(no id)"),
                item.get("owner"),
                grade_owner_activity(item.get("owner"), registry, now=now)))

    # --- the work objects (the register the WIP ceiling binds on) ------------
    objects_dir = repo_root / OBJECTS_RELDIR
    if objects_dir.is_dir():
        for path in sorted(objects_dir.glob("*.yaml")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if _yaml_scalar(text, _LIFECYCLE_RE) != IN_FLIGHT:
                continue
            owner = _yaml_scalar(text, _OWNER_RE)
            out.append(InFlightRow(
                OBJECTS_REGISTER,
                _yaml_scalar(text, _ID_RE) or path.stem,
                owner,
                grade_owner_activity(owner, registry, now=now)))
    return out


def census(rows: Iterable[InFlightRow]) -> dict[str, dict[str, int]]:
    """``{register: {support: n}}`` plus a ``total`` roll-up. Never raises."""
    out: dict[str, dict[str, int]] = {
        r: {s: 0 for s in CLAIM_SUPPORTS} for r in REGISTERS}
    out["total"] = {s: 0 for s in CLAIM_SUPPORTS}
    for row in rows:
        bucket = out.setdefault(
            row.register, {s: 0 for s in CLAIM_SUPPORTS})
        bucket[row.grade.support] = bucket.get(row.grade.support, 0) + 1
        out["total"][row.grade.support] += 1
    return out


def unsupported(rows: Iterable[InFlightRow]) -> list[InFlightRow]:
    return [r for r in rows if r.grade.support == UNSUPPORTED]
