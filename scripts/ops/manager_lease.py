#!/usr/bin/env python3
"""The MANAGER LEASE — exactly one management session at a time, never two.

Operator requirement, 2026-09-01, verbatim in intent:

  * work runs continuously, across days
  * **exactly ONE management session at a time, never concurrent**
  * a NEW manager must be able to take over the previous manager's sub-sessions
  * sub-sessions must NOT depend on any particular manager

WHY THE LEASE LIVES IN THE REPO
-------------------------------
Not by elimination — by the requirement. A lease that must survive its holder's
DEATH and be readable by a session arriving COLD cannot live in any session's
memory, and cannot live behind a credential no session is allowed to hold. The
repo is the only store both properties hold in. Commit noise is the accepted
cost (`WO-20260901-PHASE-E.yaml::lease_design`).

WHY EXPIRY, NOT HANDOVER
------------------------
⚠️ Takeover is TIME-BASED and deliberately does not require the outgoing manager
to do anything. A session that dies cannot run its own close-out — that is the
same reason this phase needs a reaper at all — so a handover that depends on the
outgoing manager cooperating fails in exactly the case it exists for. An outgoing
manager that is still alive learns on its next `heartbeat` that it no longer
holds the lease, and stands down.

⚠️ THE LEASE IS ONLY AS FRESH AS THE LAST PUSH, AND THIS IS A REAL LIMITATION
-----------------------------------------------------------------------------
Another session sees your claim only after it reaches `origin`. An unclaimed-
looking lease may be one an unpushed session is holding. So:

  * `claim` and `heartbeat` write the file; **committing and pushing it is the
    caller's job** and is not optional. `--commit` stages+commits for you; it
    deliberately does NOT push, because a push needs a rebase dance this script
    must not perform unattended on `main`.
  * a claim you never pushed protects nothing. State that rather than assuming
    the file write was the safety.

STATES, NEVER COLLAPSED
-----------------------
``held_fresh``   another session holds it and its heartbeat is inside the TTL.
                 You MUST NOT manage. Not an error — the mechanism working.
``held_by_me``   this session holds it.
``expired``      held, but the heartbeat is older than the TTL. Claimable. This
                 is the takeover path, not a fault.
``released``     a manager explicitly stood down. Claimable.
``absent``       no lease file at all — a bootstrap/deploy fact, NOT a release.
                 Claimable, and reported distinctly so a missing file is never
                 read as "somebody released it".
``unreadable``   the file exists and could not be parsed. ⚠️ **WE DID NOT LOOK.**
                 REFUSED, deliberately: the one outcome the operator forbade is
                 two concurrent managers, so an unreadable lease fails CLOSED.
                 `--force --reason "..."` is the escape, and it is recorded in
                 the file so a takeover on no evidence is never invisible.

The TTL is CHOSEN, NOT MEASURED (see ``TTL_MINUTES``).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
LEASE_PATH = REPO_ROOT / "docs" / "claude" / "work" / "MANAGER-LEASE.json"

#: How long a claim stays valid without a heartbeat.
#:
#: ⚠️ CHOSEN, NOT MEASURED. There is no distribution of manager-session gaps to
#: calibrate against — this is the first lease this system has had. The two
#: failure directions are not symmetric and that is what picked the value:
#:   * TOO SHORT -> a live manager mid-operation loses its lease to a new
#:     arrival, producing the two-concurrent-managers state the operator
#:     forbade. This is the harm.
#:   * TOO LONG  -> a dead manager blocks its successor for up to the TTL. The
#:     cost is a delay, and sub-sessions keep running unsupervised meanwhile.
#: 90 minutes is long enough that an ordinary long operation (a CI wait, a
#: multi-PR merge) cannot silently drop the lease, and short enough that a
#: manager that died overnight is claimable the next morning without a --force.
#: Revisit it against real gaps once there are some; do not read it as tuned.
TTL_MINUTES = 90

#: What a healthy manager should not exceed between heartbeats. Advisory — it is
#: what `status` measures you against, and is deliberately well inside the TTL so
#: a missed beat is visible before it is fatal.
HEARTBEAT_TARGET_MINUTES = 30

CLAIMABLE = {"expired", "released", "absent"}
REFUSING = {"held_fresh", "unreadable"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(s: Any) -> Optional[datetime]:
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        d = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def read_lease(path: Path = LEASE_PATH) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Returns (lease, readable). ``(None, True)`` means genuinely absent."""
    if not path.is_file():
        return None, True
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None, False
    return (d, True) if isinstance(d, dict) else (None, False)


