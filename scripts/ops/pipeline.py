#!/usr/bin/env python3
# wiring: scripts/ci/run_guards.py::pipeline-guard (--self-test + --check).
# Read by A3 (the daily brief) for section 0 and the section-5 unrouted count.
"""THE FOLLOW-THROUGH PIPELINE — the thing that pulls work to a finish.

WHY THIS EXISTS, in the operator's words (2026-09-21), which are the
acceptance criterion and not a preamble:

    "there's too many things here that get just dropped halfway through, and
    that was definitely one of the problems we were trying to solve and that
    still doesn't seem to have been resolved... not just more CI guards or
    whatever, not just building up the CLAUDE.md -- actually creating infra
    that has a pipeline that pulls things through to the finish."

⚠️ **ARCHIVING THE REGISTERS DID NOT FIX THIS. IT MOVED IT.** The reset removed
the place work rotted and did not build the thing that pulls it through, so
between the reset and this module follow-through was WORSE than before: 1,065
unresolved backlog rows and 91 monitoring rows sat in git history with nothing
reading them at all. Scope of record: docs/plans/OPERATING-PLAN-2026-09-21.md
section 3b.

THE FIVE REASONS THE OLD SYSTEM DROPPED THINGS
-----------------------------------------------
Written here so this module can be CHECKED against them rather than hoped at.
Each is answered by a specific mechanism below, and the self-test asserts the
mechanism rather than the intention.

  (1) FILING WAS FREE; PICKING UP WAS VOLUNTARY.
      -> `due()` is computed, not declared. Nothing has to choose to look.
  (2) MOST ROWS CARRIED NO DUE CONDITION.
      -> `due_when` is REQUIRED and validated. OPEN-ITEMS.json got this right
         (91 of 91 carried `clears_when`) and that part is kept deliberately.
  (3) NO LINK BACK TO THE GENERATOR.
      -> `origin.rerun` is REQUIRED: the command that REGENERATES the finding,
         so a successor can ask whether it still applies instead of trusting a
         months-old sentence.
  (4) NO FORCED TERMINAL STATE.
      -> `terminal_reason` is REQUIRED to enter `done` or `killed`. An item may
         not simply stop being mentioned.
  (5) THE SURFACE NOBODY READ. `DUE.md` was rendered, read by the `duty` skill,
      which a session had to CHOOSE to run, and the operator never saw it.
      -> The pull is the OPERATOR'S OWN PAGE. `due()` feeds brief section 0 and
         `unrouted_count()` is reported as a NUMBER in section 5. A reminder is
         not a mechanism; a rising count on a page they open daily is.

⚠️ **THE TEST OF "THIS IS NOT THE EIGHT REGISTERS AGAIN" IS SPECIFIC**, and it
is the property to defend in review: in the old model an item could sit in a
register forever without anyone noticing, and 951 rows prove it could. Here,
coming due puts an item on the operator's page and it STAYS there until it is
routed. If that property is ever removed, this is the eight registers again.

ONE FILE PER RECORD, AND WHY IT IS NOT ONE SHARED JSONL FILE
--------------------------------------------------------------
The archived registers were JSON arrays, and a shared array is what produced
their merge conflicts: two sessions editing different rows collide on the same
bytes. The first version of THIS module fixed that by making the store
**append-only JSONL** -- a state change is a new LINE, not an edit -- and that
was still wrong, one level down: every writer's new line lands at the SAME
end-of-file position, so two concurrent PRs each appending one record still
touch the same bytes and conflict on merge. MEASURED 2026-09-24 (lane E64):
of 9 green lane PRs, each squash merge re-conflicted every OTHER open PR, and
ONLY on this file -- GitHub's server-side merge does not honour
`.gitattributes` merge drivers, so a union driver cannot fix it either.

⚠️ **RE-POINTED 2026-09-24 (E64): the store is now a DIRECTORY,
`docs/claude/work/pipeline/`, one immutable JSON file per RECORD.** A state
change is a brand-new file with a unique name, never an edit to an existing
one. Two branches each adding a differently-named file is an ordinary git
merge with nothing to reconcile -- there is no shared tail left to collide on.
The property this module has always promised -- **the current state of an
item is its LAST record; nothing is ever edited in place** -- is unchanged;
only how "last" is computed moved, from position in one file to sorted
filenames (`{iso-timestamp}-{random}.json`, so lexicographic order is
chronological order). `read_log()` still returns every record, raw, for the
audit trail; `load()` still folds to the current state per id.

⚠️ **A pre-existing flat `PIPELINE.jsonl` FILE is still read**, using the
original line-based parser (`_read_legacy_jsonl`), if `store` happens to point
at one -- this is a migration safety net, not a second live format: the
committed store is migrated to the directory in the same change that shipped
this paragraph (see `scripts/ops/migrate_pipeline_to_dir.py`), and nothing
after that migration writes the flat file again. `append()` mirrors the same
branch so a caller mid-transition cannot silently start a second store.

⚠️ **A PARSE FAILURE IS NOT A MISSING ROW.** A malformed line is reported as
`unreadable`, never skipped silently, because "we could not read it" and "it is
not there" must stay distinguishable (scripts/ci/check_collapsed_states.py
enforces this class of distinction repo-wide). A loader that drops junk lines
would make a corrupted pipeline look like an empty one -- an EMPTY pipeline
reads as "nothing is due", which is precisely the false all-clear this module
exists to prevent.

A SIXTH FAILURE MODE, FOUND LIVE INSIDE THIS MODULE: TWO FINDINGS SHARING ONE ID
----------------------------------------------------------------------------
Last-record-wins is correct for a STATE CHANGE to a known item -- that is what
gives the audit trail for free. It is silently wrong for two CONCURRENT
sessions that each mint "the next free number" from the store they read at
their own startup: neither can see the other's in-flight write, both pick the
same id, and `load()` cannot tell "a new state for item X" from "an unrelated
finding that reused X's id". The second record does not merge with the first;
it DISPLACES it -- the first finding vanishes from `items`, `due()` and
`render_section_0()` with no error anywhere.

⚠️ **THIS IS NOT HYPOTHETICAL. IT HAPPENED IN THIS FILE, THE SAME DAY IT WAS
WRITTEN.** `PI-20260921-0002` was filed twice for two UNRELATED findings (a
board-pointer reader count and a pr-landing-guard remedy-text bug). `--check`
reported `clean — 4 item(s) validated over 8 record(s), 0 unreadable`
throughout -- a parse-clean, validate-clean store that had already silently
dropped a filed finding. That is the dangerous half: a displaced row looks
EXACTLY like a healthy store from every signal this module used to report,
which is the same collapsed-state class `check_collapsed_states.py` polices
everywhere else, occurring inside the module built to stop findings being
dropped.

THE FIX, in three parts:

1. **`append()` REQUIRES an explicit `intent`** ("new" files a finding under
   an id that must not already exist; "update" records a state change and is
   checked against the existing record's IDENTITY -- `what` and `origin`,
   the fields the self-test's own `base(**over)` convention never varies for
   a real state change). A mismatch is refused, by name, rather than written.
   This is a heuristic, not a proof: it cannot catch a hand-edited or
   out-of-band write that bypasses `append()` entirely (`--check`, part 2, is
   the backstop for that), and it cannot distinguish a genuinely different
   finding that happens to coincide on both `what` and `origin.ref` --
   deliberately narrow rather than deliberately clever, so the refusal
   reason is always statable in one sentence.
2. **`--check` reports every collision it finds, and never prints bare
   `clean` over one.** A NEW collision fails the guard. The one instance
   above PRE-DATES this fix and cannot be repaired without rewriting
   append-only history, which this module's own contract forbids -- so it is
   named in `GRANDFATHERED_COLLISIONS`, the same escape-hatch shape
   `check_collapsed_states.py::GRANDFATHERED_UNREAD` already uses: reported
   LOUDLY every run, never silently passed, and not a place a NEW collision
   may hide.
3. **`mint_id()` allocates ids scoped to the CALLING SESSION**, because a
   global "next free number" is exactly what collided. Two sessions can
   never collide on the same prefix; one session's own ids stay a readable,
   gapless, dated sequence. It does not replace part 1 -- a caller that
   hand-picks an id instead of calling `mint_id()` is still caught by
   `append()`'s refusal, not by this.

Self-test:  python3 scripts/ops/pipeline.py --self-test
Check:      python3 scripts/ops/pipeline.py --check
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

#: ⚠️ RE-POINTED 2026-09-24 (E64): a DIRECTORY, not a file -- see the module
#: docstring's "ONE FILE PER RECORD" section for why. Every caller that reads
#: this constant and passes it straight to `read_log()` / `append()` needs no
#: change: both dispatch on whether the path is a file or a directory.
STORE = Path("docs/claude/work/pipeline")

#: Extension for one record file under STORE.
RECORD_SUFFIX = ".json"

ORIGIN_KINDS = ("audit", "review", "session", "deploy", "research", "operator")
DUE_KINDS = ("observation", "date", "event")
NEXT_ACTIONS = ("dispatch_lane", "check_observation", "apply_mandate", "ask_operator")
STATES = ("queued", "due", "routed", "done", "killed")

#: States an item may not enter without a stated reason. This is reason (4).
TERMINAL_STATES = ("done", "killed")
#: States that still owe someone work. `routed` is NOT terminal -- it means a
#: lane/mandate/row now owns it, and that owner's completion is what closes it.
OPEN_STATES = ("queued", "due", "routed")

_ISO_DATE = "%Y-%m-%d"


class PipelineError(ValueError):
    """A refusal to accept an item. Carries WHICH field and WHY."""


#: Ids whose colliding records PRE-DATE the collision check below and could
#: not be repaired here without rewriting append-only history (this module's
#: own contract forbids that -- see the module docstring). Same convention as
#: `check_collapsed_states.py::GRANDFATHERED_UNREAD`: a DATED DEBT LIST, not
#: an exemption. Reported LOUDLY on every `--check` run, never silently
#: passed, and a store with a collision is never printed as `clean`. Adding a
#: name here is not how a NEW collision gets silenced -- fix the caller that
#: produced it, or mint the finding a fresh id.
#:
#: PI-20260921-0002, added 2026-09-21: two unrelated findings (a
#: board-pointer reader count and a pr-landing-guard remedy-text bug) were
#: filed under the same id the same day this module shipped, before
#: `append()` could refuse it. See docs/claude/work/PIPELINE.jsonl lines
#: 20-21 for the two records.
GRANDFATHERED_COLLISIONS = {
    "PI-20260921-0002",
}

#: Single RECORD FILES (not ids) whose collision is already in append-only
#: history. Narrower than GRANDFATHERED_COLLISIONS on purpose: a later
#: collision on the SAME id, at any other record, still fails the guard.
#: Same rules: a dated debt list, reported LOUDLY every run, never `clean`.
#:
#: 20260930T080938782595Z-98f85ccf.json (PI-20260929-AQRK6CL1-0014), added
#: 2026-09-30 by lane PROP-ETH-DOM (session_01HS7ws2n8c57QMUquRobE9z): two
#: update records for 0014 were appended from two different bases 90 s apart
#: (#14615 from a main without #14638's record, #14638 from a main without
#: #14615's) and merged 07:24Z/08:09Z-stamped; the later one is therefore not
#: an append-only extension of the earlier. Nothing is lost: record
#: 20260930T081547130622Z-18fc31c5.json restores the displaced text verbatim.
#: Cause, for the next caller: append a pipeline update from a FRESH
#: origin/main, after your previous update to the same id has merged.
GRANDFATHERED_COLLISION_RECORDS = {
    "20260930T080938782595Z-98f85ccf.json",
}


def _is_grandfathered_collision(c: dict) -> bool:
    return c.get("id") in GRANDFATHERED_COLLISIONS or c.get("at") in GRANDFATHERED_COLLISION_RECORDS


@dataclass
class LoadResult:
    """⚠️ Four counts, never collapsed into one 'items' list.

    `unreadable` existing and being non-empty is a LOUD condition: it means the
    store is partially unreadable, which must never render as an empty or
    healthy pipeline. `collisions` is the sibling loud condition for a store
    that parses and validates cleanly but has silently displaced a finding --
    see the module docstring's "SIXTH FAILURE MODE".
    """

    items: dict[str, dict] = field(default_factory=dict)
    #: Each entry: (location, reason) -- location is `"line N"` for a legacy
    #: flat-file store or a record filename for the directory store. A string
    #: in both cases so callers never have to branch on which backend this is.
    unreadable: list[tuple[str, str]] = field(default_factory=list)
    #: Every successfully-parsed record, RAW and in read order -- i.e. before
    #: folding to the last-per-id state. Most callers want `items` (the
    #: current state); this is for the rarer caller that has to reproduce
    #: "was this id EVER in state X", such as a guard scanning every record a
    #: soak was ever mentioned in rather than only its current one.
    raw: list[dict] = field(default_factory=list)
    #: id -> the location (filename under the store, or "line N" for a legacy
    #: flat file) that currently holds that id's LATEST record. For a caller
    #: that needs to date a row's current content -- e.g. `git log -1 -- that
    #: file` now answers "when did this row last change", which used to
    #: require diffing many historical snapshots of one shared file.
    sources: dict[str, str] = field(default_factory=dict)
    records: int = 0
    #: Each entry: {"id", "at", "reason"} -- an id where the record `at`
    #: does NOT look like a state change of the record it displaced.
    #: Populated by `read_log()` as it folds, so detecting this costs no
    #: extra pass over the store.
    collisions: list[dict] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return not self.unreadable


def _today(today: date | None = None) -> date:
    return today or datetime.now(timezone.utc).date()


def _parse_date(s: str, field_name: str) -> date:
    try:
        return datetime.strptime(s, _ISO_DATE).date()
    except (TypeError, ValueError):
        raise PipelineError(
            f"{field_name}: expected an ISO date YYYY-MM-DD, got {s!r}"
        ) from None


def validate(item: dict) -> dict:
    """Refuse an item that cannot be followed through, and say which reason.

    ⚠️ Every refusal below maps to one of the five documented failure reasons.
    A validator that accepted an item with no `due_when` would rebuild reason
    (2); one that accepted `killed` with no `terminal_reason` would rebuild
    reason (4). Do not relax these to make an import pass -- fix the import.
    """
    if not isinstance(item, dict):
        raise PipelineError(f"item must be an object, got {type(item).__name__}")

    for key in ("id", "what", "origin", "due_when", "next_action", "state"):
        if key not in item:
            raise PipelineError(f"missing required field {key!r}")

    if not isinstance(item["id"], str) or not item["id"].strip():
        raise PipelineError("id: must be a non-empty string")
    if not isinstance(item["what"], str) or not item["what"].strip():
        raise PipelineError("what: must be a non-empty string")

    # ── reason (3): it must know how to regenerate itself ────────────────────
    origin = item["origin"]
    if not isinstance(origin, dict):
        raise PipelineError("origin: must be an object")
    if origin.get("kind") not in ORIGIN_KINDS:
        raise PipelineError(
            f"origin.kind: must be one of {ORIGIN_KINDS}, got {origin.get('kind')!r}"
        )
    for k in ("ref", "rerun"):
        if not isinstance(origin.get(k), str) or not origin[k].strip():
            raise PipelineError(
                f"origin.{k}: required and non-empty — reason (3), a row that "
                f"cannot point at what produced it cannot be re-checked later"
            )

    # ── reason (2): it must know when it needs attention ─────────────────────
    due_when = item["due_when"]
    if not isinstance(due_when, dict):
        raise PipelineError("due_when: must be an object")
    kind = due_when.get("kind")
    if kind not in DUE_KINDS:
        raise PipelineError(
            f"due_when.kind: must be one of {DUE_KINDS}, got {kind!r}"
        )
    if kind == "date":
        _parse_date(due_when.get("due_date"), "due_when.due_date")
    else:
        if not isinstance(due_when.get("clears_when"), str) or not due_when["clears_when"].strip():
            raise PipelineError(
                "due_when.clears_when: required for observation/event items — "
                "reason (2). What would have to be TRUE for this to be over?"
            )
    soak = due_when.get("soak")
    if soak is not None:
        if kind != "observation" or not isinstance(soak, dict) or \
                not isinstance(soak.get("subject"), str) or not soak["subject"].strip():
            raise PipelineError(
                "due_when.soak: an observation item's soak block needs a non-empty "
                "`subject` (the key scripts/ops/soak_report.py grades it under)"
            )
    every = due_when.get("check_every_days")
    if every is not None and (not isinstance(every, int) or every < 1):
        raise PipelineError(
            f"due_when.check_every_days: must be a positive int, got {every!r}"
        )

    if item["next_action"] not in NEXT_ACTIONS:
        raise PipelineError(
            f"next_action: must be one of {NEXT_ACTIONS}, got {item['next_action']!r}"
        )
    if item["state"] not in STATES:
        raise PipelineError(
            f"state: must be one of {STATES}, got {item['state']!r}"
        )

    # ── reason (4): it may not simply stop being mentioned ───────────────────
    reason = item.get("terminal_reason")
    if item["state"] in TERMINAL_STATES:
        if not isinstance(reason, str) or not reason.strip():
            raise PipelineError(
                f"terminal_reason: REQUIRED to enter {item['state']!r} — reason "
                f"(4). An item leaves the pipeline done or killed WITH A STATED "
                f"REASON, never by going quiet."
            )
    elif reason not in (None, ""):
        raise PipelineError(
            f"terminal_reason: set to {reason!r} on non-terminal state "
            f"{item['state']!r} — that reads as closed while still being open"
        )

    if item["state"] == "routed" and not str(item.get("routed_to") or "").strip():
        raise PipelineError(
            "routed_to: required in state 'routed' — routed to WHAT? A routing "
            "with no destination is how an item stops being anyone's."
        )
    return item


def _identity_mismatches(prior: dict, new: dict) -> list[str]:
    """Say why `new` does NOT look like a state change of `prior` under the
    SAME id -- i.e. why it is plausibly a DIFFERENT finding instead.

    Deliberately narrow, and the narrowness is documented rather than hidden:
    `state`, `routed_to`, `terminal_reason`, `next_action` and `due_when` are
    exactly the fields a real state change is EXPECTED to move (this is the
    self-test's own `base(**over)` convention -- a state-change round trip
    only ever varies those). `origin.kind` and `origin.ref` are the finding's
    IDENTITY -- what produced it -- and must match exactly.

    `what` is checked as an APPEND, not an equality, and this was corrected
    against the REAL store rather than assumed: `PI-20260921-0004` (a
    genuine, legitimate item) was filed at 2080 characters and later
    RE-APPENDED at 2995 -- the same session adding a measured "reply-channel
    mechanics" paragraph to the SAME finding as it learned more. A strict
    `==` flagged that as a collision on this file's real data on the first
    run of this check, which would have made `--check` fail the guard over
    ordinary practice. So `new.what` must **start with** `prior.what` --
    elaboration is allowed, replacement is not.

    What this heuristic MISSES, stated rather than hidden: (1) a genuinely
    different finding whose text happens to begin with the prior finding's
    text verbatim would pass as a plausible update -- vanishingly unlikely
    for free-text paragraphs this long, and narrow rather than clever, so a
    false negative here is a coincidence, not a design gap; (2) it cannot
    stop a hand-edited or out-of-band write to the store that never calls
    this function at all -- `--check`'s collision report (below) is the
    backstop for that; (3) a legitimate CORRECTION to `what` (a typo fixed,
    text removed rather than only added) is now refused too -- this module
    accepts that false-positive cost rather than risk silently welcoming a
    real collision, and `PI-20260921-0002` is exactly what welcoming one
    looks like.
    """
    out: list[str] = []
    old_what = prior.get("what") or ""
    new_what = new.get("what") or ""
    if not new_what.startswith(old_what):
        out.append("what changed (not an append-only extension of the prior text)")
    po = prior.get("origin") or {}
    no = new.get("origin") or {}
    if po.get("kind") != no.get("kind"):
        out.append(f"origin.kind changed ({po.get('kind')!r} -> {no.get('kind')!r})")
    if po.get("ref") != no.get("ref"):
        out.append(f"origin.ref changed ({po.get('ref')!r} -> {no.get('ref')!r})")
    return out


def _read_legacy_jsonl(store: Path) -> LoadResult:
    """The original single-file reader. Kept for a pre-migration flat file
    (see the module docstring's "ONE FILE PER RECORD" section) -- not a
    second live format, a migration safety net.
    """
    res = LoadResult()
    for lineno, raw_line in enumerate(store.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue
        try:
            rec = json.loads(line)
            if not isinstance(rec, dict) or not isinstance(rec.get("id"), str):
                raise ValueError("record is not an object carrying a string id")
        except Exception as exc:  # noqa: BLE001 — the message is the payload
            res.unreadable.append((f"line {lineno}", f"{type(exc).__name__}: {exc}"))
            continue
        res.records += 1
        res.raw.append(rec)
        prior = res.items.get(rec["id"])
        if prior is not None:
            mismatches = _identity_mismatches(prior, rec)
            if mismatches:
                res.collisions.append({
                    "id": rec["id"],
                    "at": f"line {lineno}",
                    "reason": "; ".join(mismatches),
                })
        res.items[rec["id"]] = rec
        res.sources[rec["id"]] = f"line {lineno}"
    return res


def read_log(store: Path = STORE) -> LoadResult:
    """Read every record, folding to the LAST per id. Never drops a bad one.

    Dispatches on what `store` actually is: a pre-existing flat FILE is read
    with the legacy line-based parser (`_read_legacy_jsonl`); anything else
    (missing, or a directory) is read as the current one-file-per-record
    store, in filename order -- filenames are timestamp-prefixed, so sorted
    order is chronological order, the same "last record wins" contract the
    flat file gave by line position.

    ⚠️ Also flags a COLLISION the moment a record displaces a prior one under
    the same id without looking like its state change (`_identity_mismatches`)
    -- see the module docstring's "SIXTH FAILURE MODE". This costs no extra
    pass: the fold already visits every record once.
    """
    if store.is_file():
        return _read_legacy_jsonl(store)

    res = LoadResult()
    if not store.exists():
        return res
    for path in sorted(store.glob(f"*{RECORD_SUFFIX}")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(rec, dict) or not isinstance(rec.get("id"), str):
                raise ValueError("record is not an object carrying a string id")
        except Exception as exc:  # noqa: BLE001 — the message is the payload
            res.unreadable.append((path.name, f"{type(exc).__name__}: {exc}"))
            continue
        res.records += 1
        res.raw.append(rec)
        prior = res.items.get(rec["id"])
        if prior is not None:
            mismatches = _identity_mismatches(prior, rec)
            if mismatches:
                res.collisions.append({
                    "id": rec["id"],
                    "at": path.name,
                    "reason": "; ".join(mismatches),
                })
        res.items[rec["id"]] = rec
        res.sources[rec["id"]] = path.name
    return res


def load(store: Path = STORE) -> LoadResult:
    return read_log(store)


def _record_filename(now: datetime | None = None) -> str:
    """A filename for one NEW record: timestamp-prefixed so sorted directory
    order is chronological order, random-suffixed so two sessions writing in
    the same instant still never collide -- no coordination required, which
    is the entire point (see the module docstring)."""
    now = now or datetime.now(timezone.utc)
    return f"{now.strftime('%Y%m%dT%H%M%S%f')}Z-{uuid.uuid4().hex[:8]}{RECORD_SUFFIX}"


def version_of(item_id: str, store: Path = STORE) -> str | None:
    """The VERSION TOKEN of an item's current state: the filename (or
    `"line N"` for a legacy flat file) holding its LATEST record, or None if the
    id does not exist. A writer that READS an item and later UPDATES it passes
    this token back as `append(..., based_on=token)`; see `append()`."""
    return read_log(store).sources.get(item_id)


def append(item: dict, store: Path = STORE, *, intent: str,
           based_on: str | None = None) -> dict:
    """Validate, then write one NEW record file. Never rewrites history.

    `intent` is REQUIRED -- there is deliberately no default, because the
    caller having to say which it means is the fix. Two values:

      "new"    -- this id must NOT already exist. Refused if it does, naming
                  the existing record's state and origin, because writing
                  anyway is exactly how PI-20260921-0002 collided.
      "update" -- this id MUST already exist, and the new record must look
                  like a state change of it (`_identity_mismatches`). A
                  mismatch is refused by name rather than silently folded
                  over the record it would have displaced.

    ⚠️ STALE-SNAPSHOT GUARD (PI-20261004-4GA8WQPA-0002). An "update" must also
    say WHICH VERSION it read: `based_on` is the token `version_of(id)`
    returned when the writer read the item, and the write is refused unless
    that is STILL the item's latest record. `_identity_mismatches` cannot see
    this: a writer holding an old read passes the identity check and silently
    reverts a newer update (MEASURED 2026-10-04: PR #16505 restored routing
    text that had been replaced 25 minutes earlier on 10 items). `based_on` is
    REQUIRED for "update" -- like `intent`, no default, because having to name
    the version read is the fix -- and is stored on the record. It guards a
    writer's own read-to-write window; it cannot see a conflicting record that
    arrives later through a merge of two branches (`--check` reports those).

    Neither path re-derives what "plausibly a state change" means locally --
    that definition lives once, in `_identity_mismatches`, so this function
    and `read_log()`'s own collision detector can never drift apart on it.

    ⚠️ Writes a brand-new, uniquely-named file (`_record_filename()`), never
    an edit to an existing one -- this is the actual merge-safety property:
    two concurrent callers each produce a differently-named file, so there is
    nothing for git to reconcile. If `store` is a pre-existing flat file
    (migration transition), the legacy single-file append is used instead, so
    a caller mid-transition cannot silently start a second store.
    """
    validate(item)
    if intent not in ("new", "update"):
        raise PipelineError(f"intent: must be 'new' or 'update', got {intent!r}")

    prior = read_log(store).items.get(item["id"])

    if intent == "new" and prior is not None:
        raise PipelineError(
            f"id {item['id']!r} already exists (state={prior.get('state')!r}, "
            f"origin.ref={(prior.get('origin') or {}).get('ref')!r}) -- call "
            f"append(..., intent='update') to record a state change of THAT "
            f"finding, or mint_id() a fresh id if this is a DIFFERENT one. "
            f"Refusing rather than silently displacing it the way "
            f"PI-20260921-0002 already was."
        )
    if intent == "update":
        if prior is None:
            raise PipelineError(
                f"id {item['id']!r}: intent='update' but no prior record "
                f"exists to update -- call append(..., intent='new') to file "
                f"it for the first time."
            )
        current = read_log(store).sources.get(item["id"])
        if based_on is None:
            raise PipelineError(
                f"id {item['id']!r}: intent='update' requires based_on= the "
                f"version you READ (pipeline.version_of({item['id']!r}) -> "
                f"{current!r} right now). Without it a stale read can silently "
                f"revert a newer update (PI-20261004-4GA8WQPA-0002).")
        if based_on != current:
            raise PipelineError(
                f"id {item['id']!r}: STALE SNAPSHOT -- you read {based_on!r} but "
                f"the latest record is {current!r}. Re-read the item, re-apply "
                f"your change on top of the CURRENT state, and retry.")
        item = dict(item, based_on=based_on)
        mismatches = _identity_mismatches(prior, item)
        if mismatches:
            raise PipelineError(
                f"id {item['id']!r}: intent='update' but this record does "
                f"not look like a state change of the existing one -- "
                f"{'; '.join(mismatches)}. A genuinely different finding "
                f"needs its OWN id (mint_id()); reusing this one would "
                f"displace the existing finding exactly the way "
                f"PI-20260921-0002 already was."
            )

    if store.is_file():
        line = json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
        with open(store, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        return item

    store.mkdir(parents=True, exist_ok=True)
    name = _record_filename()
    payload = json.dumps(item, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    tmp = store / f".{name}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, store / name)  # atomic: no reader ever sees a partial file
    return item


def _session_tag(session_ref: str) -> str:
    """A short, deterministic, filesystem/id-safe slug for `session_ref`.

    Prefers the `session_XXXXXXXX...` token the harness already hands out
    (unique per running session by construction, which is the whole point);
    falls back to slugifying whatever string was given so a caller with no
    formal session id yet (an audit, a manual filing) still gets something
    legible and stable rather than an exception.
    """
    m = re.search(r"session_[A-Za-z0-9]+", session_ref)
    token = m.group(0) if m else session_ref
    slug = re.sub(r"[^A-Za-z0-9]", "", token).upper()
    return slug[-8:] if slug else "UNSCOPED"


def mint_id(session_ref: str, *, today: date | None = None, store: Path = STORE) -> str:
    """Allocate a NEW pipeline id that no OTHER concurrent session can also
    pick, by scoping the counter to the CALLING session rather than to a
    shared "next free number".

    ⚠️ WHY SESSION-SCOPED, NOT A GLOBAL COUNTER: two sessions filing
    concurrently each read the store at their own startup and cannot see the
    other's in-flight write -- a shared counter WILL be read-and-incremented
    twice with no lock to stop it (this store is deliberately lock-free, see
    the module docstring). The only counter safe to allocate without
    coordination is one namespaced to something already unique to the
    CALLER, and the environment already hands every session one: its own
    session id.

    id shape: `PI-<YYYYMMDD>-<SESSION_TAG>-<NNNN>`. Two sessions can never
    collide (different tags, by construction); one session's own ids stay a
    readable, gapless, dated sequence -- which is the property worth keeping
    from the plain `PI-<date>-NNNN` scheme. Do NOT "fix" the collision by
    making ids opaque instead (a UUID) and stopping there: that only makes a
    collision less LIKELY while leaving the store exactly as unable to
    REPORT one -- `append()`'s intent check and `--check`'s collision report
    are the actual fix; this function only makes reaching them less likely
    to matter.

    What this does NOT solve, stated rather than hidden: (1) it does not stop
    a caller that bypasses `mint_id()` and hand-picks a colliding id --
    `append()`'s refusal is the real backstop, not this; (2) it does not
    protect a session racing ITSELF by calling `mint_id()` twice before the
    first `append()` lands -- a live agent session issues tool calls
    sequentially, so this is not exercised in practice, but a caller doing
    its own concurrency must serialize its own calls; (3) two distinct
    session ids could in principle share the same 8-character tail --
    astronomically unlikely given the id format in use, and not
    mathematically ruled out, which is exactly why (1)'s backstop exists
    independently of this function being correct.
    """
    tag = _session_tag(session_ref)
    date_str = _today(today).strftime("%Y%m%d")
    prefix = f"PI-{date_str}-{tag}-"
    existing = read_log(store).items
    used = [
        int(item_id[len(prefix):])
        for item_id in existing
        if item_id.startswith(prefix) and item_id[len(prefix):].isdigit()
    ]
    nxt = (max(used) + 1) if used else 1
    return f"{prefix}{nxt:04d}"


# ── SOAK-WATCH (2026-10-04): a soak's due-ness comes from its GRADED STATE ───
# `scripts/ops/soak_alarm.py`'s four states, ported onto this store (that module
# read the archived OPEN-ITEMS.json and graded nothing after 2026-09-21). An item
# carrying `due_when.soak.subject` is due when `scripts/ops/soak_report.py`
# grades that subject ready / dead / could-not-look, and NOT due while it is
# accruing — a weekly "not ready yet" row is the desensitised alarm the canonical
# rule refuses. A subject the state file does not carry, or no state file at
# all, is could-not-look -> due: the safe direction for a missing read.
SOAK_STATE = Path("docs/claude/work/soak-state.json")
SOAK_DUE_VERDICTS = ("ready", "dead", "could-not-look")
_soak_cache: tuple[float, dict] | None = None


def load_soak_states(path: Path = SOAK_STATE) -> dict:
    """``{subject: verdict}`` from the soak state file; ``{}`` if unreadable."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        return {k: v.get("verdict") for k, v in (doc.get("subjects") or {}).items()}
    except (OSError, ValueError, AttributeError):
        return {}


