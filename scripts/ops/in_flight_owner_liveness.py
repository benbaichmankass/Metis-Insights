#!/usr/bin/env python3
# wiring: manual-only - a session or manager runs this by hand (its live layer
# needs a get_session/list_sessions observation CI cannot produce, same reason
# session_registry.py::reconcile is not CI-wired); deliberately not added to
# run_guards.py in this PR to avoid touching that heavily-contended shared file
"""IS THE SESSION OWNING THIS in_flight WORK OBJECT STILL ALIVE?

WHY THIS EXISTS — MEASURED, NOT HYPOTHETICAL
----------------------------------------------
`docs/claude/work/objects/*.yaml` counts `lifecycle: in_flight` objects against
an 8-slot WIP ceiling (`scripts/ci/check_wip_ceiling.py`). Nothing ever asks
whether the session recorded as `owner:` is still doing anything. A session
that finishes its work, opens its PR, and never flips the object's `lifecycle`
back leaves a WIP slot permanently occupied by nobody — the exact shape of the
sub-session-registry gap `session_registry.py` already fixes for SESSIONS.json,
one layer up, for work objects instead of session rows.

Measured 2026-09-18 (MI-316), by hand, before this script existed: of 8
`in_flight` objects, 3 had a dead owner —
  * `WO-20260912-REPAIR-THE-HEDGE-BOOK-FLAT-READ-THAT` — owner's PR merged
    2026-09-12T06:26Z, session `SESSION_STATUS_BUCKET_COMPLETED` the same
    morning, lifecycle never flipped. Closed by this session (evidence in the
    object's own `verdict`).
  * `WO-20260909-DECISION-M20-EXIT-EVAL-MARGIN-COLLAPSED` — owner field was
    the LITERAL STRING `claude` (never a valid session id), the real owning
    session went `COMPLETED` on 2026-09-10T03:29Z, done_condition re-verified
    live and closed 8 days later.
  * `WO-20260912-UNBLOCK-THE-PROP-ACCOUNT-IT-IS-STARVED` — real-money prop
    account, owner `ARCHIVED`/`COMPLETED` 2026-09-12T12:57Z, work genuinely
    unfinished — flagged (owner cleared, lifecycle left alone) rather than
    closed, because judging whether the underlying fix landed is not this
    detector's job.

That is 3 of 8 — the WIP ceiling was reading 100% "full" while more than a
third of it was nobody's active work.

TWO INDEPENDENT LAYERS, DIFFERENT STRENGTH, NEITHER OPTIONAL
--------------------------------------------------------------
1. ``grade_registry`` — OFFLINE, cheap, runs anywhere (CI included): joins each
   `in_flight` object to `docs/claude/work/SESSIONS.json` by `owns_object`, and
   reads THAT registry's own `state` / `last_observed.state`. ⚠️ **Partial by
   construction, like `session_registry.cross_check`**: it can only be as
   fresh as the registry itself, and the registry can be WRONG in the safe
   direction — the M20 case above had `state: idle` in SESSIONS.json (not a
   terminal string) while the session itself had actually completed 8 days
   earlier. A clean `grade_registry` verdict is therefore evidence of nothing
   MORE than "the registry does not currently claim this is done" — it is not
   proof of life.
2. ``grade_live`` — needs a fresh `get_session`/`list_sessions`-shaped
   observation (session id → `status_bucket`), which only a session holding
   that MCP tool can produce; CI cannot. This is the layer that actually
   caught the M20 case. Mirrors `session_registry.reconcile`'s own
   justification for why the live layer cannot be replaced by the offline one.

THREE STATES, NEVER COLLAPSED, ON BOTH LAYERS
------------------------------------------------
``clean``    every graded `in_flight` object's owner reads alive (or the
             object has no resolvable owner to check against this layer).
``orphaned`` at least one `in_flight` object's owner reads terminal on this
             layer. A REAL finding — the WIP slot is not being worked.
``unknown``  **WE DID NOT LOOK.** No observation was supplied (`grade_live`),
             or the registry could not be read (`grade_registry`), or an
             object's owner could not be resolved against what was supplied.
             Never collapsed into `clean` — an unlooked-at slot is not a
             proven-alive one, which is the same discipline `handoff_check.py`
             applies to its own `ready` verdict.

WHAT THIS DOES NOT DO
------------------------
It never writes an object file — flagging an orphan is a decision about
whether to close it (if the work is verifiably done), reassign it (if it is
not), or leave it for a human, and only a session that has actually read the
object's `done_condition` can make that call. This script's job stops at
telling you WHICH objects need that judgement, and why.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
OBJECTS_DIR = REPO_ROOT / "docs" / "claude" / "work" / "objects"
SESSIONS_PATH = REPO_ROOT / "docs" / "claude" / "work" / "SESSIONS.json"

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover - exercised only in a bare environment
    yaml = None

_STRICT_ID_RE = re.compile(r"\Asession_[A-Za-z0-9]{6,}\Z")

# The registry's OWN `state` / `last_observed.state` vocabulary that means
# "this session is not going to do more work" — read live off SESSIONS.json,
# see its own `state counts` for the full observed vocabulary. Deliberately
# NOT including `idle` — idle is ambiguous (mid-turn-boundary vs genuinely
# done) and the M20 case above proves the registry's own `idle` can be stale
# in the DANGEROUS direction (reading alive when the platform says completed).
# That is exactly why `grade_live` exists as a second, stronger layer.
TERMINAL_REGISTRY_STATES = {
    "completed", "done", "archived", "failed", "aborted", "failed_wrong_repo",
}

# The platform's own terminal buckets (`get_session`/`list_sessions`
# `status_bucket`, lowercased). `blocked` and `working`/`review_ready` are
# deliberately excluded — a BLOCKED session (e.g. waiting on the operator) is
# alive, just stuck, which is a different fact from a session that will never
# touch this object again.
TERMINAL_LIVE_BUCKETS = {
    "session_status_bucket_completed", "session_status_bucket_failed",
}

PASS, FAIL, UNKNOWN = "clean", "orphaned", "unknown"


def _v(state: str, message: str, **extra: Any) -> Dict[str, Any]:
    return dict(state=state, message=message, **extra)


# --------------------------------------------------------------------------- #
# Reading the objects store
# --------------------------------------------------------------------------- #
def read_in_flight_objects(objects_dir: Path = OBJECTS_DIR
                           ) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Every `lifecycle: in_flight` object, plus a list of files that failed
    to parse (so a parse failure is reported, never silently skipped)."""
    if yaml is None:
        return [], ["PyYAML is not importable — cannot read any object file"]
    objs: List[Dict[str, Any]] = []
    errors: List[str] = []
    if not objects_dir.is_dir():
        return [], [f"{objects_dir} is not a directory"]
    for path in sorted(objects_dir.glob("*.yaml")):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - a bad file must be reported, not skipped
            errors.append(f"{path.name}: {exc}")
            continue
        if not isinstance(doc, dict):
            continue
        if doc.get("lifecycle") != "in_flight":
            continue
        objs.append({
            "id": doc.get("id") or path.stem,
            "owner": doc.get("owner"),
            "path": str(path.relative_to(REPO_ROOT)),
        })
    return objs, errors


