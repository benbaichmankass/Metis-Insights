#!/usr/bin/env python3
"""MANAGER PREFLIGHT — may this manager act right now, or does something have
to be true first?

WHY THIS IS CODE AND NOT A `manager` SKILL.md
---------------------------------------------
On 2026-09-02 a manager session failed four ways in one afternoon, and three of
the four were rules it had ALREADY READ:

  | rule                                          | where it lived            | outcome |
  | a bot commit at HEAD means CI never fired     | CLAUDE.md, VERBATIM       | hit 3x  |
  | ping the operator on autonomous action (F6)   | MI-54, filed that morning | 0 pings in 8h |
  | poll SESSIONS.json for a blocked sub-session  | step 3 of every sweep     | skipped every pass |

Everything that DID hold that day was an executable refusal — `handoff_check.py`
grading `unknown`, `run_probes.py --check` failing a row with no probe,
`check_backlog_criteria.py` refusing a bad severity, `open-items-guard` refusing
an empty observation, `backlog_append.detect_format` refusing to write a file it
could not reproduce. **Not one of them is a document.**

So this file is deliberately not prose about the workflow. It is the workflow's
rules, in the only form that has been observed to bind. Its shape is copied from
`handoff_check.py` — the one manager mechanism that worked all day — including
the part that matters most: **refusing is a legitimate, useful output.**

WHAT IT ENFORCES, AND WHAT A VIOLATION COSTS
--------------------------------------------
Each check maps to a specific structural rule in
`docs/design/operating-model-DESIGN.md`, and each names its cost rather than
just failing.

 1. ``work_has_parent``   *"Nothing is worked that has no parent."* A live
                          sub-session whose registry row names no work object is
                          an orphan task: no successor can situate it, and the
                          constraint readout cannot see it at all. Measured
                          2026-09-02: **0 of 18 working rows named one**, while
                          the field (`owns_object`) exists and is populated on 5
                          of 67 rows — none of them working.
 2. ``session_cap``       the operator's concurrency cap on SESSIONS. Graded as
                          an INTERVAL, because two things are uncertain at once:
                          which observed sessions are live, and (today) what the
                          limit actually is. Over every candidate limit is a
                          definite FAIL; under every candidate limit is a
                          definite PASS; the band between them refuses.
 3. ``artifact_cap``      the operator's INDEPENDENT cap on ARTIFACTS. Several
                          sessions may share one artifact and one session may
                          hold several, so neither cap implies the other.
                          ⚠️ **This grades `unknown` today and that is correct**
                          — the limit is agreed and the POPULATION is not. See
                          `CONCURRENCY-CAPS.json`.
 4. ``f6_digest_owed``    F6 is *the condition on which autonomy was granted*.
                          Refuses when autonomous actions have accumulated past
                          the newest queued ping. ⚠️ It targets ONE ROLLED-UP
                          DIGEST, never a ping per action — this repo measured
                          202 of 376 CRITICALs in a single window being one
                          un-latched alarm, which trained the operator past the
                          channel reserved for an unprotected position.
 5. ``subsession_queue``  a BLOCKED sub-session is the manager's queue, not the
                          sub-session's problem. Four sat idle with explicit
                          asks while the manager reported they "held work"; one
                          waited 1h50m.
 6. ``bot_authored_head`` trap (a). A branch whose tip commit is authored by a
                          bot triggers no workflows, so its PR shows **zero
                          checks and is BLOCKED, not green**. Hit three times in
                          one afternoon by a manager that had the rule in front
                          of it.
 7. ``register_edits``    traps (b) and (d). A read-append-write on a register
                          that does not round-trip reformats the whole file and
                          re-attributes it; and a register-touching PR
                          re-conflicts with every sibling that touches the same
                          register.
 8. ``blocked_claims``    trap (c). Work blocked on a claim about live state
                          that nobody probed. The motivating case (MI-65) blocked
                          on a secret being unset, read out of a day-old doc,
                          against the operator's own contrary sighting.
 9. ``lease``             delegated ENTIRELY to `manager_lease.grade` — the same
                          call `handoff_check` makes. Duplicating the policy
                          would let the two drift.

THE SELF-TEST RUNS ON EVERY INVOCATION, NOT BEHIND A FLAG
----------------------------------------------------------
A check whose teeth are assumed rather than demonstrated is the
`check_selftest_wiring` defect: registered, and never shown to be able to fail.
So `run()` executes the planted-failure suite first and, if it does not pass,
the ENTIRE verdict is `unknown` — this tool refuses to grade a manager with
machinery it has just failed to verify. `--self-test` exists only to run the
suite alone.

THREE STATES, NEVER COLLAPSED
-----------------------------
``ready``      every check passed.
``not_ready``  at least one check FAILED. A known blocker.
``unknown``    nothing failed and something could not be LOOKED AT.

⚠️ ``ready`` IS UNOBTAINABLE WITHOUT A LIVE OBSERVATION, and that is the
enforcement rather than an inconvenience. There is deliberately **no flag that
asserts the sessions are fine** — asserting it is what failed, twice, and it is
the same reasoning `handoff_check.py` gives for its own refusal.

WHAT THIS DELIBERATELY DOES NOT TRY TO CHECK — the honest boundary
------------------------------------------------------------------
* **"The manager does not execute items."** Whether an action was management or
  a build is a judgement about intent. Any proxy (owner == "manager" and tier
  looks buildy) would be matching English for a semantic property, which is
  diagnostic-provenance sub-class A — the thing this repo defers C4 over. It
  stays prose.
* **Which sibling PRs a register edit will re-conflict with.** That needs the
  file lists of every open PR, which this container cannot fetch. Check 7 names
  the conflict SURFACE it can see and grades `unknown` rather than implying it
  looked.
* **Whether a queued ping was DELIVERED.** The delivery ledger
  (`runtime_logs/pending_pings_delivered`) is VM-local and gitignored. Check 4
  can prove a digest was WRITTEN and nothing more; it says so in its own message
  rather than letting "queued" read as "the operator was told".

EXIT CODES: 0 ready · 3 not_ready · 4 unknown. Both non-ready states are
non-zero so a caller cannot treat "we could not look" as a pass.
"""
#
# wiring: manual-only - a MANAGER runs this before spawning, merging, or handing
# over; it is deliberately NOT registered in run_guards.py. Every check here FAILS
# on origin/main today (18 parentless sessions, 122 unnotified actions, 1 unprobed
# blockage), so wiring it into CI would fail every PR from day one -- the "52
# findings for 26 days" condition, where a permanently-red check becomes something
# everyone learns to walk past. That is the same desensitised-alarm failure this
# file's own F6 check exists to prevent, and shipping it would be self-defeating.
# Wiring it into CI becomes correct once the findings are drained, not before.
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import manager_lease  # noqa: E402
import session_registry as sr  # noqa: E402

REPO_ROOT = sr.REPO_ROOT
WORK_DIR = REPO_ROOT / "docs" / "claude" / "work"
OBJECTS_DIR = WORK_DIR / "objects"
CAPS_PATH = WORK_DIR / "CONCURRENCY-CAPS.json"
PINGS_PATH = REPO_ROOT / "docs" / "claude" / "pending-pings.jsonl"

PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"

#: Registers where a read-append-write is known to be dangerous, or where two
#: concurrent PRs reliably collide. Not "every JSON file" — a list that grows
#: without argument stops being read.
REGISTERS: Tuple[str, ...] = (
    "docs/claude/OPEN-ITEMS.json",
    "docs/claude/work/SESSIONS.json",
    "docs/claude/work/MANAGER-CHECKLIST.json",
    "docs/claude/work/OPEN-PRS.json",
    "docs/claude/work/MANAGER-LEASE.json",
    "docs/claude/health-review-backlog.json",
    "docs/claude/performance-review-backlog.json",
    "docs/claude/ml-review-backlog.json",
    "docs/claude/research-review-backlog.json",
    "CLAUDE.md",
)

