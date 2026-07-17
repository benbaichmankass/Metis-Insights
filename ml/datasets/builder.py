"""Builder framework for dataset families (WS3).

A `DatasetBuilder` produces:
  - `data.jsonl`     — one JSON object per row
  - `metadata.json`  — the canonical `DatasetMetadata` block

under a versioned path:
  `<output_dir>/<family>/<symbol_scope>/<timeframe>/<version>/`
"""
from __future__ import annotations

import json
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Iterable, Iterator, Mapping

from .metadata import DatasetMetadata, LeakageStatus


class SchemaViolation(ValueError):
    """A produced row does not conform to the builder's declared schema."""


class VersionConflict(FileExistsError):
    """The target version directory already exists and `overwrite=False`."""


@dataclass(frozen=True)
class DatasetPaths:
    root: Path
    data: Path
    metadata: Path

    @classmethod
    def for_dataset(
        cls,
        output_dir: Path,
        family: str,
        symbol_scope: str,
        timeframe: str,
        version: str,
    ) -> "DatasetPaths":
        root = output_dir / family / symbol_scope / timeframe / version
        return cls(
            root=root,
            data=root / "data.jsonl",
            metadata=root / "metadata.json",
        )


def _resolve_commit_sha(override: str | None) -> str:
    if override:
        return override
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "unknown"


_TYPE_TOKENS: dict[type, str] = {
    int: "int",
    float: "float",
    str: "str",
    bool: "bool",
}


def _type_token(t: type) -> str:
    return _TYPE_TOKENS.get(t, t.__name__)


class DatasetBuilder(ABC):
    """Abstract base for dataset builders.

    Subclasses set the class variables and implement
    `iter_rows(**kwargs)`. The base `build(...)` method writes the
    canonical artifact layout and metadata block.
    """

    family: ClassVar[str]
    builder_version: ClassVar[str]
    schema: ClassVar[Mapping[str, type]]
    default_symbol_scope: ClassVar[str] = "all"
    default_timeframe: ClassVar[str] = "all"
    leakage_test_status: ClassVar[LeakageStatus] = LeakageStatus.NOT_APPLICABLE
    label_version: ClassVar[str] = "n/a"

    @abstractmethod
    def iter_rows(self, **kwargs: Any) -> Iterator[Mapping[str, Any]]:
        """Yield one row dict per dataset record.

        Each row's keys must be a subset of `self.schema` and the
        values must match the declared types (or be `None`).
        """

    def build(
        self,
        output_dir: Path,
        version: str,
        *,
        source: str,
        symbol_scope: str | None = None,
        timeframe: str | None = None,
        timezone_name: str = "UTC",
        commit_sha: str | None = None,
        notes: str = "",
        overwrite: bool = False,
        **iter_rows_kwargs: Any,
    ) -> DatasetPaths:
        symbol_scope = symbol_scope or self.default_symbol_scope
        timeframe = timeframe or self.default_timeframe

        paths = DatasetPaths.for_dataset(
            output_dir=output_dir,
            family=self.family,
            symbol_scope=symbol_scope,
            timeframe=timeframe,
            version=version,
        )

        if paths.root.exists() and not overwrite:
            raise VersionConflict(
                f"version directory already exists: {paths.root}"
            )
        paths.root.mkdir(parents=True, exist_ok=True)

        schema_tokens = {name: _type_token(t) for name, t in self.schema.items()}
        allowed_fields = set(self.schema.keys())
        # Forward post-resolution scope into iter_rows so builders that
        # need to stamp rows with the canonical scope (e.g. market_raw
        # adapters) don't have to be passed redundantly. setdefault, so
        # operator-supplied kwargs win.
        iter_rows_kwargs = dict(iter_rows_kwargs)
        iter_rows_kwargs.setdefault("symbol_scope", symbol_scope)
        iter_rows_kwargs.setdefault("timeframe", timeframe)
        row_count = self._write_rows(paths.data, allowed_fields, iter_rows_kwargs)

        # Persist the effective scalar build params so the dir is
        # self-describing (MB-20260716-BUILDPARAMS-IGNORED). Excludes the
        # forwarded scope (already top-level) and non-scalar kwargs (paths,
        # etc. — not label-defining and possibly host-specific).
        effective_build_params = {
            k: v
            for k, v in iter_rows_kwargs.items()
            if k not in ("symbol_scope", "timeframe")
            and (v is None or isinstance(v, (str, int, float, bool)))
        }

        metadata = DatasetMetadata(
            family=self.family,
            version=version,
            symbol_scope=symbol_scope,
            timeframe=timeframe,
            source=source,
            timezone_name=timezone_name,
            generation_commit_sha=_resolve_commit_sha(commit_sha),
            label_version=self.label_version,
            leakage_test_status=self.leakage_test_status,
            builder=type(self).__qualname__,
            builder_version=self.builder_version,
            row_count=row_count,
            schema=schema_tokens,
            notes=notes,
            build_params=effective_build_params,
        )
        paths.metadata.write_text(
            json.dumps(metadata.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return paths

    def _write_rows(
        self,
        data_path: Path,
        allowed_fields: set[str],
        iter_rows_kwargs: Mapping[str, Any],
    ) -> int:
        count = 0
        with data_path.open("w", encoding="utf-8") as fh:
            for row in self.iter_rows(**iter_rows_kwargs):
                self._validate_row(row, allowed_fields)
                fh.write(json.dumps(row, sort_keys=True))
                fh.write("\n")
                count += 1
        return count

    def _validate_row(
        self, row: Mapping[str, Any], allowed_fields: Iterable[str]
    ) -> None:
        allowed = set(allowed_fields)
        unknown = set(row.keys()) - allowed
        if unknown:
            raise SchemaViolation(
                f"row contains fields not in schema: {sorted(unknown)}"
            )
        for name, expected_type in self.schema.items():
            if name not in row:
                continue
            value = row[name]
            if value is None:
                continue
            if not isinstance(value, expected_type):
                raise SchemaViolation(
                    f"row field {name!r} expected {expected_type.__name__}; "
                    f"got {type(value).__name__}"
                )
