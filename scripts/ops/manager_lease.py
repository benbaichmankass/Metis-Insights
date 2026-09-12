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
``unrefreshable``
                 past the TTL, **and `main` is red**, so a heartbeat could not
                 have landed however promptly it was opened. NOT claimable.
                 ⚠️ **`expired` and `unrefreshable` are opposite facts and only
                 one of them means nobody is managing.** See below.
``expired_unverified``
                 past the TTL, and `main`'s state COULD NOT BE READ. Claimable
                 — deliberately, because failing closed here would make the
                 lease unclaimable exactly when a manager has genuinely DIED
                 and CI is unreachable, which is the case takeover exists for —
                 but the message says plainly that the `unrefreshable` case was
                 not ruled out.

WHY `unrefreshable` EXISTS
-------------------------
Tracked by:
BL-20260909-A-BASE-BRANCH-RED-CAN-EXPIRE-THE-MANAGER-LEASE-OF-A-SESSION-THAT-IS-ALIVE-AND-HEARTBEATING

⚠️ THAT ID IS ON ONE LINE ON PURPOSE AND MUST STAY THERE. It was originally
wrapped inside a parenthetical heading, which split it across a newline;
`check_backlog_refs.py` then read the truncated prefix, resolved it to nothing,
and failed the PR — correctly. A reference that does not resolve reads as
tracked while being tracked by nobody, which is the whole point of that guard.
Do not re-wrap it to fit the column.
MEASURED 2026-09-09T08:05:10Z: `status` read `heartbeat_at=07:34:57Z` against a
90-minute TTL, while the holder had heartbeated THREE more times (07:46:27,
07:54:48, 07:56:42) — every one trapped in PR #11515, which could not merge
because an unrelated guard was red **on clean main**. So a session that was
alive, working and beating on cadence read as expired, and a session arriving
cold would have found it claimable.

⚠️ **THE COUPLING NOBODY DESIGNED.** The lease lives in the repo precisely so it
survives its holder's DEATH and is readable COLD — correct, and why it cannot be
session state. But that makes its refresh a MERGE, and a merge is gated on the
repo being green. **Lease liveness therefore depends on CI health, a completely
unrelated property.**

⚠️ **THIS IS NOT THE 2026-09-07 PACING CLASS.** That one is a heartbeat stamped
when its PR opens and merged ~60 min later, so it lands two-thirds through its
own TTL; the fix there is to merge sooner. This one is not fixable by pacing at
all — when every PR is blocked, no heartbeat lands however promptly it is
opened. Conflating them makes the wrong fix look sufficient.

⚠️ **WHAT THIS DOES NOT DO, STATED PLAINLY.** It does NOT make a heartbeat reach
`main` while `main` is red — that is the row's option (a)/(c) and would need a
landing path outside the guard set. This is option (b): the TTL is suspended and
**the reason is recorded**, so the register says *"could not refresh"* instead of
silently reading *expired*. A blocked manager still cannot refresh; what changes
is that its successor is no longer told the lease is free.

⚠️ **AND IT IS EMPHATICALLY NOT A TTL WIDENING OR A GUARD BYPASS**, both of which
that row rules out by name. The TTL is unchanged at 90 minutes and no guard is
skipped.

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

#: `expired_unverified` IS claimable, on purpose. Failing closed there would make
#: the lease unclaimable exactly when a manager has DIED and CI is unreachable —
#: the case takeover exists for — so the uncertainty is carried in the MESSAGE
#: rather than in the refusal.
CLAIMABLE = {"expired", "expired_unverified", "released", "absent"}
REFUSING = {"held_fresh", "unreadable", "unrefreshable"}

#: How `main`'s CI was read. Three values, never collapsed: a repo we could not
#: ask about is not a green one.
MAIN_GREEN = "green"
MAIN_RED = "red"
MAIN_UNKNOWN = "unknown"
MAIN_STATES = (MAIN_GREEN, MAIN_RED, MAIN_UNKNOWN)


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


