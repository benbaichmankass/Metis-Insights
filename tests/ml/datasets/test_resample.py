"""Tests for the `resample` market_raw adapter (derive 15m from cached 5m)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ml.datasets.adapters import ResampleMarketRawAdapter, list_adapters


def _write_5m(tmp_path: Path, n: int = 6) -> Path:
    # 6 consecutive 5m bars from 00:00 → two full 15m buckets.
    base = "2024-01-01T00:%02d:00Z"
    rows = []
    for i in range(n):
        px = 100.0 + i
        rows.append({
            "ts": base % (i * 5), "symbol": "MES", "timeframe": "5m",
            "open": px, "high": px + 2, "low": px - 1, "close": px + 0.5,
            "volume": 10.0 + i, "source": "csv",
        })
    d = tmp_path / "market_raw" / "MES" / "5m" / "v001"
    d.mkdir(parents=True)
    (d / "data.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    return d


class TestResample:
    def test_5m_to_15m_aggregation(self, tmp_path: Path):
        src = _write_5m(tmp_path, n=6)
        rows = list(ResampleMarketRawAdapter().iter_bars(
            symbol="MES", timeframe="15m", source_path=src))
        assert len(rows) == 2  # 6 x 5m -> 2 x 15m
        b0 = rows[0]
        assert b0["ts"] == "2024-01-01T00:00:00Z"
        assert b0["timeframe"] == "15m"
        assert b0["symbol"] == "MES"
        assert b0["source"] == "resample"
        # bucket 0 = bars i=0,1,2 (px 100,101,102)
        assert b0["open"] == 100.0          # first.open
        assert b0["close"] == 102.5         # last.close (102 + 0.5)
        assert b0["high"] == 104.0          # max high (102 + 2)
        assert b0["low"] == 99.0            # min low (100 - 1)
        assert b0["volume"] == 33.0         # 10 + 11 + 12

    def test_accepts_data_file_directly(self, tmp_path: Path):
        src = _write_5m(tmp_path, n=3)
        rows = list(ResampleMarketRawAdapter().iter_bars(
            symbol="MES", timeframe="15m", source_path=src / "data.jsonl"))
        assert len(rows) == 1

    def test_unknown_timeframe_raises(self, tmp_path: Path):
        src = _write_5m(tmp_path, n=3)
        with pytest.raises(ValueError, match="unsupported timeframe"):
            list(ResampleMarketRawAdapter().iter_bars(
                symbol="MES", timeframe="7m", source_path=src))

    def test_missing_source_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            list(ResampleMarketRawAdapter().iter_bars(
                symbol="MES", timeframe="15m", source_path=tmp_path / "nope"))


def test_registry_includes_resample():
    assert "resample" in list_adapters()
