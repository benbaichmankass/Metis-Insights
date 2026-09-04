#!/usr/bin/env python3
"""THE MANAGER WAKE — a manager's idle time is bounded by a MECHANISM, not by the operator looking.

MEASURED 2026-09-04, population = one manager session
(``session_016e2k4UmsMGgpbrJ5ctqeFv``), one gap:

    last manager act ~09:45Z; operator asked "what have you been up to" 21:53Z.
    TWELVE HOURS against a standing directive that "a manager idle for thirty
    minutes is a failure" -- 24x the stated bar -- and NOTHING NOTICED.

⚠️ THE MANAGER DID NOT DIE, AND THAT IS THE WHOLE POINT.
The session stayed alive and connected; it simply stopped taking turns. Every
mechanism this repo has is aimed at DEATH -- the lease's 90-minute TTL,
time-based takeover, "a session that dies cannot hand over". Death is the case
that is already covered. An ALIVE-AND-SILENT manager is covered by nothing, and
that is the hole this closes.

────────────────────────────────────────────────────────────────────────────
WHY THE LEASE AND R7 ARE NOT DETECTORS, AND MUST NOT BE MISTAKEN FOR ONE
────────────────────────────────────────────────────────────────────────────

⚠️ **THE LEASE EXPIRES CORRECTLY AND NOTHING READS IT.** It is a
mutual-exclusion token, not an alarm. On 2026-09-04 it expired at 09:38Z and sat
expired for **746 minutes** -- claimable by anyone, claimed by nobody. An expiry
that no process reads is not a detection.

⚠️ **R7 GRADES A HEARTBEAT ONLY WHEN A COMMIT ADVANCES ONE.** A manager that
writes no commits is graded by nothing at all, so it can be silent forever
without ever failing R7. That is the same distinction ``CLAUDE.md`` already
draws -- R7 grades whether the manager CHECKED IN, never whether it ACTED
(``BL-20260903-THE-PR-QUEUE-WATCHER-CANNOT-SEE-A-TEN-MINUTE-STALL``: every gap
15-35min while a green PR sat ten minutes and the operator noticed first).
2026-09-04 is that finding at 48x the scale.

The common shape of both: **they are read by the manager, or by a commit the
manager makes.** A silent manager makes neither. So the detector cannot live
inside the manager session -- it has to originate OUTSIDE it. That is the one
structural requirement, and it is what picks a Routine.

────────────────────────────────────────────────────────────────────────────
THE MECHANISM, AND WHY IT SELF-REBINDS
────────────────────────────────────────────────────────────────────────────

A cloud Routine, **fresh session per fire**, runs ``--assess`` against the
COMMITTED lease, and if the holder is SILENT it pokes that holder with
``--brief``. Prompt text and settings live in
``docs/claude/work/manager-wake-routine-prompt.md``.

⚠️ **THE TARGET IS DERIVED FROM THE LEASE, NEVER HARDCODED.** A Routine bound to
a specific ``persistent_session_id`` points at one session, and managers change:
the moment a new manager claims the lease, a hardcoded wake is aimed at a dead
session and is silently useless -- covered-looking and inert, which this repo
names as worse than absent. Reading ``holder`` out of ``MANAGER-LEASE.json`` on
every fire means a new manager becomes the wake's target by claiming the lease,
with nothing to remember to update.

WHAT IT IS NOT, each ruled out for a MEASURED reason rather than taste:

* **NOT a GitHub cron.** Measured in this repo: ``work-digest`` fired FIVE times
  in a day against 24 declared; ``probes`` fired ~4h50m late and once rather
  than daily; #10845 already moved the digest OFF cron for exactly this. A wake
  that does not fire is worse than none because it reads as covered.
* **NOT anything needing a MINTED CREDENTIAL.** Operator, 2026-09-02: "no minted
  tokens, ever". A Routine runs as the account, holding no secret this repo
  stores.
* **NOT the reaper.** A session that DIES cannot wake itself, and the lease
  expiring into nobody is that case. It is real and complementary; the operator
  was offered it and chose the wake first. Filed as its own row.
* **NOT another reminder.** "Check in every 5-10 minutes" ALREADY EXISTS, was
  read at session start, and produced a twelve-hour gap. "A reminder is not a
  mechanism" is a named non-fix here (MI-15, twice;
  ``BL-20260903-MANAGER-CHECKLIST-GOES-STALE-SILENTLY``). The difference is not
  emphasis: this text is delivered by something the silent manager does not run.
* **NOT a poll loop.** One fire an hour, and most fires do nothing. A long
  fallback beats a short poll.

────────────────────────────────────────────────────────────────────────────
FOUR ASSESS STATES, NEVER COLLAPSED
────────────────────────────────────────────────────────────────────────────

``active``       a holder exists and its heartbeat is inside the silence
                 threshold. Nothing to do. The common case, and it is the
                 mechanism working -- not a wasted run.
``silent``       a holder exists and its heartbeat is OLDER than the threshold.
                 **WAKE IT.** This is the case the whole file exists for.
``no_manager``   the lease is absent or released -- nobody holds it, so there is
                 nobody to wake. ⚠️ **NOT a success and NOT this mechanism's
                 case**: an unheld lease means management has stopped entirely,
                 which is the REAPER's case. Reported distinctly so "nobody to
                 wake" can never be read as "everything is fine".
``unreadable``   the lease could not be parsed. ⚠️ **WE DID NOT LOOK.** Not
                 evidence about the manager at all.

⚠️ ``no_manager`` and ``active`` are deliberately distinct even though neither
pokes anyone. Collapsing them would report "management has stopped" as
"the manager is fine", which is the exact substitution this row was opened over.

────────────────────────────────────────────────────────────────────────────
TWO LIMITATIONS, STATED RATHER THAN HIDDEN
────────────────────────────────────────────────────────────────────────────

⚠️ **1. THE BOUND IS ~30-90 MINUTES, NOT 30.** The silence threshold is 30
minutes (the operator's bar, and the lease's own ``HEARTBEAT_TARGET_MINUTES``),
but the Routine platform minimum is HOURLY. So a manager that goes silent just
after a fire is not poked until the next one: worst case is
``threshold + one cron period``. Against a measured 720-minute gap that is a
~8-24x improvement, and it is NOT the stated 30-minute bar. Do not report this
as meeting that bar.

⚠️ **2. THE LEASE HEARTBEAT IS ONLY AS FRESH AS THE LAST PUSH**, and this
mechanism inherits that. A manager working hard without pushing a heartbeat
looks silent and gets poked. That false wake is CHOSEN, on an asymmetry that is
measured on one side and bounded on the other:
  * a FALSE wake costs one queued turn in a session that is already working --
    it does not interrupt, and the brief it carries is the status update the
    manager owed anyway.
  * a MISSED wake cost 720 minutes, a green Tier-2-approved PR sitting ~7 hours,
    and three sub-sessions blocked on single acts.
So it favours waking, deliberately. A wake is not evidence the manager was
broken; the receipt records the observed silence so the two are never conflated.

Tier-1 throughout: reads committed JSON, writes one committed JSON. No network,
no MCP, no VM, no credential.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]

LEASE_PATH = REPO / "docs" / "claude" / "work" / "MANAGER-LEASE.json"
CHECKLIST_PATH = REPO / "docs" / "claude" / "work" / "MANAGER-CHECKLIST.json"
SESSIONS_PATH = REPO / "docs" / "claude" / "work" / "SESSIONS.json"
MERGE_QUEUE_PATH = REPO / "docs" / "claude" / "work" / "MERGE-QUEUE.json"
RECEIPT_PATH = REPO / "docs" / "claude" / "work" / "MANAGER-WAKE.json"

#: How long a manager may go without a heartbeat before the wake pokes it.
#:
#: NOT independently chosen: it is the lease's own ``HEARTBEAT_TARGET_MINUTES``,
#: which is itself the operator's "a manager idle for thirty minutes is a
#: failure". Imported below where the lease module is importable so the two can
#: never drift; this literal is the fallback and must be kept equal to it.
SILENCE_THRESHOLD_MINUTES = 30

#: The Routine's declared cadence. The platform minimum is hourly; see
#: LIMITATION 1. Used only to describe the bound honestly in output.
CADENCE_MINUTES = 60

#: How many runs the receipt keeps. Bounded on purpose -- an unbounded ledger in
#: a file every fire rewrites is a merge-conflict generator on a shared register,
#: which this repo has already paid for (26 merges/day, 73% touching one).
RECEIPT_KEEP_RUNS = 50

ACTIVE = "active"
SILENT = "silent"
NO_MANAGER = "no_manager"
UNREADABLE = "unreadable"

ASSESS_STATES: tuple[str, ...] = (ACTIVE, SILENT, NO_MANAGER, UNREADABLE)

#: Outcomes a fire may record. ``poked`` and ``no_action`` are both healthy;
#: ``failed`` is the loud one.
OUTCOMES: tuple[str, ...] = ("poked", "no_action", "failed")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _load_json(path: Path) -> tuple[Any, bool]:
    """Returns ``(data, readable)``. ``(None, True)`` means genuinely absent.

    The absent/unreadable split is kept at every call site: "there is no file"
    is a deploy fact, "the file is corrupt" means we did not look. Collapsing
    them is how a broken register gets reported as an empty one.
    """
    if not path.is_file():
        return None, True
    try:
        return json.loads(path.read_text(encoding="utf-8")), True
    except (OSError, ValueError):
        return None, False


def _silence_threshold() -> int:
    """The threshold, preferring the lease's own constant over our fallback."""
    try:
        sys.path.insert(0, str(REPO / "scripts" / "ops"))
        import manager_lease  # type: ignore

        value = getattr(manager_lease, "HEARTBEAT_TARGET_MINUTES", None)
        if isinstance(value, int) and value > 0:
            return value
    except Exception:
        pass
    return SILENCE_THRESHOLD_MINUTES