#: A diff touching more than this fraction of a register's lines is a REFORMAT
#: wearing an edit's clothes. CHOSEN, not tuned: the motivating failure rewrites
#: essentially the whole file (a naive round-trip of OPEN-ITEMS.json rewrites
#: 100% of it), and a genuine hand edit to one row of a 700+ line register moves
#: single digits. There is a wide empty band between those, so the exact value
#: is not load-bearing; anything in 0.1–0.5 separates the two cases identically.
REFORMAT_FRACTION = 0.25
REFORMAT_MIN_LINES = 100

#: Autonomous actions tolerated before a digest is owed. CHOSEN so that the
#: measured 2026-09-02 failure — 82 commits landed on main and 40 sub-sessions
#: spawned since the newest queued ping, over ~13 hours — trips at the third
#: action rather than the eighty-second. It cannot nag: the count is taken
#: strictly after the newest ping, so ONE digest returns it to zero.
DIGEST_THRESHOLD = 3

_BOT_AUTHOR_RE = re.compile(r"\[bot\]\s*$|\[bot\]@")

#: Substrings that mark an observed session as EXPLICITLY handing something back
#: to the manager. Matched case-insensitively against whatever status-ish string
#: a row carries, and the matched token is REPORTED, so a reader can contradict
#: the classification instead of having to trust it.
_NEEDS_ACTION_TOKENS = ("review_ready", "needs_action", "needs_input", "blocked",
                        "awaiting", "input_required")
#: Statuses that mean the session is NOT actively working. ⚠️ Kept apart from
#: the set above, deliberately. Measured 2026-09-02 the two populations were
#: BOTH 16 and pooling them would have reported one number for two different
#: facts — an explicit hand-back is a request, while a registry that says
#: `working` over a platform that says `idle` is a bookkeeping divergence. They
#: have different remedies (answer it / correct the row), and this repo has
#: already had to un-pool exactly this shape once (`starved` vs `no_winner` in
#: arbitration_fanout_soak, where pooling overstated the finding 6.5x).
_NOT_WORKING_TOKENS = ("idle", "paused", "suspended")
#: Statuses that mean the session is over. Everything else counts as live, which
#: is the pessimistic direction for a CAP and the safe one.
_TERMINAL_TOKENS = ("archiv", "complete", "closed", "cancel", "fail", "expired",
                    "stopped", "terminated", "done")


# --------------------------------------------------------------------------
# small shared helpers
# --------------------------------------------------------------------------
def _c(name: str, state: str, message: str, **extra: Any) -> Dict[str, Any]:
    return dict(check=name, state=state, message=message, **extra)


