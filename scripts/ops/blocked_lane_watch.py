#!/usr/bin/env python3
#
# wiring: fired by `.github/workflows/session-reaper.yml` (cron `17 */3 * * *`)
# and by `--self-test` in that workflow's pre-flight. It is deliberately ALSO
# runnable by hand, which `pr_queue_latency.py` is not -- see "WHY THIS ONE MAY
# BE INVOKED BY A SESSION" below. Its receipt is
# `docs/claude/work/BLOCKED-LANE-WATCH.json`.
"""BLOCKED-LANE WATCH -- has the thing a blocked lane is waiting for HAPPENED?

MI-235. Three lanes, measured, each freed only because a manager happened to
read the roster by hand:

    MI-222  asked 2026-09-09T17:53:18Z "close PR #11579"
            #11579 was closed 19:41:11Z, ~2h later      -> blocked 13 HOURS
    MI-139  asked 2026-09-06T19:10:07Z "disposition #11105"
            #11105 was closed 10:22:01Z, 9h BEFORE it asked -> blocked 3.5 DAYS
    MI-238  went idle 2026-09-11T03:56:52Z, its own post_turn_summary reading
            "#215 awaiting merge"; merged by the manager 04:29Z -> 33 MINUTES

The third is the mildest and the most damning: the lane announced its blocker in
a MACHINE-READABLE field and nothing read it.

────────────────────────────────────────────────────────────────────────────
THE FINDING UNDER THE FINDING -- WHY THIS COULD NOT JUST BE A CADENCE
────────────────────────────────────────────────────────────────────────────
The obvious build is "grade `SESSIONS.json::needs_action` on a clock". It was
MEASURED FIRST, and the measurement is why this module starts one step earlier.

POPULATION: all 209 rows of `docs/claude/work/SESSIONS.json` at `main`
(`updated_at 2026-09-11T04:32:00Z`), read 2026-09-11T05:3xZ.

    rows carrying `needs_action` ................................  4  (1.9%)
      of those, written after 2026-09-05 ........................  0
      of those, whose session is already `archived` .............  2
    rows declaring a blocker in a TYPED, resolvable field .......  0

⚠️ **AND NOT ONE OF THE THREE INCIDENTS USED THAT FIELD.** All three declared
their blocker to the PLATFORM (`status_bucket: BLOCKED` plus
`post_turn_summary.needs_action`), which a manager then transcribed into
`observation` PROSE by hand. A resolver reading `needs_action` on a clock would
have graded **0 of 3**. The repo-side field is not where the signal lives.

So the blocker has to become a DECLARED TYPED EDGE before any cadence has
anything to grade, and that half lives in
`session_registry.py blocked-on` -- which REFUSES a kind this module cannot
resolve, at WRITE time. That refusal is load-bearing: it is why
`could_not_look` below can only ever mean *we tried and failed*, never *there
was never a resolver for this*. Two opposite findings with opposite remedies
(a resolver gap vs. an author gap) would otherwise share one value, which is
precisely the collapse `src/runtime/decision_subject.py` was built to refuse.

────────────────────────────────────────────────────────────────────────────
WHY THE PROSE IS NOT SCRAPED
────────────────────────────────────────────────────────────────────────────
MI-258 built exactly that scraper as a probe and MEASURED it before shipping
any of it: over all 26 `decision_requests` in the work store it flagged 4
vanished subjects and **3 were FALSE (75%)** -- a YAML line-wrap truncating an
id mid-token, and two sentence-ending periods captured into a path.

⚠️ THE FAILURE DIRECTION HERE IS THE OPPOSITE OF MI-258's, AND IT STILL DOES
NOT LICENSE SCRAPING. There, a false positive HIDES a decision. Here, a false
positive wakes a lane that is still blocked -- it costs one turn, while a false
negative costs the days measured above, so this module FAILS TOWARD WAKING
throughout. But a 75%-wrong signal would not be failing toward waking, it would
be noise, and an alarm that is mostly wrong is walked past -- the desensitised
alarm this repo has already promoted to a P1 in its own right. A declared edge
or nothing.

────────────────────────────────────────────────────────────────────────────
THE FOUR STATES, AND WHY NONE COLLAPSES INTO ANOTHER
────────────────────────────────────────────────────────────────────────────
``cleared``          the declared condition is SATISFIED. The lane is free and
                     does not know it. This is the only state that pages.
``still_blocking``   resolved, and the condition does NOT hold. The ordinary,
                     correct, quiet case -- a WARN here would be the
                     desensitised-alarm P1.
``could_not_look``   a declared blocker whose resolver RAN AND FAILED (an
                     unreadable object, a cross-repo PR with no state file, an
                     ambiguous ref). *We did not look.* ⚠️ IT PAGES, and that
                     is deliberate: an ungradeable blocker is a lane nothing can
                     free, which is MI-235 itself one level up. Reading it as
                     `still_blocking` would make the sensor's own blindness
                     indistinguishable from a lane that is legitimately waiting.
``undeclared``       nobody wrote a blocker down. ⚠️ DELIBERATELY NOT
                     `could_not_look`: "we tried and could not" and "there is
                     nothing here to check" are opposite findings with opposite
                     remedies. It is the state of 100% of rows on the day this
                     shipped, so pooling it into `could_not_look` would page on
                     every row from the first run and get the alarm disabled
                     inside a day. It is COUNTED and never paged.

They are branched on and not merely counted: `cleared` and `could_not_look` are
the only states that reach the wake list, `still_blocking` is the only one that
advances nothing, and `undeclared` is the only one reported as an AUTHOR gap.

────────────────────────────────────────────────────────────────────────────
WHAT THIS DOES NOT DO
────────────────────────────────────────────────────────────────────────────
⚠️ **IT DOES NOT WAKE THE LANE, AND SAYING SO IS THE POINT.** The wake hop is
`create_trigger` + `fire_trigger` -- ``mcp__*`` tools **CI does not hold**, and
the operator ruled out minted credentials for CI on 2026-09-02 ("no minted
tokens, ever"), so no workflow can ever perform it. That channel already exists
and is already proven: MI-123's poke-only Routine woke a manager out-of-band at
2026-09-04T23:09:19Z. What failed in all three incidents was NOTICING, not
poking -- MI-235's own `why_it_matters` says so in terms. This is the noticer.

⚠️ **THEREFORE READ THE EXIT CODE, NOT THE RECEIPT'S EXISTENCE.** A run that
writes a clean receipt and exits 0 means every declared blocker still holds. It
does NOT mean no lane is stuck: a lane that declared nothing is `undeclared` and
this module has no opinion about it at all.

⚠️ **IT NEVER MERGES, CLOSES, COMMENTS ON, POKES OR ARCHIVES ANYTHING.** It
reads `SESSIONS.json`, `origin`'s refs, `origin/main`'s history, the work store,
and an optional PR-state file. Tier-1: no order path, no strategy config, no
risk caps, no VM.

────────────────────────────────────────────────────────────────────────────
WHY THIS ONE MAY BE INVOKED BY A SESSION
────────────────────────────────────────────────────────────────────────────
`pr_queue_latency.py` is deliberately unreachable from a prompt, because it
checks THE MANAGER and "a check invoked by the actor it checks cannot catch that
actor failing". That argument does not transfer: this module checks the WORLD on
behalf of a lane, not the lane, so a blocked lane running it on itself is the
cheapest possible wake -- it needs no manager and no MCP at all. The cadence is
the FLOOR for lanes that cannot or did not, not the only route.

────────────────────────────────────────────────────────────────────────────
THE LATCH
────────────────────────────────────────────────────────────────────────────
The receipt is the latch, and it is DURABLE (committed to `main`) because the
condition outlives any process -- the same correction
`BL-20260823-TARGET-NAKED-COOLDOWN-RESETS-ON-EVERY-RESTART` forced when a
per-process `time.monotonic()` latch put 202 of 376 CRITICALs on the operator's
channel. A blocker pages on the run that first grades it `cleared`, and not
again. ⚠️ If the receipt fails to land, the latch does not advance and the next
run pages again -- failing toward waking, deliberately.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = REPO_ROOT / "docs" / "claude" / "work" / "SESSIONS.json"
OBJECTS_DIR = REPO_ROOT / "docs" / "claude" / "work" / "objects"
RECEIPT_PATH = REPO_ROOT / "docs" / "claude" / "work" / "BLOCKED-LANE-WATCH.json"

# ── the four states, as named constants ──────────────────────────────────────
# Named rather than inlined so `collapsed-state-guard`'s consumer scan binds to
# THIS contract's own tokens and not to the bare English words "cleared" and
# "undeclared", which appear across dozens of unrelated modules.
BLOCKER_CLEARED = "cleared"
BLOCKER_STILL_BLOCKING = "still_blocking"
BLOCKER_COULD_NOT_LOOK = "could_not_look"
BLOCKER_UNDECLARED = "undeclared"

ALL_BLOCKER_STATES = (
    BLOCKER_CLEARED,
    BLOCKER_STILL_BLOCKING,
    BLOCKER_COULD_NOT_LOOK,
    BLOCKER_UNDECLARED,
)

# ── the closed vocabulary of blocker kinds, and what may clear each ──────────
# ⚠️ THIS TABLE IS THE WRITE-TIME CONTRACT TOO. `session_registry.py blocked-on`
# imports it and REFUSES anything not in it, which is what guarantees that a
# `could_not_look` at read time always means "the resolver failed" and never
# "there was never a resolver". Adding a kind here without adding its resolver
# below is caught by `--self-test`.
CLEARS_WHEN_BY_KIND: Dict[str, Tuple[str, ...]] = {
    "pull_request": ("merged", "closed_or_merged"),
    "branch": ("deleted", "exists"),
    "path_on_main": ("exists", "deleted"),
    "work_object": ("done_or_accepted",),
    "operator_decision": ("answered",),
}

# Why a resolver could not answer. A closed vocabulary so a receipt reader can
# tell a transient read failure from a structural one without parsing prose.
REASON_CROSS_REPO_NO_STATE = "cross_repo_pr_no_state_file"
REASON_PR_STATE_UNREADABLE = "pr_state_unreadable"
REASON_GIT_READ_FAILED = "git_read_failed"
REASON_OBJECT_UNREADABLE = "work_object_unreadable"
REASON_AMBIGUOUS_REF = "ambiguous_ref"
REASON_MALFORMED = "malformed_declaration"

# A row in one of these lifecycle states is not waiting on anything; its
# declaration is history. Filtered OUT of the graded population rather than
# graded, and counted, so "we skipped it" never reads as "it was fine".
TERMINAL_ROW_STATES = frozenset(
    {"archived", "completed", "done", "accepted", "failed", "failed_wrong_repo"}
)

# Row states that mean "this lane is waiting". Deliberately PERMISSIVE: a row
# carrying a declared blocker is graded whatever its state word, because the
# state vocabulary in this register is not closed (12 distinct values measured
# 2026-09-11) and a lane whose state word we do not recognise is exactly the
# lane most likely to be forgotten. Only TERMINAL_ROW_STATES filters anything.
_PR_REF = re.compile(r"^(?:(?P<repo>[A-Za-z0-9._-]+/[A-Za-z0-9._-]+)#|#?)(?P<num>\d+)$")


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─────────────────────────────────────────────────────────────────────────────
# THE WORLD -- every fact a resolver may consult, gathered ONCE.
#
# Passed in rather than read per-blocker so the grading policy below is a PURE
# FUNCTION and is therefore arguable in tests rather than against a live roster.
# The lesson of that is a policy which could only be exercised live cancelled
# the one leg that matched the journal; see
# BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG.
# (⚠️ That id is kept on ONE line deliberately. The first draft wrapped it after
# `...-CANCELLED-THE-` and artifact-validity-guard correctly graded the fragment
# as a reference resolving to NOTHING — the exact line-wrap truncation MI-258
# measured as a source of its 75% false-positive rate, reproduced here in a
# Python comment within an hour of citing it.)
# ─────────────────────────────────────────────────────────────────────────────
def _git(*args: str) -> Tuple[bool, str]:
    """Run a read-only git command. Returns (ok, output) -- NEVER raises, and
    never collapses a failure into an empty string: the caller must be able to
    tell "no match" from "we could not look"."""
    try:
        out = subprocess.run(
            ("git",) + args, cwd=str(REPO_ROOT), capture_output=True, text=True,
            timeout=60, check=False,
        )
    except Exception as exc:  # pragma: no cover - defensive
        return False, str(exc)
    if out.returncode != 0:
        return False, (out.stderr or "").strip()
    return True, out.stdout


def collect_world(*, this_repo: str, pr_state_path: Optional[Path] = None,
                  git_ok: bool = True) -> Dict[str, Any]:
    """Gather origin's refs, main's merged-PR numbers, and any supplied PR state.

    ⚠️ `readable` flags are carried per-source rather than one global boolean,
    because a run that could read branches but not the PR list must grade PR
    blockers `could_not_look` while still grading branch blockers honestly.
    Collapsing them would make one failed read blind the whole sweep.
    """
    world: Dict[str, Any] = {
        "this_repo": this_repo,
        "branches": set(),
        "branches_readable": False,
        "merged_prs": set(),
        "merged_prs_readable": False,
        "pr_states": {},          # "owner/repo#N" -> "open" | "closed" | "merged"
        "pr_states_readable": False,
        # ⚠️ WHICH REPOS THE LISTING ACTUALLY COVERS. Load-bearing, and the
        # self-test caught its absence: "absent from the open-PR listing" only
        # implies "not open" for a repo the listing enumerates. A cross-repo PR
        # is absent from a single-repo listing BY CONSTRUCTION, so without this
        # every foreign PR graded `cleared` and would have woken a lane on a PR
        # nobody looked at -- the unasserted-denominator class, in the one
        # direction this module is not allowed to be sloppy about.
        "pr_states_repos": set(),
        "paths_readable": False,
    }
    if git_ok:
        ok, out = _git("for-each-ref", "--format=%(refname:short)", "refs/remotes/origin")
        if ok:
            world["branches"] = {
                line.strip()[len("origin/"):]
                for line in out.splitlines()
                if line.strip().startswith("origin/")
            }
            world["branches_readable"] = True
        # `(#N)` is the squash-merge subject GitHub writes. Bounded depth: this
        # runs on a cron, and an unbounded `git log` over a repo this size is
        # the kind of unbudgeted cost that wedges a job.
        ok, out = _git("log", "--oneline", "-4000", "origin/main")
        if ok:
            world["merged_prs"] = set(re.findall(r"\(#(\d+)\)", out))
            world["merged_prs_readable"] = True
        ok, _ = _git("cat-file", "-e", "origin/main:docs/claude/work/SESSIONS.json")
        world["paths_readable"] = ok

    if pr_state_path is not None:
        try:
            raw = json.loads(Path(pr_state_path).read_text(encoding="utf-8"))
        except Exception:
            # ⚠️ NOT `{}`. A `curl ... || echo '{}'` fallback here would turn a
            # 403 into "no PR is open", which is a named failure class in this
            # repo -- the shape that made a CI watcher report TIMEOUT having
            # checked nothing.
            world["pr_states_readable"] = False
        else:
            states: Dict[str, str] = {}
            # `--pr-state` is DOCUMENTED as a listing of open PRs for
            # `--this-repo`; a row may name another repo explicitly to widen it.
            covered = {this_repo}
            rows = raw if isinstance(raw, list) else raw.get("pull_requests", [])
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    num = row.get("number")
                    repo = row.get("repo") or this_repo
                    if num is None:
                        continue
                    if row.get("merged_at") or row.get("merged"):
                        state = "merged"
                    else:
                        state = str(row.get("state") or "open").lower()
                    states[f"{repo}#{num}"] = state
                    covered.add(repo)
                world["pr_states"] = states
                world["pr_states_repos"] = covered
                world["pr_states_readable"] = True
    return world


def _path_exists_on_main(path: str, world: Dict[str, Any]) -> Optional[bool]:
    """True / False / None -- None is *we could not look*, never False."""
    if not world.get("paths_readable"):
        return None
    ok, _ = _git("cat-file", "-e", f"origin/main:{path}")
    if ok:
        return True
    # Distinguish "absent" from "git itself is broken" by re-asserting the
    # control we already know resolves. Without this, a git failure reads as a
    # deleted file -- which would grade `deleted` as CLEARED and wake a lane on
    # the strength of a broken read.
    control_ok, _ = _git("cat-file", "-e", "origin/main:docs/claude/work/SESSIONS.json")
    return False if control_ok else None


def _read_object(ref: str) -> Optional[Dict[str, Any]]:
    """Load a work object by id. None means unreadable (never 'empty')."""
    name = ref if ref.endswith(".yaml") else f"{ref}.yaml"
    candidate = OBJECTS_DIR / name
    try:
        text = candidate.read_text(encoding="utf-8")
    except Exception:
        return None
    try:
        import yaml  # type: ignore
    except Exception:
        return None
    try:
        doc = yaml.safe_load(text)
    except Exception:
        return None
    return doc if isinstance(doc, dict) else None


# ─────────────────────────────────────────────────────────────────────────────
# THE POLICY -- a pure function over (blocker, world).
# ─────────────────────────────────────────────────────────────────────────────
def grade_blocker(blocker: Any, world: Dict[str, Any],
                  *, object_reader=_read_object,
                  path_checker=_path_exists_on_main) -> Dict[str, Any]:
    """Grade ONE declared blocker into exactly one of the four states."""
    if not isinstance(blocker, dict):
        return {"state": BLOCKER_COULD_NOT_LOOK, "reason": REASON_MALFORMED,
                "why": "blocker is not an object"}
    kind = blocker.get("kind")
    ref = blocker.get("ref")
    clears_when = blocker.get("clears_when")
    if kind not in CLEARS_WHEN_BY_KIND or not isinstance(ref, str) or not ref.strip():
        return {"state": BLOCKER_COULD_NOT_LOOK, "reason": REASON_MALFORMED,
                "kind": kind, "ref": ref,
                "why": f"unknown kind {kind!r} or empty ref"}
    if clears_when not in CLEARS_WHEN_BY_KIND[kind]:
        return {"state": BLOCKER_COULD_NOT_LOOK, "reason": REASON_MALFORMED,
                "kind": kind, "ref": ref, "clears_when": clears_when,
                "why": (f"clears_when {clears_when!r} is not valid for kind "
                        f"{kind!r}; allowed: {CLEARS_WHEN_BY_KIND[kind]}")}
    ref = ref.strip()
    base = {"kind": kind, "ref": ref, "clears_when": clears_when}

    if kind == "pull_request":
        return {**base, **_grade_pr(ref, clears_when, world)}
    if kind == "branch":
        return {**base, **_grade_branch(ref, clears_when, world)}
    if kind == "path_on_main":
        return {**base, **_grade_path(ref, clears_when, world, path_checker)}
    if kind == "work_object":
        return {**base, **_grade_object(ref, object_reader)}
    if kind == "operator_decision":
        return {**base, **_grade_decision(ref, object_reader)}
    # Unreachable while CLEARS_WHEN_BY_KIND and this dispatch agree -- asserted
    # by `--self-test`, so a kind added to the table without a resolver fails
    # loudly here rather than silently grading every such blocker `could_not_look`.
    raise AssertionError(f"no resolver for declared kind {kind!r}")  # pragma: no cover


def _grade_pr(ref: str, clears_when: str, world: Dict[str, Any]) -> Dict[str, Any]:
    m = _PR_REF.match(ref)
    if not m:
        return {"state": BLOCKER_COULD_NOT_LOOK, "reason": REASON_AMBIGUOUS_REF,
                "why": f"{ref!r} is not '#N' or 'owner/repo#N'"}
    repo = m.group("repo") or world["this_repo"]
    num = m.group("num")
    key = f"{repo}#{num}"
    same_repo = repo == world["this_repo"]

    # A merged PR is settled from `origin/main`'s own history and needs no API.
    # ⚠️ ONLY for THIS repo: a `(#215)` subject on our main says nothing about
    # ict-trader-dashboard#215, and matching it would be a cross-repo number
    # collision that wakes a lane on another repo's PR.
    if same_repo and world.get("merged_prs_readable") and num in world["merged_prs"]:
        return {"state": BLOCKER_CLEARED, "why": f"#{num} is in origin/main's history"}

    state = world.get("pr_states", {}).get(key)
    if state is None:
        covered = repo in world.get("pr_states_repos", set())
        if not world.get("pr_states_readable") or not covered:
            return {"state": BLOCKER_COULD_NOT_LOOK,
                    "reason": (REASON_CROSS_REPO_NO_STATE if not same_repo
                               else REASON_PR_STATE_UNREADABLE),
                    "why": (f"no PR-state listing covers {repo}; "
                            "merged-into-main is the only offline signal here "
                            "and it does not apply across repositories")}
        # The listing WAS readable, it covers this repo, and this PR is not in
        # it. For an OPEN-PR listing that means "not open" -> closed or merged.
        # ⚠️ Sound ONLY because the listing enumerates `repo`; that is exactly
        # what `covered` above establishes.
        if clears_when in ("closed_or_merged",):
            return {"state": BLOCKER_CLEARED,
                    "why": f"{key} is absent from the open-PR listing"}
        return {"state": BLOCKER_COULD_NOT_LOOK, "reason": REASON_PR_STATE_UNREADABLE,
                "why": (f"{key} is absent from the open-PR listing, which "
                        "distinguishes closed from merged not at all, and "
                        "clears_when=merged needs that distinction")}

    if state == "open":
        return {"state": BLOCKER_STILL_BLOCKING, "why": f"{key} is open"}
    if state == "merged":
        return {"state": BLOCKER_CLEARED, "why": f"{key} is merged"}
    if state == "closed":
        if clears_when == "closed_or_merged":
            return {"state": BLOCKER_CLEARED, "why": f"{key} is closed"}
        return {"state": BLOCKER_STILL_BLOCKING,
                "why": f"{key} is closed UNMERGED and clears_when=merged"}
    return {"state": BLOCKER_COULD_NOT_LOOK, "reason": REASON_AMBIGUOUS_REF,
            "why": f"unrecognised PR state {state!r} for {key}"}


def _grade_branch(ref: str, clears_when: str, world: Dict[str, Any]) -> Dict[str, Any]:
    if not world.get("branches_readable"):
        return {"state": BLOCKER_COULD_NOT_LOOK, "reason": REASON_GIT_READ_FAILED,
                "why": "origin's branch list could not be read"}
    present = ref in world["branches"]
    if clears_when == "deleted":
        return ({"state": BLOCKER_CLEARED, "why": f"origin/{ref} is gone"} if not present
                else {"state": BLOCKER_STILL_BLOCKING, "why": f"origin/{ref} still exists"})
    return ({"state": BLOCKER_CLEARED, "why": f"origin/{ref} exists"} if present
            else {"state": BLOCKER_STILL_BLOCKING, "why": f"origin/{ref} does not exist"})


def _grade_path(ref: str, clears_when: str, world: Dict[str, Any],
                path_checker) -> Dict[str, Any]:
    exists = path_checker(ref, world)
    if exists is None:
        return {"state": BLOCKER_COULD_NOT_LOOK, "reason": REASON_GIT_READ_FAILED,
                "why": f"could not read origin/main:{ref}"}
    if clears_when == "exists":
        return ({"state": BLOCKER_CLEARED, "why": f"{ref} is on main"} if exists
                else {"state": BLOCKER_STILL_BLOCKING, "why": f"{ref} is not on main"})
    return ({"state": BLOCKER_CLEARED, "why": f"{ref} is gone from main"} if not exists
            else {"state": BLOCKER_STILL_BLOCKING, "why": f"{ref} is still on main"})


def _grade_object(ref: str, object_reader) -> Dict[str, Any]:
    doc = object_reader(ref)
    if doc is None:
        return {"state": BLOCKER_COULD_NOT_LOOK, "reason": REASON_OBJECT_UNREADABLE,
                "why": f"work object {ref} is unreadable or absent"}
    lifecycle = str(doc.get("lifecycle") or "").strip().lower()
    if not lifecycle:
        return {"state": BLOCKER_COULD_NOT_LOOK, "reason": REASON_OBJECT_UNREADABLE,
                "why": f"{ref} declares no lifecycle"}
    if lifecycle in ("done", "accepted"):
        return {"state": BLOCKER_CLEARED, "why": f"{ref} lifecycle={lifecycle}"}
    return {"state": BLOCKER_STILL_BLOCKING, "why": f"{ref} lifecycle={lifecycle}"}


def _grade_decision(ref: str, object_reader) -> Dict[str, Any]:
    """`ref` is `<OBJECT-ID>::<request-id>`.

    ⚠️ A request with no `answer` block is STILL_BLOCKING, never
    `could_not_look`: the object read fine and the absence of an answer IS the
    reading. Only a failure to read the object is *we did not look*.
    """
    if "::" not in ref:
        return {"state": BLOCKER_COULD_NOT_LOOK, "reason": REASON_AMBIGUOUS_REF,
                "why": f"{ref!r} is not '<OBJECT-ID>::<request-id>'"}
    obj_id, req_id = ref.split("::", 1)
    doc = object_reader(obj_id.strip())
    if doc is None:
        return {"state": BLOCKER_COULD_NOT_LOOK, "reason": REASON_OBJECT_UNREADABLE,
                "why": f"work object {obj_id} is unreadable or absent"}
    requests = doc.get("decision_requests")
    if not isinstance(requests, list):
        return {"state": BLOCKER_COULD_NOT_LOOK, "reason": REASON_OBJECT_UNREADABLE,
                "why": f"{obj_id} declares no decision_requests[]"}
    for req in requests:
        if isinstance(req, dict) and str(req.get("id") or "").strip() == req_id.strip():
            if req.get("answer"):
                return {"state": BLOCKER_CLEARED,
                        "why": f"{req_id} carries an answer block"}
            return {"state": BLOCKER_STILL_BLOCKING,
                    "why": f"{req_id} is declared and unanswered"}
    return {"state": BLOCKER_COULD_NOT_LOOK, "reason": REASON_AMBIGUOUS_REF,
            "why": f"{obj_id} declares no request {req_id!r}"}


def grade_row(row: Dict[str, Any], world: Dict[str, Any], **kw) -> Dict[str, Any]:
    """Grade one registry row. Returns its per-blocker verdicts and a roll-up."""
    session_id = str(row.get("session_id") or "")
    declared = row.get("blocked_on")
    if not declared:
        return {"session_id": session_id, "title": row.get("title"),
                "lane_state": BLOCKER_UNDECLARED, "blockers": []}
    if not isinstance(declared, list):
        declared = [declared]
    verdicts = [grade_blocker(b, world, **kw) for b in declared]
    states = {v["state"] for v in verdicts}
    # ⚠️ ANY cleared frees the lane, never ALL. A lane that got one of the two
    # things it asked for may well be able to proceed, and if it cannot it loses
    # one turn -- against the 13h / 3.5d this module exists to end. Requiring
    # ALL would reproduce the failure: one ungradeable blocker would pin a lane
    # whose real blocker cleared days ago.
    if BLOCKER_CLEARED in states:
        lane = BLOCKER_CLEARED
    elif BLOCKER_COULD_NOT_LOOK in states:
        lane = BLOCKER_COULD_NOT_LOOK
    else:
        lane = BLOCKER_STILL_BLOCKING
    return {"session_id": session_id, "title": row.get("title"),
            "checklist_item": row.get("checklist_item"),
            "lane_state": lane, "blockers": verdicts}


def _latch_key(session_id: str, verdict: Dict[str, Any]) -> str:
    """The page-once key, and the STATE is part of it deliberately.

    ⚠️ THE STATE WAS MISSING UNTIL 2026-09-11 AND THAT DEFEATED THE WHOLE
    MECHANISM IN ITS OWN MOTIVATING CASE. `assess` latches `cleared` and
    `could_not_look` through this one key, so without the state a blocker that
    paged once as *we could not look* could never page again -- including on the
    single transition the watcher exists for, `could_not_look -> cleared`. Found
    by the MI-235 review lane and CONFIRMED BY EXECUTION against a positive
    control, on both routes that reach it: a cross-repo PR whose listing was
    unreadable and is later covered (MI-238's own case), and a same-repo PR
    whose listing was unreadable and is later found merged into main. In each,
    run 2 graded `cleared` and woke NOBODY while the same world with an empty
    latch woke 1 -- so it was the latch, not the grading.

    Including the state keeps the anti-fatigue property intact: a blocker that
    stays `could_not_look` still pages exactly once, because its key is
    unchanged. Only a genuine CHANGE OF STATE mints a new key, which is the
    only thing that should ever re-page.
    """
    return (f"{session_id}|{verdict.get('state')}|{verdict.get('kind')}"
            f"|{verdict.get('ref')}|{verdict.get('clears_when')}")


def assess(rows: Sequence[Dict[str, Any]], world: Dict[str, Any],
           already_paged: Sequence[str] = (), **kw) -> Dict[str, Any]:
    """The whole sweep: grade every non-terminal row, roll up, decide the page."""
    paged = set(already_paged)
    graded: List[Dict[str, Any]] = []
    skipped_terminal = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("state") or "").strip().lower() in TERMINAL_ROW_STATES:
            # Counted, never silently dropped: "we skipped it" must not read as
            # "it was fine".
            if row.get("blocked_on"):
                skipped_terminal += 1
            continue
        graded.append(grade_row(row, world, **kw))

    wake: List[Dict[str, Any]] = []
    new_latch = set(paged)
    for g in graded:
        if g["lane_state"] not in (BLOCKER_CLEARED, BLOCKER_COULD_NOT_LOOK):
            continue
        fresh = [v for v in g["blockers"]
                 if v["state"] in (BLOCKER_CLEARED, BLOCKER_COULD_NOT_LOOK)
                 and _latch_key(g["session_id"], v) not in paged]
        if fresh:
            wake.append({**g, "newly": fresh})
            new_latch.update(_latch_key(g["session_id"], v) for v in fresh)

    counts = {s: 0 for s in ALL_BLOCKER_STATES}
    for g in graded:
        counts[g["lane_state"]] += 1
    return {
        "rows_graded": len(graded),
        "rows_skipped_terminal_with_declaration": skipped_terminal,
        "lane_state_counts": counts,
        "wake_list": wake,
        "latch": sorted(new_latch),
        "declared_rows": sum(1 for g in graded if g["lane_state"] != BLOCKER_UNDECLARED),
    }


# ─────────────────────────────────────────────────────────────────────────────
# I/O shell
# ─────────────────────────────────────────────────────────────────────────────
def _read_registry(path: Path) -> Tuple[Optional[List[Dict[str, Any]]], bool]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None, False
    rows = doc.get("sessions") if isinstance(doc, dict) else doc
    return (rows, True) if isinstance(rows, list) else (None, False)


def _prior_latch(path: Path) -> List[str]:
    try:
        prior = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        # ⚠️ An unreadable latch PAGES rather than suppressing -- the same
        # polarity `target_naked_alert_state.json` was corrected to. Failing
        # loud makes a broken latch announce itself as noise instead of silence.
        return []
    got = prior.get("latch")
    return [str(x) for x in got] if isinstance(got, list) else []


def render(report: Dict[str, Any]) -> str:
    c = report["lane_state_counts"]
    out = [
        "BLOCKED-LANE WATCH",
        f"  rows graded ............ {report['rows_graded']}",
        f"    declaring a blocker .. {report['declared_rows']}",
        f"    undeclared ........... {c[BLOCKER_UNDECLARED]}   (author gap; never pages)",
        f"  lanes still blocked .... {c[BLOCKER_STILL_BLOCKING]}",
        f"  lanes FREE (cleared) ... {c[BLOCKER_CLEARED]}",
        f"  lanes ungradeable ...... {c[BLOCKER_COULD_NOT_LOOK]}   (we did not look; pages)",
        f"  terminal rows skipped .. {report['rows_skipped_terminal_with_declaration']}",
        "",
    ]
    if not report["wake_list"]:
        out.append("  Nothing NEW to report. ⚠️ This is not 'no lane is stuck' —")
        out.append("  a lane that declared no blocker is invisible to this sweep.")
        return "\n".join(out)
    out.append("  ⚠️ WAKE DUE — these lanes are waiting for something that has happened:")
    for w in report["wake_list"]:
        out.append(f"    · {w['session_id']}  [{w.get('checklist_item') or '—'}]")
        out.append(f"      {(w.get('title') or '')[:96]}")
        for v in w["newly"]:
            tag = "CLEARED" if v["state"] == BLOCKER_CLEARED else "COULD NOT LOOK"
            out.append(f"      {tag}: {v['kind']} {v['ref']} — {v['why']}")
        out.append(f"      poke: fire_trigger a poke-only Routine bound to {w['session_id']}")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true",
                    help="exercise the grading policy and exit")
    ap.add_argument("--registry", default=str(REGISTRY_PATH))
    ap.add_argument("--pr-state", default=None,
                    help="JSON listing of OPEN pull requests (gh api .../pulls?state=open)")
    ap.add_argument("--this-repo", default="benbaichmankass/Metis-Insights")
    ap.add_argument("--write-receipt", action="store_true")
    ap.add_argument("--session-id", default=None,
                    help="grade ONLY this session's row (a lane checking itself)")
    a = ap.parse_args(argv)

    if a.self_test:
        return _self_test()

    rows, readable = _read_registry(Path(a.registry))
    if not readable or rows is None:
        # ⚠️ Exit 4, not 0. An unreadable register is the sensor being blind and
        # must never render as "no lane is blocked".
        print("BLOCKED-LANE WATCH: COULD NOT READ THE REGISTER — this is NOT "
              "an empty roster, it is a blind sensor.", file=sys.stderr)
        return 4
    if a.session_id:
        rows = [r for r in rows if isinstance(r, dict)
                and r.get("session_id") == a.session_id]

    world = collect_world(this_repo=a.this_repo,
                          pr_state_path=Path(a.pr_state) if a.pr_state else None)
    report = assess(rows, world, already_paged=_prior_latch(RECEIPT_PATH))
    report["generated_at"] = _now_iso()
    report["registry_rows_total"] = len(rows)
    report["world_readable"] = {
        k: world[k] for k in
        ("branches_readable", "merged_prs_readable", "pr_states_readable", "paths_readable")
    }
    print(render(report))

    if a.write_receipt:
        RECEIPT_PATH.parent.mkdir(parents=True, exist_ok=True)
        RECEIPT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                                encoding="utf-8")
        print(f"\nreceipt: {RECEIPT_PATH.relative_to(REPO_ROOT)}")

    return 3 if report["wake_list"] else 0


# ─────────────────────────────────────────────────────────────────────────────
def _self_test() -> int:
    failures: List[str] = []

    def check(label: str, got: Any, want: Any) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    W = {"this_repo": "o/r", "branches": {"claude/live"}, "branches_readable": True,
         "merged_prs": {"11579"}, "merged_prs_readable": True,
         "pr_states": {"o/r#4": "open", "o/r#5": "closed", "o/r#6": "merged"},
         "pr_states_repos": {"o/r"},
         "pr_states_readable": True, "paths_readable": True}
    def g(b, w=W, **kw):
        return grade_blocker(b, w, **kw)["state"]

    # ── the three measured incidents, as fixtures ──
    check("MI-222 PR merged into main -> cleared",
          g({"kind": "pull_request", "ref": "#11579",
             "clears_when": "closed_or_merged"}), BLOCKER_CLEARED)
    check("MI-222 branch still on origin -> still_blocking",
          g({"kind": "branch", "ref": "claude/live", "clears_when": "deleted"}),
          BLOCKER_STILL_BLOCKING)
    check("MI-222 branch gone -> cleared",
          g({"kind": "branch", "ref": "claude/gone", "clears_when": "deleted"}),
          BLOCKER_CLEARED)
    check("MI-238 cross-repo PR with no state file -> could_not_look",
          g({"kind": "pull_request", "ref": "other/dash#215", "clears_when": "merged"}),
          BLOCKER_COULD_NOT_LOOK)

    # ── PR state table ──
    check("open PR -> still_blocking",
          g({"kind": "pull_request", "ref": "#4", "clears_when": "closed_or_merged"}),
          BLOCKER_STILL_BLOCKING)
    check("closed PR, clears_when=closed_or_merged -> cleared",
          g({"kind": "pull_request", "ref": "#5", "clears_when": "closed_or_merged"}),
          BLOCKER_CLEARED)
    check("closed-unmerged PR, clears_when=merged -> still_blocking",
          g({"kind": "pull_request", "ref": "#5", "clears_when": "merged"}),
          BLOCKER_STILL_BLOCKING)
    check("merged PR -> cleared",
          g({"kind": "pull_request", "ref": "#6", "clears_when": "merged"}),
          BLOCKER_CLEARED)

    # ── the blind-read cases: NEVER cleared, NEVER still_blocking ──
    blind = dict(W, branches_readable=False, merged_prs_readable=False,
                 pr_states_readable=False, pr_states={})
    check("unreadable branch list -> could_not_look",
          g({"kind": "branch", "ref": "claude/live", "clears_when": "deleted"}, blind),
          BLOCKER_COULD_NOT_LOOK)
    check("unreadable PR state -> could_not_look",
          g({"kind": "pull_request", "ref": "#4", "clears_when": "merged"}, blind),
          BLOCKER_COULD_NOT_LOOK)
    check("unreadable path -> could_not_look",
          grade_blocker({"kind": "path_on_main", "ref": "a/b.md",
                         "clears_when": "exists"}, W,
                        path_checker=lambda *_: None)["state"],
          BLOCKER_COULD_NOT_LOOK)

    # ── cross-repo number collision must NOT be settled off our own main ──
    check("cross-repo #11579 is NOT cleared by OUR main's history",
          g({"kind": "pull_request", "ref": "other/dash#11579",
             "clears_when": "closed_or_merged"}), BLOCKER_COULD_NOT_LOOK)

    # REGRESSION (caught by this self-test during the build): a cross-repo PR is
    # absent from a single-repo open-PR listing BY CONSTRUCTION, so the
    # absent-therefore-closed inference must not fire for it.
    check("cross-repo PR absent from a listing that does not cover its repo",
          g({"kind": "pull_request", "ref": "other/dash#4",
             "clears_when": "closed_or_merged"}), BLOCKER_COULD_NOT_LOOK)
    check("SAME-repo PR absent from the open listing -> cleared",
          g({"kind": "pull_request", "ref": "#999",
             "clears_when": "closed_or_merged"}), BLOCKER_CLEARED)
    check("a listing that explicitly covers another repo DOES settle it",
          grade_blocker({"kind": "pull_request", "ref": "other/dash#4",
                         "clears_when": "closed_or_merged"},
                        dict(W, pr_states_repos={"o/r", "other/dash"}))["state"],
          BLOCKER_CLEARED)

    # ── malformed declarations ──
    for bad, label in (
        ({"kind": "vibes", "ref": "x", "clears_when": "y"}, "unknown kind"),
        ({"kind": "branch", "ref": "", "clears_when": "deleted"}, "empty ref"),
        ({"kind": "branch", "ref": "b", "clears_when": "merged"}, "wrong clears_when"),
        ("not-an-object", "non-object"),
    ):
        check(f"malformed ({label}) -> could_not_look", g(bad), BLOCKER_COULD_NOT_LOOK)

    # ── work object + operator decision ──
    objs = {"WO-A": {"lifecycle": "done"}, "WO-B": {"lifecycle": "in_flight"},
            "WO-C": {"decision_requests": [{"id": "D1", "answer": {"chosen": "x"}},
                                           {"id": "D2"}]}}
    def rd(ref):
        return objs.get(ref)
    check("work object done -> cleared",
          g({"kind": "work_object", "ref": "WO-A", "clears_when": "done_or_accepted"},
            W, object_reader=rd), BLOCKER_CLEARED)
    check("work object in_flight -> still_blocking",
          g({"kind": "work_object", "ref": "WO-B", "clears_when": "done_or_accepted"},
            W, object_reader=rd), BLOCKER_STILL_BLOCKING)
    check("missing work object -> could_not_look",
          g({"kind": "work_object", "ref": "WO-Z", "clears_when": "done_or_accepted"},
            W, object_reader=rd), BLOCKER_COULD_NOT_LOOK)
    check("answered decision -> cleared",
          g({"kind": "operator_decision", "ref": "WO-C::D1", "clears_when": "answered"},
            W, object_reader=rd), BLOCKER_CLEARED)
    check("unanswered decision -> still_blocking (NOT could_not_look)",
          g({"kind": "operator_decision", "ref": "WO-C::D2", "clears_when": "answered"},
            W, object_reader=rd), BLOCKER_STILL_BLOCKING)
    check("decision id absent -> could_not_look",
          g({"kind": "operator_decision", "ref": "WO-C::D9", "clears_when": "answered"},
            W, object_reader=rd), BLOCKER_COULD_NOT_LOOK)

    # ── row roll-up ──
    check("row with no blocked_on -> undeclared",
          grade_row({"session_id": "s"}, W)["lane_state"], BLOCKER_UNDECLARED)
    check("ANY cleared frees the lane",
          grade_row({"session_id": "s", "blocked_on": [
              {"kind": "branch", "ref": "claude/live", "clears_when": "deleted"},
              {"kind": "pull_request", "ref": "#6", "clears_when": "merged"}]},
              W)["lane_state"], BLOCKER_CLEARED)
    check("ungradeable beats still_blocking in the roll-up",
          grade_row({"session_id": "s", "blocked_on": [
              {"kind": "branch", "ref": "claude/live", "clears_when": "deleted"},
              {"kind": "pull_request", "ref": "other/d#1", "clears_when": "merged"}]},
              W)["lane_state"], BLOCKER_COULD_NOT_LOOK)

    # ── the sweep, the latch, and the terminal filter ──
    rows = [
        {"session_id": "free", "state": "blocked", "blocked_on": [
            {"kind": "pull_request", "ref": "#6", "clears_when": "merged"}]},
        {"session_id": "stuck", "state": "blocked", "blocked_on": [
            {"kind": "pull_request", "ref": "#4", "clears_when": "merged"}]},
        {"session_id": "dead", "state": "archived", "blocked_on": [
            {"kind": "pull_request", "ref": "#6", "clears_when": "merged"}]},
        {"session_id": "quiet", "state": "idle"},
    ]
    r1 = assess(rows, W)
    check("sweep wakes exactly the freed lane", [w["session_id"] for w in r1["wake_list"]],
          ["free"])
    check("archived row is skipped, not graded",
          r1["rows_skipped_terminal_with_declaration"], 1)
    check("undeclared row is counted", r1["lane_state_counts"][BLOCKER_UNDECLARED], 1)
    r2 = assess(rows, W, already_paged=r1["latch"])
    check("LATCH: a second run does not re-page the same blocker", r2["wake_list"], [])
    rows2 = rows + [{"session_id": "free2", "state": "blocked", "blocked_on": [
        {"kind": "branch", "ref": "claude/gone", "clears_when": "deleted"}]}]
    r3 = assess(rows2, W, already_paged=r1["latch"])
    check("LATCH: a NEW clear still pages",
          [w["session_id"] for w in r3["wake_list"]], ["free2"])

    # ── the write-time contract and the dispatch table cannot drift apart ──
    for kind in CLEARS_WHEN_BY_KIND:
        for cw in CLEARS_WHEN_BY_KIND[kind]:
            try:
                st = grade_blocker({"kind": kind, "ref": "x::y" if kind ==
                                    "operator_decision" else "x", "clears_when": cw},
                                   W, object_reader=lambda _r: None,
                                   path_checker=lambda *_: None)["state"]
            except AssertionError as exc:
                failures.append(f"no resolver for declared kind: {exc}")
                continue
            if st not in ALL_BLOCKER_STATES:
                failures.append(f"{kind}/{cw} produced non-vocabulary state {st!r}")

    # ── every state is reachable: a contract that cannot emit one of its states
    #    is a collapsed state wearing four labels.
    reachable = {BLOCKER_CLEARED, BLOCKER_STILL_BLOCKING, BLOCKER_COULD_NOT_LOOK}
    reachable.add(grade_row({"session_id": "s"}, W)["lane_state"])
    check("all four states are reachable", sorted(reachable), sorted(ALL_BLOCKER_STATES))

    if failures:
        print("blocked-lane-watch self-test: FAIL")
        for f in failures:
            print(f"  ✗ {f}")
        return 1
    print("blocked-lane-watch self-test: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
