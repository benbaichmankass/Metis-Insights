#!/usr/bin/env python3
#
# wiring: fired by `.github/workflows/pr-queue-watch.yml` (schedule +
# workflow_dispatch) and by NOTHING ELSE. It is deliberately NOT reachable from
# a session prompt, a skill, or a manager checklist step -- see "WHY THE MANAGER
# CANNOT INVOKE THIS" below. Its own liveness is graded by
# `scripts/ci/check_pr_queue_watch.py`, which runs in `run_guards.py` on every
# PR, so a watcher that stops announces itself in everybody's CI.
"""PR-QUEUE LATENCY -- how long has a finished piece of work sat unmerged?

THE SIBLING, AND WHY IT IS NOT A DUPLICATE
------------------------------------------
`queue_latency.py` asks the same question about SESSIONS and answers it from
`list_sessions`. That read is an ``mcp__*`` tool, **CI holds no MCP tools**, and
that file refuses -- correctly -- to substitute a stale registry snapshot. So it
reports `unknown` permanently unless a Claude Routine pipes a live read into it.

A blind sensor that says it is blind beats a green one that checked nothing. But
a permanently-`unknown` watcher is not yet a guard, and it is aimed at the
failure that has cost the most.

THIS FILE IS THE MCP-FREE HALF. It does not ask *"is a session waiting?"* -- it
cannot know that. It asks a NARROWER question that `GITHUB_TOKEN` alone can
answer: **is there an open, unmerged pull request that nobody has pushed to for
hours?** Every such PR is finished work sitting in front of the one actor who can
merge it.

⚠️ THE PROXY, STATED RATHER THAN HIDDEN
---------------------------------------
Branch quiescence is a PROXY for hand-back, **not proof of one**. A session can
be alive and thinking with nothing pushed; a session can be dead with a fresh
push. This file therefore:

  * never claims a session is dead, idle, or blocked -- only that an ARTIFACT
    has not moved;
  * never kills, closes, merges, un-drafts or comments on anything.

`session_reaper.py` grades the same proxy and its docstring says why that is
defensible: *"the cost of calling a live session `stalled_with_work` is a
refreshed row in a ledger."* The same holds here, and it is the reason this
escalates rather than acts.

WHAT ALREADY EXISTED, AND WHAT DID NOT
--------------------------------------
Checked on the live tree before writing a line, because
`RC-BUILT-A-MECHANISM-THAT-ALREADY-EXISTED` is a named recurrence here:

  * `session_reaper.py` -- grades branch quiescence for REGISTRY SESSIONS and
    writes `REAPER-OBSERVATIONS.json`. **It records; it never times a queue and
    never escalates.** Its ledger has no consumer.
  * `reconcile_open_prs.py` -- iterates the rows `OPEN-PRS.json` already has and
    moves settled ones out. Its own docstring: *"It never enumerates what is
    open."* Structurally cannot see an unrecorded open PR.
  * `open_pr_record.py --strict` -- grades the FILE's decisions. `run_guards.py`
    states in a comment why it deliberately does NOT fetch the live list in
    per-PR CI: it would *"redden PRs for a row nobody could have written yet."*

So: three mechanisms touch open PRs and **not one of them times how long a PR
has been waiting, and not one escalates.** That is the hole this fills, and it
is filled OUTSIDE per-PR CI precisely so it can never redden a contributor's PR
for the manager's backlog.

WHY THE MANAGER CANNOT INVOKE THIS
----------------------------------
Operator directive: *"I want actual guards and mechanisms in the repo/vm
themselves that watch the manager and enforce the rules on him."*

The measurement that decides the shape: **every mechanism the manager had to
CHOOSE to run went unused; every mechanism that STOOD IN THE WAY worked.** A
check invoked by the actor it checks cannot catch that actor failing, and a
preflight is skipped by exactly the manager who skipped the step before it.

So the clock is a workflow the manager does not trigger, and the enforcement is
that **the watcher's own deadness fails everybody's CI** (see
`check_pr_queue_watch.py`). There is nothing to remember and nothing to opt into.

⚠️ AND A CRON IS NOT EVIDENCE OF A RUN. This repo measured `probes.yml`'s first
scheduled run firing ~4h50m late and once instead of daily, and
`session-reaper.yml` failing five times before its first success. So the cadence
is NOT trusted: every run writes a dated receipt, and the CI guard grades that
receipt's age. Read the receipt, never the cron expression.

THREE READ STATES, NEVER COLLAPSED
----------------------------------
``measured``        the open-PR list was obtained and at least one head branch
                    time resolved.
``no_observation``  **WE COULD NOT LOOK.** The list was unreadable. This is NEVER
                    "no PR is open" -- an empty queue is a real and different
                    reading, and it grades `measured` with zero waiting.
``undateable``      PRs were read and NO head branch time could be resolved, so
                    a COUNT exists and a LATENCY does not. Reporting 0 hours here
                    would assert an observation nobody made.

FOUR PER-PR STATES, NEVER COLLAPSED
-----------------------------------
``waiting``         open, unmerged, and its head branch has been quiet past the
                    threshold. The finding.
``active``          a push landed inside the window. Someone is working; this is
                    not a queue.
``held_declared``   the PR itself declares it must not be merged. **COUNTED AND
                    REPORTED, never silently dropped** -- so the marker cannot be
                    used to hide a PR, only to say why it is not escalated on.
``undateable``      the head branch is not on `origin`, or carries no parseable
                    commit time. ⚠️ **NOT "fresh"** -- we did not look.

⚠️ DRAFTS COUNT, AND EXCLUDING THEM WOULD MISS THE MAJORITY. The convention in
this repo is *"DRAFT PR. I merge, not you."* -- so a draft IS the normal
hand-back shape. Measured 2026-09-02T17:21Z over all 10 open PRs, 6 of the 6
quiescent ones were drafts. A drafts-excluded watcher would have reported a
clean queue.

EXIT CODES: 0 quiet - 3 escalate - 4 unknown. Both non-quiet states are non-zero
so a caller cannot read "we could not look" as "the queue is fine".
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

# ONE OWNER for the escalation policy. `band_of` / `escalation_due` /
# `unknown_report_due` are imported rather than re-derived: two watchers with two
# copies of "when do we page?" are free to drift, and this repo already records
# what a second copy of a rule costs.
import queue_latency as ql  # noqa: E402
import session_registry as sr  # noqa: E402

REPO_ROOT = sr.REPO_ROOT
STATE_PATH = REPO_ROOT / "docs" / "claude" / "work" / "PR-QUEUE-WATCH.json"

MEASURED = ql.MEASURED
NO_OBSERVATION = ql.NO_OBSERVATION
UNDATEABLE = ql.UNDATEABLE

WAITING, ACTIVE, HELD_DECLARED, PR_UNDATEABLE = (
    "waiting", "active", "held_declared", "undateable")

#: Escalate when the WORST quiescence exceeds this. **CHOSEN, with its basis
#: stated -- not tuned.**
#:
#: Two independent anchors, and they agree:
#:
#: 1. The manager lease TTL is 90 minutes, and takeover is time-based. A PR quiet
#:    for 6h has outlived FOUR lease generations -- every manager that could have
#:    merged it has come and gone.
#: 2. MEASURED 2026-09-02T17:21Z over the live queue (population: ALL 10 open PRs
#:    in this repo, quiescence = hours since the last commit on the head branch):
#:    the distribution is cleanly bimodal --
#:        0.2, 1.0, 1.0   |gap|   5.3, 5.7, 7.4, 7.7, 11.0, 18.9, 109.5
#:    The nearest observations either side of the gap are 1.0h and 5.3h, so every
#:    threshold in (1.0, 5.3] separates "being worked" from "handed back"
#:    identically on this data. 6h sits just past the gap.
#:
#: ⚠️ n = 10 is a small denominator and one repo on one day is not a
#: distribution. The value is defensible, not calibrated.
DEFAULT_THRESHOLD_HOURS = 6.0

#: A standing backlog re-escalates at most this often, EXCEPT on a new band.
DEFAULT_REPAGE_HOURS = 12.0

#: Rewrite the receipt when it is older than this even if nothing changed, so
#: `check_pr_queue_watch.py` can tell a QUIET watcher from a DEAD one. Every run
#: writing would open a `commit-to-main` PR per run; never writing would make the
#: freshness guard meaningless. This floor is the seam between the two, and the
#: guard's window must stay comfortably wider than it.
DEFAULT_REFRESH_HOURS = 12.0

#: A PR that declares it must not be merged. ⚠️ It is a DECLARATION, never an
#: exclusion: a `held_declared` PR is still counted and still printed with its
#: age, so the marker can explain a PR's presence and can never hide it.
HOLD_MARKER = re.compile(r"\b(do\s*not\s*merge|dont\s*merge|don't\s*merge)\b", re.I)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: Any) -> Optional[datetime]:
    return ql._parse_ts(value)


def head_ref_of(pr: Dict[str, Any]) -> Optional[str]:
    """Tolerant across the `gh api` and `gh pr list --json` shapes."""
    head = pr.get("head")
    if isinstance(head, dict):
        ref = head.get("ref")
        if isinstance(ref, str) and ref:
            return ref
    for key in ("headRefName", "head_ref", "branch"):
        v = pr.get(key)
        if isinstance(v, str) and v:
            return v
    return None


def normalise_prs(raw: Any) -> Optional[List[Dict[str, Any]]]:
    """Return the OPEN, UNMERGED rows -- or ``None`` for *we could not look*.

    ⚠️ ``None`` and ``[]`` are opposite facts and are kept apart here rather than
    downstream: ``[]`` is an empty queue (good news, and a real reading) while
    ``None`` is an unreadable list.
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        for key in ("pull_requests", "prs", "items", "data"):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
        else:
            return None
    if not isinstance(raw, list):
        return None
    rows: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if item.get("merged") is True:
            continue
        state = str(item.get("state") or "open").lower()
        if state not in ("open", ""):
            continue
        rows.append(item)
    return rows


