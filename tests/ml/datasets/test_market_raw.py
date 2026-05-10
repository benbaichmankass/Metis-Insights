"""Tests for `market_raw` adapters + builder (S-AI-WS5-B-PART-1 + PART-2)."""
from __future__ import annotations

import json
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
    _iso_to_ms,
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

    def test_with_env_invokes_fetch(self, monkeypatch):
        # Past the guardrail, the adapter now actually calls the
        # exchange (S-AI-WS5-B-PART-2). Mock the exchange so CI never
        # touches the network.
        monkeypatch.setenv(OFFVM_ENV, "1")
        fake = _FakeBybitExchange(
            pages=[
                [
                    [_iso_to_ms("2025-01-01T00:00:00Z"), 1.0, 2.0, 0.5, 1.5, 100.0],
                    [_iso_to_ms("2025-01-01T01:00:00Z"), 1.5, 2.5, 1.0, 2.0, 200.0],
                ],
            ],
        )
        monkeypatch.setattr(
            BybitOffvmMarketRawAdapter,
            "_build_exchange",
            classmethod(lambda cls, **kw: fake),
        )
        adapter = BybitOffvmMarketRawAdapter()
        rows = list(
            adapter.iter_bars(
                symbol="BTCUSDT",
                timeframe="1h",
                start="2025-01-01T00:00:00Z",
                end="2025-01-01T02:00:00Z",
            )
        )
        assert len(rows) == 2
        assert rows[0]["ts"] == "2025-01-01T00:00:00Z"
        assert rows[0]["symbol"] == "BTCUSDT"
        assert rows[0]["timeframe"] == "1h"
        assert rows[0]["source"] == "bybit_v5_offvm"


class _FakeBybitExchange:
    """Records fetch_ohlcv calls and replays canned pages.

    Tests construct one of these and patch
    `BybitOffvmMarketRawAdapter._build_exchange` to return it; the
    real ccxt client is never instantiated.
    """

    def __init__(self, pages):
        self._pages = list(pages)
        self.calls: list[dict] = []

    def fetch_ohlcv(self, symbol, *, timeframe, since, limit):
        self.calls.append(
            {"symbol": symbol, "timeframe": timeframe, "since": since, "limit": limit}
        )
        if not self._pages:
            return []
        return self._pages.pop(0)


