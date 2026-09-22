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

AND THE THREE COST LEAKS, BECAUSE THEY ARE THE SAME READ
--------------------------------------------------------
Checklist row E20 names three causes of a ~$1,142 day against ~$250 of ceilings.
All three are answered by joining the same two documents this tool already
reads, so they live here rather than in a fourth script.

MEASURED 2026-09-22T13:02Z by the E20 lane, population **the 60 most recent
sessions on this account** (`list_sessions(mine=true)`), `cost_usd` read from
`external_metadata.usage.cost_usd`:

| | |
|---|--:|
| readable `cost_usd` | **51 of 60** — absent on 9, which must read *could not look*, never `$0` |
| total of the readable | **$5,892.69** |
| **(2) idle tail** — work finished, session **NOT archived** | **22 sessions, $2,088.73** |
| **(3) silent wall** — `status_bucket` BLOCKED | **4 sessions, $1,347.60** |
| of which ONE lane | **$1,343.06** |

⚠️⚠️ **THE SINGLE LARGEST LINE IS ONE BLOCKED LANE, AND IT IS 30x WHAT E20
ESTIMATED.** `session_01XYu2vvg9Qgxoqf4jJQyd8i` ("ENGINEERING LANE MI-305"),
spawned by the previous manager on 2026-09-18, sat **BLOCKED and IDLE for 72.8
hours** holding **$1,343.06** — 447M cache-read and 596k output tokens — and has
**never committed a single line to git**. It is more than E20's entire measured
13-lane day ($1,142) in one session that produced nothing, and E20's cited
instance of this cause (B2, $229) is 1/6th of it. Nothing was looking, which is
the finding rather than the number.

⚠️ **A LANE'S SPEND IS FINAL ONLY ONCE THE LANE IS ARCHIVED**, and that is now
MECHANICAL rather than a warning: `fmt_cost` refuses to render a bare figure. It
always prints `running` or `final` (from `session_status == ARCHIVED`, the only
field that answers it) and the read time. E20's note records the manager making
this exact mistake twice in one day, the second time inside the commit
describing the mistake, because `get_session` on a LIVE lane returns a RUNNING
total and a running total written onto a row reads like a final one.

⚠️ **THE `--offline` HALF EXISTS BECAUSE THE CHEAP SIGNAL WAS MEASURED AND
KILLED.** The obvious way to make this involuntary is a CI check deriving lane
liveness from git: flag an `in_flight` row whose lane has not committed for N
hours. **It does not work, and the measurement says so outright: 5 of the 7
lanes in `status_bucket` WORKING had NEVER committed anything.** Commit recency
does not separate a working lane from a dead one, so that guard would have
redded five healthy lanes. What CI *can* check is the half that needs no session
state at all -- `--offline` -- and the rest needs the MCP tools. Saying which
half is which is the point; a guard built on the killed signal would have been
this repo's favourite kind of green.

USAGE
-----
    # from a session holding the Claude Code Remote MCP tools:
    python3 scripts/ops/lane_reconcile.py --sessions /tmp/sessions.json
    python3 scripts/ops/lane_reconcile.py --sessions /tmp/sessions.json --json

    # from CI or anywhere: the checklist-only half, no session state needed
    python3 scripts/ops/lane_reconcile.py --offline

    python3 scripts/ops/lane_reconcile.py --self-test

EXIT CODES
----------
    0  every claimed lane is alive, nothing is over ceiling, no finished lane is
       still open, or there was nothing to reconcile
    1  at least one row's lane is dead/blocked/missing, over its ceiling, or
       finished-but-unarchived -> ACT
    2  bad input (this is NOT "all clear"; a read that could not happen must
       never render as a clean bill)