def branch_times_from_git(refs: List[str], repo_root: Path = REPO_ROOT
                          ) -> Dict[str, Optional[datetime]]:
    """Newest commit time per head ref, read from the local clone's `origin`.

    ⚠️ A ref that cannot be resolved maps to ``None`` -- *we could not look* --
    and NEVER to "now". A shallow clone or a missing fetch would otherwise make
    every branch read fresh, which is the direction that reports a backed-up
    queue as healthy.
    """
    out: Dict[str, Optional[datetime]] = {}
    for ref in refs:
        ts = None
        for candidate in (f"refs/remotes/origin/{ref}", f"origin/{ref}"):
            try:
                res = subprocess.run(
                    ["git", "-C", str(repo_root), "log", "-1", "--format=%cI", candidate],
                    capture_output=True, text=True, timeout=30)
            except (OSError, subprocess.SubprocessError):
                break
            if res.returncode == 0 and res.stdout.strip():
                ts = _parse_ts(res.stdout.strip())
                if ts is not None:
                    break
        out[ref] = ts
    return out


def _pr_number(pr: Dict[str, Any]) -> Any:
    return pr.get("number") or pr.get("id")


def grade_pr(pr: Dict[str, Any], pushed_at: Optional[datetime], now: datetime,
             threshold_hours: float) -> Dict[str, Any]:
    """PURE. One PR in, one graded row out."""
    ref = head_ref_of(pr)
    title = str(pr.get("title") or "")
    body = str(pr.get("body") or "")
    row: Dict[str, Any] = {
        "pr": _pr_number(pr),
        "title": title[:120],
        "head_ref": ref,
        "draft": bool(pr.get("draft") or pr.get("isDraft")),
        "quiet_hours": None,
        "state": None,
        "why": "",
    }
    if HOLD_MARKER.search(title):
        row["state"] = HELD_DECLARED
        row["why"] = ("the PR's own title declares it must not be merged, so it is "
                      "not waiting on a merge decision. Counted and printed, never "
                      "hidden.")
    if pushed_at is None:
        row["state"] = row["state"] or PR_UNDATEABLE
        row["why"] = row["why"] or (
            "the head branch is not on `origin` or carries no parseable commit "
            "time -- WE COULD NOT LOOK. This is not 'recently pushed'.")
        return row
    quiet = (now - pushed_at).total_seconds() / 3600.0
    row["quiet_hours"] = round(quiet, 1)
    row["last_push_utc"] = pushed_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    if row["state"] == HELD_DECLARED:
        return row
    if quiet >= threshold_hours:
        row["state"] = WAITING
        row["why"] = (f"open and unmerged with no push for {quiet:.1f}h "
                      f"(threshold {threshold_hours}h).")
    else:
        row["state"] = ACTIVE
        row["why"] = f"a push landed {quiet:.1f}h ago -- being worked, not queued."
    return row