def _default_soak_states() -> dict:
    """Re-read when the file changes. The brief route runs inside the long-lived
    web API process and ict-git-sync rewrites the file weekly, so a process-
    lifetime cache would serve last week's grades. Anchored at the repo root,
    not the cwd, for the same reason."""
    global _soak_cache
    path = Path(__file__).resolve().parents[2] / SOAK_STATE
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = -1.0
    if _soak_cache is None or _soak_cache[0] != mtime:
        _soak_cache = (mtime, load_soak_states(path))
    return _soak_cache[1]


def soak_verdict(item: dict, soak_states: dict | None = None) -> str | None:
    """The graded verdict for a soak item, ``could-not-look`` if ungraded, or
    ``None`` for an item that is not a soak."""
    subj = ((item.get("due_when") or {}).get("soak") or {}).get("subject")
    if not subj:
        return None
    states = _default_soak_states() if soak_states is None else soak_states
    return states.get(subj) or "could-not-look"


def is_due(item: dict, today: date | None = None, soak_states: dict | None = None) -> bool:
    """Is this item asking for attention now?

    ⚠️ COMPUTED, NOT DECLARED — this is reason (1). An item does not become due
    because someone remembered to set a flag; it becomes due because the clock
    or the condition says so, whether or not anyone looked.
    """
    if item.get("state") in TERMINAL_STATES:
        return False
    dw = item.get("due_when") or {}
    today = _today(today)

    sv = soak_verdict(item, soak_states)
    if sv is not None:
        return sv in SOAK_DUE_VERDICTS

    # One-shot observation (non-soak definition of done, 2026-10-04): due once
    # `observation.due_by` is reached, whatever the timer says; an unparseable
    # due_by is DUE (the safe direction for a broken clock).
    obs = item.get("observation")
    if isinstance(obs, dict) and obs.get("due_by"):
        try:
            if _parse_date(str(obs["due_by"])[:10], "observation.due_by") <= today:
                return True
        except PipelineError:
            return True

    if dw.get("kind") == "date":
        try:
            return _parse_date(dw.get("due_date"), "due_when.due_date") <= today
        except PipelineError:
            # ⚠️ An unparseable due date is treated as DUE, not as not-due. The
            # safe direction for a broken clock is to surface the item.
            return True

    every = dw.get("check_every_days")
    if not isinstance(every, int) or every < 1:
        # No cadence declared on an observation item -> it is always asking.
        return True
    last = dw.get("last_checked")
    if not isinstance(last, str) or not last.strip():
        return True
    try:
        return _parse_date(last, "due_when.last_checked") + timedelta(days=every) <= today
    except PipelineError:
        return True