def read_sessions_registry(path: Path = SESSIONS_PATH
                           ) -> Tuple[Optional[Any], bool]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None, False
    try:
        return json.loads(text), True
    except json.JSONDecodeError:
        return None, False


def _owns_object_index(reg_doc: Optional[Any]) -> Dict[str, List[Dict[str, Any]]]:
    rows = (reg_doc or {}).get("sessions") if isinstance(reg_doc, dict) else None
    idx: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        oo = r.get("owns_object")
        if isinstance(oo, str) and oo.strip():
            idx.setdefault(oo.strip(), []).append(r)
    return idx


def _row_classify(row: Dict[str, Any]) -> str:
    """`terminal` / `non_terminal` / `unstated`, from a single registry row.

    Two independent fields can disagree (a stale `state` beside a fresher
    `last_observed.state`, or vice versa) — the benefit of the doubt goes to
    ALIVE: if either field reads non-terminal, the row is `non_terminal`. A
    false "still alive" wastes a re-check; a false "orphaned" risks closing
    or reassigning work a session is mid-way through.
    """
    st = row.get("state")
    lo = (row.get("last_observed") or {}).get("state") if isinstance(
        row.get("last_observed"), dict) else None
    vals = [v for v in (st, lo) if isinstance(v, str) and v.strip()]
    if not vals:
        return "unstated"
    if any(v not in TERMINAL_REGISTRY_STATES for v in vals):
        return "non_terminal"
    return "terminal"


