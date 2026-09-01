"""Tier-1 read endpoints exposing the work store (``docs/claude/work/``).

Backs the SPA's **Work** section — what is in flight, under which intent, and
what each object is waiting on. This is the READ half of the operating layer's
visibility phase (Phase B); the control half (answering decisions from the UI,
the read gate) is Phase H and is deliberately not here.

- ``GET /api/bot/work`` — the whole store: intents, objects, steps, a lifecycle
  roll-up, the WIP-ceiling reading, and a ``coverage`` block stating what the
  store does **not** cover.
- ``GET /api/bot/work/object/{object_id}`` — one object, in full.

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
the operating-layer build's own phases; the carried backlog rows migrate in Phase
C together with the WIP ceiling. ``coverage.complete`` is ``false`` and the
renderer is expected to show it.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException

from src.utils.paths import repo_root

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

# The declared ceiling (A5). ⚠️ It is NOT enforced yet — enforcement ships in
# Phase C together with the migration, because a ceiling without the real rows is
# meaningless and the rows without a ceiling are the condition the redesign
# exists to end. This route REPORTS the reading; it gates nothing.
_WIP_CEILING = 8
_CEILING_ENFORCED = False

# Approximate count of carried backlog rows still outside the store, from the
# build plan. Reported as an explicit approximation so the gap has a size.
_CARRIED_ROWS_APPROX = 572


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
                    "kind": item.get("kind"),
                    "ref": item.get("ref"),
                    "since": item.get("since"),
                    "note": item.get("note"),
                }
            )
        else:
            # A bare scalar edge is not the typed shape the design requires;
            # surface it rather than dropping it.
            edges.append({"kind": None, "ref": item, "since": None, "note": None})
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
    extra = {k: v for k, v in data.items() if k not in _OBJECT_KNOWN_KEYS}
    return {
        "id": data.get("id") or path.stem,
        "type": data.get("type"),
        "parentIntent": data.get("parent_intent"),
        "title": data.get("title"),
        "stage": data.get("stage"),
        "lifecycle": lifecycle,
        "lifecycleDeclared": data.get("lifecycle"),
        "owner": data.get("owner"),
        "openedAt": data.get("opened_at"),
        "closedAt": data.get("closed_at"),
        "reviewTrigger": data.get("review_trigger"),
        "doneCondition": data.get("done_condition"),
        "blockedOn": edges,
        "blockedOnState": blocked_state,
        "note": data.get("note"),
        "evidence": data.get("evidence") or [],
        "verdict": data.get("verdict"),
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
        "openedAt": data.get("opened_at"),
        "reviewCadence": data.get("review_cadence"),
        "why": data.get("why"),
        "doneLooksLike": data.get("done_looks_like"),
        "extra": {k: v for k, v in data.items() if k not in known},
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
        "openedAt": data.get("opened_at"),
        "note": data.get("note"),
        "extra": {k: v for k, v in data.items() if k not in known},
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

    ⚠️ ``enforced`` is FALSE and ``state`` says so. A ceiling that is merely
    DECLARED and one that is ENFORCED are different facts, and rendering a count
    under a ceiling nothing checks as "within limits" would assert an enforcement
    that does not exist. Enforcement ships in Phase C with the migration.
    """
    return {
        "ceiling": _WIP_CEILING,
        "inFlight": in_flight,
        "enforced": _CEILING_ENFORCED,
        "state": "declared_not_enforced",
        "note": (
            "The ceiling of 8 is DECLARED but not enforced. Enforcement ships in "
            "Phase C together with the backlog migration, because a ceiling with no "
            "real rows behind it is meaningless and the rows without a ceiling are "
            "the condition the redesign exists to end. This count is a reading, "
            "not a gate."
        ),
    }


def _coverage_block() -> dict[str, Any]:
    """What this store does NOT cover — served on every response, deliberately.

    The view renders a store that is knowingly partial. Leaving the consumer to
    infer that is how a partial picture gets read as a complete one.
    """
    return {
        "complete": False,
        "scope": "operating-layer-build",
        "carriedRowsApprox": _CARRIED_ROWS_APPROX,
        "carriedRowsMigrateIn": "Phase C",
        "note": (
            "This store holds the operating-layer build's own phases and nothing "
            "else. The carried backlog rows (~572 not-closed) migrate in Phase C, "
            "together with the WIP ceiling. A bug to fix still goes to the review "
            "backlogs; what a session must KNOW before it plans is still "
            "OPEN-ITEMS.json. Do not read this as the whole of the system's work."
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