def next_due_date(item: dict) -> date | None:
    """The date `is_due` will next flip True for an OPEN, non-soak item, or
    None when it is not a clock item (terminal, soak-bound, event kind with no
    cadence) or is already/always due (no usable cadence -> `date.min`).

    The same three clocks `is_due` reads, folded to one date: the EARLIEST of
    `observation.due_by`, `due_when.due_date` (kind date), and
    `last_checked + check_every_days`. Kept next to `is_due` and pinned to it
    by the self-test so the two cannot fork the definition of "due"."""
    if item.get("state") in TERMINAL_STATES:
        return None
    dw = item.get("due_when") or {}
    if (dw.get("soak") or {}).get("subject"):
        return None
    cands: list[date] = []
    obs = item.get("observation")
    if isinstance(obs, dict) and obs.get("due_by"):
        try:
            cands.append(_parse_date(str(obs["due_by"])[:10], "observation.due_by"))
        except PipelineError:
            return date.min
    if dw.get("kind") == "date":
        try:
            cands.append(_parse_date(dw.get("due_date"), "due_when.due_date"))
        except PipelineError:
            return date.min
    else:
        every, last = dw.get("check_every_days"), dw.get("last_checked")
        if not isinstance(every, int) or every < 1 or not isinstance(last, str) or not last.strip():
            return date.min
        try:
            cands.append(_parse_date(last, "due_when.last_checked") + timedelta(days=every))
        except PipelineError:
            return date.min
    return min(cands) if cands else None