def grade(lease: Optional[Dict[str, Any]], readable: bool, me: Optional[str],
          now: Optional[datetime] = None,
          ttl_minutes: int = TTL_MINUTES) -> Tuple[str, str]:
    """Grade the lease. PURE, so the policy is arguable in tests rather than
    against a live pair of sessions.

    Returns (state, human message).
    """
    now = now or _now()
    if not readable:
        return "unreadable", (
            "the lease file exists and could not be parsed. We did not look — that "
            "is NOT the same as 'nobody holds it'. Refusing to claim, because the "
            "one outcome this mechanism exists to prevent is two concurrent "
            "managers. Fix the file, or claim with --force --reason.")
    if lease is None:
        return "absent", (
            "no lease file exists. Claimable — but note this is a bootstrap/deploy "
            "fact, not evidence that a manager released it.")

    holder = lease.get("holder")
    if not holder or str(lease.get("state", "")).strip().lower() == "released":
        return "released", (
            f"released by {lease.get('released_by') or lease.get('holder') or 'unknown'} "
            f"at {lease.get('released_at') or 'unknown time'}. Claimable.")

    beat = _parse_iso(lease.get("heartbeat_at")) or _parse_iso(lease.get("claimed_at"))
    if beat is None:
        return "expired", (
            f"held by {holder}, but it carries no readable heartbeat_at or "
            f"claimed_at — so its freshness cannot be established and it cannot be "
            f"shown to be live. Treated as expired and claimable.")

    age_min = (now - beat).total_seconds() / 60.0
    if age_min > ttl_minutes:
        return "expired", (
            f"held by {holder}, last heartbeat {age_min:.0f} min ago — past the "
            f"{ttl_minutes} min TTL. Claimable. ⚠️ If that session is in fact still "
            f"alive it will discover on its next heartbeat that it no longer holds "
            f"the lease and stand down; that is the designed takeover, not a race.")

    if me and holder == me:
        return "held_by_me", (
            f"you hold it. Last heartbeat {age_min:.0f} min ago; beat again within "
            f"{HEARTBEAT_TARGET_MINUTES} min (TTL {ttl_minutes} min).")

    return "held_fresh", (
        f"HELD by {holder}, last heartbeat {age_min:.0f} min ago (TTL {ttl_minutes} "
        f"min). DO NOT MANAGE. Exactly one management session at a time is an "
        f"operator requirement, not a convention. Either wait for it to expire, or "
        f"ask the operator to stand it down.")


def _write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


_DOC = [
    "THE MANAGER LEASE. Exactly one management session at a time, never two.",
    "",
    "Written by scripts/ops/manager_lease.py -- do not hand-edit; a hand-edit that",
    "breaks the JSON reads as `unreadable`, which REFUSES every claim until it is",
    "repaired or forced.",
    "",
    "READ THIS FILE BEFORE MANAGING ANYTHING:  python3 scripts/ops/manager_lease.py status",
    "",
    "Takeover is TIME-BASED. A session that dies cannot hand over, so nothing here",
    "depends on the outgoing manager cooperating. An outgoing manager still alive",
    "learns at its next heartbeat that it no longer holds the lease, and stands down.",
    "",
    "A CLAIM YOU DID NOT PUSH PROTECTS NOTHING -- another session reads `origin`.",
    "",
    "Sub-sessions do NOT depend on this. They keep running with no lease held; what",
    "pauses is SUPERVISION. docs/claude/work/SESSIONS.json is what a cold manager",
    "reads to pick them up. Design: docs/claude/work/objects/WO-20260901-PHASE-E.yaml",
]