def main_ci_state(timeout: float = 8.0) -> Tuple[str, str]:
    """Is `main` mergeable right now? ``(state, detail)`` over ``MAIN_STATES``.

    ⚠️ THREE VALUES AND `unknown` IS LOAD-BEARING. No token, no network, a
    rate-limit, an unparseable payload — every one of them is *we could not
    look*, and reporting any of them as `green` would silently restore the
    exact collapse this function exists to prevent. It is the value this
    returns most often outside CI, so it is the one written first.

    ⚠️ IT DOES NOT CLASSIFY *WHY* MAIN IS RED, and that is deliberate rather
    than lazy. The row this implements says "red on a guard unrelated to the
    lease", but the property that matters to a heartbeat is only whether a
    merge can happen at all — a red caused BY the lease file blocks it just as
    completely. Classifying the cause would add a judgement the caller cannot
    check, to reach the same answer.

    BOUNDED: one request, `timeout` seconds. A manager reading `status` must
    never hang on GitHub being slow — an unreachable API grades `unknown`,
    which is claimable, so a slow network cannot deadlock a takeover.
    """
    import urllib.error
    import urllib.request

    token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if not token:
        return MAIN_UNKNOWN, "no GITHUB_TOKEN/GH_TOKEN in the environment"

    repo = os.environ.get("GITHUB_REPOSITORY", "benbaichmankass/Metis-Insights")
    url = f"https://api.github.com/repos/{repo}/commits/main/check-runs?per_page=100"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "manager_lease",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        return MAIN_UNKNOWN, f"could not read main's check runs: {exc}"

    runs = payload.get("check_runs")
    if not isinstance(runs, list):
        return MAIN_UNKNOWN, "the check-runs payload carried no `check_runs` list"
    if not runs:
        # ⚠️ NOT green. Zero check runs on main is the same shape as the
        # zero-check trap CLAUDE.md documents for PRs: it renders identically
        # to "all passed" and to "nothing ran".
        return MAIN_UNKNOWN, "main's head reported ZERO check runs — not a pass"

    failed = sorted({r.get("name") for r in runs
                     if r.get("conclusion") in ("failure", "timed_out")})
    if failed:
        return MAIN_RED, "red on " + ", ".join(x for x in failed if x)
    pending = [r for r in runs if r.get("status") != "completed"]
    if pending:
        return MAIN_UNKNOWN, (f"{len(pending)} check(s) still running on main — "
                              "not yet a pass and not a failure")
    return MAIN_GREEN, f"{len(runs)} check(s) on main, none failing"


def _past_ttl(holder: str, age_min: Optional[float], ttl_minutes: int,
              main_ci: str) -> Tuple[str, str]:
    """A lease past its TTL — but WHY it is past it decides whether to claim.

    ⚠️ The whole point of this split: a holder that COULD NOT refresh and one
    that DID NOT are opposite facts, and only the second means nobody is
    managing. Collapsing them is what let a live, heartbeating manager read as
    claimable on 2026-09-09.
    """
    how = (f"last heartbeat {age_min:.0f} min ago — past the {ttl_minutes} min TTL"
           if age_min is not None else
           "and it carries no readable heartbeat_at or claimed_at, so its "
           "freshness cannot be established")

    if main_ci == MAIN_RED:
        return "unrefreshable", (
            f"held by {holder}, {how} — BUT `main` is RED, so a heartbeat could "
            f"not have landed however promptly it was opened. The lease refreshes "
            f"by MERGING, and a merge is gated on the repo being green, so this "
            f"says NOTHING about whether {holder} is alive. NOT claimable: "
            f"'could not refresh' and 'did not refresh' are opposite facts and "
            f"only the second means nobody is managing. Get `main` green, or "
            f"claim with --force --reason if you have independent evidence that "
            f"{holder} is gone.")

    if main_ci == MAIN_UNKNOWN:
        return "expired_unverified", (
            f"held by {holder}, {how}. Claimable — but ⚠️ `main`'s CI state could "
            f"NOT be read, so the `unrefreshable` case was not ruled out: if "
            f"`main` is red, {holder} may be alive and simply unable to land a "
            f"heartbeat. Claiming is still permitted, deliberately, because "
            f"refusing here would make the lease unclaimable exactly when a "
            f"manager has DIED and CI is unreachable. Say in your claim that you "
            f"could not check.")

    return "expired", (
        f"held by {holder}, {how}. Claimable, and `main` is GREEN — so a "
        f"heartbeat COULD have landed and did not. ⚠️ If that session is in fact "
        f"still alive it will discover on its next heartbeat that it no longer "
        f"holds the lease and stand down; that is the designed takeover, not a "
        f"race.")


def grade(lease: Optional[Dict[str, Any]], readable: bool, me: Optional[str],
          now: Optional[datetime] = None,
          ttl_minutes: int = TTL_MINUTES,
          main_ci: str = MAIN_UNKNOWN) -> Tuple[str, str]:
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
        return _past_ttl(holder, None, ttl_minutes, main_ci)

    age_min = (now - beat).total_seconds() / 60.0
    if age_min > ttl_minutes:
        return _past_ttl(holder, age_min, ttl_minutes, main_ci)

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