REDATE_MIN_OBSERVATION_CHARS = 40


def redate_problems(prior: dict, new: dict) -> list[str]:
    """Why `new` is a RE-DATE that carries no new observation (PI-20261005-
    4GA8WQPA-0002, the lapse wave). A re-date is a record for an OPEN item whose
    state did not change and whose next-due date moved LATER. Moving a date is
    free and looks like work; the BACKLOG-BURNDOWN-2 lane took 358 due items to
    19 that way and left 553 open, all lapsing together again. So the record
    must carry `redate: {observed_at, observation}` -- what the writer actually
    READ this time -- and that observation must differ from the previous one.
    Empty list = not a re-date, or an admissible one."""
    if new.get("state") != prior.get("state") or new.get("state") in TERMINAL_STATES:
        return []  # a disposition (routed/killed/done) is not a re-date
    a, b = next_due_date(prior), next_due_date(new)
    if a is None or b is None or b <= a:
        return []
    rd = new.get("redate")
    if not isinstance(rd, dict):
        return [f"next-due moved {a} -> {b} with no `redate` block: a new date is not "
                f"an observation. Re-run origin.rerun, then record what it showed "
                f"(`pipeline.py --redate ID --observation TEXT --next-due DATE`), "
                f"or close/kill the item with a terminal_reason."]
    out: list[str] = []
    obs = str(rd.get("observation") or "").strip()
    if len(obs) < REDATE_MIN_OBSERVATION_CHARS:
        out.append(f"`redate.observation` must say what was read "
                   f"(>= {REDATE_MIN_OBSERVATION_CHARS} chars), got {len(obs)}")
    prev = str(((prior.get("redate") or {}).get("observation")) or "").strip()
    if obs and obs == prev:
        out.append("`redate.observation` repeats the previous record's text verbatim -- "
                   "that is a new date, not a new observation")
    try:
        _parse_date(str(rd.get("observed_at") or "")[:10], "redate.observed_at")
    except PipelineError as exc:
        out.append(str(exc))
    return out


def redate(item_id: str, *, observation: str, next_due: date, store: Path = STORE,
           today: date | None = None, evidence: str | None = None) -> dict:
    """Push an item's next-due date out BECAUSE you re-read it: sets the cadence,
    `due_when.due_date` (kind date) and `observation.due_by` TOGETHER (changing
    only one is the PI-20261005-WFBREDQP-0001 trap: `is_due` honours `due_by`
    regardless of cadence), stamps the `redate` block `redate_problems` demands,
    and writes through `append()` with the version it read."""
    today = _today(today)
    res = read_log(store)
    prior = res.items.get(item_id)
    if prior is None:
        raise PipelineError(f"{item_id!r}: no such item")
    if prior.get("state") in TERMINAL_STATES:
        raise PipelineError(f"{item_id!r} is {prior.get('state')}; nothing to re-date")
    if next_due <= today:
        raise PipelineError(f"next_due {next_due} is not after today {today}")
    rec = json.loads(json.dumps(prior))
    rec.pop("based_on", None)
    dw = rec.setdefault("due_when", {})
    if dw.get("kind") == "date":
        dw["due_date"] = next_due.isoformat()
    else:
        dw["last_checked"] = today.isoformat()
        dw["check_every_days"] = (next_due - today).days
    if isinstance(rec.get("observation"), dict):
        rec["observation"]["due_by"] = next_due.isoformat()
    rec["redate"] = {"observed_at": today.isoformat(), "observation": observation.strip(),
                     **({"evidence": evidence} if evidence else {})}
    problems = redate_problems(prior, rec)
    if problems:
        raise PipelineError("; ".join(problems))
    return append(rec, store, intent="update", based_on=res.sources[item_id])