def _parse_ts(value: Any) -> Optional[datetime]:
    """Accept the several timestamp spellings these registers actually carry.

    Measured on the live files: pings use ``2026-09-02T00:19:53.578570+00:00``
    and registry rows use ``2026-09-02T11:51:40Z``. A parser that handled only
    one would silently treat every row of the other kind as undated, which reads
    as 'no actions since the last ping' — the permissive direction, and the one
    that reproduces the failure being checked for.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _norm_edges(value: Any) -> List[Dict[str, Any]]:
    """`blocked_on` is a typed edge — but only in the schema.

    Measured on MANAGER-CHECKLIST.json 2026-09-02 it appears as a LIST of dicts,
    as a BARE dict, and as a BARE STRING, and one entry uses ``kind: item``
    which is not in the declared vocabulary at all. Normalising here (rather
    than assuming the schema) is what lets check 8 grade the string form as the
    unprobed claim it is, instead of crashing on it or skipping it.
    """
    if isinstance(value, dict):
        return [value]
    if isinstance(value, str) and value.strip():
        return [{"kind": None, "ref": value.strip(), "_shape": "bare_string"}]
    if isinstance(value, list):
        out: List[Dict[str, Any]] = []
        for e in value:
            out.extend(_norm_edges(e))
        return out
    return []


def _git(args: Sequence[str]) -> Optional[str]:
    try:
        r = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True,
                           text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _status_tokens(row: Dict[str, Any]) -> List[str]:
    """Every status-ish string on a row, lowercased, including one level of
    ``post_turn_summary`` — which is where ``needs_action`` lives."""
    vals: List[str] = []
    for key in ("status", "session_status", "status_bucket", "state", "bucket"):
        v = row.get(key)
        if isinstance(v, str) and v.strip():
            vals.append(v.strip().lower())
    summary = row.get("post_turn_summary")
    if isinstance(summary, dict):
        na = summary.get("needs_action")
        if isinstance(na, str) and na.strip():
            vals.append("needs_action:" + na.strip().lower())
        elif na:
            vals.append("needs_action")
        for key in ("status_bucket", "status"):
            v = summary.get(key)
            if isinstance(v, str) and v.strip():
                vals.append(v.strip().lower())
    if row.get("needs_action"):
        vals.append("needs_action")
    return vals


# --------------------------------------------------------------------------
# 1 · work_has_parent — "nothing is worked that has no parent"
# --------------------------------------------------------------------------
def known_object_ids() -> Optional[set]:
    try:
        return {p.stem for p in OBJECTS_DIR.glob("*.yaml")}
    except OSError:
        return None


def check_work_has_parent(reg_doc: Optional[Any], reg_readable: bool,
                          object_ids: Optional[set],
                          enforced: Sequence[str]) -> Dict[str, Any]:
    if not reg_readable:
        return _c("work_has_parent", UNKNOWN,
                  "SESSIONS.json could not be parsed, so no session could be checked "
                  "for a parent. WE DID NOT LOOK.")
    if object_ids is None:
        return _c("work_has_parent", UNKNOWN,
                  f"{OBJECTS_DIR} could not be listed, so a named parent could not be "
                  f"resolved to a real object. WE DID NOT LOOK.")
    rows = sr.registry_rows(reg_doc)
    live = [r for r in rows if (r.get("state") or "") in enforced]
    missing = [r for r in live if not (r.get("owns_object") or "").strip()]
    dangling = [r for r in live
                if (r.get("owns_object") or "").strip()
                and (r.get("owns_object") or "").strip() not in object_ids]
    # CENSUS the wider number, so narrow enforcement can never hide it — the
    # session_registry.py discipline.
    census = sum(1 for r in rows if not (r.get("owns_object") or "").strip())
    pop = (f"population: {len(live)} registry row(s) in state {list(enforced)} of "
           f"{len(rows)} total; {len(object_ids)} objects on disk; "
           f"{census}/{len(rows)} rows overall name no object")
    if missing or dangling:
        ids = [r.get("session_id") or r.get("registry_key") for r in missing + dangling]
        return _c("work_has_parent", FAIL,
                  f"{len(missing)} live session(s) name NO parent work object and "
                  f"{len(dangling)} name one that does not exist. A step has a "
                  f"MANDATORY parent — without it the work is an orphan no successor "
                  f"can situate, and it is invisible to the constraint readout, which "
                  f"walks objects and never sees a session. Fix with "
                  f"`owns_object: <WO-id>` on the row (author the object first if it "
                  f"does not exist). {pop}",
                  missing=[r.get("session_id") for r in missing],
                  dangling=[(r.get("session_id"), r.get("owns_object")) for r in dangling],
                  ids=ids, population=pop)
    if not live:
        return _c("work_has_parent", UNKNOWN,
                  f"no registry row is in state {list(enforced)}, so nothing was "
                  f"graded. That is not the same as every session having a parent. "
                  f"{pop}", population=pop)
    return _c("work_has_parent", PASS,
              f"every live session names a parent object that exists. {pop}",
              population=pop)


# --------------------------------------------------------------------------
# 2/3 · the two caps
# --------------------------------------------------------------------------
def read_caps() -> Tuple[Optional[Any], bool]:
    return sr.read_json(CAPS_PATH)


def cap_bounds(cap: Any) -> Tuple[Optional[int], Optional[int], List[Any]]:
    """(lowest candidate, highest candidate, all candidates) for one cap.

    A settled `limit` is a single candidate. A `contested` list yields several,
    and the caller grades the COUNT against the whole set rather than picking —
    which is what makes an unresolved contest cost nothing outside the narrow
    band where the answer actually depends on it.
    """
    if not isinstance(cap, dict):
        return None, None, []
    cands: List[int] = []
    if isinstance(cap.get("limit"), int):
        cands.append(cap["limit"])
    for entry in cap.get("contested") or []:
        if isinstance(entry, dict) and isinstance(entry.get("value"), int):
            cands.append(entry["value"])
    if not cands:
        return None, None, []
    return min(cands), max(cands), sorted(set(cands))


def classify_live(observation: List[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    """(definitely live ids, unclassifiable ids). Terminal is what we can name;
    everything else counts as live, which is pessimistic for a cap and therefore
    safe — an unrecognised status can only ever make the count too HIGH, never
    too low."""
    live, unknown_ids = [], []
    for row in observation:
        sid = row.get("session_id")
        if not sid:
            continue
        toks = _status_tokens(row)
        if not toks:
            unknown_ids.append(sid)
            continue
        if any(t in tok for tok in toks for t in _TERMINAL_TOKENS):
            continue
        live.append(sid)
    return live, unknown_ids


def check_session_cap(caps_doc: Optional[Any], caps_readable: bool,
                      reg_doc: Optional[Any], reg_readable: bool,
                      observation: Optional[List[Dict[str, Any]]],
                      enforced: Sequence[str]) -> Dict[str, Any]:
    if not caps_readable or not isinstance(caps_doc, dict):
        return _c("session_cap", UNKNOWN,
                  f"{CAPS_PATH.name} could not be read, so there is no declared "
                  f"limit to grade against. WE DID NOT LOOK. This tool hardcodes no "
                  f"cap, deliberately — a number in code separates from the "
                  f"operator's words about it.")
    cap = (caps_doc.get("caps") or {}).get("sessions")
    lo, hi, cands = cap_bounds(cap)
    if lo is None:
        return _c("session_cap", UNKNOWN,
                  "no session limit is declared (neither `limit` nor `contested`), so "
                  "nothing can be graded. WE DID NOT LOOK.")
    if observation is None:
        return _c("session_cap", UNKNOWN,
                  f"candidate limit(s) {cands}, but NO live-session observation was "
                  f"supplied, so nothing was counted. ⚠️ The registry alone is a CLAIM, "
                  f"not an observation — it has been measured incomplete twice (3 of 6, "
                  f"then 26 of 55 unregistered) and would under-count exactly when it "
                  f"matters. Pass --live-sessions.", candidates=cands)
    if not reg_readable:
        return _c("session_cap", UNKNOWN,
                  "SESSIONS.json could not be parsed, so observed sessions could not be "
                  "attributed to this manager. WE DID NOT LOOK.", candidates=cands)
    declared = {r.get("session_id") for r in sr.registry_rows(reg_doc)
                if (r.get("state") or "") in enforced and r.get("session_id")}
    live_ids, unclassified = classify_live(observation)
    # The cap counts work THIS MANAGER assigned: the intersection of "declared
    # live in the registry" and "observed present".
    counted = sorted(declared & set(live_ids))
    lower = len(counted)
    upper = lower + len(sorted(declared & set(unclassified)))
    pop = (f"population: {len(observation)} observed session(s); {len(declared)} "
           f"registry row(s) in state {list(enforced)}; count interval [{lower}, "
           f"{upper}] against candidate limit(s) {cands}")
    if lower > hi:
        return _c("session_cap", FAIL,
                  f"{lower} concurrent session(s) — over the cap under EVERY candidate "
                  f"limit ({cands}), so the contest does not matter here. The operator's "
                  f"instruction is a SPAWN GATE: do not start another until a slot "
                  f"frees. {pop}", counted=counted, interval=[lower, upper],
                  candidates=cands, population=pop)
    if upper <= lo:
        return _c("session_cap", PASS,
                  f"{upper} concurrent session(s) at most — under the cap under EVERY "
                  f"candidate limit ({cands}). {pop}", counted=counted,
                  interval=[lower, upper], candidates=cands, population=pop)
    reason = ("the declared limit is CONTESTED and the count falls in the band where "
              "the answer depends on which value is right"
              if len(cands) > 1 else
              "some observed sessions carry no status this tool can classify")
    return _c("session_cap", UNKNOWN,
              f"count is somewhere in [{lower}, {upper}] against candidate limit(s) "
              f"{cands} — {reason}. REFUSING rather than picking. Resolve it in "
              f"{CAPS_PATH.name} (or supply richer session status) and re-run. {pop}",
              counted=counted, interval=[lower, upper], candidates=cands,
              unclassified=sorted(declared & set(unclassified)), population=pop)


def artifact_candidates(open_prs: Optional[List[Dict[str, Any]]]) -> Optional[Dict[str, int]]:
    """The candidate populations, computed rather than asserted, so the operator
    decides with numbers in front of them."""
    if open_prs is None:
        return None
    def _is(pr: Dict[str, Any], *needles: str) -> bool:
        text = f"{pr.get('title', '')} {json.dumps(pr.get('labels') or [])}".lower()
        return any(n in text for n in needles)
    def head(pr: Dict[str, Any]) -> str:
        return ((pr.get("head") or {}).get("ref")) or ""
    total = len(open_prs)
    no_dnm = [p for p in open_prs if not _is(p, "do not merge", "do-not-merge")]
    no_auto = [p for p in no_dnm if not head(p).startswith("automation/")]
    return {
        "all_open_prs": total,
        "excluding_do_not_merge": len(no_dnm),
        "excluding_do_not_merge_and_automation": len(no_auto),
        "open_non_draft": sum(1 for p in open_prs if not p.get("draft")),
        "open_non_draft_excluding_both": sum(1 for p in no_auto if not p.get("draft")),
    }


def check_artifact_cap(caps_doc: Optional[Any], caps_readable: bool,
                       open_prs: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    if not caps_readable or not isinstance(caps_doc, dict):
        return _c("artifact_cap", UNKNOWN,
                  f"{CAPS_PATH.name} could not be read. WE DID NOT LOOK.")
    cap = (caps_doc.get("caps") or {}).get("artifacts")
    lo, hi, cands = cap_bounds(cap)
    definition = (cap or {}).get("definition") if isinstance(cap, dict) else None
    cand_counts = artifact_candidates(open_prs)
    if not definition:
        msg = (f"REFUSED — the artifact cap has a LIMIT ({cands or 'undeclared'}) and "
               f"NO POPULATION. `definition` is null in {CAPS_PATH.name}, so there is "
               f"nothing to count. ⚠️ This is a legitimate output, not a gap to paper "
               f"over: 'open PR' overcounts (a DO-NOT-MERGE PR and a bot-filed "
               f"automation PR are not artifacts in flight) and 'in_flight object' "
               f"undercounts (a doc, a workflow or a running soak may have no object). "
               f"A confident wrong count would be worse than this refusal.")
        if cand_counts:
            msg += (" Candidate populations, measured from the supplied open-PR list: "
                    + ", ".join(f"{k}={v}" for k, v in cand_counts.items())
                    + ". Every one of them exceeds a limit of 4, so the definition is "
                      "not academic.")
        else:
            msg += (" No open-PR list was supplied, so the candidate populations could "
                    "not even be measured — pass --open-prs to see them.")
        return _c("artifact_cap", UNKNOWN, msg, candidates=cands,
                  candidate_populations=cand_counts, definition=None)
    if lo is None:
        return _c("artifact_cap", UNKNOWN,
                  "a population is defined but no limit is declared. WE DID NOT LOOK.",
                  definition=definition)
    if cand_counts is None:
        return _c("artifact_cap", UNKNOWN,
                  f"population `{definition}` is defined and limit(s) {cands} declared, "
                  f"but no open-PR observation was supplied to count over. WE DID NOT "
                  f"LOOK.", definition=definition, candidates=cands)
    count = cand_counts.get(str(definition))
    if count is None:
        return _c("artifact_cap", UNKNOWN,
                  f"population `{definition}` is declared but this tool cannot compute "
                  f"it; known populations are {sorted(cand_counts)}. REFUSING rather "
                  f"than substituting a different one — reporting a value under a "
                  f"label that does not describe it is the substitution class this "
                  f"repo has a guard for.",
                  definition=definition, candidate_populations=cand_counts)
    pop = f"population: `{definition}` = {count}; candidate limit(s) {cands}"
    if count > hi:
        return _c("artifact_cap", FAIL,
                  f"{count} artifact(s) in flight — over the cap under every candidate "
                  f"limit {cands}. {pop}", count=count, population=pop)
    if count <= lo:
        return _c("artifact_cap", PASS, f"{count} artifact(s) in flight. {pop}",
                  count=count, population=pop)
    return _c("artifact_cap", UNKNOWN,
              f"{count} artifact(s) against CONTESTED limit(s) {cands} — the verdict "
              f"depends on which is right. REFUSING rather than picking. {pop}",
              count=count, population=pop)


# --------------------------------------------------------------------------
# 4 · f6_digest_owed
# --------------------------------------------------------------------------
def read_pings(path: Path = PINGS_PATH) -> Tuple[Optional[List[Dict[str, Any]]], bool]:
    try:
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
        return rows, True
    except OSError:
        return None, False


def newest_ping(rows: Iterable[Dict[str, Any]],
                event: Optional[str] = None) -> Optional[datetime]:
    best = None
    for r in rows:
        if event and r.get("event") != event:
            continue
        ts = _parse_ts(r.get("at"))
        if ts and (best is None or ts > best):
            best = ts
    return best


def count_autonomous_actions(since: datetime, reg_doc: Optional[Any],
                             base: str) -> Tuple[Optional[int], Optional[int]]:
    """(commits landed on `base` since, sub-sessions spawned since).

    ⚠️ THE DATE FILTERING IS DONE HERE, NOT BY ``git --since``, AND THAT IS
    DELIBERATE. Measured 2026-09-02 on git 2.43.0 against this repo (population:
    all 2263 commits reachable from HEAD): ``--since=2027-01-01T00:00:00+00:00``
    and ``--since=2099-…`` both correctly return 0, while
    ``--since=2999-01-01T00:00:00+00:00`` returns **all 2263** — the filter is
    silently DROPPED, with no error and no warning, when the date overflows
    git's parser.

    That is an unasserted denominator (diagnostic-provenance sub-class C) sitting
    underneath a check whose whole job is counting: a timestamp git dislikes
    would make every commit in history read as "since the last ping". It was
    caught by this file's own planted-failure suite, which is the argument for
    running that suite on every invocation rather than behind a flag. Reading
    ``%cI`` and comparing in Python removes the class outright — no date string
    reaches git at all.
    """
    out = _git(["log", base, "--format=%H%x00%cI"])
    commits = None
    if out is not None:
        commits = 0
        for line in out.splitlines():
            _, _, iso = line.partition("\x00")
            ts = _parse_ts(iso)
            # ⚠️ An UNPARSEABLE commit date is counted, not skipped. Skipping it
            # would under-count autonomous actions, which is the permissive
            # direction and the one that reproduces the failure being checked for.
            if ts is None or ts > since:
                commits += 1
    spawns = None
    if isinstance(reg_doc, (dict, list)):
        spawns = sum(1 for r in sr.registry_rows(reg_doc)
                     if (_parse_ts(r.get("spawned_at")) or datetime.min.replace(
                         tzinfo=timezone.utc)) > since)
    return commits, spawns


def check_f6_digest(ping_rows: Optional[List[Dict[str, Any]]], pings_readable: bool,
                    reg_doc: Optional[Any], base: str,
                    threshold: int = DIGEST_THRESHOLD) -> Dict[str, Any]:
    if not pings_readable or ping_rows is None:
        return _c("f6_digest_owed", UNKNOWN,
                  f"{PINGS_PATH.name} could not be read, so whether the operator has "
                  f"been told anything is unestablished. WE DID NOT LOOK — this is not "
                  f"'no digest is owed'.")
    newest = newest_ping(ping_rows)
    if newest is None:
        return _c("f6_digest_owed", UNKNOWN,
                  "no ping row carries a parseable `at`, so there is no point to count "
                  "from. WE DID NOT LOOK.")
    commits, spawns = count_autonomous_actions(newest, reg_doc, base)
    if commits is None:
        return _c("f6_digest_owed", UNKNOWN,
                  f"`git log {base}` could not be run, so autonomous actions could not "
                  f"be counted. WE DID NOT LOOK.")
    digest = newest_ping(ping_rows, event="work_digest")
    total = commits + (spawns or 0)
    caveat = ("⚠️ A queued ping is not a DELIVERED one — the delivery ledger is "
              "VM-local and gitignored, so this check can prove a digest was WRITTEN "
              "and nothing more.")
    pop = (f"population: pings in {PINGS_PATH.name} (newest {newest.isoformat()}"
           + (f", newest work_digest {digest.isoformat()}" if digest else
              ", NO work_digest row ever")
           + f"); {commits} commit(s) on {base} and "
           + (f"{spawns} registry spawn(s)" if spawns is not None
              else "an unreadable registry")
           + " since")
    if total > threshold:
        return _c("f6_digest_owed", FAIL,
                  f"{total} autonomous action(s) ({commits} landed on {base}, "
                  f"{spawns if spawns is not None else '?'} sub-sessions spawned) since "
                  f"the newest queued ping, against a threshold of {threshold}. **F6 is "
                  f"the condition on which autonomy was granted.** Queue ONE ROLLED-UP "
                  f"DIGEST covering them — never a ping per action; a channel that "
                  f"fires constantly delivers less visibility than one that fires "
                  f"rarely, and this repo has already trained an operator past its own "
                  f"CRITICAL channel that way. {caveat} {pop}",
                  commits=commits, spawns=spawns, total=total,
                  since=newest.isoformat(), population=pop)
    return _c("f6_digest_owed", PASS,
              f"{total} autonomous action(s) since the newest queued ping — within the "
              f"threshold of {threshold}. {caveat} {pop}",
              commits=commits, spawns=spawns, total=total,
              since=newest.isoformat(), population=pop)


# --------------------------------------------------------------------------
# 5 · subsession_queue
# --------------------------------------------------------------------------
def check_subsession_queue(observation: Optional[List[Dict[str, Any]]],
                           reg_doc: Optional[Any], reg_readable: bool,
                           enforced: Sequence[str]) -> Dict[str, Any]:
    if observation is None:
        return _c("subsession_queue", UNKNOWN,
                  "no live-session observation was supplied, so no sub-session could be "
                  "polled. ⚠️ WE DID NOT LOOK — and 'I did not poll' is exactly how four "
                  "sessions came to sit idle with explicit asks while the manager "
                  "reported they held work. Pass --live-sessions (list_sessions or "
                  "get_session output).")
    if not reg_readable:
        return _c("subsession_queue", UNKNOWN,
                  "SESSIONS.json could not be parsed, so observed sessions could not be "
                  "attributed to this manager. WE DID NOT LOOK.")
    mine = {r.get("session_id") for r in sr.registry_rows(reg_doc)
            if (r.get("state") or "") in enforced and r.get("session_id")}
    graded, waiting, stalled, statusless = 0, [], [], 0
    for row in observation:
        sid = row.get("session_id")
        if sid not in mine:
            continue
        toks = _status_tokens(row)
        if not toks:
            statusless += 1
            continue
        graded += 1
        hit = [t for tok in toks for t in _NEEDS_ACTION_TOKENS if t in tok]
        entry = {"session_id": sid, "matched": sorted(set(hit)), "status": toks}
        if hit:
            waiting.append(entry)
            continue
        # NOT an explicit hand-back: the registry declares this row live while
        # the platform says it is not working. A different fact, a different fix.
        idle = [t for tok in toks for t in _NOT_WORKING_TOKENS if t in tok]
        dead = [t for tok in toks for t in _TERMINAL_TOKENS if t in tok]
        if idle or dead:
            stalled.append({"session_id": sid, "matched": sorted(set(idle + dead)),
                            "status": toks, "terminal": bool(dead)})
    pop = (f"population: {len(mine)} registry row(s) in state {list(enforced)}; "
           f"{graded} of them carried a gradeable status in the observation; "
           f"{statusless} carried none. ⚠️ The two counts below are DIFFERENT "
           f"populations and are deliberately not pooled")
    if waiting or stalled:
        parts = []
        if waiting:
            parts.append(
                f"{len(waiting)} sub-session(s) are EXPLICITLY waiting on you "
                f"(handed something back and stopped). A blocked sub-session is the "
                f"MANAGER'S QUEUE, not the sub-session's problem — one waited 1h50m "
                f"on 2026-09-02 while this poll was skipped on every pass. Answer them.")
        if stalled:
            n_dead = sum(1 for s in stalled if s["terminal"])
            parts.append(
                f"{len(stalled)} row(s) the registry calls live are NOT WORKING on the "
                f"platform ({n_dead} of them terminal). That is a bookkeeping "
                f"divergence, not a request — and it is why the session cap cannot be "
                f"counted off the registry alone. Close them out or correct the row.")
        return _c("subsession_queue", FAIL,
                  " ".join(parts) + f" The matched token is reported per row so you can "
                  f"contradict the classification rather than having to trust it. {pop}",
                  waiting=waiting, stalled=stalled, population=pop)
    if graded == 0:
        return _c("subsession_queue", UNKNOWN,
                  f"an observation was supplied but NOT ONE of this manager's rows "
                  f"carried a status field this tool could read, so nothing was graded. "
                  f"WE DID NOT LOOK — this is not 'nobody is blocked'. `list_sessions` "
                  f"may not carry per-session status; `get_session` "
                  f"(post_turn_summary.needs_action / status_bucket) does. {pop}",
                  population=pop)
    return _c("subsession_queue", PASS,
              f"none of the {graded} graded sub-session(s) is waiting on you. {pop}",
              population=pop)


# --------------------------------------------------------------------------
# 6 · bot_authored_head
# --------------------------------------------------------------------------
def is_bot_author(name: str, email: str) -> bool:
    return bool(_BOT_AUTHOR_RE.search(name or "") or _BOT_AUTHOR_RE.search(email or ""))


def check_bot_authored_head(base: str = "origin/main") -> Dict[str, Any]:
    out = _git(["log", f"{base}..HEAD", "--format=%H%x00%an%x00%ae%x00%s"])
    if out is None:
        return _c("bot_authored_head", UNKNOWN,
                  f"`git log {base}..HEAD` could not be run (no remote-tracking ref, or "
                  f"a shallow/detached clone), so the tip's author is unestablished. WE "
                  f"DID NOT LOOK.")
    rows = [ln.split("\x00") for ln in out.splitlines() if ln.strip()]
    if not rows:
        return _c("bot_authored_head", PASS,
                  f"no commits of your own on top of {base} — nothing to push, so no "
                  f"PR can be silently check-less yet.")
    sha, name, email, subject = (rows[0] + ["", "", "", ""])[:4]
    if is_bot_author(name, email):
        return _c("bot_authored_head", FAIL,
                  f"the tip commit {sha[:8]} is authored by `{name}`. GitHub does not "
                  f"trigger workflows for a bot/GITHUB_TOKEN push, so a PR with this at "
                  f"HEAD shows **zero checks and is BLOCKED, not green** — and "
                  f"`total_count: 0` is indistinguishable from 'CI has not started' and "
                  f"from a merge conflict. Push one ordinary commit of your own to arm "
                  f"CI, and read `mergeable_state` before concluding anything from a "
                  f"zero check count. This trap was hit THREE times on 2026-09-02 by a "
                  f"manager with the rule in front of it. Subject: {subject!r}",
                  sha=sha, author=name, email=email)
    return _c("bot_authored_head", PASS,
              f"tip commit {sha[:8]} is authored by `{name}` (not a bot), so a push will "
              f"arm CI.", sha=sha, author=name)


# --------------------------------------------------------------------------
# 7 · register_edits
# --------------------------------------------------------------------------
def json_round_trips(path: Path) -> Optional[bool]:
    """Can this register be rewritten byte-identically by a naive dump?

    If not, ANY read-append-write reformats the whole file and re-attributes it
    to the touching PR. This is COMPUTED here rather than carried as a doc claim,
    because the doc claim is exactly the kind that goes stale — measured
    2026-09-02, OPEN-ITEMS.json does not round-trip while all four work registers
    do (indent=2, ensure_ascii=False, trailing newline).
    """
    try:
        raw = path.read_bytes()
        doc = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    for indent in (1, 2, 3, 4):
        for ensure_ascii in (True, False):
            for tail in ("\n", ""):
                cand = (json.dumps(doc, indent=indent,
                                   ensure_ascii=ensure_ascii) + tail).encode()
                if cand == raw:
                    return True
    return False


def touched_registers(base: str) -> Optional[List[Tuple[str, int, int]]]:
    """[(path, changed_lines, total_lines)] for every register this branch touches."""
    out = _git(["diff", "--numstat", f"{base}...HEAD", "--", *REGISTERS])
    if out is None:
        return None
    found = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, deleted, path = parts
        if added == "-" or deleted == "-":
            continue  # binary; not a register we grade
        try:
            total = len((REPO_ROOT / path).read_text(encoding="utf-8").splitlines())
        except OSError:
            total = 0
        found.append((path, int(added) + int(deleted), total))
    return found


def check_register_edits(base: str, open_prs: Optional[List[Dict[str, Any]]]
                         ) -> Dict[str, Any]:
    touched = touched_registers(base)
    if touched is None:
        return _c("register_edits", UNKNOWN,
                  f"`git diff {base}...HEAD` could not be run, so which registers this "
                  f"branch touches is unestablished. WE DID NOT LOOK.")
    if not touched:
        return _c("register_edits", PASS,
                  "this branch touches no shared register, so it has an empty conflict "
                  "surface and no reformat risk.")
    reformats, notes = [], []
    for path, changed, total in touched:
        rt = json_round_trips(REPO_ROOT / path) if path.endswith(".json") else None
        if rt is False:
            notes.append(f"{path} does NOT round-trip (verified now) — a "
                         f"read-append-write WILL reformat it; edit it with anchored "
                         f"replacements and check `git diff --numstat`")
        if total >= REFORMAT_MIN_LINES and changed > REFORMAT_FRACTION * total:
            reformats.append({"path": path, "changed": changed, "total": total,
                              "fraction": round(changed / total, 3)})
    if reformats:
        detail = "; ".join(f"{r['path']} {r['changed']}/{r['total']} lines "
                           f"({r['fraction']:.0%})" for r in reformats)
        return _c("register_edits", FAIL,
                  f"this branch REWRITES a register rather than editing it: {detail}. "
                  f"That is a reformat wearing an edit's clothes — it re-attributes the "
                  f"whole file to this PR, buries the real change, and conflicts with "
                  f"every sibling PR that touches the same file. "
                  + (" ".join(notes) if notes else ""),
                  reformats=reformats, touched=[t[0] for t in touched])
    surface = [t[0] for t in touched]
    if open_prs is None:
        return _c("register_edits", UNKNOWN,
                  f"this branch touches {len(surface)} shared register(s) ({', '.join(surface)}) "
                  f"and the diff is surgical, but NO open-PR list was supplied, so which "
                  f"siblings it will re-conflict with is unestablished. ⚠️ WE DID NOT "
                  f"LOOK. Pass --open-prs. (This tool can name the conflict SURFACE; it "
                  f"cannot fetch other PRs' file lists from this container, so it refuses "
                  f"rather than implying it checked.) "
                  + (" ".join(notes) if notes else ""),
                  touched=surface, notes=notes)
    others = [p.get("number") for p in open_prs if p.get("number")]
    return _c("register_edits", PASS,
              f"this branch touches {len(surface)} register(s) ({', '.join(surface)}) "
              f"with a surgical diff. {len(others)} other PR(s) are open — name which of "
              f"them touch the same files BEFORE you land this, or you will re-conflict "
              f"them. " + (" ".join(notes) if notes else ""),
              touched=surface, open_pr_count=len(others), notes=notes)


# --------------------------------------------------------------------------
# 8 · blocked_claims
# --------------------------------------------------------------------------
#: Edge kinds that name something INSIDE the repo, whose state a reader can go
#: and check. Everything else is a claim about the world.
_INTERNAL_KINDS = {"object", "item", "step", "intent", "pr"}


def edge_is_live_state_claim(edge: Dict[str, Any]) -> bool:
    kind = (edge.get("kind") or "").strip().lower()
    if not kind:
        return True  # a bare string names nothing checkable
    return kind not in _INTERNAL_KINDS


def edge_has_evidence(item: Dict[str, Any], edge: Dict[str, Any]) -> bool:
    """A dated observation somewhere on the row or the edge. Deliberately
    permissive about WHERE — the point is to catch a blockage with no probe at
    all, not to police field names on a hand-written register."""
    blob = json.dumps({k: v for k, v in item.items()
                       if k in ("blocked_on_basis", "verified_at", "observed",
                                "measured", "basis")}, ensure_ascii=False)
    blob += json.dumps(edge, ensure_ascii=False)
    return bool(re.search(r"\d{4}-\d{2}-\d{2}", blob))


def check_blocked_claims(ck_doc: Optional[Any], ck_readable: bool) -> Dict[str, Any]:
    if not ck_readable or not isinstance(ck_doc, dict):
        return _c("blocked_claims", UNKNOWN,
                  "MANAGER-CHECKLIST.json could not be parsed, so blocked work could "
                  "not be checked for evidence. WE DID NOT LOOK.")
    items = [i for i in (ck_doc.get("items") or []) if isinstance(i, dict)]
    blocked = [i for i in items if (i.get("state") or "") == "blocked"]
    findings = []
    for item in blocked:
        for edge in _norm_edges(item.get("blocked_on")):
            if edge_is_live_state_claim(edge) and not edge_has_evidence(item, edge):
                findings.append({"id": item.get("id"),
                                 "ref": edge.get("ref"),
                                 "kind": edge.get("kind"),
                                 "shape": edge.get("_shape", "typed")})
    pop = (f"population: {len(blocked)} blocked item(s) of {len(items)}; an edge counts "
           f"as a live-state CLAIM when its kind is outside {sorted(_INTERNAL_KINDS)} or "
           f"it is a bare string naming nothing checkable")
    if findings:
        return _c("blocked_claims", FAIL,
                  f"{len(findings)} item(s) are blocked on a claim about live state with "
                  f"NO dated observation behind it. **Probe it before you block work on "
                  f"it.** On 2026-09-02 a session was held on a secret declared unset "
                  f"from a day-old doc, against the operator's own contrary sighting — "
                  f"a doc is a claim about the world, never a reading of it. Add a dated "
                  f"`blocked_on_basis` / `verified_at`, or unblock. {pop}",
                  findings=findings, population=pop)
    if not blocked:
        return _c("blocked_claims", PASS,
                  f"no item is in state `blocked`, so nothing is held on an unprobed "
                  f"claim. {pop}", population=pop)
    return _c("blocked_claims", PASS,
              f"every live-state blockage carries a dated observation. {pop}",
              population=pop)


# --------------------------------------------------------------------------
# 9 · lease — delegated entirely, so the policy cannot drift from handoff_check
# --------------------------------------------------------------------------
def check_lease(lease, readable, me: Optional[str]) -> Dict[str, Any]:
    state, msg = manager_lease.grade(lease, readable, me)
    if state == "unreadable":
        return _c("lease", UNKNOWN, msg, verdict=state)
    if not me:
        return _c("lease", UNKNOWN,
                  "no --session-id was given, so whether YOU hold the manager lease "
                  "could not be established — only that somebody might. WE DID NOT LOOK.",
                  verdict=state)
    if state == "held_by_me":
        return _c("lease", PASS, msg, verdict=state)
    return _c("lease", FAIL,
              f"you do not hold the manager lease (state={state}). {msg} Exactly one "
              f"management session runs at a time; managing without the lease is the "
              f"concurrent-manager condition the lease exists to prevent.",
              verdict=state)


# --------------------------------------------------------------------------
# grading
# --------------------------------------------------------------------------
def grade(checks: List[Dict[str, Any]]) -> str:
    """PURE, so the policy is arguable in tests rather than against a live
    manager. FAIL dominates UNKNOWN because a known blocker is a definite
    not-ready; UNKNOWN dominates PASS because 'we could not look' is never a
    clean bill of health."""
    if any(c["state"] == FAIL for c in checks):
        return "not_ready"
    if any(c["state"] == UNKNOWN for c in checks):
        return "unknown"
    return "ready"


def run(observation: Optional[Any] = None, manager_session_id: Optional[str] = None,
        base: str = "origin/main", open_prs: Optional[List[Dict[str, Any]]] = None,
        enforced: Sequence[str] = ("working",),
        threshold: int = DIGEST_THRESHOLD,
        skip_self_test: bool = False) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    if not skip_self_test:
        ok, failures = _self_test(quiet=True)
        if not ok:
            # ⚠️ Machinery that just failed to verify itself does not get to
            # grade a manager. This is the whole reason the suite runs on every
            # invocation rather than behind a flag.
            return {"readiness": "unknown", "checks": [_c(
                "self_test", UNKNOWN,
                f"THE PLANTED-FAILURE SUITE DID NOT PASS ({len(failures)} case(s): "
                f"{'; '.join(failures[:4])}). Every verdict below would be produced by "
                f"machinery whose teeth are unproven, so nothing was graded. Fix the "
                f"tool before trusting it.", failures=failures)]}
        checks.append(_c("self_test", PASS,
                         "the planted-failure suite passed, so every check below has "
                         "been shown THIS RUN to be able to fail."))

    reg, reg_ok = sr.read_json(sr.REGISTRY_PATH)
    ck, ck_ok = sr.read_json(sr.CHECKLIST_PATH)
    caps, caps_ok = read_caps()
    lease, lease_ok = manager_lease.read_lease()
    pings, pings_ok = read_pings()
    obs = sr.normalise_observation(observation) if observation is not None else None

    checks += [
        check_work_has_parent(reg, reg_ok, known_object_ids(), enforced),
        check_session_cap(caps, caps_ok, reg, reg_ok, obs, enforced),
        check_artifact_cap(caps, caps_ok, open_prs),
        check_f6_digest(pings, pings_ok, reg, base, threshold),
        check_subsession_queue(obs, reg, reg_ok, enforced),
        check_bot_authored_head(base),
        check_register_edits(base, open_prs),
        check_blocked_claims(ck, ck_ok),
        check_lease(lease, lease_ok, manager_session_id),
    ]
    return {"readiness": grade(checks), "checks": checks}


_EXIT = {"ready": 0, "not_ready": 3, "unknown": 4}

_ADVICE = {
    "ready": "Every check passed. Proceed.",
    "not_ready": "DO NOT proceed — the FAILING checks above name what each violation "
                 "would COST. Fix them, then re-run.",
    "unknown": "REFUSED — not because something failed, but because something could "
               "not be LOOKED AT. `unknown` is not a soft `ready`: the failures this "
               "tool exists for are invisible from inside, which is exactly why "
               "asserting they are fine is what went wrong. Supply what is missing "
               "and re-run.",
}


# --------------------------------------------------------------------------
# self-test — every check must be shown to FAIL on a planted break
# --------------------------------------------------------------------------
def _self_test(quiet: bool = False) -> Tuple[bool, List[str]]:
    failures: List[str] = []

    def check(label: str, got: Any, want: Any) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r} want {want!r}")
        if not quiet:
            print(f"  self-test ({label}): "
                  f"{'PASS' if got == want else f'FAIL got={got!r} want={want!r}'}")

    P, F, U = {"state": PASS}, {"state": FAIL}, {"state": UNKNOWN}
    check("all pass -> ready", grade([P, P]), "ready")
    check("any FAIL -> not_ready", grade([P, F]), "not_ready")
    check("any UNKNOWN, no fail -> unknown, NEVER ready", grade([P, U]), "unknown")
    check("FAIL dominates UNKNOWN", grade([U, F]), "not_ready")

    # 1 · work_has_parent
    objs = {"WO-REAL"}
    reg_ok_doc = {"sessions": [{"session_id": "s1", "state": "working",
                                "owns_object": "WO-REAL"}]}
    check("a live session with a real parent PASSES",
          check_work_has_parent(reg_ok_doc, True, objs, ("working",))["state"], PASS)
    check("A LIVE SESSION WITH NO PARENT FAILS — the orphan-task rule",
          check_work_has_parent({"sessions": [{"session_id": "s1", "state": "working"}]},
                                True, objs, ("working",))["state"], FAIL)
    check("a parent that names a NONEXISTENT object FAILS too",
          check_work_has_parent({"sessions": [{"session_id": "s1", "state": "working",
                                               "owns_object": "WO-GHOST"}]},
                                True, objs, ("working",))["state"], FAIL)
    check("an unreadable registry is UNKNOWN, not a pass",
          check_work_has_parent(None, False, objs, ("working",))["state"], UNKNOWN)
    check("an unlistable objects dir is UNKNOWN, not a pass",
          check_work_has_parent(reg_ok_doc, True, None, ("working",))["state"], UNKNOWN)
    check("NO live rows is UNKNOWN — 'nothing graded' is not 'all have parents'",
          check_work_has_parent({"sessions": []}, True, objs, ("working",))["state"],
          UNKNOWN)

    # 2 · session_cap — the interval logic is the load-bearing part
    caps3 = {"caps": {"sessions": {"limit": 3}}}
    caps_contested = {"caps": {"sessions": {"contested": [{"value": 3}, {"value": 4}]}}}
    reg4 = {"sessions": [{"session_id": f"s{i}", "state": "working"} for i in range(4)]}
    obs4 = [{"session_id": f"s{i}", "status": "running"} for i in range(4)]
    obs2 = [{"session_id": f"s{i}", "status": "running"} for i in range(2)]
    check("over EVERY candidate limit FAILS",
          check_session_cap(caps3, True, reg4, True, obs4, ("working",))["state"], FAIL)
    check("under EVERY candidate limit PASSES",
          check_session_cap(caps3, True, reg4, True, obs2, ("working",))["state"], PASS)
    check("IN THE CONTESTED BAND IT REFUSES rather than picking a limit",
          check_session_cap(caps_contested, True, reg4, True, obs4,
                            ("working",))["state"], UNKNOWN)
    check("...but over BOTH contested values it is still a definite FAIL",
          check_session_cap(caps_contested, True,
                            {"sessions": [{"session_id": f"s{i}", "state": "working"}
                                          for i in range(9)]}, True,
                            [{"session_id": f"s{i}", "status": "running"}
                             for i in range(9)], ("working",))["state"], FAIL)
    check("a TERMINAL status does not count toward the cap",
          check_session_cap(caps3, True, reg4, True,
                            [{"session_id": f"s{i}", "status": "archived"}
                             for i in range(4)], ("working",))["state"], PASS)
    check("NO observation is UNKNOWN — the registry alone is a claim",
          check_session_cap(caps3, True, reg4, True, None, ("working",))["state"], UNKNOWN)
    check("an unreadable caps file is UNKNOWN (no hardcoded cap exists)",
          check_session_cap(None, False, reg4, True, obs4, ("working",))["state"], UNKNOWN)
    check("a caps file declaring NO limit is UNKNOWN",
          check_session_cap({"caps": {"sessions": {}}}, True, reg4, True, obs4,
                            ("working",))["state"], UNKNOWN)
    check("an UNCLASSIFIABLE status widens the interval into a refusal",
          check_session_cap(caps3, True,
                            {"sessions": [{"session_id": f"s{i}", "state": "working"}
                                          for i in range(4)]}, True,
                            [{"session_id": "s0"}, {"session_id": "s1"},
                             {"session_id": "s2", "status": "running"},
                             {"session_id": "s3", "status": "running"}],
                            ("working",))["state"], UNKNOWN)

    # 3 · artifact_cap — refusing while undefined is the DESIGNED behaviour
    prs = [{"number": 1, "draft": False, "title": "a", "head": {"ref": "claude/x"}},
           {"number": 2, "draft": True, "title": "[DO NOT MERGE] b",
            "head": {"ref": "claude/y"}},
           {"number": 3, "draft": False, "title": "c",
            "head": {"ref": "automation/z"}}]
    check("AN UNDEFINED POPULATION REFUSES, even with a limit and a live list",
          check_artifact_cap({"caps": {"artifacts": {"limit": 4, "definition": None}}},
                             True, prs)["state"], UNKNOWN)
    check("...and it still refuses with no list at all",
          check_artifact_cap({"caps": {"artifacts": {"limit": 4, "definition": None}}},
                             True, None)["state"], UNKNOWN)
    check("a DEFINED population under the limit PASSES",
          check_artifact_cap({"caps": {"artifacts": {
              "limit": 4, "definition": "open_non_draft_excluding_both"}}},
              True, prs)["state"], PASS)
    check("a DEFINED population over the limit FAILS",
          check_artifact_cap({"caps": {"artifacts": {
              "limit": 1, "definition": "all_open_prs"}}}, True, prs)["state"], FAIL)
    check("a population name this tool cannot compute REFUSES, never substitutes",
          check_artifact_cap({"caps": {"artifacts": {
              "limit": 4, "definition": "vibes"}}}, True, prs)["state"], UNKNOWN)
    check("candidate populations are COMPUTED, not asserted (DO-NOT-MERGE excluded)",
          artifact_candidates(prs)["excluding_do_not_merge"], 2)
    check("...and automation branches excluded on top of that",
          artifact_candidates(prs)["excluding_do_not_merge_and_automation"], 1)

    # 4 · f6_digest_owed
    old = "2020-01-01T00:00:00+00:00"
    future = "2999-01-01T00:00:00+00:00"
    reg_spawns = {"sessions": [{"session_id": f"s{i}", "spawned_at": "2100-01-01T00:00:00Z"}
                               for i in range(9)]}
    check("MANY ACTIONS SINCE THE NEWEST PING FAILS — F6 is the autonomy condition",
          check_f6_digest([{"at": old}], True, reg_spawns, "HEAD", 3)["state"], FAIL)
    check("a fresh ping returns the count to zero and PASSES",
          check_f6_digest([{"at": future}], True, reg_spawns, "HEAD", 3)["state"], PASS)
    check("unreadable pings are UNKNOWN — not 'no digest is owed'",
          check_f6_digest(None, False, reg_spawns, "HEAD", 3)["state"], UNKNOWN)
    check("pings with no parseable `at` are UNKNOWN",
          check_f6_digest([{"message": "hi"}], True, reg_spawns, "HEAD", 3)["state"],
          UNKNOWN)
    check("an unresolvable base ref is UNKNOWN, not a pass",
          check_f6_digest([{"at": old}], True, None,
                          "refs/nope/definitely-not-a-ref", 3)["state"], UNKNOWN)
    check("both timestamp spellings parse (Z and +00:00)",
          [_parse_ts("2026-09-02T11:51:40Z") is not None,
           _parse_ts("2026-09-02T00:19:53.578570+00:00") is not None], [True, True])
    # ⚠️ REGRESSION PIN. `git log --since=2999-01-01T00:00:00+00:00` silently
    # returns EVERY commit (measured: 2263 of 2263, git 2.43.0) because the date
    # overflows git's parser and the filter is dropped with no error, while
    # --since=2099 correctly returns 0. Reverting to git-side date filtering
    # would make every commit in history read as "since the last ping". This
    # asserts the count is taken in Python, where no date string reaches git.
    check("A FAR-FUTURE CUTOFF COUNTS ZERO COMMITS — git's --since would return ALL",
          count_autonomous_actions(_parse_ts("2999-01-01T00:00:00+00:00"), None,
                                   "HEAD")[0], 0)
    check("...while a 2020 cutoff still counts this repo's whole history, so the "
          "assertion above is not vacuously zero",
          count_autonomous_actions(_parse_ts("2020-01-01T00:00:00+00:00"), None,
                                   "HEAD")[0] > 100, True)

    # 5 · subsession_queue
    reg1 = {"sessions": [{"session_id": "s1", "state": "working"}]}
    check("A SUB-SESSION WAITING ON THE MANAGER FAILS",
          check_subsession_queue([{"session_id": "s1", "status": "review_ready"}],
                                 reg1, True, ("working",))["state"], FAIL)
    check("needs_action inside post_turn_summary is caught too",
          check_subsession_queue([{"session_id": "s1", "status": "running",
                                   "post_turn_summary": {"needs_action": True}}],
                                 reg1, True, ("working",))["state"], FAIL)
    check("a plainly running session PASSES",
          check_subsession_queue([{"session_id": "s1", "status": "running"}],
                                 reg1, True, ("working",))["state"], PASS)
    # ⚠️ THE TWO POPULATIONS MUST NOT POOL. Measured 2026-09-02 they were BOTH
    # 16 and a pooled count reported one number for two different facts with two
    # different remedies. These four assertions are what keep them apart.
    _idle = check_subsession_queue([{"session_id": "s1", "status": "session_status_idle"}],
                                   reg1, True, ("working",))
    check("an IDLE row the registry calls working FAILS...", _idle["state"], FAIL)
    check("...as a STALLED row, never as an explicit hand-back",
          [len(_idle["stalled"]), len(_idle["waiting"])], [1, 0])
    _wait = check_subsession_queue(
        [{"session_id": "s1", "status": "session_status_bucket_review_ready"}],
        reg1, True, ("working",))
    check("an explicit hand-back FAILS as WAITING, never as stalled",
          [len(_wait["waiting"]), len(_wait["stalled"])], [1, 0])
    check("an ARCHIVED row the registry still calls working is stalled-and-terminal",
          check_subsession_queue([{"session_id": "s1", "status": "session_status_archived"}],
                                 reg1, True, ("working",))["stalled"][0]["terminal"], True)
    check("NO observation is UNKNOWN — 'I did not poll' is the measured failure",
          check_subsession_queue(None, reg1, True, ("working",))["state"], UNKNOWN)
    check("an observation carrying NO status is UNKNOWN, not 'nobody is blocked'",
          check_subsession_queue([{"session_id": "s1"}], reg1, True,
                                 ("working",))["state"], UNKNOWN)
    check("a session that is not this manager's is ignored, so grading is empty -> UNKNOWN",
          check_subsession_queue([{"session_id": "other", "status": "review_ready"}],
                                 reg1, True, ("working",))["state"], UNKNOWN)

    # 6 · bot_authored_head
    check("a [bot] author is recognised", is_bot_author("github-actions[bot]", ""), True)
    check("...by email too",
          is_bot_author("x", "41898282+github-actions[bot]@users.noreply.github.com"),
          True)
    check("a human author is not", is_bot_author("Ben", "ben@example.com"), False)
    check("a name merely CONTAINING 'bot' is not a bot",
          is_bot_author("Robot Roberts", "robert@example.com"), False)
    check("an unresolvable base ref is UNKNOWN, not a pass",
          check_bot_authored_head("refs/nope/definitely-not-a-ref")["state"], UNKNOWN)

    # 7 · register_edits
    check("OPEN-ITEMS.json does NOT round-trip — computed, not quoted from a doc",
          json_round_trips(REPO_ROOT / "docs/claude/OPEN-ITEMS.json"), False)
    check("...while SESSIONS.json does, so the finding is specific and not vacuous",
          json_round_trips(REPO_ROOT / "docs/claude/work/SESSIONS.json"), True)
    check("a missing file round-trips as UNKNOWN (None), never as True",
          json_round_trips(REPO_ROOT / "docs/claude/work/NO-SUCH-FILE.json"), None)

    # 8 · blocked_claims
    check("BLOCKED ON A BARE-STRING CLAIM WITH NO DATE FAILS",
          check_blocked_claims({"items": [{"id": "X", "state": "blocked",
                                           "blocked_on": "operator says the key is unset"}]},
                               True)["state"], FAIL)
    check("blocked on an external event with NO dated probe FAILS",
          check_blocked_claims({"items": [{"id": "X", "state": "blocked", "blocked_on": {
              "kind": "external_event", "ref": "the secret is unset"}}]},
              True)["state"], FAIL)
    check("...but WITH a dated basis it PASSES",
          check_blocked_claims({"items": [{"id": "X", "state": "blocked",
                                           "blocked_on_basis": "ASSESSED 2026-09-02 by probe",
                                           "blocked_on": {"kind": "external_event",
                                                          "ref": "the secret is unset"}}]},
              True)["state"], PASS)
    check("an INTERNAL edge needs no live probe — its target is checkable in-repo",
          check_blocked_claims({"items": [{"id": "X", "state": "blocked", "blocked_on": {
              "kind": "object", "ref": "WO-Y"}}]}, True)["state"], PASS)
    check("an unreadable checklist is UNKNOWN, not a pass",
          check_blocked_claims(None, False)["state"], UNKNOWN)
    check("all three real blocked_on SHAPES normalise (list, dict, bare string)",
          [len(_norm_edges([{"kind": "object"}])), len(_norm_edges({"kind": "object"})),
           len(_norm_edges("a string"))], [1, 1, 1])

    # 9 · lease
    mine = {"state": "held", "holder": "S1",
            "heartbeat_at": manager_lease._iso(manager_lease._now())}
    check("holding the lease PASSES", check_lease(mine, True, "S1")["state"], PASS)
    check("SOMEONE ELSE HOLDING IT FAILS", check_lease(mine, True, "S2")["state"], FAIL)
    check("an unreadable lease is UNKNOWN, never a pass",
          check_lease(None, False, "S1")["state"], UNKNOWN)
    check("no --session-id cannot establish that YOU hold it -> UNKNOWN",
          check_lease(mine, True, None)["state"], UNKNOWN)

    if not quiet:
        print("manager-preflight self-test:", "PASS" if not failures else "FAIL")
    return (not failures), failures


def _load_json_arg(spec: Optional[str]) -> Optional[Any]:
    if not spec:
        return None
    text = sys.stdin.read() if spec == "-" else Path(spec).read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        blob = sr._outermost_json(text)
        return json.loads(blob) if blob else None


def _normalise_prs(raw: Any) -> Optional[List[Dict[str, Any]]]:
    """Keep the PR ROWS (not just numbers) — the artifact-cap candidates need
    `draft`, `title` and the head ref to tell the populations apart."""
    if raw is None:
        return None
    for key in ("pull_requests", "data", "items", "results"):
        if isinstance(raw, dict) and isinstance(raw.get(key), list):
            raw = raw[key]
            break
    if not isinstance(raw, list):
        return None
    return [r for r in raw if isinstance(r, dict) and r.get("number")]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true",
                    help="run the planted-failure suite alone and exit. It ALSO runs on "
                         "every ordinary invocation — this flag only runs it by itself.")
    ap.add_argument("--session-id", default=None,
                    help="the MANAGER's session id (needed for the lease check)")
    ap.add_argument("--live-sessions", default=None,
                    help="path to `list_sessions`/`get_session` output, or '-' for "
                         "stdin. WITHOUT IT the verdict can never be `ready`.")
    ap.add_argument("--open-prs", default=None,
                    help="live open pull-request rows (JSON from list_pull_requests). "
                         "Needed for the artifact-cap candidates and the register "
                         "conflict surface.")
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--enforce-states", default="working",
                    help="CSV of SESSIONS.json states counted as live (default: working)")
    ap.add_argument("--digest-threshold", type=int, default=DIGEST_THRESHOLD)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        ok, _ = _self_test()
        return 0 if ok else 1

    res = run(observation=_load_json_arg(a.live_sessions),
              manager_session_id=a.session_id, base=a.base,
              open_prs=_normalise_prs(_load_json_arg(a.open_prs)),
              enforced=tuple(s.strip() for s in a.enforce_states.split(",") if s.strip()),
              threshold=a.digest_threshold)
    for c in res["checks"]:
        icon = {PASS: "PASS", FAIL: "FAIL", UNKNOWN: "????"}[c["state"]]
        print(f"manager-preflight: [{icon}] {c['check']}: {c['message']}")
    print(f"manager-preflight: readiness={res['readiness']}")
    print(f"manager-preflight: {_ADVICE[res['readiness']]}")
    if a.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    return _EXIT[res["readiness"]]


if __name__ == "__main__":
    raise SystemExit(main())
