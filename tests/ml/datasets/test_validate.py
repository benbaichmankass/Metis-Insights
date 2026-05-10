"""Tests for `ml.datasets.validate.validate_dataset`."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ml.datasets.metadata import DatasetMetadata, LeakageStatus
from ml.datasets.validate import validate_dataset


def _write_dataset(tmp_path: Path, *, rows, row_count_override=None) -> Path:
    ds = tmp_path / "backtest_results" / "all" / "all" / "v001"
    ds.mkdir(parents=True)
    metadata = DatasetMetadata(
        family="backtest_results",
        version="v001",
        symbol_scope="all",
        timeframe="all",
        source="synthetic",
        timezone_name="UTC",
        generation_commit_sha="abc123",
        label_version="n/a",
        leakage_test_status=LeakageStatus.NOT_APPLICABLE,
        builder="BacktestResultsBuilder",
        builder_version="v1",
        row_count=row_count_override if row_count_override is not None else len(rows),
        schema={"id": "int", "win_rate": "float", "strategy_version": "str"},
    )
    (ds / "metadata.json").write_text(
        json.dumps(metadata.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with (ds / "data.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    return ds


class TestHappyPath:
    def test_validates_consistent_dataset(self, tmp_path: Path):
        ds = _write_dataset(
            tmp_path,
            rows=[
                {"id": 1, "win_rate": 0.6, "strategy_version": "vwap-v2"},
                {"id": 2, "win_rate": 0.55, "strategy_version": "vwap-v2"},
            ],
        )
        report = validate_dataset(ds)
        assert report.ok
        assert report.metadata is not None
        assert report.metadata.row_count == 2

    def test_null_field_is_ok(self, tmp_path: Path):
        ds = _write_dataset(
            tmp_path,
            rows=[
                {"id": 1, "win_rate": None, "strategy_version": "vwap-v2"},
            ],
        )
        report = validate_dataset(ds)
        assert report.ok


class TestErrors:
    def test_missing_metadata(self, tmp_path: Path):
        ds = tmp_path / "empty"
        ds.mkdir()
        report = validate_dataset(ds)
        assert not report.ok
        assert any("metadata.json" in e for e in report.errors)

    def test_missing_data(self, tmp_path: Path):
        ds = _write_dataset(tmp_path, rows=[{"id": 1, "win_rate": 0.5,
                                              "strategy_version": "v"}])
        (ds / "data.jsonl").unlink()
        report = validate_dataset(ds)
        assert not report.ok
        assert any("data.jsonl" in e for e in report.errors)

    def test_row_count_mismatch(self, tmp_path: Path):
        ds = _write_dataset(
            tmp_path,
            rows=[{"id": 1, "win_rate": 0.5, "strategy_version": "v"}],
            row_count_override=99,
        )
        report = validate_dataset(ds)
        assert not report.ok
        assert any("row_count mismatch" in e for e in report.errors)

    def test_unknown_field(self, tmp_path: Path):
        ds = _write_dataset(
            tmp_path,
            rows=[{"id": 1, "win_rate": 0.5, "strategy_version": "v", "oops": 1}],
        )
        report = validate_dataset(ds)
        assert not report.ok
        assert any("unknown fields" in e for e in report.errors)

    def test_wrong_type(self, tmp_path: Path):
        ds = _write_dataset(
            tmp_path,
            rows=[{"id": "not-an-int", "win_rate": 0.5, "strategy_version": "v"}],
        )
        report = validate_dataset(ds)
        assert not report.ok
        assert any("expected int" in e for e in report.errors)

    def test_not_a_directory(self, tmp_path: Path):
        report = validate_dataset(tmp_path / "does-not-exist")
        assert not report.ok
