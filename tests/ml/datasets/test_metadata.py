"""Invariants for `ml.datasets.metadata.DatasetMetadata`."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ml.datasets.metadata import DatasetMetadata, LeakageStatus, SCHEMA_VERSION


def _meta(**overrides):
    base = dict(
        family="backtest_results",
        version="v001",
        symbol_scope="all",
        timeframe="all",
        source="trade_journal.db",
        timezone_name="UTC",
        generation_commit_sha="abc123",
        label_version="n/a",
        leakage_test_status=LeakageStatus.NOT_APPLICABLE,
        builder="BacktestResultsBuilder",
        builder_version="v1",
        row_count=3,
        schema={"id": "int", "win_rate": "float"},
    )
    base.update(overrides)
    return DatasetMetadata(**base)


class TestConstruct:
    def test_minimal(self):
        m = _meta()
        assert m.family == "backtest_results"
        assert m.schema_version == SCHEMA_VERSION
        assert m.generated_at.tzinfo is not None

    def test_build_params_defaults_empty_and_roundtrips(self):
        # Legacy dirs / raw families carry no build params.
        assert _meta().build_params == {}
        # Populated build params survive the to_dict/from_dict roundtrip
        # (MB-20260716-BUILDPARAMS-IGNORED — the dir is self-describing).
        m = _meta(build_params={"vol_threshold": 0.004, "n_vol_buckets": 3})
        d = m.to_dict()
        assert d["build_params"] == {"vol_threshold": 0.004, "n_vol_buckets": 3}
        assert DatasetMetadata.from_dict(d).build_params == m.build_params

    def test_required_fields_rejected_when_blank(self):
        for field_name in (
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
            with pytest.raises(ValueError):
                _meta(**{field_name: ""})

    def test_version_format(self):
        with pytest.raises(ValueError):
            _meta(version="001")
        with pytest.raises(ValueError):
            _meta(version="vONE")
        _meta(version="v999")  # ok

    def test_row_count_non_negative(self):
        _meta(row_count=0)
        with pytest.raises(ValueError):
            _meta(row_count=-1)

    def test_generated_at_must_be_aware(self):
        with pytest.raises(ValueError):
            _meta(generated_at=datetime(2026, 5, 10, 12, 0))  # naive

    def test_schema_must_be_non_empty(self):
        with pytest.raises(ValueError):
            _meta(schema={})


class TestSerialisation:
    def test_roundtrip(self):
        m = _meta(
            generated_at=datetime(2026, 5, 10, 12, 0, tzinfo=timezone.utc),
            leakage_test_status=LeakageStatus.PASSED,
            notes="smoke run",
        )
        payload = m.to_dict()
        assert payload["leakage_test_status"] == "passed"
        assert payload["generated_at"] == "2026-05-10T12:00:00+00:00"
        assert payload["schema"] == {"id": "int", "win_rate": "float"}

        m2 = DatasetMetadata.from_dict(payload)
        assert m2 == m

    def test_to_dict_does_not_share_schema_ref(self):
        m = _meta(schema={"id": "int"})
        d = m.to_dict()
        d["schema"]["id"] = "str"
        # mutation of the serialised dict must not leak back into the
        # frozen dataclass instance
        assert m.schema["id"] == "int"
