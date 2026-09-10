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

import html
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
# Per-FILE commit provenance — the as-of stamp
# ═════════════════════════════════════════════════════════════════════════════

#: ⚠️ `read_tree_provenance` above grades the WHOLE TREE against `origin/main`.
#: That is necessary and it is NOT the same question as *how old is the
#: checklist I am serving*. A tree can be perfectly `synced` while the
#: checklist on it was last written three hours ago, and a reader shown only
#: the tree stamp would read that as fresh. So the file gets its own stamp.
#:
#: THE FAILURE THIS EXISTS TO PREVENT is the coordination board's, 2026-09-07:
#: a frozen board served reads byte-identically to a live one, and the
#: documented staleness check passed on every attempt, so ~20h of death went
#: unnoticed. A workflow page whose checklist stopped updating must not read
#: identically to one whose checklist is current.

FILE_COMMIT_KNOWN = "known"
FILE_COMMIT_UNCOMMITTED = "uncommitted"
FILE_COMMIT_UNKNOWN = "unknown"

#: Three states, never collapsed. `uncommitted` is *git knows this tree and has
#: no commit touching this path* — a real and reportable condition (a file
#: written but never committed, so no other session can see it). `unknown` is
#: *we could not look* (git unreadable). Folding the second into the first
#: would report a git failure as a definite statement about the file, and
#: folding either into `known` with a null timestamp would let a page render an
#: absent as-of stamp as merely blank.
FILE_COMMIT_STATES = (FILE_COMMIT_KNOWN, FILE_COMMIT_UNCOMMITTED,
                      FILE_COMMIT_UNKNOWN)


@dataclass(frozen=True)
class FileCommit:
    """When the served file was last COMMITTED, and whether the tree matches it.

    ``dirty`` is ``True``/``False`` when we established it and ``None`` when we
    could not look — never defaulted to ``False``. A dirty file means the
    commit timestamp describes different bytes than the ones being served, so a
    fabricated ``False`` would attach a trustworthy-looking stamp to content
    that commit never contained. On the live VM this should always be ``False``
    (``ict-git-sync`` fast-forwards a clean tree); a ``True`` there is itself
    the finding.
    """

    state: str
    sha: Optional[str] = None
    committed_at: Optional[str] = None
    age_hours: Optional[float] = None
    dirty: Optional[bool] = None
    note: str = ""


def read_file_commit(
    relpath: str, *,
    repo_dir: Optional[Path] = None,
    git: Optional[GitRunner] = None,
    now: Optional[datetime] = None,
) -> FileCommit:
    """Grade one path's last commit. Never raises; never fabricates a time."""
    repo = Path(repo_dir) if repo_dir else Path(_repo_root())
    run = git or _default_git(repo)
    ref = now or datetime.now(timezone.utc)

    out, err = run(["log", "-1", "--format=%H%x09%cI", "--", relpath])
    if out is None:
        return FileCommit(FILE_COMMIT_UNKNOWN,
                          note=f"could not read the file log ({err or 'no output'})")
    if not out.strip():
        # git answered and had nothing: the path has no commit on this history.
        return FileCommit(FILE_COMMIT_UNCOMMITTED,
                          note=f"git reports no commit touching {relpath} on this tree")

    sha, _, stamp = out.strip().partition("\t")
    parsed = _parse_iso(stamp)
    age = (max(0.0, (ref - parsed).total_seconds() / 3600.0)
           if parsed is not None else None)

    status, status_err = run(["status", "--porcelain", "--", relpath])
    dirty: Optional[bool] = None if status is None else bool(status.strip())

    return FileCommit(
        FILE_COMMIT_KNOWN, sha=sha[:12] or None,
        committed_at=stamp.strip() or None, age_hours=age, dirty=dirty,
        note=("" if dirty is not None
              else f"working-tree cleanliness unread ({status_err or 'no output'})"),
    )


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

#: Every status the four sections below actually cover. Derived from the four
#: tuples rather than restated, so a section gaining a state can never leave
#: this out of date — the drift that would make the population line lie.
_SECTIONED_STATES = frozenset(_IN_FLIGHT + _BLOCKED + _RECENTLY_DONE + _NEXT)