def due_bucket(item: dict, today: date | None = None) -> str | None:
    """WHY an item is due — the same branches as ``is_due``, named. ``None`` iff
    ``is_due`` is False (a test pins the two together, so this cannot fork the
    definition of "due"). Buckets: ``date-passed`` · ``no-cadence`` (observation
    or event with no usable ``check_every_days``) · ``never-checked`` (cadence
    declared, ``last_checked`` blank) · ``lapsed`` (checked, cadence elapsed)."""
    if not is_due(item, today):
        return None
    dw = item.get("due_when") or {}
    if dw.get("kind") == "date":
        return "date-passed"
    every = dw.get("check_every_days")
    if not isinstance(every, int) or every < 1:
        return "no-cadence"
    last = dw.get("last_checked")
    if not isinstance(last, str) or not last.strip():
        return "never-checked"
    return "lapsed"


def due(items: Iterable[dict], today: date | None = None) -> list[dict]:
    return [i for i in items if is_due(i, today)]


def unrouted(items: Iterable[dict], today: date | None = None) -> list[dict]:
    """Due, and nobody has taken it. THIS is the number section 5 reports.

    ⚠️ `routed` is excluded and `queued`/`due` are not. An item that a lane owns
    is being worked; an item that is due and unowned is the thing that used to
    rot invisibly.
    """
    return [i for i in due(items, today) if i.get("state") != "routed"]


# ── JC-SA-06 (2026-09-29): section 0 cap + the routing alarm ─────────────────
# MEASURED 2026-09-29 over the store (1,554 items, 177 due, 153 unrouted): the
# unrouted count was 85 when A10 closed and 153 now; filing dates (the date
# embedded in the id — items carry no created field) put 30 on 09-24, 16 on
# 09-25, 4 on 09-26, 53 on 09-27, 36 on 09-28, 11 on 09-29, with three older
# (09-21, 09-18, 09-09 → oldest 20 days). Section 0 rendered all 177 in full
# prose: 301,617 bytes, 35% of an 868,265-byte brief.
#
# BOUNDS, chosen from that distribution:
#   count  > 100 — 85 is the level A10 closed at and the operator accepted; 100
#                  is that level plus ~one day of the 09-27/28 filing rate
#                  (36-53/day). Firing today (153) is correct, not noise.
#   oldest > 7 d — 5 of the 6 filing days are <= 5 days old (median ~2 d); a
#                  week is longer than any healthy routing turnaround and shorter
#                  than the 20-day tail that is there now.
# Both are constants so moving one is a reviewed diff, not a tuning knob.
SECTION0_CAP = 25
UNROUTED_COUNT_BOUND = 100
UNROUTED_OLDEST_DAYS_BOUND = 7
_ID_DATE = re.compile(r"(?<!\d)(20\d{6})(?!\d)")


def filed_date(item: dict) -> date | None:
    """Filing date = the YYYYMMDD in the id (no created field exists). None if absent."""
    m = _ID_DATE.search(str(item.get("id", "")))
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def unrouted_alarm(items: Iterable[dict], today: date | None = None) -> dict:
    """Bound check on the unrouted backlog. ``undated`` is reported, never dropped:
    an item whose age could not be read is *we could not look*, not *young*."""
    today = _today(today)
    rows = unrouted(items, today)
    dated = [(filed_date(i), i) for i in rows]
    ages = [((today - d).days, i["id"]) for d, i in dated if d is not None]
    oldest_days, oldest_id = max(ages) if ages else (None, None)
    breached = []
    if len(rows) > UNROUTED_COUNT_BOUND:
        breached.append(f"unrouted count {len(rows)} > {UNROUTED_COUNT_BOUND}")
    if oldest_days is not None and oldest_days > UNROUTED_OLDEST_DAYS_BOUND:
        breached.append(f"oldest unrouted {oldest_days}d ({oldest_id}) > "
                        f"{UNROUTED_OLDEST_DAYS_BOUND}d")
    return {"count": len(rows), "oldest_days": oldest_days, "oldest_id": oldest_id,
            "undated": sum(1 for d, _ in dated if d is None), "breached": breached}


def unrouted_count(items: Iterable[dict], today: date | None = None) -> int:
    return len(unrouted(items, today))


def stats(res: LoadResult, today: date | None = None) -> dict:
    items = list(res.items.values())
    by_state = {s: sum(1 for i in items if i.get("state") == s) for s in STATES}
    return {
        "records": res.records,
        "items": len(items),
        "by_state": by_state,
        "open": sum(by_state[s] for s in OPEN_STATES),
        "due": len(due(items, today)),
        "unrouted": unrouted_count(items, today),
        "unrouted_alarm": unrouted_alarm(items, today),
        # ⚠️ Reported ALWAYS, including as 0, so "we could not read the store"
        # is never rendered as a clean bill of health.
        "unreadable": len(res.unreadable),
        "readable": res.healthy,
        # ⚠️ Same reasoning, for the sibling loud condition: a store that
        # parses and validates cleanly can still have silently displaced a
        # finding. Reported ALWAYS, including as 0.
        "collisions": len(res.collisions),
    }


def render_section_0(res: LoadResult, today: date | None = None,
                     limit: int | None = SECTION0_CAP) -> list[str]:
    """Brief section 0 — WHAT CAME DUE. Consumed by A3.

    ⚠️ Renders the unreadable count FIRST when non-zero. A brief that opened
    with a tidy due-list while half the store failed to parse would be the
    frozen-board failure this repo already paid for: a valid-looking read of a
    broken source is indistinguishable from a healthy one unless it says so.

    JC-SA-06: lists at most ``limit`` items — unrouted before routed, oldest
    filed first — and SAYS how many it left out. ``limit=None`` renders all
    (``--due --all``). The alarm banner precedes the list.
    """
    L = ["## §0 — WHAT CAME DUE", ""]
    if not res.healthy:
        L += [
            f"> ⚠️ **{len(res.unreadable)} RECORD(S) COULD NOT BE PARSED** — this "
            f"section is INCOMPLETE and the counts below are a floor, not a total.",
            "",
        ]
        for loc, why in res.unreadable[:10]:
            L.append(f"> - {loc}: {why}")
        L.append("")
    alarm = unrouted_alarm(res.items.values(), today)
    if alarm["breached"]:
        L += ["> 🚨 **ROUTING ALARM** — " + "; ".join(alarm["breached"]) +
              ". Due items are reaching this page faster than they are routed.", ""]
    if alarm["undated"]:
        L += [f"> {alarm['undated']} unrouted item(s) carry no date in their id — "
              f"their age could not be read and is not in the oldest-age figure.", ""]
    soaks = [i for i in res.items.values()
             if i.get("state") in OPEN_STATES and soak_verdict(i) is not None]
    if soaks:
        c = collections.Counter(soak_verdict(i) for i in soaks)
        L += [f"> **Soaks ({len(soaks)} tracked):** {c.get('ready', 0)} ready · {c.get('dead', 0)} dead · "
              f"{c.get('could-not-look', 0)} could-not-look · {c.get('accruing', 0)} accruing (quiet). "
              f"Ready/dead/could-not-look are listed below as due; the full table is "
              f"`docs/claude/work/SOAK-REPORT.md`.", ""]
    rows = due(res.items.values(), today)
    if not rows:
        L += ["Nothing came due." if res.healthy else
              "Nothing came due **among the records that parsed**.", ""]
        return L
    L += [f"**{len(rows)} item(s) due. Each needs a disposition today.**", ""]
    far = date.max
    # A READY or DEAD soak is a decision owed now — listed first so the cap never
    # hides it behind older unrouted rows (SOAK-WATCH 2026-10-04).
    ordered = sorted(rows, key=lambda r: (soak_verdict(r) not in ("ready", "dead"),
                                          r.get("state") == "routed",
                                          filed_date(r) or far, r.get("id", "")))
    shown = ordered if limit is None else ordered[:limit]
    for i in shown:
        owner = f" → `{i['routed_to']}`" if i.get("routed_to") else ""
        sv = soak_verdict(i)
        if sv is not None:
            owner += f" · **soak: {sv}**"
        what = str(i.get("what"))
        if limit is not None and len(what) > 300:
            what = what[:300].rstrip() + "…"
        L.append(
            f"- **{i.get('id')}** [{i.get('state')}{owner}] {what} "
            f"· next: `{i.get('next_action')}` · rerun: `{(i.get('origin') or {}).get('rerun')}`"
        )
    if len(ordered) > len(shown):
        L += ["", f"**{len(ordered) - len(shown)} more due, not shown** (oldest-first "
              f"cap of {limit}; unrouted listed before routed). All of them: "
              f"`python3 scripts/ops/pipeline.py --due --all`."]
    L.append("")
    return L


