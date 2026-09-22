#!/usr/bin/env python3
"""Reconcile MANAGER-CHECKLIST rows against the lanes that are actually alive.

THE FAILURE THIS EXISTS TO STOP RECURRING
-----------------------------------------
On 2026-09-22 the manager ran ELEVEN lanes to completion and then left every
one of them idle for over an hour while THIRTY rows sat `queued`, twenty-five
of them Tier-1 with an empty ``blocked_on``. Four of the eleven had not
finished at all — they died BLOCKED, three of them on the same ``git push``
403, and each one said so in its own ``post_turn_summary``:

    E34  "write access denied (git proxy 403); awaiting add_repo approval"
         ...with an unpushed patch file in a container that dies
    E17  "PR creation blocked (403 scope)"
    E27  "investigating PR creation 403"
    E33  "The model's tool call could not be parsed (retry also failed)"

Nothing read those summaries. Nothing compared `state: in_flight` against
whether a session was breathing. The operator had to notice, twice.

⚠️ THE ROOT CAUSE WAS NOT INATTENTION, IT WAS THAT SUPERVISION WAS A MEMORY
TASK. There was no surface on which "row says in_flight, session says failed"
was a visible fact, so it could only be caught by a manager who happened to
re-derive it. That is reason (5) from the operating plan — the one that killed
``DUE.md``: **asking was voluntary.** This makes it a command that answers in
one line, and one whose exit code can fail a check.

⚠️ AND IT IS DELIBERATELY NOT A NEW REGISTER. CLAUDE.md is explicit that a
memo nothing points at is not work, and the 2026-09-21 reset archived eight
registers for exactly this reason. This writes NOTHING. It reads the checklist
and a sessions dump and prints a reconciliation.

WHY IT TAKES A FILE INSTEAD OF CALLING THE API
----------------------------------------------
Session state lives behind an MCP tool (``list_sessions``) that a plain script
cannot call. Rather than pretend otherwise, this takes the dump as an argument.
That is a real limitation and it is stated rather than hidden: **this tool
cannot tell you anything if nobody runs ``list_sessions``.** What it does is
make the comparison mechanical once they have, and make a stale row impossible
to miss. Closing the last gap — running it on a schedule — is the follow-up,
and until that lands, "the pull is built but not connected" applies here too.

USAGE
-----
    # manager: dump `list_sessions` output to a file, then
    python3 scripts/ops/lane_reconcile.py --sessions /tmp/sessions.json
    python3 scripts/ops/lane_reconcile.py --sessions /tmp/sessions.json --json
    python3 scripts/ops/lane_reconcile.py --self-test

EXIT CODES
----------
    0  every claimed lane is alive, or there was nothing to reconcile
    1  at least one row's lane is dead, blocked or missing -> ACT
    2  bad input (this is NOT "all clear"; a read that could not happen must
       never render as a clean bill)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
CHECKLIST = REPO / "docs" / "claude" / "work" / "MANAGER-CHECKLIST.json"

#: States that ASSERT someone is working the row right now. A row in one of
#: these with a dead lane is the defect this tool exists for.
CLAIMS_A_LANE = frozenset({"in_flight"})

#: Session status buckets that mean the lane is NOT working. `blocked` is in
#: here on purpose and it is the one that bit us: a blocked lane looks alive
#: in every listing that only checks "is the session archived", and it is the
#: state all four of 2026-09-22's dead lanes were in.
NOT_WORKING = {
    "SESSION_STATUS_BUCKET_FAILED": "failed",
    "SESSION_STATUS_BUCKET_BLOCKED": "blocked",
    "SESSION_STATUS_BUCKET_COMPLETED": "completed",
    "SESSION_STATUS_BUCKET_REVIEW_READY": "review_ready",
}
WORKING = "SESSION_STATUS_BUCKET_WORKING"


def _sessions_from(payload: Any) -> Dict[str, dict]:
    """Accept either the raw `list_sessions` envelope or a bare list."""
    if isinstance(payload, dict):
        data = payload.get("ccr", payload).get("data", payload.get("data"))
    else:
        data = payload
    if not isinstance(data, list):
        raise ValueError("sessions dump holds no list of sessions")
    return {s["id"]: s for s in data if isinstance(s, dict) and s.get("id")}


def _summary(sess: dict) -> str:
    pts = sess.get("post_turn_summary") or {}
    bits = [pts.get("status_detail"), pts.get("needs_action")]
    return " | ".join(b for b in bits if b) or "(no post-turn summary)"


def reconcile(rows: List[dict], sessions: Dict[str, dict]) -> Dict[str, Any]:
    stale: List[dict] = []
    alive: List[dict] = []
    unknown: List[dict] = []

    for r in rows:
        if r.get("state") not in CLAIMS_A_LANE:
            continue
        lane = r.get("lane")
        if not lane:
            # `in_flight` with no lane at all: nobody is working it and the row
            # does not even name who was supposed to.
            stale.append({"id": r["id"], "lane": None, "why": "no lane named",
                          "detail": "", "title": r.get("title", "")[:90]})
            continue
        sess = sessions.get(lane)
        if sess is None:
            # NOT "assume fine". A lane we cannot see is a lane we cannot
            # vouch for, and saying so is the whole point of the read-state.
            unknown.append({"id": r["id"], "lane": lane,
                            "why": "lane not in the dump — COULD NOT LOOK",
                            "detail": "", "title": r.get("title", "")[:90]})
            continue
        bucket = sess.get("status_bucket", "")
        if bucket == WORKING:
            alive.append({"id": r["id"], "lane": lane,
                          "title": r.get("title", "")[:90]})
        else:
            stale.append({"id": r["id"], "lane": lane,
                          "why": NOT_WORKING.get(bucket, f"bucket {bucket!r}"),
                          "detail": _summary(sess),
                          "title": r.get("title", "")[:90]})

    # The other half of the job: what is there to hand a free lane?
    dispatchable = [
        {"id": r["id"], "tier": r.get("tier"), "owner": r.get("owner"),
         "title": r.get("title", "")[:90]}
        for r in rows
        if r.get("state") == "queued" and not (r.get("blocked_on") or [])
    ]
    return {"stale": stale, "alive": alive, "unknown": unknown,
            "dispatchable": dispatchable}


def render(res: Dict[str, Any]) -> Tuple[str, int]:
    out: List[str] = []
    stale, alive, unknown, disp = (res["stale"], res["alive"],
                                   res["unknown"], res["dispatchable"])

    out.append(f"lanes working: {len(alive)}   "
               f"rows claiming a DEAD lane: {len(stale)}   "
               f"lanes we could not look at: {len(unknown)}   "
               f"unblocked rows ready to dispatch: {len(disp)}")
    out.append("")

    if stale:
        out.append("STALE — the row says in_flight and the lane is not working. "
                   "Each one needs a TRUE state and, if the work is unfinished, "
                   "a re-dispatch:")
        for s in stale:
            out.append(f"  [{s['id']}] {s['why']}  lane={s['lane']}")
            out.append(f"       {s['title']}")
            if s["detail"]:
                out.append(f"       lane said: {s['detail']}")
    if unknown:
        out.append("")
        out.append("COULD NOT LOOK — these are NOT clean. A lane missing from "
                   "the dump is unverified, not fine:")
        for u in unknown:
            out.append(f"  [{u['id']}] lane={u['lane']}  {u['title']}")
    if alive:
        out.append("")
        out.append("WORKING:")
        for a in alive:
            out.append(f"  [{a['id']}] {a['title']}")

    out.append("")
    if not alive and disp:
        out.append(f"⚠️  ZERO LANES WORKING WHILE {len(disp)} UNBLOCKED ROWS ARE "
                   "QUEUED. That is the failure this tool exists for: dispatch, "
                   "or take the next step yourself, or go to the operator for "
                   "prioritisation — but do not leave it.")
    elif disp:
        out.append(f"READY TO DISPATCH ({len(disp)} unblocked):")
        for d in disp[:12]:
            out.append(f"  [{d['id']}] tier={d['tier']} owner={d['owner']}  {d['title']}")
        if len(disp) > 12:
            out.append(f"  … and {len(disp) - 12} more")
    else:
        out.append("Nothing unblocked is queued. If no lane is working either, "
                   "the next step is the operator's call on priority, not "
                   "silence.")

    code = 1 if (stale or unknown) else 0
    return "\n".join(out), code


# --------------------------------------------------------------------------
# self-test — a check that cannot go red is the state half this register
# describes, so every branch here is planted and proven.
# --------------------------------------------------------------------------
def _self_test() -> int:
    ok = True

    def check(label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        print(f"  {'PASS' if passed else 'FAIL'}  {label}"
              + (f" — {detail}" if detail and not passed else ""))
        ok = ok and passed

    def sess(sid: str, bucket: str, detail: str = "") -> dict:
        return {"id": sid, "status_bucket": bucket,
                "post_turn_summary": {"status_detail": detail}}

    print("lane_reconcile self-test")

    # P1 — the exact 2026-09-22 case: a blocked lane under an in_flight row.
    res = reconcile(
        [{"id": "E34", "state": "in_flight", "lane": "s1", "title": "review"}],
        {"s1": sess("s1", "SESSION_STATUS_BUCKET_BLOCKED", "git proxy 403")})
    check("P1 a BLOCKED lane under an in_flight row is stale",
          [s["id"] for s in res["stale"]] == ["E34"], str(res["stale"]))
    check("P1b ...and the lane's own words are carried through",
          "403" in (res["stale"][0]["detail"] if res["stale"] else ""),
          "the post_turn_summary is the evidence; dropping it makes the "
          "finding unactionable")

    # P2 — failed, and P3 — completed. Different causes, same verdict.
    for bucket, name in (("SESSION_STATUS_BUCKET_FAILED", "FAILED"),
                         ("SESSION_STATUS_BUCKET_COMPLETED", "COMPLETED")):
        r = reconcile([{"id": "X", "state": "in_flight", "lane": "s"}],
                      {"s": sess("s", bucket)})
        check(f"P2 a {name} lane under an in_flight row is stale",
              len(r["stale"]) == 1)

    # P4 — in_flight with no lane named at all.
    r = reconcile([{"id": "Y", "state": "in_flight", "lane": None}], {})
    check("P4 in_flight with NO lane is stale", len(r["stale"]) == 1)

    # P5 — the collapsed state. A lane we cannot see must not read as fine.
    r = reconcile([{"id": "Z", "state": "in_flight", "lane": "gone"}], {})
    check("P5 a lane missing from the dump is COULD-NOT-LOOK, not clean",
          len(r["unknown"]) == 1 and not r["stale"])
    _, code = render(r)
    check("P5b ...and it still exits non-zero", code == 1,
          "'we could not look' rendering as a pass is the exact defect "
          "check_collapsed_states.py exists for")

    # N1 — a genuinely working lane is silent.
    r = reconcile([{"id": "W", "state": "in_flight", "lane": "s"}],
                  {"s": sess("s", WORKING)})
    _, code = render(r)
    check("N1 a WORKING lane is not a finding", not r["stale"] and code == 0)

    # N2 — states that do not claim a lane are ignored, whatever their lane says.
    r = reconcile(
        [{"id": "D", "state": "done", "lane": "dead"},
         {"id": "L", "state": "landed_unproven", "lane": "dead"},
         {"id": "Q", "state": "queued", "lane": None},
         {"id": "P", "state": "dropped", "lane": "dead"}],
        {"dead": sess("dead", "SESSION_STATUS_BUCKET_COMPLETED")})
    check("N2 done/landed_unproven/queued/dropped do not claim a lane",
          not r["stale"] and not r["unknown"], str(r))

    # P6 — the dispatch half: blocked rows are NOT dispatchable, unblocked are.
    r = reconcile(
        [{"id": "A", "state": "queued", "blocked_on": []},
         {"id": "B", "state": "queued", "blocked_on": [{"ref": "A"}]},
         {"id": "C", "state": "queued"}],
        {})
    check("P6 only rows with an empty blocked_on are dispatchable",
          sorted(d["id"] for d in r["dispatchable"]) == ["A", "C"],
          str(r["dispatchable"]))

    # P7 — the headline the operator actually needed on 2026-09-22.
    r = reconcile([{"id": "A", "state": "queued", "blocked_on": []}], {})
    text, _ = render(r)
    check("P7 zero lanes + queued work prints the loud line",
          "ZERO LANES WORKING" in text)
    r = reconcile([{"id": "W", "state": "in_flight", "lane": "s"},
                   {"id": "A", "state": "queued", "blocked_on": []}],
                  {"s": sess("s", WORKING)})
    text, _ = render(r)
    check("P7b ...and does NOT print it when a lane is working",
          "ZERO LANES WORKING" not in text)

    # P8 — bad input must exit 2, never 0.
    try:
        _sessions_from({"nope": 1})
        check("P8 a malformed dump raises", False, "it returned instead")
    except ValueError:
        check("P8 a malformed dump raises rather than reading as empty", True)

    print("\nself-test: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sessions", help="path to a list_sessions dump (JSON)")
    ap.add_argument("--checklist", default=str(CHECKLIST))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    if a.self_test:
        return _self_test()
    if not a.sessions:
        print("ERROR: --sessions is required (dump `list_sessions` to a file "
              "first). Refusing to print a reconciliation with no session "
              "state — that would be a clean bill from a read that never "
              "happened.", file=sys.stderr)
        return 2
    try:
        rows = json.loads(Path(a.checklist).read_text(encoding="utf-8"))["items"]
        sessions = _sessions_from(json.loads(
            Path(a.sessions).read_text(encoding="utf-8")))
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    res = reconcile(rows, sessions)
    if a.json:
        print(json.dumps(res, indent=2))
        return 1 if (res["stale"] or res["unknown"]) else 0
    text, code = render(res)
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
