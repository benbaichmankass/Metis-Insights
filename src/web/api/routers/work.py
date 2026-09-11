"""Tier-1 read endpoints exposing the work store (``docs/claude/work/``).

Backs the SPA's **Work** section — what is in flight, under which intent, and
what each object is waiting on — plus, since Phase H, the CONTROL half:
decisions the operator can answer from the UI.

⚠️ The read gate (attaching ``require_session`` to the read surface) is the OTHER
half of Phase H and is deliberately NOT here. It is not a property of this
router, and its precondition — the Android app and the Streamlit dashboard off
the live feed — was not met when this landed.

- ``GET /api/bot/work`` — the whole store: intents, objects, steps, a lifecycle
  roll-up, the WIP-ceiling reading, and a ``coverage`` block stating what the
  store does **not** cover.
- ``GET /api/bot/work/object/{object_id}`` — one object, in full.
- ``GET /api/bot/work/decisions`` — **Phase H.** Every decision waiting on the
  operator, with a four-state answer grade and every open transit window.
- ``POST /api/bot/work/decision`` — **Phase H, Tier 2.** Submit one answer.
  Token-gated and FAIL-CLOSED, and it writes NO truth: see
  ``src/runtime/work_decisions.py`` for the transit contract.

Modelled directly on ``roadmap.py``: file-backed from committed YAML (the VM's
``ict-git-sync`` mirrors ``main``), read-only, no DB, no secrets — so it adds no
table and is exempt from the new-table-wiring guard. Best-effort: a missing or
garbled store degrades to an empty envelope, never a 5xx. Short in-process cache
keyed on file mtimes.

Three things this module refuses to do, each because collapsing them is the
defect this repo has a guard family for:

1. **A file that fails to parse is REPORTED, never dropped.** It lands in
   ``readErrors`` and is counted in ``lifecycle.unknown``. Silently omitting it
   would make "we could not read the store" indistinguishable from "the store is
   empty" — the ``silent-empty-guard`` shape, consumer side.

2. **``lifecycle`` is never collapsed.** All six declared states ship as explicit
   keys with explicit zeros, plus ``unknown`` for a row we could not grade. They
   sum to ``total`` by construction, so the partition is checkable rather than
   trusted. A key that vanishes makes a consumer branch on absence, and absence
   is not one of the states.

3. **An empty ``blocked_on`` is a CLAIM, not an absence of information** — the
   work store's own README says so. ``blockedOnState`` separates ``declared_none``
   (the row asserts nothing blocks it) from ``unstated`` (the key is missing, so
   nobody has said). Reading the second as the first is how a false "ready"
   appears.

⚠️ **The store is NOT a complete picture of the system's work**, and this route
says so on every response rather than leaving the consumer to infer it. It holds
the operating-layer build's own phases PLUS the carried backlog rows, which
migrated in on 2026-09-01 (Phase C) together with the WIP ceiling. ``coverage.complete`` is ``false`` and the
renderer is expected to show it.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Header, HTTPException, Request

# Phase H — the decision round-trip. ONE owner for the transit contract and the
# four answer states; this router never re-derives either (two copies of "what
# counts as committed" is how a committed answer stops grading as committed).
# The manager checklist's ONE grading owner. Imported whole rather than
# cherry-picking helpers, so a reader of this route can see that every status
# reading here is `manager_status`'s and none of it is re-derived locally.
from src.runtime import manager_status
from src.runtime.decision_subject import (
    SUBJECT_GONE,
    SUBJECT_STATES,
    SubjectResolver,
    grade_subject,
    is_actionable,
)
from src.runtime.work_decisions import (
    ANSWERED_IN_CONVERSATION,
    ANSWER_STATES,
    ENGAGED_NOT_SETTLED,
    SETTLED_STATES,
    VERDICT_UNRECOGNISED,
    ASKED_BY_RECORDED,
    ASKED_BY_STATES,
    COMMITTED,
    IN_TRANSIT,
    MAX_FREE_TEXT_CHARS,
    NOT_SUBMITTED,
    STALE_TRANSIT_SECONDS,
    TRANSIT_UNREADABLE,
    UNREADABLE,
    append_submission,
    grade_answer_state,
    latest_submissions,
    malformed_request_count,
    normalise_requests,
    read_transit,
    transit_log_path,
    transit_window,
)
from src.utils.paths import repo_root
# ONE owner for the ceiling + migration facts — imported, never re-derived.
# They had two homes and Phase C updated neither; see the module docstring.
from src.utils.work_facts import (
    CARRIED_ROWS_MIGRATED,
    CARRIED_ROWS_MIGRATED_IN,
    CEILING_ENFORCED,
    CEILING_STATE,
    WIP_CEILING,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bot/work", tags=["work"])

# Guards the {object_id} path param against traversal (we only ever open
# <dir>/<id>.yaml) and matches the store's own id convention.
#
# ⚠️ Anchored to a leading ALPHANUMERIC, deliberately. A looser
# `^[A-Za-z0-9._-]+$` admits `..`, and the route was then safe only by the
# accident that `".." + ".yaml"` concatenates to `...yaml` — an ordinary
# filename inside the directory. Resting a traversal guard on an incidental
# string join is the kind of defence that stops holding the moment someone
# changes how the path is built, so the id is constrained at the door as well
# as resolved inside the directory afterwards (defence in depth, not either/or).
_OBJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# The six declared lifecycle states, in the order the design lists them.
# `unknown` is NOT one of them — it is the seventh bucket for a row we could not
# grade, kept separate so it can never be mistaken for a real state.
_LIFECYCLE_STATES: tuple[str, ...] = (
    "dormant",
    "ready",
    "in_flight",
    "waiting",
    "done",
    "accepted",
)
_UNKNOWN = "unknown"

# Lifecycle states that count against the WIP ceiling. An object being WORKED is
# in flight; one waiting on someone else is not consuming a working slot.
_COUNTS_AGAINST_CEILING = frozenset({"in_flight"})

# The ceiling (A5). ⚠️ IT IS ENFORCED AS OF 2026-09-01 (Phase C, #10657):
# scripts/ci/check_wip_ceiling.py FAILS CI on a ninth `in_flight` object, and
# exceeding it needs an approved justification at wip-ceiling-exception.yaml.
#
# ⚠️ THIS ROUTE STILL GATES NOTHING, and the distinction is the whole point:
# ENFORCEMENT LIVES IN CI, NOT HERE. A read route that refused anything would be
# a second, drifting copy of the rule. What changed is what this route may
# truthfully SAY about it.
#
# ⚠️ These three lines said the OPPOSITE until 2026-09-01 and were WRONG IN THE
# DANGEROUS DIRECTION for the ~20 minutes after Phase C merged: the operator's
# own screenshot of the deployed SPA showed "Declared, not enforced. Nothing
# checks this yet" beside 584 migrated objects, i.e. the page told a reader the
# ceiling was advisory when it would in fact fail their CI. Phase C shipped the
# enforcement and the migration and never updated the route's description of
# itself — the code carrying the stale comment, which is the same class as
# `field beats comment` one layer up.
_WIP_CEILING = WIP_CEILING
_CEILING_ENFORCED = CEILING_ENFORCED

# Carried backlog rows MIGRATED IN on 2026-09-01 (Phase C). Kept as a named
# constant because the coverage note still has to say what was carried and when;
# it is history now, not a pending gap.
_CARRIED_ROWS_MIGRATED = CARRIED_ROWS_MIGRATED


def _work_dir() -> Path:
    return Path(repo_root()) / "docs" / "claude" / "work"


def _intents_dir() -> Path:
    return _work_dir() / "intents"


def _objects_dir() -> Path:
    return _work_dir() / "objects"


def _steps_dir() -> Path:
    return _work_dir() / "steps"


def _load_yaml(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Return (payload, error). Exactly one is non-None.

    A read or parse failure returns the reason rather than an empty dict, so the
    caller can report it instead of serving a silent gap.
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return None, f"unreadable: {exc}"
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        return None, f"malformed yaml: {exc}"
    if data is None:
        return None, "empty file"
    if not isinstance(data, dict):
        return None, f"expected a mapping, got {type(data).__name__}"
    return data, None


def _jsonable(value: Any) -> Any:
    """Coerce a parsed-YAML value into something JSON-serialisable.

    ⚠️ This is what makes the "never a 5xx" contract REAL rather than nominal.
    ``extra`` preserves whatever free-form keys an object file carries, and YAML
    yields native ``date`` / ``datetime`` objects for an unquoted ``2026-09-01``.
    FastAPI's encoder happens to handle those two, but the store's schema is
    deliberately open — a future key holding any other non-encodable type would
    raise at RESPONSE-render time, i.e. *after* ``_build_index``'s try/except,
    turning a Tier-1 read surface into a 500 that the module's own error
    handling could never catch.

    Dates become ISO strings (what the API already emits for the known fields,
    so nothing changes shape); anything else unrecognised becomes its ``str()``
    rather than being dropped — a value we could not type is still a value the
    reader should see, and silently omitting it is the gap this file refuses to
    serve elsewhere.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _grade_lifecycle(value: Any) -> str:
    """Map a raw ``lifecycle`` value onto a declared state, or ``unknown``.

    Deliberately does NOT guess: an unrecognised or missing value grades
    ``unknown`` rather than defaulting to ``dormant``. Defaulting would assert a
    state nobody declared.
    """
    if not isinstance(value, str):
        return _UNKNOWN
    token = value.strip().lower()
    return token if token in _LIFECYCLE_STATES else _UNKNOWN