# --------------------------------------------------------------------------- #
# Layer 1 — offline, registry-only
# --------------------------------------------------------------------------- #
def grade_registry(objects: Sequence[Dict[str, Any]],
                   reg_doc: Optional[Any], reg_readable: bool) -> Dict[str, Any]:
    if not reg_readable:
        return _v(UNKNOWN, "SESSIONS.json could not be parsed. WE DID NOT LOOK.")
    if not objects:
        return _v(PASS, "no `in_flight` work objects to grade.", population=0)

    idx = _owns_object_index(reg_doc)
    orphaned: List[Dict[str, Any]] = []
    no_row: List[Dict[str, Any]] = []
    for obj in objects:
        rows = idx.get(obj["id"], [])
        if not rows:
            no_row.append(obj)
            continue
        classes = [_row_classify(r) for r in rows]
        if "non_terminal" in classes:
            continue  # alive by at least one row
        if all(c == "terminal" for c in classes):
            orphaned.append({**obj, "registry_rows": rows})
        # else: mix of terminal/unstated with no non_terminal row — ambiguous,
        # falls through to `no_row`-style unknown below via `unresolved`.
        elif "unstated" in classes:
            no_row.append(obj)

    pop = {"in_flight": len(objects), "no_registry_row": len(no_row),
           "orphaned": len(orphaned)}
    if orphaned:
        return _v(FAIL,
                  f"{len(orphaned)} of {len(objects)} `in_flight` object(s) have "
                  f"EVERY matching SESSIONS.json row in a terminal state "
                  f"({sorted(TERMINAL_REGISTRY_STATES)}) — nobody registered as "
                  f"working them is still alive by this record.",
                  orphaned=[o["id"] for o in orphaned], population=pop)
    if no_row:
        return _v(UNKNOWN,
                  f"{len(no_row)} of {len(objects)} `in_flight` object(s) have no "
                  f"SESSIONS.json row naming them via `owns_object` (or only rows "
                  f"with no state recorded) — this could mean a manager holds them "
                  f"directly (SESSIONS.json only registers SUB-sessions) or that "
                  f"the registry itself is incomplete. WE DID NOT LOOK far enough "
                  f"to tell those apart from this layer alone.",
                  no_registry_row=[o["id"] for o in no_row], population=pop)
    return _v(PASS, f"all {len(objects)} `in_flight` object(s) have at least one "
              f"non-terminal SESSIONS.json row.", population=pop)


# --------------------------------------------------------------------------- #
# Layer 2 — live, needs a fresh get_session/list_sessions-shaped observation
# --------------------------------------------------------------------------- #
def normalise_live_buckets(raw: Any) -> Dict[str, str]:
    """session id -> lowercased `status_bucket`, from whatever shape
    `get_session`/`list_sessions` output takes (bare dict, `{"ccr": {...}}`,
    or a `{"data": [...]}` / `{"sessions": [...]}` listing wrapper)."""
    out: Dict[str, str] = {}
    stack = [raw]
    for _ in range(6):
        if not stack:
            break
        cur = stack.pop(0)
        if isinstance(cur, dict) and "ccr" in cur and isinstance(cur["ccr"], (dict, list)):
            stack.insert(0, cur["ccr"])
            continue
        if isinstance(cur, dict):
            for key in ("sessions", "data", "results", "items"):
                inner = cur.get(key)
                if isinstance(inner, (list, dict)):
                    stack.insert(0, inner)
                    break
            else:
                sid = cur.get("id") or cur.get("session_id")
                bucket = cur.get("status_bucket")
                if isinstance(sid, str) and sid.strip() and isinstance(bucket, str):
                    out[sid.strip()] = bucket.strip().lower()
            continue
        if isinstance(cur, list):
            stack = list(cur) + stack
    return out