def assess(prs: Optional[List[Dict[str, Any]]],
           branch_times: Optional[Dict[str, Optional[datetime]]],
           now: datetime,
           threshold_hours: float = DEFAULT_THRESHOLD_HOURS) -> Dict[str, Any]:
    """PURE, so the policy is arguable in tests rather than against a live queue."""
    if prs is None:
        return {
            "state": NO_OBSERVATION, "over_threshold": None, "worst_min": None,
            "worst_hours": None, "band": None, "rows": [], "by_state": {},
            "population": (
                "WE COULD NOT LOOK -- the open-PR list was unreadable, so nothing "
                "was counted. This is NOT 'no PR is open': an empty queue is a "
                "real reading and grades `measured` with zero waiting."),
        }
    branch_times = branch_times or {}
    rows = [grade_pr(pr, branch_times.get(head_ref_of(pr) or ""), now, threshold_hours)
            for pr in prs]
    by_state: Dict[str, int] = {}
    for r in rows:
        by_state[r["state"]] = by_state.get(r["state"], 0) + 1
    dated = [r for r in rows if r["quiet_hours"] is not None]
    if rows and not dated:
        return {
            "state": UNDATEABLE, "over_threshold": None, "worst_min": None,
            "worst_hours": None, "band": None,
            "rows": sorted(rows, key=lambda r: str(r["pr"])), "by_state": by_state,
            "population": (
                f"{len(rows)} open PR(s) were READ and NOT ONE head branch time "
                f"could be resolved, so a COUNT exists and a LATENCY does not. "
                f"Reporting 0 hours would assert an observation nobody made. The "
                f"usual cause is a shallow clone -- fetch every `origin` head."),
        }
    waiting = [r for r in rows if r["state"] == WAITING]
    waiting.sort(key=lambda r: -(r["quiet_hours"] or 0))
    worst_hours = max((r["quiet_hours"] or 0) for r in dated) if dated else 0.0
    worst_min = int(round(worst_hours * 60))
    return {
        "state": MEASURED,
        "over_threshold": len(waiting),
        "worst_hours": worst_hours,
        # `band_of` is the imported policy and speaks in MINUTES, so the two
        # watchers share one band ladder rather than two that can diverge.
        "worst_min": worst_min,
        "band": ql.band_of(worst_min),
        "rows": waiting + sorted((r for r in rows if r["state"] != WAITING),
                                 key=lambda r: str(r["pr"])),
        "waiting": waiting,
        "by_state": by_state,
        "threshold_hours": threshold_hours,
        "population": (
            f"ALL {len(rows)} open, unmerged PR(s) on this repo, graded on hours "
            f"since the last commit to the head branch. Drafts INCLUDED -- the "
            f"convention here is 'DRAFT PR, the manager merges', so a draft is "
            f"the normal hand-back shape. {len(rows) - len(dated)} row(s) were "
            f"undateable and are excluded from the worst-wait figure."),
    }