def _normalise_blocked_on(raw: Any) -> tuple[list[dict[str, Any]], str]:
    """Return (edges, state).

    ``state`` is one of:
      * ``declared_none`` — the key is present and empty: the row CLAIMS nothing
        blocks it. The store's README is explicit that this is a claim.
      * ``declared``      — one or more typed edges.
      * ``unstated``      — the key is absent: nobody has said. **NOT** the same
        as ``declared_none``, and must never be rendered as "nothing blocks".
      * ``malformed``     — present but not a list.
    """
    if raw is None:
        return [], "unstated"
    if not isinstance(raw, list):
        return [], "malformed"
    if not raw:
        return [], "declared_none"
    edges: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            edges.append(
                {
                    "kind": _jsonable(item.get("kind")),
                    "ref": _jsonable(item.get("ref")),
                    "since": _jsonable(item.get("since")),
                    "note": _jsonable(item.get("note")),
                }
            )
        else:
            # A bare scalar edge is not the typed shape the design requires;
            # surface it rather than dropping it.
            edges.append({"kind": None, "ref": _jsonable(item), "since": None, "note": None})
    return edges, "declared"


# Keys promoted to top-level camelCase fields on an object row. Everything else
# a file carries is preserved verbatim under ``extra`` — the store's objects
# carry free-form keys (``scope_split``, ``carried_out_of_scope``, emoji-prefixed
# warnings) and dropping them would hide exactly the caveats they exist to raise.
_OBJECT_KNOWN_KEYS = frozenset(
    {
        "id",
        "type",
        "parent_intent",
        "title",
        "stage",
        "lifecycle",
        "owner",
        "opened_at",
        "closed_at",
        "review_trigger",
        "done_condition",
        "blocked_on",
        "note",
        "evidence",
        "verdict",
    }
)


def _object_row(data: dict[str, Any], path: Path) -> dict[str, Any]:
    lifecycle = _grade_lifecycle(data.get("lifecycle"))
    edges, blocked_state = _normalise_blocked_on(data.get("blocked_on"))
    extra = {str(k): _jsonable(v) for k, v in data.items() if k not in _OBJECT_KNOWN_KEYS}
    return {
        "id": data.get("id") or path.stem,
        "type": data.get("type"),
        "parentIntent": data.get("parent_intent"),
        "title": data.get("title"),
        "stage": data.get("stage"),
        "lifecycle": lifecycle,
        "lifecycleDeclared": data.get("lifecycle"),
        "owner": data.get("owner"),
        "openedAt": _jsonable(data.get("opened_at")),
        "closedAt": _jsonable(data.get("closed_at")),
        "reviewTrigger": data.get("review_trigger"),
        "doneCondition": data.get("done_condition"),
        "blockedOn": edges,
        "blockedOnState": blocked_state,
        "note": data.get("note"),
        "evidence": _jsonable(data.get("evidence") or []),
        "verdict": _jsonable(data.get("verdict")),
        "hasVerdict": data.get("verdict") is not None,
        "extra": extra,
        "path": f"docs/claude/work/objects/{path.name}",
    }


def _intent_row(data: dict[str, Any], path: Path) -> dict[str, Any]:
    known = {"id", "title", "status", "opened_at", "review_cadence", "why", "done_looks_like"}
    return {
        "id": data.get("id") or path.stem,
        "title": data.get("title"),
        "status": data.get("status"),
        "openedAt": _jsonable(data.get("opened_at")),
        "reviewCadence": data.get("review_cadence"),
        "why": data.get("why"),
        "doneLooksLike": data.get("done_looks_like"),
        "extra": {str(k): _jsonable(v) for k, v in data.items() if k not in known},
        "path": f"docs/claude/work/intents/{path.name}",
    }


def _step_row(data: dict[str, Any], path: Path) -> dict[str, Any]:
    known = {"id", "title", "parent_object", "lifecycle", "owner", "opened_at", "note"}
    return {
        "id": data.get("id") or path.stem,
        "title": data.get("title"),
        "parentObject": data.get("parent_object"),
        "lifecycle": _grade_lifecycle(data.get("lifecycle")),
        "lifecycleDeclared": data.get("lifecycle"),
        "owner": data.get("owner"),
        "openedAt": _jsonable(data.get("opened_at")),
        "note": data.get("note"),
        "extra": {str(k): _jsonable(v) for k, v in data.items() if k not in known},
        "path": f"docs/claude/work/steps/{path.name}",
    }


def _yaml_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    try:
        return sorted(
            [p for p in directory.iterdir() if p.suffix in (".yaml", ".yml") and p.is_file()]
        )
    except OSError:
        return []


# ── module-level cache keyed on (per-dir newest mtime, file count) ────────────
_CACHE: dict[str, Any] = {}


def _cache_key() -> tuple:
    parts: list[tuple[int, int]] = []
    for directory in (_intents_dir(), _objects_dir(), _steps_dir()):
        files = _yaml_files(directory)
        try:
            newest = max((f.stat().st_mtime_ns for f in files), default=0)
        except OSError:
            newest = 0
        parts.append((newest, len(files)))
    return tuple(parts)


def _empty_envelope(reason: str) -> dict[str, Any]:
    """The degraded shape. Every key a consumer branches on is still PRESENT."""
    return {
        "present": False,
        "reason": reason,
        "intents": [],
        "objects": [],
        "steps": [],
        "readErrors": [],
        "lifecycle": {state: 0 for state in (*_LIFECYCLE_STATES, _UNKNOWN)},
        "summary": {
            "intentCount": 0,
            "objectCount": 0,
            "stepCount": 0,
            "objectsGraded": 0,
            "readErrorCount": 0,
        },
        "wip": _wip_block(0),
        "coverage": _coverage_block(),
    }


def _wip_block(in_flight: int) -> dict[str, Any]:
    """The WIP-ceiling reading.

    ⚠️ ``enforced`` is TRUE as of 2026-09-01 and ``state`` says so. A ceiling
    that is merely DECLARED and one that is ENFORCED are different facts, and
    the reading must not drift from which one holds — IN EITHER DIRECTION. It
    read ``declared_not_enforced`` for ~20 minutes after Phase C shipped the
    guard, which told the operator's own screenshot that a limit binding their
    CI was advisory.

    ⚠️ ``state`` stays a STRING rather than being folded into the boolean,
    because a third state is foreseeable (enforced-but-under-an-approved-
    exception) and a bool cannot carry it.
    """
    return {
        "ceiling": _WIP_CEILING,
        "inFlight": in_flight,
        "enforced": _CEILING_ENFORCED,
        "state": CEILING_STATE,
        "note": (
            "The ceiling of 8 is ENFORCED: scripts/ci/check_wip_ceiling.py fails "
            "CI on a ninth `in_flight` object, and exceeding it needs an approved "
            "justification at docs/claude/work/wip-ceiling-exception.yaml. "
            "⚠️ Enforcement is in CI, NOT in this route — this count is still a "
            "reading and this route still gates nothing; a read path that refused "
            "something would be a second copy of the rule, free to drift from the "
            "one that binds."
        ),
    }


def _coverage_block() -> dict[str, Any]:
    """What this store does NOT cover — served on every response, deliberately.

    The view renders a store that is knowingly partial. Leaving the consumer to
    infer that is how a partial picture gets read as a complete one.
    """
    return {
        # ⚠️ STILL FALSE, and deliberately so even though the migration LANDED.
        # `complete` asks whether this store is the whole of the system's work,
        # not whether Phase C ran. It is not: `steps` is empty, and nobody has
        # audited that every workstream has an object. Flipping this to True on
        # the strength of the migration would convert "we carried the backlog"
        # into "we account for everything", which is a different claim.
        "complete": False,
        "scope": "carried-backlog-plus-operating-layer-build",
        # ⚠️ THE TWO KEY NAMES BELOW ARE KEPT DELIBERATELY, stale-sounding and
        # all. The SPA binds them through its api-contract checker, precisely so
        # a bot-side rename FAILS ITS BUILD instead of rendering a silent
        # em-dash — so renaming them here is a cross-repo change, not a tidy-up,
        # and doing it in a bot-only PR would break the consumer this route
        # exists for. The VALUES carry the correction instead.
        "carriedRowsApprox": _CARRIED_ROWS_MIGRATED,
        "carriedRowsMigrateIn": CARRIED_ROWS_MIGRATED_IN,
        "carriedRowsMigratedOn": "2026-09-01",
        "note": (
            "The ~572 carried backlog rows MIGRATED IN on 2026-09-01 (Phase C), "
            "so this store is no longer the operating-layer build alone. ⚠️ Read "
            "the LIFECYCLE, not the count: they arrived `dormant` — carried, not "
            "started, and NOT queued. Carrying everything is not the same as "
            "everything being open. ⚠️ `complete` is still false: there are no "
            "`steps`, and no audit has established that every workstream has an "
            "object. A bug to fix still goes to the review backlogs; what a "
            "session must KNOW before it plans is still OPEN-ITEMS.json."
        ),
    }