_SESSION_ID_RE = re.compile(r"^session_[A-Za-z0-9]+$")


# ═════════════════════════════════════════════════════════════════════════════
# The `state` / `status` merge — THE ONE OWNER
# ═════════════════════════════════════════════════════════════════════════════

#: THE CHECKLIST CARRIES TWO COMPETING STATUS FIELDS AND THIS IS THE ONLY PLACE
#: THEY ARE RECONCILED.
#:
#: MEASURED 2026-09-10 over all 244 items of `MANAGER-CHECKLIST.json` at
#: `origin/claude/mgr-checklist-schema-20260910T0718` (population: every entry
#: in `items`, re-derived for this change rather than inherited — MI-237's
#: earlier reading of 177/83 was taken on a smaller file and must not be
#: re-quoted as current):
#:
#:     carry `state`            179
#:     carry `status`            83
#:     carry BOTH                18   of which 13 DISAGREE
#:     carry NEITHER              0
#:
#: So **65 items carry `status` and no `state`**. Nothing in this repo reads
#: `status` on this file — `build_sections` here and
#: `scripts/ops/manager_view.py::ACTIVE_CHECKLIST_STATES` both key on `state`
#: alone — so those 65 rows land in NO section of the readout and surface only
#: as `(no state) 65` in the counts line. That is the state of the world this
#: function is written against, not a defect it introduces.
#:
#: ⚠️ **WHY THE MERGE LIVES HERE AND MAY NOT BE REPEATED IN A RENDERER.** A
#: second implementation — in the SPA's TypeScript, in a route, in a report —
#: becomes a SECOND definition of an item's status, free to drift from the one
#: the guards read. That is the class this repo has a guard family for. Both
#: consumers (the Telegram `/status` readout and `GET /api/bot/work/checklist`)
#: call THIS function, so the page and the guards cannot disagree.
#:
#: ⚠️ **`state` WINS WHEN BOTH ARE PRESENT, AND THAT CHOICE IS NOT ARBITRARY.**
#: It is precisely the field every existing consumer already reads, so
#: preferring it means the new surfaces agree with `manager_view.py` and the
#: guards BY CONSTRUCTION rather than by coincidence. Where `state` is absent
#: those consumers see nothing at all, so falling back to `status` there cannot
#: contradict them either. A disagreement is never silently resolved — it is
#: REPORTED as `disagree`, because which of the two is right is a question for
#: whoever owns the row, not for a renderer.

STATUS_BASIS_STATE_ONLY = "state_only"
STATUS_BASIS_STATUS_ONLY = "status_only"
STATUS_BASIS_AGREE = "agree"
STATUS_BASIS_DISAGREE = "disagree"
STATUS_BASIS_UNDECLARED = "undeclared"

#: The closed vocabulary. Registered with `collapsed-state-guard` as
#: `manager_status.status_basis`.
#:
#: `undeclared` is *the row declares no status at all* and is deliberately kept
#: apart from every other value even though it is measured at ZERO today. Were
#: it folded into `status_only`, a row with nothing declared would render as a
#: row whose status came from `status` — asserting a reading nobody wrote. A
#: state measured at zero is not a state that cannot occur.
STATUS_BASES = (
    STATUS_BASIS_STATE_ONLY,
    STATUS_BASIS_STATUS_ONLY,
    STATUS_BASIS_AGREE,
    STATUS_BASIS_DISAGREE,
    STATUS_BASIS_UNDECLARED,
)

#: The checklist file declares its own `states` vocabulary in a top-level
#: `states` object. This is the fallback used when that cannot be read; the
#: file's own declaration wins whenever it is present, because the file is the
#: field and this constant is prose about it.
DECLARED_STATE_VOCABULARY = (
    "done", "landed_unproven", "in_flight", "blocked", "queued", "triage",
    "dropped",
)


