"""Filesystem model registry (WS4 + WS7-PART-1).

One JSON file per model id under the registry root. Status is the
legacy WS4 backing state machine (5 states); `_ALLOWED_TRANSITIONS`
enforces the legal edges. WS7 introduces a second, orthogonal
`target_deployment_stage` field (7 stages from `ml.manifest`)
representing where in the deployment pipeline the model
currently sits; `_STAGE_TRANSITIONS` enforces those edges and
`stage_history` records the events.

Promotion gates (the actual content of `reason`) live in
`ml.promotion.checklist`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..manifest import VALID_DEPLOYMENT_STAGES

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

# WS7 deployment-stage transitions. Every forward edge has a
# rollback edge so an operator can demote a model that misbehaves.
# `live_approved` has no further forward — once there, the only
# legal move is back to `advisory` for re-evaluation.
_STAGE_TRANSITIONS: dict[str, frozenset[str]] = {
    "research_only": frozenset({"candidate"}),
    "candidate": frozenset({"backtest_approved", "research_only"}),
    "backtest_approved": frozenset({"shadow", "candidate"}),
    "shadow": frozenset({"advisory", "backtest_approved"}),
    "advisory": frozenset({"limited_live", "shadow"}),
    "limited_live": frozenset({"live_approved", "advisory"}),
    "live_approved": frozenset({"advisory"}),
}

_DEFAULT_STAGE = "research_only"


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

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise RegistryError(
                f"status must be one of {VALID_STATUSES}; got {self.status!r}"
            )
        if not isinstance(self.model_id, str) or not self.model_id.strip():
            raise RegistryError("model_id must be a non-empty string")
        if self.target_deployment_stage not in VALID_DEPLOYMENT_STAGES:
            raise RegistryError(
                f"target_deployment_stage must be one of "
                f"{VALID_DEPLOYMENT_STAGES}; got "
                f"{self.target_deployment_stage!r}"
            )

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
        notes: str = "",
        by: str = "experiments-runner",
    ) -> RegistryEntry:
        if self.exists(model_id):
            raise RegistryError(f"model_id {model_id!r} already registered")
        now = _now_utc()
        event = StatusEvent(
            from_status=None,
            to_status="candidate",
            by=by,
            reason="initial registration",
            at=now,
        )
        stage = manifest.get("target_deployment_stage", _DEFAULT_STAGE)
        if stage not in VALID_DEPLOYMENT_STAGES:
            raise RegistryError(
                f"manifest.target_deployment_stage must be one of "
                f"{VALID_DEPLOYMENT_STAGES}; got {stage!r}"
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

        Walks the model along the
        `research_only → candidate → backtest_approved → shadow →
        advisory → limited_live → live_approved` ladder defined in
        `ml.manifest.VALID_DEPLOYMENT_STAGES`, enforcing the
        `_STAGE_TRANSITIONS` edges (forward + one-step rollback).
        Recorded as a `StageEvent` on `stage_history`.

        Refuses no-op transitions explicitly so audit-log entries
        always represent real state changes.
        """
        if new_stage not in VALID_DEPLOYMENT_STAGES:
            raise RegistryError(
                f"new_stage must be one of {VALID_DEPLOYMENT_STAGES}; "
                f"got {new_stage!r}"
            )
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
        )
        self._write(updated)
        return updated

    def _write(self, entry: RegistryEntry) -> None:
        path = self._entry_path(entry.model_id)
        path.write_text(
            json.dumps(entry.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
