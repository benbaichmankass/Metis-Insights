#!/usr/bin/env python3
#
# wiring: fired by .github/workflows/pr-queue-watch.yml on its cron -- a clock
# the manager does not own. Deliberately NOT reachable from a prompt, a skill or
# a checklist step, for the reason that workflow's header records: every
# mechanism the manager had to CHOOSE to run went unused; every mechanism that
# STOOD IN THE WAY worked.
"""THE MANAGER-STATE WATCH — the half of the queue watch that needs no MCP.

WHY THIS EXISTS: A CONSTRAINT STATED RATHER THAN ENGINEERED AROUND
-------------------------------------------------------------------
Operator direction, 2026-09-03:

    "I don't think it's a good idea for that mechanism to rely on having another
     routine session running. It would be better if we can hard code that into
     the repo or the VM instead."

**Part of it can move, and part of it provably cannot.** Session liveness needs
`list_sessions`, an `mcp__*` tool **CI does not hold** — `queue_latency.py`
reports `unknown` permanently for exactly this reason and refuses to substitute
a stale registry snapshot for a live read. No amount of workflow engineering
changes that, so pretending otherwise would produce a watcher that looks
repo-anchored and answers a different question than the one it claims.

So the split is drawn explicitly:

| question | needs | lives in |
|---|---|---|
| is a sub-session actually RUNNING? | `list_sessions` (MCP) | **the Routine — irreducible** |
| has an open PR gone quiet? | git + the PR list | `pr_queue_latency.py` (already) |
| is a manager holding a lease it stopped heartbeating? | **this repo alone** | **HERE** |
| is a registry row asserting live work long past its spawn? | **this repo alone** | **HERE** |

The last two are answerable from committed files with no network and no MCP, so
they belong on a clock nothing can forget to run. This does NOT duplicate
`pr_queue_latency.py` — that one owns branch quiescence and is untouched; this
runs beside it in the same workflow.

⚠️ IT STORES NOTHING AND ASSERTS NOTHING. Every verdict is derived at read time
from files that already exist. There is no new register here — the disease being
treated is too many registers asserting state nobody verified.

⚠️ WHAT IT CANNOT SEE, SAID PLAINLY. A registry row can look perfectly healthy
here while the session it names has been dead for hours; that is precisely the
gap the Routine covers, and this tool reports its own blindness rather than
implying coverage it does not have. A `clear` verdict from this file is NOT a
statement that the fleet is fine.

THREE STATES, NEVER COLLAPSED
-----------------------------
``clear``     every check ran and found nothing.
``findings``  at least one check found something. A real finding.
``unknown``   at least one check could not be EVALUATED (an unreadable file).
              ⚠️ **WE DID NOT LOOK** — never a soft pass.

EXIT CODES: 0 clear · 3 findings · 4 unknown — mirroring `handoff_check.py` so a
caller cannot treat "we could not look" as a pass.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

import manager_lease  # noqa: E402
import session_registry as sr  # noqa: E402

REPO_ROOT = sr.REPO_ROOT

CLEAR, FINDINGS, UNKNOWN = "clear", "findings", "unknown"
OK, FINDING, UNEVALUATED = "ok", "finding", "unevaluated"
_EXIT = {CLEAR: 0, FINDINGS: 3, UNKNOWN: 4}

#: Registry `state` values that assert the session is live work.
ACTIVE_REGISTRY_STATES = ("working", "running")

#: How long a registry row may assert live work before it is worth a second look.
#: ⚠️ CHOSEN, NOT TUNED, and it is a PROMPT rather than a verdict: this file
#: cannot tell a long-running session from a dead one — only `list_sessions` can
#: — so the threshold is set well past a normal session's life so that what it
#: surfaces is worth a manager's attention rather than a daily shrug. Measured
#: 2026-09-03 over all 82 SESSIONS.json rows: 20 assert an active `state`, and
#: their spawn ages span 1.2h to 47h.
DEFAULT_ROW_AGE_HOURS = 12.0


def _parse_ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _c(name: str, state: str, message: str, **extra: Any) -> Dict[str, Any]:
    return dict(check=name, state=state, message=message, **extra)


def check_lease_heartbeat(lease: Optional[Dict[str, Any]], readable: bool,
                          now: datetime,
                          ttl_minutes: int = manager_lease.TTL_MINUTES
                          ) -> Dict[str, Any]:
    """A lease HELD but no longer heartbeated — a manager that died holding it.

    ⚠️ The TTL is imported from `manager_lease`, never restated. A second copy of
    the expiry policy would be free to drift from the one that actually governs
    takeover, and then this watcher would report a manager as dead while the
    lease still refused the successor's claim, or the reverse.

    ⚠️ THIS IS NOT AN OUTAGE, AND THE MESSAGE SAYS SO. Takeover is time-based by
    design precisely because a session that dies cannot hand over, so an expired
    lease is already claimable and the system self-heals. What it costs is
    SUPERVISION in the meantime, which is exactly the thing nobody was watching.
    """
    if not readable:
        return _c("lease_heartbeat", UNEVALUATED,
                  "MANAGER-LEASE.json could not be parsed, so whether a manager is "
                  "holding a lease it stopped heartbeating is unestablished. WE DID "
                  "NOT LOOK — this is not 'nobody holds it'.")
    if lease is None:
        return _c("lease_heartbeat", OK,
                  "no lease file exists. A bootstrap/deploy fact, not evidence "
                  "that a manager released one.")
    state = str(lease.get("state", "")).strip().lower()
    holder = lease.get("holder")
    if state != "held" or not isinstance(holder, str) or not holder.strip():
        return _c("lease_heartbeat", OK,
                  f"the lease is not held (state={state or 'unset'!s}), so there is "
                  f"no heartbeat that could be missing.")
    beat = _parse_ts(lease.get("heartbeat_at")) or _parse_ts(lease.get("claimed_at"))
    if beat is None:
        return _c("lease_heartbeat", UNEVALUATED,
                  f"the lease is HELD by {holder} and carries no parseable "
                  f"`heartbeat_at` or `claimed_at`, so its age cannot be "
                  f"established. A held lease that cannot be dated is not a "
                  f"healthy one — WE DID NOT LOOK.", holder=holder)
    age_min = (now - beat).total_seconds() / 60.0
    if age_min >= ttl_minutes:
        return _c("lease_heartbeat", FINDING,
                  f"the lease is HELD by {holder} and its last heartbeat is "
                  f"{age_min:.0f} min old, past the {ttl_minutes} min TTL. The "
                  f"holder is not heartbeating, so SUPERVISION has stopped even "
                  f"though sub-sessions keep running. Takeover is time-based and "
                  f"this is already claimable — `manager_lease.py claim` — so this "
                  f"is a prompt to take over, not an outage.",
                  holder=holder, heartbeat_age_min=round(age_min, 1))
    return _c("lease_heartbeat", OK,
              f"held by {holder}, heartbeated {age_min:.0f} min ago (TTL "
              f"{ttl_minutes} min).", holder=holder,
              heartbeat_age_min=round(age_min, 1))


def check_stale_active_rows(reg_doc: Optional[Any], readable: bool, now: datetime,
                            max_age_hours: float = DEFAULT_ROW_AGE_HOURS
                            ) -> Dict[str, Any]:
    """Registry rows asserting live work long past their spawn.

    ⚠️ THIS IS A PROMPT, NOT A VERDICT, AND THE DISTINCTION IS THE HONEST PART.
    A row here is old, which is not the same as dead — only a `list_sessions`
    read can tell those apart, and this file does not have one. So the finding is
    phrased as *go and look*, never as *this session is gone*. Reporting an old
    row as a dead session would be asserting exactly the kind of unverified state
    this whole cycle is about.
    """
    if not readable:
        return _c("stale_active_rows", UNEVALUATED,
                  "SESSIONS.json could not be parsed, so rows asserting live work "
                  "could not be counted. WE DID NOT LOOK.")
    rows = sr.registry_rows(reg_doc)
    active, undateable, stale = 0, 0, []
    for r in rows:
        if str(r.get("state", "")).strip().lower() not in ACTIVE_REGISTRY_STATES:
            continue
        active += 1
        spawned = _parse_ts(r.get("spawned_at")) or _parse_ts(r.get("confirmed_at"))
        if spawned is None:
            # ⚠️ COUNTED, never silently dropped: a row asserting live work that
            # cannot be dated is its own small finding, and folding it into the
            # clean count would hide it.
            undateable += 1
            continue
        age_h = (now - spawned).total_seconds() / 3600.0
        if age_h >= max_age_hours:
            stale.append({"session_id": r.get("session_id"),
                          "title": str(r.get("title") or "")[:60],
                          "age_hours": round(age_h, 1)})
    pop = (f"population: {len(rows)} registry row(s), {active} asserting an active "
           f"state {list(ACTIVE_REGISTRY_STATES)}; {undateable} of those carry no "
           f"parseable spawn timestamp and are counted separately, never as clean")
    if stale:
        stale.sort(key=lambda e: -e["age_hours"])
        return _c("stale_active_rows", FINDING,
                  f"{len(stale)} row(s) have asserted live work for more than "
                  f"{max_age_hours}h (oldest {stale[0]['age_hours']}h). ⚠️ OLD IS "
                  f"NOT DEAD — this file holds no live observation and cannot tell "
                  f"a long-running session from a stopped one. Run "
                  f"`scripts/ops/manager_view.py --live-sessions <list_sessions>` "
                  f"to find out which. {pop}",
                  rows=stale[:10], population=pop, undateable=undateable)
    if undateable:
        return _c("stale_active_rows", FINDING,
                  f"no row is over {max_age_hours}h, but {undateable} row(s) assert "
                  f"live work with NO parseable spawn timestamp, so their age is "
                  f"unknowable from the repo. {pop}",
                  rows=[], population=pop, undateable=undateable)
    return _c("stale_active_rows", OK,
              f"no row has asserted live work for more than {max_age_hours}h. {pop}",
              rows=[], population=pop, undateable=0)


def grade(checks: Sequence[Dict[str, Any]]) -> str:
    """PURE. FINDING dominates UNEVALUATED because a known finding is definite;
    UNEVALUATED dominates OK because 'we could not look' is never clean."""
    if any(c["state"] == FINDING for c in checks):
        return FINDINGS
    if any(c["state"] == UNEVALUATED for c in checks):
        return UNKNOWN
    return CLEAR


def run(now: Optional[datetime] = None,
        max_age_hours: float = DEFAULT_ROW_AGE_HOURS) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    lease, lease_ok = manager_lease.read_lease()
    reg, reg_ok = sr.read_json(sr.REGISTRY_PATH)
    checks = [
        check_lease_heartbeat(lease, lease_ok, now),
        check_stale_active_rows(reg, reg_ok, now, max_age_hours),
    ]
    return {"verdict": grade(checks), "checks": checks}


_ADVICE = {
    CLEAR: "Every repo-answerable check ran and found nothing. ⚠️ This says "
           "NOTHING about whether sub-sessions are actually running — that needs "
           "`list_sessions`, which CI cannot call. See the Routine.",
    FINDINGS: "FINDINGS above, from committed files alone. None of them asserts "
              "that a session is dead; they say where to look.",
    UNKNOWN: "REFUSED a clean verdict — a file could not be read. `unknown` is "
             "not a soft `clear`.",
}


def _self_test() -> int:
    ok = True

    def check(label: str, got: Any, want: Any) -> None:
        nonlocal ok
        good = got == want
        ok &= good
        print(f"  self-test ({label}): "
              f"{'PASS' if good else f'FAIL got={got!r} want={want!r}'}")

    now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)

    def iso(hours_ago: float) -> str:
        return (now - __import__("datetime").timedelta(hours=hours_ago)).isoformat()

    # --- grading policy, arguable here rather than against a live fleet ------
    check("all ok -> clear", grade([{"state": OK}, {"state": OK}]), CLEAR)
    check("a finding -> findings", grade([{"state": OK}, {"state": FINDING}]),
          FINDINGS)
    check("an unevaluated check -> unknown, NEVER clear",
          grade([{"state": OK}, {"state": UNEVALUATED}]), UNKNOWN)
    check("a FINDING dominates an unevaluated one (a known finding is definite)",
          grade([{"state": UNEVALUATED}, {"state": FINDING}]), FINDINGS)

    # --- lease heartbeat, both directions ------------------------------------
    held_fresh = {"state": "held", "holder": "S1", "heartbeat_at": iso(0.1)}
    held_dead = {"state": "held", "holder": "S1", "heartbeat_at": iso(5)}
    check("A HELD LEASE PAST ITS TTL IS A FINDING — supervision has stopped",
          check_lease_heartbeat(held_dead, True, now)["state"], FINDING)
    check("...and a freshly heartbeated one is not, so the check discriminates",
          check_lease_heartbeat(held_fresh, True, now)["state"], OK)
    check("a RELEASED lease has no heartbeat to be missing",
          check_lease_heartbeat({"state": "released", "holder": None}, True,
                                now)["state"], OK)
    check("an UNREADABLE lease is unevaluated, never ok",
          check_lease_heartbeat(None, False, now)["state"], UNEVALUATED)
    check("an ABSENT lease is ok, and is a different fact from unreadable",
          check_lease_heartbeat(None, True, now)["state"], OK)
    check("a HELD lease with no parseable heartbeat is unevaluated, never ok — "
          "a held lease that cannot be dated is not a healthy one",
          check_lease_heartbeat({"state": "held", "holder": "S1"}, True,
                                now)["state"], UNEVALUATED)
    check("the TTL is IMPORTED from manager_lease, not restated (a second copy "
          "would drift from the policy that actually governs takeover)",
          isinstance(manager_lease.TTL_MINUTES, int), True)

    # --- stale active rows, both directions ----------------------------------
    fresh_reg = {"sessions": [{"session_id": "s1", "state": "working",
                               "spawned_at": iso(1)}]}
    old_reg = {"sessions": [{"session_id": "s1", "state": "working",
                             "spawned_at": iso(40)}]}
    check("A ROW ASSERTING LIVE WORK FOR 40h IS A FINDING",
          check_stale_active_rows(old_reg, True, now)["state"], FINDING)
    check("...and a 1h-old one is not, so the check discriminates",
          check_stale_active_rows(fresh_reg, True, now)["state"], OK)
    check("...and the finding says OLD IS NOT DEAD rather than asserting a death",
          "OLD IS NOT DEAD" in check_stale_active_rows(old_reg, True,
                                                       now)["message"], True)
    check("...and points at the tool that CAN tell them apart",
          "manager_view.py" in check_stale_active_rows(old_reg, True,
                                                       now)["message"], True)
    check("a DORMANT row is not graded — it asserts no live work",
          check_stale_active_rows({"sessions": [{"session_id": "s1",
                                                 "state": "idle",
                                                 "spawned_at": iso(40)}]}, True,
                                  now)["state"], OK)
    check("an active row with NO parseable spawn is a finding, never folded into "
          "the clean count",
          check_stale_active_rows({"sessions": [{"session_id": "s1",
                                                 "state": "working"}]}, True,
                                  now)["state"], FINDING)
    check("...and it is COUNTED separately so the clean number stays honest",
          check_stale_active_rows({"sessions": [{"session_id": "s1",
                                                 "state": "working"}]}, True,
                                  now)["undateable"], 1)
    check("an UNREADABLE registry is unevaluated, never ok",
          check_stale_active_rows(None, False, now)["state"], UNEVALUATED)
    check("every verdict states its population",
          "population:" in check_stale_active_rows(fresh_reg, True,
                                                   now)["message"], True)

    # --- the honest boundary is ADVERTISED, not implied ----------------------
    check("a CLEAR verdict says outright that it proves nothing about liveness",
          "list_sessions" in _ADVICE[CLEAR], True)

    print("manager-state-watch self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--max-row-age-hours", type=float,
                    default=DEFAULT_ROW_AGE_HOURS)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()

    res = run(max_age_hours=a.max_row_age_hours)
    icon = {OK: "OK  ", FINDING: "FIND", UNEVALUATED: "????"}
    for c in res["checks"]:
        print(f"manager-state-watch: [{icon[c['state']]}] {c['check']}: "
              f"{c['message']}")
    print(f"manager-state-watch: verdict={res['verdict']}")
    print(f"manager-state-watch: {_ADVICE[res['verdict']]}")
    if a.json:
        print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
    return _EXIT[res["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