@dataclass(frozen=True)
class EffectiveState:
    """One item's reconciled status, WITH the basis it was reconciled on.

    ``value`` is what a renderer should group and label by. ``basis`` says how
    it was arrived at and is never inferable from ``value`` alone — a row
    reading ``in_flight`` on `disagree` and one reading ``in_flight`` on
    `agree` are the same value and very different facts.

    ``in_declared_vocabulary`` is FALSE for a value the file's own `states`
    block does not declare. Measured on the same 244-item population: `state`
    carries ``deferred`` and ``waiting``, and `status` carries ``superseded``,
    ``filed_not_taken``, ``operator_approved``, ``failed``, ``unassigned`` and
    ``duplicate_of_MI-180`` — none of them declared. Such a value is passed
    through VERBATIM and flagged, never mapped into a declared bucket: guessing
    that ``superseded`` means ``dropped`` would invent a decision nobody made.
    """

    value: Optional[str]
    basis: str
    state: Optional[str]
    status: Optional[str]
    in_declared_vocabulary: bool

    @property
    def disagrees(self) -> bool:
        return self.basis == STATUS_BASIS_DISAGREE


def _declared_vocabulary(checklist: Optional[dict[str, Any]]) -> tuple[str, ...]:
    """The file's own `states` keys, falling back to the constant above."""
    if isinstance(checklist, dict):
        declared = checklist.get("states")
        if isinstance(declared, dict) and declared:
            return tuple(str(k) for k in declared)
    return DECLARED_STATE_VOCABULARY


def _field(item: dict[str, Any], key: str) -> Optional[str]:
    raw = item.get(key)
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def effective_state(
    item: dict[str, Any], *, vocabulary: Optional[tuple[str, ...]] = None,
) -> EffectiveState:
    """Reconcile one item's `state` and `status`. The ONE owner of this merge.

    See the block comment above for the measured population and for why `state`
    wins a disagreement. Never raises: a non-mapping item grades `undeclared`.
    """
    vocab = vocabulary or DECLARED_STATE_VOCABULARY
    if not isinstance(item, dict):
        return EffectiveState(None, STATUS_BASIS_UNDECLARED, None, None, False)

    state = _field(item, "state")
    status = _field(item, "status")

    if state is not None and status is not None:
        basis = (STATUS_BASIS_AGREE if state == status
                 else STATUS_BASIS_DISAGREE)
        value = state
    elif state is not None:
        basis, value = STATUS_BASIS_STATE_ONLY, state
    elif status is not None:
        basis, value = STATUS_BASIS_STATUS_ONLY, status
    else:
        basis, value = STATUS_BASIS_UNDECLARED, None

    return EffectiveState(
        value=value,
        basis=basis,
        state=state,
        status=status,
        in_declared_vocabulary=value in vocab if value is not None else False,
    )


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