def _build_index() -> dict[str, Any]:
    work_dir = _work_dir()
    if not work_dir.exists():
        return _empty_envelope("work store directory not present")

    read_errors: list[dict[str, str]] = []

    intents: list[dict[str, Any]] = []
    for path in _yaml_files(_intents_dir()):
        data, err = _load_yaml(path)
        if err:
            read_errors.append({"path": f"intents/{path.name}", "error": err, "level": "intent"})
            continue
        intents.append(_intent_row(data, path))

    objects: list[dict[str, Any]] = []
    for path in _yaml_files(_objects_dir()):
        data, err = _load_yaml(path)
        if err:
            read_errors.append({"path": f"objects/{path.name}", "error": err, "level": "object"})
            continue
        objects.append(_object_row(data, path))

    steps: list[dict[str, Any]] = []
    for path in _yaml_files(_steps_dir()):
        data, err = _load_yaml(path)
        if err:
            read_errors.append({"path": f"steps/{path.name}", "error": err, "level": "step"})
            continue
        steps.append(_step_row(data, path))

    # Lifecycle roll-up. Every state ships with an explicit zero, and an object
    # we could NOT read is counted in `unknown` — so the buckets sum to the file
    # count and the partition is checkable rather than trusted.
    lifecycle = {state: 0 for state in (*_LIFECYCLE_STATES, _UNKNOWN)}
    for obj in objects:
        lifecycle[obj["lifecycle"]] += 1
    lifecycle[_UNKNOWN] += sum(1 for e in read_errors if e["level"] == "object")

    in_flight = sum(lifecycle[s] for s in _COUNTS_AGAINST_CEILING)

    # Objects grouped under their intent, so a consumer can render the tree
    # without a second pass. An object naming an intent that does not exist is
    # kept and flagged, never silently reparented.
    intent_ids = {i["id"] for i in intents}
    for obj in objects:
        parent = obj.get("parentIntent")
        obj["parentIntentKnown"] = parent in intent_ids if parent else False

    # Objects ordered by how much they demand attention: in flight first, then
    # what is ready to pick up, then blocked, then settled.
    order = {s: n for n, s in enumerate(
        ("in_flight", "ready", "waiting", "dormant", "done", "accepted", _UNKNOWN)
    )}
    objects.sort(key=lambda o: (order.get(o["lifecycle"], 99), o["id"]))

    object_ids = {o["id"] for o in objects}
    for obj in objects:
        for edge in obj["blockedOn"]:
            ref = edge.get("ref")
            # Only an `object` edge is expected to name a row in this store; an
            # external_event / operator_decision edge names something else and
            # must not be graded as a dangling reference.
            edge["refResolvedInStore"] = (
                ref in object_ids if edge.get("kind") == "object" and isinstance(ref, str) else None
            )

    return {
        "present": True,
        "reason": None,
        "intents": intents,
        "objects": objects,
        "steps": steps,
        "readErrors": read_errors,
        "lifecycle": lifecycle,
        "summary": {
            "intentCount": len(intents),
            "objectCount": len(objects),
            "stepCount": len(steps),
            "objectsGraded": len(objects),
            "readErrorCount": len(read_errors),
        },
        "wip": _wip_block(in_flight),
        "coverage": _coverage_block(),
    }


def _get_index() -> dict[str, Any]:
    key = _cache_key()
    if _CACHE.get("key") != key:
        _CACHE["key"] = key
        try:
            _CACHE["data"] = _build_index()
        except Exception as exc:  # noqa: BLE001  # allow-silent: not silent — the failure is logged WITH a stack trace and surfaced to the caller as present:false + reason, so it degrades visibly rather than as an empty result. A Tier-1 read surface must not 5xx (roadmap.py's contract).
            # Loud on BOTH channels, deliberately. `exc_info=True` puts the stack
            # trace in the journal, and the envelope carries `present: false`
            # plus the reason — so "the store failed to build" can never be read
            # as "the store is empty", which is the whole point of the guard
            # this justification answers.
            logger.warning("work: failed to build index: %s", exc, exc_info=True)
            _CACHE["data"] = _empty_envelope(f"index build failed: {exc}")
    return _CACHE["data"]


@router.get("")
def get_work() -> dict[str, Any]:
    """The work store: intents, objects, steps, lifecycle roll-up, coverage."""
    return _get_index()


@router.get("/object/{object_id}")
def get_work_object(object_id: str) -> dict[str, Any]:
    """One work object, in full. ``present: false`` (HTTP 200) on unknown id."""
    if not _OBJECT_ID_RE.match(object_id) or ".." in object_id:
        raise HTTPException(status_code=400, detail="invalid object id")
    directory = _objects_dir()
    for suffix in (".yaml", ".yml"):
        path = directory / f"{object_id}{suffix}"
        # Defense in depth: keep the resolved path inside the objects dir.
        try:
            resolved = path.resolve()
            resolved.relative_to(directory.resolve())
        except (ValueError, OSError):
            raise HTTPException(status_code=400, detail="invalid object id")
        if not resolved.exists():
            continue
        data, err = _load_yaml(resolved)
        if err:
            # We FOUND the file and could not read it. That is not "no such
            # object" — say which it is.
            return {"present": False, "id": object_id, "error": err}
        return {"present": True, "id": object_id, "object": _object_row(data, resolved)}
    return {"present": False, "id": object_id}


# ═════════════════════════════════════════════════════════════════════════════
# PHASE H — the control half. Decisions, answerable from the UI.
# ═════════════════════════════════════════════════════════════════════════════
#
# The operating model puts "anything waiting on the operator" at the TOP of the
# dashboard, and requires decisions to be answerable from the UI *"so the
# operator is not the bottleneck on their own decisions."* The measured
# constraint is DECISION, so this is the one route pair in the operating layer
# that acts on it directly.
#
# ⚠️ THE REPO STAYS THE SOURCE OF TRUTH. The write route below appends a
# SUBMISSION to the live layer and nothing else; `committed` is read back off
# the object YAML in the repo. See `src/runtime/work_decisions.py` for why that
# is the whole transit contract rather than an implementation detail.



# ═════════════════════════════════════════════════════════════════════════════
# The manager checklist — the live Workflow page's other half
# ═════════════════════════════════════════════════════════════════════════════

#: ⚠️ THIS ROUTE COMPUTES NO STATUS OF ITS OWN. Every status reading below
#: comes from ``src/runtime/manager_status.py`` — the same module that renders
#: the Telegram ``/status`` command — so the page and the bot cannot tell the
#: operator two different stories about the same row. The `state`/`status`
#: merge in particular has ONE owner (`manager_status.effective_state`) and
#: re-deriving it here, or in the SPA's TypeScript, would create a SECOND
#: definition of an item's status, free to drift from the one
#: `scripts/ops/manager_view.py` and the manager guards read. That is the class
#: this repo keeps a guard family for.

_CHECKLIST_CACHE_TTL_S = 20.0
_checklist_cache: tuple[float, dict[str, Any]] | None = None

#: A page must be able to say WHY a row's status is what it is, not just what
#: it is. The wording lives here rather than in `manager_status` because it is
#: PRESENTATION for this surface — `manager_status` renders the same five bases
#: for Telegram, under its own character budget. ⚠️ The wording is per-surface;
#: **the merge is not**. `effective_state` remains the one owner of which value
#: wins, and nothing below re-decides that.
_STATUS_BASIS_NOTES = {
    manager_status.STATUS_BASIS_STATE_ONLY: (
        "Only `state` is declared. This is the ordinary row and the field "
        "every other consumer of this file reads."
    ),
    manager_status.STATUS_BASIS_STATUS_ONLY: (
        "Only `status` is declared — no `state`. ⚠️ Nothing else in the repo "
        "reads `status` on this file, so this row is invisible to the Telegram "
        "/status sections and to scripts/ops/manager_view.py. Measured "
        "2026-09-10: 65 of 244 rows are in this position."
    ),
    manager_status.STATUS_BASIS_AGREE: (
        "`state` and `status` are both declared and identical. Nothing to "
        "reconcile."
    ),
    manager_status.STATUS_BASIS_DISAGREE: (
        "⚠️ `state` and `status` are both declared and DIFFERENT. `state` is "
        "shown because it is what every other consumer reads — that is a rule "
        "for picking a value, NOT a finding that `status` is wrong. Which one "
        "is right is a question for whoever owns the row; neither field was "
        "edited. Measured 2026-09-10: 13 of 244 rows, four of them reading "
        "`state: in_flight` against `status: done`."
    ),
    manager_status.STATUS_BASIS_UNDECLARED: (
        "Neither `state` nor `status` is declared, so this row asserts no "
        "status at all. That is not the same as a status of `triage` and it is "
        "not a rendering gap — nobody has said."
    ),
}


