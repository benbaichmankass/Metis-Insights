"""Reproducible dataset framework for the AI traders track.

Durable artifact of WS3 in `docs/AI-TRADERS-ROADMAP.md`. Establishes
the layout, metadata schema, builder API, and validation entry points
for the dataset families enumerated in
`docs/data/dataset-taxonomy.md`.

Deps: stdlib only.
"""
from __future__ import annotations

from .builder import (
    DatasetBuilder,
    DatasetPaths,
    SchemaViolation,
    VersionConflict,
)
from .metadata import DatasetMetadata, LeakageStatus
from .registry import FAMILY_REGISTRY, get_builder, list_families
from .validate import ValidationReport, validate_dataset

__all__ = [
    "DatasetBuilder",
    "DatasetMetadata",
    "DatasetPaths",
    "FAMILY_REGISTRY",
    "LeakageStatus",
    "SchemaViolation",
    "ValidationReport",
    "VersionConflict",
    "get_builder",
    "list_families",
    "validate_dataset",
]
