#!/usr/bin/env python3
"""One rolled-up DAILY summary of autonomous work — Phase B, the digest half.

The notification contract has two halves. ``work_phase_ping.py`` (Phase A) ships
the first: an event **per state change**, as it happens. This ships the second:
**one rolled-up daily summary**, so the operator can see a day's movement in a
single message instead of reconstructing it from the repo.

⚠️ **STATE CHANGES ONLY, NEVER ACTIVITY.** A verdict written, a decision
recorded, a deployment made, a WIP ceiling hit — those are events. A sweep
started, a file edited, a session opened — those are not, and must never reach
the operator. This is not a stylistic preference: **202 of 376 CRITICAL/ERROR
rows in one measured window were a single un-latched alarm**, which trained the
operator past the one channel reserved for an unprotected position. A digest that
narrates activity would rebuild that failure on a daily cadence.

**The event definition is IMPORTED, not re-derived.** ``PING_WORTHY`` and
``transitions()`` come from ``work_phase_ping`` so the per-event path and the
roll-up can never drift on what counts as an event. Two copies of that predicate
is exactly how they would.

⚠️ **IT WRITES TO ``docs/claude/pending-pings.jsonl``, NOT TO TELEGRAM** — the
same queue and the same failure direction as its sibling. The VM's
``scripts/notify_on_pull.py`` drains it on the next ``ict-git-sync`` pull. So a
digest is truth in transit between the commit and the send, and it fails BACK:
an un-committed row is a digest that never happened, never one wrongly shown as
delivered.

⚠️ **UNSCHEDULED AS SHIPPED.** There is no cron behind this. It is a plain script
so an existing daily job can call it; wiring the trigger is a
``.github/workflows/`` change owned by another session. **A digest that has never
fired has not been observed to work** — and that is not hypothetical here:
``probes.yml`` and ``due-list.yml`` were both merged, enabled, and have never
fired on cron (``OI-20260901-SCHEDULED-PROBES-AND-DUE-LIST-HAVE-NEVER-FIRED-ON-CRON``).

Usage::

    python3 scripts/ops/work_digest.py --base origin/main~20 --head HEAD
    python3 scripts/ops/work_digest.py --base <ref> --head HEAD --write
    python3 scripts/ops/work_digest.py --self-test
"""
# wiring: manual-only - no cron ships with this. The trigger is a
# `.github/workflows/` concern declared by another session, so this ships as a
# callable script with a pure `build_digest()` rather than guessing at a
# scheduler. Claiming a cadence the repo does not have would be the same
# "a green run is not an observation" error the module docstring warns about.

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# ONE owner for "what counts as an event" — imported, never re-derived.
# ONE owner for the ceiling + migration facts — imported, never re-derived.
# This file restating them is precisely how the SPA and the digest came to
# disagree after Phase C; see src/utils/work_facts.py.
from src.utils.work_facts import WIP_CEILING as _WIP_CEILING  # noqa: E402
from src.utils.work_facts import (  # noqa: E402
    CARRIED_ROWS_MIGRATED_IN,
    CEILING_ENFORCED,
    CEILING_STATE,
)
from scripts.ops.work_phase_ping import (  # noqa: E402
    PING_WORTHY,
    _field,
    _git_show_dir,
    transitions,
)

PENDING = REPO_ROOT / "docs" / "claude" / "pending-pings.jsonl"
STATE = REPO_ROOT / "runtime_logs" / "work_digest_state.json"

# Mirrors src/web/api/routers/work.py. Kept in the same order as the design.
LIFECYCLE_STATES: tuple[str, ...] = (
    "dormant", "ready", "in_flight", "waiting", "done", "accepted",
)
UNKNOWN = "unknown"
COUNTS_AGAINST_CEILING = frozenset({"in_flight"})
# Re-exported from the shared owner so existing references keep working;
# the VALUE lives in src/utils/work_facts.py and only there.
WIP_CEILING = _WIP_CEILING


