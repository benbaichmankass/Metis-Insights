"""Training-manifest schema (WS4).

A `TrainingManifest` ties together: model identity, the trainer +
evaluator callables (resolved by Python qualname), the dataset
reference, configs for trainer/evaluator, and the target deployment
tier. Stored as YAML on disk; loaded into a frozen dataclass with
invariant checks at construction time.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

MANIFEST_VERSION = "v1"

VALID_DEPLOYMENT_STAGES: tuple[str, ...] = (
    "research_only",
    "candidate",
    "backtest_approved",
    "shadow",
    "advisory",
    "limited_live",
    "live_approved",
)


@dataclass(frozen=True)
class DatasetRef:
    family: str
    symbol_scope: str
    timeframe: str
    version: str

    def path_under(self, root: Path) -> Path:
        return root / self.family / self.symbol_scope / self.timeframe / self.version

    def to_dict(self) -> dict[str, str]:
        return {
            "family": self.family,
            "symbol_scope": self.symbol_scope,
            "timeframe": self.timeframe,
            "version": self.version,
        }


@dataclass(frozen=True)
class TrainingManifest:
    manifest_version: str
    model_id: str
    model_family: str
    trainer: str
    trainer_config: Mapping[str, Any]
    dataset: DatasetRef
    evaluator: str
    evaluator_config: Mapping[str, Any]
    target_deployment_stage: str
    notes: str = ""

    def __post_init__(self) -> None:
        if self.manifest_version != MANIFEST_VERSION:
            raise ValueError(
                f"manifest_version must be {MANIFEST_VERSION!r}; "
                f"got {self.manifest_version!r}"
            )
        for required in ("model_id", "model_family", "trainer", "evaluator"):
            value = getattr(self, required)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{required} must be a non-empty string")
        if "." not in self.trainer:
            raise ValueError(
                f"trainer must be a fully-qualified callable; got {self.trainer!r}"
            )
        if "." not in self.evaluator:
            raise ValueError(
                f"evaluator must be a fully-qualified callable; got {self.evaluator!r}"
            )
        if self.target_deployment_stage not in VALID_DEPLOYMENT_STAGES:
            raise ValueError(
                f"target_deployment_stage must be one of {VALID_DEPLOYMENT_STAGES}; "
                f"got {self.target_deployment_stage!r}"
            )

    @classmethod
    def from_yaml(cls, path: Path) -> "TrainingManifest":
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"manifest at {path} must be a YAML mapping")
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TrainingManifest":
        data = dict(payload)
        ds = data.pop("dataset", None)
        if ds is None:
            raise ValueError("manifest missing 'dataset' block")
        if not isinstance(ds, Mapping):
            raise ValueError("'dataset' must be a mapping")
        data["dataset"] = DatasetRef(**ds)
        data.setdefault("notes", "")
        data.setdefault("trainer_config", {})
        data.setdefault("evaluator_config", {})
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_version": self.manifest_version,
            "model_id": self.model_id,
            "model_family": self.model_family,
            "trainer": self.trainer,
            "trainer_config": dict(self.trainer_config),
            "dataset": self.dataset.to_dict(),
            "evaluator": self.evaluator,
            "evaluator_config": dict(self.evaluator_config),
            "target_deployment_stage": self.target_deployment_stage,
            "notes": self.notes,
        }
