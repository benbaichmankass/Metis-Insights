#!/usr/bin/env python3
"""THE REAPER — what a session left behind, recorded by something that is not
that session.

WHY THIS EXISTS
---------------
`docs/claude/work/objects/WO-20260901-PHASE-E.yaml` names three mechanisms and
ships two of them:

  1. incremental progress written as work happens   — partially, see below
  2. a lease per session                            — `manager_lease.py`
  3. **a reaper** — an expired/abandoned session is detected, and what was
     actually done is recorded against the object **by something that is not
     the dead session**

(3) did not exist. Measured 2026-09-02 at `main` 90437919 with a positive
control (`manager_lease` matches 4 files, so the probe can find a positive):
``grep -rlnE "reaper|reap_|expired[_ ]lease"`` over ``scripts/`` matched only
`manager_lease.py` and `handoff_check.py` — the two files that mention the
concept in prose — plus one unrelated GPU-burst module. Nothing reaped anything.

THE DESIGN CONSTRAINT THAT DECIDES THE SHAPE
--------------------------------------------
⚠️ **A DEAD SESSION CANNOT REPORT ITS OWN DEATH**, which is the same reason
`close_session.py` cannot satisfy Phase E: it is session-invoked, so a session
that dies never runs it.

⚠️ **AND THE MANAGER CANNOT BE THE CLOCK EITHER.** Measured 2026-09-02: four
sub-sessions sat IDLE/BLOCKED for up to **142 minutes** while the manager
asserted they held work. A mechanism that depends on the manager noticing is
not a mechanism. So the reaper is EXTERNAL and CADENCED — a GitHub Actions
cron (`.github/workflows/session-reaper.yml`), which is a clock no session owns.

WHAT THE REAPER CAN SEE, AND WHAT IT CANNOT
-------------------------------------------
Stated rather than hidden, because a reaper whose blind spots are unstated is
worse than none — a successor reads its clean report as coverage.

CAN see (all of it from `origin` + the repo, no MCP, no live session state):
  * every row in `SESSIONS.json` and the branches/PRs it names
  * whether each branch exists on `origin`, and its newest commit time
  * whether a PR the row names is in `origin/main`'s history
  * ``claude/**`` heads on `origin` that NO registry row names — i.e. sessions
    that demonstrably existed and were never registered
  * **the branch a row FAILED to record**, recovered from the ``Claude-Session:``
    trailer every commit in this repo carries. ⚠️ **THIS IS THE INCREMENTAL-
    PROGRESS MECHANISM PHASE E ASKS FOR, AND IT ALREADY EXISTED UNREAD** — the
    `provenance-consumer-guard` shape one level up: a signal WRITTEN and never
    READ. The session stamps its own id into every commit *as work happens*, so
    the link from session to work is durable in git before any close-out runs.
    Measured 2026-09-02: **35 of 67 registry rows name NO branch at all** — the
    registry is a spawn-time record nothing ever updates — and scanning the 194
    ``claude/**`` heads for trailers located the work of **16 of those 35**
    anyway, with no cooperation from the sessions concerned.

CANNOT see:
  * **whether a session is alive.** Only `list_sessions` answers that and CI
    holds no such tool. The reaper grades on a PROXY — branch quiescence — and
    that is only safe because of the next paragraph.
  * **work that was never pushed.** Zero visibility, by construction: it lives
    in a container nothing can reach. This is not a gap to be closed by a
    better reaper; the only remedy is pushing early, which is why
    `session_registry.py`'s spawn prompt makes a pushed branch a PRECONDITION
    of reporting done.
  * **an unregistered session's work object.** It can COUNT unregistered
    branches; it cannot say what they were for.

⚠️ **THE REAPER RECORDS. IT NEVER KILLS, ARCHIVES, DELETES OR CLOSES ANYTHING.**
That is what makes grading on a proxy defensible: the cost of calling a live
session `stalled_with_work` is a refreshed row in a ledger. A reaper that acted
on the same proxy would have to be right about death, and it cannot be.

STATES, NEVER COLLAPSED
-----------------------
``active``               a branch on `origin` with a commit inside the freshness
                         window. Working, and its work is durable.
``stalled_with_work``    a branch on `origin`, nothing new inside the window.
                         ⚠️ **THE WORK IS SAFE.** What has stopped is
                         SUPERVISION, not the work — this is precisely the
                         state a successor can pick up, and it is the outcome a
                         kill is *supposed* to produce.
``landed``               no branch on `origin`, and a PR this row names is in
                         `origin/main`'s history. Merged and cleaned up.
``no_landing_evidence``  no branch on `origin` and no merged PR named.
                         ⚠️ **THIS IS NOT "LOST" AND MUST NEVER BE READ AS IT.**
                         The registry records PRs by hand, so a row whose work
                         landed under a PR number nobody wrote down grades here
                         too. Measured on the live registry (population: all 34
                         branch entries across 67 rows at `main` 90437919):
                         11 `on origin`, 14 `landed`, **7 `no_landing_evidence`**,
                         2 malformed — and at least one of the 7
                         (`claude/two-way-telegram-decisions`) has work that DID
                         land, as #10789. The reaper cannot separate *work lost*
                         from *record stale*, and says so instead of guessing.
``unreadable``           the row could not be graded — a malformed branch entry,
                         an unparseable field. ⚠️ *We could not look*, never a
                         pass. Two live rows carry PROSE in their `branches`
                         array (`'(rotating — one PR head at a time)'` and
                         `'claude/telegram-ping-mirror (expected; not yet
                         pushed)'`); the second is the documented lost-work case
                         from Phase E itself.

Usage:
    python3 scripts/ops/session_reaper.py --self-test
    python3 scripts/ops/session_reaper.py --json
    python3 scripts/ops/session_reaper.py --write     # append to the ledger

Exit 0 always unless `--strict` (then non-zero on a finding). Tier-1: reads
`origin` and the repo, writes only its own ledger.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]
REGISTRY = REPO / "docs/claude/work/SESSIONS.json"
LEDGER = REPO / "docs/claude/work/REAPER-OBSERVATIONS.json"

#: Every state this module may emit. Declared once so a caller can branch
#: exhaustively rather than testing for the two it happens to remember.
REAPER_STATES = (
    "active",
    "stalled_with_work",
    "landed",
    "no_landing_evidence",
    "unreadable",
)

#: WHERE the branch the reaper graded came from. A SEPARATE AXIS from
#: `REAPER_STATES`, deliberately: "what state is this work in" and "did the
#: registry actually record where it is" are different questions, and folding
#: the second into the first would hide the registry's own failure behind a
#: healthy-looking work state.
BRANCH_SOURCE_STATES = (
    #: the registry row named it. The record is intact.
    "registry_declared",
    #: the row named nothing (or nothing usable) and the reaper recovered the
    #: branch from a ``Claude-Session:`` commit trailer. ⚠️ THE RECORD FAILED
    #: AND THE REAPER RESCUED IT — a run with many of these is a registry
    #: problem being masked by the reaper, not a healthy registry.
    "recovered_from_commit_trailer",
    #: neither. ⚠️ *We could not look*, never "the session produced nothing".
    "none_found",
)

#: How long a branch may go without a commit before it stops counting as
#: `active`. A THRESHOLD, not a death certificate — see the module docstring on
#: why grading on a proxy is safe here.
DEFAULT_STALE_AFTER_MIN = 90

REPO_PREFIX = "Metis-Insights:"


# ---------------------------------------------------------------- git reads


def _git(*args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=str(REPO), capture_output=True, text=True
    )
    return out.stdout if out.returncode == 0 else ""


def collect_origin_state(fetch: bool = True) -> Dict:
    """Everything the reaper knows about `origin`, gathered once.

    Separated from grading so `grade_row` stays a PURE function — the policy is
    then arguable in tests rather than against a live remote, which is the
    lesson `BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG`
    paid for.
    """
    if fetch:
        _git("fetch", "--quiet", "origin")
    heads: Dict[str, str] = {}
    for line in _git("ls-remote", "--heads", "origin").strip().splitlines():
        if "\t" not in line:
            continue
        sha, ref = line.split("\t", 1)
        heads[ref.replace("refs/heads/", "").strip()] = sha.strip()

    log = _git("log", "--oneline", "--no-merges", "-3000", "origin/main")
    merged_prs = set(re.findall(r"\(#(\d+)\)", log))
    return {
        "heads": heads,
        "merged_prs": merged_prs,
        "head_count": len(heads),
        "merged_pr_count": len(merged_prs),
        # `unreadable_remote` is a real state: an empty ls-remote is "we could
        # not look", NOT "origin has no branches". Grading it as the latter
        # would report every session as no_landing_evidence at once.
        "remote_read": bool(heads),
    }


def branch_last_commit_utc(branch: str) -> Optional[str]:
    """ISO-8601 of the newest commit on `origin/<branch>`, or None if unknown.

    None is *we could not date it*, never *it is old*: an undateable branch
    cannot be shown to be stale, so `grade_row` refuses to call it stalled.
    """
    raw = _git("log", "-1", "--format=%cI", f"origin/{branch}").strip()
    return raw or None


SESSION_ID_RE = re.compile(r"session_[A-Za-z0-9]{20,}")


def attribute_branches_by_trailer(origin: Dict, session_ids,
                                  max_commits: int = 40) -> Dict[str, List[str]]:
    """session id -> ``claude/**`` branches whose UNMERGED commits name it.

    ⚠️ **THIS READS A SIGNAL THAT ALREADY EXISTED AND HAD NO CONSUMER.** Every
    commit in this repo carries a ``Claude-Session:`` trailer, written by the
    session AS IT WORKS. That is the durable session->work link Phase E's
    "incremental progress" mechanism asks for; nothing read it until now.

    Scoped to commits reachable from a ``claude/**`` head but NOT from
    `origin/main` (``^origin/main``), for two reasons: a merged commit's work is
    already `landed` and needs no recovery, and it bounds the scan to the
    unmerged frontier instead of the whole history.

    ⚠️ **RECOVERY IS NOT A SUBSTITUTE FOR THE REGISTRY RECORDING ITS BRANCH.**
    It finds work that a row failed to name; it cannot find work never pushed,
    and it cannot say what an unregistered session's work was FOR. A run that
    recovers many rows is a registry problem, not a healthy registry.
    """
    wanted = set(session_ids)
    found: Dict[str, List[str]] = {}
    for branch in sorted(origin.get("heads", {})):
        if not branch.startswith("claude/"):
            continue
        body = _git("log", "--format=%B%x00", f"-{max_commits}",
                    f"origin/{branch}", "^origin/main")
        if not body:
            continue
        for sid in set(SESSION_ID_RE.findall(body)):
            if sid in wanted:
                found.setdefault(sid, []).append(branch)
    return found


# ---------------------------------------------------------------- grading


def _branch_names(row: Dict) -> List[str]:
    """The branch names in this row that name a branch IN THIS REPO.

    Cross-repo entries (``ict-trader-dashboard:...``) are not this reaper's to
    grade and are dropped rather than counted as missing — reporting another
    repo's branch as absent from `origin` here would be a finding about nothing.
    """
    out: List[str] = []
    for raw in row.get("branches") or []:
        b = str(raw)
        if ":" in b:
            if not b.startswith(REPO_PREFIX):
                continue
            b = b.split(":", 1)[1]
        out.append(b.strip())
    return out


def _is_branch_shaped(name: str) -> bool:
    """A branch name, or prose that was typed into a `branches` array?

    Git refuses whitespace, `(`, `~`, `^`, `:` and `?` in ref names, so this is
    a real structural test rather than a guess about intent.
    """
    if not name:
        return False
    return not any(c in name for c in " \t()~^:?*[\\")


def _pr_numbers(row: Dict) -> List[str]:
    nums: List[str] = []
    for raw in row.get("prs") or []:
        # Entries are variously "Metis-Insights#10654", "#210", or a bare int.
        m = re.search(r"(\d+)\s*$", str(raw))
        if m:
            nums.append(m.group(1))
    return nums


def grade_row(row: Dict, origin: Dict, now: datetime,
              stale_after_min: int = DEFAULT_STALE_AFTER_MIN,
              commit_times: Optional[Dict[str, Optional[str]]] = None,
              attributed: Optional[Dict[str, List[str]]] = None) -> Dict:
    """Grade ONE registry row. Pure — no git, no clock, no network.

    `commit_times` maps branch name -> ISO-8601 newest commit (or None for
    *undateable*). `attributed` maps session id -> branches recovered from
    commit trailers. Supplying both is what keeps this testable.
    """
    commit_times = commit_times or {}
    sid = str(row.get("session_id") or row.get("registry_key") or "?")
    obj = row.get("owns_object")
    names = _branch_names(row)
    malformed = [n for n in names if not _is_branch_shaped(n)]
    usable = [n for n in names if _is_branch_shaped(n)]

    recovered = list((attributed or {}).get(sid) or [])
    branch_source = "registry_declared" if usable else (
        "recovered_from_commit_trailer" if recovered else "none_found")
    if not usable and recovered:
        usable = recovered

    base = {
        "session_id": sid,
        "owns_object": obj,
        "title": row.get("title"),
        "branches_in_repo": usable,
        "branch_source": branch_source,
        "recovered_branches": recovered if branch_source ==
        "recovered_from_commit_trailer" else [],
        "malformed_branch_entries": malformed,
        "prs": _pr_numbers(row),
    }

    if not origin.get("remote_read", True):
        return {**base, "state": "unreadable",
                "why": "origin could not be read; no row can be graded against it"}

    if not usable:
        if malformed:
            return {**base, "state": "unreadable",
                    "why": f"only non-branch text in `branches`: {malformed!r}"}
        # A row naming no branch at all cannot be graded either way. It is not
        # evidence of loss and it is not evidence of safety.
        return {**base, "state": "unreadable",
                "why": "the row names no branch in this repo"}

    on_origin = [b for b in usable if b in origin["heads"]]
    if on_origin:
        stamps = [commit_times.get(b) for b in on_origin]
        dated = [s for s in stamps if s]
        if not dated:
            # We have the branch — so the WORK IS SAFE — but cannot date it.
            # `stalled_with_work` is the honest floor: it says the work is
            # durable and supervision is unproven, which is exactly true.
            return {**base, "state": "stalled_with_work",
                    "newest_commit_utc": None,
                    "why": "branch is on origin; its newest commit could not be dated"}
        newest = max(dated)
        try:
            ts = datetime.fromisoformat(newest.replace("Z", "+00:00"))
        except ValueError:
            return {**base, "state": "stalled_with_work",
                    "newest_commit_utc": newest,
                    "why": "branch is on origin; its commit timestamp did not parse"}
        age_min = (now - ts).total_seconds() / 60.0
        state = "active" if age_min <= stale_after_min else "stalled_with_work"
        return {**base, "state": state,
                "newest_commit_utc": newest,
                "branch_age_minutes": round(age_min, 1),
                "why": ("a commit inside the freshness window"
                        if state == "active"
                        else f"no commit for {age_min:.0f} min; THE WORK IS ON ORIGIN "
                             "— supervision has stopped, the work has not been lost")}

    landed = [n for n in base["prs"] if n in origin["merged_prs"]]
    if landed:
        return {**base, "state": "landed", "merged_prs": landed,
                "why": f"no branch on origin and PR(s) {landed} are in origin/main"}

    return {**base, "state": "no_landing_evidence",
            "why": ("no branch on origin and no PR this row names is in origin/main. "
                    "⚠️ NOT a claim the work was lost — the row may simply never "
                    "have recorded the PR its work landed under.")}


def coverage(rows: List[Dict], origin: Dict,
             unregistered_owners: Optional[Dict[str, List[str]]] = None) -> Dict:
    """What the reaper could NOT see, measured rather than asserted.

    `unregistered_owners` maps session id -> branches, for sessions whose work
    is on `origin` and which NO registry row names. ⚠️ **THIS IS THE HALF A
    REGISTRY-KEYED REAPER WOULD MISS, AND IT MISSES IT EXACTLY WHEN IT MATTERS**
    — `SESSIONS.json` has been measured incomplete twice (3 of 6 absent on
    2026-09-01; 26 of 55 on 2026-09-02, 17 carrying the manager's own id as
    parent). Reading the commit trailer instead of the registry means an
    unregistered session's work is still ATTRIBUTABLE to a session id, which is
    the difference between "184 branches nobody claims" and "this named session
    left this work behind".
    """
    declared = set()
    for r in rows:
        for b in _branch_names(r):
            if _is_branch_shaped(b):
                declared.add(b)
    session_shaped = [
        h for h in origin.get("heads", {})
        if h.startswith("claude/") and h not in declared
    ]
    owners = unregistered_owners or {}
    return {
        # A branch on origin under claude/ that no registry row names. Each is a
        # session that demonstrably EXISTED and was never registered.
        "unregistered_claude_branches": len(session_shaped),
        "unregistered_sample": sorted(session_shaped)[:15],
        # ...and, where its commits carry a `Claude-Session:` trailer, WHICH
        # session left it. This is recovered without the registry and without
        # the session, which is the only route that works for the case the
        # registry is measured to fail at.
        "unregistered_but_attributable_sessions": len(owners),
        "unregistered_owner_map": {k: v[:3] for k, v in sorted(owners.items())},
        "unregistered_attribution_caveat": (
            "A session id recovered here says WHOSE the work is, never WHAT it "
            "was for: `owns_object` lives only in the registry, so an "
            "unregistered session's work is locatable and still unattached to "
            "any work object. Recovery is not a substitute for registering."
        ),
        "registry_declared_branches": len(declared),
        "origin_head_count": origin.get("head_count", 0),
        # ⚠️ Deliberately NOT a ratio. Many `claude/**` heads are board-post and
        # relay branches rather than sub-sessions, and the registry only began
        # on 2026-09-01, so a percentage here would describe a population this
        # module cannot define. The COUNT plus this caveat is the honest form.
        "unregistered_caveat": (
            "COUNT ONLY, no ratio: `claude/**` includes board-post and relay "
            "branches that were never sub-sessions, and the registry began "
            "2026-09-01 while branches predate it. This is an upper bound on "
            "unregistered sessions, not a measurement of them."
        ),
        "unpushed_work": "invisible_by_construction",
        "unpushed_caveat": (
            "Work never pushed lives in a container nothing outside it can "
            "reach. The reaper has ZERO visibility of it and no reaper can "
            "have any. The only remedy is pushing early."
        ),
        "liveness": "not_observed",
        "liveness_caveat": (
            "The reaper cannot tell alive from dead — `list_sessions` is an MCP "
            "tool CI does not hold. It grades branch quiescence, a PROXY, which "
            "is safe only because it records and never acts."
        ),
    }


def reap(now: Optional[datetime] = None, stale_after_min: int = DEFAULT_STALE_AFTER_MIN,
         fetch: bool = True) -> Dict:
    now = now or datetime.now(timezone.utc)
    origin = collect_origin_state(fetch=fetch)
    try:
        registry = json.loads(REGISTRY.read_text())
        rows = registry.get("sessions") or []
        read_state = "registry_read"
    except Exception as exc:  # noqa: BLE001 — an unreadable registry is a STATE
        rows, read_state = [], f"unreadable: {exc}"

    # Recover the branches the registry failed to record, BEFORE grading, so a
    # row that names nothing is still graded on its real work rather than on the
    # registry's silence.
    unrecorded = [str(r.get("session_id")) for r in rows
                  if r.get("session_id")
                  and not [b for b in _branch_names(r) if _is_branch_shaped(b)]]
    attributed = (attribute_branches_by_trailer(origin, unrecorded)
                  if (fetch and unrecorded) else {})

    commit_times: Dict[str, Optional[str]] = {}
    interesting = set()
    for r in rows:
        interesting.update(b for b in _branch_names(r) if _is_branch_shaped(b))
    for bs in attributed.values():
        interesting.update(bs)
    for b in interesting:
        if b in origin["heads"]:
            commit_times[b] = branch_last_commit_utc(b) if fetch else None

    # Whose is the work on a branch NO registry row names? Read the trailer
    # rather than the registry — the registry is precisely what failed here.
    declared_all = set()
    for r in rows:
        declared_all.update(b for b in _branch_names(r) if _is_branch_shaped(b))
    for bs in attributed.values():
        declared_all.update(bs)
    unregistered_owners: Dict[str, List[str]] = {}
    if fetch:
        for b in sorted(origin.get("heads", {})):
            if not b.startswith("claude/") or b in declared_all:
                continue
            body = _git("log", "--format=%B%x00", "-40", f"origin/{b}", "^origin/main")
            for sid in set(SESSION_ID_RE.findall(body or "")):
                unregistered_owners.setdefault(sid, []).append(b)

    graded = [grade_row(r, origin, now, stale_after_min, commit_times, attributed)
              for r in rows]
    by_state = {s: sum(1 for g in graded if g["state"] == s) for s in REAPER_STATES}
    by_source = {s: sum(1 for g in graded if g.get("branch_source") == s)
                 for s in BRANCH_SOURCE_STATES}
    return {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "registry_read_state": read_state,
        "stale_after_minutes": stale_after_min,
        "rows_graded": len(graded),
        # Both partitions sum to rows_graded by construction, so each is
        # checkable rather than trusted.
        "by_state": by_state,
        # ⚠️ READ THIS BESIDE `by_state`, NEVER `by_state` ALONE. A healthy
        # `by_state` sitting on top of a large `recovered_from_commit_trailer`
        # count means the REGISTRY failed and the reaper covered for it.
        "by_branch_source": by_source,
        "observations": graded,
        "coverage": coverage(rows, origin, unregistered_owners),
    }


def write_ledger(report: Dict) -> Path:
    """Append this run to the durable ledger, keyed so a row is findable by the
    OBJECT it belongs to — i.e. recorded *against the object*, by something that
    is not the session that made the work.
    """
    try:
        led = json.loads(LEDGER.read_text())
    except Exception:  # noqa: BLE001 — first run, or a file we must not silently drop
        led = {"_doc": [
            "THE REAPER'S LEDGER — what each session left behind, recorded by",
            "the reaper (an external cron) rather than by the session itself.",
            "",
            "A dead session cannot run its own close-out. This file is what a",
            "manager arriving COLD reads to learn what a session that is no",
            "longer running actually produced.",
            "",
            "⚠️ `no_landing_evidence` IS NOT `lost`. See the state table in",
            "scripts/ops/session_reaper.py. ⚠️ `coverage` names what the reaper",
            "CANNOT see — read it beside `by_state`, never `by_state` alone.",
        ], "schema_version": 1, "runs": []}
    led["updated_at"] = report["generated_at"]
    led["latest"] = {k: report[k] for k in
                     ("generated_at", "rows_graded", "by_state",
                      "by_branch_source", "coverage")}
    led["runs"] = ([{k: report[k] for k in
                     ("generated_at", "rows_graded", "by_state", "by_branch_source")}]
                   + led.get("runs", []))[:60]
    led["observations"] = report["observations"]
    LEDGER.write_text(json.dumps(led, indent=2, ensure_ascii=False) + "\n")
    return LEDGER


# ---------------------------------------------------------------- self-test


def _self_test() -> int:
    now = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
    origin = {"heads": {"claude/live": "a", "claude/quiet": "b", "claude/other": "c"},
              "merged_prs": {"10654"}, "head_count": 3, "merged_pr_count": 1,
              "remote_read": True}
    fails = []

    def check(label, got, want):
        ok = got == want
        print(f"  {label}: {'PASS' if ok else f'FAIL (got {got!r}, want {want!r})'}")
        if not ok:
            fails.append(label)

    fresh = (now - timedelta(minutes=5)).isoformat()
    old = (now - timedelta(minutes=600)).isoformat()

    check("1 fresh commit on origin -> active",
          grade_row({"session_id": "s1", "branches": ["claude/live"]}, origin, now,
                    commit_times={"claude/live": fresh})["state"], "active")

    g = grade_row({"session_id": "s2", "branches": ["claude/quiet"]}, origin, now,
                  commit_times={"claude/quiet": old})
    check("2 quiet branch on origin -> stalled_with_work", g["state"], "stalled_with_work")
    check("2b ...and the reason says the work is NOT lost",
          "not been lost" in g["why"], True)

    check("3 branch gone + PR merged -> landed",
          grade_row({"session_id": "s3", "branches": ["claude/gone"],
                     "prs": ["Metis-Insights#10654"]}, origin, now)["state"], "landed")

    check("4 branch gone + no merged PR -> no_landing_evidence (NOT 'lost')",
          grade_row({"session_id": "s4", "branches": ["claude/gone"]},
                    origin, now)["state"], "no_landing_evidence")

    check("5 prose in `branches` -> unreadable, never a pass",
          grade_row({"session_id": "s5",
                     "branches": ["claude/x (expected; not yet pushed)"]},
                    origin, now)["state"], "unreadable")

    check("6 undateable branch on origin -> stalled_with_work, never active",
          grade_row({"session_id": "s6", "branches": ["claude/live"]}, origin, now,
                    commit_times={"claude/live": None})["state"], "stalled_with_work")

    dead = {**origin, "remote_read": False, "heads": {}}
    check("7 unreadable origin -> unreadable, NOT no_landing_evidence",
          grade_row({"session_id": "s7", "branches": ["claude/live"]},
                    dead, now)["state"], "unreadable")

    check("8 a cross-repo branch is not graded as missing here",
          grade_row({"session_id": "s8",
                     "branches": ["ict-trader-dashboard:claude/spa"]},
                    origin, now)["state"], "unreadable")

    # --- the branch-source axis: the registry's failure must stay visible ---
    g = grade_row({"session_id": "s9", "branches": []}, origin, now,
                  commit_times={"claude/quiet": old},
                  attributed={"s9": ["claude/quiet"]})
    check("11 a row naming NO branch is graded on its recovered branch",
          g["state"], "stalled_with_work")
    check("11b ...and the recovery is declared, not silently absorbed",
          g["branch_source"], "recovered_from_commit_trailer")

    check("12 a row the trailer scan could not attribute -> none_found",
          grade_row({"session_id": "sA", "branches": []}, origin, now,
                    attributed={})["branch_source"], "none_found")

    check("13 a registry-declared branch is NOT reported as recovered",
          grade_row({"session_id": "sB", "branches": ["claude/live"]}, origin, now,
                    commit_times={"claude/live": fresh},
                    attributed={"sB": ["claude/other"]})["branch_source"],
          "registry_declared")

    check("14 recovery never upgrades a row to `active` on an undateable branch",
          grade_row({"session_id": "sC", "branches": []}, origin, now,
                    commit_times={}, attributed={"sC": ["claude/quiet"]})["state"],
          "stalled_with_work")

    cov = coverage([{"session_id": "s", "branches": ["claude/live"]}], origin)
    check("9 an unregistered claude/ branch is counted",
          cov["unregistered_claude_branches"], 2)
    check("9b ...and is reported as a COUNT with no ratio",
          "ratio" in cov["unregistered_caveat"], True)
    check("10 unpushed work is declared invisible, never zero",
          cov["unpushed_work"], "invisible_by_construction")

    print(f"\nsession-reaper self-test: {'PASS' if not fails else 'FAIL ' + str(fails)}")
    return 1 if fails else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write", action="store_true", help="append to the ledger")
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--stale-after-minutes", type=int, default=DEFAULT_STALE_AFTER_MIN)
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero when any row grades unreadable")
    a = ap.parse_args(argv)

    if a.self_test:
        return _self_test()

    rep = reap(stale_after_min=a.stale_after_minutes, fetch=not a.no_fetch)
    if a.write:
        print(f"ledger: {write_ledger(rep)}")
    if a.json:
        print(json.dumps(rep, indent=2, ensure_ascii=False))
    else:
        print(f"session-reaper — {rep['rows_graded']} row(s) graded "
              f"at {rep['generated_at']}")
        for s in REAPER_STATES:
            print(f"  {s:22} {rep['by_state'][s]}")
        print("\nWHERE THE GRADED BRANCH CAME FROM (read beside the states above):")
        for s in BRANCH_SOURCE_STATES:
            print(f"  {s:32} {rep['by_branch_source'][s]}")
        c = rep["coverage"]
        print("\nWHAT THIS RUN COULD NOT SEE:")
        print(f"  unregistered claude/ branches on origin: "
              f"{c['unregistered_claude_branches']} (count only — "
              f"{c['unregistered_caveat'].split(':')[0]})")
        print(f"    ...of which attributable to a session by commit trailer: "
              f"{c['unregistered_but_attributable_sessions']} session id(s)")
        print(f"  unpushed work: {c['unpushed_work']}")
        print(f"  liveness:      {c['liveness']}")
        for g in rep["observations"]:
            if g["state"] in ("stalled_with_work", "no_landing_evidence", "unreadable"):
                print(f"\n  [{g['state']}] {g['session_id']}  obj={g['owns_object']}")
                print(f"      {g['why']}")

    if a.strict and rep["by_state"]["unreadable"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