def _render_age(hours: float) -> str:
    """An age string in the unit a reader can act on.

    ⚠️ **The thresholds mirror `Work.svelte`'s own `age()` helper on purpose.**
    The two render the SAME quantity onto the SAME page -- the stamp line and
    this warning sentence -- so a server that said "0.3h ago" beside a stamp
    reading "18m ago" would look like two different measurements of two
    different things. They are two renderings and they must agree.

    ⚠️ This is a SECOND implementation of that formatting and it can drift;
    the repo boundary is why it is not shared. What keeps it honest is that
    both sides read the same `commitAgeHours` field, so any disagreement is
    cosmetic and visible on one screen rather than silent.
    """
    if hours < 1.0:
        return f"{round(hours * 60.0)}m"
    if hours < 48.0:
        return f"{hours:.1f}h"
    return f"{hours / 24.0:.1f}d"


#: How old this tree's VIEW OF MAIN may get before `synced` stops meaning
#: anything.
#:
#: ⚠️ **A DIFFERENT QUANTITY FROM `CHECKLIST_STALE_AFTER_MINUTES`, WITH A
#: DIFFERENT BASIS -- do not merge them if they ever coincide numerically.**
#: That one is a recorded OPERATOR DECISION about how late a MANAGER's push may
#: be. This one is a property of `ict-git-sync.timer`: it fires every 5 minutes
#: (`OnUnitActiveSec=5min`, `RandomizedDelaySec=30`), so a view of main older
#: than 20 minutes is roughly FOUR consecutive missed cycles -- a sync failure
#: rather than jitter. Reusing one number for both would be a coincidence
#: masquerading as a decision.
TREE_FETCH_STALE_AFTER_MINUTES = 20.0

#: How late a manager's checklist push may be before the page WARNS.
#:
#: ⚠️ **15 MINUTES IS AN OPERATOR DECISION, TYPED VERBATIM**
#: (`DEC-20260910-WORKFLOW-PAGE-STALENESS-THRESHOLD`, 2026-09-10). They were
#: offered ~1h or "leave it at 3.0h" and chose NEITHER, typing "15 min" --
#: tighter than either. It is stored in MINUTES because that is the unit they
#: answered in; expressing it as `0.25` hours would launder a decision into a
#: rounded figure and make the next reader think it was derived.
#:
#: ⚠️ **IT IS EXPECTED TO FIRE OFTEN, AND THAT IS THE POINT.** The page is
#: exactly as fresh as the last PUSH plus `ict-git-sync`'s ~5-minute pull, so
#: at 15 minutes this warns whenever a manager goes two sync cycles without
#: pushing. That makes manager lateness visible to the OPERATOR rather than
#: only to a guard, which is the enforcement CLAUDE.md names. **Do not widen
#: it back because it fires often, and do not add a softer second tier** --
#: both were forbidden in terms when the decision was routed.
CHECKLIST_STALE_AFTER_MINUTES = 15.0

def _freshness_warnings(
    tree: Any, commit: Any, *,
    stale_after_minutes: float = CHECKLIST_STALE_AFTER_MINUTES,
    fetch_stale_after_minutes: float = TREE_FETCH_STALE_AFTER_MINUTES,
) -> list[str]:
    """What makes THIS page's data untrustworthy right now, in plain words.

    ⚠️ This is the half that stops a frozen page reading identically to a live
    one — the coordination board's 2026-09-07 failure, where a board at
    GitHub's comment cap served reads byte-identically to a working one for
    ~20h and the documented staleness check passed every time.

    An empty list means the three things below were CHECKED and none of them
    fired; it does not mean nothing was wrong, and the page states the raw
    readings beside it so a consumer is never left with only this verdict.
    """
    out: list[str] = []

    if tree.state == manager_status.TREE_BEHIND:
        out.append(
            f"The VM's tree is {tree.behind_commits} commit(s) BEHIND "
            f"origin/main, so a checklist edit already pushed may not be here "
            f"yet. ict-git-sync pulls roughly every 5 minutes."
        )
    elif tree.state == manager_status.TREE_UNKNOWN:
        out.append(
            f"The VM's tree could not be graded against origin/main "
            f"({tree.note or 'no detail'}) — this is *we could not look*, NOT "
            f"a clean tree. Treat the rows below as unverified."
        )
    elif tree.state != manager_status.TREE_SYNCED:
        out.append(f"Unrecognised tree state {tree.state!r}.")

    # ── `synced` has to justify itself ─────────────────────────────────────
    # ⚠️ `synced` compares HEAD against the LOCAL `origin/main` ref, and
    # `scripts/deploy_pull_restart.sh` runs `git fetch` then
    # `git reset --hard origin/main` -- so on the live VM that equality holds
    # BY CONSTRUCTION and `treeBehindCommits: 0` is guaranteed rather than
    # measured. It cannot tell a tree that fetched 30 seconds ago from one
    # whose sync died an hour back. MEASURED 2026-09-10: the page read
    # `synced` / `behind 0` at 9071b8321 while GitHub's main was b8cbbbf10 --
    # a strict ancestor, genuinely one commit behind. Nothing was wrong with
    # the comparison; what was missing was the age of its base.
    if tree.state == manager_status.TREE_SYNCED:
        age = getattr(tree, "main_ref_age_hours", None)
        if age is None:
            out.append(
                "How long ago this tree last FETCHED could not be established, "
                "so `synced` cannot be trusted: it compares HEAD against the "
                "LOCAL origin/main ref, and without a fetch age there is no "
                "way to tell a current view of main from an ancient one. This "
                "is *we could not look*, NOT a fresh tree."
            )
        elif age * 60.0 >= fetch_stale_after_minutes:
            out.append(
                f"This tree last FETCHED {_render_age(age)} ago, so `synced` "
                f"only means it is level with main AS OF THEN. ict-git-sync "
                f"pulls every ~5 minutes, so this is several missed cycles — "
                f"treat the rows below as possibly behind whatever has landed "
                f"since."
            )

    if commit.state == manager_status.FILE_COMMIT_UNCOMMITTED:
        out.append(
            "The checklist has NO commit on this tree, so it has never been "
            "pushed and no other session can see it."
        )
    elif commit.state == manager_status.FILE_COMMIT_UNKNOWN:
        out.append(
            f"The checklist's last commit could not be read "
            f"({commit.note or 'no detail'}) — the as-of stamp below is "
            f"absent because we could not look, not because it is new."
        )
    elif (commit.age_hours is not None
          and commit.age_hours * 60.0 >= stale_after_minutes):
        # ⚠️ RENDER THE UNIT THE READER CAN USE. This said `{age_hours:.1f}h`
        # while the threshold was 3.0h, where it was fine. At a 15-MINUTE
        # threshold the very first warning it can emit reads "0.3h ago" --
        # true, and unreadable. The threshold change without the unit change
        # would have shipped a banner nobody can act on.
        said = _render_age(commit.age_hours)
        out.append(
            f"The checklist was last COMMITTED {said} ago. A manager is "
            f"expected to push it before answering a status request, so this "
            f"page is {said} behind whatever they are actually working on."
        )

    if commit.dirty:
        out.append(
            "The checklist differs from its last commit in the VM's working "
            "tree, so the as-of stamp describes different bytes than the rows "
            "being served. On the live VM this should never happen."
        )
    elif commit.dirty is None and commit.state == manager_status.FILE_COMMIT_KNOWN:
        out.append(
            "Whether the served file matches its last commit could not be "
            "established — the as-of stamp may not describe these bytes."
        )
    return out


def _checklist_item(
    item: dict[str, Any],
    vocabulary: tuple[str, ...],
    registered: set[str] | None,
) -> dict[str, Any]:
    """One checklist row: every declared field VERBATIM, plus the grading.

    ⚠️ The whole row is passed through untouched under ``fields``. The
    operator's ask is that a collapsed row expands to *every* remaining detail,
    and the checklist's keys are free-form prose that no allowlist here could
    keep up with — measured on the real file, items carry keys like
    ``the_one_owner_rule_that_decides_the_design`` and
    ``⚠️_ITS_BLOCKER_IS_GONE_AND_SO_IS_ITS_PATH``. An allowlist would silently
    drop exactly the detail the page exists to show.
    """
    eff = manager_status.effective_state(item, vocabulary=vocabulary)
    owner_rendered, owner_grade = manager_status.grade_owner(
        item.get("owner"), registered)
    return {
        "id": item.get("id"),
        "title": item.get("title"),
        "owner": item.get("owner"),
        "ownerRendered": owner_rendered,
        "ownerGrade": owner_grade,
        "lane": item.get("lane"),
        "tier": item.get("tier"),
        "priority": item.get("priority"),
        "status": {
            # `value` is what a renderer groups and labels by. `basis` says how
            # it was arrived at and is NOT inferable from `value`: an
            # `in_flight` on `disagree` and an `in_flight` on `agree` are the
            # same value and different facts.
            "value": eff.value,
            "basis": eff.basis,
            "declaredState": eff.state,
            "declaredStatus": eff.status,
            "disagrees": eff.disagrees,
            "inDeclaredVocabulary": eff.in_declared_vocabulary,
            "note": _STATUS_BASIS_NOTES.get(
                eff.basis, f"unrecognised basis {eff.basis!r}"),
        },
        "sectioned": eff.value in manager_status._SECTIONED_STATES,
        "fields": _jsonable(item),
    }


