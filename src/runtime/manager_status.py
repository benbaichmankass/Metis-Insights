"""The manager status readout, rendered for Telegram (`/status`).

Operator directive, 2026-09-01, quoted verbatim in ``CLAUDE.md``:

    "every manager session keeps a detailed checklist of work items in its
     scope ... and every status update should start with the checklist, then
     summary of what has been done (recently) and what's next."

**The order is the contract: checklist -> recently done -> next.** A status
update that opens with a narrative is not following it, so this module renders
those sections in that order and ``tests/test_manager_status.py`` asserts the
ordering rather than trusting it.

Two things this module exists to get right, both of which a naive dump gets
wrong:

**1. Telegram caps a message at 4096 characters and the checklist does not
fit.** Measured 2026-09-02 over ``docs/claude/work/MANAGER-CHECKLIST.json``
(population: all 57 items in the file at ``main`` 0b52157): **123,033
characters of JSON, mean 2,158 and max 7,532 per item** -- a SINGLE item can
exceed the whole message budget, and the items carry very long free-prose keys
(``⚠️_ITS_BLOCKER_IS_GONE_AND_SO_IS_ITS_PATH`` and friends). So this readout is
a SUMMARY by construction. What it drops it SAYS, with counts, in
``StatusReadout.omissions`` and in the rendered footer -- a truncated list that
reads as complete is the unstated-population error
``docs/CLAUDE-RULES-CANONICAL.md`` § "Always state the population" exists for.

**2. The bot reads the VM's WORKING TREE, which lags ``main`` between
git-syncs.** Measured 2026-09-02: ``/api/bot/work/decisions`` graded a request
``in_transit`` for minutes after its answer was already committed to ``main``,
because the VM was still on the older sha. A confident status over a stale tree
is worse than no status, so every readout stamps the tree it read --
``synced`` / ``behind_main`` / ``unknown``, never collapsed, registered with
``collapsed-state-guard`` as ``manager_status.tree_state``.

Read-only throughout: two JSON reads and three ``git rev-parse``/``log`` reads.
No order path, no write, nothing that can refuse a trade.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from src.utils.paths import repo_root as _repo_root

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# Budget
# ═════════════════════════════════════════════════════════════════════════════

#: Telegram's own hard cap on one message body.
TELEGRAM_MESSAGE_LIMIT = 4096

#: Room held back in EVERY message for the omission footer. Reserved everywhere
#: rather than only in the last message because which message is last is not
#: known until packing finishes, and over-reserving costs a little space where
#: under-reserving would blow the cap -- the direction that actually breaks.
_FOOTER_RESERVE = 320

#: Room held back for the "(continued i/n)" marker, whose `n` is likewise not
#: known until packing finishes.
_CONT_RESERVE = 48

#: A mandatory section may spill across at most this many messages. The cap is
#: what stops a pathological checklist turning `/status` into a flood -- the
#: desensitised-alarm failure this repo treats as a P1.
MAX_MESSAGES = 3

_TITLE_CHARS = 90
_BLOCKER_CHARS = 70
_OWNER_CHARS = 24


# ═════════════════════════════════════════════════════════════════════════════
# Tree provenance -- three states, never collapsed
# ═════════════════════════════════════════════════════════════════════════════

TREE_SYNCED = "synced"
TREE_BEHIND = "behind_main"
TREE_UNKNOWN = "unknown"

#: The closed vocabulary. Registered with `collapsed-state-guard` as
#: `manager_status.tree_state`.
TREE_STATES = (TREE_SYNCED, TREE_BEHIND, TREE_UNKNOWN)


@dataclass(frozen=True)
class TreeProvenance:
    """Which tree this status was read from, and how far it is from ``main``.

    ``behind_commits`` is ``None`` when we could not COUNT, and ``0`` only on a
    tree that genuinely equals ``origin/main``. Those are opposite statements
    and a fabricated zero would report a stale tree as current -- the
    dangerous direction.
    """

    state: str
    head_sha: Optional[str] = None
    main_sha: Optional[str] = None
    behind_commits: Optional[int] = None
    main_age_hours: Optional[float] = None
    note: str = ""


GitRunner = Callable[[list[str]], "tuple[Optional[str], Optional[str]]"]


def _default_git(repo_dir: Path) -> GitRunner:
    def run(args: list[str]) -> tuple[Optional[str], Optional[str]]:
        # `safe.directory=*` for the same reason `src/runtime/health.py` uses
        # it: a service user reading a repo it does not own (or via the /opt
        # symlink) otherwise fails with "detected dubious ownership", which
        # would surface as `unknown` for an ownership reason rather than a
        # real one (BL-20260623-005).
        try:
            proc = subprocess.run(
                ["git", "-C", str(repo_dir), "-c", "safe.directory=*", *args],
                capture_output=True, text=True, timeout=5.0, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return None, str(exc)[:160]
        if proc.returncode != 0:
            return None, ((proc.stderr or "").strip()[:160]
                          or f"git exited {proc.returncode}")
        return (proc.stdout or "").strip(), None

    return run


def read_tree_provenance(
    *,
    repo_dir: Optional[Path] = None,
    git: Optional[GitRunner] = None,
    now: Optional[datetime] = None,
    main_ref: str = "origin/main",
) -> TreeProvenance:
    """Grade the working tree this status is about to be read from.

    ``synced``      HEAD is byte-identical to ``origin/main``.
    ``behind_main`` HEAD differs and ``origin/main`` carries commits it lacks.
    ``unknown``     we could not establish it.

    ⚠️ **A tree that DIFFERS from ``origin/main`` while being behind it by ZERO
    commits grades ``unknown``, deliberately, and this is the one mapping worth
    stating.** Such a tree carries commits ``origin/main`` does not (a local
    branch, a half-applied cherry-pick), so we cannot say what of ``main`` it
    reflects. Grading it ``synced`` would assert currency we did not establish
    -- the dangerous direction. Grading it ``behind_main`` would name a
    direction the code did not compute, which is the semantic-substitution
    class ``diagnostic-provenance-guard`` exists for. ``unknown`` is exactly
    *we could not establish it*, and ``note`` says which flavour so a reader
    never mistakes it for a failed ``rev-parse``.

    ⚠️ ``origin/main`` here is the LOCAL remote-tracking ref. This never
    fetches -- the VM's ``ict-git-sync.timer`` owns that -- so ``synced`` means
    *level with main as this tree last fetched it*, never *level with GitHub*.
    ``note`` says so on the synced path rather than leaving it inferred.
    """
    repo = Path(repo_dir) if repo_dir else Path(_repo_root())
    run = git or _default_git(repo)
    ref = now or datetime.now(timezone.utc)

    head, head_err = run(["rev-parse", "--short", "HEAD"])
    if not head:
        return TreeProvenance(
            state=TREE_UNKNOWN,
            note=f"could not read HEAD ({head_err or 'no output'})",
        )

    main, main_err = run(["rev-parse", "--short", main_ref])
    if not main:
        return TreeProvenance(
            state=TREE_UNKNOWN, head_sha=head,
            note=f"could not read {main_ref} ({main_err or 'no output'})",
        )

    if head == main:
        return TreeProvenance(
            state=TREE_SYNCED, head_sha=head, main_sha=main, behind_commits=0,
            note=f"level with the local {main_ref} ref (this never fetches)",
        )

    raw, count_err = run(["rev-list", "--count", f"HEAD..{main_ref}"])
    behind: Optional[int] = None
    if raw is not None:
        try:
            behind = int(raw.strip())
        except ValueError:
            behind = None

    if behind is None:
        return TreeProvenance(
            state=TREE_UNKNOWN, head_sha=head, main_sha=main,
            note=(f"HEAD differs from {main_ref} and the commit count could "
                  f"not be read ({count_err or 'unparseable'})"),
        )

    if behind == 0:
        # See the docstring: we looked, and what we found is that this tree is
        # not a point on main's history. That is not "synced" and it is not
        # "behind" -- it is that we cannot say what of main it reflects.
        return TreeProvenance(
            state=TREE_UNKNOWN, head_sha=head, main_sha=main,
            behind_commits=0,
            note=(f"HEAD carries commits {main_ref} does not, so what of main "
                  f"this tree reflects could not be established"),
        )

    age_hours: Optional[float] = None
    stamp, _ = run(["log", "-1", "--format=%cI", main_ref])
    parsed = _parse_iso(stamp)
    if parsed is not None:
        age_hours = max(0.0, (ref - parsed).total_seconds() / 3600.0)

    return TreeProvenance(
        state=TREE_BEHIND, head_sha=head, main_sha=main, behind_commits=behind,
        main_age_hours=age_hours,
        note=f"{behind} commit(s) behind the local {main_ref} ref",
    )


def render_tree_stamp(tree: TreeProvenance) -> str:
    """The one-line provenance stamp. Always states which of the three it is."""
    head = tree.head_sha or "?"
    if tree.state == TREE_SYNCED:
        return f"tree: synced · {head} == origin/main (as last fetched)"
    if tree.state == TREE_BEHIND:
        age = (f", newest {tree.main_age_hours:.1f}h old"
               if tree.main_age_hours is not None else "")
        return (f"tree: ⚠️ behind_main · {head}, origin/main {tree.main_sha} — "
                f"{tree.behind_commits} commit(s) behind{age}. "
                f"This status may be stale.")
    return (f"tree: ⚠️ unknown · {head} — {tree.note}. "
            f"Treat everything below as unverified.")


# ═════════════════════════════════════════════════════════════════════════════
# Reading the checklist + the sub-session registry
# ═════════════════════════════════════════════════════════════════════════════

CHECKLIST_RELPATH = "docs/claude/work/MANAGER-CHECKLIST.json"
SESSIONS_RELPATH = "docs/claude/work/SESSIONS.json"

#: Display order for the checklist's own declared `states` vocabulary.
_STATE_ORDER = ("in_flight", "blocked", "ready", "queued", "triage",
                "landed_unproven", "done", "dropped")

_IN_FLIGHT = ("in_flight",)
_BLOCKED = ("blocked",)
#: ⚠️ `done` and `landed_unproven` ride the same SECTION but keep their own
#: LABEL on every line. The checklist's own vocabulary is explicit that
#: `done` = "merged AND its effect observed" and `landed_unproven` = "merged;
#: effect NOT yet observed on the fleet", and collapsing those two is the
#: failure `CLAUDE.md` says this repo keeps paying for.
_RECENTLY_DONE = ("done", "landed_unproven")
_NEXT = ("ready", "queued", "triage")

_SESSION_ID_RE = re.compile(r"^session_[A-Za-z0-9]+$")


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class FileRead:
    """``read`` / ``absent`` / ``unreadable`` -- never collapsed.

    An ABSENT checklist genuinely means no manager has written one; one we
    could not parse is *we did not look*, and rendering the second as an empty
    checklist is the `silent-empty-guard` shape on the consumer side.
    """

    state: str
    data: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


def read_json_file(path: Path) -> FileRead:
    try:
        if not path.exists():
            return FileRead("absent", error=f"{path} does not exist")
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return FileRead("unreadable", error=str(exc)[:160])
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return FileRead("unreadable", error=f"malformed JSON: {exc}"[:160])
    if not isinstance(data, dict):
        return FileRead("unreadable", error="top level is not an object")
    return FileRead("read", data=data)


def _registered_session_ids(sessions: FileRead) -> Optional[set[str]]:
    """Session ids in the registry, or ``None`` when we could not read it.

    ``None`` is load-bearing: it means *we could not look*, and the owner line
    then says so rather than reporting every owner as unregistered -- which
    would be a fabricated finding about the exact register MI-15 already
    records being incomplete.
    """
    if sessions.state != "read":
        return None
    rows = sessions.data.get("sessions")
    if not isinstance(rows, list):
        return None
    return {
        str(r.get("session_id")) for r in rows
        if isinstance(r, dict) and r.get("session_id")
    }


def _clip(text: Any, limit: int, *, empty: str = "") -> str:
    s = " ".join(str(text or "").split())
    if not s:
        # An item with no title renders as a NAMED gap, never as blank space --
        # measured on the real file 2026-09-02, MI-37 and MI-39 carry no
        # `title`, and a bare dash reads as a rendering bug rather than as the
        # checklist row it is.
        return empty
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def grade_owner(owner: Any, registered: Optional[set[str]]) -> tuple[str, str]:
    """Return ``(rendered, grade)`` for one item's ``owner`` field.

    ⚠️ **``owner`` is NOT reliably a session id.** Measured 2026-09-02 over all
    57 checklist items: **19 carry no owner at all, 11 read ``manager``**, and
    several carry free prose (``"drains #1-#3 merged; #4 (session_012zFXi2) +
    #5 (session_01HMfmAi) running"``, ``"manager (SHOULD HAVE BEEN
    DELEGATED)"``). So the registry cross-check is applied ONLY to a
    session-id-shaped owner; anything else is shown verbatim and clipped, never
    coerced into a session id and never counted as a missing registration.

    Grades: ``registered`` · ``unregistered`` (a session id the registry does
    not carry -- the MI-15 signal) · ``registry_unread`` (*we could not look*,
    never ``unregistered``) · ``not_a_session`` · ``unowned``.
    """
    if owner is None or not str(owner).strip():
        return "—", "unowned"
    raw = str(owner).strip()
    if not _SESSION_ID_RE.match(raw):
        return _clip(raw, _OWNER_CHARS), "not_a_session"
    short = "…" + raw[-6:]
    if registered is None:
        return short + "?", "registry_unread"
    if raw in registered:
        return short, "registered"
    return short + " ⚠️unreg", "unregistered"


# ═════════════════════════════════════════════════════════════════════════════
# Section building
# ═════════════════════════════════════════════════════════════════════════════


@dataclass
class Section:
    key: str
    heading: str
    lines: list[str]
    mandatory: bool
    #: Stated under the heading when the section's population needs qualifying.
    caveat: Optional[str] = None
    #: A shorter rendering used when the full one does not fit. Measured
    #: 2026-09-02 against the real 57-item checklist: the full `recently done`
    #: and `next` sections were dropped ENTIRELY, so the operator got the
    #: checklist and neither of the other two parts they asked for. Ids alone
    #: cost ~7 characters each and carry the answer, so a compacted section
    #: beats an absent one -- and the footer says which sections were compacted
    #: so a short list is never mistaken for a complete one.
    compact_lines: Optional[list[str]] = None


def _item_line(item: dict[str, Any], registered: Optional[set[str]]) -> str:
    owner, _ = grade_owner(item.get("owner"), registered)
    return (f"• {item.get('id') or '(no id)'} — "
            f"{_clip(item.get('title'), _TITLE_CHARS, empty='(no title declared)')}  [{owner}]")


def _blocked_line(item: dict[str, Any], registered: Optional[set[str]]) -> str:
    owner, _ = grade_owner(item.get("owner"), registered)
    edges = item.get("blocked_on")
    if isinstance(edges, list) and edges:
        first = edges[0] if isinstance(edges[0], dict) else {}
        ref = first.get("ref") or "(unnamed)"
        what = first.get("what") or first.get("note")
        more = f" +{len(edges) - 1} more" if len(edges) > 1 else ""
        blocker = (f"← {first.get('kind') or 'blocked_on'} {ref}{more}"
                   + (f": {_clip(what, _BLOCKER_CHARS)}" if what else ""))
    else:
        # `blocked` with no typed edge is a real and reportable state: the item
        # declares it is waiting and does NOT say on what. Saying so beats
        # rendering it as an ordinary blocked row.
        blocker = "← ⚠️ blocked_on NOT DECLARED"
    return (f"• {item.get('id') or '(no id)'} — "
            f"{_clip(item.get('title'), _TITLE_CHARS, empty='(no title declared)')}  [{owner}]\n"
            f"    {blocker}")


def _labelled_line(item: dict[str, Any], registered: Optional[set[str]]) -> str:
    owner, _ = grade_owner(item.get("owner"), registered)
    return (f"• [{item.get('state')}] {item.get('id') or '(no id)'} — "
            f"{_clip(item.get('title'), _TITLE_CHARS, empty='(no title declared)')}  [{owner}]")


#: An id's short handle -- `MI-08-PHASE-H` -> `MI-08`. Ids on the real
#: checklist run to 60 characters (`MI-18-DUPLICATE-DELIVERY-IS-REAL-I-WAS-...`),
#: so the compact form uses the stable numeric prefix the operator and the
#: checklist file both key on.
_SHORT_ID_RE = re.compile(r"^([A-Za-z]+-\d+)")


def _short_id(item: dict[str, Any]) -> str:
    raw = str(item.get("id") or "").strip()
    if not raw:
        return "(no id)"
    match = _SHORT_ID_RE.match(raw)
    return match.group(1) if match else raw[:16]


def _compact_by_state(items: list[dict[str, Any]]) -> list[str]:
    """One line per state: ``done (11): MI-05, MI-12, …``.

    Grouped BY STATE rather than flattened, so `done` and `landed_unproven`
    stay apart even in the compact form -- they are different facts ("merged
    AND observed" vs "merged; effect NOT observed") and this repo's own
    checklist vocabulary says collapsing them is the failure it keeps paying
    for. A compact rendering is not a licence to blur them.
    """
    groups: dict[str, list[str]] = {}
    for item in items:
        groups.setdefault(str(item.get("state") or "(no state)"), []).append(
            _short_id(item))
    out = []
    for state in sorted(groups, key=lambda k: (_STATE_ORDER.index(k)
                                               if k in _STATE_ORDER
                                               else len(_STATE_ORDER), k)):
        ids = groups[state]
        out.append(f"  {state} ({len(ids)}): " + ", ".join(ids))
    return out


def build_sections(
    items: list[dict[str, Any]], registered: Optional[set[str]],
) -> list[Section]:
    """The five sections, in the operator's binding DISPLAY order."""
    def pick(states: tuple[str, ...]) -> list[dict[str, Any]]:
        got = [i for i in items if isinstance(i, dict) and i.get("state") in states]
        # Sorted by id -- deterministic, and honest: see the `recently done`
        # caveat for why this is not sorted by recency.
        return sorted(got, key=lambda i: str(i.get("id") or ""))

    counts: dict[str, int] = {}
    for i in items:
        if isinstance(i, dict):
            counts[str(i.get("state") or "(no state)")] = counts.get(
                str(i.get("state") or "(no state)"), 0) + 1
    ordered = sorted(
        counts.items(),
        key=lambda kv: (_STATE_ORDER.index(kv[0])
                        if kv[0] in _STATE_ORDER else len(_STATE_ORDER), kv[0]),
    )
    count_line = " · ".join(f"{k} {v}" for k, v in ordered) or "(no items)"

    in_flight = pick(_IN_FLIGHT)
    blocked = pick(_BLOCKED)
    grades: dict[str, int] = {}
    for i in in_flight + blocked:
        grades[grade_owner(i.get("owner"), registered)[1]] = grades.get(
            grade_owner(i.get("owner"), registered)[1], 0) + 1
    owner_line = "owners (in_flight+blocked): " + (
        " · ".join(f"{k} {v}" for k, v in sorted(grades.items())) or "none")

    return [
        Section("counts", "📋 CHECKLIST — {n} items".format(n=len(items)),
                [count_line, owner_line], mandatory=True),
        Section("in_flight", f"▶️ IN FLIGHT ({len(in_flight)})",
                [_item_line(i, registered) for i in in_flight], mandatory=True),
        Section("blocked", f"⛔ BLOCKED ({len(blocked)})",
                [_blocked_line(i, registered) for i in blocked], mandatory=True),
        Section(
            "recently_done", "✅ RECENTLY DONE ({n})".format(
                n=len(pick(_RECENTLY_DONE))),
            [_labelled_line(i, registered) for i in pick(_RECENTLY_DONE)],
            mandatory=False,
            compact_lines=_compact_by_state(pick(_RECENTLY_DONE)),
            # State the population rather than implying a window we cannot
            # compute. Measured 2026-09-02: only 17 of 57 items carry `added`
            # and NO item carries a completion timestamp of any kind, so
            # "recently" cannot be derived from the file.
            caveat=("no completion timestamp exists in the checklist, so this "
                    "is EVERY done/landed_unproven item by id — not a window. "
                    "done = merged AND observed; landed_unproven = merged, "
                    "effect NOT observed."),
        ),
        Section("next", "⏭️ NEXT ({n})".format(n=len(pick(_NEXT))),
                [_labelled_line(i, registered) for i in pick(_NEXT)],
                mandatory=False,
                compact_lines=_compact_by_state(pick(_NEXT))),
    ]


# ═════════════════════════════════════════════════════════════════════════════
# Packing under the 4096 cap
# ═════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Omission:
    section: str
    shown: int
    total: int


@dataclass(frozen=True)
class StatusReadout:
    messages: list[str]
    omissions: list[Omission]
    tree: TreeProvenance
    checklist_read: str
    sessions_read: str
    #: Sections rendered as ids only because the full form did not fit. Every
    #: item is still represented -- distinct from `omissions`, where rows are
    #: genuinely missing.
    compacted: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """No row is MISSING. A compacted section is still complete."""
        return not self.omissions


def _render_omission_footer(
    omissions: list[Omission], compacted: list[str], total_items: int,
) -> str:
    """State what was lost and how much. Never let a short list read as complete.

    Compaction and dropping are reported SEPARATELY: a compacted section is
    fully represented (every id is there) at reduced detail, whereas a dropped
    one is missing rows entirely. Pooling them would tell the operator rows are
    gone that are not, and -- far worse in the other direction -- would let
    genuinely missing rows hide inside a reassuring "shown compactly".
    """
    parts: list[str] = []
    if compacted:
        parts.append(
            "ℹ️ COMPACTED (all ids present, titles omitted): "
            + ", ".join(sorted(compacted))
        )
    if omissions:
        detail = ", ".join(
            f"{o.total - o.shown} of {o.total} {o.section}" for o in omissions)
        dropped = sum(o.total - o.shown for o in omissions)
        parts.append(
            f"⚠️ OMITTED — {dropped} line(s) did not fit Telegram's "
            f"4096-char cap: {detail}."
        )
    if not parts:
        return (f"\n\n✅ Complete: all {total_items} checklist items are "
                f"represented above.")
    parts.append(f"This is a SUMMARY, not the full checklist. "
                 f"Full: {CHECKLIST_RELPATH}")
    return "\n\n" + "\n".join(parts)


def pack_messages(
    header: list[str], sections: list[Section], *,
    limit: int = TELEGRAM_MESSAGE_LIMIT, max_messages: int = MAX_MESSAGES,
) -> tuple[list[str], list[Omission], list[str]]:
    """Pack sections into <= ``max_messages`` bodies, each within ``limit``.

    Returns ``(messages, omissions, compacted_section_keys)``.

    Sections are emitted in the caller's DISPLAY order, which is the operator's
    binding checklist -> recently done -> next. That order doubles as the
    priority order, so what survives a squeeze is always the checklist.

    Three degradations, tried in this order, so the operator loses as little as
    possible before anything is dropped outright:
      1. the section fits as it is;
      2. it does not fit here but fits in a FRESH message -- spill;
      3. it fits only in its COMPACT form (ids, no titles) -- compact and say so;
      4. only then are lines dropped, and counted.
    """
    budget = limit - _FOOTER_RESERVE - _CONT_RESERVE
    messages: list[str] = []
    cur: list[str] = list(header)
    cur_len = sum(len(x) + 1 for x in cur)
    omissions: list[Omission] = []
    compacted: list[str] = []

    def flush() -> None:
        nonlocal cur, cur_len
        if cur:
            messages.append("\n".join(cur))
        cur, cur_len = [], 0

    def cost(lines: list[str]) -> int:
        return sum(len(x) + 1 for x in lines)

    def can_open_message() -> bool:
        return len(messages) + 1 < max_messages

    for sec in sections:
        total = len(sec.lines)
        head_block = [""] + [sec.heading]
        if sec.caveat:
            head_block.append(f"  ({sec.caveat})")
        head_cost = cost(head_block)

        if not total:
            # An empty section still prints its zero heading: "0 blocked" is a
            # reading, and a heading that vanishes makes a consumer branch on
            # absence.
            if cur_len + head_cost <= budget:
                cur.extend(head_block)
                cur_len += head_cost
            continue

        full_cost = head_cost + cost(sec.lines)

        # (1) it fits here.
        if cur_len + full_cost <= budget:
            cur.extend(head_block)
            cur.extend(sec.lines)
            cur_len += full_cost
            continue

        # (2) it fits in a fresh message.
        if full_cost <= budget and can_open_message():
            flush()
            cur.extend(head_block)
            cur.extend(sec.lines)
            cur_len = full_cost
            continue

        # (3) its compact form fits (here, or in a fresh message).
        if sec.compact_lines:
            note = ["  (compacted to ids — the full form did not fit)"]
            comp_cost = head_cost + cost(note) + cost(sec.compact_lines)
            if cur_len + comp_cost <= budget:
                cur.extend(head_block + note + sec.compact_lines)
                cur_len += comp_cost
                compacted.append(sec.key)
                continue
            if comp_cost <= budget and can_open_message():
                flush()
                cur.extend(head_block + note + sec.compact_lines)
                cur_len = comp_cost
                compacted.append(sec.key)
                continue

        # (4) partial fill, then count what was dropped.
        shown = 0
        pending_head = True
        for line in sec.lines:
            line_cost = len(line) + 1 + (head_cost if pending_head else 0)
            if cur_len + line_cost > budget:
                if not can_open_message():
                    break
                flush()
                pending_head = True
                line_cost = len(line) + 1 + head_cost
                if line_cost > budget:
                    break
                cur_len = 0
            if pending_head:
                cur.extend(head_block)
                pending_head = False
            cur.append(line)
            cur_len += line_cost
            shown += 1
        if shown < total:
            omissions.append(Omission(sec.key, shown, total))

    flush()
    return messages or [""], omissions, compacted


# ═════════════════════════════════════════════════════════════════════════════
# The public entry point
# ═════════════════════════════════════════════════════════════════════════════


def build_status(
    *,
    repo_dir: Optional[Path] = None,
    git: Optional[GitRunner] = None,
    now: Optional[datetime] = None,
    limit: int = TELEGRAM_MESSAGE_LIMIT,
    max_messages: int = MAX_MESSAGES,
) -> StatusReadout:
    """Render the manager status. Never raises -- a bot command must always reply."""
    repo = Path(repo_dir) if repo_dir else Path(_repo_root())
    ref = now or datetime.now(timezone.utc)

    try:
        tree = read_tree_provenance(repo_dir=repo, git=git, now=ref)
    except Exception as exc:  # noqa: BLE001 -- provenance must never break the reply
        logger.warning("manager_status: tree provenance failed: %s", exc)
        tree = TreeProvenance(state=TREE_UNKNOWN,
                              note=f"provenance read raised: {exc}"[:160])

    checklist = read_json_file(repo / CHECKLIST_RELPATH)
    sessions = read_json_file(repo / SESSIONS_RELPATH)
    registered = _registered_session_ids(sessions)

    stamp = render_tree_stamp(tree)
    if checklist.state != "read":
        # ⚠️ NOT an empty checklist. "we could not read it" and "there is no
        # work" are opposite statements, and only one of them is good news.
        body = (
            f"📋 MANAGER STATUS — {_iso(ref)}\n"
            f"{stamp}\n\n"
            f"⚠️ CHECKLIST {checklist.state.upper()} — {checklist.error}\n"
            f"This is NOT a claim that nothing is in flight; it is that "
            f"{CHECKLIST_RELPATH} could not be read on this tree."
        )
        return StatusReadout([body], [], tree, checklist.state, sessions.state)

    raw_items = checklist.data.get("items")
    items = [i for i in raw_items if isinstance(i, dict)] if isinstance(raw_items, list) else []
    dropped = (len(raw_items) - len(items)) if isinstance(raw_items, list) else 0

    as_of = checklist.data.get("as_of") or checklist.data.get("updated_at")
    age = _parse_iso(as_of)
    age_txt = (f", {max(0.0, (ref - age).total_seconds() / 3600.0):.1f}h ago"
               if age is not None else ", age unknown")

    header = [
        f"📋 MANAGER STATUS — read {_iso(ref)}",
        stamp,
        f"checklist as_of {as_of or '(undeclared)'}{age_txt} · "
        f"cycle {checklist.data.get('cycle') or '(none)'}",
    ]
    if sessions.state != "read":
        header.append(
            f"⚠️ SESSIONS.json {sessions.state} — owner registration could not "
            f"be checked (shown as '?'), NOT that owners are unregistered."
        )
    if dropped:
        header.append(f"⚠️ {dropped} checklist entr(ies) were not objects and "
                      f"were dropped before grading.")

    sections = build_sections(items, registered)
    messages, omissions, compacted = pack_messages(
        header, sections, limit=limit, max_messages=max_messages,
    )

    messages[-1] += _render_omission_footer(omissions, compacted, len(items))
    if len(messages) > 1:
        n = len(messages)
        messages = [
            m if i == 0 else f"📋 MANAGER STATUS (continued {i + 1}/{n})\n{m}"
            for i, m in enumerate(messages)
        ]
    return StatusReadout(messages, omissions, tree, checklist.state,
                         sessions.state, compacted)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


__all__ = [
    "CHECKLIST_RELPATH",
    "MAX_MESSAGES",
    "SESSIONS_RELPATH",
    "TELEGRAM_MESSAGE_LIMIT",
    "TREE_BEHIND",
    "TREE_STATES",
    "TREE_SYNCED",
    "TREE_UNKNOWN",
    "FileRead",
    "Omission",
    "Section",
    "StatusReadout",
    "TreeProvenance",
    "build_sections",
    "build_status",
    "grade_owner",
    "pack_messages",
    "read_json_file",
    "read_tree_provenance",
    "render_tree_stamp",
]
