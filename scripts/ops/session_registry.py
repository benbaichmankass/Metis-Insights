#!/usr/bin/env python3
"""THE SUB-SESSION REGISTRY — registering a spawn, and DETECTING one that was
never registered.

WHY THIS EXISTS — A MEASURED, TWICE-REPEATED FAILURE, NOT A HYPOTHETICAL
-----------------------------------------------------------------------
`docs/claude/work/SESSIONS.json` is the ONLY thing a manager arriving COLD can
read to pick up the sub-sessions its predecessor spawned. A session that is not
in it is, to a successor, a session that does not exist.

  * 2026-09-01 (`MI-15-SESSIONS-REGISTRY-INCOMPLETE`): **3 of 6** spawned
    sub-sessions were absent from the registry. Remedy applied: *remember to
    register.* Filed `landed_unproven`.
  * 2026-09-02T05:56Z: **6 of 9** absent, **5 of them LIVE**, including all
    three sessions carrying the cycle's highest-priority work. The MI-15 row was
    still sitting at `landed_unproven` while it happened again, worse.

**"Remember to register" has now failed twice.** The moment a manager spawns a
session is exactly the moment it is least likely to stop and write a record —
so the remedy cannot be another reminder.

WHY SPAWNING AND REGISTERING CANNOT BE MADE ONE ATOMIC ACTION
-------------------------------------------------------------
⚠️ Stated plainly rather than worked around: **the repo does not own the spawn.**
A sub-session is created by the `create_session` MCP tool, which lives in the
Claude Code Remote MCP server. Nothing in this repository is on that call path —
there is no hook, no wrapper and no interposition point — so no code here can
make the registry write happen *as part of* the spawn. Claiming otherwise would
be a mechanism that looks like a coupling and is not one.

What IS available, and what this module ships, is three things in descending
strength:

1. **PUT THE REGISTRY ON THE PATH TO THE THING THE MANAGER ACTUALLY WANTS.**
   `register` writes the row **and prints the spawn prompt**. The prompt is not
   optional — a sub-session cannot run correctly without one — so the cheapest
   route to a correct spawn now goes *through* the registry rather than around
   it. This is a real coupling in the direction that matters (you cannot obtain
   the artifact without leaving the record) and it is still bypassable by
   writing the prompt by hand. It is therefore necessary and NOT sufficient,
   which is why (2) and (3) exist.

2. **AN OFFLINE DETECTOR THAT RUNS IN CI ON EVERY PR** — `cross_check`. The
   manager already records a session id in `MANAGER-CHECKLIST.json::items[].owner`
   when it assigns work. That is a SECOND, INDEPENDENT record of the same fact,
   written at a different moment for a different reason. An owner id that
   appears nowhere in the registry is a session the registry has lost, and the
   whole comparison is two file reads — no MCP, no network, no live state.
   ⚠️ **PARTIAL BY CONSTRUCTION, and that is not a defect to be hidden:** a
   session never written into the checklist either is invisible to this
   detector too. It catches the overlap of two incomplete records, which is
   strictly more than the zero either caught alone.

3. **A LIVE DETECTOR THE MANAGER RUNS** — `reconcile`. Only a session holding
   the `list_sessions` MCP tool can enumerate what is actually running, and CI
   holds no such tool. So this one cannot be scheduled; what forces it is that
   `handoff_check.py` **cannot return `ready` without it** (`not_observed`
   grades the handoff `unknown`). The verdict is the enforcement.

STATES, NEVER COLLAPSED
-----------------------
`reconcile` (live):
  ``reconciled``    every observed live session appears in the registry.
  ``unregistered``  one or more do not. THE FINDING.
  ``not_observed``  no observation was supplied. ⚠️ **WE DID NOT LOOK.**
                    Emphatically NOT `reconciled` — a registry nobody compared
                    against anything is not a clean registry, and grading it
                    clean is the exact shape this repo files under collapsed
                    states.
  ``unreadable``    the registry could not be parsed. Also not `reconciled`.

`cross_check` (offline):
  ``consistent``          every owner id the checklist names is in the registry.
  ``owner_unregistered``  at least one is not. THE FINDING.
  ``no_owners``           the checklist named no session id at all — again
                          *we did not look*, never "nothing is missing".
  ``unreadable``          either file could not be parsed.

MEASURED AT `main` 550c9f6d (population: all 42 items in MANAGER-CHECKLIST.json
against all 30 rows in SESSIONS.json): **5 owner session ids appear nowhere in
the registry**, 2 of them on items whose state is `in_flight`. Both `in_flight`
ones were then polled with `get_session` rather than assumed:
`session_01PnQLKnuezWVB4D47PtnTqR` (Phase H, the control half) came back
**IDLE with three unactioned `needs_action` items** — live work a handoff would
have dropped — and `session_01LqVvgvusnN87neBn5ayzhg` came back ARCHIVED/FAILED,
matching the checklist's own note. So the detector's first run found a real live
orphan by a route entirely independent of the count that motivated it.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = REPO_ROOT / "docs" / "claude" / "work" / "SESSIONS.json"
CHECKLIST_PATH = REPO_ROOT / "docs" / "claude" / "work" / "MANAGER-CHECKLIST.json"

#: A session id as the platform mints them. Used both to VALIDATE a row and to
#: HARVEST ids out of the checklist's free-text `owner` field, which is prose as
#: often as it is a bare id ("session_01Lq… (archived; its PR #10694 stands)").
SESSION_ID_RE = re.compile(r"session_[A-Za-z0-9]{6,}")
_STRICT_ID_RE = re.compile(r"\Asession_[A-Za-z0-9]{6,}\Z")

#: ⚠️ A SEPARATE, STRICTER pattern for harvesting ids out of FREE TEXT, and the
#: difference is a measured false-positive class rather than fussiness. The first
#: live run of `reconcile` against real `list_sessions` output reported
#: `session_context`, `session_status` and `session_inbound` as unregistered
#: sessions: the loose pattern had matched JSON KEY NAMES (`session_context`,
#: `cross_session_inbound`). 3 of 32 reported findings were nonexistent — an
#: alarm wrong 9% of the time on its first run is one nobody reads.
#: Real ids are `session_` + 22 mixed-case alphanumerics, so requiring >=16 and
#: at least one DIGIT excludes every all-lowercase-word key while excluding no
#: real id. Applied ONLY to the text fallback; a structured observation is
#: matched exactly and needs no heuristic.
_TEXT_HARVEST_RE = re.compile(r"session_(?=[A-Za-z0-9]{16,}\b)(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{16,}")

#: Checklist states whose owner MUST be registered. `in_flight` is the
#: population where losing a session costs live work; a `done` item's session is
#: finished and orphaning it costs nothing. Deliberately narrow — a guard that
#: fails on rows nobody can act on is the wall this repo has already paid for.
DEFAULT_ENFORCED_STATES: Tuple[str, ...] = ("in_flight",)

#: Statuses (as `list_sessions`/`get_session` report them) under which a session
#: can no longer be orphaned. ⚠️ Matched only when the status is KNOWN: an entry
#: with no status is counted as LIVE, because the fail-safe direction for an
#: alarm about LOSING work is to alarm.
TERMINAL_STATUSES = {"archived", "session_status_archived"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Tuple[Optional[Any], bool]:
    """Returns (parsed, readable). ``(None, True)`` means genuinely absent."""
    if not path.is_file():
        return None, True
    try:
        return json.loads(path.read_text(encoding="utf-8")), True
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None, False


def registry_rows(doc: Optional[Any]) -> List[Dict[str, Any]]:
    if not isinstance(doc, dict):
        return []
    rows = doc.get("sessions")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def registry_ids(rows: Iterable[Dict[str, Any]]) -> List[str]:
    out = []
    for r in rows:
        sid = r.get("session_id")
        if isinstance(sid, str) and sid.strip():
            out.append(sid.strip())
    return out


def id_known(candidate: str, known: Sequence[str]) -> bool:
    """Is ``candidate`` one of the registered ids?

    ⚠️ PREFIX-TOLERANT, and this is load-bearing rather than lax. The checklist
    abbreviates ids in prose (``session_012zFXi2`` for
    ``session_012zFXi272Uywe4vzXsr7Jfi``). Treating an abbreviation as a
    different session manufactures findings — measured: it inflated the
    2026-09-02 census from 5 to 7, and a detector that cries wolf twice out of
    seven is one nobody reads. Tolerance runs ONE WAY ONLY (a registered id may
    extend the candidate, never the reverse), so a genuinely unknown id can
    never be swallowed by a shorter registered one.
    """
    return any(k == candidate or k.startswith(candidate) for k in known)


class Verdict(dict):
    """A graded result. A dict so it serialises for free; attribute-ish access
    is deliberately NOT added — callers read ``v["state"]`` so the state is
    never mistaken for a boolean."""

    @property
    def state(self) -> str:
        return str(self.get("state"))


def _v(state: str, message: str, **extra: Any) -> Verdict:
    return Verdict(state=state, message=message, **extra)


# --------------------------------------------------------------------------- #
# (2) THE OFFLINE DETECTOR — two repo files, no MCP, no network.
# --------------------------------------------------------------------------- #
def cross_check(reg_doc: Optional[Any], reg_readable: bool,
                ck_doc: Optional[Any], ck_readable: bool,
                enforced_states: Sequence[str] = DEFAULT_ENFORCED_STATES) -> Verdict:
    """Every session id the CHECKLIST names as an owner should be in the REGISTRY.

    ``enforced_states`` selects which checklist items *count as findings*; every
    item is still CENSUSED, so the guard's narrow enforcement never hides the
    wider number (the unstated-denominator error this repo files under
    diagnostic provenance).
    """
    if not reg_readable or not ck_readable:
        which = ", ".join(n for n, ok in (("SESSIONS.json", reg_readable),
                                          ("MANAGER-CHECKLIST.json", ck_readable)) if not ok)
        return _v("unreadable",
                  f"could not parse {which}. WE DID NOT LOOK — this is not evidence "
                  f"that every owner is registered.")

    known = registry_ids(registry_rows(reg_doc))
    items = ck_doc.get("items") if isinstance(ck_doc, dict) else None
    items = [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []

    census: List[Dict[str, Any]] = []
    owners_seen = 0
    for it in items:
        owner = it.get("owner")
        if not isinstance(owner, str):
            continue
        for sid in SESSION_ID_RE.findall(owner):
            owners_seen += 1
            if id_known(sid, known):
                continue
            census.append({
                "session_id": sid,
                "checklist_item": it.get("id"),
                "item_state": it.get("state"),
                "owner_field": owner,
            })

    if owners_seen == 0:
        return _v("no_owners",
                  "the checklist names no session id in any `owner` field, so this "
                  "detector had nothing to compare. WE DID NOT LOOK — never read "
                  "this as 'nothing is missing'.",
                  census=[], findings=[], population={"items": len(items),
                                                      "registered": len(known),
                                                      "owner_mentions": 0})

    findings = [c for c in census if c.get("item_state") in set(enforced_states)]
    pop = {"items": len(items), "registered": len(known),
           "owner_mentions": owners_seen, "absent_total": len(census),
           "absent_enforced": len(findings), "enforced_states": list(enforced_states)}

    if findings:
        return _v("owner_unregistered",
                  f"{len(findings)} session id(s) on {'/'.join(enforced_states)} "
                  f"checklist item(s) appear nowhere in the registry "
                  f"({len(census)} absent across all states, of {owners_seen} owner "
                  f"mentions over {len(items)} items against {len(known)} registered "
                  f"rows). A successor reading the registry cannot see this work.",
                  census=census, findings=findings, population=pop)
    if census:
        return _v("consistent",
                  f"every owner on a {'/'.join(enforced_states)} item is registered. "
                  f"⚠️ {len(census)} owner id(s) on OTHER states are still absent — "
                  f"censused, not enforced.",
                  census=census, findings=[], population=pop)
    return _v("consistent",
              f"all {owners_seen} owner mention(s) across {len(items)} checklist "
              f"items resolve to registered sessions.",
              census=[], findings=[], population=pop)


# --------------------------------------------------------------------------- #
# (3) THE LIVE DETECTOR — needs an observation the repo cannot make for itself.
# --------------------------------------------------------------------------- #
def normalise_observation(raw: Any) -> List[Dict[str, Any]]:
    """Accept what `list_sessions` plausibly hands back, without guessing.

    Tolerated shapes: a list of id strings; a list of dicts carrying
    ``id``/``session_id``; a dict wrapping either under ``sessions``/``data``.
    Anything else yields no entries — and the CALLER must treat an empty
    observation as ``not_observed`` rather than as "nothing is running".
    """
    # Descend wrapper objects, including the MCP transport's own `{"ccr": {...}}`
    # envelope. Bounded depth: a listing is at most a couple of wrappers deep,
    # and an unbounded walk would happily "find" a list that is not the sessions.
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
    out = []
    for e in raw:
        if isinstance(e, str) and _STRICT_ID_RE.match(e.strip()):
            out.append({"session_id": e.strip()})
        elif isinstance(e, dict):
            inner = e.get("ccr") if isinstance(e.get("ccr"), dict) else e
            sid = inner.get("id") or inner.get("session_id")
            if isinstance(sid, str) and sid.strip():
                out.append({
                    "session_id": sid.strip(),
                    "status": str(inner.get("session_status")
                                  or inner.get("status") or "").lower() or None,
                    "parent_session_id": inner.get("parent_session_id"),
                    "tags": inner.get("tags") if isinstance(inner.get("tags"), list) else None,
                    "title": inner.get("title"),
                })
    return out


def reconcile(reg_doc: Optional[Any], reg_readable: bool,
              observation: Optional[Any],
              manager_session_id: Optional[str] = None) -> Verdict:
    """Compare a LIVE observation of running sessions against the registry."""
    if not reg_readable:
        return _v("unreadable",
                  "SESSIONS.json could not be parsed. WE DID NOT LOOK; this is not "
                  "a clean registry.")
    if observation is None:
        return _v("not_observed",
                  "no live-session observation was supplied, so nothing was "
                  "compared. ⚠️ WE DID NOT LOOK. Only a session holding the "
                  "`list_sessions` MCP tool can produce this observation — CI "
                  "cannot — so pass it with --live-sessions. This is NOT "
                  "`reconciled`.")

    entries = normalise_observation(observation)
    if not entries:
        return _v("not_observed",
                  "an observation was supplied and no session entry could be read "
                  "out of it. An unparseable observation is not an empty one — "
                  "refusing to read it as 'nothing is running'.")

    known = registry_ids(registry_rows(reg_doc))
    unregistered, excluded_terminal, excluded_foreign, excluded_self = [], 0, 0, 0
    for e in entries:
        sid = e["session_id"]
        if manager_session_id and sid == manager_session_id:
            excluded_self += 1
            continue
        status = (e.get("status") or "")
        if status and status in TERMINAL_STATUSES:
            excluded_terminal += 1
            continue
        parent = e.get("parent_session_id")
        # ⚠️ Parentage filters ONE WAY. A session whose parent is a DIFFERENT
        # manager is not ours to register. A session with parent UNKNOWN is kept,
        # because an unattributable running session is precisely the dangerous
        # one — the fail-safe direction for an alarm about lost work is to alarm.
        if manager_session_id and isinstance(parent, str) and parent and parent != manager_session_id:
            excluded_foreign += 1
            continue
        if not id_known(sid, known):
            unregistered.append(e)

    pop = {"observed": len(entries), "registered_rows": len(known),
           "excluded_self": excluded_self, "excluded_terminal": excluded_terminal,
           "excluded_foreign": excluded_foreign,
           "graded": len(entries) - excluded_self - excluded_terminal - excluded_foreign}
    if unregistered:
        return _v("unregistered",
                  f"{len(unregistered)} LIVE session(s) appear nowhere in the "
                  f"registry, of {pop['graded']} graded (observed {pop['observed']}; "
                  f"excluded self {excluded_self}, terminal {excluded_terminal}, "
                  f"other-manager {excluded_foreign}). A handoff right now loses "
                  f"them.",
                  unregistered=unregistered, population=pop)
    return _v("reconciled",
              f"all {pop['graded']} graded live session(s) are registered "
              f"(observed {pop['observed']}).",
              unregistered=[], population=pop)


# --------------------------------------------------------------------------- #
# Structural integrity of the registry itself — cheap, offline, always-on.
# --------------------------------------------------------------------------- #
def structural(reg_doc: Optional[Any], reg_readable: bool) -> Verdict:
    if not reg_readable:
        return _v("unreadable", "SESSIONS.json could not be parsed.", findings=[])
    rows = registry_rows(reg_doc)
    findings, seen = [], {}
    for i, r in enumerate(rows):
        sid = r.get("session_id")
        if not isinstance(sid, str) or not sid.strip():
            # A pending row is the ONE legitimate id-less row: it records a spawn
            # that was planned before the platform minted an id.
            if str(r.get("state", "")).strip().lower() == "spawn_pending" and r.get("registry_key"):
                continue
            findings.append({"row": i, "why": "no session_id, and not a spawn_pending row",
                             "title": r.get("title")})
            continue
        sid = sid.strip()
        if not _STRICT_ID_RE.match(sid):
            findings.append({"row": i, "why": f"malformed session_id {sid!r}",
                             "title": r.get("title")})
        if sid in seen:
            findings.append({"row": i, "why": f"duplicate session_id {sid!r} "
                                              f"(first at row {seen[sid]})",
                             "title": r.get("title")})
        seen.setdefault(sid, i)
    state = "malformed" if findings else "well_formed"
    return _v(state,
              f"{len(findings)} structural finding(s) over {len(rows)} row(s)."
              if findings else f"{len(rows)} row(s), all well-formed.",
              findings=findings, population={"rows": len(rows)})


def pending_rows(reg_doc: Optional[Any]) -> List[Dict[str, Any]]:
    """Rows written by `register` before the platform minted a session id."""
    return [r for r in registry_rows(reg_doc)
            if str(r.get("state", "")).strip().lower() == "spawn_pending"
            and not (isinstance(r.get("session_id"), str) and r["session_id"].strip())]


# --------------------------------------------------------------------------- #
# (1) THE COUPLING — the registry row is on the path to the spawn prompt.
# --------------------------------------------------------------------------- #
_PROMPT_TEMPLATE = """\
You are a sub-session in the Metis-Insights trading repo, dispatched by the
manager. Repo checked out on `main`. Work on a fresh `claude/**` branch. Do NOT
message the operator directly — the manager relays.

