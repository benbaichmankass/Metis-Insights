"""Dataset metadata schema (WS3).

The `DatasetMetadata` block is mandatory for every dataset artifact
produced by `ml.datasets.builder.DatasetBuilder.build`. It carries
the lineage required to reproduce a training run later.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

SCHEMA_VERSION = "v1"


class LeakageStatus(str, Enum):
    PASSED = "passed"
    SKIPPED = "skipped"
    NOT_APPLICABLE = "n/a"
    FAILED = "failed"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class DatasetMetadata:
    """Mandatory metadata block for every dataset artifact.

    Every field below MUST be populated; empty strings on string
    fields raise `ValueError` at construction time. `generated_at`
    must be timezone-aware. `row_count` must be >= 0.

    Naming: see `docs/data/versioning-policy.md` § Naming for the
    canonical `family / symbol_scope / timeframe / version` rule.
    """

    family: str
    version: str                  # e.g. "v001"
    symbol_scope: str             # "all" or comma-joined symbols
    timeframe: str                # "all" or e.g. "1m", "15m", "1h"
    source: str                   # e.g. "trade_journal.db"
    timezone_name: str            # e.g. "UTC"
    generation_commit_sha: str    # git rev-parse HEAD or "unknown"
    label_version: str            # "n/a" for raw families
    leakage_test_status: LeakageStatus
    builder: str                  # python qualname of the builder class
    builder_version: str          # e.g. "v1"
    row_count: int
    schema: Mapping[str, str]     # field name -> type token
    notes: str = ""
    # Effective family build params the dataset was actually built with
    # (the scalar `iter_rows` kwargs — e.g. `vol_threshold`, `n_vol_buckets`).
    # Persisted so a version dir is SELF-DESCRIBING: the `version` string is
    # opaque (a "v004" dir may hold a 0.003 label), and the trainer training
    # path does NOT apply a manifest's `dataset.build_params` — only gpu-burst
    # does — so without this a mislabeled dir is undetectable
    # (MB-20260716-BUILDPARAMS-IGNORED). Empty for legacy dirs / raw families.
    build_params: Mapping[str, Any] = field(default_factory=dict)
    generated_at: datetime = field(default_factory=_now_utc)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for required in (
            "family",
            "version",
            "symbol_scope",
            "timeframe",
            "source",
            "timezone_name",
            "generation_commit_sha",
            "label_version",
            "builder",
            "builder_version",
        ):
            value = getattr(self, required)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"DatasetMetadata.{required} must be a non-empty string")
        if not self.version.startswith("v") or not self.version[1:].isdigit():
            raise ValueError(
                f"version must match 'vNNN' (digits after v); got {self.version!r}"
            )
        if self.row_count < 0:
            raise ValueError(f"row_count must be >= 0; got {self.row_count}")
        if self.generated_at.tzinfo is None:
            raise ValueError("generated_at must be timezone-aware")
        if not self.schema:
            raise ValueError("schema must list at least one field")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["leakage_test_status"] = self.leakage_test_status.value
        d["generated_at"] = self.generated_at.isoformat()
        d["schema"] = dict(self.schema)
        return d

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DatasetMetadata":
        data = dict(payload)
        if "leakage_test_status" in data and not isinstance(
            data["leakage_test_status"], LeakageStatus
        ):
            data["leakage_test_status"] = LeakageStatus(data["leakage_test_status"])
        if isinstance(data.get("generated_at"), str):
            data["generated_at"] = datetime.fromisoformat(data["generated_at"])
        return cls(**data)