def cmd_status(a) -> int:
    lease, readable = read_lease()
    state, msg = grade(lease, readable, a.session_id)
    print(f"manager-lease: state={state}")
    print(f"manager-lease: {msg}")
    if lease and lease.get("holder"):
        print(f"manager-lease: holder={lease.get('holder')} "
              f"claimed_at={lease.get('claimed_at')} "
              f"heartbeat_at={lease.get('heartbeat_at')}")
    print(f"manager-lease: claimable={state in CLAIMABLE}")
    return 0


def _commit(paths, message: str) -> None:
    try:
        subprocess.run(["git", "add", *[str(p) for p in paths]],
                       cwd=REPO_ROOT, check=True)
        subprocess.run(["git", "commit", "-m", message], cwd=REPO_ROOT, check=True)
        print("manager-lease: committed. ⚠️ NOT PUSHED — push it, or the claim is "
              "invisible to every other session.")
    except subprocess.CalledProcessError as e:
        print(f"manager-lease: commit failed ({e}). The file is written; commit and "
              f"push it by hand.")


def cmd_claim(a) -> int:
    lease, readable = read_lease()
    state, msg = grade(lease, readable, a.session_id)
    if state == "held_by_me":
        print(f"manager-lease: you already hold it. {msg}")
        return 0
    if state in REFUSING and not a.force:
        print(f"::error::manager-lease: REFUSED (state={state}). {msg}")
        return 3
    if state in REFUSING and a.force:
        if not a.reason:
            print("::error::manager-lease: --force requires --reason. A takeover on "
                  "no stated evidence is exactly what must never be invisible.")
            return 3
        print(f"::warning::manager-lease: FORCED takeover over state={state}. "
              f"reason={a.reason}")

    now = _now()
    payload = {
        "_doc": _DOC,
        "schema_version": 1,
        "state": "held",
        "holder": a.session_id,
        "claimed_at": _iso(now),
        "heartbeat_at": _iso(now),
        "expires_at": _iso(now + timedelta(minutes=TTL_MINUTES)),
        "ttl_minutes": TTL_MINUTES,
        "heartbeat_target_minutes": HEARTBEAT_TARGET_MINUTES,
        "claimed_over_state": state,
        "forced": bool(a.force),
        "force_reason": a.reason if a.force else None,
        "previous_holder": (lease or {}).get("holder"),
        "note": a.note,
    }
    _write(LEASE_PATH, payload)
    print(f"manager-lease: CLAIMED by {a.session_id} over state={state}; "
          f"expires {payload['expires_at']} unless heartbeated.")
    if a.commit:
        _commit([LEASE_PATH], f"manager-lease: claim by {a.session_id}")
    else:
        print("manager-lease: ⚠️ written but NOT committed. Commit and push it — "
              "another session reads origin, not your working tree.")
    return 0


def cmd_heartbeat(a) -> int:
    lease, readable = read_lease()
    state, msg = grade(lease, readable, a.session_id)
    if state != "held_by_me":
        print(f"::error::manager-lease: you do NOT hold the lease (state={state}). "
              f"{msg}")
        print("::error::manager-lease: STAND DOWN — stop managing, do not re-claim "
              "silently. If you believe this is wrong, take it to the operator.")
        return 3
    now = _now()
    lease["heartbeat_at"] = _iso(now)
    lease["expires_at"] = _iso(now + timedelta(minutes=TTL_MINUTES))
    _write(LEASE_PATH, lease)
    print(f"manager-lease: heartbeat {lease['heartbeat_at']}; "
          f"expires {lease['expires_at']}.")
    if a.commit:
        _commit([LEASE_PATH], f"manager-lease: heartbeat {lease['heartbeat_at']}")
    return 0


def cmd_release(a) -> int:
    lease, readable = read_lease()
    state, _ = grade(lease, readable, a.session_id)
    if state not in {"held_by_me", "expired", "held_fresh"} and not a.force:
        print(f"manager-lease: nothing to release (state={state}).")
        return 0
    if state == "held_fresh" and not a.force:
        print(f"::error::manager-lease: the lease is held by "
              f"{(lease or {}).get('holder')}, not by you. Releasing someone else's "
              f"lease needs --force --reason.")
        return 3
    now = _now()
    payload = {
        "_doc": _DOC,
        "schema_version": 1,
        "state": "released",
        "holder": None,
        "released_by": a.session_id,
        "released_at": _iso(now),
        "previous_holder": (lease or {}).get("holder"),
        "forced": bool(a.force),
        "force_reason": a.reason if a.force else None,
        "note": a.note,
    }
    _write(LEASE_PATH, payload)
    print(f"manager-lease: RELEASED by {a.session_id}. The next session may claim.")
    if a.commit:
        _commit([LEASE_PATH], f"manager-lease: release by {a.session_id}")
    else:
        print("manager-lease: ⚠️ written but NOT committed — a release nobody can "
              "see keeps the next manager locked out until the TTL expires.")
    return 0