#: Why a lane's state may or may not be trusted, per observation grade. The
#: wording is PRESENTATION for this surface; the grading itself has one owner in
#: `manager_status.observe_session` and is not re-derived here.
_OBSERVATION_NOTES = {
    manager_status.OBS_RECENT: (
        "Observed within the manager lease's own TTL, so the state below is as "
        "current as this system can make it."
    ),
    manager_status.OBS_STALE: (
        "⚠️ Nobody has looked at this lane for longer than the manager lease's "
        "TTL, so its state may simply be OUT OF DATE rather than wrong. "
        "Measured 2026-09-10: two lanes read `working` while both were idle and "
        "completed. Do not read the state below as live."
    ),
    manager_status.OBS_UNKNOWN: (
        "⚠️ No usable observation timestamp on this row, so how stale its state "
        "is could not be established. That is *we did not look*, NOT a fresh "
        "lane."
    ),
}


def _sessions_panel() -> dict[str, Any]:
    """Live lanes from the sub-session registry, WITH how stale each reading is.

    ⚠️ THIS IS NOT A LIVE FEED AND THE PAYLOAD SAYS SO. `list_sessions` is an
    ``mcp__*`` tool no route holds, so a lane's state is only ever as good as
    the last MANAGER OBSERVATION written into the registry. Publishing it
    without that caveat is how a dead lane reads as a running one.
    """
    repo = Path(repo_root())
    read = manager_status.read_json_file(repo / manager_status.SESSIONS_RELPATH)
    lease = manager_status.read_json_file(repo / manager_status.LEASE_RELPATH)
    stale_minutes = manager_status._observation_stale_minutes(
        lease.data if lease.state == "read" else None)

    if read.state != "read":
        # ⚠️ NOT "no lanes are running". We could not read the register.
        return {
            "present": False,
            "readState": read.state,
            "reason": read.error,
            "lanes": [],
            "summary": {"live": 0, "byObservationState": {
                s: 0 for s in manager_status.OBSERVATION_STATES}},
            "staleAfterMinutes": stale_minutes,
            "note": _SESSIONS_NOTE,
        }

    rows = read.data.get("sessions")
    rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    lanes = []
    by_obs = {s: 0 for s in manager_status.OBSERVATION_STATES}
    for row in rows:
        if row.get("state") not in manager_status.LIVE_SESSION_STATES:
            continue
        obs = manager_status.observe_session(row, stale_minutes=stale_minutes)
        by_obs[obs.state] = by_obs.get(obs.state, 0) + 1
        lanes.append({
            "sessionId": row.get("session_id"),
            "title": row.get("title"),
            "item": row.get("checklist_item"),
            "object": row.get("owns_object"),
            "state": row.get("state"),
            "blockedOn": _jsonable(row.get("needs_action")
                                   or row.get("depends_on")),
            "branches": _jsonable(row.get("branches") or (
                [row["branch"]] if row.get("branch") else [])),
            "prs": _jsonable(row.get("prs") or (
                [row["pr"]] if row.get("pr") else [])),
            "spawnedAt": row.get("spawned_at"),
            "observation": {
                "state": obs.state,
                "basis": obs.basis,
                "at": obs.at,
                # The FIELD the timestamp came from. Measured 2026-09-10, the
                # newest observation lives under five different key names across
                # the 21 live rows, and they do not mean the same thing — so a
                # reader must be able to see which one answered.
                "fromField": obs.from_field,
                "ageMinutes": (round(obs.age_minutes, 1)
                               if obs.age_minutes is not None else None),
                "note": _OBSERVATION_NOTES.get(obs.state, ""),
            },
        })

    # Stalest first: the rows most likely to be lying are the ones to read.
    lanes.sort(key=lambda lane: -(lane["observation"]["ageMinutes"] or 1e9))
    return {
        "present": True,
        "readState": read.state,
        "asOf": read.data.get("updated_at"),
        "lanes": lanes,
        "summary": {"live": len(lanes), "registryRows": len(rows),
                    "byObservationState": by_obs},
        "staleAfterMinutes": stale_minutes,
        "note": _SESSIONS_NOTE,
    }


_SESSIONS_NOTE = (
    "Session state is only as fresh as the last MANAGER OBSERVATION written "
    "into docs/claude/work/SESSIONS.json. This is NOT a live feed: reading the "
    "platform's own session list needs `list_sessions`, an mcp__* tool no API "
    "route holds. Read each lane's observation age beside its state."
)


def _checklist_payload() -> dict[str, Any]:
    """Build the checklist envelope. Best-effort: never raises to the caller."""
    repo = Path(repo_root())
    read = manager_status.read_json_file(repo / manager_status.CHECKLIST_RELPATH)
    tree = manager_status.read_tree_provenance(repo_dir=repo)
    commit = manager_status.read_file_commit(
        manager_status.CHECKLIST_RELPATH, repo_dir=repo)

    freshness = {
        "path": manager_status.CHECKLIST_RELPATH,
        # ⚠️ THE PAGE MUST BE ABLE TO TELL FRESH FROM STALE. The route reads the
        # VM's WORKING TREE, which `ict-git-sync` pulls roughly every 5
        # minutes, so the page is exactly as fresh as the last PUSH plus that
        # sync interval. There is no separate "update the page" step — which is
        # the point — but it means a manager must PUSH the checklist BEFORE
        # answering a status request, not after. These three readings are what
        # let a stale page announce itself instead of reading like a live one.
        "commitState": commit.state,
        "commitSha": commit.sha,
        "committedAt": commit.committed_at,
        "commitAgeHours": (round(commit.age_hours, 3)
                           if commit.age_hours is not None else None),
        # `None` is "we could not look", never "clean". A dirty file means the
        # commit stamp describes different bytes than the ones being served.
        "workingTreeDirty": commit.dirty,
        "treeState": tree.state,
        "treeHeadSha": tree.head_sha,
        "treeMainSha": tree.main_sha,
        "treeBehindCommits": tree.behind_commits,
        # ⚠️ THE FIELD THAT MAKES `treeState` READABLE. `treeBehindCommits` is
        # zero by construction on the live VM (fetch, then hard-reset), so it
        # is this number — how old our VIEW of main is — that carries the
        # staleness. `None` is "we could not establish when we last fetched",
        # never 0.0.
        "mainRefAgeHours": (round(tree.main_ref_age_hours, 3)
                            if tree.main_ref_age_hours is not None else None),
        "treeNote": tree.note,
        "stamp": manager_status.render_tree_stamp(tree),
        "note": commit.note,
        # ⚠️ An EMPTY list means the checks ran and none fired. It is not a
        # claim that the data is correct, and the raw readings above are served
        # beside it precisely so a consumer is never left with only a verdict.
        "warnings": _freshness_warnings(tree, commit),
    }

    if read.state != "read":
        # ⚠️ NOT an empty checklist. "we could not read it" and "there is no
        # work" are opposite statements and only one of them is good news.
        return {
            "present": False,
            "readState": read.state,
            "reason": read.error,
            "items": [],
            "declaredStates": {},
            "summary": _checklist_summary([], 0),
            "freshness": freshness,
            # The lanes are a DIFFERENT register: an unreadable checklist says
            # nothing about whether the session registry can be read.
            "sessions": _sessions_panel(),
        }

    raw_items = read.data.get("items")
    items = ([i for i in raw_items if isinstance(i, dict)]
             if isinstance(raw_items, list) else [])
    dropped = ((len(raw_items) - len(items))
               if isinstance(raw_items, list) else 0)

    vocabulary = manager_status._declared_vocabulary(read.data)
    sessions = manager_status.read_json_file(
        repo / manager_status.SESSIONS_RELPATH)
    registered = manager_status._registered_session_ids(sessions)

    rows = [_checklist_item(i, vocabulary, registered) for i in items]
    return {
        "present": True,
        "readState": read.state,
        # ⚠️ Explicitly None on the HEALTHY envelope, matching `/api/bot/work`
        # and the decision inbox. A key that VANISHES makes a consumer branch on
        # absence, and absence is not one of the states. Caught by the SPA's own
        # api-contract checker (ict-trader-dashboard/webapp/tests/api-contract.mjs)
        # against a real captured payload, which is exactly the direction
        # `provenance-consumer-guard` cannot see.
        "reason": None,
        "asOf": read.data.get("as_of") or read.data.get("updated_at"),
        "cycle": read.data.get("cycle"),
        "managerSession": read.data.get("manager_session"),
        "schemaVersion": read.data.get("schema_version"),
        # The file's OWN vocabulary, served rather than restated, so the page
        # renders the definitions the file declares instead of a copy of them.
        "declaredStates": _jsonable(read.data.get("states") or {}),
        # `None` is "we could not look" — the owner column then says so rather
        # than reporting every owner as unregistered, which would be a
        # fabricated finding about the register MI-15 already tracks.
        "sessionsRegistryState": sessions.state,
        "items": rows,
        "summary": _checklist_summary(rows, dropped),
        "freshness": freshness,
        "sessions": _sessions_panel(),
    }


