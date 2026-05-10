"""Tests for `market_raw` adapters + builder (S-AI-WS5-B-PART-1)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ml.datasets.adapters import (
    BybitOffvmMarketRawAdapter,
    CsvMarketRawAdapter,
    list_adapters,
)
from ml.datasets.adapters.bybit_offvm import (
    OFFVM_ENV,
    OffVmGuardrailViolation,
)
from ml.datasets.families.market_raw import MarketRawBuilder
from ml.datasets.validate import validate_dataset

_CSV_HEADER = "ts,open,high,low,close,volume\n"
_CSV_ROWS = (
    "2026-05-01T00:00:00Z,100.0,101.0,99.5,100.5,1000.0\n"
    "2026-05-01T01:00:00Z,100.5,102.0,100.0,101.5,1500.0\n"
    "2026-05-01T02:00:00Z,101.5,101.8,100.7,100.9,800.0\n"
)


def _stage_csv(tmp_path: Path) -> Path:
    csv_path = tmp_path / "bars.csv"
    csv_path.write_text(_CSV_HEADER + _CSV_ROWS, encoding="utf-8")
    return csv_path


class TestCsvAdapter:
    def test_iter_bars(self, tmp_path: Path):
        adapter = CsvMarketRawAdapter()
        rows = list(
            adapter.iter_bars(
                csv_path=_stage_csv(tmp_path),
                symbol="BTCUSDT",
                timeframe="1h",
            )
        )
        assert len(rows) == 3
        assert rows[0]["ts"] == "2026-05-01T00:00:00Z"
        assert rows[0]["symbol"] == "BTCUSDT"
        assert rows[0]["timeframe"] == "1h"
        assert rows[0]["open"] == 100.0
        assert rows[0]["close"] == 100.5
        assert rows[0]["volume"] == 1000.0
        assert rows[0]["source"] == "csv"

    def test_missing_file_raises(self, tmp_path: Path):
        adapter = CsvMarketRawAdapter()
        with pytest.raises(FileNotFoundError):
            list(
                adapter.iter_bars(
                    csv_path=tmp_path / "missing.csv",
                    symbol="BTCUSDT",
                    timeframe="1h",
                )
            )

    def test_missing_columns_raises(self, tmp_path: Path):
        bad = tmp_path / "bad.csv"
        bad.write_text("ts,open,close\n2026-05-01,1,2\n", encoding="utf-8")
        adapter = CsvMarketRawAdapter()
        with pytest.raises(ValueError):
            list(
                adapter.iter_bars(
                    csv_path=bad, symbol="BTCUSDT", timeframe="1h"
                )
            )

    def test_case_insensitive_headers(self, tmp_path: Path):
        ucase = tmp_path / "ucase.csv"
        ucase.write_text(
            "TS,OPEN,HIGH,LOW,CLOSE,VOLUME\n"
            "2026-05-01T00:00:00Z,1.0,2.0,0.5,1.5,100.0\n",
            encoding="utf-8",
        )
        adapter = CsvMarketRawAdapter()
        rows = list(
            adapter.iter_bars(
                csv_path=ucase, symbol="BTCUSDT", timeframe="1m"
            )
        )
        assert len(rows) == 1
        assert rows[0]["close"] == 1.5

    def test_volume_optional(self, tmp_path: Path):
        novol = tmp_path / "novol.csv"
        novol.write_text(
            "ts,open,high,low,close\n"
            "2026-05-01T00:00:00Z,1,2,0,1.5\n",
            encoding="utf-8",
        )
        adapter = CsvMarketRawAdapter()
        rows = list(
            adapter.iter_bars(
                csv_path=novol, symbol="BTCUSDT", timeframe="1h"
            )
        )
        assert rows[0]["volume"] == 0.0


class TestBybitOffvmEnvGate:
    def test_refuses_without_env(self, monkeypatch):
        monkeypatch.delenv(OFFVM_ENV, raising=False)
        adapter = BybitOffvmMarketRawAdapter()
        with pytest.raises(OffVmGuardrailViolation):
            list(
                adapter.iter_bars(
                    symbol="BTCUSDT",
                    timeframe="1h",
                    start="2025-01-01",
                    end="2025-01-02",
                )
            )

    def test_refuses_with_wrong_env_value(self, monkeypatch):
        monkeypatch.setenv(OFFVM_ENV, "yes-please")
        adapter = BybitOffvmMarketRawAdapter()
        with pytest.raises(OffVmGuardrailViolation):
            list(
                adapter.iter_bars(
                    symbol="BTCUSDT",
                    timeframe="1h",
                    start="2025-01-01",
                    end="2025-01-02",
                )
            )

    def test_with_env_raises_not_implemented(self, monkeypatch):
        monkeypatch.setenv(OFFVM_ENV, "1")
        adapter = BybitOffvmMarketRawAdapter()
        # Past the guardrail, the actual fetch is filed for a follow-up.
        with pytest.raises(NotImplementedError):
            list(
                adapter.iter_bars(
                    symbol="BTCUSDT",
                    timeframe="1h",
                    start="2025-01-01",
                    end="2025-01-02",
                )
            )


class TestMarketRawBuilder:
    def test_build_round_trip_via_csv(self, tmp_path: Path):
        csv_path = _stage_csv(tmp_path)
        out = tmp_path / "datasets"
        builder = MarketRawBuilder()
        paths = builder.build(
            output_dir=out,
            version="v001",
            source=str(csv_path),
            symbol_scope="BTCUSDT",
            timeframe="1h",
            commit_sha="deadbeef",
            adapter="csv",
            csv_path=csv_path,
            symbol="BTCUSDT",
        )
        assert paths.root == out / "market_raw" / "BTCUSDT" / "1h" / "v001"
        assert paths.metadata.is_file()
        assert paths.data.is_file()

        rows = [
            json.loads(line)
            for line in paths.data.read_text().splitlines()
            if line
        ]
        assert len(rows) == 3
        assert rows[0]["source"] == "csv"

        metadata = json.loads(paths.metadata.read_text())
        assert metadata["family"] == "market_raw"
        assert metadata["row_count"] == 3
        assert metadata["leakage_test_status"] == "n/a"
        assert "open" in metadata["schema"]
        assert metadata["schema"]["open"] == "float"

        report = validate_dataset(paths.root)
        assert report.ok, report.errors

    def test_build_unknown_adapter_raises(self, tmp_path: Path):
        builder = MarketRawBuilder()
        with pytest.raises(KeyError):
            builder.build(
                output_dir=tmp_path / "out",
                version="v001",
                source="x",
                symbol_scope="BTCUSDT",
                timeframe="1h",
                commit_sha="x",
                adapter="made-up",
            )


def test_registry_includes_market_raw():
    from ml.datasets import list_families, get_builder

    assert "market_raw" in list_families()
    assert isinstance(get_builder("market_raw"), MarketRawBuilder)


def test_adapter_registry_includes_csv_and_bybit():
    names = list_adapters()
    assert "csv" in names
    assert "bybit_v5_offvm" in names


def test_offvm_env_constant_is_explicit():
    # Pin the env-var name so a future refactor doesn't silently
    # change the operator-facing contract.
    assert OFFVM_ENV == "ICT_OFFVM_BUILD_HOST"