# ────────────────────────────────────────────────────────────────────────────
# assess
# ────────────────────────────────────────────────────────────────────────────


def assess(now: datetime | None = None, lease_path: Path = LEASE_PATH) -> dict[str, Any]:
    """Is a manager SILENT, and who would we wake?

    Reads only the committed lease. Returns one of ``ASSESS_STATES`` plus the
    evidence for it -- never a bare boolean, because "do not poke" has three
    different causes and they need different responses.
    """
    now = now or _now()
    threshold = _silence_threshold()

    lease, readable = _load_json(lease_path)
    if not readable:
        return {
            "state": UNREADABLE,
            "wake_session": None,
            "reason": "MANAGER-LEASE.json exists and could not be parsed. WE DID NOT LOOK -- "
            "this is not evidence about the manager.",
            "silent_minutes": None,
            "threshold_minutes": threshold,
            "assessed_at": _iso(now),
        }

    if lease is None:
        return {
            "state": NO_MANAGER,
            "wake_session": None,
            "reason": "No lease file at all. A bootstrap/deploy fact, NOT a release -- "
            "and nobody to wake.",
            "silent_minutes": None,
            "threshold_minutes": threshold,
            "assessed_at": _iso(now),
        }

    lease_state = lease.get("state")
    holder = lease.get("holder")

    if lease_state == "released" or not holder:
        return {
            "state": NO_MANAGER,
            "wake_session": None,
            "reason": (
                f"Lease state is {lease_state!r} with holder {holder!r} -- nobody holds it, so "
                "there is nobody to wake. ⚠️ This is NOT 'the manager is fine': management has "
                "stopped entirely, which is the REAPER's case, not this mechanism's."
            ),
            "silent_minutes": None,
            "threshold_minutes": threshold,
            "assessed_at": _iso(now),
        }

    # A holder exists. The question is only whether it has been heard from.
    # `heartbeat_at` is the manager's last recorded act; fall back to claim time
    # so a lease claimed and never beaten still ages rather than reading fresh.
    beat = _parse_iso(lease.get("heartbeat_at")) or _parse_iso(lease.get("claimed_at"))
    if beat is None:
        return {
            "state": UNREADABLE,
            "wake_session": holder,
            "reason": "Lease parsed but carries neither a readable heartbeat_at nor claimed_at, "
            "so its age cannot be computed. WE DID NOT LOOK.",
            "silent_minutes": None,
            "threshold_minutes": threshold,
            "assessed_at": _iso(now),
        }

    silent_minutes = int((now - beat).total_seconds() // 60)

    if silent_minutes < threshold:
        return {
            "state": ACTIVE,
            "wake_session": holder,
            "reason": f"Holder {holder} beat {silent_minutes}min ago, inside the "
            f"{threshold}min threshold. Nothing to do -- this is the mechanism working.",
            "silent_minutes": silent_minutes,
            "threshold_minutes": threshold,
            "assessed_at": _iso(now),
        }

    return {
        "state": SILENT,
        "wake_session": holder,
        "reason": f"Holder {holder} has not beaten for {silent_minutes}min, past the "
        f"{threshold}min threshold. WAKE IT.",
        "silent_minutes": silent_minutes,
        "threshold_minutes": threshold,
        "lease_expired": lease_state == "held"
        and silent_minutes >= int(lease.get("ttl_minutes") or 90),
        "assessed_at": _iso(now),
    }


# ────────────────────────────────────────────────────────────────────────────
# brief -- what the wake CARRIES
# ────────────────────────────────────────────────────────────────────────────
#
# ⚠️ A POKE IS NOT A WAKE. A manager woken with no state re-derives everything:
# it reads the lease, the checklist, the registry and the queue before it can
# take one act, and that re-derivation is most of a turn. So the wake lands on
# the contract the operator already requires of every status update --
# CHECKLIST -> RECENTLY DONE -> NEXT (CLAUDE.md, operator directive 2026-09-01,
# "the order is the contract") -- computed from the same committed registers the
# manager would have read anyway.


def _checklist_section(now: datetime) -> list[str]:
    data, readable = _load_json(CHECKLIST_PATH)
    if not readable:
        return ["⚠️ MANAGER-CHECKLIST.json is UNREADABLE. We did not look -- repair it first."]
    if data is None:
        return ["⚠️ MANAGER-CHECKLIST.json is ABSENT. The contract's first section has no source."]

    items = [i for i in data.get("items", []) if isinstance(i, dict)]
    declared = set(data.get("states", {}))
    counts: dict[str, int] = {}
    for item in items:
        counts[str(item.get("state"))] = counts.get(str(item.get("state")), 0) + 1

    lines = [
        f"Cycle **{data.get('cycle')}** · {len(items)} items · checklist last updated "
        f"{data.get('updated_at')} (by {data.get('manager_session')})."
    ]
    lines.append("State counts: " + ", ".join(f"`{k}` {v}" for k, v in sorted(counts.items())))

    # Undeclared states are surfaced rather than silently bucketed -- a register
    # whose rows use a vocabulary its own schema does not declare is a real
    # finding, and hiding it here would make this brief the thing that hides it.
    undeclared = sorted(set(counts) - declared - {"None"})
    if undeclared:
        lines.append(
            "⚠️ States used by items but NOT declared in the file's own `states` map: "
            + ", ".join(f"`{s}`" for s in undeclared)
            + " — the register and its schema disagree."
        )

    stale = _staleness(data.get("updated_at"), now)
    if stale is not None:
        lines.append(f"⚠️ The checklist itself is {stale} minutes old.")

    p1_open = [
        i
        for i in items
        if str(i.get("priority", "")).startswith("P1")
        and i.get("state") in {"in_flight", "blocked", "queued", "ready", "waiting"}
    ]
    if p1_open:
        lines.append("")
        lines.append(f"**P1 items not finished ({len(p1_open)}):**")
        for item in p1_open[:12]:
            prs = ", ".join(f"#{p}" for p in (item.get("prs") or []))
            lines.append(
                f"- `{item.get('id')}` [{item.get('state')}] {item.get('title')}"
                + (f" — {prs}" if prs else "")
            )
        if len(p1_open) > 12:
            lines.append(f"- …and {len(p1_open) - 12} more.")
    return lines


def _recently_done_section() -> list[str]:
    data, readable = _load_json(CHECKLIST_PATH)
    if not readable or data is None:
        return ["(no readable checklist — cannot list recently done)"]
    items = [i for i in data.get("items", []) if isinstance(i, dict)]
    done = [i for i in items if i.get("state") == "done"]
    unproven = [i for i in items if i.get("state") == "landed_unproven"]
    lines = [
        f"`done` (merged AND observed): **{len(done)}** · "
        f"`landed_unproven` (merged, effect NOT seen): **{len(unproven)}**",
        "",
        "⚠️ Those two are not the same fact and the repo's stated failure mode is collapsing "
        "them. `landed_unproven` is work that still owes an observation.",
    ]
    if unproven:
        lines.append("")
        lines.append("**Owing an observation:**")
        for item in unproven[:8]:
            lines.append(f"- `{item.get('id')}` {item.get('title')}")
        if len(unproven) > 8:
            lines.append(f"- …and {len(unproven) - 8} more.")
    return lines


def _next_section(now: datetime) -> list[str]:
    """What is WAITING ON THE MANAGER SPECIFICALLY.

    Sourced from the three registers whose stalling was the measured cost on
    2026-09-04: the merge queue (a green approved PR sat ~7h), the sub-session
    registry (three sessions at need_input, each naming one act), and the lease.
    """
    lines: list[str] = []

    queue, readable = _load_json(MERGE_QUEUE_PATH)
    lines.append("**Merge queue** — the manager RUNS it; it does not resolve the conflicts that")
    lines.append("not scheduling them causes.")
    if not readable:
        lines.append("- ⚠️ MERGE-QUEUE.json UNREADABLE. We did not look.")
    elif queue is None:
        lines.append("- ⚠️ MERGE-QUEUE.json ABSENT.")
    else:
        entries = [e for e in queue.get("entries", []) if isinstance(e, dict)]
        rebasing = [e for e in entries if e.get("state") == "rebasing"]
        if len(rebasing) > 1:
            lines.append(
                f"- ⚠️ **R8 VIOLATION**: {len(rebasing)} entries are `rebasing`; the invariant is "
                "at most ONE."
            )
        for entry in entries[:8]:
            blocked = f" blocked_on={entry.get('blocked_on')}" if entry.get("blocked_on") else ""
            lines.append(
                f"- #{entry.get('pr')} [{entry.get('state')}] pos {entry.get('position')}"
                f"{blocked} — `{entry.get('branch')}`"
            )
        if not entries:
            lines.append("- (queue empty)")
        stale = _staleness(queue.get("updated_at"), now)
        if stale is not None:
            lines.append(f"- ⚠️ Queue last updated {stale} minutes ago.")

    lines.append("")
    sessions, readable = _load_json(SESSIONS_PATH)
    lines.append("**Sub-sessions** — ⚠️ `last_observed` is a SNAPSHOT written at that session's")
    lines.append("own last turn, never live state. Poll `get_session` for what is true now.")
    lines.append("⚠️ And a manager CANNOT message a running cloud sub-session (verified: no")
    lines.append("reachable agents), so an act one is blocked on must be ABSORBED, not relayed.")
    if not readable:
        lines.append("- ⚠️ SESSIONS.json UNREADABLE. We did not look.")
    elif sessions is None:
        lines.append("- ⚠️ SESSIONS.json ABSENT.")
    else:
        rows = [s for s in sessions.get("sessions", []) if isinstance(s, dict)]
        # ⚠️ `last_observed` is not one shape. MEASURED 2026-09-04 over all 95
        # registered rows: 27 dict, 67 null, 1 a BARE TIMESTAMP STRING. A reader
        # that assumes the documented dict crashes on that one row, so this
        # normalises instead -- and counts the odd shapes rather than swallowing
        # them, because a registry disagreeing with its own schema is a finding.
        malformed = sum(1 for s in rows if s.get("last_observed") is not None
                        and not isinstance(s.get("last_observed"), dict))
        never = sum(1 for s in rows if s.get("last_observed") is None)
        waiting = [
            s
            for s in rows
            if isinstance(s.get("last_observed"), dict)
            and s["last_observed"].get("status_category")
            in {"need_input", "blocked", "review_ready"}
        ]
        lines.append(
            f"- {len(rows)} registered; {never} NEVER observed; {malformed} with a "
            f"`last_observed` that is not an object; {len(waiting)} last seen needing something:"
        )
        if malformed:
            lines.append(
                "  - ⚠️ Those malformed rows carry no readable status and are invisible to "
                "every status query, including this one."
            )
        for row in waiting[:10]:
            obs = row["last_observed"]
            need = str(obs.get("needs_action") or "").strip().replace("\n", " ")
            lines.append(
                f"  - `{row.get('session_id')}` [{obs.get('status_category')}] "
                f"{row.get('owns_object')} — {need[:180]}"
            )
        stale = _staleness(sessions.get("updated_at"), now)
        if stale is not None:
            lines.append(f"- ⚠️ Registry last updated {stale} minutes ago.")

    return lines


def _staleness(value: Any, now: datetime) -> int | None:
    parsed = _parse_iso(value)
    if parsed is None:
        return None
    return int((now - parsed).total_seconds() // 60)


def brief(now: datetime | None = None, verdict: dict[str, Any] | None = None) -> str:
    """The text the wake DELIVERS. Self-contained: no links to go read."""
    now = now or _now()
    verdict = verdict or assess(now=now)

    silent = verdict.get("silent_minutes")
    head = [
        "# ⏰ MANAGER WAKE — you have been silent and a mechanism noticed",
        "",
        f"Fired at {_iso(now)}. Your lease heartbeat is "
        + (f"**{silent} minutes** old" if silent is not None else "of unknown age")
        + f" against a {verdict.get('threshold_minutes')}-minute threshold.",
        "",
        "This is not a reminder you set yourself and it is not something you can forget to "
        "read: it originates OUTSIDE your session, which is the only reason it reaches a "
        "manager that has stopped taking turns.",
        "",
        "⚠️ Being woken is NOT proof you were broken — the lease is only as fresh as the last "
        "PUSH, so a manager working hard without pushing a heartbeat looks silent from here. "
        "If that is what happened, push the heartbeat; that is the fix, not an argument.",
        "",
        "Answer in the order the operator requires of every status update — "
        "**checklist → recently done → next**. That order is the contract.",
        "",
        "---",
        "",
        "## 1. CHECKLIST",
        "",
    ]
    body = _checklist_section(now)
    body += ["", "---", "", "## 2. RECENTLY DONE", ""]
    body += _recently_done_section()
    body += ["", "---", "", "## 3. NEXT — what is waiting ON YOU", ""]
    body += _next_section(now)
    body += [
        "",
        "---",
        "",
        "## Before you do anything else",
        "",
        "1. `python3 scripts/ops/manager_lease.py status` — you may no longer hold it. Takeover "
        "is time-based; if someone else holds it fresh, **stand down**.",
        "2. Heartbeat and **PUSH** it. A claim you never pushed protects nothing, and an "
        "unpushed heartbeat is what makes a working manager look silent to this wake.",
        "3. Then work the three sections above in that order.",
        "",
        "⚠️ Asking the operator a question NEVER means waiting for the answer — state an "
        "assumption and keep going. A manager that blocks becomes an extra decision gate in "
        "front of the operator, which is the measured constraint the manager exists to relieve.",
    ]
    return "\n".join(head + body)


# ────────────────────────────────────────────────────────────────────────────
# receipt
# ────────────────────────────────────────────────────────────────────────────
#
# ⚠️ WHY A RECEIPT AND NOT `list_triggers`. The obvious watcher reads the
# Routine's own state. It cannot be built, for the reasons
# `check_drain_liveness.py` already measured on this account: `list_triggers` is
# an mcp__* tool, so nothing outside a healthy Claude session can call it; and
# `last_run` is absent for Routines that wake a bound session, so it cannot
# separate "never fired" from "fired normally". What IS observable from anywhere
# is whether the wake left a trace in the repo. Hence one bounded receipt per
# fire -- INCLUDING fires that found nothing to do, because without those
# "nothing needed waking" and "the wake is dead" are indistinguishable, which is
# precisely the failure this mechanism was built to end.


def record_run(
    state: str,
    outcome: str,
    detail: str = "",
    wake_session: str | None = None,
    silent_minutes: int | None = None,
    now: datetime | None = None,
    path: Path = RECEIPT_PATH,
) -> dict[str, Any]:
    now = now or _now()
    if state not in ASSESS_STATES:
        raise ValueError(f"state must be one of {ASSESS_STATES}, got {state!r}")
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}, got {outcome!r}")

    existing, readable = _load_json(path)
    runs: list[dict[str, Any]] = []
    if readable and isinstance(existing, dict):
        runs = [r for r in existing.get("runs", []) if isinstance(r, dict)]

    runs.append(
        {
            "at": _iso(now),
            "assessed_state": state,
            "outcome": outcome,
            "wake_session": wake_session,
            "silent_minutes": silent_minutes,
            "detail": detail,
        }
    )
    runs = runs[-RECEIPT_KEEP_RUNS:]

    doc = {
        "_doc": [
            "THE MANAGER WAKE RECEIPT. One entry per Routine fire, including fires that",
            "found nothing to wake.",
            "",
            "Written by scripts/ops/manager_wake.py --record. Graded by",
            "scripts/ops/check_wake_liveness.py, which is the thing that notices when the",
            "wake itself has stopped -- because a dead detector reads exactly like a",
            "healthy one from every other surface.",
            "",
            "A run with outcome `no_action` is NOT a wasted fire. It is the evidence that",
            "separates 'nothing needed waking' from 'the wake is dead'.",
        ],
        "schema_version": 1,
        "updated_at": _iso(now),
        "cadence_minutes": CADENCE_MINUTES,
        "silence_threshold_minutes": _silence_threshold(),
        "runs": runs,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return doc


# ────────────────────────────────────────────────────────────────────────────


def _self_test() -> int:
    """Exercises the state machine against synthetic leases, in a temp dir."""
    import tempfile

    failures: list[str] = []

    def check(label: str, got: Any, want: Any) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    now = datetime(2026, 9, 4, 22, 0, 0, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)

        missing = d / "absent.json"
        check("absent -> no_manager", assess(now, missing)["state"], NO_MANAGER)

        bad = d / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        check("corrupt -> unreadable", assess(now, bad)["state"], UNREADABLE)

        released = d / "released.json"
        released.write_text(json.dumps({"state": "released", "holder": None}), encoding="utf-8")
        check("released -> no_manager", assess(now, released)["state"], NO_MANAGER)

        fresh = d / "fresh.json"
        fresh.write_text(
            json.dumps(
                {
                    "state": "held",
                    "holder": "session_x",
                    "heartbeat_at": _iso(now - timedelta(minutes=5)),
                }
            ),
            encoding="utf-8",
        )
        check("fresh beat -> active", assess(now, fresh)["state"], ACTIVE)

        quiet = d / "quiet.json"
        quiet.write_text(
            json.dumps(
                {
                    "state": "held",
                    "holder": "session_y",
                    "heartbeat_at": _iso(now - timedelta(minutes=200)),
                    "ttl_minutes": 90,
                }
            ),
            encoding="utf-8",
        )
        verdict = assess(now, quiet)
        check("stale beat -> silent", verdict["state"], SILENT)
        check("silent names holder", verdict["wake_session"], "session_y")
        check("silent minutes", verdict["silent_minutes"], 200)

        # A lease claimed and never beaten must AGE, not read fresh.
        never = d / "never.json"
        never.write_text(
            json.dumps(
                {
                    "state": "held",
                    "holder": "session_z",
                    "claimed_at": _iso(now - timedelta(minutes=400)),
                }
            ),
            encoding="utf-8",
        )
        check("claim-only ages -> silent", assess(now, never)["state"], SILENT)

        # The receipt round-trips and stays bounded.
        receipt = d / "receipt.json"
        for i in range(RECEIPT_KEEP_RUNS + 10):
            record_run(ACTIVE, "no_action", detail=f"run {i}", now=now, path=receipt)
        doc = json.loads(receipt.read_text(encoding="utf-8"))
        check("receipt bounded", len(doc["runs"]), RECEIPT_KEEP_RUNS)

        try:
            record_run("nonsense", "no_action", now=now, path=receipt)
            failures.append("record_run accepted an undeclared state")
        except ValueError:
            pass

    if failures:
        for f in failures:
            print(f"FAIL {f}")
        return 1
    print("manager_wake self-test: PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--assess", action="store_true", help="is a manager silent, and who?")
    ap.add_argument("--brief", action="store_true", help="print the text the wake delivers")
    ap.add_argument("--json", action="store_true", help="machine-readable output for --assess")
    ap.add_argument("--record", action="store_true", help="append a run to the receipt")
    ap.add_argument("--state", default=None, help=f"with --record: one of {ASSESS_STATES}")
    ap.add_argument("--outcome", default=None, help=f"with --record: one of {OUTCOMES}")
    ap.add_argument("--detail", default="", help="with --record: trigger id or reason")
    ap.add_argument("--wake-session", default=None)
    ap.add_argument("--silent-minutes", type=int, default=None)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    if args.record:
        if not args.state or not args.outcome:
            ap.error("--record needs --state and --outcome")
        doc = record_run(
            args.state,
            args.outcome,
            detail=args.detail,
            wake_session=args.wake_session,
            silent_minutes=args.silent_minutes,
        )
        print(f"recorded: {doc['runs'][-1]}")
        return 0

    if args.brief:
        print(brief())
        return 0

    if args.assess or True:
        verdict = assess()
        if args.json:
            print(json.dumps(verdict, indent=2, ensure_ascii=False))
        else:
            print(f"state:          {verdict['state']}")
            print(f"wake_session:   {verdict['wake_session']}")
            print(f"silent_minutes: {verdict['silent_minutes']}")
            print(f"threshold:      {verdict['threshold_minutes']}min")
            print(f"reason:         {verdict['reason']}")
        # Exit code is INFORMATIONAL, never a gate: `silent` is the mechanism
        # doing its job, not a build failure. 0 always, so this can never wedge
        # a runner that calls it.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