"""
from __future__ import annotations

import argparse
import json
import re
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

#: `status_bucket` values meaning the lane's WORK IS OVER. A session in one of
#: these that is not ARCHIVED is the idle tail: it can still accrue and has
#: nothing left to contribute.
FINISHED = frozenset({"SESSION_STATUS_BUCKET_COMPLETED",
                      "SESSION_STATUS_BUCKET_REVIEW_READY"})

#: The ONLY field that answers "is this lane's spend FINAL?". Measured
#: 2026-09-22: 35 of 60 sessions are not archived, and every one of those
#: carries a RUNNING total that reads exactly like a final one.
ARCHIVED = "SESSION_STATUS_ARCHIVED"

COST_MEASURED = "measured"
COST_UNREADABLE = "unreadable"   # no usage block: WE COULD NOT LOOK, not $0


def lane_cost(sess: dict) -> Tuple[Optional[float], str]:
    """(cost_usd, read_state) for one session. NEVER returns 0.0 for a gap.

    ⚠️ Measured 2026-09-22: `external_metadata.usage` is absent on 9 of 60
    sessions, 6 of them in `status_bucket` WORKING -- so the commonest reason to
    be unable to read a lane's cost is that the lane is BUSY. Rendering that as
    `$0.00` would put the cheapest-looking figure on the lanes most likely to be
    expensive, which is the collapsed state
    `docs/CLAUDE-RULES-CANONICAL.md` names: *"an unmeasured quantity is `null`,
    never `0.0`"*.
    """
    usage = (sess.get("external_metadata") or {}).get("usage")
    if not isinstance(usage, dict) or not isinstance(usage.get("cost_usd"),
                                                     (int, float)):
        return None, COST_UNREADABLE
    return float(usage["cost_usd"]), COST_MEASURED


def finality(sess: dict) -> str:
    """`final` once the lane is ARCHIVED, else `running`. E20's own rule."""
    return "final" if sess.get("session_status") == ARCHIVED else "running"


def fmt_cost(sess: dict, read_at: str) -> str:
    """A spend figure that CANNOT be mistaken for a final one.

    ⚠️ THIS FUNCTION EXISTS SO THE RULE IS NOT A REMINDER. E20's note:
    *"A LANE'S SPEND IS FINAL ONLY ONCE THE LANE IS ARCHIVED. Read it there, or
    record it with the read-time and the word `running` beside it. A number with
    no read-time is the same defect as a count that cannot say whether anyone
    looked."* The manager wrote a running total onto a row as a final one twice
    in one day, the second time in the commit describing the first. So no caller
    here is given the option of printing the bare float.
    """
    cost, state = lane_cost(sess)
    if state == COST_UNREADABLE:
        return ("— (no usage block: COULD NOT LOOK, not $0 — "
                f"read {read_at})")
    return f"${cost:,.2f} ({finality(sess)}, read {read_at})"


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

    # ── E20 cause (1): A CEILING NOBODY READS MID-FLIGHT IS NOT A CEILING ──
    # Nothing read spend during a run; the manager set ceilings and looked once,
    # at the end. Six of twelve lanes exceeded theirs, two by more than 6x.
    over_ceiling: List[dict] = []
    for r in rows:
        lane, ceil = r.get("lane"), r.get("ceiling_usd")
        if not lane or not isinstance(ceil, (int, float)) or ceil <= 0:
            continue
        sess = sessions.get(lane)
        if sess is None:
            continue
        cost, state = lane_cost(sess)
        if state != COST_MEASURED or cost <= ceil:
            continue
        over_ceiling.append({
            "id": r["id"], "lane": lane, "cost": cost, "ceiling": float(ceil),
            "over_x": cost / ceil, "finality": finality(sess),
            "bucket": sess.get("status_bucket", ""),
            "title": r.get("title", "")[:90]})

    # ── E20 cause (2): FINISHED LANES KEEP SPENDING ────────────────────────
    # A9 went $37.13 -> $50.78 AFTER its work merged, sitting subscribed to its
    # own PR; E16 $25.38 -> $56.14 the same way. Measured 2026-09-22: 22
    # sessions in this state holding $2,088.73 of RUNNING spend. The fix the row
    # calls free: archive on merge, and do not subscribe a lane to the PR the
    # manager is going to merge anyway.
    #
    # ⚠️ THIS SCANS THE SESSIONS, NOT THE ROWS, and that is deliberate: the 22
    # far outnumber the checklist's live rows, because a lane whose row has been
    # closed is exactly the one nobody looks at again.
    idle_tail = sorted(
        ({"lane": sid, "cost": lane_cost(sess)[0],
          "cost_state": lane_cost(sess)[1],
          "bucket": sess.get("status_bucket", ""),
          "updated_at": sess.get("updated_at", ""),
          "title": (sess.get("title") or "")[:78]}
         for sid, sess in sessions.items()
         if sess.get("status_bucket") in FINISHED
         and sess.get("session_status") != ARCHIVED),
        key=lambda d: -(d["cost"] or 0.0))

    # ── E20 cause (3): AN UNATTENDED LANE HITS A SILENT WALL ────────────────
    # ⚠️ E20 HYPOTHESISED A COMMAND SHAPE (a compound piped git command awaiting
    # a Bash approval) AND SAID IN TERMS THAT IT WAS A HYPOTHESIS, NOT
    # ESTABLISHED. This does not encode it, and deliberately proposes no rule
    # about command shapes: `list_sessions` does not expose the pending action,
    # so nothing here can establish which shapes are refused. What it DOES do is
    # make the condition impossible to sit on -- a BLOCKED lane is surfaced with
    # its spend and its idle time, every run. A human must clear the prompt; the
    # manager's job is to surface it, not to answer it.
    walled = sorted(
        ({"lane": sid, "cost": lane_cost(sess)[0],
          "cost_state": lane_cost(sess)[1],
          "updated_at": sess.get("updated_at", ""),
          "title": (sess.get("title") or "")[:78]}
         for sid, sess in sessions.items()
         if sess.get("status_bucket") == "SESSION_STATUS_BUCKET_BLOCKED"),
        key=lambda d: -(d["cost"] or 0.0))

    return {"stale": stale, "alive": alive, "unknown": unknown,
            "dispatchable": dispatchable, "over_ceiling": over_ceiling,
            "idle_tail": idle_tail, "walled": walled}