def grade_live(objects: Sequence[Dict[str, Any]],
               observation: Optional[Any]) -> Dict[str, Any]:
    if not objects:
        return _v(PASS, "no `in_flight` work objects to grade.", population=0)
    if observation is None:
        return _v(UNKNOWN,
                  "no live-session observation was supplied. ⚠️ WE DID NOT LOOK. "
                  "Only a session holding get_session/list_sessions can produce "
                  "this — pass it with --live-sessions. Never read a missing "
                  "observation as 'everyone is alive'.")
    buckets = normalise_live_buckets(observation)
    if not buckets:
        return _v(UNKNOWN,
                  "an observation was supplied and no (session id, status_bucket) "
                  "pair could be read out of it. An unparseable observation is "
                  "not an empty one — refusing to read it as 'nobody is dead'.")

    orphaned, unresolved = [], []
    for obj in objects:
        owner = obj.get("owner")
        if not isinstance(owner, str) or not _STRICT_ID_RE.match(owner.strip()):
            unresolved.append(obj)  # no resolvable session id to check (null, "claude", ...)
            continue
        bucket = buckets.get(owner.strip())
        if bucket is None:
            unresolved.append(obj)  # not in this observation window
            continue
        if bucket in TERMINAL_LIVE_BUCKETS:
            orphaned.append({**obj, "status_bucket": bucket})

    pop = {"in_flight": len(objects), "unresolved": len(unresolved),
           "orphaned": len(orphaned)}
    if orphaned:
        return _v(FAIL,
                  f"{len(orphaned)} of {len(objects)} `in_flight` object(s) have "
                  f"an owner whose PLATFORM status_bucket is terminal "
                  f"({sorted(TERMINAL_LIVE_BUCKETS)}) RIGHT NOW.",
                  orphaned=[o["id"] for o in orphaned], population=pop)
    if unresolved:
        return _v(UNKNOWN,
                  f"{len(unresolved)} of {len(objects)} `in_flight` object(s) "
                  f"could not be resolved against this observation (no owner, an "
                  f"owner that is not a session id, or an owner outside this "
                  f"observation's window). WE DID NOT LOOK for those.",
                  unresolved=[o["id"] for o in unresolved], population=pop)
    return _v(PASS, f"all {len(objects)} `in_flight` object(s) resolved to a "
              f"non-terminal platform status_bucket.", population=pop)