def _checklist_summary(rows: list[dict[str, Any]], dropped: int) -> dict[str, Any]:
    """Counts that SUM to the population, so the partition is checkable.

    ``byBasis`` ships all five states with explicit zeros for the same reason
    ``lifecycle`` above does: a key that vanishes makes a consumer branch on
    absence, and absence is not one of the states.
    """
    by_status: dict[str, int] = {}
    by_basis = {b: 0 for b in manager_status.STATUS_BASES}
    off_vocabulary = 0
    unsectioned = 0
    for row in rows:
        status = row["status"]
        key = status["value"] or "(no status declared)"
        by_status[key] = by_status.get(key, 0) + 1
        by_basis[status["basis"]] = by_basis.get(status["basis"], 0) + 1
        if status["value"] is not None and not status["inDeclaredVocabulary"]:
            off_vocabulary += 1
        if not row["sectioned"]:
            unsectioned += 1
    return {
        "total": len(rows),
        "byStatus": by_status,
        "byBasis": by_basis,
        # The finding the page must not bury: measured 2026-09-10 over the real
        # 244-item file, 13 rows declare a `state` and a `status` that
        # DISAGREE, and 18 carry a status no `/status` section covers.
        "disagreeing": by_basis.get(manager_status.STATUS_BASIS_DISAGREE, 0),
        "statusOnly": by_basis.get(manager_status.STATUS_BASIS_STATUS_ONLY, 0),
        "offDeclaredVocabulary": off_vocabulary,
        "unsectioned": unsectioned,
        "nonObjectEntriesDropped": dropped,
    }


@router.get("/checklist")
def get_work_checklist() -> dict[str, Any]:
    """The manager checklist, graded — the live Workflow page's data source.

    Read-only, file-backed, no DB, no secrets, no write surface. Best-effort:
    an unreadable checklist degrades to ``present: false`` WITH its read state,
    never a 5xx and never an empty list that reads as "no work".
    """
    global _checklist_cache
    now = time.monotonic()
    cached = _checklist_cache
    if cached is not None and (now - cached[0]) < _CHECKLIST_CACHE_TTL_S:
        return cached[1]
    try:
        payload = _checklist_payload()
    except Exception as exc:  # noqa: BLE001  # allow-silent: not silent — logged WITH a stack and surfaced as present:false + reason. A Tier-1 read surface must not 5xx (roadmap.py's contract), and a checklist page that 500s is invisible rather than empty.
        logger.warning("work: checklist read failed: %s", exc, exc_info=True)
        return {
            "present": False,
            "readState": "unreadable",
            "reason": f"checklist read failed: {exc}",
            "items": [],
            "declaredStates": {},
            "summary": _checklist_summary([], 0),
            "freshness": {"path": manager_status.CHECKLIST_RELPATH,
                          "commitState": manager_status.FILE_COMMIT_UNKNOWN,
                          "treeState": manager_status.TREE_UNKNOWN,
                          "workingTreeDirty": None,
                          "note": "payload build failed before provenance was read"},
        }
    _checklist_cache = (now, payload)
    return payload


#: Operator-facing wording per answer state. PRESENTATION for this surface; the
#: grading itself has ONE owner in `work_decisions.grade_answer_state` and is
#: not re-derived here or in the SPA.
_ANSWER_STATE_NOTES = {
    NOT_SUBMITTED: "Waiting on you. Nobody has answered this through any channel.",
    IN_TRANSIT: (
        "Submitted, NOT yet decided — it becomes a decision only when it is "
        "committed into the work object."
    ),
    COMMITTED: "Answered through the decision channel (SPA or Telegram) and committed.",
    ANSWERED_IN_CONVERSATION: (
        "Answered IN CONVERSATION and recorded by hand as a verdict. It never "
        "travelled through the decision channel, which is why it used to show "
        "as unanswered here."
    ),
    ENGAGED_NOT_SETTLED: (
        "⚠️ You responded but did NOT settle it — this question is still open. "
        "Shown apart from both answered and untouched, because it is neither."
    ),
    VERDICT_UNRECOGNISED: (
        "⚠️ Carries a verdict that neither vocabulary recognises, so it could "
        "not be graded. That is *we could not tell*, NOT an answer."
    ),
    UNREADABLE: (
        "⚠️ The transit channel could not be read, so whether an answer is in "
        "flight is unknown. Not a claim that you have not answered."
    ),
}