def render_digest(verdict: Dict[str, Any], top: int = 8) -> str:
    if verdict["state"] != MEASURED:
        return f"[pr queue] {verdict['state'].upper()} -- {verdict['population']}"
    lines = [
        f"[pr queue] {verdict['over_threshold']} open PR(s) waiting on a merge "
        f"decision; worst {verdict['worst_hours']:.1f}h.",
        "  by state: " + (", ".join(f"{k}={v}" for k, v in
                                    sorted(verdict["by_state"].items())) or "none"),
        f"  population: {verdict['population']}",
    ]
    for r in verdict["rows"][:top]:
        q = "     ?" if r["quiet_hours"] is None else f"{r['quiet_hours']:6.1f}"
        lines.append(f"  {q}h  {r['state']:<13} #{r['pr']}  {r['title'][:56]}")
    return "\n".join(lines)


def read_state(path: Path = STATE_PATH) -> Tuple[Optional[Dict[str, Any]], bool]:
    return ql.read_state(path)


def refresh_due(state: Optional[Dict[str, Any]], state_readable: bool,
                now: datetime, refresh_hours: float = DEFAULT_REFRESH_HOURS
                ) -> Tuple[bool, str]:
    """Should the receipt be rewritten even though nothing changed?

    This is the LIVENESS half and is separate from the PAGE half on purpose: a
    quiet queue must still leave a dated trace, or `check_pr_queue_watch.py`
    cannot tell a watcher that is quiet from one that is dead.
    """
    if not state_readable or state is None:
        return True, "no readable receipt yet -- write one so liveness becomes gradeable."
    last = _parse_ts(state.get("generated_at"))
    if last is None:
        return True, "the receipt carries no parseable timestamp -- rewrite it."
    hours = (now - last).total_seconds() / 3600.0
    if hours >= refresh_hours:
        return True, (f"the receipt is {hours:.1f}h old (floor {refresh_hours}h) -- "
                      f"refresh it so a quiet watcher stays distinguishable from a "
                      f"dead one.")
    return False, (f"the receipt is {hours:.1f}h old, inside the {refresh_hours}h "
                   f"floor -- no write, so a quiet run does not open a PR per run.")