def _check_git_merge_safety(check) -> None:
    """RULE ONE's positive control for the whole re-point: prove, with plain
    git (no merge driver, no `-Xtheirs`), that two branches each appending
    one record merge cleanly in EITHER order. This is the actual property
    lane E64 exists to buy -- the flat-file predecessor failed exactly this
    scenario in production (9 green PRs, every squash re-conflicting every
    other one on this file, verbatim in the module docstring above).
    """
    import shutil
    import subprocess

    def git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
        # `-c gc.auto=0 -c maintenance.auto=false`: a `git merge` may fork a
        # background `git gc --auto` into the throwaway repo, and that process
        # can still be writing `.git/objects` while TemporaryDirectory tears
        # the tree down -- MEASURED on PR #16964's guards job (run 37671602666,
        # 2026-10-07): `OSError: [Errno 39] Directory not empty: 'objects'`
        # from the cleanup, with every merge assertion already passed. The
        # control is about merge semantics, not garbage collection, so
        # background maintenance is switched off for these calls.
        return subprocess.run(
            ["git", "-c", "gc.auto=0", "-c", "maintenance.auto=false", *args],
            cwd=str(cwd), capture_output=True, text=True)

    def base(**over: Any) -> dict:
        item = {
            "id": "PI-1",
            "what": "one line",
            "origin": {"kind": "audit", "ref": "#1",
                       "rerun": "python3 scripts/ci/run_guards.py --all"},
            "due_when": {"kind": "observation", "clears_when": "x",
                         "check_every_days": 7},
            "next_action": "check_observation",
            "state": "queued",
        }
        item.update(over)
        return item

    if shutil.which("git") is None:
        check("git merge-safety control (skipped -- no git on PATH)", True)
        return

    for order in ("a-then-b", "b-then-a"):
        # ignore_cleanup_errors: the assertions above are the control; a
        # cleanup race on the throwaway repo must not read as a failed guard.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            repo = Path(td)
            git("init", "-q", "-b", "main", cwd=repo)
            git("config", "user.email", "test@example.invalid", cwd=repo)
            git("config", "user.name", "pipeline self-test", cwd=repo)

            store = repo / "docs" / "claude" / "work" / "pipeline"
            append(base(id="BASE"), store, intent="new")
            git("add", "-A", cwd=repo)
            git("commit", "-q", "-m", "base record", cwd=repo)

            git("checkout", "-q", "-b", "branch-a", cwd=repo)
            append(base(id="A", what="branch A's finding"), store, intent="new")
            git("add", "-A", cwd=repo)
            git("commit", "-q", "-m", "branch A appends one record", cwd=repo)

            git("checkout", "-q", "main", cwd=repo)
            git("checkout", "-q", "-b", "branch-b", cwd=repo)
            append(base(id="B", what="branch B's finding"), store, intent="new")
            git("add", "-A", cwd=repo)
            git("commit", "-q", "-m", "branch B appends one record", cwd=repo)

            git("checkout", "-q", "main", cwd=repo)
            first, second = (
                ("branch-a", "branch-b") if order == "a-then-b"
                else ("branch-b", "branch-a")
            )
            r1 = git("merge", "--no-edit", first, cwd=repo)
            r2 = git("merge", "--no-edit", second, cwd=repo)
            check(f"git merge-safety control ({order}): both merges succeed "
                  f"with NO conflict (plain git, no merge driver)",
                  r1.returncode == 0 and r2.returncode == 0)

            res = read_log(store)
            check(f"git merge-safety control ({order}): all three records "
                  f"survive the merge, none displaced",
                  set(res.items) == {"BASE", "A", "B"})