def _decision_inbox() -> dict[str, Any]:
    """Every operator decision the store is waiting on, with its answer state.

    Two sources of a pending decision, and they are counted separately because
    they are different facts:

      * a ``decision_requests[]`` entry — an ANSWERABLE question, with options
      * a ``blocked_on`` edge of ``kind: operator_decision`` — a declared
        dependency on the operator that carries NO answerable request

    The second is the one worth surfacing loudly: it is a question the operator
    is blocking on that they cannot answer from the UI, because nobody wrote it
    down as a request. Folding the two together would hide exactly that gap.
    """
    index = _get_index()
    rows, transit_state, transit_error = read_transit()
    latest = latest_submissions(rows)
    now = datetime.now(timezone.utc)
    # Built ONCE per inbox build and shared across every request: it caches
    # each canonical register after the first read, and the backlog union alone
    # is 1691 rows. Re-reading per request would put real cost on an API path
    # for no new information.
    subject_resolver = SubjectResolver(repo_root())

    requests: list[dict[str, Any]] = []
    unanswerable: list[dict[str, Any]] = []
    malformed = 0

    for obj in index.get("objects", []):
        object_id = obj.get("id")
        raw_extra = obj.get("extra") or {}
        # `decision_requests` is a free-form key, so `_object_row` preserves it
        # under `extra` verbatim. Read it from there rather than adding a second
        # parse of the file — one reader, one shape.
        source = {"decision_requests": raw_extra.get("decision_requests")}
        malformed += malformed_request_count(source)
        for req in normalise_requests(source, str(object_id)):
            submission = latest.get((str(object_id), req["id"]))
            state = grade_answer_state(req, submission, transit_state)
            # MI-258: does the thing this asks about still EXIST? A perfectly
            # graded question about a deleted row is still a question that
            # should not be on the operator's screen. One resolver for the
            # whole build — every register is read at most once.
            subject = grade_subject(req, subject_resolver)
            requests.append(
                {
                    **req,
                    **subject,
                    "objectTitle": obj.get("title"),
                    "objectLifecycle": obj.get("lifecycle"),
                    "parentIntent": obj.get("parentIntent"),
                    "answerState": state,
                    "answerStateNote": _ANSWER_STATE_NOTES.get(state, ""),
                    # ⚠️ PUBLISHED SO NO CONSUMER RE-DERIVES IT. The SPA
                    # previously filtered its "Waiting on you" list on
                    # `answerState !== "committed"` — a SECOND definition of
                    # "answered" living in TypeScript, free to drift from this
                    # one, and it is the same mistake as merging state/status.
                    # Read from SETTLED_STATES, which `work_decisions` owns.
                    "settled": state in SETTLED_STATES,
                    # ⚠️ PUBLISHED FOR THE SAME REASON `settled` IS: whether a
                    # question is WORK is one decision with one owner, and the
                    # SPA re-deriving it in TypeScript is how a second
                    # definition is born. `settled` alone is no longer the
                    # test — a live, unanswered question about a row that was
                    # deleted is not work either.
                    "actionable": is_actionable(
                        settled=state in SETTLED_STATES,
                        subject_state=subject["subjectState"],
                    ),
                    "transit": transit_window(
                        submission if state == IN_TRANSIT else None, now=now
                    ),
                }
            )
        for edge in obj.get("blockedOn", []):
            if edge.get("kind") != "operator_decision":
                continue
            ref = edge.get("ref")
            # An edge whose `ref` names a request we already surfaced is that
            # request's dependency edge, not a separate gap.
            if isinstance(ref, str) and any(
                r["objectId"] == object_id and r["id"] == ref for r in requests
            ):
                continue
            unanswerable.append(
                {
                    "objectId": object_id,
                    "objectTitle": obj.get("title"),
                    "objectLifecycle": obj.get("lifecycle"),
                    "ref": ref,
                    "since": edge.get("since"),
                    "note": edge.get("note"),
                }
            )

    by_state = {state: 0 for state in ANSWER_STATES}
    by_subject = {state: 0 for state in SUBJECT_STATES}
    for req in requests:
        by_state[req["answerState"]] += 1
        by_subject[req["subjectState"]] += 1

    # collapsed-state: subject_gone — THIS ROUTE BRANCHES ON ONE OF THE FOUR
    # STATES, DELIBERATELY, AND THE OTHER THREE ARE PUBLISHED RATHER THAN
    # MANUFACTURED INTO A BRANCH. The only question asked here is "is this
    # work?", and only `subject_gone` answers no: `live`, `unknown` and
    # `undeclared` all KEEP the question on the operator's list, which is the
    # fail-safe direction and the whole point — `unknown` is *we could not
    # look*, and withdrawing a decision on that would be the inversion MI-258
    # exists to refuse. The three are not collapsed by being unbranched here:
    # they are graded apart by `decision_subject` (distinct `subjectBasis` and
    # `subjectNote` per state), counted apart in `bySubjectState` below, and
    # rendered apart by the SPA from that data. Adding a second branch at this
    # site purely to satisfy the guard is the decorative branch CLAUDE.md's
    # `BYBIT_HEDGE_MODE_SYMBOLS` row names in terms.
    #
    # An unanswered question whose subject is gone. Counted SEPARATELY rather
    # than silently subtracted from `awaitingOperator`: a number that shrinks
    # with no published reason is indistinguishable from work disappearing.
    moot_unanswered = sum(
        1 for r in requests
        if r["subjectState"] == SUBJECT_GONE and not r["settled"]
    )

    # Attention order: what the operator can act on NOW comes first. An answered
    # question is not a task.
    # ⚠️ SETTLED ROWS SORT LAST BUT ARE NOT HIDDEN. The operator needs to see
    # what they decided AND under what condition — this page is the one surface
    # they read, and dropping an answered row loses that record there.
    # `engaged_not_settled` sorts near the top: the operator engaged and the
    # question is still open, which is exactly the state most at risk of being
    # mistaken for done.
    order = {
        NOT_SUBMITTED: 0, ENGAGED_NOT_SETTLED: 1, VERDICT_UNRECOGNISED: 2,
        UNREADABLE: 3, IN_TRANSIT: 4,
        COMMITTED: 5, ANSWERED_IN_CONVERSATION: 6,
    }
    urgency_order = {"blocking": 0, "routine": 1}
    requests.sort(
        key=lambda r: (
            # ⚠️ MOOT SORTS LAST, AND IS NOT DROPPED. MI-258 is explicit that
            # a question which was asked and became moot is a RECORD, and that
            # the reason it became moot is the useful part — so it leaves the
            # top of the operator's list without leaving the page.
            0 if r["subjectState"] != SUBJECT_GONE else 1,
            order.get(r["answerState"], 9),
            urgency_order.get(r.get("urgency"), 9),
            str(r.get("askedOn") or ""),
            r["id"],
        )
    )

    stale_open = sum(
        1 for r in requests if r["answerState"] == IN_TRANSIT and r["transit"].get("stale")
    )

    # ── the PUSH-BACK reading ────────────────────────────────────────────────
    # `committed` says the operator's answer reached the repo. It says NOTHING
    # about whether the session that ASKED ever learned it — which, until
    # 2026-09-02, nothing anywhere did (measured: zero requests in the store
    # named their asker). These counts are that missing half.
    #
    # ⚠️ Read `committedWithNoAsker` beside `delivered`. A push-back rate over a
    # population that mostly HAS no address is not a delivery rate; it is a
    # recording-coverage problem wearing a delivery figure's label.
    by_asked_by = {s: 0 for s in ASKED_BY_STATES}
    for req in requests:
        state_key = req.get("askedByState")
        if state_key in by_asked_by:
            by_asked_by[state_key] += 1

    committed = [r for r in requests if r["answerState"] == COMMITTED]
    push_states = [(r.get("push") or {}).get("state") for r in committed]
    push_back = {
        "delivered": sum(1 for s in push_states if s == "pushed"),
        # A REAL state, not an error: the asking session could not receive it.
        # The answer stays discoverable on the pull path, which is untouched.
        "sessionGone": sum(1 for s in push_states if s == "session_gone"),
        # Committed, has a reachable asker, and no settled push outcome yet —
        # either not attempted or retried after an `unknown`.
        "notYetDelivered": sum(
            1 for r in committed
            if not (r.get("push") or {}).get("state")
            and r.get("askedByState") == ASKED_BY_RECORDED
        ),
        # ⚠️ The denominator that matters: a committed answer with nobody to
        # push it to. Not a failure of delivery — a failure to record an asker.
        "committedWithNoAsker": sum(
            1 for r in committed if r.get("askedByState") != ASKED_BY_RECORDED
        ),
    }

    return {
        "present": index.get("present", False),
        # Explicitly None on the healthy envelope, matching `/api/bot/work`.
        # A key that VANISHES makes a consumer branch on absence, and absence is
        # not one of the states — the SPA's api-contract checker caught exactly
        # this when the field existed only on the degraded shape.
        "reason": None,
        "requests": requests,
        "unanswerableOperatorEdges": unanswerable,
        "summary": {
            # ⚠️ `awaitingOperator` counts what the operator can ACT on — a
            # question with no answer submitted, plus one we could not grade.
            # An `in_transit` question is also unanswered, but it is waiting on
            # a COMMITTER, not on the operator, and pooling them would put work
            # on the operator's plate that is not theirs.
            # ⚠️ `engaged_not_settled` and `verdict_unrecognised` COUNT AS
            # AWAITING. The first is the operator having spoken without
            # settling; the second is a verdict neither vocabulary recognises,
            # so we could not grade it. Neither is a decision, and folding
            # either into `decided` would report a question as closed that
            # nobody closed.
            # ⚠️ MI-258: MOOT ROWS ARE EXCLUDED, and `mootUnanswered` below
            # says how many — read the two together. Before this, a question
            # about a row deleted on 2026-09-09 sat here as work waiting on the
            # operator for more than a day.
            "awaitingOperator": (by_state[NOT_SUBMITTED] + by_state[UNREADABLE]
                                 + by_state[ENGAGED_NOT_SETTLED]
                                 + by_state[VERDICT_UNRECOGNISED]
                                 - moot_unanswered),
            # The withdrawal, stated. Never folded into `decided`: nobody
            # decided these, they stopped being questions.
            "mootUnanswered": moot_unanswered,
            # ⚠️ Read `subject_undeclared` as the DENOMINATOR, not as a
            # failure. Measured 25 of 26 on 2026-09-11: the resolver was never
            # asked to resolve those, so pooling them with `subject_unknown`
            # would report a 96% resolution failure for work never attempted.
            "bySubjectState": by_subject,
            # The ONE owner of "is this work?" — see `is_actionable`.
            "actionableCount": sum(1 for r in requests if r["actionable"]),
            "awaitingCommit": by_state[IN_TRANSIT],
            # Settled through EITHER channel. Read from SETTLED_STATES rather
            # than re-derived, so this can never drift from the one owner.
            "decided": sum(by_state[k] for k in SETTLED_STATES),
            # The channel split, because "answered" and "answered through the
            # route" are different facts and only the second round-trips.
            "decidedViaRoute": by_state[COMMITTED],
            "decidedInConversation": by_state[ANSWERED_IN_CONVERSATION],
            "byAnswerState": by_state,
            "requestCount": len(requests),
            # Reported, never swallowed: a question the operator can SEE and
            # cannot ANSWER is worse than one that was never asked.
            "malformedRequestsDropped": malformed,
            "unanswerableOperatorEdgeCount": len(unanswerable),
            "staleOpenWindows": stale_open,
            "staleAfterSeconds": STALE_TRANSIT_SECONDS,
            # Who asked, graded over EVERY request — the coverage denominator
            # for the push-back block below. `unrecorded` is the ordinary state
            # of every request written before the field existed; `malformed` is
            # a finding (an asker was recorded and cannot be reached).
            "byAskedByState": by_asked_by,
            "pushBack": push_back,
        },
        "transit": {
            # ⚠️ `absent` and `unreadable` are NOT the same. An append-only log
            # that has never been written genuinely holds nothing; one we could
            # not open is *we did not look*, and every request it would have
            # covered grades `unreadable` rather than `not_submitted`.
            "state": transit_state,
            "error": transit_error,
            "path": str(transit_log_path()),
            "rowsRead": len(rows),
        },
        "writeGate": _write_gate_reading(),
        "note": (
            "The repo is the source of truth. This route reports `committed` "
            "ONLY from an `answer` block on the work object in the repo — never "
            "from the transit log — so an answer that does not commit leaves "
            "its question UNANSWERED. Transit fails BACK, never forward."
        ),
    }