def _money(cost: Optional[float], state: str, fin: str, read_at: str) -> str:
    """The same refusal as `fmt_cost`, for a row that already carries the parts."""
    if state != COST_MEASURED or cost is None:
        return f"— (COULD NOT LOOK, not $0 — read {read_at})"
    return f"${cost:,.2f} ({fin}, read {read_at})"


def render(res: Dict[str, Any], read_at: str = "(read time not stated)"
           ) -> Tuple[str, int]:
    out: List[str] = []
    stale, alive, unknown, disp = (res["stale"], res["alive"],
                                   res["unknown"], res["dispatchable"])
    over, tail, walled = (res.get("over_ceiling") or [],
                          res.get("idle_tail") or [],
                          res.get("walled") or [])

    out.append(f"lanes working: {len(alive)}   "
               f"rows claiming a DEAD lane: {len(stale)}   "
               f"lanes we could not look at: {len(unknown)}   "
               f"unblocked rows ready to dispatch: {len(disp)}")
    out.append(f"over ceiling: {len(over)}   "
               f"finished but NOT archived (still able to spend): {len(tail)}   "
               f"walled on a permission prompt: {len(walled)}")
    out.append(f"session state read at: {read_at}  — every figure below is as "
               f"of that instant, and a `running` total is NOT a final one")
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

    if over:
        out.append("")
        out.append("OVER CEILING — E20 cause (1). A ceiling nobody reads "
                   "mid-flight is not a ceiling. Read what the lane has "
                   "produced, THEN kill or re-scope; never interrupt blind:")
        for o in over:
            out.append(f"  [{o['id']}] {o['over_x']:.1f}x  "
                       f"{_money(o['cost'], COST_MEASURED, o['finality'], read_at)}"
                       f" against a ${o['ceiling']:,.2f} ceiling")
            out.append(f"       lane={o['lane']}  {o['title']}")
    if tail:
        total = sum(t["cost"] or 0.0 for t in tail)
        unread = sum(1 for t in tail if t["cost_state"] != COST_MEASURED)
        out.append("")
        out.append(f"FINISHED BUT STILL OPEN — E20 cause (2), and measured as "
                   f"the largest line in the day's spend. {len(tail)} "
                   f"session(s), ${total:,.2f} of RUNNING spend"
                   + (f" ({unread} could not be read, excluded from the total "
                      f"rather than counted as $0)" if unread else "")
                   + ". Each has nothing left to contribute. ARCHIVE THEM, and "
                     "do not subscribe a lane to the PR the manager will merge:")
        for t in tail[:12]:
            out.append(f"  {_money(t['cost'], t['cost_state'], 'running', read_at)}"
                       f"  {t['bucket'].replace('SESSION_STATUS_BUCKET_', '')}"
                       f"  {t['lane']}")
            out.append(f"       {t['title']}")
        if len(tail) > 12:
            out.append(f"  … and {len(tail) - 12} more")
    if walled:
        total = sum(w["cost"] or 0.0 for w in walled)
        out.append("")
        out.append(f"WALLED — E20 cause (3). {len(walled)} lane(s) BLOCKED, "
                   f"${total:,.2f} held. A BLOCKED lane looks alive in any "
                   f"listing that only asks whether the session is archived. "
                   f"A HUMAN must clear the prompt; surface it, never answer it "
                   f"on the operator's behalf:")
        for w in walled:
            out.append(f"  {_money(w['cost'], w['cost_state'], 'running', read_at)}"
                       f"  last activity {w['updated_at'][:19]}  {w['lane']}")
            out.append(f"       {w['title']}")

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

    code = 1 if (stale or unknown or over or tail or walled) else 0
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

    # ── the three E20 cost causes, every branch planted ────────────────────
    def rich(sid: str, bucket: str, cost=None, archived=False,
             title: str = "t") -> dict:
        d = {"id": sid, "status_bucket": bucket, "title": title,
             "session_status": "SESSION_STATUS_ARCHIVED" if archived
                               else "SESSION_STATUS_IDLE"}
        if cost is not None:
            d["external_metadata"] = {"usage": {"cost_usd": cost}}
        return d

    # C1 — cause (1): over ceiling.
    r = reconcile([{"id": "B1", "state": "in_flight", "lane": "s",
                    "ceiling_usd": 35}],
                  {"s": rich("s", WORKING, 270.39)})
    check("C1 a lane over its ceiling is a finding",
          len(r["over_ceiling"]) == 1 and round(r["over_ceiling"][0]["over_x"]) == 8,
          str(r["over_ceiling"]))
    _, code = render(r, "2026-09-22T13:02:00+00:00")
    check("C1b ...and it exits non-zero", code == 1)

    # C1c — CONTROL: under ceiling is silent. Without this, C1 is satisfiable by
    # a rule that flags every lane.
    r = reconcile([{"id": "B1", "state": "in_flight", "lane": "s",
                    "ceiling_usd": 35}],
                  {"s": rich("s", WORKING, 12.0)})
    check("C1c a lane UNDER its ceiling is not a finding",
          not r["over_ceiling"], str(r["over_ceiling"]))

    # C1d — a row with no ceiling cannot be over it. A ceiling of 0 or None is
    # "none declared", not "zero dollars allowed".
    for ceil in (None, 0):
        r = reconcile([{"id": "B1", "state": "in_flight", "lane": "s",
                        "ceiling_usd": ceil}],
                      {"s": rich("s", WORKING, 999.0)})
        check(f"C1d ceiling={ceil!r} means NONE DECLARED, not zero-allowed",
              not r["over_ceiling"], str(r["over_ceiling"]))

    # C2 — cause (2): finished work, session not archived, still able to spend.
    r = reconcile([], {"a": rich("a", "SESSION_STATUS_BUCKET_COMPLETED", 50.78),
                       "b": rich("b", "SESSION_STATUS_BUCKET_REVIEW_READY", 23.78),
                       "c": rich("c", "SESSION_STATUS_BUCKET_COMPLETED", 9.0,
                                 archived=True)})
    check("C2 finished-but-unarchived lanes are findings; ARCHIVED ones are not",
          [t["lane"] for t in r["idle_tail"]] == ["a", "b"], str(r["idle_tail"]))
    check("C2b ...and they are ordered most-expensive first",
          r["idle_tail"][0]["cost"] == 50.78)

    # C3 — cause (3): a BLOCKED lane is surfaced with its spend.
    r = reconcile([], {"w": rich("w", "SESSION_STATUS_BUCKET_BLOCKED", 1343.06,
                                title="MI-305")})
    check("C3 a BLOCKED lane is surfaced with its spend",
          len(r["walled"]) == 1 and r["walled"][0]["cost"] == 1343.06)
    text, code = render(r, "2026-09-22T13:02:00+00:00")
    check("C3b ...loudly, and it exits non-zero",
          "WALLED" in text and "$1,343.06" in text and code == 1)
    check("C3c ...and it says a HUMAN must clear it, never this tool",
          "A HUMAN must clear the prompt" in text)

    # C4 — THE COLLAPSED STATE. A lane with no usage block must NOT read as $0.
    # ⚠️ Measured 2026-09-22: `usage` is absent on 9 of 60 sessions and 6 of
    # those are in bucket WORKING, so the commonest reason to be unable to read
    # a cost is that the lane is BUSY. A fabricated $0 would put the
    # cheapest-looking figure on the lanes most likely to be expensive.
    cost, state = lane_cost(rich("s", WORKING))
    check("C4 a session with no usage block reads UNREADABLE, not 0.0",
          cost is None and state == COST_UNREADABLE, f"{cost!r} {state!r}")
    rendered = fmt_cost(rich("s", WORKING), "2026-09-22T13:02:00+00:00")
    # ⚠️ The assertion is that NO DOLLAR AMOUNT is rendered at all, matched as a
    # figure rather than as the substring "$0" — the refusal text itself says
    # "not $0", and a test that tripped on its own explanation would push the
    # next author to soften the message instead of keeping the property.
    check("C4b ...and renders as COULD NOT LOOK, with NO dollar figure at all",
          "COULD NOT LOOK" in rendered
          and re.search(r"\$[\d,]+\.\d\d", rendered) is None, rendered)

    # C5 — E20's OWN RULE, MECHANICAL: a spend figure may not be rendered bare.
    # The manager wrote a running total onto a row as a final one TWICE in one
    # day, the second time in the commit describing the first.
    live = fmt_cost(rich("s", WORKING, 100.0), "2026-09-22T13:02:00+00:00")
    done = fmt_cost(rich("s", "SESSION_STATUS_BUCKET_COMPLETED", 100.0,
                         archived=True), "2026-09-22T13:02:00+00:00")
    check("C5 a LIVE lane's spend is marked `running`", "running" in live, live)
    check("C5b an ARCHIVED lane's spend is marked `final`", "final" in done, done)
    check("C5c both carry the READ TIME", all("2026-09-22T13:02" in x
                                              for x in (live, done)))

    # ── the OFFLINE half — and the signal that was KILLED on measurement ──
    # O1 — in_flight with no lane. Derivable from the checklist alone.
    o = offline_findings([{"id": "Y", "state": "in_flight", "lane": None}])
    check("O1 offline catches in_flight with no lane", len(o["no_lane"]) == 1)

    # O2 — a BARE spend_usd is refused: it cannot say whether anyone looked.
    o = offline_findings([{"id": "A", "state": "done", "spend_usd": 270.39}])
    check("O2 a bare spend_usd number is a finding",
          len(o["unmarked_spend"]) == 1, str(o["unmarked_spend"]))
    o = offline_findings([{"id": "A", "state": "done",
                           "spend_usd": "$435.05 running, read 2026-09-21T23:00Z"}])
    check("O2b a marked one is not", not o["unmarked_spend"],
          str(o["unmarked_spend"]))
    o = offline_findings([{"id": "A", "state": "done", "spend_usd": None}])
    check("O2c an UNRECORDED spend is not a finding — absent is not wrong",
          not o["unmarked_spend"])

    # O2d — THE BASELINE MUST NOT BE A SILENT HOLE. A grandfathered id is
    # exempt AND COUNTED; any other id carrying the same bare number is not.
    # Without the second half the escape hatch could quietly widen to
    # everything and every run would still print "0 findings".
    bl = sorted(SPEND_MARKER_BASELINE)[0] if SPEND_MARKER_BASELINE else None
    if bl:
        o = offline_findings([{"id": bl, "state": "done", "spend_usd": 1.0},
                              {"id": "NOT-BASELINED", "state": "done",
                               "spend_usd": 1.0}])
        check("O2d a baselined id is exempt, a new one with the same shape is not",
              [u["id"] for u in o["unmarked_spend"]] == ["NOT-BASELINED"],
              str(o["unmarked_spend"]))
        check("O2e ...and the exemption is COUNTED, never silent",
              o["grandfathered_spend"] == 1, str(o))
        text, _ = render_offline(o)
        check("O2f ...and the debt count is printed on every run",
              "spend-marker debt: 1 of" in text, text)

    # O3 — zero in_flight while unblocked rows are queued.
    o = offline_findings([{"id": "A", "state": "queued", "blocked_on": []}])
    text, code = render_offline(o)
    check("O3 offline prints the loud line and exits non-zero",
          "ZERO ROWS IN_FLIGHT" in text and code == 1)
    o = offline_findings([{"id": "W", "state": "in_flight", "lane": "s"},
                          {"id": "A", "state": "queued", "blocked_on": []}])
    text, code = render_offline(o)
    check("O3b ...and stays quiet when a row IS in_flight",
          "ZERO ROWS IN_FLIGHT" not in text and code == 0, text)

    # O4 — the offline half must SAY it read no session state. A CI run that
    # printed a reconciliation without this line would read as a clean bill on
    # lane liveness, which it cannot speak to at all.
    text, _ = render_offline(offline_findings([]))
    check("O4 offline states that it read NO session state",
          "no session state was read" in text, text)

    # P8 — bad input must exit 2, never 0.
    try:
        _sessions_from({"nope": 1})
        check("P8 a malformed dump raises", False, "it returned instead")
    except ValueError:
        check("P8 a malformed dump raises rather than reading as empty", True)

    print("\nself-test: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


# --------------------------------------------------------------------------
# The OFFLINE half — what needs NO session state, so CI can run it
# --------------------------------------------------------------------------
#: The rows carrying a BARE `spend_usd` when this rule was written (2026-09-22),
#: grandfathered by id.
#:
#: ⚠️ THIS IS AN ESCAPE HATCH AND IT IS THE `check_soak_registered.py` PATTERN
#: ON PURPOSE, not a quiet exemption. What makes it acceptable is that it is
#: NOT SILENT: each name is a visible line in a file, in the diff, under a
#: comment saying the list may only SHRINK, and the count prints on every run —
#: so a growing number is visible without anyone auditing this file. A reviewer
#: sees a deliberate act.
#:
#: ⚠️ WHY GRANDFATHER AT ALL RATHER THAN JUST FAILING. All 15 were written
#: before the rule existed, and the fix is a SCHEMA change (`spend_usd` becoming
#: an object carrying the read time and the finality) that touches the checklist's
#: consumers. Failing on them today would put a red line on every PR over rows
#: their authors cannot fix, which is the guard-that-gets-deleted-rather-than-
#: fixed reasoning `check_pr_queue_watch.py` records. New rows are held to the
#: rule from here.
SPEND_MARKER_BASELINE = frozenset((
    "A1",
    "A3",
    "A3b",
    "A8",
    "A9",
    "B1",
    "B2",
    "E10",
    "E11",
    "E13",
    "E15",
    "E16",
    "E3",
    "E9",
    "R1",
))


def offline_findings(rows: List[dict]) -> Dict[str, Any]:
    """The subset of the invariant derivable from the CHECKLIST ALONE.

    ⚠️ WHY THIS IS A SEPARATE, SMALLER FUNCTION RATHER THAN A CI VERSION OF
    `reconcile`. The tempting CI check derives lane liveness from git — flag an
    `in_flight` row whose lane has not committed for N hours. It was MEASURED
    2026-09-22 against live session state and it does not work: **5 of the 7
    lanes in `status_bucket` WORKING had never committed anything at all**, so
    commit recency cannot separate a working lane from a dead one and that guard
    would have redded five healthy lanes. Killed on measurement, the way E20
    asks the command-shape hypothesis to be treated.

    What survives needs no session state and no git at all:

      * `in_flight` with NO lane named — nobody is working it and the row does
        not even say who was supposed to.
      * a `spend_usd` recorded as a bare number with no read-time and no
        `running`/`final` marker — E20's own rule, and the mistake its note
        records the manager making twice in one day.
      * ZERO rows `in_flight` while unblocked rows are `queued` — the headline
        the operator had to notice by hand, twice.
    """
    no_lane = [{"id": r["id"], "title": r.get("title", "")[:90]}
               for r in rows
               if r.get("state") in CLAIMS_A_LANE and not r.get("lane")]

    # ⚠️ A NUMBER WITH NO READ-TIME IS THE SAME DEFECT AS A COUNT THAT CANNOT
    # SAY WHETHER ANYONE LOOKED. A `spend_usd` that is a bare float asserts
    # finality it cannot have unless the lane was archived when it was read, and
    # the row says so nowhere. Accepted shapes: a string carrying `running` or
    # `final`, or an object stating both. A bare number is refused.
    unmarked_spend = []
    grandfathered = 0
    for r in rows:
        v = r.get("spend_usd")
        if v is None:
            continue
        if r.get("id") in SPEND_MARKER_BASELINE:
            grandfathered += 1
            continue
        if isinstance(v, (int, float)):
            unmarked_spend.append({
                "id": r["id"], "value": v,
                "why": "a bare number: it cannot say whether the lane was "
                       "ARCHIVED when this was read, so it reads as final "
                       "while possibly being a running total"})
        elif isinstance(v, str) and not any(
                w in v.lower() for w in ("running", "final")):
            unmarked_spend.append({
                "id": r["id"], "value": v,
                "why": "no `running`/`final` marker"})

    in_flight = [r for r in rows if r.get("state") in CLAIMS_A_LANE]
    dispatchable = [{"id": r["id"], "tier": r.get("tier"),
                     "owner": r.get("owner"), "title": r.get("title", "")[:90]}
                    for r in rows
                    if r.get("state") == "queued" and not (r.get("blocked_on") or [])]
    return {"no_lane": no_lane, "unmarked_spend": unmarked_spend,
            "grandfathered_spend": grandfathered,
            "baseline_size": len(SPEND_MARKER_BASELINE),
            "in_flight": len(in_flight), "dispatchable": dispatchable}


def render_offline(res: Dict[str, Any]) -> Tuple[str, int]:
    out = ["OFFLINE (checklist only — no session state was read, so this says "
           "NOTHING about whether any lane is alive):",
           f"  rows in_flight: {res['in_flight']}   "
           f"in_flight with NO lane: {len(res['no_lane'])}   "
           f"spend recorded without a read-state: {len(res['unmarked_spend'])}   "
           f"unblocked and queued: {len(res['dispatchable'])}",
           f"  spend-marker debt: {res.get('grandfathered_spend', 0)} of "
           f"{res.get('baseline_size', 0)} baselined row(s) still carry a bare "
           f"number — this list may only SHRINK"]
    for n in res["no_lane"]:
        out.append(f"  ✗ [{n['id']}] state claims a lane and names none — "
                   f"{n['title']}")
    for u in res["unmarked_spend"]:
        out.append(f"  ✗ [{u['id']}] spend_usd={u['value']!r}: {u['why']}")
    if res["in_flight"] == 0 and res["dispatchable"]:
        out.append("")
        out.append(f"⚠️  ZERO ROWS IN_FLIGHT WHILE {len(res['dispatchable'])} "
                   f"UNBLOCKED ROWS ARE QUEUED. Dispatch, take the next step, "
                   f"or go to the operator for prioritisation — but do not "
                   f"leave it.")
    code = 1 if (res["no_lane"] or res["unmarked_spend"]
                 or (res["in_flight"] == 0 and res["dispatchable"])) else 0
    return "\n".join(out), code


def _read_at_of(path: Path) -> str:
    """When the session dump was written, from its mtime.

    ⚠️ NOT `datetime.now()`. A dump read from a file five hours after it was
    taken is five hours stale, and stamping the reconciliation with the CURRENT
    time would launder that into a fresh reading — the same defect as a bare
    spend figure, one level up. The mtime is the closest observable to when the
    read actually happened; `--read-at` overrides it when the caller knows
    better.
    """
    try:
        from datetime import datetime, timezone
        return (datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                .isoformat(timespec="seconds"))
    except Exception:  # noqa: BLE001
        return "(dump mtime unreadable — read time UNKNOWN)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sessions", help="path to a list_sessions dump (JSON)")
    ap.add_argument("--offline", action="store_true",
                    help="the checklist-only half: runs anywhere, reads no "
                         "session state, and says so")
    ap.add_argument("--read-at", default=None,
                    help="ISO instant the session dump was taken (default: the "
                         "dump file's mtime)")
    ap.add_argument("--checklist", default=str(CHECKLIST))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()

    if a.self_test:
        return _self_test()
    try:
        rows = json.loads(Path(a.checklist).read_text(encoding="utf-8"))["items"]
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if a.offline:
        res = offline_findings(rows)
        if a.json:
            print(json.dumps(res, indent=2))
            return 1 if (res["no_lane"] or res["unmarked_spend"]) else 0
        text, code = render_offline(res)
        print(text)
        return code

    if not a.sessions:
        print("ERROR: --sessions is required (dump `list_sessions` to a file "
              "first), or pass --offline for the half that needs no session "
              "state. Refusing to print a full reconciliation with no session "
              "state — that would be a clean bill from a read that never "
              "happened.", file=sys.stderr)
        return 2
    try:
        sessions = _sessions_from(json.loads(
            Path(a.sessions).read_text(encoding="utf-8")))
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    read_at = a.read_at or _read_at_of(Path(a.sessions))
    res = reconcile(rows, sessions)
    if a.json:
        print(json.dumps({**res, "read_at": read_at}, indent=2))
        return 1 if any(res[k] for k in ("stale", "unknown", "over_ceiling",
                                         "idle_tail", "walled")) else 0
    text, code = render(res, read_at=read_at)
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
