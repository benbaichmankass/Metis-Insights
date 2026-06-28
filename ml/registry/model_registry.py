"""Filesystem model registry (WS4 + WS7-PART-1).

One JSON file per model id under the registry root. Status is the
legacy WS4 backing state machine (5 states); `_ALLOWED_TRANSITIONS`
enforces the legal edges. WS7 introduces a second, orthogonal
`target_deployment_stage` field (3 canonical stages from
`ml.manifest`: candidate → shadow → advisory) representing where in
the deployment pipeline the model currently sits; `_STAGE_TRANSITIONS`
enforces those edges and `stage_history` records the events. Legacy
7-stage names are normalized to canonical via `canonical_stage` on
read/register/promote so historical rows keep resolving.

Promotion gates (the actual content of `reason`) live in
`ml.promotion.checklist`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..manifest import canonical_stage

VALID_STATUSES: tuple[str, ...] = (
    "candidate",
    "champion",
    "paper",
    "advisory",
    "live-approved",
)

# Allowed forward + rollback edges. `champion` is the "current
# incumbent" pointer; `live-approved` is the deployed-this-tier set.
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "candidate": frozenset({"paper", "champion"}),
    "paper": frozenset({"advisory", "candidate"}),
    "advisory": frozenset({"live-approved", "paper"}),
    "live-approved": frozenset({"champion", "advisory"}),
    "champion": frozenset({"candidate"}),
}

# Deployment-stage transitions over the 3 canonical stages (3-stage
# collapse, 2026-06-16). The ladder is `candidate → shadow → advisory`
# with a one-step rollback for each forward edge:
#   - candidate (pre-shadow; refused by the shadow factory) ↔ shadow
#   - shadow (observe-only) ↔ advisory (influences the order)
# Legacy alias stages (research_only / backtest_approved / limited_live /
# live_approved) never appear here as keys — incoming and stored stages are
# normalized to canonical via `canonical_stage` before any transition check.
_STAGE_TRANSITIONS: dict[str, frozenset[str]] = {
    "candidate": frozenset({"shadow"}),
    "shadow": frozenset({"advisory", "candidate"}),
    "advisory": frozenset({"shadow"}),
}

# Shadow is the default stage for a freshly-registered model
# (2026-05-19). A manifest that omits `target_deployment_stage`
# lands the model in shadow, where the live VM's shadow predictor
# factory can pick it up once an operator wires the model_id into
# a strategy's `shadow_model_ids`. Pre-shadow stages
# (`research_only` / `candidate` / `backtest_approved`) remain
# valid but are only reached by explicit operator demotion.
_DEFAULT_STAGE = "shadow"


class RegistryError(ValueError):
    """Registry-level failure (unknown id, illegal transition, etc.)."""


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class StatusEvent:
    from_status: str | None
    to_status: str
    by: str
    reason: str
    at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_status": self.from_status,
            "to_status": self.to_status,
            "by": self.by,
            "reason": self.reason,
            "at": self.at.isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "StatusEvent":
        return cls(
            from_status=payload.get("from_status"),
            to_status=payload["to_status"],
            by=payload["by"],
            reason=payload.get("reason", ""),
            at=datetime.fromisoformat(payload["at"]),
        )


@dataclass(frozen=True)
class StageEvent:
    """WS7 deployment-stage transition event.

    Parallel structure to `StatusEvent` but on the orthogonal
    `target_deployment_stage` axis. Kept as its own type so the two
    histories stay easy to read in audit dumps.
    """

    from_stage: str | None
    to_stage: str
    by: str
    reason: str
    at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_stage": self.from_stage,
            "to_stage": self.to_stage,
            "by": self.by,
            "reason": self.reason,
            "at": self.at.isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "StageEvent":
        return cls(
            from_stage=payload.get("from_stage"),
            to_stage=payload["to_stage"],
            by=payload["by"],
            reason=payload.get("reason", ""),
            at=datetime.fromisoformat(payload["at"]),
        )


@dataclass(frozen=True)
class RunRecord:
    """One training run of a registered model (S-AI-WS8-PART-2 follow-up).

    `register()` appends a `RunRecord` every time it's called for an existing
    `model_id`, instead of raising. This preserves the full training history
    (one record per cycle) without polluting the model_id namespace with
    timestamp suffixes — `model_id` stays stable as declared in the manifest,
    `run_id` is the unique per-run identifier produced by `run_experiment`
    (e.g. `20260514T162241Z`). The newest record's `metrics` /
    `code_revision` / `model_state_path` define the entry's "current" state.
    """

    run_id: str
    model_state_path: str
    metrics: Mapping[str, float]
    code_revision: str
    at: datetime
    by: str = "experiments-runner"

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "model_state_path": self.model_state_path,
            "metrics": dict(self.metrics),
            "code_revision": self.code_revision,
            "at": self.at.isoformat(),
            "by": self.by,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RunRecord":
        return cls(
            run_id=payload["run_id"],
            model_state_path=payload["model_state_path"],
            metrics=dict(payload.get("metrics", {})),
            code_revision=payload.get("code_revision", "unknown"),
            at=datetime.fromisoformat(payload["at"]),
            by=payload.get("by", "experiments-runner"),
        )


@dataclass(frozen=True)
class RegistryEntry:
    model_id: str
    status: str
    manifest: Mapping[str, Any]
    model_state_path: str
    metrics: Mapping[str, float]
    code_revision: str
    created_at: datetime
    history: tuple[StatusEvent, ...] = field(default_factory=tuple)
    notes: str = ""
    target_deployment_stage: str = _DEFAULT_STAGE
    stage_history: tuple[StageEvent, ...] = field(default_factory=tuple)
    # Per-run training history. Daily cadence: one record per cycle.
    # Newest record's `metrics` / `code_revision` / `model_state_path`
    # mirror the entry's top-level fields. Backward-compatible — older
    # entries written before this field existed deserialize with `runs=()`.
    runs: tuple[RunRecord, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise RegistryError(
                f"status must be one of {VALID_STATUSES}; got {self.status!r}"
            )
        if not isinstance(self.model_id, str) or not self.model_id.strip():
            raise RegistryError("model_id must be a non-empty string")
        # Normalize the CURRENT stage through the alias map so a historical
        # entry whose stored `target_deployment_stage` is an old 7-stage name
        # (e.g. `live_approved`) still loads — never fail to deserialize a
        # registry row. `stage_history` records are left as-is (audit record).
        try:
            canonical = canonical_stage(self.target_deployment_stage)
        except ValueError as exc:
            raise RegistryError(str(exc)) from exc
        if canonical != self.target_deployment_stage:
            object.__setattr__(self, "target_deployment_stage", canonical)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "status": self.status,
            "manifest": dict(self.manifest),
            "model_state_path": self.model_state_path,
            "metrics": dict(self.metrics),
            "code_revision": self.code_revision,
            "created_at": self.created_at.isoformat(),
            "history": [e.to_dict() for e in self.history],
            "notes": self.notes,
            "target_deployment_stage": self.target_deployment_stage,
            "stage_history": [e.to_dict() for e in self.stage_history],
            "runs": [r.to_dict() for r in self.runs],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RegistryEntry":
        return cls(
            model_id=payload["model_id"],
            status=payload["status"],
            manifest=dict(payload["manifest"]),
            model_state_path=payload["model_state_path"],
            metrics=dict(payload["metrics"]),
            code_revision=payload["code_revision"],
            created_at=datetime.fromisoformat(payload["created_at"]),
            history=tuple(
                StatusEvent.from_dict(e) for e in payload.get("history", [])
            ),
            notes=payload.get("notes", ""),
            target_deployment_stage=payload.get(
                "target_deployment_stage", _DEFAULT_STAGE
            ),
            stage_history=tuple(
                StageEvent.from_dict(e)
                for e in payload.get("stage_history", [])
            ),
            runs=tuple(
                RunRecord.from_dict(r) for r in payload.get("runs", [])
            ),
        )


class ModelRegistry:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _entry_path(self, model_id: str) -> Path:
        return self.root / f"{model_id}.json"

    def get(self, model_id: str) -> RegistryEntry:
        path = self._entry_path(model_id)
        if not path.is_file():
            raise RegistryError(f"no registry entry for {model_id!r}")
        return RegistryEntry.from_dict(
            json.loads(path.read_text(encoding="utf-8"))
        )

    def exists(self, model_id: str) -> bool:
        return self._entry_path(model_id).is_file()

    def list(self, status: str | None = None) -> list[RegistryEntry]:
        out: list[RegistryEntry] = []
        for path in sorted(self.root.glob("*.json")):
            entry = RegistryEntry.from_dict(
                json.loads(path.read_text(encoding="utf-8"))
            )
            if status is None or entry.status == status:
                out.append(entry)
        return out

    def register(
        self,
        *,
        model_id: str,
        manifest: Mapping[str, Any],
        model_state_path: str,
        metrics: Mapping[str, float],
        code_revision: str,
        run_id: str | None = None,
        notes: str = "",
        by: str = "experiments-runner",
    ) -> RegistryEntry:
        """Register a training run for ``model_id``.

        Append-on-duplicate semantics: re-registering the same ``model_id``
        appends a new :class:`RunRecord` to the existing entry's ``runs``
        list (preserving full training history) and refreshes the entry's
        top-level ``metrics`` / ``model_state_path`` / ``code_revision`` to
        reflect the latest run. Status, stage, and historical events are
        preserved as-is. Daily-cadence re-trains no longer raise.

        ``run_id`` is the unique per-run identifier (e.g.
        ``20260514T162241Z``) produced by
        :func:`ml.experiments.runner.run_experiment`. When omitted it's
        derived from ``_now_utc()`` so callers that haven't been updated
        yet still work, but the runner always supplies it explicitly.
        """
        raw_stage = manifest.get("target_deployment_stage", _DEFAULT_STAGE)
        # Normalize through the alias map (accept an old name, store canonical).
        try:
            stage = canonical_stage(raw_stage)
        except ValueError as exc:
            raise RegistryError(
                f"manifest.target_deployment_stage invalid: {exc}"
            ) from exc
        now = _now_utc()
        if not run_id:
            run_id = now.strftime("%Y%m%dT%H%M%SZ")
        new_run = RunRecord(
            run_id=run_id,
            model_state_path=model_state_path,
            metrics=metrics,
            code_revision=code_revision,
            at=now,
            by=by,
        )

        if self.exists(model_id):
            existing = self.get(model_id)
            # Idempotency: if this exact run_id is already recorded,
            # don't duplicate — return the existing entry untouched.
            if any(r.run_id == run_id for r in existing.runs):
                return existing
            event = StatusEvent(
                from_status=existing.status,
                to_status=existing.status,
                by=by,
                reason=f"re-trained (run_id={run_id})",
                at=now,
            )
            entry = RegistryEntry(
                model_id=existing.model_id,
                status=existing.status,
                manifest=manifest,
                model_state_path=model_state_path,
                metrics=metrics,
                code_revision=code_revision,
                created_at=existing.created_at,
                history=existing.history + (event,),
                notes=notes or existing.notes,
                target_deployment_stage=existing.target_deployment_stage,
                stage_history=existing.stage_history,
                runs=existing.runs + (new_run,),
            )
            self._write(entry)
            return entry

        event = StatusEvent(
            from_status=None,
            to_status="candidate",
            by=by,
            reason="initial registration",
            at=now,
        )
        entry = RegistryEntry(
            model_id=model_id,
            status="candidate",
            manifest=manifest,
            model_state_path=model_state_path,
            metrics=metrics,
            code_revision=code_revision,
            created_at=now,
            history=(event,),
            notes=notes,
            target_deployment_stage=stage,
            stage_history=(),
            runs=(new_run,),
        )
        self._write(entry)
        return entry

    def promote(
        self,
        model_id: str,
        new_status: str,
        *,
        by: str,
        reason: str,
    ) -> RegistryEntry:
        if new_status not in VALID_STATUSES:
            raise RegistryError(
                f"new_status must be one of {VALID_STATUSES}; got {new_status!r}"
            )
        current = self.get(model_id)
        if new_status != current.status:
            allowed = _ALLOWED_TRANSITIONS.get(current.status, frozenset())
            if new_status not in allowed:
                raise RegistryError(
                    f"transition {current.status!r} -> {new_status!r} not allowed; "
                    f"allowed: {sorted(allowed)}"
                )
        event = StatusEvent(
            from_status=current.status,
            to_status=new_status,
            by=by,
            reason=reason,
            at=_now_utc(),
        )
        updated = RegistryEntry(
            model_id=current.model_id,
            status=new_status,
            manifest=current.manifest,
            model_state_path=current.model_state_path,
            metrics=current.metrics,
            code_revision=current.code_revision,
            created_at=current.created_at,
            history=current.history + (event,),
            notes=current.notes,
            target_deployment_stage=current.target_deployment_stage,
            stage_history=current.stage_history,
            # Carry the training-run history forward. RegistryEntry.runs
            # defaults to () (field(default_factory=tuple)), so omitting it
            # here silently WIPED every model's run history on a status
            # transition — the cross_run_stability promotion gate
            # (promotion/gates.py) reads entry.runs, so a promoted-then-
            # re-evaluated model lost its stability evidence (S-AUDIT-G B1).
            runs=current.runs,
        )
        self._write(updated)
        return updated

    def promote_stage(
        self,
        model_id: str,
        new_stage: str,
        *,
        by: str,
        reason: str,
    ) -> RegistryEntry:
        """WS7 deployment-stage promotion.

        Walks the model along the canonical
        `candidate → shadow → advisory` ladder defined in
        `ml.manifest.VALID_DEPLOYMENT_STAGES`, enforcing the
        `_STAGE_TRANSITIONS` edges (forward + one-step rollback). The
        requested stage is normalized through the alias map first, so an
        old 7-stage name is accepted and stored canonical. Recorded as a
        `StageEvent` on `stage_history`.

        Refuses no-op transitions explicitly so audit-log entries
        always represent real state changes.
        """
        # Normalize the requested stage through the alias map: a caller may
        # still pass an old name (e.g. `live_approved`); store the canonical.
        try:
            new_stage = canonical_stage(new_stage)
        except ValueError as exc:
            raise RegistryError(str(exc)) from exc
        if not isinstance(by, str) or not by.strip():
            raise RegistryError("by must be a non-empty string")
        if not isinstance(reason, str) or not reason.strip():
            raise RegistryError("reason must be a non-empty string")
        current = self.get(model_id)
        if new_stage == current.target_deployment_stage:
            raise RegistryError(
                f"stage already {new_stage!r}; refusing no-op transition"
            )
        allowed = _STAGE_TRANSITIONS.get(
            current.target_deployment_stage, frozenset()
        )
        if new_stage not in allowed:
            raise RegistryError(
                f"stage transition {current.target_deployment_stage!r} -> "
                f"{new_stage!r} not allowed; allowed: {sorted(allowed)}"
            )
        event = StageEvent(
            from_stage=current.target_deployment_stage,
            to_stage=new_stage,
            by=by,
            reason=reason,
            at=_now_utc(),
        )
        updated = RegistryEntry(
            model_id=current.model_id,
            status=current.status,
            manifest=current.manifest,
            model_state_path=current.model_state_path,
            metrics=current.metrics,
            code_revision=current.code_revision,
            created_at=current.created_at,
            history=current.history,
            notes=current.notes,
            target_deployment_stage=new_stage,
            stage_history=current.stage_history + (event,),
            # Carry the training-run history forward (see set_status above) —
            # a stage promotion must not wipe entry.runs (S-AUDIT-G B1).
            runs=current.runs,
        )
        self._write(updated)
        return updated

    def _write(self, entry: RegistryEntry) -> None:
        path = self._entry_path(entry.model_id)
        path.write_text(
            json.dumps(entry.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