## Your unit
**{title}**

{why}

## Registry
You are registered as `{registry_ref}` in `docs/claude/work/SESSIONS.json`.
If you learn something that changes your row's scope, say so in your PR body —
the manager owns that file.

## Standing rules
- START by reading `docs/CLAUDE-RULES-CANONICAL.md`, the root `CLAUDE.md`, and
  the SKILL.md of whichever skill covers this work.
- ALWAYS STATE THE POPULATION on any quantitative claim.
- Never weaken a guard or a test to get CI green.
- Run `python3 scripts/ci/run_guards.py --base main` AFTER committing; if a tool
  is absent in your container, say which guards you could not run rather than
  reporting them green.
- `issue_write` / `add_issue_comment` / `create_pull_request` MAY 403 for you —
  TRY THEM DIRECTLY FIRST, and fall back to the relays only on an actual refusal.
  ⚠️ This line asserted a flat 403 until 2026-09-02 and that was wrong: it has now
  been measured in BOTH directions on the same day. MI-75 hit
  `Resource not accessible by integration` on `create_pull_request`; MI-77 used
  `create_pull_request` AND `add_issue_comment` with no 403 at all and said so.
  So it is variable, not a property of being a sub-session, and neither reading
  generalises. Assuming the 403 costs a working session a relay round-trip and a
  buried CI run; assuming it works costs one refused call you can recover from —
  which is why the instruction is try-then-fall-back rather than either claim.