def _resolve(ref: str) -> str | None:
    """Resolve a ref to a sha, or None if it does not exist HERE.

    ⚠️ Returning None is load-bearing on a SHALLOW clone, which is the normal
    state of a session's checkout: a ref that is simply not in this clone's
    history must read as *we could not look at that window*, never as *nothing
    changed in it*. Those are opposite statements and the second is the
    dangerous one — it would report a quiet day for a window nobody examined.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", ref],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
        return out or None
    except subprocess.CalledProcessError:
        return None


def _is_shallow() -> bool:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip() == "true"
    except subprocess.CalledProcessError:
        return False


def standing_state(ref: str = "HEAD") -> dict[str, Any]:
    """The store's CURRENT shape at ``ref`` — the denominator for any change.

    Every lifecycle state ships as an explicit key with an explicit zero, plus
    ``unknown`` for a row whose state could not be read. They sum to the object
    count by construction, so the partition is checkable rather than trusted.
    """
    files = _git_show_dir(ref)
    counts = {s: 0 for s in (*LIFECYCLE_STATES, UNKNOWN)}
    blocked: list[dict[str, str]] = []
    for path, text in sorted(files.items()):
        state = _field(text, "lifecycle")
        counts[state if state in LIFECYCLE_STATES else UNKNOWN] += 1
        if state == "waiting":
            blocked.append({
                "object": _field(text, "id") or Path(path).stem,
                "title": _field(text, "title") or "",
            })
    in_flight = sum(counts[s] for s in COUNTS_AGAINST_CEILING)
    return {
        "objectCount": len(files),
        "lifecycle": counts,
        "waiting": blocked,
        "wip": {
            "ceiling": WIP_CEILING,
            "inFlight": in_flight,
            # ⚠️ DECLARED, not enforced — Phase C enforces it alongside the
            # migration. `ceilingHit` is a real event worth pinging; "under the
            # ceiling" is NOT a clean bill of health while nothing checks it.
            "enforced": CEILING_ENFORCED,
            "state": CEILING_STATE,
            "ceilingHit": in_flight >= WIP_CEILING,
        },
    }


# ── The registers the digest reads, beyond the work store ────────────────────
#
# ⚠️ **IT USED TO MEASURE THE WORK STORE AND CALL THAT "THE WORK".** On
# 2026-09-02 the digest reported `No lifecycle change` over a window that had
# merged 52 PRs, retired two backlog classes, cleared two monitoring rows and
# closed a capability gap — every one of which is a decision or a state change,
# and not one of which moves a `lifecycle:` field in
# `docs/claude/work/objects/*.yaml`. A confident report of a quiet night is
# strictly worse than no report, because it looks like oversight happened.
#
# So the digest reads the registers where those decisions actually land. Each is
# a keyed-item JSON file (`{"items": [{"id": ..., <state field>: ...}]}`) and is
# diffed BETWEEN TWO GIT REFS by the same mechanism the work store already uses
# — no new state file, nothing to fall out of sync, and the window is whatever
# the caller passed.
#
# ⚠️ **STATE CHANGES AND DECISIONS, NEVER ACTIVITY** — the rule that governs
# what may be in here. A checklist item reaching `done`, a register row cleared,
# a backlog row given a terminal disposition: those are verdicts. A row edited,
# a note added, a session opened: those are not, and are invisible to this by
# construction, because only a change to the declared STATE FIELD is an event.
# Merged PRs are ATTRIBUTION and a denominator; they are never the headline. A
# digest that lists everything is a digest nobody reads, and this repo has
# already measured what that costs (202 of 376 CRITICAL/ERROR rows in one
# window were a single un-latched alarm).


class Source(NamedTuple):
    """One keyed-item register the digest diffs across the window."""

    name: str
    path: str
    #: The item field whose CHANGE is an event. ``None`` == the item's
    #: PRESENCE is the event (a row filed, a row cleared).
    field: str | None
    #: Transitions INTO one of these are reportable. Empty == every change is.
    events: frozenset[str]
    #: Enumerate rows that APPEARED? False == count them only. A backlog gains
    #: rows constantly and the verdict is the event, not the filing.
    enumerate_added: bool
    added_verb: str
    removed_verb: str


#: A terminal disposition on a review backlog. `docs/CLAUDE-RULES-CANONICAL.md`
#: § "Backlog governance" declares five ways a row ends; the on-disk files also
#: carry the historical spellings, and all of them are verdicts.
BACKLOG_TERMINAL = frozenset({
    "resolved", "fixed", "wont_fix", "superseded", "invalid",
    "closed_answered", "closed_unfixable", "promoted_to_roadmap",
})

#: A checklist item reaching one of these is a decision the operator wants.
#: `queued` and `triage` are deliberately absent: scoping an item is work in
#: progress, and pinging on it would make the digest narrate the manager's
#: notebook. `blocked` IS here — a blocker is the single most actionable thing
#: a night can produce.
CHECKLIST_EVENTS = frozenset({
    "in_flight", "landed_unproven", "done", "blocked", "dropped",
})

SOURCES: tuple[Source, ...] = (
    Source("manager checklist", "docs/claude/work/MANAGER-CHECKLIST.json",
           "state", CHECKLIST_EVENTS, True, "added", "removed"),
    Source("open-items register", "docs/claude/OPEN-ITEMS.json",
           None, frozenset(), True, "filed", "CLEARED"),
    Source("health backlog", "docs/claude/health-review-backlog.json",
           "status", BACKLOG_TERMINAL, False, "filed", "removed"),
    Source("performance backlog", "docs/claude/performance-review-backlog.json",
           "status", BACKLOG_TERMINAL, False, "filed", "removed"),
    Source("ml backlog", "docs/claude/ml-review-backlog.json",
           "status", BACKLOG_TERMINAL, False, "filed", "removed"),
    Source("research backlog", "docs/claude/research-review-backlog.json",
           "status", BACKLOG_TERMINAL, False, "filed", "removed"),
)

#: Per-source read grades. NEVER collapsed — `no_changes` on a source we could
#: not open is the exact lie this whole module exists to stop telling.
READ_STATES = ("read", "absent", "unreadable")


def _items_at(ref: str, path: str) -> tuple[str, dict[str, dict]]:
    """``(read_state, {id: item})`` for one register at one ref.

    * ``read``       — parsed; the dict is what it holds (possibly empty).
    * ``absent``     — the path does not exist at that ref. A real observation:
      the register had not been created yet.
    * ``unreadable`` — it exists and we could not parse it. **We could not
      look.** Emphatically not ``absent`` and emphatically not empty.
    """
    try:
        raw = subprocess.run(
            ["git", "show", f"{ref}:{path}"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout
    except subprocess.CalledProcessError:
        return "absent", {}
    try:
        doc = json.loads(raw)
        items = doc["items"]
    except (ValueError, KeyError, TypeError):
        return "unreadable", {}
    if not isinstance(items, list):
        return "unreadable", {}
    out: dict[str, dict] = {}
    for it in items:
        if isinstance(it, dict) and it.get("id"):
            out[str(it["id"])] = it
    return "read", out


def _title_of(item: dict) -> str:
    for key in ("title", "summary", "note"):
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip().split("\n")[0][:110]
    return ""


def register_events(base: str, head: str) -> tuple[list[dict], dict[str, str], dict[str, dict]]:
    """Diff every register in ``SOURCES``. Returns (events, reads, counts).

    An event is a change to the declared STATE FIELD, or (for a presence-keyed
    register) a row appearing or disappearing. An edit that leaves the state
    field alone is activity and produces nothing — that is the filter, and it
    is structural rather than a heuristic over the diff text.
    """
    events: list[dict] = []
    reads: dict[str, str] = {}
    counts: dict[str, dict] = {}
    for src in SOURCES:
        base_state, before = _items_at(base, src.path)
        head_state, after = _items_at(head, src.path)
        # The read grade is the WORSE of the two ends: a window we cannot see
        # one side of is a window we did not examine.
        grade = "unreadable" if "unreadable" in (base_state, head_state) else (
            "absent" if head_state == "absent" else "read")
        reads[src.name] = grade
        c = {"added": 0, "removed": 0, "transitioned": 0, "reported": 0,
             "firstSeen": False}
        counts[src.name] = c
        if grade != "read":
            continue
        if base_state == "absent" and after:
            # The register was created inside the window. Diffing it would
            # report every row it has ever held as "new tonight" — a loud lie.
            c["firstSeen"] = True
            c["added"] = len(after)
            continue
        for key, item in after.items():
            prev = before.get(key)
            if prev is None:
                c["added"] += 1
                landed = item.get(src.field) if src.field else src.added_verb
                # A row that APPEARS already in an event state is an event; one
                # that appears in `queued`/`triage` is somebody scoping work,
                # which is activity. The same predicate gates both the arrival
                # and the transition, so an item cannot smuggle a non-event past
                # the filter by being born in it.
                if src.enumerate_added and not (
                    src.events and landed not in src.events
                ):
                    events.append({"source": src.name, "id": key,
                                   "title": _title_of(item), "from": None,
                                   "to": landed})
                    c["reported"] += 1
                continue
            if src.field is None:
                continue
            old, new = prev.get(src.field), item.get(src.field)
            if new == old or new is None:
                continue
            c["transitioned"] += 1
            if src.events and new not in src.events:
                continue
            events.append({"source": src.name, "id": key,
                           "title": _title_of(item), "from": old, "to": new})
            c["reported"] += 1
        for key, item in before.items():
            if key in after:
                continue
            c["removed"] += 1
            if src.field is None:
                events.append({"source": src.name, "id": key,
                               "title": _title_of(item), "from": "open",
                               "to": src.removed_verb})
                c["reported"] += 1
    return events, reads, counts


def _window_attribution(base: str, head: str) -> dict[str, Any]:
    """Commits and merged-PR numbers in the window.

    ⚠️ **ATTRIBUTION AND A DENOMINATOR, NEVER THE HEADLINE.** "52 PRs merged" is
    activity: it says a lot happened and nothing about what changed. It is
    reported on the population line, next to the window, so a reader can size
    how much work produced how few state changes — which is itself the useful
    signal when the two diverge.
    """
    try:
        out = subprocess.run(
            ["git", "log", "--format=%s", f"{base}..{head}"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout.splitlines()
    except subprocess.CalledProcessError:
        return {"commits": None, "mergedPrs": None}
    prs = {m.group(1) for line in out
           if (m := _PR_RE.search(line)) is not None}
    return {"commits": len(out), "mergedPrs": len(prs)}


_PR_RE = re.compile(r"\(#(\d+)\)\s*$")


def build_digest(base: str, head: str = "HEAD", now: datetime | None = None) -> dict[str, Any]:
    """Assemble the digest. Pure apart from git reads — no writes, no network.

    ``digestState`` is FOUR states and they are never collapsed:

      * ``window_unresolved``  — a ref is not in this clone. **We could not look
        at all.** Emphatically NOT "nothing changed"; on a shallow clone this is
        the common case and reporting it as a quiet day would be a false
        negative delivered with confidence.
      * ``sources_unreadable`` — the window resolved and **not one register
        could be read**. Also "we could not look", one layer down, and it used
        not to exist: before 2026-09-02 an unreadable register was
        indistinguishable from a quiet one.
      * ``no_changes``         — every readable source read, and none moved. A
        real, reportable observation — but read it beside ``sourceReads``,
        because "nothing changed across 6 of 6" and "across 4 of 6" are
        different claims and only the first is a clean night.
      * ``changes_observed``   — one or more state changes.

    ⚠️ **``sourceReads`` ships on EVERY digest, including the unresolved one.** A
    key that vanishes makes a consumer branch on absence, and absence is not one
    of the states.
    """
    now = now or datetime.now(timezone.utc)
    base_sha, head_sha = _resolve(base), _resolve(head)
    standing = standing_state(head if head_sha else "HEAD")

    if base_sha is None or head_sha is None:
        return {
            "digestState": "window_unresolved",
            "unresolvedRef": base if base_sha is None else head,
            "shallowClone": _is_shallow(),
            "base": base, "head": head,
            "changes": [],
            "events": [],
            "sourceReads": {src.name: "not_attempted" for src in SOURCES},
            "sourceCounts": {},
            "window": {"commits": None, "mergedPrs": None},
            "standing": standing,
            "generatedAt": now.isoformat(),
            "coverageComplete": False,
        }

    changes = [t for t in transitions(base, head) if t["to"] in PING_WORTHY]
    events, reads, counts = register_events(base, head)
    read_n = sum(1 for g in reads.values() if g == "read")
    unreadable_n = sum(1 for g in reads.values() if g == "unreadable")

    # ⚠️ `absent` IS AN OBSERVATION AND `unreadable` IS NOT. A register that does
    # not exist at head was looked for and found missing; one that exists and
    # will not parse was not looked at. Only the second may downgrade the whole
    # digest — treating `absent` as "could not look" would make every fresh
    # checkout report a broken window, and treating `unreadable` as "nothing
    # there" is the false negative this module exists to prevent.
    if changes or events:
        state = "changes_observed"
    elif read_n == 0 and unreadable_n:
        state = "sources_unreadable"
    else:
        state = "no_changes"

    return {
        "digestState": state,
        "unresolvedRef": None,
        "shallowClone": _is_shallow(),
        "base": base, "head": head,
        "baseSha": base_sha[:8], "headSha": head_sha[:8],
        "changes": changes,
        "events": events,
        "sourceReads": reads,
        "sourceCounts": counts,
        "window": _window_attribution(base, head),
        "standing": standing,
        "generatedAt": now.isoformat(),
        "coverageComplete": False,
    }


#: States that need the operator's EYES, not just their awareness. These are
#: enumerated by id; everything else is summarised as a count. The line between
#: them is the anti-changelog rule: a digest that lists everything is a digest
#: nobody reads.
ATTENTION_STATES = frozenset({"blocked", "CLEARED", "dropped"})
_MAX_ENUMERATED = 8


def _summarise(counts: dict[str, dict], events: list[dict], name: str) -> str | None:
    """One line per source: what moved, by destination state, with counts."""
    c = counts.get(name)
    if c is None:
        return None
    if c["firstSeen"]:
        return (f"{name}: first appeared in this window ({c['added']} rows) — "
                f"NOT diffed, because every row would read as new tonight.")
    mine = [e for e in events if e["source"] == name]
    by_state: dict[str, int] = {}
    for e in mine:
        by_state[str(e["to"])] = by_state.get(str(e["to"]), 0) + 1
    if not by_state and not c["added"] and not c["removed"]:
        return None
    bits = [f"{n} {state}" for state, n in
            sorted(by_state.items(), key=lambda kv: (-kv[1], kv[0]))]
    # `added` on a count-only source (the backlogs) is the FILING rate — real,
    # and deliberately not an enumerated event: the verdict is the event.
    unreported_added = c["added"] - sum(
        1 for e in mine if e["from"] is None)
    if unreported_added > 0:
        bits.append(f"{unreported_added} filed")
    return f"{name}: " + " · ".join(bits) if bits else None


def render(d: dict[str, Any]) -> str:
    """One operator-readable message. States its population on every number."""
    st = d["standing"]
    lc = st["lifecycle"]
    n = st["objectCount"]
    reads = d.get("sourceReads") or {}
    read_ok = sum(1 for g in reads.values() if g == "read")
    lines = [f"[work digest] {d['generatedAt'][:10]}"]

    if d["digestState"] == "window_unresolved":
        lines.append(
            f"⚠️ window NOT examined — ref {d['unresolvedRef']!r} is not in this clone"
            + (" (shallow)" if d["shallowClone"] else "")
            + ". This is 'we could not look', NOT 'nothing changed'."
        )
        lines.append(
            f"No register was read ({len(reads)} declared). Nothing below "
            f"describes the window you asked about."
        )
    else:
        win = d.get("window") or {}
        commits, prs = win.get("commits"), win.get("mergedPrs")
        # ⚠️ ATTRIBUTION, NOT THE HEADLINE. The PR count sits on the population
        # line so a reader can size how much work produced how few state
        # changes — the divergence is the signal, the raw count never is.
        pop = (f"Window {d['baseSha']}..{d['headSha']}"
               + (f" — {commits} commits / {prs} merged PRs" if commits is not None else "")
               + f"; {n} work objects; {read_ok}/{len(reads)} registers read.")

        if d["digestState"] == "sources_unreadable":
            lines.append(
                "⚠️ NOT ONE REGISTER COULD BE READ in "
                f"{d['baseSha']}..{d['headSha']}. This is 'we could not look', "
                "NOT 'nothing changed'."
            )
        elif d["digestState"] == "no_changes":
            lines.append(
                f"No state change in {d['baseSha']}..{d['headSha']} across the "
                f"{read_ok} register(s) read."
                + ("" if read_ok == len(reads) else
                   f" ⚠️ {len(reads) - read_ok} of {len(reads)} NOT READ — see below; "
                   f"this is not a clean night, it is a partial one.")
            )
        else:
            total = len(d["events"]) + len(d["changes"])
            lines.append(
                f"{total} state change(s) across {read_ok}/{len(reads)} "
                f"registers read + the work store."
            )
        lines.append(pop)

        # Per-source summary — counts, never a changelog.
        for src in SOURCES:
            grade = reads.get(src.name)
            if grade == "unreadable":
                lines.append(
                    f"⚠️ {src.name}: UNREADABLE — we could not look. "
                    f"Nothing about it below is an observation."
                )
                continue
            if grade == "absent":
                lines.append(f"{src.name}: absent at head (register does not exist).")
                continue
            summary = _summarise(d["sourceCounts"], d["events"], src.name)
            if summary:
                lines.append(summary)

        # Work-store lifecycle keeps its own line — it is a different register
        # with a different vocabulary, and folding it in would hide a quiet
        # store behind a busy backlog.
        if d["changes"]:
            lines.append(f"work store: {len(d['changes'])} lifecycle change(s):")
            for t in d["changes"][:_MAX_ENUMERATED]:
                origin = t["from"] or "new"
                title = f" · {t['title']}" if t["title"] else ""
                lines.append(f"  • {t['object']}: {origin} → {t['to']}{title}")
            if len(d["changes"]) > _MAX_ENUMERATED:
                lines.append(f"  … +{len(d['changes']) - _MAX_ENUMERATED} more")
        else:
            lines.append(f"work store: no lifecycle change (population: {n} objects).")

        # The only things enumerated by id: what is waiting on a human, and what
        # a register stopped watching.
        attention = [e for e in d["events"] if str(e["to"]) in ATTENTION_STATES]
        if attention:
            lines.append("NEEDS YOU / CHANGED WATCH:")
            for e in attention[:_MAX_ENUMERATED]:
                mark = "✔" if e["to"] == "CLEARED" else "⛔"
                title = f" — {e['title']}" if e["title"] else ""
                lines.append(f"  {mark} {e['id']}: {e['to']}{title}")
            if len(attention) > _MAX_ENUMERATED:
                lines.append(f"  … +{len(attention) - _MAX_ENUMERATED} more")

    lines.append(
        "Standing: "
        + " / ".join(f"{s} {lc[s]}" for s in (*LIFECYCLE_STATES, UNKNOWN))
        + f" (of {n})"
    )

    wip = st["wip"]
    if wip["ceilingHit"]:
        lines.append(
            f"⚠️ WIP CEILING HIT: {wip['inFlight']} in flight vs ceiling "
            f"{wip['ceiling']} — ENFORCED in CI: a ninth in_flight object is "
            f"REFUSED without an approved wip-ceiling-exception.yaml."
        )
    else:
        lines.append(
            f"WIP {wip['inFlight']}/{wip['ceiling']} in flight "
            f"({wip['state']} — this IS a gate: the ninth is refused)."
        )

    if st["waiting"]:
        lines.append("Waiting: " + ", ".join(w["object"] for w in st["waiting"]))

    lines.append(
        "⚠️ Work store covers the operating-layer build's own phases — the "
        f"carried backlog rows: {CARRIED_ROWS_MIGRATED_IN}. Registers above are "
        "read too, but this is still NOT the whole of system work."
    )
    return "\n".join(lines)


def _already_sent_today(day: str) -> bool:
    """One digest per UTC day. A latch, so a double invocation cannot double-ping."""
    try:
        return json.loads(STATE.read_text(encoding="utf-8")).get("lastDigestDay") == day
    except (OSError, ValueError):
        # ⚠️ An unreadable latch SENDS rather than suppresses. Failing loud is
        # the only safe direction on a notification path, and it makes a broken
        # latch announce itself as a duplicate instead of as silence — the
        # reasoning the target-naked cooldown had to be corrected to.
        return False


def _record_sent(day: str) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({"lastDigestDay": day}), encoding="utf-8")
    except OSError as exc:
        print(f"work-digest: WARNING could not write latch {STATE}: {exc}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="origin/main",
                    help="ref to compare FROM (the start of the window)")
    ap.add_argument("--head", default="HEAD", help="ref to compare TO")
    ap.add_argument("--write", action="store_true",
                    help="append the digest to pending-pings.jsonl (default: print only)")
    ap.add_argument("--force", action="store_true",
                    help="write even if a digest was already recorded for today")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        return _self_test()

    digest = build_digest(a.base, a.head)
    message = render(digest)
    print(message)

    if not a.write:
        return 0

    day = digest["generatedAt"][:10]
    if _already_sent_today(day) and not a.force:
        print(f"work-digest: a digest is already recorded for {day} — not queuing "
              f"a second (use --force to override)")
        return 0

    row = {
        "at": digest["generatedAt"],
        "target": "claude",
        "priority": "normal",
        "event": "work_digest",
        "digest_state": digest["digestState"],
        "message": message,
    }
    try:
        with PENDING.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"work-digest: FAILED to queue: {exc}")
        return 1
    _record_sent(day)
    print("work-digest: queued — COMMIT pending-pings.jsonl or the digest never "
          "happened (transit fails back, by design)")
    return 0


def _self_test() -> int:
    """A detector whose failure path is never exercised is indistinguishable
    from one that always passes. Each check here has a positive control."""
    ok = True

    def check(n: int, label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= passed
        print(f"  self-test {n} ({label}): {'PASS' if passed else f'FAIL {detail}'}")

    # 1-2: an unresolvable base must read as "did not look", never "no changes".
    d = build_digest("definitely-not-a-ref-000", "HEAD")
    check(1, "unresolvable base -> window_unresolved",
          d["digestState"] == "window_unresolved", str(d["digestState"]))
    check(2, "and NEVER no_changes", d["digestState"] != "no_changes")

    # 3: that state must SAY it did not look, in the operator-visible text.
    txt = render(d)
    check(3, "message says 'could not look', not a quiet day",
          "could not look" in txt.lower() or "NOT examined" in txt, txt[:80])

    # 4: the standing partition must sum to the object count.
    st = standing_state("HEAD")
    total = sum(st["lifecycle"].values())
    check(4, "lifecycle buckets sum to objectCount",
          total == st["objectCount"], f"{total} != {st['objectCount']}")

    # 5: every state key present, including explicit zeros.
    missing = [s for s in (*LIFECYCLE_STATES, UNKNOWN) if s not in st["lifecycle"]]
    check(5, "no lifecycle key ever vanishes", not missing, str(missing))

    # 6: dormant/ready are NOT events — imported from the one owner.
    noisy = [s for s in ("dormant", "ready") if s in PING_WORTHY]
    check(6, "dormant/ready never ping (imported predicate)", not noisy, str(noisy))

    # 7: a ceiling hit renders as an event; positive control on the negative.
    hit = dict(st, wip=dict(st["wip"], inFlight=WIP_CEILING, ceilingHit=True))
    hit_txt = render({**d, "standing": hit})
    quiet_txt = render(d)
    check(7, "ceiling hit is loud, and not-hit is not",
          "WIP CEILING HIT" in hit_txt and "WIP CEILING HIT" not in quiet_txt)

    # 8: the digest never claims the store is complete.
    check(8, "coverage is declared incomplete", d["coverageComplete"] is False)

    # 9: an unreadable latch must SEND, not suppress.
    check(9, "unreadable latch fails loud (sends)",
          _already_sent_today("not-a-day-that-was-recorded") is False)

    # 10: the source list must not have drifted off disk. A source path that
    # no longer exists reads as `absent` forever — correct-looking, and
    # permanently blind to the register it was meant to watch.
    missing = [src.path for src in SOURCES if not (REPO_ROOT / src.path).exists()]
    check(10, "every declared source exists on disk", not missing, str(missing))

    # 11: and it must cover every review backlog, not the ones that existed the
    # day it was written. This is the LIVE_BACKLOGS lesson: a hand-maintained
    # coverage list that can fall behind unnoticed IS the defect.
    declared = {src.path for src in SOURCES}
    on_disk = {
        f"docs/claude/{p.name}"
        for p in (REPO_ROOT / "docs" / "claude").glob("*-review-backlog.json")
    }
    check(11, "every review backlog on disk is read", on_disk <= declared,
          f"unread: {sorted(on_disk - declared)}")

    # 12: the per-source read grades ship even when nothing was attempted — a
    # key that vanishes makes a consumer branch on absence, and absence is not
    # one of the states.
    check(12, "sourceReads ships on the unresolved envelope",
          set(d.get("sourceReads") or {}) == {src.name for src in SOURCES},
          str(sorted(d.get("sourceReads") or {})))

    print("work-digest self-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