# ─────────────────────────────────────────────────────────────── self-test ──
def _selftest() -> int:
    failures: list[str] = []

    def check(label: str, cond: bool) -> None:
        print(f"  {'PASS' if cond else 'FAIL'}: {label}")
        if not cond:
            failures.append(label)

    def base(**over: Any) -> dict:
        item = {
            "id": "PI-20260921-0001",
            "what": "one line",
            "origin": {"kind": "audit", "ref": "#12674",
                       "rerun": "python3 scripts/ci/run_guards.py --all"},
            "due_when": {"kind": "observation", "clears_when": "the roster reads 3",
                         "check_every_days": 7, "last_checked": "2026-09-01"},
            "next_action": "check_observation",
            "state": "queued",
            "routed_to": None,
            "terminal_reason": None,
        }
        item.update(over)
        return item

    def refuses(label: str, item: dict, needle: str) -> None:
        try:
            validate(item)
        except PipelineError as exc:
            check(f"{label} (says why: {needle!r})", needle in str(exc))
        else:
            check(f"{label} — REFUSED", False)

    print("— the five failure reasons, each asserted —")
    check("a well-formed item validates (positive control)", validate(base()) is not None)

    refuses("reason (2): no due condition is refused",
            base(due_when={"kind": "observation", "check_every_days": 7}), "clears_when")
    refuses("reason (3): no rerun command is refused",
            base(origin={"kind": "audit", "ref": "#1", "rerun": "  "}), "origin.rerun")
    refuses("reason (4): `killed` with no reason is refused",
            base(state="killed"), "terminal_reason")
    refuses("reason (4): `done` with no reason is refused",
            base(state="done"), "terminal_reason")
    refuses("a terminal_reason on an OPEN item is refused",
            base(state="queued", terminal_reason="looks fine"), "non-terminal")
    refuses("`routed` with no destination is refused",
            base(state="routed"), "routed_to")
    check("…but `killed` WITH a reason is accepted",
          validate(base(state="killed", terminal_reason="unworked since 2026-06")) is not None)
    check("…and `routed` WITH a destination is accepted",
          validate(base(state="routed", routed_to="A7")) is not None)

    print("— reason (1): due is COMPUTED, not declared —")
    t = date(2026, 9, 21)
    check("an observation past its cadence is due",
          is_due(base(), t))
    check("…and the SAME item inside its cadence is NOT due (negative control)",
          not is_due(base(due_when={"kind": "observation", "clears_when": "x",
                                    "check_every_days": 7,
                                    "last_checked": "2026-09-20"}), t))
    check("a never-checked observation is due",
          is_due(base(due_when={"kind": "observation", "clears_when": "x",
                                "check_every_days": 30}), t))
    check("a future dated item is not due",
          not is_due(base(due_when={"kind": "date", "due_date": "2026-12-01"}), t))
    check("a past dated item is due",
          is_due(base(due_when={"kind": "date", "due_date": "2026-09-20"}), t))
    check("⚠️ an UNPARSEABLE due date is treated as DUE, not as quiet",
          is_due({"state": "queued", "due_when": {"kind": "date", "due_date": "soon"}}, t))
    check("a killed item is never due, whatever its clock says",
          not is_due(base(state="killed", terminal_reason="r",
                          due_when={"kind": "date", "due_date": "2020-01-01"}), t))

    print("— SOAK-WATCH: a soak item's due-ness is its graded state —")
    sk = base(due_when={"kind": "observation", "clears_when": "gate 2", "check_every_days": 7,
                        "last_checked": "2026-09-01", "soak": {"subject": "bybit_1/x"}})
    check("an ACCRUING soak is quiet even with its timer long elapsed",
          not is_due(sk, t, soak_states={"bybit_1/x": "accruing"}))
    for v in ("ready", "dead", "could-not-look"):
        check(f"a {v} soak is due", is_due(sk, t, soak_states={"bybit_1/x": v}))
    check("⚠️ an UNGRADED soak (absent from the state file) is could-not-look -> due, never quiet",
          is_due(sk, t, soak_states={}))
    refuses("a soak block with no subject is refused",
            base(due_when={"kind": "observation", "clears_when": "x", "soak": {}}), "due_when.soak")
    ob = base(observation={"what": "report produced", "how_to_check": "ls", "due_by": "2026-09-20"},
              due_when={"kind": "observation", "clears_when": "x", "check_every_days": 30,
                        "last_checked": "2026-09-20"})
    check("a one-shot observation past its due_by is due even with its timer not elapsed",
          is_due(ob, t))
    check("a one-shot observation before its due_by follows its timer",
          not is_due(dict(ob, observation=dict(ob["observation"], due_by="2026-12-01")), t))
    check("a non-soak item ignores soak states entirely",
          soak_verdict(base(), {"bybit_1/x": "dead"}) is None)

    print("— reason (5): the number the operator's page reports —")
    pool = [base(id="A", state="queued"),
            base(id="B", state="routed", routed_to="A7"),
            base(id="C", state="killed", terminal_reason="dead")]
    check("unrouted counts the due-and-unowned only",
          [i["id"] for i in unrouted(pool, t)] == ["A"])
    check("…and a routed item is still DUE (its owner is being tracked)",
          {i["id"] for i in due(pool, t)} == {"A", "B"})

    print("— re-date must carry a new observation (lapse wave) —")
    with tempfile.TemporaryDirectory() as td:
        rs = Path(td) / "p"
        t0 = date(2026, 10, 7)
        append(base(id="R", due_when={"kind": "observation", "clears_when": "x",
                                      "check_every_days": 7, "last_checked": "2026-10-01"}),
               rs, intent="new")
        prior = read_log(rs).items["R"]
        check("next_due_date folds cadence (last_checked + every)", next_due_date(prior) == date(2026, 10, 8))
        moved = json.loads(json.dumps(prior))
        moved["due_when"]["last_checked"] = "2026-10-07"
        check("a bare re-date (new date, no observation) is refused",
              bool(redate_problems(prior, moved)))
        try:
            redate("R", observation="too short", next_due=date(2026, 10, 20), store=rs, today=t0)
        except PipelineError:
            check("redate() refuses a one-word observation", True)
        else:
            check("redate() refuses a one-word observation", False)
        out = redate("R", observation="re-ran origin.rerun: 3 of 5 rows still reproduce; cause unchanged",
                     next_due=date(2026, 10, 20), store=rs, today=t0)
        check("redate() with a real observation lands and moves next-due",
              next_due_date(out) == date(2026, 10, 20) and read_log(rs).items["R"]["redate"]["observed_at"] == "2026-10-07")
        again = json.loads(json.dumps(read_log(rs).items["R"]))
        again["due_when"]["last_checked"] = "2026-10-20"
        again["due_when"]["check_every_days"] = 30
        check("repeating the previous observation verbatim is refused",
              any("repeats" in p for p in redate_problems(read_log(rs).items["R"], again)))
        shut = json.loads(json.dumps(prior))
        shut.update(state="killed", terminal_reason="r")
        check("closing is not a re-date (needs no observation)", redate_problems(prior, shut) == [])
        append(base(id="O", observation={"what": "w", "how_to_check": "h", "due_by": "2026-10-08"},
                    due_when={"kind": "observation", "clears_when": "x", "check_every_days": 7,
                              "last_checked": "2026-10-01"}), rs, intent="new")
        o = redate("O", observation="re-read the 05:30Z report: produced, no regression since", next_due=date(2026, 10, 25), store=rs, today=t0)
        check("redate() moves observation.due_by with the cadence (WFBREDQP-0001 trap)",
              o["observation"]["due_by"] == "2026-10-25" and not is_due(o, date(2026, 10, 24)))

    print("— one file per record, and the unreadable record —")
    with tempfile.TemporaryDirectory() as td:
        store = Path(td) / "pipeline"
        append(base(id="X"), store, intent="new")
        append(base(id="X", state="routed", routed_to="A7"), store, intent="update",
               based_on=version_of("X", store))
        append(base(id="Y"), store, intent="new")
        res = read_log(store)
        check("two records for one id fold to the LAST (append-only state)",
              res.items["X"]["state"] == "routed")
        check("…and the raw log kept BOTH (the audit trail survives, as "
              "separate files -- nothing is ever edited in place)",
              res.records == 3 and len(res.items) == 2
              and len(list(store.glob(f"*{RECORD_SUFFIX}"))) == 3)

        (store / "zz-malformed.json").write_text("{not json\n", encoding="utf-8")
        (store / "zz-not-an-object.json").write_text("[1,2]\n", encoding="utf-8")
        res2 = read_log(store)
        check("⚠️ a malformed record file is REPORTED, never skipped silently",
              len(res2.unreadable) == 2)
        check("…a JSON array file is also unreadable (it carries no id)",
              any("id" in why or "object" in why for _, why in res2.unreadable))
        check("…the good items still load (partial read is not total failure)",
              len(res2.items) == 2)
        check("…and `healthy` is False so nothing can call this clean",
              not res2.healthy and stats(res2, t)["readable"] is False)
        check("⚠️ section 0 SAYS the store is partly unreadable",
              "COULD NOT BE PARSED" in "\n".join(render_section_0(res2, t)))
        check("…and a HEALTHY store's section 0 does NOT say that "
              "(negative control)",
              "COULD NOT BE PARSED" not in "\n".join(render_section_0(res, t)))

        empty = read_log(Path(td) / "missing")
        check("a missing store is readable-and-empty, not an error",
              empty.healthy and not empty.items)

        legacy = Path(td) / "legacy.jsonl"
        legacy.write_text('{"not": "an id-bearing dict but still valid json"}\n')
        check("⚠️ a pre-existing FLAT FILE is still read (migration safety "
              "net) via the legacy line-based parser, not the directory one",
              len(read_log(legacy).unreadable) == 1)

    print("— a resurrected flat file next to the directory store is caught —")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "docs" / "claude" / "work"
        store = root / "pipeline"
        append(base(id="X"), store, intent="new")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = _check(store)
        check("PLANTED DEFECT: a resurrected sibling PIPELINE.jsonl FAILS "
              "_check(), never silently ignored",
              _resurrected_legacy_file(store) is None)
        check("…(negative control) no sibling file, and _check() proceeds "
              "to the ordinary verdict", rc == 0)

        (root / "PIPELINE.jsonl").write_text(
            json.dumps(base(id="Y")) + "\n", encoding="utf-8")
        out2 = io.StringIO()
        with contextlib.redirect_stdout(out2):
            rc2 = _check(store)
        check("PLANTED DEFECT: _check() now FAILS with the sibling file "
              "present, never printing a clean verdict over it",
              rc2 == 1 and "exists ALONGSIDE" in out2.getvalue())

    print("— an id-named record (it would fold out of time order) is caught —")
    with tempfile.TemporaryDirectory() as td:
        store = Path(td) / "pipeline"
        append(base(id="Z"), store, intent="new")
        with contextlib.redirect_stdout(io.StringIO()):
            rc_ok = _check(store)
        check("…(negative control) timestamp-named records only -> clean", rc_ok == 0)
        (store / "PI-Z.json").write_text(json.dumps(base(id="Z")), encoding="utf-8")
        out3 = io.StringIO()
        with contextlib.redirect_stdout(out3):
            rc3 = _check(store)
        check("PLANTED DEFECT: an id-named record file FAILS _check() "
              "(PI-20261002-APBY4NTV-0003)",
              rc3 == 1 and "PI-Z.json" in out3.getvalue())
        check("…and the failure NAMES the fix (the migration script)",
              "migrate_pipeline_to_dir.py" in out2.getvalue())

    print("— validation is enforced on the WRITE path, not just on read —")
    with tempfile.TemporaryDirectory() as td:
        store = Path(td) / "pipeline"
        try:
            append(base(state="killed"), store, intent="new")
        except PipelineError:
            check("append() refuses an invalid item", True)
        else:
            check("append() refuses an invalid item", False)
        check("…and wrote NOTHING when it refused", not store.exists())

    print("— NEW: two DIFFERENT findings must never share an id —")
    with tempfile.TemporaryDirectory() as td:
        store = Path(td) / "pipeline"
        append(base(id="COLL"), store, intent="new")

        try:
            append(base(id="COLL"), store, intent="new")
        except PipelineError as exc:
            check("intent='new' refuses a SECOND filing under an id that "
                  "already exists (says why: 'already exists')",
                  "already exists" in str(exc))
        else:
            check("intent='new' refuses a SECOND filing under an existing id",
                  False)

        try:
            append(base(id="COLL", what="a totally unrelated finding",
                        origin={"kind": "audit", "ref": "#999",
                                "rerun": "python3 other.py"}),
                   store, intent="update", based_on=version_of("COLL", store))
        except PipelineError as exc:
            check("intent='update' refuses a record whose identity "
                  "(what/origin) does not match the one it claims to update",
                  "does not look like a state change" in str(exc))
        else:
            check("intent='update' refuses a mismatched record", False)

        try:
            append(base(id="NEVER-FILED"), store, intent="update")
        except PipelineError as exc:
            check("intent='update' refuses when there is no prior record",
                  "no prior record" in str(exc))
        else:
            check("intent='update' refuses with no prior record", False)

        # …but a GENUINE state change under intent='update' is accepted —
        # the negative control that proves the checks above are measuring
        # the identity mismatch and not intent='update' itself.
        append(base(id="COLL", state="routed", routed_to="A7"), store,
               intent="update", based_on=version_of("COLL", store))
        # STALE-SNAPSHOT GUARD (PI-20261004-4GA8WQPA-0002): a writer holding the
        # version it read BEFORE that update is refused; one that omits it too.
        sstore = Path(td) / "stale"
        append(base(id="COLL"), sstore, intent="new")
        read_v = version_of("COLL", sstore)
        append(base(id="COLL", state="killed", terminal_reason="superseded"),
               sstore, intent="update", based_on=read_v)
        for label, kw, needle in (
                ("an update based on a SUPERSEDED version is refused", {"based_on": "00000000T000000000000Z-deadbeef.json"}, "STALE SNAPSHOT"),
                ("an update with NO based_on is refused", {}, "requires based_on")):
            try:
                append(base(id="COLL", state="routed", routed_to="A9"), sstore,
                       intent="update", **kw)
            except PipelineError as exc:
                check(label, needle in str(exc))
            else:
                check(label, False)
        check("…and the stale write never landed (state still killed)",
              read_log(sstore).items["COLL"]["state"] == "killed")
        check("an accepted update records the version it was based on",
              read_log(sstore).items["COLL"].get("based_on") == read_v)
        check("…but intent='update' WITH matching identity is accepted "
              "(negative control)",
              read_log(store).items["COLL"]["state"] == "routed")
        check("…and the store still carries ALL THREE records as separate "
              "files — refused writes never landed, accepted ones are never "
              "edited in place",
              len(list(store.glob(f"*{RECORD_SUFFIX}"))) == 2)

    print("— NEW: --check reports a collision instead of silently folding it —")
    with tempfile.TemporaryDirectory() as td:
        # Manufacture the exact live defect directly (bypassing append()'s
        # own refusal, the way an out-of-band write already did on main):
        # two DIFFERENT findings filed under the SAME id, as two files that
        # sort in the order they're meant to fold.
        store = Path(td) / "pipeline"
        store.mkdir()
        a = base(id="X", what="finding A", origin={"kind": "audit", "ref": "#1",
                                                     "rerun": "python3 a.py"})
        b = base(id="X", what="finding B",
                 origin={"kind": "session", "ref": "sess_2",
                         "rerun": "python3 b.py"})
        (store / "0001-a.json").write_text(json.dumps(a), encoding="utf-8")
        (store / "0002-b.json").write_text(json.dumps(b), encoding="utf-8")
        res = read_log(store)
        check("read_log() flags the id as a collision, not a clean fold",
              len(res.collisions) == 1 and res.collisions[0]["id"] == "X")
        check("…and the folded view still shows the LAST record unchanged — "
              "collisions are reported ALONGSIDE the fold, not instead of it",
              res.items["X"]["what"] == "finding B")

        clean = Path(td) / "clean"
        append(base(id="Y"), clean, intent="new")
        append(base(id="Y", state="routed", routed_to="A7"), clean,
               intent="update", based_on=version_of("Y", clean))
        check("…and a genuine state-change pair raises NO collision "
              "(negative control)",
              read_log(clean).collisions == [])

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = _check(store)
        check("_check() FAILS on a fresh (non-grandfathered) collision",
              rc == 1)
        check("…and its output never prints the bare-clean verdict over one",
              "pipeline: clean —" not in out.getvalue())

        # A GRANDFATHERED collision stays green (CI cannot rewrite
        # append-only history to repair PI-20260921-0002) but is still
        # reported loudly, never as "clean".
        graveyard = Path(td) / "grandfathered"
        graveyard.mkdir()
        gid = next(iter(GRANDFATHERED_COLLISIONS))
        ga = base(id=gid, what="finding A",
                  origin={"kind": "audit", "ref": "#1", "rerun": "python3 a.py"})
        gb = base(id=gid, what="finding B",
                  origin={"kind": "session", "ref": "sess_2",
                          "rerun": "python3 b.py"})
        (graveyard / "20260101T000001000000Z-a.json").write_text(json.dumps(ga), encoding="utf-8")
        (graveyard / "20260101T000002000000Z-b.json").write_text(json.dumps(gb), encoding="utf-8")
        out2 = io.StringIO()
        with contextlib.redirect_stdout(out2):
            rc2 = _check(graveyard)
        check("_check() still EXITS 0 on a KNOWN/grandfathered collision "
              "(pre-existing debt, not a fresh regression)", rc2 == 0)
        check("…but its output STILL never prints the bare-clean verdict — "
              "grandfathered is 'not blocking', never 'clean'",
              "pipeline: clean —" not in out2.getvalue())
        check("…and the collision is named in the output regardless "
              "(loud, not silently passed)",
              gid in out2.getvalue())

    print("— POSITIVE CONTROL: two branches each appending one record merge "
          "cleanly with plain git (the actual property this re-point buys) —")
    _check_git_merge_safety(check)

    print()
    if failures:
        print(f"SELF-TEST FAILED — {len(failures)} check(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("SELF-TEST PASS")
    return 0


def _resurrected_legacy_file(store: Path) -> Path | None:
    """The sibling flat file, if `store` is a directory AND that file exists.

    ⚠️ THE HAZARD THIS CATCHES (found in review, 2026-09-24, the day of the
    E64 re-point): at least 6 lane PRs were already appending to the OLD flat
    file when this migration merged. Each one now hits a modify/delete
    conflict on `docs/claude/work/PIPELINE.jsonl` (deleted here, modified by
    them) -- and a hand-resolved conflict can easily choose to KEEP the file.
    That resurrects a flat `PIPELINE.jsonl` sitting right next to the new
    directory, and nothing reads it: `read_log(store)` only uses the legacy
    parser when `store` itself IS a file, so a directory-store call ignores
    it completely. `--check` would report `clean` while any row filed there
    is invisible to `due()`/`render_section_0()` forever -- exactly the loss
    class this module exists to prevent, recreated one level up.
    """
    if not store.is_dir():
        return None
    legacy = store.parent / "PIPELINE.jsonl"
    return legacy if legacy.exists() else None


#: A record name that sorts chronologically: it starts with its own timestamp.
_TIMESTAMP_LED = re.compile(r"^\d{8}T\d{6}")


def _check(store: Path) -> int:
    """Validate every item in the real store. Used by the guard."""
    legacy = _resurrected_legacy_file(store)
    if legacy is not None:
        print(f"::error::pipeline: {legacy} exists ALONGSIDE the directory "
              f"store {store} -- a resurrected flat file (e.g. from a "
              f"hand-resolved modify/delete git conflict) is silently "
              f"invisible to read_log(): it only uses the legacy parser when "
              f"`store` itself IS a file. Any row appended there is LOST -- "
              f"due() never sees it and this very check would otherwise "
              f"stay green. Fix: run "
              f"`python3 scripts/ops/migrate_pipeline_to_dir.py --apply` to "
              f"move its records into {store} (it is safe to re-run: it "
              f"only migrates records not already present, matched on full "
              f"content), which also deletes {legacy}.")
        return 1

    res = read_log(store)
    bad: list[str] = []
    for item_id, item in sorted(res.items.items()):
        try:
            validate(item)
        except PipelineError as exc:
            bad.append(f"{item_id}: {exc}")

    s = stats(res)
    print(json.dumps(s, indent=2, sort_keys=True))

    if res.unreadable:
        print(f"\n::error::pipeline: {len(res.unreadable)} UNREADABLE record(s) — "
              f"a store that cannot be fully read must never render as an empty "
              f"or healthy pipeline.")
        for loc, why in res.unreadable:
            print(f"  {loc}: {why}")
    if bad:
        print(f"\n::error::pipeline: {len(bad)} INVALID item(s) — each names the "
              f"field and the failure reason it maps to.")
        for b in bad:
            print(f"  {b}")

    # ⚠️ A DISPLACEMENT IS NOT AN EMPTY-DENOMINATOR PROBLEM -- it is the
    # opposite: the store parses and validates cleanly while a filed finding
    # is invisible behind another record wearing its id. See the module
    # docstring's "SIXTH FAILURE MODE". Reported here, loudly, whether or not
    # it is grandfathered -- "clean" is never printed over any of them.
    new_collisions = [c for c in res.collisions if not _is_grandfathered_collision(c)]
    known_collisions = [c for c in res.collisions if _is_grandfathered_collision(c)]
    if res.collisions:
        print(f"\n::warning::pipeline: {len(res.collisions)} id(s) with a "
              f"DISPLACED/SHADOWED record — two records share an id without "
              f"the later one looking like a state change of the earlier, "
              f"so load() folded them and the earlier finding is invisible "
              f"to due()/render_section_0() even though nothing above failed.")
        for c in res.collisions:
            tag = ("GRANDFATHERED -- pre-existing, see GRANDFATHERED_COLLISIONS"
                   "/GRANDFATHERED_COLLISION_RECORDS"
                   if _is_grandfathered_collision(c) else "NEW")
            print(f"  {c['id']} at {c['at']}: {c['reason']} [{tag}]")

    # ⚠️ load() folds in FILENAME order, and that is chronological only for a
    # name that starts with its timestamp. An id-named record ('PI-....json')
    # sorts AFTER every '2026...' record, so it silently overrides any later
    # update or closure of the same id (PI-20261002-APBY4NTV-0003: the
    # manager's closure of PI-20261002-UBMITVIS-0001 folded as 'queued').
    misnamed = sorted(p.name for p in store.glob(f"*{RECORD_SUFFIX}")
                      if not _TIMESTAMP_LED.match(p.name))
    if misnamed:
        print(f"\n::error::pipeline: {len(misnamed)} record file(s) not named "
              f"by their timestamp. load() folds in filename order, so such a "
              f"record is folded out of time order and can SHADOW a later "
              f"update of its id. Rename each to the "
              f"`YYYYMMDDTHHMMSS...Z-<suffix>.json` of its creation commit.")
        for name in misnamed:
            print(f"  {name}")

    if res.unreadable or bad or new_collisions or misnamed:
        return 1

    # ⚠️ THE DENOMINATOR IS PART OF THE VERDICT, not decoration. A bare
    # "clean" over an empty or truncated store reads as a clean bill of health
    # for nothing — the exact failure this module's docstring names, caught
    # here by diagnostic-provenance-guard on this file's own first commit.
    # ⚠️ AND IT IS NEVER PRINTED OVER A COLLISION, grandfathered or not — a
    # store with a displacement is not clean, even when CI must stay green
    # over a pre-existing one it cannot rewrite history to repair.
    if known_collisions:
        print(f"\npipeline: NOT clean — {len(res.items)} item(s) validated over "
              f"{res.records} record(s); {s['due']} due, {s['unrouted']} "
              f"unrouted; {len(known_collisions)} KNOWN/GRANDFATHERED id "
              f"collision(s) above (not blocking, but not clean)")
        return 0
    print(f"\npipeline: clean — {len(res.items)} item(s) validated over "
          f"{res.records} record(s); {s['due']} due, {s['unrouted']} unrouted")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="validate every item in the store")
    ap.add_argument("--due", action="store_true", help="render brief section 0")
    ap.add_argument("--all", action="store_true",
                    help="with --due: render every due item (no cap, no truncation)")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--version-of", metavar="ID",
                    help="print the version token of ID's latest record (the "
                         "value append(..., based_on=) needs) and exit")
    ap.add_argument("--redate", metavar="ID",
                    help="re-date ID: needs --observation and --next-due (see redate())")
    ap.add_argument("--observation", help="with --redate: what you READ this time")
    ap.add_argument("--next-due", help="with --redate: YYYY-MM-DD")
    ap.add_argument("--evidence", help="with --redate: command/ref the observation came from")
    ap.add_argument("--mint-id", metavar="SESSION_REF",
                    help="print a fresh, session-scoped id and exit -- see "
                         "mint_id()'s docstring for why it is scoped this way")
    ap.add_argument("--store", default=str(STORE))
    args = ap.parse_args(argv)

    if args.self_test:
        return _selftest()
    store = Path(args.store)
    if args.redate:
        if not (args.observation and args.next_due):
            ap.error("--redate needs --observation and --next-due")
        try:
            rec = redate(args.redate, observation=args.observation,
                         next_due=_parse_date(args.next_due, "--next-due"),
                         store=store, evidence=args.evidence)
        except PipelineError as exc:
            print(f"pipeline: refused -- {exc}")
            return 1
        print(f"pipeline: re-dated {rec['id']} -> next due {next_due_date(rec)}")
        return 0
    if args.version_of:
        v = version_of(args.version_of, store)
        print(v if v else "")
        return 0 if v else 1
    if args.mint_id:
        print(mint_id(args.mint_id, store=store))
        return 0
    if args.check:
        return _check(store)
    res = read_log(store)
    if args.stats:
        print(json.dumps(stats(res), indent=2, sort_keys=True))
        return 0 if res.healthy else 1
    print("\n".join(render_section_0(res, limit=None if args.all else SECTION0_CAP)))
    return 0 if res.healthy else 1


if __name__ == "__main__":
    sys.exit(main())