def _labelled_line(
    item: dict[str, Any], registered: Optional[set[str]],
    vocabulary: Optional[tuple[str, ...]] = None,
) -> str:
    owner, _ = grade_owner(item.get("owner"), registered)
    eff = effective_state(item, vocabulary=vocabulary)
    # A row whose two status fields disagree is LABELLED as such rather than
    # rendered as an ordinary row under the winning value. `state` winning is
    # a rule for picking a value, not a finding that `status` was wrong.
    mark = f" ⚠️status={eff.status}" if eff.disagrees else ""
    return (f"• [{eff.value or 'no status declared'}{mark}] "
            f"{item.get('id') or '(no id)'} — "
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


def _compact_by_state(
    items: list[dict[str, Any]],
    vocabulary: Optional[tuple[str, ...]] = None,
) -> list[str]:
    """One line per state: ``done (11): MI-05, MI-12, …``.

    Grouped BY STATE rather than flattened, so `done` and `landed_unproven`
    stay apart even in the compact form -- they are different facts ("merged
    AND observed" vs "merged; effect NOT observed") and this repo's own
    checklist vocabulary says collapsing them is the failure it keeps paying
    for. A compact rendering is not a licence to blur them.
    """
    groups: dict[str, list[str]] = {}
    for item in items:
        eff = effective_state(item, vocabulary=vocabulary)
        groups.setdefault(eff.value or "(no status declared)", []).append(
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
    vocabulary: Optional[tuple[str, ...]] = None,
) -> list[Section]:
    """The five sections, in the operator's binding DISPLAY order.

    ⚠️ Every status read here goes through `effective_state`, the one owner of
    the `state`/`status` merge. Reading `item["state"]` directly — which this
    function used to do — silently drops the 65 items that carry only
    `status`, and re-introducing that read makes this a second definition of
    an item's status.
    """
    def pick(states: tuple[str, ...]) -> list[dict[str, Any]]:
        got = [i for i in items if isinstance(i, dict)
               and effective_state(i, vocabulary=vocabulary).value in states]
        # Sorted by id -- deterministic, and honest: see the `recently done`
        # caveat for why this is not sorted by recency.
        return sorted(got, key=lambda i: str(i.get("id") or ""))

    counts: dict[str, int] = {}
    bases: dict[str, int] = {b: 0 for b in STATUS_BASES}
    off_vocab: list[str] = []
    unsectioned: list[str] = []
    for i in items:
        if not isinstance(i, dict):
            continue
        eff = effective_state(i, vocabulary=vocabulary)
        key = eff.value or "(no status declared)"
        counts[key] = counts.get(key, 0) + 1
        bases[eff.basis] = bases.get(eff.basis, 0) + 1
        if eff.value is not None and not eff.in_declared_vocabulary:
            off_vocab.append(eff.value)
        if eff.value not in _SECTIONED_STATES:
            unsectioned.append(eff.value or "(no status declared)")
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
        grade = grade_owner(i.get("owner"), registered)[1]
        grades[grade] = grades.get(grade, 0) + 1
    owner_line = "owners (in_flight+blocked): " + (
        " · ".join(f"{k} {v}" for k, v in sorted(grades.items())) or "none")

    # State the basis of the status column rather than letting a merged value
    # read as a single declared one. `disagree` is called out on its own line
    # because it is the only basis that means a row is telling two stories.
    # ⚠️ THESE LINES ARE DELIBERATELY TERSE, AND THE FULL EXPLANATIONS LIVE ON
    # THE WORKFLOW PAGE INSTEAD. This section is MANDATORY, so every character
    # spent here is taken from the checklist rows the operator actually asked
    # for — a first draft cost ~450 characters and pushed the omission footer
    # itself over the cap under a tight budget, which is the readout blinding
    # itself to what it dropped. `GET /api/bot/work/checklist` has no character
    # budget and carries the same facts in full; that split is much of the
    # argument for the page existing.
    basis_line = "status basis: " + " · ".join(
        f"{b} {bases.get(b, 0)}" for b in STATUS_BASES)
    lines = [count_line, owner_line, basis_line]
    if bases.get(STATUS_BASIS_DISAGREE):
        lines.append(
            f"⚠️ {bases[STATUS_BASIS_DISAGREE]} item(s): `state` ≠ `status` — "
            f"shown by `state`; both kept, neither edited.")
    if unsectioned:
        # THE POPULATION STATEMENT. Without it the four sections below read as
        # the whole checklist while covering only part of it: measured
        # 2026-09-10 over the real 244-item file, 18 items carry a status no
        # section covers and appear in the counts line and NOWHERE ELSE.
        lines.append(
            f"⚠️ {len(unsectioned)} of {len(items)} item(s) have a status NO "
            f"section below covers ({', '.join(sorted(set(unsectioned)))}).")
    if off_vocab:
        # No list of values here: `unsectioned` above already names the ones
        # that go unrendered, and the rest (`ready`) are drift in the file's
        # own `states` block rather than a row nobody reads. Full detail is on
        # the page as `summary.offDeclaredVocabulary`.
        lines.append(
            f"ℹ️ {len(off_vocab)} item(s) use a status the file's own `states` "
            f"block does not declare (shown verbatim, never remapped).")

    return [
        Section("counts", "📋 CHECKLIST — {n} items".format(n=len(items)),
                lines, mandatory=True),
        Section("in_flight", f"▶️ IN FLIGHT ({len(in_flight)})",
                [_item_line(i, registered) for i in in_flight], mandatory=True),
        Section("blocked", f"⛔ BLOCKED ({len(blocked)})",
                [_blocked_line(i, registered) for i in blocked], mandatory=True),
        Section(
            "recently_done", "✅ RECENTLY DONE ({n})".format(
                n=len(pick(_RECENTLY_DONE))),
            [_labelled_line(i, registered, vocabulary)
             for i in pick(_RECENTLY_DONE)],
            mandatory=False,
            compact_lines=_compact_by_state(pick(_RECENTLY_DONE), vocabulary),
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
                [_labelled_line(i, registered, vocabulary)
                 for i in pick(_NEXT)],
                mandatory=False,
                compact_lines=_compact_by_state(pick(_NEXT), vocabulary)),
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
    #: Telegram parse mode for `messages`. "HTML" for the expandable
    #: one-message render; None for the plain-text packer, whose bodies are
    #: NOT escaped and would be mangled (or 400) if sent as HTML.
    #: ⚠️ Carried on the readout rather than hardcoded at the send site so the
    #: two renderers can never be sent under each other's mode.
    parse_mode: Optional[str] = None

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
# ONE message with expandable sections (operator, 2026-09-03)
# ═════════════════════════════════════════════════════════════════════════════
#
# Operator, verbatim: *"the 'status' message in the claude bot needs to be
# reformatted - only 1 message each time, with drop-down sections so that i can
# see the whole summary upfront and decide what sections to dive into"*.
#
# ⚠️ THIS REPLACES THE CHUNKING, IT DOES NOT WRAP IT. `pack_messages` spends the
# 4096 budget on DETAIL and spills what will not fit across up to three
# messages; collapsing the detail is what makes one message sufficient, so the
# two are alternatives rather than layers. `pack_messages` is kept — it is the
# honest fallback if the HTML render ever fails, and its degradation ladder is
# still the thing that decides what to drop.
#
# ⚠️ THE SUMMARY IS DELIBERATELY *OUTSIDE* THE BLOCKQUOTES. The ask is to "see
# the whole summary upfront and decide what sections to dive into", so every
# section HEADING (which carries its own count) and the counts/owner roll-up
# stay uncollapsed; only the per-item rows go inside. Collapsing the summary
# would satisfy the letter and defeat the ask.

#: Telegram's expandable blockquote. Bot API 7.0+, HTML parse mode.
_EXPANDABLE_OPEN = "<blockquote expandable>"
_EXPANDABLE_CLOSE = "</blockquote>"

#: Sections whose DETAIL is collapsed. `counts` is the roll-up itself, so it is
#: never collapsed -- it IS the summary.
_NEVER_COLLAPSED = ("counts",)

#: Narrowest a clipped row may get before rows are dropped instead. An id is
#: ~24 characters and is the part that lets the operator go and look the item
#: up, so clipping below this would keep a row that identifies nothing.
_CLIP_FLOOR = 46


def _clip_line(line: str, width: int) -> str:
    """Clip one rendered row, marking that it was clipped.

    ⚠️ The ellipsis is what stops a clipped title from reading as the whole
    title -- the same reason `_clip` exists for field values.
    """
    if width <= 0 or len(line) <= width:
        return line
    return line[: max(1, width - 1)].rstrip() + "…"


def _esc(text: Any) -> str:
    """HTML-escape one line for Telegram's HTML parse mode.

    ⚠️ EVERY data-derived string must go through this. Checklist titles are
    free prose written by other sessions, so an unescaped `<` or `&` would
    either break the parse (Telegram rejects the whole message with 400) or
    silently swallow text as a tag. A status command that 400s is strictly
    worse than an ugly one.
    """
    return html.escape(str(text), quote=False)


def render_expandable_message(
    header: list[str],
    sections: list[Section],
    *,
    total_items: int,
    limit: int = TELEGRAM_MESSAGE_LIMIT,
) -> tuple[str, list[Omission], list[str]]:
    """Render ONE HTML message: summary uncollapsed, detail in drop-downs.

    Returns ``(text, omissions, compacted_section_keys)``.

    ⚠️ THE BUDGET COUNTS THE TAGS. Telegram caps the raw string it receives, so
    `<blockquote expandable>` (24 chars) and `</blockquote>` (13) are spent from
    the same 4096 as the content. Measuring the rendered string rather than the
    visible text is the difference between fitting and a 400.

    ⚠️ TRUNCATION IS STATED, NEVER SILENT. If everything still does not fit with
    every section collapsed, rows are dropped from the LOWEST-value section
    first -- reverse display order, so the checklist is last to suffer -- and
    the count is reported in the footer. A silent drop is the collapsed-state
    failure this repo files bugs about, and a SECOND message would contradict
    the operator's instruction.
    """
    omissions: list[Omission] = []
    compacted: list[str] = []
    #: full | compact, per section key.
    form: dict[str, str] = {s.key: "full" for s in sections}
    chosen: dict[str, list[str]] = {s.key: list(s.lines) for s in sections}

    def block(sec: Section, lines: list[str]) -> list[str]:
        out = ["", f"<b>{_esc(sec.heading)}</b>"]
        if sec.caveat:
            out.append(f"<i>({_esc(sec.caveat)})</i>")
        if form[sec.key] == "compact":
            out.append("<i>(compacted to ids — the full form did not fit)</i>")
        if not lines:
            return out
        if sec.key in _NEVER_COLLAPSED:
            out.extend(_esc(x) for x in lines)
            return out
        body = "\n".join(_esc(x) for x in lines)
        out.append(f"{_EXPANDABLE_OPEN}{body}{_EXPANDABLE_CLOSE}")
        return out

    def assemble() -> str:
        parts = [f"<b>{_esc(header[0])}</b>"] + [_esc(h) for h in header[1:]]
        for sec in sections:
            parts.extend(block(sec, chosen[sec.key]))
        return "\n".join(parts) + _esc(
            _render_omission_footer(omissions, compacted, total_items))

    text = assemble()
    if len(text) <= limit:
        return text, omissions, compacted

    # ── (1) COMPACT the optional sections, lowest value first ───────────────
    # The same ladder `pack_messages` uses, and for the same measured reason:
    # rendering the optional sections in FULL dropped BOTH of them entirely,
    # so the operator got the checklist and neither of the other two parts
    # they asked for. Ids cost ~7 characters and carry the answer, so a
    # compacted section beats an absent one.
    for sec in reversed(sections):
        if len(text) <= limit:
            break
        if sec.key in _NEVER_COLLAPSED or not sec.compact_lines:
            continue
        form[sec.key] = "compact"
        chosen[sec.key] = list(sec.compact_lines)
        compacted.append(sec.key)
        text = assemble()

    # ── (1b) CLIP long rows before losing any ───────────────────────────────
    # `in_flight` and `blocked` are MANDATORY and carry no `compact_lines`, so
    # without this the ladder jumps straight from full detail to dropping rows
    # -- measured against the real 90-item checklist, that emptied `blocked`
    # (0 of 7) while `in_flight` kept 19 of 26. A blocked item the operator
    # cannot see is the worst row to lose, and the id plus a clipped title is
    # what identifies it. Every row survives clipping; none survives a drop, so
    # this is tried first. The floor is deliberately wide enough to keep an id.
    if len(text) > limit:
        # ⚠️ A COMPACTED SECTION IS NEVER CLIPPED. Its lines are already
        # id-lists, so clipping one silently drops IDS -- and the footer's
        # central claim for a compacted section is "all ids present". Clipping
        # there would make that claim FALSE, which is worse than the section
        # being shorter. Full-form rows are safe to clip: what the ellipsis
        # removes is the TITLE, and the id survives at the front of the line.
        clippable = [s for s in sections
                     if s.key not in _NEVER_COLLAPSED
                     and form[s.key] != "compact"
                     and chosen[s.key]]
        widest = max((max((len(x) for x in chosen[s.key]), default=0)
                      for s in clippable), default=0)
        lo, hi = _CLIP_FLOOR, max(_CLIP_FLOOR, widest)
        best: Optional[int] = None
        while lo <= hi:
            mid = (lo + hi) // 2
            for sec in clippable:
                chosen[sec.key] = [_clip_line(x, mid) for x in sec.lines]
            if len(assemble()) <= limit:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        for sec in clippable:
            src_lines = sec.lines
            chosen[sec.key] = ([_clip_line(x, best) for x in src_lines]
                               if best is not None
                               else [_clip_line(x, _CLIP_FLOOR) for x in src_lines])
        text = assemble()

    # ── (2) only NOW drop rows, still lowest value first ────────────────────
    for sec in reversed(sections):
        if len(text) <= limit:
            break
        if sec.key in _NEVER_COLLAPSED or not chosen[sec.key]:
            continue
        lines = list(chosen[sec.key])
        # `total` is the section's REAL population, never the compacted line
        # count -- an omission reported against a compacted denominator would
        # understate what is missing.
        total = len(sec.lines)
        lo, hi = 0, len(lines)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            chosen[sec.key] = lines[:mid]
            omissions = [o for o in omissions if o.section != sec.key]
            if form[sec.key] == "compact" or mid < len(lines):
                omissions.append(Omission(sec.key, mid, total))
            if len(assemble()) <= limit:
                lo = mid
            else:
                hi = mid - 1
        chosen[sec.key] = lines[:lo]
        omissions = [o for o in omissions if o.section != sec.key]
        if lo < len(lines) or form[sec.key] == "compact":
            omissions.append(Omission(sec.key, lo, total))
        text = assemble()

    # ── (3) the FLOOR: header + headings + footer are irreducible ────────────
    # Sections can be emptied but the summary cannot, so a small enough budget
    # leaves a message that still exceeds it. At the real 4096 cap this cannot
    # arise (the floor measures ~1.1k), but "cannot arise" is exactly the kind
    # of claim this repo distrusts -- and returning an over-length string means
    # Telegram 400s the WHOLE reply and the operator gets NO status at all.
    # So it is cut, and the cut is STATED where the operator will see it.
    if len(text) > limit:
        marker = "\n…[CUT: over Telegram's cap even with every section empty]"
        keep = max(0, limit - len(marker))
        text = text[:keep] + marker

    return text, omissions, compacted


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
    expandable: bool = True,
) -> StatusReadout:
    """Render the manager status. Never raises -- a bot command must always reply.

    ``expandable`` (default) renders ONE HTML message whose per-section detail
    sits in drop-downs, per the operator's 2026-09-03 ask. Passing False keeps
    the original plain-text multi-message packer, which is what the tests of
    the degradation ladder exercise and what a caller with no HTML surface
    would want.
    """
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

    sections = build_sections(
        items, registered, _declared_vocabulary(checklist.data))

    if expandable:
        text, omissions, truncated = render_expandable_message(
            header, sections, total_items=len(items), limit=limit,
        )
        return StatusReadout([text], omissions, tree, checklist.state,
                             sessions.state, truncated, parse_mode="HTML")

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
    "DECLARED_STATE_VOCABULARY",
    "STATUS_BASES",
    "STATUS_BASIS_AGREE",
    "STATUS_BASIS_DISAGREE",
    "STATUS_BASIS_STATE_ONLY",
    "STATUS_BASIS_STATUS_ONLY",
    "STATUS_BASIS_UNDECLARED",
    "EffectiveState",
    "FILE_COMMIT_KNOWN",
    "FILE_COMMIT_STATES",
    "FILE_COMMIT_UNCOMMITTED",
    "FILE_COMMIT_UNKNOWN",
    "FileCommit",
    "FileRead",
    "Omission",
    "Section",
    "StatusReadout",
    "TreeProvenance",
    "build_sections",
    "build_status",
    "effective_state",
    "render_expandable_message",
    "grade_owner",
    "pack_messages",
    "read_file_commit",
    "read_json_file",
    "read_tree_provenance",
    "render_tree_stamp",
]