def _write_gate_reading() -> dict[str, Any]:
    """Whether the write route can accept anything right now, and why.

    Published on the READ route deliberately: the SPA must be able to tell the
    operator *"answering is closed because the server holds no write token"*
    rather than rendering a submit button that will 503. Three states, never
    collapsed — `open` (a token is configured) · `closed_no_token` (fail-closed,
    the deliberate posture, NOT an outage) · `unknown` (we could not read the
    environment at all).

    ⚠️ The token's VALUE is never echoed, only whether one is set.
    """
    try:
        configured = bool(os.environ.get("DASHBOARD_API_TOKEN", "").strip())
    except Exception:  # noqa: BLE001  # allow-silent: an environment read that raises is `unknown`, which is reported to the caller, not swallowed — the whole point of the third state.
        return {"state": "unknown", "acceptsWrites": None,
                "note": "could not read the server environment"}
    if configured:
        return {"state": "open", "acceptsWrites": True,
                "note": "DASHBOARD_API_TOKEN is set; the write route accepts a bearer."}
    return {
        "state": "closed_no_token",
        "acceptsWrites": False,
        "note": (
            "DASHBOARD_API_TOKEN is UNSET, so POST /api/bot/work/decision "
            "refuses with 503. This is FAIL-CLOSED and deliberate (the "
            "prop.py polarity), not an outage: a dropped .env value closes "
            "writes rather than reopening an anonymous write hole."
        ),
    }


@router.get("/decisions")
def get_work_decisions() -> dict[str, Any]:
    """Decisions waiting on the operator, plus every open transit window."""
    try:
        return _decision_inbox()
    except Exception as exc:  # noqa: BLE001  # allow-silent: not silent — logged WITH a stack and surfaced as present:false + reason. A Tier-1 read surface must not 5xx (roadmap.py's contract), and a decision inbox that 500s is invisible rather than empty.
        logger.warning("work: decision inbox failed: %s", exc, exc_info=True)
        return {
            "present": False,
            "reason": f"decision inbox failed: {exc}",
            "requests": [],
            "unanswerableOperatorEdges": [],
            "summary": {
                "awaitingOperator": 0,
                "awaitingCommit": 0,
                "decided": 0,
                "byAnswerState": {state: 0 for state in ANSWER_STATES},
                "requestCount": 0,
                "malformedRequestsDropped": 0,
                "unanswerableOperatorEdgeCount": 0,
                "staleOpenWindows": 0,
                "staleAfterSeconds": STALE_TRANSIT_SECONDS,
            },
            # ⚠️ `unreadable`, never `absent`. We did not establish that the
            # channel is empty — we failed before reaching it.
            "transit": {"state": TRANSIT_UNREADABLE, "error": str(exc),
                        "path": None, "rowsRead": 0},
            "writeGate": {"state": "unknown", "acceptsWrites": None,
                          "note": "inbox build failed before the gate was read"},
        }


def _require_decision_write_token(authorization: str | None) -> None:
    """Fail-closed bearer gate. Copied POLARITY from ``prop.py``, deliberately.

    Two operator-write routes already exist on this API and they take OPPOSITE
    postures. ``POST /api/bot/learning/progress`` is unauthenticated because it
    carries no secret and has no trading impact. ``POST /api/bot/prop/report``
    is token-gated and **fail-closed — 503 when ``DASHBOARD_API_TOKEN`` is
    unset** rather than accepting an anonymous write, so a dropped ``.env``
    value can never reopen a write hole (BL-20260705-DASHBOARD-API-TOKEN-UNSET).

    **A decision write is the second kind.** It records what the operator
    decided; an anonymous one would let anyone on the internet answer a question
    in the operator's name on a public, browser-reachable host. So: 503 when the
    token is unset, 401 on missing / wrong-scheme / wrong bearer.
    """
    expected = os.environ.get("DASHBOARD_API_TOKEN", "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="decision endpoint is not configured to accept writes",
        )
    if not authorization:
        raise HTTPException(status_code=401, detail="missing authorization")
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="bearer scheme required")
    if not hmac.compare_digest(authorization[7:].strip(), expected):
        raise HTTPException(status_code=401, detail="bad token")


def _find_request(object_id: str, request_id: str) -> tuple[dict[str, Any] | None, str]:
    """Locate one declared request. Returns ``(request, reason)``."""
    if not _OBJECT_ID_RE.match(object_id) or ".." in object_id:
        return None, "invalid object id"
    found = get_work_object(object_id)
    if not found.get("present"):
        # ⚠️ Two different failures, kept apart: the file exists and could not be
        # parsed (`error` set), versus no such object. Accepting a submission
        # against an object we could not read would strand the answer.
        if found.get("error"):
            return None, f"object could not be read: {found['error']}"
        return None, "no such work object"
    extra = (found.get("object") or {}).get("extra") or {}
    for req in normalise_requests({"decision_requests": extra.get("decision_requests")}, object_id):
        if req["id"] == request_id:
            return req, ""
    return None, "no such decision request on that object"


@router.post("/decision")
async def post_work_decision(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Submit an answer to one decision request. **Tier 2** (operator write).

    Body: ``{object_id, request_id, chosen?, free_text?}``.

    ⚠️ **This does not decide anything.** It appends ONE submission to the live
    layer's transit log. The question stays ``unanswered`` until a committer
    writes the answer into the work object in the repo — which is the only place
    a decision is truth. The response says so in ``answerState``: it comes back
    ``in_transit``, never ``committed``.
    """
    _require_decision_write_token(authorization)
    try:
        body = await request.json()
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="invalid JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    object_id = body.get("object_id")
    request_id = body.get("request_id")
    if not isinstance(object_id, str) or not isinstance(request_id, str):
        raise HTTPException(status_code=400, detail="object_id and request_id are required")

    req, reason = _find_request(object_id.strip(), request_id.strip())
    if req is None:
        raise HTTPException(status_code=404 if "no such" in reason else 400, detail=reason)

    # Already truth in the repo. Refuse rather than accept-and-supersede: a
    # committed decision is changed by editing the repo, and silently queueing a
    # second answer behind one that already landed would leave the operator
    # unsure which of the two the system holds.
    if req.get("answer"):
        raise HTTPException(
            status_code=409,
            detail="this decision is already recorded in the repo; change it there",
        )

    chosen = body.get("chosen")
    free_text = body.get("free_text")
    if chosen is not None and not isinstance(chosen, str):
        raise HTTPException(status_code=400, detail="chosen must be a string")
    if free_text is not None and not isinstance(free_text, str):
        raise HTTPException(status_code=400, detail="free_text must be a string")
    chosen = chosen.strip() if isinstance(chosen, str) and chosen.strip() else None
    free_text = free_text.strip() if isinstance(free_text, str) and free_text.strip() else None

    option_keys = {o["key"] for o in req["options"]}
    if chosen is not None and option_keys and chosen not in option_keys:
        # A submission naming an option the author never wrote is not an answer
        # to THIS question.
        raise HTTPException(
            status_code=400,
            detail=f"chosen must be one of {sorted(option_keys)}",
        )
    if free_text is not None and not req["allowsFreeText"]:
        raise HTTPException(
            status_code=400, detail="this request does not accept free text"
        )
    if free_text is not None and len(free_text) > MAX_FREE_TEXT_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"free_text exceeds {MAX_FREE_TEXT_CHARS} characters",
        )
    if chosen is None and free_text is None:
        # Refusing the empty submission is the same rule `board-post.yml`
        # applies to a blank comment: a vacuous answer that reads as compliance
        # is worse than no answer.
        raise HTTPException(status_code=400, detail="an answer needs `chosen` or `free_text`")

    submitted_by = body.get("submitted_by")
    try:
        row = await asyncio.to_thread(
            append_submission,
            object_id=object_id.strip(),
            request_id=request_id.strip(),
            chosen=chosen,
            free_text=free_text,
            submitted_by=submitted_by if isinstance(submitted_by, str) else None,
        )
    except OSError as exc:
        # NOT swallowed into a 200. A submission the operator believes landed
        # and did not is the forward failure this contract exists to refuse.
        logger.error("work: decision submission failed to persist: %s", exc)
        raise HTTPException(
            status_code=503, detail=f"submission did not persist: {exc}"
        ) from exc

    return {
        "accepted": True,
        "submissionId": row["submission_id"],
        "objectId": object_id.strip(),
        "requestId": request_id.strip(),
        "submittedAt": row["submitted_at"],
        # Deliberately NOT `committed`. The answer is in transit; the question
        # is still unanswered until it reaches the repo.
        "answerState": IN_TRANSIT,
        "note": (
            "Submitted, NOT decided. This answer is truth in transit: it becomes "
            "the decision only when a committer writes it into "
            f"docs/claude/work/objects/{object_id.strip()}.yaml. Until then the "
            "question reads UNANSWERED — transit fails back, never forward."
        ),
    }
