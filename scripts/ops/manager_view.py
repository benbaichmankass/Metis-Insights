#!/usr/bin/env python3
#
# wiring: manual-only — run by a MANAGER holding the MCP tools. CI cannot run
# this usefully: three of its five inputs are live observations, two of which
# come from `mcp__*` tools CI does not hold. It refuses to substitute a stored
# snapshot for any of them. See "WHY THIS CANNOT BE A WORKFLOW" below.
"""THE MANAGER VIEW — one derived read, replacing five manual ones.

WHAT IT REPLACES, AND WHY THAT IS THE POINT
-------------------------------------------
To answer *"what is actually in flight and what is waiting on me?"* a manager
today reconciles FIVE surfaces by hand:

  1. `docs/claude/work/SESSIONS.json`          — which sub-sessions exist
  2. `docs/claude/work/MANAGER-CHECKLIST.json` — what each was assigned
  3. `list_sessions`                            — what is actually RUNNING
  4. the open pull-request list                 — what is waiting to merge
  5. `get_check_runs` per PR                    — whether CI is green

That reconciliation is what consumes the manager's context, and **context is
the scarce resource** — not tokens, not tools, not wall-clock. This prints the
join as ONE table so the reconciliation is done once, by code, in a form a
successor can re-run instead of re-deriving.

⚠️ IT STORES NOTHING. THERE IS NO FOURTH REGISTER HERE.
--------------------------------------------------------
**Every column is DERIVED at read time and thrown away.** This file has no
output artifact, no `--write-state`, and no state path. That is a direct
response to the disease it was built for:

    Measured 2026-09-03 by the manager against a live `list_sessions(mine=true,
    limit=40)` read: 35 of 36 sub-sessions were IDLE, one RUNNING. Against that,
    `MANAGER-CHECKLIST.json` carried 25 items reading `in_flight`, 17 of them
    owned by a named session — and every one of those sessions was idle.
    `SESSIONS.json` said `working` for sessions stopped hours earlier; MI-84
    measured 17 of 17 inherited `working` rows wrong.

**A stored state goes stale the moment a session idles. A derived one cannot.**
Adding a sixth file asserting session state would reproduce the defect with one
more place to disagree, so the registry's stored `state` is treated here as a
CLAIM to be graded — never as an input to be trusted, and never as something
this tool rewrites. Correcting a row is the manager's write, through
`session_registry.py`; this tool only tells the truth about it.

⚠️ Measured on `main` @ `bdbf090c` (population: all 82 rows of SESSIONS.json):
only **34** rows carry a `state` field at all — 18 `working`, 14 `idle`, 1
`running`, 1 `archived` — and **48 carry none**. So for 48 of 82 rows the stored
register does not even have an opinion to be stale. Deriving is not merely
fresher than reading that field; for most rows it is the only thing available.

THREE STATES PER CELL, NEVER COLLAPSED
---------------------------------------
Every cell in the table is one of three things, and the difference is load-
bearing rather than cosmetic (`docs/CLAUDE-RULES-CANONICAL.md` § "Collapsed
states"):

  a concrete value   we looked and this is what is there.
  ``not_observed``   **WE DID NOT LOOK** — the input that would answer this
                     column was not supplied. Never rendered as a blank, a dash,
                     a zero, or an optimistic default.
  ``none_found``     we DID look, and the supplied observation contains nothing
                     for this row. A real finding, and the opposite fact.

⚠️ THE CI COLUMN CARRIES A FOURTH VALUE, AND IT IS THE ONE THAT BIT THE MANAGER
-------------------------------------------------------------------------------
``no_checks`` — the check-run payload was read and contains **zero** check runs.
This is NOT `success` and NOT `not_observed`. It is the state a PR lands in when
its last commit came from a relay: **GitHub fires no workflows for a
`GITHUB_TOKEN` push**, so a relay commit landing LAST leaves the PR with zero
checks. On the morning of 2026-09-03 the manager read that condition through
`get_status` — which returns *legacy commit statuses* and reports
`total_count: 0` on perfectly healthy green PRs — and took "no checks" for
"blocked". Both readings are wrong in opposite directions, and neither is
distinguishable from the other unless the state exists. So it exists, it is
rendered in its own right, and the renderer says which remedy each implies.

THE FIVE INPUTS, AND WHICH OF THEM CI COULD EVER SUPPLY
--------------------------------------------------------
| input | source | can CI get it? |
|---|---|---|
| SESSIONS.json | this repo | yes |
| MANAGER-CHECKLIST.json | this repo | yes |
| live sessions | `list_sessions` (MCP) | **NO — CI holds no MCP tools** |
| open PRs | `list_pull_requests` / `gh pr list` | yes, with a token |
| CI conclusions | `get_check_runs` / `gh api` | yes, with a token |

⚠️ WHY THIS CANNOT BE A WORKFLOW. Session liveness needs `list_sessions`, an
`mcp__*` tool CI does not hold — the same wall `queue_latency.py` hit and
reported honestly rather than engineering around ("no observation ⇒ `unknown`,
permanently, with no flag to assert otherwise"). This file takes the same
position: **there is no flag that asserts the sessions are fine.** Omitting
`--live-sessions` grades every session's live column `not_observed` and the
whole verdict `unknown`, forever.

⚠️ AND CROSS-REPO PR REFERENCES ARE NOT MATCHED. `SESSIONS.json` records PRs
repo-qualified (`Metis-Insights#10654`, `ict-trader-dashboard#210`). A PR in
another repository cannot be graded against THIS repository's open-PR list, so
it is reported as `other_repo` rather than silently compared — comparing it
would manufacture a "closed PR" finding for a PR that is open somewhere else.

VERDICT — MIRRORS `handoff_check.py` RATHER THAN INVENTING A REGISTER
----------------------------------------------------------------------
``clear``      every input was supplied and nothing contradicts anything.
``attention``  a FINDING: the registry or checklist claims a session is working
               and the live observation says otherwise, or an `in_flight`
               checklist item is owned by a session that is not running.
``unknown``    nothing contradicted, and at least one input was not supplied.
               ⚠️ **NEVER a soft pass.** A table with a `not_observed` column is
               not a clean bill of health, and the failure this exists for is
               invisible from inside.

EXIT CODES: 0 clear · 3 attention · 4 unknown — the same split, and for the same
reason: both non-clear states are non-zero so a caller cannot treat "we could
not look" as a pass.

USAGE
-----
    python3 scripts/ops/manager_view.py \
        --live-sessions  <(list_sessions output) \
        --open-prs       <(list_pull_requests output) \
        --check-runs     <(a {"<pr>": <get_check_runs output>} map) \
        --manager-session-id "$CLAUDE_SESSION_ID"

Any input may be omitted; the columns it feeds then read `not_observed` and the
verdict can never be `clear`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import manager_preflight as mp  # noqa: E402
import session_registry as sr  # noqa: E402

REPO_ROOT = sr.REPO_ROOT

#: The repository these PR numbers belong to. A reference qualified with any
#: OTHER repo is reported `other_repo`, never compared. See the docstring.
DEFAULT_REPO = "Metis-Insights"

#: Cell sentinels. ⚠️ Two distinct facts, deliberately never one value.
NOT_OBSERVED = "not_observed"
NONE_FOUND = "none_found"
#: CI-only. The payload was read and holds zero check runs — see the docstring.
NO_CHECKS = "no_checks"
#: CI-only. There is no PR to have checks, so "no checks" would be a category
#: error rather than a finding.
NO_PR = "no_pr"
OTHER_REPO = "other_repo"

CLEAR, ATTENTION, UNKNOWN = "clear", "attention", "unknown"
_EXIT = {CLEAR: 0, ATTENTION: 3, UNKNOWN: 4}

#: Checklist states that assert live work. An item in one of these, owned by a
#: session that is not running, is the headline finding.
ACTIVE_CHECKLIST_STATES = ("in_flight",)
#: Registry `state` values that assert live work.
ACTIVE_REGISTRY_STATES = ("working", "running")

#: A repo-qualified PR reference as SESSIONS.json writes them.
_PR_REF_RE = re.compile(r"\A(?:([A-Za-z0-9._-]+)#)?(\d{2,7})\Z")

#: A whole session id. Reused from `session_registry` rather than restated, so
#: the two cannot drift on what counts as an id.
_STRICT_SESSION_ID = sr._STRICT_ID_RE

#: ⚠️ Conclusions ordered WORST FIRST. The rollup reports the worst, because a
#: PR with one failing check and nine green ones is a failing PR, and reporting
#: the majority would be an average over a safety signal.
_CI_WORST_FIRST = ("failure", "timed_out", "cancelled", "action_required",
                   "startup_failure", "stale", "neutral", "skipped", "success")


def _parse_ts(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Input normalisers. Each returns None for "we could not read this", which the
# caller MUST render `not_observed` — never as an empty result.
# --------------------------------------------------------------------------- #
def normalise_prs(raw: Any) -> Optional[List[Dict[str, Any]]]:
    """Open PRs, keeping the fields the join needs (number, head ref, title).

    ⚠️ Returns ``None`` when nothing usable was found. An unparseable open-PR
    observation is NOT an empty one — "we could not look" and "no PR is open"
    are opposite facts, and the `|| echo '{}'` idiom that collapses them is a
    named failure class in this repo.
    """
    for _ in range(4):
        if not isinstance(raw, dict):
            break
        for key in ("pull_requests", "open_prs", "data", "results", "items"):
            inner = raw.get(key)
            if isinstance(inner, (list, dict)):
                raw = inner
                break
        else:
            break
        if isinstance(raw, list):
            break
    if not isinstance(raw, list):
        return None
    out: List[Dict[str, Any]] = []
    for e in raw:
        if not isinstance(e, dict):
            continue
        num = e.get("number", e.get("pr"))
        if isinstance(num, bool) or not isinstance(num, int):
            continue
        head = e.get("head")
        out.append({
            "number": num,
            "head_ref": (str(head.get("ref") or "") if isinstance(head, dict)
                         else str(e.get("head_ref") or "")),
            "title": str(e.get("title") or ""),
            "draft": bool(e.get("draft")),
        })
    return out or None


def rollup_check_runs(payload: Any) -> str:
    """One CI conclusion for one PR, from a `get_check_runs`-shaped payload.

    ⚠️ ZERO CHECK RUNS IS ``no_checks``, NOT ``success``. That distinction is the
    entire reason this function exists rather than a dict lookup — see the module
    docstring. A relay commit landing last leaves a PR in exactly this state, and
    reading it as green would merge an untested branch while reading it as red
    would block a healthy one.
    """
    if isinstance(payload, str):
        return payload.strip().lower() or NOT_OBSERVED
    runs: Any = payload
    for _ in range(4):
        if not isinstance(runs, dict):
            break
        for key in ("check_runs", "checkRuns", "data", "results", "items"):
            inner = runs.get(key)
            if isinstance(inner, (list, dict)):
                runs = inner
                break
        else:
            break
        if isinstance(runs, list):
            break
    if not isinstance(runs, list):
        return NOT_OBSERVED
    if not runs:
        return NO_CHECKS
    concluded: List[str] = []
    pending = 0
    for r in runs:
        if not isinstance(r, dict):
            continue
        status = str(r.get("status") or "").strip().lower()
        concl = str(r.get("conclusion") or "").strip().lower()
        # ⚠️ A run that has not COMPLETED has no conclusion to read. Counting it
        # as anything but pending would report a verdict nobody reached.
        if status and status != "completed":
            pending += 1
            continue
        if concl:
            concluded.append(concl)
        else:
            pending += 1
    if not concluded and not pending:
        return NO_CHECKS
    for worst in _CI_WORST_FIRST:
        if worst in concluded:
            # A failure outranks an unfinished run: it is already known bad.
            if worst in ("neutral", "skipped", "success") and pending:
                return "pending"
            return worst
    return "pending" if pending else NO_CHECKS


def normalise_checks(raw: Any) -> Optional[Dict[int, str]]:
    """PR number -> rolled-up CI conclusion.

    Tolerated: a mapping of PR number (int or str) to either a conclusion string
    or a `get_check_runs` payload; or a list of ``{"pr": N, ...}`` entries.
    """
    if isinstance(raw, dict):
        inner = raw.get("check_runs_by_pr")
        if isinstance(inner, dict):
            raw = inner
    out: Dict[int, str] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                num = int(str(k).lstrip("#"))
            except (TypeError, ValueError):
                continue
            out[num] = rollup_check_runs(v)
    elif isinstance(raw, list):
        for e in raw:
            if not isinstance(e, dict):
                continue
            num = e.get("pr", e.get("number"))
            if isinstance(num, bool) or not isinstance(num, int):
                continue
            out[num] = rollup_check_runs(e.get("check_runs", e))
    else:
        return None
    return out or None


def timestamp_index(raw: Any) -> Dict[str, str]:
    """session_id -> its freshest activity timestamp, harvested from the RAW payload.

    ⚠️ WHY THIS EXISTS RATHER THAN A SECOND SESSION PARSER. `session_registry`
    is the one owner of *what a session entry is*, and its `normalise_observation`
    deliberately keeps only the fields `reconcile` needs — id, status, parent,
    tags, title. It therefore DROPS `updated_at`, so the age column read
    `none_found` for all 24 rows on the first live run even though every entry
    carried one. Forking that normaliser to add a field would create a second
    definition of a session entry, which is exactly how the two would drift; this
    walks the same raw payload for timestamps ONLY and merges them in by id, so
    the id/status half stays owned in one place.

    Preference order is `updated_at` → `last_activity` → `created_at`: the age
    the manager wants is *time since anything last happened*, and falling back to
    `created_at` on a session that has never been touched is the honest reading,
    not a fabrication — a row with none of the three gets no entry at all and the
    caller renders `none_found` rather than a zero.
    """
    out: Dict[str, str] = {}

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 6:
            return
        if isinstance(node, list):
            for item in node:
                walk(item, depth + 1)
            return
        if not isinstance(node, dict):
            return
        sid = node.get("id") or node.get("session_id")
        if isinstance(sid, str) and _STRICT_SESSION_ID.match(sid.strip()):
            for key in ("updated_at", "last_activity", "created_at"):
                val = node.get(key)
                if isinstance(val, str) and val.strip():
                    out.setdefault(sid.strip(), val.strip())
                    break
        for value in node.values():
            walk(value, depth + 1)

    walk(raw)
    return out


def _load(spec: Optional[str]) -> Optional[Any]:
    """Read a JSON-ish observation from a path or stdin. Mirrors the loaders in
    `session_registry` / `open_pr_record` so a manager pastes the same way."""
    if spec is None:
        return None
    text = sys.stdin.read() if spec == "-" else Path(spec).read_text(encoding="utf-8")
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    ends = [i for i in (text.rfind("}"), text.rfind("]")) if i >= 0]
    for cand in ([text] + ([text[min(starts):max(ends) + 1]] if starts and ends else [])):
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            continue
    return text


# --------------------------------------------------------------------------- #
# The join. Pure, so the policy is arguable in tests rather than against a live
# fleet — the property `handoff_check.grade` is built on.
# --------------------------------------------------------------------------- #
def parse_pr_ref(ref: Any, repo: str = DEFAULT_REPO) -> Tuple[Optional[int], bool]:
    """(number, belongs_to_this_repo). A bare number is assumed to be ours."""
    if isinstance(ref, bool):
        return None, False
    if isinstance(ref, int):
        return ref, True
    m = _PR_REF_RE.match(str(ref).strip())
    if not m:
        return None, False
    owner, num = m.group(1), int(m.group(2))
    return num, (owner is None or owner == repo)


def checklist_items_for(ck_doc: Optional[Any], session_id: str) -> List[Dict[str, Any]]:
    """Checklist items whose `owner` names this session.

    ⚠️ Prefix-tolerant via `session_registry.id_known`, because the checklist
    abbreviates ids in prose. Treating an abbreviation as a different session
    manufactures findings — measured there: it inflated a census from 5 to 7.
    """
    out = []
    for item in (ck_doc.get("items") if isinstance(ck_doc, dict) else None) or []:
        if not isinstance(item, dict):
            continue
        owner = item.get("owner")
        if not isinstance(owner, str) or not owner.strip():
            continue
        for cand in sr.SESSION_ID_RE.findall(owner):
            if sr.id_known(cand, [session_id]) or session_id.startswith(cand):
                out.append(item)
                break
    return out


def _live_index(observation: Optional[List[Dict[str, Any]]]) -> Optional[Dict[str, Dict[str, Any]]]:
    if observation is None:
        return None
    return {e["session_id"]: e for e in observation if e.get("session_id")}


def _live_state(row: Optional[Dict[str, Any]]) -> Tuple[str, List[str]]:
    """(one-word live state, the raw status tokens it came from).

    The vocabulary is IMPORTED from `manager_preflight` rather than restated —
    a second copy would be free to drift from the one the preflight enforces.
    """
    if row is None:
        return NOT_OBSERVED, []
    toks = mp._status_tokens(row)
    if not toks:
        # ⚠️ An entry carrying NO status is `not_observed`, never "running".
        # "The row exists" is not "the row is working".
        return NOT_OBSERVED, []
    if any(t in tok for tok in toks for t in mp._NEEDS_ACTION_TOKENS):
        return "needs_action", toks
    if any(t in tok for tok in toks for t in mp._TERMINAL_TOKENS):
        return "terminal", toks
    if any(t in tok for tok in toks for t in mp._NOT_WORKING_TOKENS):
        return "idle", toks
    return "running", toks


def derive_row(reg_row: Dict[str, Any], ck_doc: Optional[Any],
               live_idx: Optional[Dict[str, Dict[str, Any]]],
               prs: Optional[List[Dict[str, Any]]],
               checks: Optional[Dict[int, str]],
               now: datetime, repo: str = DEFAULT_REPO) -> Dict[str, Any]:
    """One table row, every column derived from the inputs at read time."""
    sid = str(reg_row.get("session_id") or "").strip()
    live_row = live_idx.get(sid) if (live_idx is not None and sid) else None
    if live_idx is not None and live_row is None:
        # We DID look and this registered session was not in the observation.
        live, toks = NONE_FOUND, []
    else:
        live, toks = _live_state(live_row)

    # --- the checklist item -------------------------------------------------
    items = checklist_items_for(ck_doc, sid) if (ck_doc is not None and sid) else []
    if ck_doc is None:
        ck_id, ck_state = NOT_OBSERVED, NOT_OBSERVED
    elif not items:
        declared = reg_row.get("checklist_item")
        ck_id = str(declared) if declared else NONE_FOUND
        ck_state = NONE_FOUND
    else:
        ck_id = ",".join(str(i.get("id") or "?") for i in items)
        ck_state = ",".join(str(i.get("state") or "?") for i in items)

    # --- the PR -------------------------------------------------------------
    refs: List[Any] = []
    for item in items:
        for p in (item.get("prs") or []):
            refs.append(p)
    for p in (reg_row.get("prs") or []):
        refs.append(p)
    ours, foreign = [], 0
    for ref in refs:
        num, mine = parse_pr_ref(ref, repo)
        if num is None:
            continue
        if mine:
            ours.append(num)
        else:
            foreign += 1
    # A branch match is the fallback when no PR was ever recorded on either row.
    if not ours and prs is not None:
        for b in (reg_row.get("branches") or []):
            # `branches` entries are `<repo>:<ref>` and may carry a trailing
            # parenthetical note ("(expected; not yet pushed)"), so the ref is
            # taken up to the first space rather than assumed to be the whole
            # string.
            text = str(b)
            ref_part = text.split(":", 1)[1] if ":" in text else text
            ref_part = ref_part.strip().split(" ", 1)[0]
            if not ref_part:
                continue
            if ":" in text and not text.startswith(f"{repo}:"):
                foreign += 1
                continue
            for p in prs:
                if p["head_ref"] and p["head_ref"] == ref_part:
                    ours.append(p["number"])

    open_numbers = {p["number"] for p in prs} if prs is not None else None
    if prs is None:
        pr_cell: Any = NOT_OBSERVED
        pr_open: List[int] = []
    else:
        pr_open = sorted({n for n in ours if n in open_numbers})
        if pr_open:
            pr_cell = pr_open
        elif ours:
            # Recorded, and not open now. That is a real observation, not a gap.
            pr_cell = "closed_or_merged"
        elif foreign:
            pr_cell = OTHER_REPO
        else:
            pr_cell = NONE_FOUND

    # --- CI -----------------------------------------------------------------
    if not pr_open:
        ci: Any = NO_PR if prs is not None else NOT_OBSERVED
    elif checks is None:
        ci = NOT_OBSERVED
    else:
        got = [checks[n] for n in pr_open if n in checks]
        if not got:
            ci = NOT_OBSERVED
        else:
            ci = next((c for c in _CI_WORST_FIRST if c in got),
                      NO_CHECKS if all(g == NO_CHECKS for g in got) else got[0])

    # --- age since last activity -------------------------------------------
    age_h: Optional[float] = None
    src = None
    if live_row is not None:
        for key in ("updated_at", "last_activity", "created_at"):
            ts = _parse_ts(live_row.get(key))
            if ts:
                age_h, src = (now - ts).total_seconds() / 3600.0, key
                break
    age_cell: Any = round(age_h, 1) if age_h is not None else (
        NOT_OBSERVED if live_idx is None else NONE_FOUND)

    # --- the registry's own CLAIM, and whether the live read contradicts it --
    claim = str(reg_row.get("state") or "").strip().lower() or NONE_FOUND
    active_claims = [c for c in (claim,) if c in ACTIVE_REGISTRY_STATES]
    active_items = [i for i in items
                    if str(i.get("state") or "") in ACTIVE_CHECKLIST_STATES]
    if live in (NOT_OBSERVED,):
        agreement = NOT_OBSERVED
    elif (active_claims or active_items) and live in ("idle", "terminal",
                                                      "needs_action", NONE_FOUND):
        agreement = "contradicted"
    else:
        agreement = "agrees"

    return {
        "session_id": sid,
        "title": str(reg_row.get("title") or ""),
        "registry_claim": claim,
        "live": live,
        "live_tokens": toks,
        "pr": pr_cell,
        "open_prs": pr_open,
        "ci": ci,
        "checklist_item": ck_id,
        "checklist_state": ck_state,
        "age_hours": age_cell,
        "age_basis": src,
        "agreement": agreement,
        "asserts_active": bool(active_claims or active_items),
    }


def derive(reg_doc: Optional[Any], reg_readable: bool,
           ck_doc: Optional[Any], ck_readable: bool,
           observation: Optional[List[Dict[str, Any]]] = None,
           prs: Optional[List[Dict[str, Any]]] = None,
           checks: Optional[Dict[int, str]] = None,
           now: Optional[datetime] = None,
           only_active: bool = True,
           repo: str = DEFAULT_REPO) -> Dict[str, Any]:
    """The whole table, plus the verdict and the population behind it."""
    now = now or datetime.now(timezone.utc)
    live_idx = _live_index(observation)
    reg_rows = sr.registry_rows(reg_doc) if reg_readable else []

    rows = [derive_row(r, ck_doc if ck_readable else None, live_idx, prs, checks,
                       now, repo)
            for r in reg_rows if str(r.get("session_id") or "").strip()]

    shown = [r for r in rows if r["asserts_active"]] if only_active else rows

    # ⚠️ An UNREGISTERED live session is a finding this table cannot show as a
    # row, because it has no registry row to render. It is counted and named
    # separately rather than dropped — the exact loss `session_registry` exists
    # for (6 of 9 absent on 2026-09-02, 5 of them live).
    # ⚠️ AND IT NEEDS BOTH SIDES. With an UNREADABLE registry there is nothing to
    # be absent FROM, so every live session would read "unregistered" and the
    # tool would report a fleet-wide loss whose real cause is a parse error. That
    # is `we did not look` wearing a finding's clothes, so it stays None.
    known = sr.registry_ids(reg_rows)
    unregistered = ([e["session_id"] for e in observation
                     if not sr.id_known(e.get("session_id", ""), known)]
                    if (observation is not None and reg_readable) else None)

    missing = [n for n, v in (("live-sessions", observation), ("open-prs", prs),
                              ("check-runs", checks)) if v is None]
    if not reg_readable:
        missing.append("SESSIONS.json (unreadable)")
    if not ck_readable:
        missing.append("MANAGER-CHECKLIST.json (unreadable)")

    contradicted = [r for r in shown if r["agreement"] == "contradicted"]
    verdict = grade(shown, missing, contradicted, unregistered)

    pop = {
        "registry_rows": len(reg_rows),
        "rows_derived": len(rows),
        "rows_shown": len(shown),
        "only_active": only_active,
        "observed_sessions": len(observation) if observation is not None else None,
        "open_prs_observed": len(prs) if prs is not None else None,
        "prs_with_checks": len(checks) if checks is not None else None,
        "contradicted": len(contradicted),
        "unregistered_live": len(unregistered) if unregistered is not None else None,
        "inputs_missing": missing,
    }
    return {"verdict": verdict, "rows": shown, "all_rows": rows,
            "contradicted": contradicted, "unregistered_live": unregistered,
            "population": pop}


def grade(rows: Sequence[Dict[str, Any]], missing: Sequence[str],
          contradicted: Sequence[Dict[str, Any]],
          unregistered: Optional[Sequence[str]]) -> str:
    """PURE. FAIL-shaped findings dominate UNKNOWN because a known divergence is
    definite; UNKNOWN dominates CLEAR because "we could not look" is never a
    clean bill of health — the same ordering `handoff_check.grade` states."""
    if contradicted or unregistered:
        return ATTENTION
    if missing:
        return UNKNOWN
    return CLEAR


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
_ADVICE = {
    CLEAR: "Every input was supplied and nothing contradicts anything.",
    ATTENTION: "FINDINGS above. A row marked `contradicted` means a register "
               "claims live work over a live read that says otherwise — correct "
               "the row (session_registry.py) or re-poke the session; do not "
               "leave the claim standing.",
    UNKNOWN: "REFUSED a clean verdict — not because something failed, but "
             "because something could not be LOOKED AT. `unknown` is not a soft "
             "`clear`: a table with a not_observed column is not a clean bill of "
             "health. Supply what the population line names as missing.",
}


def _cell(v: Any) -> str:
    if isinstance(v, list):
        return ",".join(f"#{n}" for n in v) if v else NONE_FOUND
    return str(v)


def render(result: Dict[str, Any]) -> str:
    pop = result["population"]
    out: List[str] = []
    cols = ("session", "live", "PR", "CI", "item", "state", "age_h", "vs register")
    widths = (26, 12, 18, 14, 22, 12, 7, 12)
    out.append("  ".join(c.ljust(w) for c, w in zip(cols, widths)).rstrip())
    out.append("  ".join("-" * w for w in widths))
    for r in sorted(result["rows"], key=lambda x: (x["agreement"] != "contradicted",
                                                   x["session_id"])):
        cells = (r["session_id"][:26], r["live"], _cell(r["pr"])[:18], _cell(r["ci"]),
                 _cell(r["checklist_item"])[:22], _cell(r["checklist_state"])[:12],
                 _cell(r["age_hours"]), r["agreement"])
        out.append("  ".join(str(c).ljust(w) for c, w in zip(cells, widths)).rstrip())
    if not result["rows"]:
        out.append("(no row asserts live work)")

    out.append("")
    out.append(
        f"POPULATION: {pop['rows_shown']} row(s) shown of {pop['rows_derived']} "
        f"derived from {pop['registry_rows']} registry row(s)"
        + (" (only rows whose registry state or checklist item asserts LIVE work)"
           if pop["only_active"] else " (all rows)")
        + "; live observation: "
        + (f"{pop['observed_sessions']} session(s)"
           if pop["observed_sessions"] is not None else "NOT SUPPLIED")
        + "; open PRs: "
        + (f"{pop['open_prs_observed']}" if pop["open_prs_observed"] is not None
           else "NOT SUPPLIED")
        + "; PRs with check runs: "
        + (f"{pop['prs_with_checks']}" if pop["prs_with_checks"] is not None
           else "NOT SUPPLIED")
        + ".")

    if result["contradicted"]:
        out.append("")
        out.append(f"⚠️ {len(result['contradicted'])} row(s) CONTRADICTED — a "
                   f"register asserts live work and the live read disagrees:")
        for r in result["contradicted"]:
            out.append(f"   {r['session_id']}  claim={r['registry_claim']}/"
                       f"{r['checklist_state']}  live={r['live']}"
                       + (f"  ({','.join(r['live_tokens'])})" if r["live_tokens"] else "")
                       + f"  — {r['title'][:60]}")

    if result["unregistered_live"]:
        out.append("")
        out.append(f"⚠️ {len(result['unregistered_live'])} live session(s) appear "
                   f"in NO registry row, so they cannot be shown as a table row "
                   f"at all: {', '.join(result['unregistered_live'])}")
    elif result["unregistered_live"] is None:
        out.append("")
        out.append("⚠️ unregistered-session check: NOT OBSERVED — no live session "
                   "list was supplied, so whether the registry has lost a running "
                   "session is unestablished. This is not 'none lost'.")

    if any(r["ci"] == NO_CHECKS for r in result["rows"]):
        out.append("")
        out.append("ℹ️ `no_checks` means the check-run payload was READ and holds "
                   "zero runs — NOT that CI passed and NOT that it was not looked "
                   "at. A relay commit landing last does this (GitHub fires no "
                   "workflows for a GITHUB_TOKEN push): push an ordinary commit "
                   "to trigger the required checks. ⚠️ Read CI with "
                   "`get_check_runs`; `get_status` returns legacy commit statuses "
                   "and reads total_count=0 on healthy green PRs.")

    if pop["inputs_missing"]:
        out.append("")
        out.append("⚠️ INPUTS NOT SUPPLIED (their columns read `not_observed`): "
                   + ", ".join(pop["inputs_missing"]))
    out.append("")
    out.append(f"manager-view: verdict={result['verdict']}")
    out.append(f"manager-view: {_ADVICE[result['verdict']]}")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Planted-failure suite. Both directions on every state: a planted divergence
# must FIRE and a clean input must stay QUIET. One direction proves a check
# runs, never that it discriminates.
# --------------------------------------------------------------------------- #
def _self_test() -> int:
    ok = True

    def check(label: str, got: Any, want: Any) -> None:
        nonlocal ok
        good = got == want
        ok &= good
        print(f"  self-test ({label}): "
              f"{'PASS' if good else f'FAIL got={got!r} want={want!r}'}")

    now = datetime(2026, 9, 3, 6, 0, tzinfo=timezone.utc)
    SID = "session_01AAAAAAAAAAAAAAAAAAAA"
    reg = {"sessions": [{"session_id": SID, "state": "working", "title": "t",
                         "prs": [f"{DEFAULT_REPO}#10877"],
                         "branches": [f"{DEFAULT_REPO}:claude/x"]}]}
    ck = {"items": [{"id": "MI-89", "state": "in_flight", "owner": SID,
                     "prs": [10877]}]}
    prs = [{"number": 10877, "head_ref": "claude/x", "title": "t", "draft": True}]

    # --- the verdict register mirrors handoff_check, and UNKNOWN is not a pass -
    check("all inputs, nothing divergent -> clear", grade([], [], [], []), CLEAR)
    check("a contradiction -> attention", grade([], [], [{"x": 1}], []), ATTENTION)
    check("a missing input with no finding -> unknown, NEVER clear",
          grade([], ["open-prs"], [], []), UNKNOWN)
    check("a FINDING dominates a missing input (a known divergence is definite)",
          grade([], ["open-prs"], [{"x": 1}], []), ATTENTION)
    check("an unregistered LIVE session is itself a finding",
          grade([], [], [], ["session_01ZZ"]), ATTENTION)

    # --- THE HEADLINE DISEASE: a register saying `working` over an idle read ---
    live_idle = [{"session_id": SID, "status": "idle"}]
    r = derive(reg, True, ck, True, live_idle, prs, {10877: "success"}, now)
    check("A REGISTRY `working` OVER A LIVE `idle` IS CONTRADICTED — the "
          "measured disease (17 of 17 inherited rows wrong)",
          r["rows"][0]["agreement"], "contradicted")
    check("...and it drives the verdict to attention", r["verdict"], ATTENTION)
    live_run = [{"session_id": SID, "session_status": "SESSION_STATUS_RUNNING"}]
    r2 = derive(reg, True, ck, True, live_run, prs, {10877: "success"}, now)
    check("...while a genuinely RUNNING session agrees, so the check "
          "discriminates rather than always firing",
          r2["rows"][0]["agreement"], "agrees")
    check("...and that verdict is clear", r2["verdict"], CLEAR)
    check("an ARCHIVED session under a `working` claim is contradicted too",
          derive(reg, True, ck, True, [{"session_id": SID, "status": "archived"}],
                 prs, {10877: "success"}, now)["rows"][0]["agreement"],
          "contradicted")
    check("a session the registry claims live and the live list does NOT carry "
          "is `none_found`, never `running`",
          derive(reg, True, ck, True, [], prs, {10877: "success"},
                 now)["rows"][0]["live"], NONE_FOUND)

    # --- WE DID NOT LOOK is never a pass, per column ---------------------------
    r3 = derive(reg, True, ck, True, None, prs, {10877: "success"}, now)
    check("NO live observation -> the live column is not_observed",
          r3["rows"][0]["live"], NOT_OBSERVED)
    check("...the agreement column too — silence is not agreement",
          r3["rows"][0]["agreement"], NOT_OBSERVED)
    check("...and the verdict is unknown, NEVER clear", r3["verdict"], UNKNOWN)
    check("an observation entry with NO status is not_observed, not `running`",
          derive(reg, True, ck, True, [{"session_id": SID}], prs, None,
                 now)["rows"][0]["live"], NOT_OBSERVED)
    check("NO open-PR observation -> the PR column is not_observed",
          derive(reg, True, ck, True, live_run, None, None,
                 now)["rows"][0]["pr"], NOT_OBSERVED)
    check("NO check-run observation -> the CI column is not_observed, never green",
          derive(reg, True, ck, True, live_run, prs, None,
                 now)["rows"][0]["ci"], NOT_OBSERVED)
    check("an UNREADABLE registry yields no rows and cannot be clear",
          derive(None, False, ck, True, live_run, prs, {10877: "success"},
                 now)["verdict"], UNKNOWN)
    check("...and it does NOT report every live session as unregistered — with "
          "nothing to be absent FROM, that finding would be a parse error "
          "wearing a finding's clothes",
          derive(None, False, ck, True, live_run, prs, {10877: "success"},
                 now)["unregistered_live"], None)
    check("an UNREADABLE checklist leaves the item column not_observed",
          derive(reg, True, None, False, live_run, prs, {10877: "success"},
                 now)["rows"][0]["checklist_item"], NOT_OBSERVED)
    check("no live list -> the unregistered-session census is None (not zero)",
          derive(reg, True, ck, True, None, prs, None, now)["unregistered_live"],
          None)

    # --- CI: `no_checks` is its own state, and that is the 2026-09-03 trap -----
    check("ZERO check runs is `no_checks`, NOT success", rollup_check_runs(
        {"check_runs": []}), NO_CHECKS)
    check("...and NOT not_observed either — the payload WAS read",
          rollup_check_runs({"check_runs": []}) == NOT_OBSERVED, False)
    check("an unreadable check payload IS not_observed",
          rollup_check_runs(None), NOT_OBSERVED)
    check("all green rolls up success", rollup_check_runs({"check_runs": [
        {"status": "completed", "conclusion": "success"},
        {"status": "completed", "conclusion": "success"}]}), "success")
    check("ONE failure among nine greens rolls up failure, never the majority",
          rollup_check_runs({"check_runs": [
              {"status": "completed", "conclusion": "success"}] * 9 + [
              {"status": "completed", "conclusion": "failure"}]}), "failure")
    check("an in-progress run rolls up pending, not success",
          rollup_check_runs({"check_runs": [
              {"status": "completed", "conclusion": "success"},
              {"status": "in_progress", "conclusion": None}]}), "pending")
    check("a failure OUTRANKS a still-running check — it is already known bad",
          rollup_check_runs({"check_runs": [
              {"status": "completed", "conclusion": "failure"},
              {"status": "in_progress", "conclusion": None}]}), "failure")
    check("a PR with no checks shows no_checks in the table",
          derive(reg, True, ck, True, live_run, prs,
                 {10877: NO_CHECKS}, now)["rows"][0]["ci"], NO_CHECKS)
    check("a row with NO open PR shows no_pr, not no_checks (a category error)",
          derive({"sessions": [{"session_id": SID, "state": "working"}]}, True,
                 {"items": []}, True, live_run, [], None, now)["rows"][0]["ci"],
          NO_PR)

    # --- cross-repo references are NOT compared against this repo's PR list ----
    check("a repo-qualified ref for ANOTHER repo is not this repo's",
          parse_pr_ref("ict-trader-dashboard#210"), (210, False))
    check("...and one for this repo is", parse_pr_ref(f"{DEFAULT_REPO}#10877"),
          (10877, True))
    check("a bare int is assumed ours", parse_pr_ref(10877), (10877, True))
    foreign_reg = {"sessions": [{"session_id": SID, "state": "working",
                                 "prs": ["ict-trader-dashboard#210"]}]}
    check("a session whose only PR is in another repo reads `other_repo`, never "
          "`closed_or_merged` — comparing it would manufacture a finding",
          derive(foreign_reg, True, {"items": []}, True, live_run, prs, None,
                 now)["rows"][0]["pr"], OTHER_REPO)

    # --- the PR join, both directions -----------------------------------------
    check("a recorded PR that IS open is reported open",
          derive(reg, True, ck, True, live_run, prs, None, now)["rows"][0]["pr"],
          [10877])
    check("a recorded PR that is NOT in the open list reads closed_or_merged",
          derive(reg, True, ck, True, live_run, [], None,
                 now)["rows"][0]["pr"], "closed_or_merged")
    branch_only = {"sessions": [{"session_id": SID, "state": "working",
                                 "branches": [f"{DEFAULT_REPO}:claude/x"]}]}
    check("with no recorded PR, the BRANCH matches an open PR's head ref",
          derive(branch_only, True, {"items": []}, True, live_run, prs, None,
                 now)["rows"][0]["pr"], [10877])
    noted = {"sessions": [{"session_id": SID, "state": "working", "branches": [
        f"{DEFAULT_REPO}:claude/x (expected; not yet pushed)"]}]}
    check("...and a branches entry carrying a parenthetical note still matches, "
          "since 61 of 82 real rows write them that way",
          derive(noted, True, {"items": []}, True, live_run, prs, None,
                 now)["rows"][0]["pr"], [10877])

    # --- the checklist join ----------------------------------------------------
    check("an item owned by this session is joined",
          derive(reg, True, ck, True, live_run, prs, None,
                 now)["rows"][0]["checklist_item"], "MI-89")
    check("an ABBREVIATED owner id still matches (prefix-tolerant)",
          derive(reg, True, {"items": [{"id": "MI-89", "state": "in_flight",
                                        "owner": SID[:20]}]}, True, live_run,
                 prs, None, now)["rows"][0]["checklist_item"], "MI-89")
    check("a prose owner naming no session matches nothing",
          derive(reg, True, {"items": [{"id": "MI-01", "state": "in_flight",
                                        "owner": "manager (SHOULD HAVE BEEN "
                                                 "DELEGATED)"}]}, True, live_run,
                 prs, None, now)["rows"][0]["checklist_state"], NONE_FOUND)

    # --- age is measured, never fabricated -------------------------------------
    aged = [{"session_id": SID, "session_status": "SESSION_STATUS_RUNNING",
             "updated_at": "2026-09-03T03:00:00Z"}]
    check("age is derived from the live row's own timestamp",
          derive(reg, True, ck, True, aged, prs, None, now)["rows"][0]["age_hours"],
          3.0)
    check("...and a live row with NO timestamp reports none_found, never 0.0 "
          "(a fabricated zero is the opposite claim)",
          derive(reg, True, ck, True, live_run, prs, None,
                 now)["rows"][0]["age_hours"], NONE_FOUND)
    check("...and with no observation at all, not_observed",
          derive(reg, True, ck, True, None, prs, None,
                 now)["rows"][0]["age_hours"], NOT_OBSERVED)

    # --- an unregistered live session cannot be a row, so it is named ----------
    r4 = derive(reg, True, ck, True,
                [{"session_id": SID, "status": "idle"},
                 {"session_id": "session_01ZZZZZZZZZZZZZZZZZZZZ", "status": "running"}],
                prs, None, now)
    check("a LIVE session absent from the registry is reported separately",
          r4["unregistered_live"], ["session_01ZZZZZZZZZZZZZZZZZZZZ"])
    check("...and forces attention", r4["verdict"], ATTENTION)

    # --- only_active, and the population that goes with it ---------------------
    dormant = {"sessions": [{"session_id": SID, "state": "idle"}]}
    check("a row asserting NO live work is hidden by default",
          len(derive(dormant, True, {"items": []}, True, live_run, prs, None,
                     now)["rows"]), 0)
    check("...but is still derived and counted, never dropped from the population",
          derive(dormant, True, {"items": []}, True, live_run, prs, None,
                 now)["population"]["rows_derived"], 1)
    check("--all shows it", len(derive(dormant, True, {"items": []}, True,
                                       live_run, prs, None, now,
                                       only_active=False)["rows"]), 1)

    # --- normalisers ------------------------------------------------------------
    check("an unparseable open-PR observation is None, not an empty list",
          normalise_prs("not json"), None)
    check("an EMPTY open-PR list is also None — 'we could not read it' and "
          "'nothing is open' must not share a value here",
          normalise_prs([]), None)
    check("a list_pull_requests-shaped payload parses",
          normalise_prs([{"number": 1, "head": {"ref": "claude/a"},
                          "title": "t", "draft": False}]),
          [{"number": 1, "head_ref": "claude/a", "title": "t", "draft": False}])
    check("a wrapper dict is descended",
          (normalise_prs({"pull_requests": [{"number": 2, "head": {"ref": "b"}}]})
           or [{}])[0]["number"], 2)
    check("a check map keyed by string PR number parses",
          normalise_checks({"10877": {"check_runs": [
              {"status": "completed", "conclusion": "success"}]}}),
          {10877: "success"})
    check("a list of {pr, check_runs} entries parses too",
          normalise_checks([{"pr": 1, "check_runs": []}]), {1: NO_CHECKS})
    check("a garbage check payload is None, never an empty map",
          normalise_checks("nope"), None)

    # --- the timestamp harvest, which the first live run needed -------------
    raw = {"ccr": {"data": [{"id": SID, "session_status": "SESSION_STATUS_IDLE",
                             "updated_at": "2026-09-03T03:00:00Z",
                             "created_at": "2026-09-01T00:00:00Z"}]}}
    check("a timestamp is harvested through the ccr/data wrapper the MCP returns",
          timestamp_index(raw), {SID: "2026-09-03T03:00:00Z"})
    check("...preferring updated_at over created_at (time since anything happened)",
          timestamp_index(raw)[SID].startswith("2026-09-03"), True)
    check("...falling back to created_at when nothing newer exists",
          timestamp_index({"data": [{"id": SID,
                                     "created_at": "2026-09-01T00:00:00Z"}]}),
          {SID: "2026-09-01T00:00:00Z"})
    check("a row with NO timestamp yields no entry, never a fabricated one",
          timestamp_index({"data": [{"id": SID}]}), {})
    check("a non-session id is not harvested (the key-name false-positive class "
          "session_registry measured at 3 of 32)",
          timestamp_index({"session_context": {"id": "not_a_session",
                                               "updated_at": "2026-09-03T03:00:00Z"}}),
          {})
    check("microsecond precision parses, which is what list_sessions emits",
          _parse_ts("2026-09-03T05:55:18.986187Z") is not None, True)

    # --- the renderer actually SHOWS the finding --------------------------------
    txt = render(derive(reg, True, ck, True, live_idle, prs, {10877: NO_CHECKS}, now))
    check("the table names the contradicted session", SID in txt, True)
    check("...marks it contradicted", "contradicted" in txt, True)
    check("...states its population", "POPULATION:" in txt, True)
    check("...and explains no_checks rather than leaving it cryptic",
          "GITHUB_TOKEN push" in txt, True)
    txt2 = render(derive(reg, True, ck, True, None, None, None, now))
    check("a run with no observations says which inputs were missing",
          "INPUTS NOT SUPPLIED" in txt2, True)
    check("...and never prints `clear`", "verdict=clear" in txt2, False)

    print("manager-view self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--live-sessions", default=None,
                    help="`list_sessions` output, or '-' for stdin. WITHOUT IT "
                         "every live column reads not_observed and the verdict "
                         "can never be `clear`. Only a session holding the MCP "
                         "tool can produce it — CI cannot.")
    ap.add_argument("--open-prs", default=None,
                    help="live open-PR list (list_pull_requests / `gh pr list "
                         "--json number,headRefName,title,isDraft`).")
    ap.add_argument("--check-runs", default=None,
                    help="a {\"<pr>\": <get_check_runs output>} map, or a list of "
                         "{pr, check_runs}. ⚠️ Use get_check_runs, NOT get_status "
                         "— get_status returns legacy commit statuses and reads "
                         "total_count=0 on healthy green PRs.")
    ap.add_argument("--repo", default=DEFAULT_REPO,
                    help="repo that bare PR numbers belong to (default: "
                         f"{DEFAULT_REPO}). Refs qualified with another repo are "
                         "reported `other_repo`, never compared.")
    ap.add_argument("--all", action="store_true",
                    help="show every registry row, not only those asserting live work")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()

    reg, reg_ok = sr.read_json(sr.REGISTRY_PATH)
    ck, ck_ok = sr.read_json(sr.CHECKLIST_PATH)
    obs_raw = _load(a.live_sessions)
    observation = sr.normalise_observation(obs_raw) if obs_raw is not None else None
    # ⚠️ A supplied-but-unreadable observation must NOT become an empty one.
    if obs_raw is not None and not observation:
        print("manager-view: an observation was supplied and no session entry "
              "could be read out of it. An unparseable observation is not an "
              "empty one — refusing to read it as 'nothing is running'.",
              file=sys.stderr)
        observation = None
    # ⚠️ Merged in from the RAW payload, because the shared normaliser drops
    # timestamps by design. Without this the age column is `none_found` for
    # every row while the observation plainly carries the answer.
    if observation is not None and obs_raw is not None:
        stamps = timestamp_index(obs_raw)
        for entry in observation:
            ts = stamps.get(entry.get("session_id", ""))
            if ts:
                entry["updated_at"] = ts
    prs = normalise_prs(_load(a.open_prs)) if a.open_prs else None
    checks = normalise_checks(_load(a.check_runs)) if a.check_runs else None

    res = derive(reg, reg_ok, ck, ck_ok, observation, prs, checks,
                 only_active=not a.all, repo=a.repo)
    print(render(res))
    if a.json:
        print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
    return _EXIT[res["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