def build_state(verdict: Dict[str, Any], now: datetime, paged: bool,
                page_reason: str, prev: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    prev = prev or {}
    runs = [r for r in (prev.get("runs") or []) if isinstance(r, dict)][-19:]
    runs.append({
        "at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "read_state": verdict["state"],
        "waiting": verdict.get("over_threshold"),
        "worst_hours": verdict.get("worst_hours"),
        "escalated": paged,
    })
    out: Dict[str, Any] = {
        "_doc": (
            "RECEIPT of the PR-queue watcher (scripts/ops/pr_queue_latency.py, "
            "fired by .github/workflows/pr-queue-watch.yml). ⚠️ READ `generated_at`, "
            "NEVER THE CRON EXPRESSION -- a merged, enabled, syntactically correct "
            "scheduled workflow in this repo is NOT evidence that it fires "
            "(probes.yml's first scheduled run was ~4h50m late and fired once "
            "instead of daily). `scripts/ci/check_pr_queue_watch.py` grades this "
            "file's age in run_guards.py on every PR, so a dead watcher announces "
            "itself in everybody's CI instead of going quiet. ⚠️ An ABSENT file "
            "means the watcher has NEVER run, which is a different fact from "
            "STALE and needs a different fix."),
        "schema_version": 1,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "read_state": verdict["state"],
        "population": verdict["population"],
        "waiting": verdict.get("over_threshold"),
        "worst_hours": verdict.get("worst_hours"),
        "threshold_hours": verdict.get("threshold_hours"),
        "by_state": verdict.get("by_state") or {},
        "rows": verdict.get("rows") or [],
        "runs": runs,
    }
    if paged:
        out["last_paged_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        out["last_band"] = verdict.get("band")
        out["last_page_reason"] = page_reason
    else:
        for key in ("last_paged_at", "last_band", "last_page_reason"):
            if prev.get(key) is not None:
                out[key] = prev[key]
    if verdict["state"] != MEASURED:
        out["last_unknown_report_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    elif prev.get("last_unknown_report_at"):
        out["last_unknown_report_at"] = prev["last_unknown_report_at"]
    return out


# ---------------------------------------------------------------------------
# SELF-TEST -- the policy is a pure function precisely so it is arguable HERE
# rather than against a live queue. Every case asserts in BOTH directions: a
# planted condition fires AND a clean input stays quiet. One direction proves a
# check runs, never that it discriminates.
# ---------------------------------------------------------------------------
def _self_test(quiet: bool = False) -> Tuple[bool, List[str]]:
    fails: List[str] = []
    now = datetime(2026, 9, 2, 17, 0, tzinfo=timezone.utc)

    def check(label: str, cond: bool) -> None:
        if not cond:
            fails.append(label)
        elif not quiet:
            print(f"  ok   {label}")

    def pr(n, ref, title="t", draft=False, **kw):
        d = {"number": n, "head": {"ref": ref}, "title": title, "draft": draft}
        d.update(kw)
        return d

    def ago(h):
        return now - timedelta(hours=h)

    # --- the three READ states are distinct, and none is the others -----------
    v_none = assess(None, {}, now)
    check("an unreadable PR list grades no_observation", v_none["state"] == NO_OBSERVATION)
    check("no_observation reports NO count (never a fabricated zero)",
          v_none["over_threshold"] is None and v_none["worst_hours"] is None)
    check("no_observation says plainly it is not an empty queue",
          "NOT 'no PR is open'" in v_none["population"])

    v_empty = assess([], {}, now)
    check("an EMPTY queue grades measured, not no_observation",
          v_empty["state"] == MEASURED and v_empty["over_threshold"] == 0)

    v_und = assess([pr(1, "a")], {"a": None}, now)
    check("PRs read with no resolvable branch time grade undateable",
          v_und["state"] == UNDATEABLE)
    check("undateable keeps the COUNT and refuses the LATENCY",
          v_und["worst_hours"] is None and len(v_und["rows"]) == 1)

    # --- the per-PR states -----------------------------------------------------
    rows = [pr(1, "quiet"), pr(2, "busy"), pr(3, "held", title="[DO NOT MERGE] x"),
            pr(4, "gone")]
    times = {"quiet": ago(20), "busy": ago(0.5), "held": ago(30), "gone": None}
    v = assess(rows, times, now, threshold_hours=6.0)
    st = {r["pr"]: r["state"] for r in v["rows"]}
    check("a branch quiet past the threshold is `waiting`", st[1] == WAITING)
    check("a branch pushed inside the window is `active`, not waiting", st[2] == ACTIVE)
    check("a PR declaring DO NOT MERGE is `held_declared`", st[3] == HELD_DECLARED)
    check("an unresolvable head branch is `undateable`, never `active`",
          st[4] == PR_UNDATEABLE)
    check("held_declared is COUNTED and printed, never dropped",
          any(r["pr"] == 3 for r in v["rows"]) and v["by_state"][HELD_DECLARED] == 1)
    check("held_declared does NOT inflate the waiting count", v["over_threshold"] == 1)
    check("an undateable row does not inflate the waiting count either",
          all(r["state"] != WAITING for r in v["rows"] if r["pr"] == 4))
    check("the worst wait ignores undateable rows rather than guessing",
          abs(v["worst_hours"] - 30.0) < 0.01)

    # --- DRAFTS COUNT. Excluding them would have missed 6 of 6 live findings. ---
    v_draft = assess([pr(9, "d", draft=True)], {"d": ago(20)}, now)
    check("a DRAFT PR still grades `waiting` (the hand-back convention here)",
          v_draft["over_threshold"] == 1)

    # --- merged / closed rows are not a queue ----------------------------------
    check("a merged row is dropped by the normaliser",
          normalise_prs([{"number": 1, "merged": True}]) == [])
    check("a closed row is dropped by the normaliser",
          normalise_prs([{"number": 1, "state": "closed"}]) == [])
    check("a NON-list input is `we could not look`, not an empty queue",
          normalise_prs("boom") is None and normalise_prs(None) is None)
    check("an empty list is an EMPTY QUEUE, not a failed read",
          normalise_prs([]) == [])

    # --- the threshold discriminates in both directions -------------------------
    check("just under the threshold does not fire",
          assess([pr(1, "a")], {"a": ago(5.9)}, now, 6.0)["over_threshold"] == 0)
    check("just over the threshold does fire",
          assess([pr(1, "a")], {"a": ago(6.1)}, now, 6.0)["over_threshold"] == 1)

    # --- escalation policy: imported, and it holds ------------------------------
    v_hot = assess([pr(1, "a")], {"a": ago(20)}, now, 6.0)
    due, _ = ql.escalation_due(v_hot, None, True, now, DEFAULT_REPAGE_HOURS)
    check("a first escalation pages", due)
    just_paged = {"last_paged_at": (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "last_band": v_hot["band"]}
    due2, _ = ql.escalation_due(v_hot, just_paged, True, now, DEFAULT_REPAGE_HOURS)
    check("a standing condition inside the cooldown does NOT re-page", not due2)
    worse = assess([pr(1, "a")], {"a": ago(40)}, now, 6.0)
    due3, _ = ql.escalation_due(worse, just_paged, True, now, DEFAULT_REPAGE_HOURS)
    check("crossing into a NEW band re-pages even inside the cooldown",
          due3 and worse["band"] > v_hot["band"])
    due4, _ = ql.escalation_due(v_empty, None, True, now, DEFAULT_REPAGE_HOURS)
    check("a quiet queue never pages", not due4)
    due5, _ = ql.escalation_due(v_none, None, True, now, DEFAULT_REPAGE_HOURS)
    check("a NON-measured verdict never pages (nothing was graded)", not due5)
    due6, _ = ql.escalation_due(v_hot, None, False, now, DEFAULT_REPAGE_HOURS)
    check("an UNREADABLE latch pages rather than suppressing", due6)

    # --- liveness refresh is a SEPARATE decision from paging ---------------------
    ref_due, _ = refresh_due(None, True, now, 12.0)
    check("with no receipt at all, a refresh is due", ref_due)
    fresh = {"generated_at": (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")}
    ref_no, _ = refresh_due(fresh, True, now, 12.0)
    check("a fresh receipt is not rewritten (no PR-per-run)", not ref_no)
    old = {"generated_at": (now - timedelta(hours=13)).strftime("%Y-%m-%dT%H:%M:%SZ")}
    ref_yes, _ = refresh_due(old, True, now, 12.0)
    check("a receipt past the floor IS rewritten, so quiet stays gradeable", ref_yes)
    check("the refresh floor is strictly under the guard window, or a quiet "
          "watcher would fail CI", DEFAULT_REFRESH_HOURS < 30.0)

    # --- the receipt never loses a prior page, and always dates itself -----------
    s1 = build_state(v_hot, now, True, "first", None)
    check("a paged receipt records the band it paged at", s1["last_band"] == v_hot["band"])
    s2 = build_state(v_empty, now + timedelta(hours=1), False, "", s1)
    check("a later quiet run KEEPS the prior page record",
          s2.get("last_paged_at") == s1["last_paged_at"])
    check("every receipt is dated", bool(s2["generated_at"]))
    check("the run ring is bounded", len(build_state(v_hot, now, False, "", {
        "runs": [{"at": "x"} for _ in range(50)]})["runs"]) <= 20)

    # --- head-ref parsing is tolerant across both `gh` shapes -------------------
    check("`gh api` head shape parses", head_ref_of({"head": {"ref": "a"}}) == "a")
    check("`gh pr list --json` head shape parses",
          head_ref_of({"headRefName": "a"}) == "a")
    check("a PR with no resolvable head ref does not crash the grader",
          head_ref_of({"number": 1}) is None)

    if not quiet:
        print(f"\n{'FAIL' if fails else 'PASS'}: "
              f"{len(fails)} failure(s) in the pr-queue-latency policy")
        for f in fails:
            print(f"  FAIL {f}")
    return (not fails), fails


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--open-prs", default=None,
                    help="Path to the open-PR list as JSON (from `gh api "
                         "repos/OWNER/REPO/pulls?state=open`). ⚠️ OMITTING THIS "
                         "GRADES `no_observation`, NEVER an empty queue -- there is "
                         "deliberately no flag that asserts the queue is fine, "
                         "because asserting it is what fails.")
    ap.add_argument("--threshold-hours", type=float, default=DEFAULT_THRESHOLD_HOURS)
    ap.add_argument("--repage-hours", type=float, default=DEFAULT_REPAGE_HOURS)
    ap.add_argument("--refresh-hours", type=float, default=DEFAULT_REFRESH_HOURS)
    ap.add_argument("--write-state", action="store_true",
                    help="Persist the receipt. Written on an escalation, on a "
                         "verdict change, or once the receipt passes the refresh "
                         "floor -- so a quiet run leaves a dated trace without "
                         "opening a PR per run.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        ok, _ = _self_test()
        return 0 if ok else 1

    now = _now()
    raw = None
    if args.open_prs:
        try:
            raw = json.loads(Path(args.open_prs).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"could not read --open-prs: {exc}", file=sys.stderr)
            raw = None
    prs = normalise_prs(raw)
    times = branch_times_from_git(
        sorted({head_ref_of(p) or "" for p in (prs or [])} - {""})) if prs else {}
    verdict = assess(prs, times, now, args.threshold_hours)

    state, readable = read_state()
    if verdict["state"] == MEASURED:
        paged, reason = ql.escalation_due(verdict, state, readable, now, args.repage_hours)
    else:
        paged, reason = ql.unknown_report_due(verdict, state, readable, now,
                                              args.repage_hours)

    changed = ((state or {}).get("read_state") != verdict["state"]
               or (state or {}).get("waiting") != verdict.get("over_threshold"))
    ref_due, ref_reason = refresh_due(state, readable, now, args.refresh_hours)
    wrote = False
    if args.write_state and (paged or changed or ref_due):
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(
            json.dumps(build_state(verdict, now, paged, reason, state),
                       indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        wrote = True

    if args.json:
        print(json.dumps({"verdict": verdict, "escalate": paged, "reason": reason,
                          "receipt_written": wrote, "refresh": ref_reason},
                         indent=2, ensure_ascii=False, default=str))
    else:
        print(render_digest(verdict))
        print(f"\n  escalate: {paged} -- {reason}")
        print(f"  receipt : {'written' if wrote else 'not written'} -- {ref_reason}")

    if verdict["state"] != MEASURED:
        return 4
    return 3 if paged else 0


if __name__ == "__main__":
    raise SystemExit(main())
