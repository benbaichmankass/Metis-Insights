"""Filesystem model registry (WS4).

One JSON file per model id under the registry root. Status is the
backing state machine; `_ALLOWED_TRANSITIONS` enforces the legal
edges. Promotion gates (the actual content of `reason`) live
in `ml.promotion.checklist`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

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

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise RegistryError(
                f"status must be one of {VALID_STATUSES}; got {self.status!r}"
            )
        if not isinstance(self.model_id, str) or not self.model_id.strip():
            raise RegistryError("model_id must be a non-empty string")

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
        )
        self._write(updated)
        return updated

    def _write(self, entry: RegistryEntry) -> None:
        path = self._entry_path(entry.model_id)
        path.write_text(
            json.dumps(entry.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
