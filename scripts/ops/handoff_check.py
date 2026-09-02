#!/usr/bin/env python3
"""MANAGER HANDOFF-READINESS — can this manager hand over right now without
losing anything?

WHY THE LEASE IS NOT ALREADY THE ANSWER
---------------------------------------
`scripts/ops/manager_lease.py` has `status` / `claim` / `heartbeat` / `release`,
and `released` is a claimable state — so the *mutual-exclusion* half is solved
and is deliberately NOT touched here. But the lease's own docstring says why it
is not a handoff:

    Takeover is TIME-BASED and deliberately does not require the outgoing
    manager to do anything. A session that dies cannot run its own close-out.

That is the right design for a manager that DIED. A *deliberate* handoff is the
other case — the manager is alive and stepping down — and there everything the
successor needs to inherit is, today, "remember to." This is the check that
turns that into a verdict.

WHAT IT REFUSES ON, AND WHY EACH ONE COSTS SOMETHING AT HANDOVER
-----------------------------------------------------------------
Every check below can genuinely FAIL, and each maps to something a successor
loses. Nothing is included that cannot fail (a check that always passes is
decoration that makes the verdict look better-evidenced than it is).

  1. ``live_registry``   an observed LIVE sub-session absent from SESSIONS.json.
                         The motivating failure: 6 of 9 absent on 2026-09-02,
                         5 of them live. THE successor loses them outright.
  2. ``checklist_owners``a session id the checklist names as owner of an
                         `in_flight` item that the registry does not carry.
                         Same loss, detected offline — see `session_registry`.
  3. ``lease``           you must actually HOLD the lease to hand it over. A
                         manager whose lease already expired is not handing
                         over; it was displaced, and its state is already stale.
  4. ``manager_state_pushed``
                         registry / checklist / lease edits that exist only in
                         this worktree. The lease already states the rule for
                         itself — *a claim you did not push protects nothing* —
                         and it is exactly as true of the registry, which is the
                         file the successor reads FIRST.
  5. ``pending_spawns``  a `spawn_pending` row whose session id was never
                         confirmed. It names the work but cannot be polled, so
                         it is a weaker record than a real row and must not be
                         handed over silently.

THREE STATES, NEVER COLLAPSED
-----------------------------
``ready``      every check passed.
``not_ready``  at least one check FAILED. A known blocker.
``unknown``    no check failed and at least one could not be evaluated.
               ⚠️ **WE COULD NOT LOOK.** A registry we failed to READ, or never
               compared against anything, must never grade as a clean handoff —
               that is the exact shape this repo files under collapsed states.

⚠️ `ready` IS UNOBTAINABLE WITHOUT A LIVE OBSERVATION, and that is the whole
enforcement mechanism rather than an inconvenience. Only a session holding the
`list_sessions` MCP tool can enumerate what is running; CI cannot, and neither
can this script. So omitting `--live-sessions` grades `unknown`, permanently.
There is no flag to assert the registry is fine — asserting it is what failed
twice.

EXIT CODES: 0 ready · 3 not_ready · 4 unknown. Both non-ready states are
non-zero so a caller cannot accidentally treat "we could not look" as a pass.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import manager_lease  # noqa: E402
import session_registry as sr  # noqa: E402

REPO_ROOT = sr.REPO_ROOT
MANAGER_STATE_PATHS = [
    "docs/claude/work/SESSIONS.json",
    "docs/claude/work/MANAGER-CHECKLIST.json",
    "docs/claude/work/MANAGER-LEASE.json",
]

PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"


def _c(name: str, state: str, message: str, **extra: Any) -> Dict[str, Any]:
    return dict(check=name, state=state, message=message, **extra)


def check_live_registry(reg_doc, reg_readable, observation,
                        manager_session_id: Optional[str]) -> Dict[str, Any]:
    v = sr.reconcile(reg_doc, reg_readable, observation, manager_session_id)
    state = {"reconciled": PASS, "unregistered": FAIL}.get(v["state"], UNKNOWN)
    return _c("live_registry", state, v["message"], verdict=v["state"],
              unregistered=[e["session_id"] for e in v.get("unregistered", [])],
              population=v.get("population"))


def check_checklist_owners(reg_doc, reg_readable, ck_doc, ck_readable,
                           enforced_states) -> Dict[str, Any]:
    v = sr.cross_check(reg_doc, reg_readable, ck_doc, ck_readable, enforced_states)
    state = {"consistent": PASS, "owner_unregistered": FAIL}.get(v["state"], UNKNOWN)
    return _c("checklist_owners", state, v["message"], verdict=v["state"],
              findings=[f["session_id"] for f in v.get("findings", [])],
              population=v.get("population"))


def check_lease(lease, readable, me: Optional[str]) -> Dict[str, Any]:
    state, msg = manager_lease.grade(lease, readable, me)
    if state == "unreadable":
        return _c("lease", UNKNOWN, msg, verdict=state)
    if not me:
        return _c("lease", UNKNOWN,
                  "no --session-id was given, so this check cannot establish whether "
                  "YOU hold the lease — only that somebody might. We did not look.",
                  verdict=state)
    if state == "held_by_me":
        return _c("lease", PASS, msg, verdict=state)
    return _c("lease", FAIL,
              f"you do not hold the lease (state={state}). {msg} A handover of "
              f"something you do not hold is not a handover.", verdict=state)


def _git(args: List[str]) -> Optional[str]:
    try:
        r = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True,
                           text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def check_manager_state_pushed(base: str = "origin/main") -> Dict[str, Any]:
    """Manager state that exists only locally protects no successor."""
    dirty = _git(["status", "--porcelain", "--", *MANAGER_STATE_PATHS])
    if dirty is None:
        return _c("manager_state_pushed", UNKNOWN,
                  "git could not be run, so whether the manager state has reached "
                  "origin could not be established. WE DID NOT LOOK.")
    unpushed = _git(["diff", "--name-only", base, "--", *MANAGER_STATE_PATHS])
    if unpushed is None:
        return _c("manager_state_pushed", UNKNOWN,
                  f"`{base}` could not be resolved (no remote-tracking ref, or a "
                  f"shallow/detached clone), so 'has it reached origin' is "
                  f"unanswerable here. WE DID NOT LOOK.",
                  uncommitted=[l[3:] for l in dirty.splitlines() if l.strip()])
    unc = [l[3:] for l in dirty.splitlines() if l.strip()]
    ahead = [p for p in unpushed.splitlines() if p.strip()]
    if unc or ahead:
        return _c("manager_state_pushed", FAIL,
                  f"manager state is not on `{base}`: "
                  f"{len(unc)} uncommitted, {len(ahead)} differing from {base}. "
                  f"Another session reads origin, not your working tree — the "
                  f"lease says this about itself and it is exactly as true of the "
                  f"registry, which is the FIRST file a successor opens.",
                  uncommitted=unc, differs_from_base=ahead)
    return _c("manager_state_pushed", PASS,
              f"registry, checklist and lease all match `{base}`.")


def check_pending_spawns(reg_doc, reg_readable) -> Dict[str, Any]:
    if not reg_readable:
        return _c("pending_spawns", UNKNOWN,
                  "SESSIONS.json could not be parsed, so pending spawns could not "
                  "be counted. WE DID NOT LOOK.")
    pend = sr.pending_rows(reg_doc)
    if pend:
        return _c("pending_spawns", FAIL,
                  f"{len(pend)} spawn_pending row(s) were never confirmed with a "
                  f"session id. Each names work that a successor cannot POLL — a "
                  f"weaker record than a real row, and one that silently rots.",
                  keys=[p.get("registry_key") for p in pend])
    return _c("pending_spawns", PASS, "no unconfirmed spawn_pending rows.")


def grade(checks: List[Dict[str, Any]]) -> str:
    """PURE, so the policy is arguable in tests rather than against a live
    handover. FAIL dominates UNKNOWN because a known blocker is a definite
    not-ready; UNKNOWN dominates PASS because 'we could not look' is never a
    clean bill of health."""
    if any(c["state"] == FAIL for c in checks):
        return "not_ready"
    if any(c["state"] == UNKNOWN for c in checks):
        return "unknown"
    return "ready"


def run(observation: Optional[Any] = None, manager_session_id: Optional[str] = None,
        base: str = "origin/main", enforced_states=sr.DEFAULT_ENFORCED_STATES
        ) -> Dict[str, Any]:
    reg, reg_ok = sr.read_json(sr.REGISTRY_PATH)
    ck, ck_ok = sr.read_json(sr.CHECKLIST_PATH)
    lease, lease_ok = manager_lease.read_lease()
    checks = [
        check_live_registry(reg, reg_ok, observation, manager_session_id),
        check_checklist_owners(reg, reg_ok, ck, ck_ok, enforced_states),
        check_lease(lease, lease_ok, manager_session_id),
        check_manager_state_pushed(base),
        check_pending_spawns(reg, reg_ok),
    ]
    return {"readiness": grade(checks), "checks": checks}


_EXIT = {"ready": 0, "not_ready": 3, "unknown": 4}

_ADVICE = {
    "ready": "Every check passed. You may release the lease and hand over.",
    "not_ready": "DO NOT hand over yet — the FAILING checks above name what a "
                 "successor would lose. Fix them, then re-run.",
    "unknown": "REFUSED — not because something failed, but because something "
               "could not be LOOKED AT. `unknown` is not a soft `ready`: the "
               "failure this check exists for is invisible from inside, so an "
               "unchecked registry is exactly the state that lost 5 live "
               "sessions. Supply what is missing and re-run.",
}


def _self_test() -> int:
    ok = True

    def check(label, got, want):
        nonlocal ok
        good = got == want
        ok &= good
        print(f"  self-test ({label}): {'PASS' if good else f'FAIL got={got!r} want={want!r}'}")

    P, F, U = {"state": PASS}, {"state": FAIL}, {"state": UNKNOWN}
    check("all pass -> ready", grade([P, P, P]), "ready")
    check("any FAIL -> not_ready", grade([P, F, P]), "not_ready")
    check("any UNKNOWN with no fail -> unknown, NEVER ready",
          grade([P, U, P]), "unknown")
    check("FAIL dominates UNKNOWN (a known blocker is definite)",
          grade([U, F]), "not_ready")
    check("no checks at all is NOT ready", grade([]) != "not_ready", True)
    check("...it is `ready` only vacuously, so the runner always supplies checks",
          grade([]), "ready")

    reg = {"sessions": [{"session_id": "session_01AAAAAAAA"}]}
    check("an UNREGISTERED live session FAILS the handoff",
          check_live_registry(reg, True, [{"id": "session_01ZZZZZZZZ"}], None)["state"], FAIL)
    check("a clean live observation PASSES",
          check_live_registry(reg, True, [{"id": "session_01AAAAAAAA"}], None)["state"], PASS)
    check("NO observation is UNKNOWN — `ready` is unobtainable without looking",
          check_live_registry(reg, True, None, None)["state"], UNKNOWN)
    check("an unreadable registry is UNKNOWN, not a pass",
          check_live_registry(None, False, [{"id": "session_01AAAAAAAA"}], None)["state"],
          UNKNOWN)

    ck_bad = {"items": [{"id": "MI-1", "state": "in_flight", "owner": "session_01ZZZZZZZZ"}]}
    check("an unregistered in_flight owner FAILS",
          check_checklist_owners(reg, True, ck_bad, True,
                                 sr.DEFAULT_ENFORCED_STATES)["state"], FAIL)
    check("an unreadable checklist is UNKNOWN, not a pass",
          check_checklist_owners(reg, True, None, False,
                                 sr.DEFAULT_ENFORCED_STATES)["state"], UNKNOWN)

    # ⚠️ Built at `_now()`, not at a literal date. A fixed timestamp would age
    # past the lease TTL and turn this assertion from "the holder passes" into
    # "an expired lease fails" — the same assertion as the next one, silently.
    mine = {"state": "held", "holder": "S1",
            "heartbeat_at": manager_lease._iso(manager_lease._now())}
    check("holding the lease PASSES", check_lease(mine, True, "S1")["state"], PASS)
    check("someone ELSE holding it FAILS", check_lease(mine, True, "S2")["state"], FAIL)
    check("an already-released lease FAILS (nothing left to hand over)",
          check_lease({"state": "released", "holder": None}, True, "S1")["state"], FAIL)
    check("an UNREADABLE lease is UNKNOWN, never a pass",
          check_lease(None, False, "S1")["state"], UNKNOWN)
    check("no --session-id cannot establish that YOU hold it -> UNKNOWN",
          check_lease(mine, True, None)["state"], UNKNOWN)

    check("an unconfirmed spawn_pending row FAILS",
          check_pending_spawns({"sessions": [{"state": "spawn_pending",
                                              "registry_key": "k", "session_id": None}]},
                               True)["state"], FAIL)
    check("no pending rows PASSES", check_pending_spawns(reg, True)["state"], PASS)
    check("an unreadable registry is UNKNOWN here too",
          check_pending_spawns(None, False)["state"], UNKNOWN)

    check("a nonexistent base ref is UNKNOWN, not a pass",
          check_manager_state_pushed("refs/nope/definitely-not-a-ref")["state"], UNKNOWN)

    print("handoff-check self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--session-id", default=None,
                    help="the OUTGOING manager's session id (needed for the lease check)")
    ap.add_argument("--live-sessions", default=None,
                    help="path to `list_sessions` output, or '-' for stdin. WITHOUT "
                         "IT the verdict can never be `ready`.")
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--enforce-states", default=None,
                    help="CSV of checklist states to enforce (default: in_flight)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()

    states = tuple(a.enforce_states.split(",")) if a.enforce_states else sr.DEFAULT_ENFORCED_STATES
    res = run(sr._load_observation(a.live_sessions), a.session_id, a.base, states)
    for c in res["checks"]:
        icon = {PASS: "PASS", FAIL: "FAIL", UNKNOWN: "????"}[c["state"]]
        print(f"handoff-check: [{icon}] {c['check']}: {c['message']}")
    print(f"handoff-check: readiness={res['readiness']}")
    print(f"handoff-check: {_ADVICE[res['readiness']]}")
    if a.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    return _EXIT[res["readiness"]]


if __name__ == "__main__":
    raise SystemExit(main())