def _resolve_main_ci(a) -> Tuple[str, str]:
    """`main`'s CI state for this invocation.

    ⚠️ ON BY DEFAULT. A default-off flag in front of this would leave the
    collapse it fixes live for everybody who does not know to pass it — the
    shape this repo forbids. `--no-check-main` is the deliberate opt-out and
    grades `unknown`, which is CLAIMABLE, so opting out can only ever make the
    tool more permissive and never silently block a takeover.
    """
    if getattr(a, "no_check_main", False):
        return MAIN_UNKNOWN, "--no-check-main: we did not look"
    return main_ci_state()


def cmd_status(a) -> int:
    lease, readable = read_lease()
    main_ci, why = _resolve_main_ci(a)
    state, msg = grade(lease, readable, a.session_id, main_ci=main_ci)
    print(f"manager-lease: state={state}")
    print(f"manager-lease: {msg}")
    if lease and lease.get("holder"):
        print(f"manager-lease: holder={lease.get('holder')} "
              f"claimed_at={lease.get('claimed_at')} "
              f"heartbeat_at={lease.get('heartbeat_at')}")
    # Printed ALWAYS, not only when it changed the verdict: a reader needs to
    # know the basis was checked even when the lease is fresh, or `main_ci` is
    # invisible exactly when someone later asks why a verdict was what it was.
    print(f"manager-lease: main_ci={main_ci} ({why})")
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
    main_ci, why = _resolve_main_ci(a)
    state, msg = grade(lease, readable, a.session_id, main_ci=main_ci)
    if state == "unrefreshable" and not a.force:
        print(f"manager-lease: main_ci={main_ci} ({why})")
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
    # ⚠️ THE BASIS IS NOW EXPLICIT. `expired` means the holder COULD have
    # refreshed and did not, which is only knowable if `main` was green — so
    # these cases state MAIN_GREEN rather than relying on a default. Before the
    # split they read `expired` on no basis at all, which is the defect.
    check("THE TAKEOVER PATH: past the TTL, main GREEN, claimable by a stranger",
          grade(fresh, True, "S2", now=t0 + timedelta(minutes=TTL_MINUTES + 1),
                main_ci=MAIN_GREEN)[0],
          "expired")
    check("PAST THE TTL WITH MAIN RED IS *NOT* A TAKEOVER — the holder may be "
          "alive and simply unable to land a heartbeat",
          grade(fresh, True, "S2", now=t0 + timedelta(minutes=TTL_MINUTES + 1),
                main_ci=MAIN_RED)[0],
          "unrefreshable")
    check("...and `unrefreshable` is not claimable",
          "unrefreshable" in CLAIMABLE, False)
    check("with main UNREADABLE it is claimable, but SAYS it could not check — "
          "refusing here would break takeover after a genuine death",
          grade(fresh, True, "S2", now=t0 + timedelta(minutes=TTL_MINUTES + 1),
                main_ci=MAIN_UNKNOWN)[0],
          "expired_unverified")
    check("...and `expired_unverified` IS claimable",
          "expired_unverified" in CLAIMABLE, True)
    check("the DEFAULT basis is `unknown`, so an uninformed caller is never "
          "told main is fine",
          grade(fresh, True, "S2", now=t0 + timedelta(minutes=TTL_MINUTES + 1))[0],
          "expired_unverified")
    check("a RED main does not touch a lease that is still fresh",
          grade(fresh, True, "S2", now=t0 + timedelta(minutes=5),
                main_ci=MAIN_RED)[0], "held_fresh")
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
          grade({"state": "held", "holder": "S1"}, True, "S2", now=t0,
                main_ci=MAIN_GREEN)[0], "expired")
    check("...but with main RED that same lease is `unrefreshable`, not expired",
          grade({"state": "held", "holder": "S1"}, True, "S2", now=t0,
                main_ci=MAIN_RED)[0], "unrefreshable")
    check("a garbage timestamp is not silently read as fresh",
          grade({"state": "held", "holder": "S1", "heartbeat_at": "yesterday-ish"},
                True, "S2", now=t0, main_ci=MAIN_GREEN)[0], "expired")
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
        p.add_argument("--no-check-main", action="store_true",
                       help="skip reading main's CI state. Grades main_ci "
                            "`unknown`, which is CLAIMABLE — so this can only "
                            "make the tool more permissive, never block a "
                            "takeover. Use it offline or to avoid the API call.")
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