- ⚠️ Distinguish a WRITE-SCOPE 403 from the transient GitHub-MCP drop: the scope
  boundary refuses writes while `issue_read` on the SAME object succeeds, and no
  amount of backoff clears it. A drop fails everything and self-heals in seconds.
  Retry once before reaching for a relay; do not build a retry loop around a 403.
- The relays are `.github/workflows/board-post.yml` and
  `.github/workflows/pr-opener.yml`, with a FRESH filename per use (the result
  file is the idempotency key, so a reused name is a silent no-op). Post a board
  START to issue #6927 before your first substantive change, naming your branch
  AND your session id — a 403 is never a reason to skip the board.
- ⚠️ Those relays commit as `github-actions[bot]`, and GitHub fires no workflows
  for `GITHUB_TOKEN` pushes, so if such a commit lands LAST your PR shows ZERO
  checks and reads as blocked, not green. Put board posts on a SEPARATE branch,
  or push an ordinary commit after, to arm CI.
- Open the PR as a DRAFT; the manager merges.
{scope}"""


def spawn_prompt(title: str, why: str, registry_ref: str,
                 scope: Optional[str] = None) -> str:
    return _PROMPT_TEMPLATE.format(
        title=title.strip(), why=(why or "").strip() or "(no rationale recorded)",
        registry_ref=registry_ref,
        scope=f"\n## Scope discipline\n{scope.strip()}\n" if scope else "")


def _dump_registry(doc: Dict[str, Any], path: Path) -> None:
    # Matches the file's existing serialisation exactly (indent=2,
    # ensure_ascii=False, trailing newline). A naive dump would re-encode every
    # non-ASCII line and bury a one-row change in a whole-file diff — the lesson
    # `scripts/ops/backlog_append.py` was written for.
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def register(path: Path, *, title: str, why: str, spawned_by: str,
             session_id: Optional[str] = None, branches: Optional[List[str]] = None,
             owns_object: Optional[str] = None, checklist_item: Optional[str] = None,
             note: Optional[str] = None, registry_key: Optional[str] = None,
             now: Optional[str] = None) -> Tuple[Dict[str, Any], str]:
    """Append one row and return (row, registry_ref).

    ``session_id`` is OPTIONAL because of an ordering fact that cannot be wished
    away: the platform mints the id when `create_session` returns, which is
    AFTER the prompt this command exists to produce. So a row written before the
    spawn carries `state: spawn_pending` plus a `registry_key`, and `confirm`
    fills the id in. ⚠️ A pending row is a WEAKER record than a confirmed one —
    it names the work but cannot be polled — so `handoff_check` refuses to grade
    a handoff `ready` while any pending row is unconfirmed.
    """
    doc, readable = read_json(path)
    if not readable:
        raise SystemExit("session-registry: SESSIONS.json is unparseable — refusing "
                         "to append. Repair it first; an append over a broken file "
                         "would destroy whatever it still holds.")
    if not isinstance(doc, dict) or not isinstance(doc.get("sessions"), list):
        raise SystemExit("session-registry: SESSIONS.json has no `sessions` list.")

    ts = now or _now_iso()
    row: Dict[str, Any] = {}
    if session_id:
        row["session_id"] = session_id
    else:
        row["session_id"] = None
        row["registry_key"] = registry_key or f"pending-{ts.replace(':', '').replace('-', '')}"
        row["state"] = "spawn_pending"
    row["title"] = title
    if owns_object:
        row["owns_object"] = owns_object
    if checklist_item:
        row["checklist_item"] = checklist_item
    row["spawned_at"] = ts
    row["spawned_by"] = spawned_by
    if branches:
        row["branches"] = list(branches)
    if why:
        row["why"] = why
    if note:
        row["note"] = note
    if session_id and "state" not in row:
        row["state"] = "working"

    doc["sessions"].append(row)
    doc["updated_at"] = ts
    doc["updated_by"] = spawned_by
    _dump_registry(doc, path)
    return row, (session_id or row.get("registry_key") or "unregistered")


def confirm(path: Path, *, registry_key: str, session_id: str,
            now: Optional[str] = None) -> Dict[str, Any]:
    doc, readable = read_json(path)
    if not readable or not isinstance(doc, dict):
        raise SystemExit("session-registry: SESSIONS.json is unparseable.")
    for row in registry_rows(doc):
        if row.get("registry_key") == registry_key:
            row["session_id"] = session_id
            row["state"] = "working"
            row["confirmed_at"] = now or _now_iso()
            doc["updated_at"] = row["confirmed_at"]
            _dump_registry(doc, path)
            return row
    raise SystemExit(f"session-registry: no pending row with registry_key "
                     f"{registry_key!r}. Nothing was written.")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _outermost_json(text: str) -> Optional[str]:
    """Slice the outermost JSON value out of a wrapped payload.

    ⚠️ NOT cosmetic. An MCP tool result arrives inside an `<other-session …>`
    envelope, so a plain `json.loads` of the file FAILS and the caller silently
    drops to the free-text fallback — which is exactly how the first live run
    reported three JSON key names as live sessions. Parsing the structure is
    always better than harvesting strings out of it.
    """
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    ends = [i for i in (text.rfind("}"), text.rfind("]")) if i >= 0]
    if not starts or not ends:
        return None
    i, j = min(starts), max(ends) + 1
    return text[i:j] if j > i else None


def _load_observation(spec: Optional[str]) -> Optional[Any]:
    if spec is None:
        return None
    text = sys.stdin.read() if spec == "-" else Path(spec).read_text(encoding="utf-8")
    for candidate in (text, _outermost_json(text)):
        if candidate is None:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    if True:
        # Fall back to harvesting bare ids out of pasted text. Deliberately
        # tolerant: a manager pasting `list_sessions` output should not have to
        # reformat it to be allowed to look.
        ids = sorted(set(_TEXT_HARVEST_RE.findall(text)))
        return ids or None


def cmd_status(a) -> int:
    reg, reg_ok = read_json(REGISTRY_PATH)
    ck, ck_ok = read_json(CHECKLIST_PATH)
    st = structural(reg, reg_ok)
    xc = cross_check(reg, reg_ok, ck, ck_ok,
                     enforced_states=tuple(a.enforce_states.split(",")) if a.enforce_states
                     else DEFAULT_ENFORCED_STATES)
    print(f"session-registry: structural={st['state']} — {st['message']}")
    for f in st.get("findings", []):
        print(f"  ::structural:: row {f['row']}: {f['why']}")
    print(f"session-registry: cross_check={xc['state']} — {xc['message']}")
    for c in xc.get("census", []):
        mark = "FINDING" if c in xc.get("findings", []) else "census "
        print(f"  ::{mark}:: {c['session_id']}  item={c['checklist_item']} "
              f"state={c['item_state']}")
    pend = pending_rows(reg)
    if pend:
        print(f"session-registry: {len(pend)} unconfirmed spawn_pending row(s): "
              + ", ".join(str(p.get('registry_key')) for p in pend))
    # ⚠️ Printed on EVERY run, including a clean one, and that is the point: a
    # green `status` is the moment a reader is most likely to conclude the
    # registry is complete. It is not — this command compares two REPO FILES,
    # so a session written into neither is invisible to it. Saying so only on a
    # finding would leave the quiet case reading as full coverage, which is the
    # unstated-denominator error one level up.
    print("session-registry: ⚠️ this is the OFFLINE half only — it compares "
          "MANAGER-CHECKLIST.json owners against the registry and CANNOT see a "
          "session absent from both. For what is actually RUNNING: "
          "`reconcile --live-sessions <list_sessions output>`.")
    if a.json:
        print(json.dumps({"structural": st, "cross_check": xc,
                          "pending": len(pend)}, indent=2, ensure_ascii=False))
    if not a.strict:
        return 0
    bad = st["state"] != "well_formed" or xc["state"] in {"owner_unregistered", "unreadable"}
    if bad:
        print("::error::session-registry: REFUSED. A session the registry does not "
              "name is, to a manager arriving cold, a session that does not exist "
              "(MI-15, twice).")
    return 1 if bad else 0


def cmd_reconcile(a) -> int:
    reg, reg_ok = read_json(REGISTRY_PATH)
    v = reconcile(reg, reg_ok, _load_observation(a.live_sessions), a.manager_session_id)
    print(f"session-registry: reconcile={v['state']} — {v['message']}")
    for e in v.get("unregistered", []):
        print(f"  ::UNREGISTERED:: {e['session_id']}  {e.get('title') or ''}")
    if a.json:
        print(json.dumps(v, indent=2, ensure_ascii=False))
    return {"reconciled": 0, "unregistered": 1}.get(v["state"], 2)


def cmd_register(a) -> int:
    # ⚠️ THE PRIORITY GATE, AT THE SPAWN CHOKEPOINT. Imported lazily because
    # `spawn_gate` imports THIS module — a top-level import would be circular.
    #
    # There is deliberately NO --force. The escape is an operator-approved
    # `spawn-priority-exception.yaml`, which is visible and argued; a bypass flag
    # is neither, and a gate with a flag beside it is a gate that gets flagged
    # past. `unknown` (an unreadable priority file) does NOT block — a typo must
    # not halt the fleet — but it is printed and is never silent.
    import spawn_gate
    verdict = spawn_gate.grade_spawn(a.owns_object)
    if verdict["state"] != spawn_gate.PERMITTED:
        print(f"session-registry: spawn-gate [{verdict['state'].upper()}] "
              f"{verdict['reason']}")
    if verdict["state"] == spawn_gate.REFUSED:
        print("session-registry: NOTHING WAS WRITTEN and no spawn prompt was "
              "produced. Fix the above, or file the exception, then re-run.")
        return 3

    row, ref = register(
        REGISTRY_PATH, title=a.title, why=a.why or "", spawned_by=a.spawned_by,
        session_id=a.session_id, branches=a.branch or None, owns_object=a.owns_object,
        checklist_item=a.checklist_item, note=a.note)
    print(f"session-registry: registered {ref}")
    print("session-registry: ⚠️ written but NOT committed — a registry entry that "
          "never reaches origin protects no successor.")
    print("\n" + "=" * 72 + "\nSPAWN PROMPT (paste into create_session)\n" + "=" * 72)
    print(spawn_prompt(a.title, a.why or "", ref, a.scope))
    if not a.session_id:
        print("=" * 72)
        print(f"session-registry: then run:  python3 scripts/ops/session_registry.py "
              f"confirm --registry-key {row['registry_key']} --session-id <new id>")
    return 0


def cmd_confirm(a) -> int:
    row = confirm(REGISTRY_PATH, registry_key=a.registry_key, session_id=a.session_id)
    print(f"session-registry: confirmed {a.registry_key} -> {a.session_id} "
          f"({row.get('title')})")
    return 0


def _self_test() -> int:
    ok = True

    def check(label, got, want):
        nonlocal ok
        good = got == want
        ok &= good
        print(f"  self-test ({label}): {'PASS' if good else f'FAIL got={got!r} want={want!r}'}")

    reg = {"sessions": [{"session_id": "session_01AAAAAAAA", "title": "a"},
                        {"session_id": "session_01BBBBBBBB", "title": "b"}]}

    # --- reconcile: BOTH directions, which is what makes it evidence ----------
    clean = [{"id": "session_01AAAAAAAA"}, {"id": "session_01BBBBBBBB"}]
    check("PLANTED unregistered live session -> `unregistered`",
          reconcile(reg, True, clean + [{"id": "session_01ZZZZZZZZ"}])["state"],
          "unregistered")
    check("clean registry -> `reconciled` (the guard is not a wall)",
          reconcile(reg, True, clean)["state"], "reconciled")
    check("NO observation is `not_observed`, NEVER `reconciled`",
          reconcile(reg, True, None)["state"], "not_observed")
    check("an unparseable observation is not read as an empty one",
          reconcile(reg, True, {"nope": 1})["state"], "not_observed")
    check("an unreadable registry is `unreadable`, not `reconciled`",
          reconcile(None, False, clean)["state"], "unreadable")
    check("the manager's OWN id is not an orphan of itself",
          reconcile(reg, True, clean + [{"id": "session_01MGRMGRMGR"}],
                    manager_session_id="session_01MGRMGRMGR")["state"], "reconciled")
    check("an ARCHIVED session cannot be orphaned",
          reconcile(reg, True, clean + [{"id": "session_01ZZZZZZZZ",
                                         "session_status": "SESSION_STATUS_ARCHIVED"}])["state"],
          "reconciled")
    check("a session with UNKNOWN status is graded LIVE (alarms fail-safe)",
          reconcile(reg, True, clean + [{"id": "session_01ZZZZZZZZ"}])["state"],
          "unregistered")
    check("another manager's child is excluded",
          reconcile(reg, True, clean + [{"id": "session_01ZZZZZZZZ",
                                         "parent_session_id": "session_01OTHERMGR"}],
                    manager_session_id="session_01MGRMGRMGR")["state"], "reconciled")
    check("but a child of UNKNOWN parentage still alarms",
          reconcile(reg, True, clean + [{"id": "session_01ZZZZZZZZ"}],
                    manager_session_id="session_01MGRMGRMGR")["state"], "unregistered")
    check("get_session's {'ccr': {...}} envelope is understood",
          reconcile(reg, True, [{"ccr": {"id": "session_01ZZZZZZZZ"}}])["state"],
          "unregistered")

    # --- cross_check: both directions, plus the abbreviation trap -------------
    ck_bad = {"items": [{"id": "MI-01", "state": "in_flight",
                         "owner": "session_01ZZZZZZZZ"}]}
    ck_good = {"items": [{"id": "MI-01", "state": "in_flight",
                          "owner": "session_01AAAAAAAA"}]}
    check("PLANTED unregistered owner on an in_flight item -> finding",
          cross_check(reg, True, ck_bad, True)["state"], "owner_unregistered")
    check("clean checklist -> `consistent`",
          cross_check(reg, True, ck_good, True)["state"], "consistent")
    check("an ABBREVIATED id in prose is NOT a false finding",
          cross_check(reg, True, {"items": [{"id": "MI-01", "state": "in_flight",
                                             "owner": "drain #4 (session_01AAAA) running"}]},
                      True)["state"], "consistent")
    check("prefix tolerance runs ONE WAY — a longer unknown id still alarms",
          cross_check(reg, True, {"items": [{"id": "MI-01", "state": "in_flight",
                                             "owner": "session_01AAAAAAAAXTRA"}]},
                      True)["state"], "owner_unregistered")
    check("a `done` item's owner is CENSUSED but not ENFORCED",
          cross_check(reg, True, {"items": [{"id": "MI-01", "state": "done",
                                             "owner": "session_01ZZZZZZZZ"}]},
                      True)["state"], "consistent")
    check("...and the census still carries it, so the denominator is visible",
          len(cross_check(reg, True, {"items": [{"id": "MI-01", "state": "done",
                                                 "owner": "session_01ZZZZZZZZ"}]},
                          True)["census"]), 1)
    check("a checklist naming NO session id is `no_owners`, not `consistent`",
          cross_check(reg, True, {"items": [{"id": "MI-01", "state": "in_flight",
                                             "owner": "manager"}]}, True)["state"],
          "no_owners")
    check("an unreadable checklist is `unreadable`, not `consistent`",
          cross_check(reg, True, None, False)["state"], "unreadable")

    # --- structural ----------------------------------------------------------
    check("a duplicate session_id is a structural finding",
          structural({"sessions": [{"session_id": "session_01AAAAAAAA"},
                                   {"session_id": "session_01AAAAAAAA"}]},
                     True)["state"], "malformed")
    check("a row with no id at all is a structural finding",
          structural({"sessions": [{"title": "nameless"}]}, True)["state"], "malformed")
    check("...but a spawn_pending row with a registry_key is legitimate",
          structural({"sessions": [{"title": "planned", "state": "spawn_pending",
                                    "registry_key": "pending-x", "session_id": None}]},
                     True)["state"], "well_formed")
    check("a clean registry is well_formed", structural(reg, True)["state"], "well_formed")

    # --- the coupling --------------------------------------------------------
    p = spawn_prompt("T", "W", "session_01AAAAAAAA")
    check("the spawn prompt names the registry reference",
          "session_01AAAAAAAA" in p, True)
    check("the spawn prompt carries the DRAFT-PR rule", "DRAFT" in p, True)

    print("session-registry self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("status", help="structural + offline cross-check (what CI runs)")
    s.add_argument("--strict", action="store_true", help="exit non-zero on a finding")
    s.add_argument("--enforce-states", default=None,
                   help="CSV of checklist states to ENFORCE (default: in_flight)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_status)

    r = sub.add_parser("reconcile", help="compare a LIVE session observation to the registry")
    r.add_argument("--live-sessions", default=None,
                   help="path to list_sessions output, or '-' for stdin. OMITTING IT "
                        "grades `not_observed`, never `reconciled`.")
    r.add_argument("--manager-session-id", default=None)
    r.add_argument("--json", action="store_true")
    r.set_defaults(fn=cmd_reconcile)

    g = sub.add_parser("register", help="append a row AND print the spawn prompt")
    g.add_argument("--title", required=True)
    g.add_argument("--why", default=None)
    g.add_argument("--spawned-by", required=True, help="the manager's session id")
    g.add_argument("--session-id", default=None,
                   help="omit when registering BEFORE the spawn; then use `confirm`")
    g.add_argument("--branch", action="append", default=None)
    g.add_argument("--owns-object", default=None)
    g.add_argument("--checklist-item", default=None)
    g.add_argument("--note", default=None)
    g.add_argument("--scope", default=None, help="scope-discipline text for the prompt")
    g.set_defaults(fn=cmd_register)

    c = sub.add_parser("confirm", help="fill in the id of a spawn_pending row")
    c.add_argument("--registry-key", required=True)
    c.add_argument("--session-id", required=True)
    c.set_defaults(fn=cmd_confirm)

    a = ap.parse_args(argv)
    if a.self_test:
        return _self_test()
    if not a.cmd:
        ap.print_help()
        return 2
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
