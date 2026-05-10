"""Dataset validator (WS3).

Loads a dataset directory produced by
`ml.datasets.builder.DatasetBuilder.build` and confirms that the
artifact is internally consistent: metadata parses, row count
matches, every JSONL row conforms to the declared schema.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .metadata import DatasetMetadata


@dataclass
class ValidationReport:
    dataset_path: Path
    metadata: DatasetMetadata | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_path": str(self.dataset_path),
            "metadata": self.metadata.to_dict() if self.metadata else None,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "ok": self.ok,
        }


_TYPE_CHECK = {
    "int": (int,),
    "float": (int, float),  # JSON has no int/float distinction; accept either
    "str": (str,),
    "bool": (bool,),
}


def _check_row_field(value: Any, type_token: str) -> str | None:
    if value is None:
        return None
    expected = _TYPE_CHECK.get(type_token)
    if expected is None:
        return None  # unknown type token; skip strict check (e.g. custom)
    if isinstance(value, expected):
        return None
    return f"expected {type_token}; got {type(value).__name__}"


def validate_dataset(dataset_path: Path) -> ValidationReport:
    """Validate a dataset directory.

    A dataset directory has `metadata.json` + `data.jsonl`. Returns
    a `ValidationReport` with `errors` populated when the artifact
    is inconsistent.
    """
    report = ValidationReport(dataset_path=dataset_path)
    if not dataset_path.is_dir():
        report.errors.append(f"not a directory: {dataset_path}")
        return report

    meta_path = dataset_path / "metadata.json"
    data_path = dataset_path / "data.jsonl"

    if not meta_path.is_file():
        report.errors.append(f"missing metadata.json under {dataset_path}")
        return report
    if not data_path.is_file():
        report.errors.append(f"missing data.jsonl under {dataset_path}")
        return report

    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
        metadata = DatasetMetadata.from_dict(payload)
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        report.errors.append(f"metadata.json parse error: {e}")
        return report
    report.metadata = metadata

    schema: Mapping[str, str] = metadata.schema
    actual_rows = 0
    with data_path.open("r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.rstrip("\n")
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as e:
                report.errors.append(f"data.jsonl line {lineno}: invalid JSON ({e})")
                continue
            if not isinstance(row, dict):
                report.errors.append(
                    f"data.jsonl line {lineno}: row is not an object"
                )
                continue
            unknown = set(row.keys()) - set(schema.keys())
            if unknown:
                report.errors.append(
                    f"data.jsonl line {lineno}: unknown fields {sorted(unknown)}"
                )
            for field_name, type_token in schema.items():
                if field_name not in row:
                    continue
                err = _check_row_field(row[field_name], type_token)
                if err is not None:
                    report.errors.append(
                        f"data.jsonl line {lineno}: field {field_name!r} {err}"
                    )
            actual_rows += 1

    if actual_rows != metadata.row_count:
        report.errors.append(
            f"row_count mismatch: metadata={metadata.row_count}, actual={actual_rows}"
        )
    return report
