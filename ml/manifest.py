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

# Canonical deployment stages (3-stage collapse, 2026-06-16, operator-approved).
# Of the old 7 stages, three runtime states actually differed:
#   - "pre-shadow" (refused by the shadow factory): research_only / candidate /
#     backtest_approved → all collapse to ``candidate``.
#   - "observe-only" (logs predictions, never influences orders): ``shadow``.
#   - "influence" (changes the order via apply_advisory_downsize, identically):
#     advisory / limited_live / live_approved → all collapse to ``advisory``.
VALID_DEPLOYMENT_STAGES: tuple[str, ...] = (
    "candidate",
    "shadow",
    "advisory",
)

# Backward-compatibility alias map: old stage name → canonical stage. Kept
# **permanently** so historical registry entries / manifests using the old
# 7-stage names keep resolving (never crash, never strand a model). The
# canonical stages map to themselves.
STAGE_ALIASES: dict[str, str] = {
    "research_only": "candidate",
    "backtest_approved": "candidate",
    "limited_live": "advisory",
    "live_approved": "advisory",
}


def canonical_stage(stage: str) -> str:
    """Normalize a canonical-or-alias deployment-stage string to canonical.

    Accepts any of the 3 canonical stages (returned unchanged) or any of the
    legacy alias names (mapped via ``STAGE_ALIASES``). Raises ``ValueError``
    on anything else — an unknown stage is a real error, not silently coerced.
    """
    if stage in VALID_DEPLOYMENT_STAGES:
        return stage
    if stage in STAGE_ALIASES:
        return STAGE_ALIASES[stage]
    raise ValueError(
        f"deployment stage must be one of {VALID_DEPLOYMENT_STAGES} "
        f"(or a legacy alias {tuple(STAGE_ALIASES)}); got {stage!r}"
    )


@dataclass(frozen=True)
class DatasetRef:
    family: str
    symbol_scope: str
    timeframe: str
    version: str
    # Optional dataset-BUILD parameterization (e.g. a vol_threshold arm of a
    # label-sensitivity A/B). Consumed only by offline dataset builders — the
    # GPU-burst driver threads it into the on-pod market_features build
    # (scripts/ml/gpu_burst/runpod_burst.py::_manifest_dataset_scope). Trainers,
    # evaluators, and path resolution ignore it entirely; None for the normal
    # cycle-built datasets.
    build_params: Mapping[str, Any] | None = None

    def path_under(self, root: Path) -> Path:
        return root / self.family / self.symbol_scope / self.timeframe / self.version

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "family": self.family,
            "symbol_scope": self.symbol_scope,
            "timeframe": self.timeframe,
            "version": self.version,
        }
        if self.build_params is not None:
            out["build_params"] = dict(self.build_params)
        return out


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
    # Human-readable summary of what this model does / how it is used.
    # Surfaced on the dashboard Models page via /api/bot/ml/registry. Kept
    # distinct from `notes` (operational caveats): `description` is the
    # "about this model" prose. Authoring it is a step in the
    # `model-training` / `new-strategy` skills.
    description: str = ""

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
        # Normalize via the alias map (accept an old name, store the canonical
        # one). `canonical_stage` raises ValueError on anything unrecognized,
        # preserving the "invalid stage rejected" contract.
        canonical = canonical_stage(self.target_deployment_stage)
        if canonical != self.target_deployment_stage:
            # frozen dataclass: rewrite the field to the canonical value.
            object.__setattr__(self, "target_deployment_stage", canonical)

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
        data.setdefault("description", "")
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
            "description": self.description,
        }
