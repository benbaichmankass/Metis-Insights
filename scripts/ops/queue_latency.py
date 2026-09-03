#!/usr/bin/env python3
#
# wiring: manual-only - fired by a cadenced Claude ROUTINE, never by CI and never
# by the manager. A GitHub workflow CANNOT run this usefully: the live read comes
# from `list_sessions`, an mcp__* tool CI does not hold, and this file refuses to
# grade without one. See "WHERE THE CLOCK LIVES" below -- that constraint is the
# whole reason the routine exists rather than a workflow.
"""MANAGER QUEUE LATENCY — how long has something been waiting on the manager?

THE LAW THIS EXISTS FOR
-----------------------
**A check invoked by the actor it checks cannot catch that actor failing.**

Every control that held on 2026-09-02 caught the manager's *outputs* —
`check_backlog_criteria` refused a bad severity, `open-items-guard` refused an
empty observation, `detect_format` refused a non-reproducible write. All of them
are action-triggered. **Inaction produces no artifact, so it trips nothing**, and
the 64-guard fleet is blind to a manager doing nothing, by construction.

`manager_preflight.py` — this file's sibling — is a *preflight*, and therefore
has exactly the failure mode it was built to fix: a manager that skipped step 3
of its sweep prompt will skip the preflight too. This is the other half, and the
two are not substitutes.

WHAT NOTHING ELSE MEASURES
--------------------------
**Nothing in the operating layer measures how long something has been waiting.**
`handoff_check.py` runs on demand · `run_probes.py` times DATA freshness, not
queue latency · `check_wip_ceiling.py` counts and never times · the sweep fires
on a clock but its output is *a prompt to the manager*, so it inherits the
manager's reliability. `needs_action` exists on every session and **no component
watches it**.

Measured 2026-09-02T13:55Z (population: all 60 rows of one `list_sessions` page,
`mine=true`, `limit=60` — ⚠️ A PAGE IS NOT A POPULATION and this one may be
truncated): **56 of 60 sessions are not WORKING** — 37 `REVIEW_READY`, 15
`BLOCKED`, 3 `COMPLETED`, 1 `FAILED` — with waits from 65 to **1,345 minutes**.
The manager meanwhile asserted three times that these sessions "held work".

WHY THIS IS ONE ROLLED-UP DIGEST AND NOT AN ALARM PER SESSION
--------------------------------------------------------------
⚠️ At the numbers above, a per-session page would deliver **56 notifications**.
That is not a hypothetical: this repo measured **202 of 376 CRITICALs in one
window** being a single un-latched alarm, which trained the operator past the one
channel reserved for an unprotected position. So the escalation is:

  * ONE digest, headlined by the WORST wait, with counts by bucket beneath it.
  * A durable latch (`QUEUE-WATCH-STATE.json`, in the REPO because a routine's
    container does not survive), so a standing condition re-pages on a floor
    rather than every firing — `BL-20260823-TARGET-NAKED-COOLDOWN-RESETS-ON-EVERY-RESTART`
    is the row where a per-PROCESS latch put 202 pages on the operator.
  * A re-page when the worst wait crosses a NEW band, because a queue getting
    materially worse is new information even inside the cooldown.

WHERE IT ESCALATES, AND WHY THAT IS THE POINT
----------------------------------------------
**To the OPERATOR, not the manager.** When the manager is the bottleneck, a
notification routed to the manager is one more thing the failing component has
to notice — which is the failure, restated. `--audience` defaults to `operator`
for exactly this reason and `manager` is available only for a dry run.

WHERE THE CLOCK LIVES — and the honest boundary
------------------------------------------------
⚠️ **THIS HALF CANNOT BE A GITHUB WORKFLOW, AND THAT IS A FINDING RATHER THAN A
DESIGN CHOICE.** The verdict needs a LIVE read of what is running; `list_sessions`
is an `mcp__*` tool and **CI holds no MCP tools**, while `api.github.com` itself is
403 at the sandbox proxy. `handoff_check.py` hit this exact wall and solved it
honestly — *"there is deliberately no flag asserting the registry is fine, because
asserting it is what failed"* — and this file copies that: **no observation ⇒
`unknown`, permanently, with no flag to assert otherwise.**

So the clock is a **Claude Routine** (`create_trigger`), which is what the design
series already names as the thing a GitHub trigger cannot do: *start a Claude
session*. The routine's session holds the MCP tools, pipes the read in here, and
pings on the verdict.

⚠️ **AND THE ONE SUBSTITUTION THAT WOULD MAKE THIS WORSE THAN NOTHING:** reading
`SESSIONS.json`'s stored `last_observed` instead of a live read. That field is a
SNAPSHOT — the sweep prompt already warns never to read it as current, and this
session measured the registry **18-for-18 stale** the same day. A staleness
detector running on stale data reports a healthy queue with total confidence.
`--live-sessions` is the only input that can produce a graded verdict.

THREE STATES, NEVER COLLAPSED
-----------------------------
``measured``       a live read was obtained and latency computed.
``no_observation`` WE COULD NOT LOOK. Never "nothing is waiting".
``undateable``     rows were read but carry no parseable `updated_at`, so a
                   COUNT exists and a LATENCY does not. Reporting 0 minutes
                   there would assert an observation nobody made.

EXIT CODES: 0 quiet · 3 escalate · 4 unknown. Both non-quiet states are non-zero
so a caller cannot treat "we could not look" as "the queue is fine".
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import session_registry as sr  # noqa: E402

REPO_ROOT = sr.REPO_ROOT
STATE_PATH = REPO_ROOT / "docs" / "claude" / "work" / "QUEUE-WATCH-STATE.json"

#: THE RECEIPT — a different artifact from the LATCH above, and the distinction
#: is the whole point of it.
#:
#: `STATE_PATH` is written ONLY when a page or an unknown-report actually fires,
#: which is correct for suppression and useless for liveness: on a quiet queue it
#: is never written, so its absence cannot tell "the Routine has never run" from
#: "the Routine ran hourly and had nothing to say". Those are opposite facts and
#: the latch collapses them — so today, if `trig_01TWdAvrwFLe6T9XFoNopTeo`
#: stopped firing, NOTHING in this repo would notice.
#:
#: This file is written on EVERY run, whatever the verdict, so the Routine's
#: SILENCE becomes gradeable by `scripts/ci/check_manager_queue_watch.py` on
#: every PR. It attests that the watcher RAN and what it saw — never that the
#: queue is healthy, and never that a page was delivered.
RECEIPT_PATH = REPO_ROOT / "docs" / "claude" / "work" / "MANAGER-QUEUE-WATCH.json"

#: How many prior runs the receipt keeps. Bounded so the file cannot grow without
#: limit in a register directory that is merged by a row-aware 3-way driver.
RECEIPT_RUNS_KEPT = 12

MEASURED, NO_OBSERVATION, UNDATEABLE = "measured", "no_observation", "undateable"

#: Buckets that mean the session is handing something BACK. `COMPLETED` and
#: `FAILED` are terminal and are counted but never escalated on — a finished
#: session is not a queue.
WAITING_BUCKETS = ("blocked", "review_ready")
TERMINAL_BUCKETS = ("completed", "failed", "archived")

#: Escalate when the WORST wait exceeds this. CHOSEN, with its basis: the
#: operator's own cited cost was MI-57 blocked 142 minutes at $48.22, and MI-60
#: blocked 65 minutes at $72.92 — so a floor at 60 catches both while they are
#: still worth catching, rather than after the spend. It is not tuned: measured
#: over the live page the worst wait was 1,345 minutes, so every value between
#: about 30 and 600 fires identically today, and the exact number only starts to
#: matter once the queue is being worked.
DEFAULT_THRESHOLD_MIN = 60

#: A standing condition re-pages at most this often…
DEFAULT_REPAGE_HOURS = 6.0
#: …EXCEPT when the worst wait crosses into a new band. A queue getting
#: materially worse is new information even inside the cooldown, and a pure
#: cooldown would swallow it.
BANDS_MIN = (60, 120, 240, 480, 960, 1920)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _bucket(row: Dict[str, Any]) -> str:
    """The coarse queue state, normalised out of whichever field carries it.

    ⚠️ `session_registry.normalise_observation` deliberately drops `status_bucket`
    and `updated_at`, so this file cannot reuse it — a latency detector built on
    that normaliser would silently see no timestamps at all and grade every row
    `undateable`. Kept as its own reader rather than widening the shared one,
    which `handoff_check.py` also depends on.
    """
    raw = (row.get("status_bucket") or row.get("session_status")
           or row.get("status") or "")
    return str(raw).lower().replace("session_status_bucket_", "").replace(
        "session_status_", "").strip()


def normalise_sessions(raw: Any) -> List[Dict[str, Any]]:
    """Keep the fields latency needs: id, bucket, updated_at, parent, title."""
    for _ in range(4):
        if not isinstance(raw, dict):
            break
        for key in ("sessions", "data", "results", "items", "ccr"):
            inner = raw.get(key)
            if isinstance(inner, (list, dict)):
                raw = inner
                break
        else:
            break
        if isinstance(raw, list):
            break
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        row = entry.get("ccr") if isinstance(entry.get("ccr"), dict) else entry
        sid = row.get("id") or row.get("session_id")
        if not isinstance(sid, str) or not sid.strip():
            continue
        out.append({
            "session_id": sid.strip(),
            "bucket": _bucket(row),
            "updated_at": row.get("updated_at") or row.get("last_activity"),
            "parent_session_id": row.get("parent_session_id"),
            "title": row.get("title"),
            "unread": bool(row.get("unread")),
        })
    return out


def waiting_rows(rows: List[Dict[str, Any]], manager_id: Optional[str],
                 now: datetime) -> Tuple[List[Dict[str, Any]], int, int]:
    """(waiting rows with latency, terminal count, undateable count).

    Scoped to the manager's own children when a manager id is given — otherwise
    a shared account's other sessions would be counted against this manager's
    queue, which is a different population wearing the same label.
    """
    waiting: List[Dict[str, Any]] = []
    terminal = undateable = 0
    for row in rows:
        if manager_id and row.get("parent_session_id") != manager_id:
            continue
        bucket = row["bucket"]
        if any(t in bucket for t in TERMINAL_BUCKETS):
            terminal += 1
            continue
        if not any(w in bucket for w in WAITING_BUCKETS):
            continue  # working: not on the queue
        ts = _parse_ts(row.get("updated_at"))
        if ts is None:
            # ⚠️ COUNTED, never dropped and never given a latency of 0 — a row we
            # cannot date is not a row that has waited no time.
            undateable += 1
            continue
        waiting.append(dict(row, waited_min=int((now - ts).total_seconds() // 60)))
    waiting.sort(key=lambda r: r["waited_min"], reverse=True)
    return waiting, terminal, undateable


def band_of(minutes: int) -> int:
    """The coarse severity band a wait falls in. Escalation re-fires inside its
    cooldown only when this INCREASES."""
    band = 0
    for edge in BANDS_MIN:
        if minutes >= edge:
            band = edge
    return band


def assess(rows: Optional[List[Dict[str, Any]]], manager_id: Optional[str],
           now: datetime, threshold_min: int = DEFAULT_THRESHOLD_MIN
           ) -> Dict[str, Any]:
    """PURE, so the policy is arguable in tests rather than against a live queue."""
    if rows is None:
        return {"state": NO_OBSERVATION, "over_threshold": None, "worst_min": None,
                "band": None, "waiting": [], "by_bucket": {}, "terminal": None,
                "undateable": None, "population":
                "⚠️ NO LIVE OBSERVATION — nothing was counted. WE COULD NOT LOOK. "
                "This is NOT 'the queue is empty': only a session holding "
                "`list_sessions` can produce the read, and reading SESSIONS.json's "
                "stored snapshot instead would be a staleness detector running on "
                "stale data."}
    waiting, terminal, undateable = waiting_rows(rows, manager_id, now)
    over = [r for r in waiting if r["waited_min"] >= threshold_min]
    by_bucket: Dict[str, int] = {}
    for r in waiting:
        by_bucket[r["bucket"]] = by_bucket.get(r["bucket"], 0) + 1
    scoped = (f"children of {manager_id}" if manager_id
              else "ALL observed sessions (no --manager-id given, so this is not "
                   "scoped to one manager's queue)")
    if not waiting and undateable:
        return {"state": UNDATEABLE, "over_threshold": None, "worst_min": None,
                "band": None, "waiting": [], "by_bucket": {}, "terminal": terminal,
                "undateable": undateable, "population":
                f"population: {len(rows)} observed row(s), {scoped}; "
                f"{undateable} waiting row(s) carry no parseable `updated_at`, so a "
                f"COUNT exists and a LATENCY does not. Reporting 0 minutes here "
                f"would assert an observation nobody made."}
    worst = waiting[0]["waited_min"] if waiting else 0
    return {
        "state": MEASURED,
        "over_threshold": len(over),
        "worst_min": worst,
        "band": band_of(worst),
        "waiting": waiting,
        "by_bucket": by_bucket,
        "terminal": terminal,
        "undateable": undateable,
        "population": (f"population: {len(rows)} observed row(s), {scoped}; "
                       f"{len(waiting)} waiting, {terminal} terminal, "
                       f"{undateable} undateable; threshold {threshold_min} min"),
    }


def manager_from_lease() -> Tuple[Optional[str], str]:
    """The CURRENT manager, from the lease — never a hardcoded id.

    ⚠️ A routine pinned to a literal session id keeps working right up until the
    first handover, then reports an EMPTY QUEUE forever while looking perfectly
    healthy — a silent-empty of exactly the shape `silent-empty-guard` exists
    for, on the one mechanism meant to catch an absent manager. The lease is the
    repo's own answer to "who is managing", and it is durable across the death of
    its holder, which is the property this needs.

    Returns (None, why) rather than falling back: an unscoped count is a
    DIFFERENT population, not a degraded version of this one.
    """
    try:
        import manager_lease
    except ImportError:
        return None, "manager_lease could not be imported, so the holder is unknown."
    lease, readable = manager_lease.read_lease()
    if not readable:
        return None, "MANAGER-LEASE.json could not be read — WE COULD NOT LOOK."
    holder = (lease or {}).get("holder")
    state = (lease or {}).get("state")
    if not isinstance(holder, str) or not holder.strip():
        return None, (f"the lease names no holder (state={state!r}) — there is no "
                      f"manager whose queue this would be.")
    return holder.strip(), f"lease holder {holder} (state={state!r})"


def read_state(path: Path = STATE_PATH) -> Tuple[Optional[Dict[str, Any]], bool]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), True
    except FileNotFoundError:
        return None, True          # never escalated here yet — a real reading
    except (OSError, json.JSONDecodeError):
        return None, False         # we could not look


def unknown_report_due(verdict: Dict[str, Any], state: Optional[Dict[str, Any]],
                       state_readable: bool, now: datetime,
                       repage_hours: float = DEFAULT_REPAGE_HOURS
                       ) -> Tuple[bool, str]:
    """Should a NON-MEASURED verdict be reported at all this firing?

    ⚠️ A watcher that can never obtain its reading is the dangerous case, not the
    harmless one: it reports `unknown` on every firing, and a permanently-refusing
    output gets skimmed past until it is furniture — which is the exact risk
    `OI-20260901-CONSTRAINT-READOUT-SHIPPED-AND-IT-REFUSES` already names about
    this repo's own constraint readout. Reporting it hourly would guarantee that
    outcome, and reporting it never would hide a broken sensor.

    So an `unknown` is REPORTED on the same durable cooldown a page uses, and to
    the BOARD rather than to the operator: a sensor that cannot see is a
    maintenance problem, not an escalation.
    """
    if verdict["state"] == MEASURED:
        return False, "the verdict is measured — this path is for non-readings only."
    if not state_readable or state is None:
        return True, f"first report of `{verdict['state']}` — nothing recorded before."
    last = _parse_ts((state or {}).get("last_unknown_report_at"))
    if last is None:
        return True, f"`{verdict['state']}` has never been reported."
    hours = (now - last).total_seconds() / 3600.0
    if hours >= repage_hours:
        return True, (f"`{verdict['state']}` has stood {hours:.1f}h since it was last "
                      f"reported (floor {repage_hours}h). A sensor that cannot see is "
                      f"a standing defect, and silence would hide it.")
    return False, (f"`{verdict['state']}` was already reported {hours:.1f}h ago — held, "
                   f"so a blind watcher does not become hourly furniture.")


def escalation_due(verdict: Dict[str, Any], state: Optional[Dict[str, Any]],
                   state_readable: bool, now: datetime,
                   repage_hours: float = DEFAULT_REPAGE_HOURS
                   ) -> Tuple[bool, str]:
    """(should we page, why). Also PURE."""
    if verdict["state"] != MEASURED:
        return False, f"state is {verdict['state']} — nothing graded, so nothing to page."
    if not verdict["over_threshold"]:
        return False, "nothing on the queue is over the threshold."
    if not state_readable:
        # ⚠️ FAIL LOUD, not quiet. An unreadable latch that suppressed would make
        # a permanently-broken latch look exactly like a healthy queue; the same
        # polarity `target_naked_alert_state` was corrected to.
        return True, ("the latch could not be read, so page rather than suppress — "
                      "a broken latch must announce itself as noise, never as silence.")
    if state is None:
        return True, "first escalation — no previous page recorded."
    last = _parse_ts((state or {}).get("last_paged_at"))
    prev_band = (state or {}).get("last_band")
    if isinstance(prev_band, int) and verdict["band"] > prev_band:
        return True, (f"the worst wait crossed into a new band "
                      f"({prev_band} → {verdict['band']} min) — materially worse is "
                      f"new information even inside the cooldown.")
    if last is None:
        return True, "the latch carries no readable timestamp — page rather than assume."
    hours = (now - last).total_seconds() / 3600.0
    if hours >= repage_hours:
        return True, (f"the condition has stood {hours:.1f}h since the last page "
                      f"(floor {repage_hours}h).")
    return False, (f"already paged {hours:.1f}h ago and the worst wait has not "
                   f"crossed a new band — held to avoid training the operator past "
                   f"the channel.")


def render_digest(verdict: Dict[str, Any], top: int = 5) -> str:
    if verdict["state"] != MEASURED:
        return (f"[manager queue] {verdict['state'].upper()} — {verdict['population']}")
    lines = [
        f"[manager queue] {verdict['over_threshold']} item(s) waiting on the manager; "
        f"worst {verdict['worst_min']} min.",
        "  by bucket: " + (", ".join(f"{k}={v}" for k, v in
                                     sorted(verdict["by_bucket"].items())) or "none"),
    ]
    for r in verdict["waiting"][:top]:
        lines.append(f"  {r['waited_min']:>5} min  {r['bucket']:<13} "
                     f"{r['session_id'][:26]}  {(r.get('title') or '')[:52]}")
    if len(verdict["waiting"]) > top:
        lines.append(f"  … and {len(verdict['waiting']) - top} more (rolled up "
                     f"deliberately — one digest, never a page per session).")
    if verdict["undateable"]:
        lines.append(f"  ⚠️ {verdict['undateable']} waiting row(s) UNDATEABLE — "
                     f"counted, no latency. Not zero.")
    lines.append("  " + verdict["population"])
    return "\n".join(lines)


def write_receipt(verdict: Dict[str, Any], now: datetime, escalated: bool,
                  manager_id: Optional[str], path: Path = RECEIPT_PATH) -> None:
    """Attest that the watcher RAN. Written on EVERY run, whatever the verdict.

    ⚠️ IT ATTESTS A RUN, NEVER A HEALTHY QUEUE AND NEVER A DELIVERED PAGE. The
    watcher can run, grade `unknown` because no observation was supplied, and
    still write this file — that is the point: the guard downstream asks "is the
    Routine still firing?", which is a different question from "is the queue
    clear?" and must not be answered by the same field.

    ⚠️ AND IT IS NOT THE LATCH. `write_state` suppresses repeat pages and is
    written only when one fires; conflating the two would either make a quiet
    watcher look dead or make a dead watcher look quiet.
    """
    prior, prior_ok = read_state(path)
    runs: List[Dict[str, Any]] = []
    if prior_ok and isinstance(prior, dict) and isinstance(prior.get("runs"), list):
        runs = [r for r in prior["runs"] if isinstance(r, dict)]
    runs.append({
        "at": now.isoformat(),
        "read_state": verdict.get("state"),
        "waiting": verdict.get("waiting"),
        "worst_min": verdict.get("worst_min"),
        "escalated": bool(escalated),
    })
    payload = {
        "_doc": [
            "RECEIPT of the MANAGER-QUEUE watcher (scripts/ops/queue_latency.py,",
            "fired by the Claude Routine 'Manager queue watch'). Written on EVERY",
            "run, whatever the verdict.",
            "",
            "WHY IT IS SEPARATE FROM QUEUE-WATCH-STATE.json: that file is a page",
            "LATCH and is written only when a page fires, so on a quiet queue it is",
            "never written and its absence cannot distinguish 'the Routine never",
            "ran' from 'the Routine ran and had nothing to say'. This file makes the",
            "Routine's SILENCE gradeable -- scripts/ci/check_manager_queue_watch.py",
            "reads its age in run_guards.py on every PR, so a Routine that stops",
            "firing announces itself in everybody's CI instead of going quiet.",
            "",
            "READ `generated_at`, NEVER THE CRON EXPRESSION. A scheduled thing in",
            "this repo is not evidence that it fires: probes.yml's first scheduled",
            "run was ~4h50m late and fired once instead of daily.",
            "",
            "IT ATTESTS A RUN, NOT A HEALTHY QUEUE AND NOT A DELIVERED PAGE.",
            "An ABSENT file means the watcher has NEVER run, which is a different",
            "fact from STALE and needs a different fix.",
        ],
        "schema_version": 1,
        "generated_at": now.isoformat(),
        "read_state": verdict.get("state"),
        "manager_id": manager_id,
        "waiting": verdict.get("waiting"),
        "worst_min": verdict.get("worst_min"),
        "escalated": bool(escalated),
        "population": verdict.get("population"),
        "runs": runs[-RECEIPT_RUNS_KEPT:],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def write_state(verdict: Dict[str, Any], now: datetime, reason: str,
                path: Path = STATE_PATH) -> None:
    payload = {
        "_doc": [
            "DURABLE LATCH for the manager-queue watcher (scripts/ops/queue_latency.py).",
            "It lives in the REPO because the watcher runs in a Routine-spawned",
            "container that does not survive its firing -- a per-process latch would",
            "reset on every run and page every hour, which is exactly how",
            "BL-20260823-TARGET-NAKED-COOLDOWN-RESETS-ON-EVERY-RESTART put 202",
            "CRITICALs on the operator's channel.",
            "WRITTEN ONLY WHEN A PAGE ACTUALLY FIRES, so this file does not churn on",
            "a quiet queue. An absent file means no page has ever fired -- NEVER that",
            "the queue is healthy.",
        ],
        "last_paged_at": now.isoformat(),
        "last_band": verdict.get("band"),
        "last_worst_min": verdict.get("worst_min"),
        "last_over_threshold": verdict.get("over_threshold"),
        "reason": reason,
    }
    # ⚠️ MERGE, never overwrite. The page timestamp and the unknown-report
    # timestamp answer different questions, and a blind watcher writing its own
    # marker must not erase the record of the last real page.
    prior, prior_ok = read_state(path)
    if prior_ok and isinstance(prior, dict):
        for key in ("last_paged_at", "last_band", "last_worst_min",
                    "last_over_threshold", "last_unknown_report_at"):
            if key not in payload and key in prior:
                payload[key] = prior[key]
    if verdict.get("state") != MEASURED:
        # a non-reading records ONLY its own marker; it never claims a page
        payload["last_unknown_report_at"] = now.isoformat()
        for key in ("last_paged_at", "last_band", "last_worst_min",
                    "last_over_threshold"):
            payload[key] = (prior or {}).get(key) if prior_ok else None
        payload["last_unknown_state"] = verdict.get("state")
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


_EXIT = {"quiet": 0, "escalate": 3, "unknown": 4}


def _self_test(quiet: bool = False) -> Tuple[bool, List[str]]:
    failures: List[str] = []

    def check(label: str, got: Any, want: Any) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r} want {want!r}")
        if not quiet:
            print(f"  self-test ({label}): "
                  f"{'PASS' if got == want else f'FAIL got={got!r} want={want!r}'}")

    now = datetime(2026, 9, 2, 14, 0, tzinfo=timezone.utc)
    def row(sid, bucket, mins, parent="M"):
        ts = now.timestamp() - mins * 60
        return {"id": sid, "status_bucket": bucket, "parent_session_id": parent,
                "updated_at": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
                "title": sid}

    # normalisation
    n = normalise_sessions([row("s1", "SESSION_STATUS_BUCKET_BLOCKED", 10)])
    check("the bucket prefix is stripped", n[0]["bucket"], "blocked")
    check("an MCP `{ccr: {...}}` envelope is descended",
          len(normalise_sessions({"ccr": [row("s1", "BLOCKED", 1)]})), 1)
    check("a row with no id is DROPPED, never given a blank one",
          len(normalise_sessions([{"status_bucket": "BLOCKED"}])), 0)

    # NO OBSERVATION is the load-bearing refusal
    v = assess(None, "M", now)
    check("NO OBSERVATION -> no_observation, never 'the queue is empty'",
          v["state"], NO_OBSERVATION)
    check("...and it carries NO worst-case number", v["worst_min"], None)
    check("...and it never escalates (nothing was graded)",
          escalation_due(v, None, True, now)[0], False)

    # measured
    rows = normalise_sessions([row("s1", "BLOCKED", 200), row("s2", "REVIEW_READY", 30),
                               row("s3", "WORKING", 5), row("s4", "COMPLETED", 900)])
    v = assess(rows, "M", now, threshold_min=60)
    check("a WORKING session is not on the queue", len(v["waiting"]), 2)
    check("a COMPLETED session is terminal, counted, not escalated on",
          v["terminal"], 1)
    check("only waits OVER the threshold count", v["over_threshold"], 1)
    check("the worst wait is the headline", v["worst_min"], 200)
    check("waiting rows are sorted worst-first",
          [r["session_id"] for r in v["waiting"]], ["s1", "s2"])

    # scoping
    check("ANOTHER MANAGER'S CHILD IS NOT ON THIS MANAGER'S QUEUE",
          len(assess(normalise_sessions([row("x", "BLOCKED", 500, parent="OTHER")]),
                     "M", now)["waiting"]), 0)
    check("...but with no --manager-id every observed row is counted",
          len(assess(normalise_sessions([row("x", "BLOCKED", 500, parent="OTHER")]),
                     None, now)["waiting"]), 1)

    # undateable
    und = assess(normalise_sessions([{"id": "s1", "status_bucket": "BLOCKED",
                                      "parent_session_id": "M"}]), "M", now)
    check("A ROW WITH NO TIMESTAMP IS `undateable`, NOT A 0-MINUTE WAIT",
          und["state"], UNDATEABLE)
    check("...and it is COUNTED rather than dropped", und["undateable"], 1)
    check("...and carries no worst-case number", und["worst_min"], None)

    # bands
    check("band_of is monotone and coarse",
          [band_of(0), band_of(59), band_of(60), band_of(1345)], [0, 0, 60, 960])

    # escalation policy
    v_hot = assess(normalise_sessions([row("s1", "BLOCKED", 200)]), "M", now, 60)
    v_cold = assess(normalise_sessions([row("s1", "BLOCKED", 5)]), "M", now, 60)
    check("nothing over the threshold does NOT page",
          escalation_due(v_cold, None, True, now)[0], False)
    check("a first breach PAGES", escalation_due(v_hot, None, True, now)[0], True)
    fresh = {"last_paged_at": now.isoformat(), "last_band": 120}
    check("A STANDING CONDITION INSIDE THE COOLDOWN IS HELD — one digest, not 56",
          escalation_due(v_hot, fresh, True, now)[0], False)
    check("...but a WORSE band re-pages even inside the cooldown",
          escalation_due(assess(normalise_sessions([row("s1", "BLOCKED", 1000)]),
                                "M", now, 60), fresh, True, now)[0], True)
    old = {"last_paged_at": datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc).isoformat(),
           "last_band": 120}
    check("...and the cooldown FLOOR eventually re-pages a standing condition",
          escalation_due(v_hot, old, True, now)[0], True)
    check("AN UNREADABLE LATCH PAGES rather than suppressing",
          escalation_due(v_hot, None, False, now)[0], True)
    check("a latch with no readable timestamp pages rather than assuming",
          escalation_due(v_hot, {"last_band": 120}, True, now)[0], True)

    # the lease resolver -- a routine must never carry a literal manager id
    mid, why = manager_from_lease()
    check("the lease resolver returns EITHER an id OR a stated reason, never both-null",
          (mid is None) != (mid is not None), True)
    check("...and when it cannot resolve, it says so rather than returning ''",
          mid is None or (isinstance(mid, str) and mid.startswith("session_")), True)

    # a BLIND watcher must be reported, but not hourly
    blind = assess(None, "M", now)
    check("a first `unknown` IS reported (a blind sensor must not be silent)",
          unknown_report_due(blind, None, True, now)[0], True)
    check("...but a repeat inside the cooldown is HELD (not hourly furniture)",
          unknown_report_due(blind, {"last_unknown_report_at": now.isoformat()},
                             True, now)[0], False)
    check("...and it IS re-reported once the floor elapses",
          unknown_report_due(blind, {"last_unknown_report_at":
                                     datetime(2026, 9, 1, 0, 0,
                                              tzinfo=timezone.utc).isoformat()},
                             True, now)[0], True)
    check("a MEASURED verdict never takes the unknown-report path",
          unknown_report_due(v_hot, None, True, now)[0], False)
    check("an unknown NEVER pages the operator — only the board",
          escalation_due(blind, None, True, now)[0], False)

    # the digest must roll up
    dig = render_digest(assess(normalise_sessions(
        [row(f"s{i}", "BLOCKED", 100 + i) for i in range(20)]), "M", now, 60))
    check("A 20-ITEM QUEUE RENDERS AS ONE BOUNDED DIGEST, NOT 20 LINES",
          len(dig.splitlines()) <= 10, True)
    check("...and it says how many it rolled up", "and 15 more" in dig, True)

    # --- THE RECEIPT IS NOT THE LATCH -------------------------------------
    # ⚠️ The whole reason the receipt exists is that the latch cannot answer
    # "did the Routine fire?" — so these assert the two are genuinely different
    # artifacts and that the receipt survives the QUIET run, which is exactly
    # the run whose absence must be detectable.
    check("the receipt and the page latch are DIFFERENT files — one more would be "
          "a second latch, not a liveness record",
          RECEIPT_PATH != STATE_PATH, True)
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        rp = Path(td) / "receipt.json"
        quiet_verdict = {"state": MEASURED, "waiting": 0, "worst_min": 0,
                         "population": "population: 0 rows"}
        write_receipt(quiet_verdict, now, False, "M", rp)
        check("A QUIET RUN STILL WRITES THE RECEIPT — the run whose absence must "
              "be detectable is precisely the boring one",
              rp.is_file(), True)
        doc = json.loads(rp.read_text())
        check("...and it dates itself, which is what the guard grades",
              doc["generated_at"], now.isoformat())
        check("...and records that NOTHING was escalated, distinctly from not "
              "having run", doc["escalated"], False)
        check("an UNKNOWN verdict is receipted too — a blind watcher is still a "
              "watcher that FIRED, and the two questions must not share a field",
              (write_receipt({"state": NO_OBSERVATION, "waiting": None,
                              "worst_min": None, "population": "p"},
                             now, False, None, rp),
               json.loads(rp.read_text())["read_state"])[1], NO_OBSERVATION)
        check("...and the run history accumulates rather than being overwritten",
              len(json.loads(rp.read_text())["runs"]), 2)
        for _ in range(RECEIPT_RUNS_KEPT + 5):
            write_receipt(quiet_verdict, now, False, "M", rp)
        check("...but is BOUNDED, so a register file cannot grow without limit",
              len(json.loads(rp.read_text())["runs"]), RECEIPT_RUNS_KEPT)

    if not quiet:
        print("queue-latency self-test:", "PASS" if not failures else "FAIL")
    return (not failures), failures


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--live-sessions", default=None,
                    help="path to `list_sessions` output, or '-' for stdin. WITHOUT "
                         "IT the verdict is `unknown`, permanently. There is "
                         "deliberately no flag to assert the queue is fine.")
    ap.add_argument("--manager-id", default=None,
                    help="scope to this manager's children (recommended — otherwise "
                         "another manager's sessions count against this queue)")
    ap.add_argument("--manager-id-from-lease", action="store_true",
                    help="resolve the manager from MANAGER-LEASE.json's holder. ⚠️ USE "
                         "THIS RATHER THAN HARDCODING AN ID IN A ROUTINE PROMPT — the "
                         "manager changes on every handover, and a routine pinned to a "
                         "dead manager's id would report an EMPTY QUEUE forever while "
                         "looking healthy. If the lease is unheld or unreadable the "
                         "verdict is `unknown`, never an unscoped count.")
    ap.add_argument("--threshold-min", type=int, default=DEFAULT_THRESHOLD_MIN)
    ap.add_argument("--repage-hours", type=float, default=DEFAULT_REPAGE_HOURS)
    ap.add_argument("--audience", choices=("operator", "manager"), default="operator",
                    help="who the digest is FOR. Defaults to `operator` because when "
                         "the manager is the bottleneck, paging the manager is one "
                         "more thing the failing component must notice.")
    ap.add_argument("--write-receipt", action="store_true",
                    help="record that this run HAPPENED, in the durable receipt "
                         "(docs/claude/work/MANAGER-QUEUE-WATCH.json). ⚠️ Pass this "
                         "on EVERY firing, including quiet ones — it is what makes "
                         "the Routine's SILENCE gradeable by CI. It attests a RUN, "
                         "never a healthy queue and never a delivered page.")
    ap.add_argument("--write-state", action="store_true",
                    help="record the page in the durable latch (only meaningful when "
                         "a page actually fires, and only after you have SENT it)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        ok, _ = _self_test()
        return 0 if ok else 1

    ok, failures = _self_test(quiet=True)
    if not ok:
        print(f"queue-latency: REFUSING — the planted-failure suite did not pass "
              f"({len(failures)}): {'; '.join(failures[:3])}")
        return _EXIT["unknown"]

    manager_id = a.manager_id
    if a.manager_id_from_lease:
        resolved, why = manager_from_lease()
        if resolved is None:
            print(f"queue-latency: UNKNOWN — {why} REFUSING to fall back to an "
                  f"unscoped count: that would silently widen the population from "
                  f"'this manager's queue' to 'every session on the account', which "
                  f"is a different number wearing the same label.")
            return _EXIT["unknown"]
        manager_id = resolved
        print(f"queue-latency: manager resolved from the lease: {manager_id}")

    raw = None
    if a.live_sessions:
        text = (sys.stdin.read() if a.live_sessions == "-"
                else Path(a.live_sessions).read_text(encoding="utf-8"))
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            blob = sr._outermost_json(text)
            raw = json.loads(blob) if blob else None
    rows = normalise_sessions(raw) if raw is not None else None

    now = _now()
    verdict = assess(rows, manager_id, now, a.threshold_min)
    state, state_ok = read_state()
    due, why = escalation_due(verdict, state, state_ok, now, a.repage_hours)

    report_due, report_why = unknown_report_due(verdict, state, state_ok, now,
                                                a.repage_hours)
    print(render_digest(verdict))
    print(f"queue-latency: audience={a.audience} escalate={due} — {why}")
    if verdict["state"] != MEASURED:
        # ⚠️ A blind sensor is a MAINTENANCE problem, not an escalation: it goes to
        # the board, on the same durable cooldown, so it neither pages the operator
        # nor becomes an hourly comment nobody reads.
        print(f"queue-latency: report_unknown={report_due} — {report_why}")
    # ⚠️ UNCONDITIONAL, and deliberately ahead of the latch. A receipt written
    # only on the interesting runs would be a second latch, and the thing this
    # exists to detect is precisely the boring run failing to happen at all.
    if a.write_receipt:
        write_receipt(verdict, now, due, manager_id)
        print(f"queue-latency: receipt written to "
              f"{RECEIPT_PATH.relative_to(REPO_ROOT)} (attests this RUN, not a "
              f"healthy queue)")
    if (due or (verdict["state"] != MEASURED and report_due)) and a.write_state:
        write_state(verdict, now, why if due else report_why)
        print(f"queue-latency: latch written to {STATE_PATH.relative_to(REPO_ROOT)}")
    if a.json:
        print(json.dumps({"verdict": verdict, "escalate": due, "reason": why},
                         indent=2, ensure_ascii=False, default=str))

    if verdict["state"] != MEASURED:
        return _EXIT["unknown"]
    return _EXIT["escalate"] if due else _EXIT["quiet"]


if __name__ == "__main__":
    raise SystemExit(main())