def run(objects_dir: Path = OBJECTS_DIR, sessions_path: Path = SESSIONS_PATH,
        live_observation: Optional[Any] = None) -> Dict[str, Any]:
    objects, parse_errors = read_in_flight_objects(objects_dir)
    reg_doc, reg_readable = read_sessions_registry(sessions_path)
    registry = grade_registry(objects, reg_doc, reg_readable)
    live = grade_live(objects, live_observation)
    return {
        "objects_graded": len(objects),
        "parse_errors": parse_errors,
        "registry": registry,
        "live": live,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _self_test() -> int:
    ok = True

    def check(label: str, got: Any, want: Any) -> None:
        nonlocal ok
        good = got == want
        ok &= good
        print(f"  self-test ({label}): {'PASS' if good else f'FAIL got={got!r} want={want!r}'}")

    objs = [{"id": "WO-A", "owner": "session_01AAAAAAAA", "path": "a.yaml"}]

    # --- grade_registry ---
    reg = {"sessions": [{"owns_object": "WO-A", "state": "working"}]}
    check("a non-terminal registry row PASSES",
          grade_registry(objs, reg, True)["state"], PASS)

    reg_dead = {"sessions": [{"owns_object": "WO-A", "state": "completed"}]}
    check("every matching row terminal -> orphaned FAILS",
          grade_registry(objs, reg_dead, True)["state"], FAIL)

    reg_stale = {"sessions": [{"owns_object": "WO-A", "state": "idle",
                               "last_observed": {"state": "idle"}}]}
    check("`idle` is NOT a terminal string on its own -> PASSES (the M20 gap "
          "this layer cannot close alone)",
          grade_registry(objs, reg_stale, True)["state"], PASS)

    reg_disagree = {"sessions": [{"owns_object": "WO-A", "state": "completed",
                                  "last_observed": {"state": "working"}}]}
    check("state=completed but last_observed=working -> benefit of the doubt, PASSES",
          grade_registry(objs, reg_disagree, True)["state"], PASS)

    reg_none = {"sessions": []}
    check("no matching row at all -> UNKNOWN, never a pass",
          grade_registry(objs, reg_none, True)["state"], UNKNOWN)

    check("an unreadable registry is UNKNOWN, never a pass",
          grade_registry(objs, None, False)["state"], UNKNOWN)

    check("no in_flight objects at all is a vacuous PASS",
          grade_registry([], reg, True)["state"], PASS)

    two_rows_one_alive = {"sessions": [
        {"owns_object": "WO-A", "state": "completed"},
        {"owns_object": "WO-A", "state": "working"},
    ]}
    check("two rows, one still alive -> PASSES (a successor picked it up)",
          grade_registry(objs, two_rows_one_alive, True)["state"], PASS)

    # --- grade_live ---
    check("no observation supplied -> UNKNOWN, never a pass",
          grade_live(objs, None)["state"], UNKNOWN)

    check("an empty/unparseable observation -> UNKNOWN, never 'nobody is dead'",
          grade_live(objs, {"unrelated": True})["state"], UNKNOWN)

    live_dead = [{"id": "session_01AAAAAAAA", "status_bucket": "SESSION_STATUS_BUCKET_COMPLETED"}]
    check("owner's live bucket is terminal -> orphaned FAILS",
          grade_live(objs, live_dead)["state"], FAIL)

    live_blocked = [{"id": "session_01AAAAAAAA", "status_bucket": "SESSION_STATUS_BUCKET_BLOCKED"}]
    check("BLOCKED is alive, not terminal -> PASSES (waiting on the operator "
          "is a different fact from done)",
          grade_live(objs, live_blocked)["state"], PASS)

    live_other = [{"id": "session_01ZZZZZZZZ", "status_bucket": "SESSION_STATUS_BUCKET_COMPLETED"}]
    check("owner not present in this observation window -> UNKNOWN, not orphaned",
          grade_live(objs, live_other)["state"], UNKNOWN)

    objs_bad_owner = [{"id": "WO-B", "owner": "claude", "path": "b.yaml"}]
    check("a non-session-id owner string (e.g. the literal 'claude') cannot be "
          "resolved against live data -> UNKNOWN, the exact M20 shape",
          grade_live(objs_bad_owner, live_dead)["state"], UNKNOWN)

    objs_null_owner = [{"id": "WO-C", "owner": None, "path": "c.yaml"}]
    check("a null owner cannot be resolved against live data either -> UNKNOWN",
          grade_live(objs_null_owner, live_dead)["state"], UNKNOWN)

    ccr_wrapped = {"ccr": {"data": [
        {"id": "session_01AAAAAAAA", "status_bucket": "SESSION_STATUS_BUCKET_COMPLETED"}]}}
    check("the MCP transport's {'ccr': {'data': [...]}} envelope is unwrapped",
          grade_live(objs, ccr_wrapped)["state"], FAIL)

    single_ccr = {"ccr": {"id": "session_01AAAAAAAA",
                          "status_bucket": "SESSION_STATUS_BUCKET_COMPLETED"}}
    check("a single get_session-shaped {'ccr': {...}} response is also read",
          grade_live(objs, single_ccr)["state"], FAIL)

    check("no in_flight objects at all is a vacuous PASS (live layer too)",
          grade_live([], live_dead)["state"], PASS)

    print("in-flight-owner-liveness self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--live-sessions", default=None,
                    help="path to a get_session/list_sessions-shaped JSON "
                         "observation, or '-' for stdin. WITHOUT IT the live "
                         "layer always grades `unknown`.")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()

    live_obs = None
    if a.live_sessions:
        text = sys.stdin.read() if a.live_sessions == "-" else Path(a.live_sessions).read_text()
        try:
            live_obs = json.loads(text)
        except json.JSONDecodeError as exc:
            print(f"in-flight-owner-liveness: --live-sessions did not parse as "
                  f"JSON ({exc}) — treating as NOT SUPPLIED (unknown, not clean).")

    res = run(live_observation=live_obs)
    print(f"in-flight-owner-liveness: {res['objects_graded']} `in_flight` "
          f"object(s) graded.")
    if res["parse_errors"]:
        print(f"in-flight-owner-liveness: {len(res['parse_errors'])} object file(s) "
              f"failed to parse and were EXCLUDED from grading:")
        for e in res["parse_errors"]:
            print(f"  - {e}")
    for layer in ("registry", "live"):
        v = res[layer]
        icon = {PASS: "PASS", FAIL: "FAIL", UNKNOWN: "????"}[v["state"]]
        print(f"in-flight-owner-liveness: [{icon}] {layer}: {v['message']}")
        if v["state"] == FAIL:
            for oid in v.get("orphaned", []):
                print(f"    -> {oid}")
    if a.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    worst = FAIL if any(res[layer]["state"] == FAIL for layer in ("registry", "live")) else (
        UNKNOWN if any(res[layer]["state"] == UNKNOWN for layer in ("registry", "live")) else PASS)
    return {PASS: 0, FAIL: 3, UNKNOWN: 4}[worst]


if __name__ == "__main__":
    raise SystemExit(main())