class TestBybitOffvmFetch:
    """Wiring of `_fetch_bars` past the env-gate (S-AI-WS5-B-PART-2 PR 2A)."""

    def test_paginates_until_end(self, monkeypatch):
        monkeypatch.setenv(OFFVM_ENV, "1")
        page_a = [
            [_iso_to_ms("2025-01-01T00:00:00Z"), 1, 2, 0, 1.5, 10],
            [_iso_to_ms("2025-01-01T01:00:00Z"), 1.5, 2.5, 1, 2, 20],
        ]
        page_b = [
            [_iso_to_ms("2025-01-01T02:00:00Z"), 2, 3, 1.5, 2.5, 30],
            [_iso_to_ms("2025-01-01T03:00:00Z"), 2.5, 3.5, 2, 3, 40],
        ]
        fake = _FakeBybitExchange(pages=[page_a, page_b, []])
        monkeypatch.setattr(
            BybitOffvmMarketRawAdapter,
            "_build_exchange",
            classmethod(lambda cls, **kw: fake),
        )
        rows = list(
            BybitOffvmMarketRawAdapter().iter_bars(
                symbol="BTCUSDT",
                timeframe="1h",
                start="2025-01-01T00:00:00Z",
                end="2025-01-02T00:00:00Z",
            )
        )
        assert [r["ts"] for r in rows] == [
            "2025-01-01T00:00:00Z",
            "2025-01-01T01:00:00Z",
            "2025-01-01T02:00:00Z",
            "2025-01-01T03:00:00Z",
        ]
        # First call uses the start-ms cursor; second call advances to the
        # last yielded bar + 1 bar (1h = 3_600_000 ms).
        assert fake.calls[0]["since"] == _iso_to_ms("2025-01-01T00:00:00Z")
        assert fake.calls[1]["since"] == _iso_to_ms("2025-01-01T02:00:00Z")
        assert fake.calls[0]["timeframe"] == "1h"
        assert fake.calls[0]["symbol"] == "BTCUSDT"

    def test_stops_at_end_window(self, monkeypatch):
        monkeypatch.setenv(OFFVM_ENV, "1")
        # Returns four bars but `end` cuts off after the second.
        page = [
            [_iso_to_ms("2025-01-01T00:00:00Z"), 1, 2, 0, 1.5, 10],
            [_iso_to_ms("2025-01-01T01:00:00Z"), 1.5, 2.5, 1, 2, 20],
            [_iso_to_ms("2025-01-01T02:00:00Z"), 2, 3, 1.5, 2.5, 30],
            [_iso_to_ms("2025-01-01T03:00:00Z"), 2.5, 3.5, 2, 3, 40],
        ]
        fake = _FakeBybitExchange(pages=[page])
        monkeypatch.setattr(
            BybitOffvmMarketRawAdapter,
            "_build_exchange",
            classmethod(lambda cls, **kw: fake),
        )
        rows = list(
            BybitOffvmMarketRawAdapter().iter_bars(
                symbol="BTCUSDT",
                timeframe="1h",
                start="2025-01-01T00:00:00Z",
                end="2025-01-01T02:00:00Z",
            )
        )
        assert len(rows) == 2
        assert rows[-1]["ts"] == "2025-01-01T01:00:00Z"

    def test_empty_first_page_returns_no_rows(self, monkeypatch):
        monkeypatch.setenv(OFFVM_ENV, "1")
        fake = _FakeBybitExchange(pages=[[]])
        monkeypatch.setattr(
            BybitOffvmMarketRawAdapter,
            "_build_exchange",
            classmethod(lambda cls, **kw: fake),
        )
        rows = list(
            BybitOffvmMarketRawAdapter().iter_bars(
                symbol="BTCUSDT",
                timeframe="1h",
                start="2025-01-01T00:00:00Z",
                end="2025-01-02T00:00:00Z",
            )
        )
        assert rows == []

    def test_unknown_timeframe_raises(self, monkeypatch):
        monkeypatch.setenv(OFFVM_ENV, "1")
        with pytest.raises(ValueError, match="unsupported timeframe"):
            list(
                BybitOffvmMarketRawAdapter().iter_bars(
                    symbol="BTCUSDT",
                    timeframe="2h",
                    start="2025-01-01T00:00:00Z",
                    end="2025-01-02T00:00:00Z",
                )
            )

    def test_inverted_window_raises(self, monkeypatch):
        monkeypatch.setenv(OFFVM_ENV, "1")
        with pytest.raises(ValueError, match="must be after start"):
            list(
                BybitOffvmMarketRawAdapter().iter_bars(
                    symbol="BTCUSDT",
                    timeframe="1h",
                    start="2025-01-02T00:00:00Z",
                    end="2025-01-01T00:00:00Z",
                )
            )

    def test_canonical_row_shape(self, monkeypatch):
        monkeypatch.setenv(OFFVM_ENV, "1")
        page = [
            [_iso_to_ms("2025-01-01T00:00:00Z"), 1.0, 2.0, 0.5, 1.5, 100.0],
        ]
        fake = _FakeBybitExchange(pages=[page])
        monkeypatch.setattr(
            BybitOffvmMarketRawAdapter,
            "_build_exchange",
            classmethod(lambda cls, **kw: fake),
        )
        row = next(
            BybitOffvmMarketRawAdapter().iter_bars(
                symbol="BTCUSDT",
                timeframe="1h",
                start="2025-01-01T00:00:00Z",
                end="2025-01-01T02:00:00Z",
            )
        )
        # Canonical shape: exact key set + types per CANONICAL_SCHEMA.
        from ml.datasets.adapters.base import CANONICAL_SCHEMA

        assert set(row.keys()) == set(CANONICAL_SCHEMA.keys())
        for name, expected in CANONICAL_SCHEMA.items():
            assert isinstance(row[name], expected), (
                f"{name} expected {expected}; got {type(row[name])}"
            )
        assert row["source"] == "bybit_v5_offvm"

    def test_credentials_threaded_to_exchange_builder(self, monkeypatch):
        monkeypatch.setenv(OFFVM_ENV, "1")
        monkeypatch.setenv("BYBIT_API_KEY", "env-key")
        monkeypatch.setenv("BYBIT_API_SECRET", "env-secret")
        captured: dict = {}

        def fake_builder(cls, *, api_key, api_secret, testnet):
            captured["api_key"] = api_key
            captured["api_secret"] = api_secret
            captured["testnet"] = testnet
            return _FakeBybitExchange(pages=[[]])

        monkeypatch.setattr(
            BybitOffvmMarketRawAdapter,
            "_build_exchange",
            classmethod(fake_builder),
        )
        list(
            BybitOffvmMarketRawAdapter().iter_bars(
                symbol="BTCUSDT",
                timeframe="1h",
                start="2025-01-01T00:00:00Z",
                end="2025-01-02T00:00:00Z",
            )
        )
        assert captured == {
            "api_key": "env-key",
            "api_secret": "env-secret",
            "testnet": False,
        }

    def test_explicit_credentials_override_env(self, monkeypatch):
        monkeypatch.setenv(OFFVM_ENV, "1")
        monkeypatch.setenv("BYBIT_API_KEY", "env-key")
        monkeypatch.setenv("BYBIT_API_SECRET", "env-secret")
        captured: dict = {}

        def fake_builder(cls, *, api_key, api_secret, testnet):
            captured["api_key"] = api_key
            captured["api_secret"] = api_secret
            captured["testnet"] = testnet
            return _FakeBybitExchange(pages=[[]])

        monkeypatch.setattr(
            BybitOffvmMarketRawAdapter,
            "_build_exchange",
            classmethod(fake_builder),
        )
        list(
            BybitOffvmMarketRawAdapter().iter_bars(
                symbol="BTCUSDT",
                timeframe="1h",
                start="2025-01-01T00:00:00Z",
                end="2025-01-02T00:00:00Z",
                api_key="kw-key",
                api_secret="kw-secret",
                testnet=True,
            )
        )
        assert captured == {
            "api_key": "kw-key",
            "api_secret": "kw-secret",
            "testnet": True,
        }

    def test_pre_window_bars_dropped(self, monkeypatch):
        # Defensive against ccxt occasionally returning a few bars
        # before the requested `since`.
        monkeypatch.setenv(OFFVM_ENV, "1")
        page = [
            [_iso_to_ms("2024-12-31T23:00:00Z"), 1, 2, 0, 1, 5],  # before start
            [_iso_to_ms("2025-01-01T00:00:00Z"), 1, 2, 0, 1.5, 10],
        ]
        fake = _FakeBybitExchange(pages=[page, []])
        monkeypatch.setattr(
            BybitOffvmMarketRawAdapter,
            "_build_exchange",
            classmethod(lambda cls, **kw: fake),
        )
        rows = list(
            BybitOffvmMarketRawAdapter().iter_bars(
                symbol="BTCUSDT",
                timeframe="1h",
                start="2025-01-01T00:00:00Z",
                end="2025-01-01T02:00:00Z",
            )
        )
        assert len(rows) == 1
        assert rows[0]["ts"] == "2025-01-01T00:00:00Z"


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