def _self_test() -> int:
    ok = True

    def check(label, got, want):
        nonlocal ok
        good = got == want
        ok &= good
        print(f"  self-test ({label}): {'PASS' if good else f'FAIL got={got!r}'}")

    t0 = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    fresh = {"state": "held", "holder": "S1", "claimed_at": _iso(t0),
             "heartbeat_at": _iso(t0)}

    check("absent is claimable, and is NOT 'released'",
          grade(None, True, "S2", now=t0)[0], "absent")
    check("UNREADABLE FAILS CLOSED — 'we did not look' never claims",
          grade(None, False, "S2", now=t0)[0], "unreadable")
    check("an unreadable lease is not in the claimable set",
          "unreadable" in CLAIMABLE, False)
    check("a fresh lease held by someone else REFUSES",
          grade(fresh, True, "S2", now=t0 + timedelta(minutes=5))[0], "held_fresh")
    check("the holder sees held_by_me, not held_fresh",
          grade(fresh, True, "S1", now=t0 + timedelta(minutes=5))[0], "held_by_me")
    check("THE TAKEOVER PATH: past the TTL it is claimable by a stranger",
          grade(fresh, True, "S2", now=t0 + timedelta(minutes=TTL_MINUTES + 1))[0],
          "expired")
    check("EXACTLY AT the TTL it is still held — the boundary is not a takeover",
          grade(fresh, True, "S2", now=t0 + timedelta(minutes=TTL_MINUTES))[0],
          "held_fresh")
    check("the DISPLACED holder is told it no longer holds it",
          grade({"state": "held", "holder": "S2", "heartbeat_at": _iso(t0)},
                True, "S1", now=t0 + timedelta(minutes=1))[0], "held_fresh")
    check("an explicit release is claimable",
          grade({"state": "released", "holder": None, "released_by": "S1"},
                True, "S2", now=t0)[0], "released")
    check("a lease with NO readable timestamp cannot be shown live, so it expires",
          grade({"state": "held", "holder": "S1"}, True, "S2", now=t0)[0], "expired")
    check("a garbage timestamp is not silently read as fresh",
          grade({"state": "held", "holder": "S1", "heartbeat_at": "yesterday-ish"},
                True, "S2", now=t0)[0], "expired")
    check("a heartbeat in the FUTURE (clock skew) is not treated as expired",
          grade({"state": "held", "holder": "S1",
                 "heartbeat_at": _iso(t0 + timedelta(hours=5))},
                True, "S2", now=t0)[0], "held_fresh")
    check("the TTL is the documented 90 minutes", TTL_MINUTES, 90)
    check("the heartbeat target sits well inside the TTL",
          HEARTBEAT_TARGET_MINUTES < TTL_MINUTES, True)

    print("manager-lease self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--session-id", default=os.environ.get("CLAUDE_SESSION_ID"),
                       help="this session's id (defaults to $CLAUDE_SESSION_ID)")
        p.add_argument("--commit", action="store_true",
                       help="git add+commit the lease (never pushes)")
        p.add_argument("--force", action="store_true")
        p.add_argument("--reason", default=None)
        p.add_argument("--note", default=None)

    for name, fn in (("status", cmd_status), ("claim", cmd_claim),
                     ("heartbeat", cmd_heartbeat), ("release", cmd_release)):
        p = sub.add_parser(name)
        common(p)
        p.set_defaults(fn=fn)

    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    if not a.cmd:
        ap.print_help()
        return 2
    if a.cmd != "status" and not a.session_id:
        print("::error::manager-lease: --session-id is required (or set "
              "$CLAUDE_SESSION_ID). A lease with nobody's name on it names nobody.")
        return 2
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
