"""Tests for the forward trend-regime labeler (S-MLOPT-S15 / Phase 3.3).

Covers `ml.datasets.labeling.trend_regime` (efficiency ratio + label mapping)
and its integration into the `market_features` family as `trend_regime_label`.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from ml.datasets.families.market_features import MarketFeaturesBuilder
from ml.datasets.labeling.trend_regime import (
    CHOP,
    TRANSITIONAL,
    TREND_REGIME_LABELS,
    TRENDING,
    efficiency_ratio,
    label_forward_window,
    trend_regime_label,
)


class TestEfficiencyRatio:
    def test_pure_trend_is_one(self):
        # All same-sign moves → net == gross → ER = 1.
        assert efficiency_ratio([0.01, 0.01, 0.02]) == pytest.approx(1.0)
        assert efficiency_ratio([-0.01, -0.01, -0.02]) == pytest.approx(1.0)

    def test_perfect_chop_is_zero(self):
        # Equal up/down → net 0 → ER = 0.
        assert efficiency_ratio([0.01, -0.01, 0.01, -0.01]) == pytest.approx(0.0)

    def test_partial(self):
        # net = 0.02, gross = 0.04 → ER = 0.5.
        assert efficiency_ratio([0.03, -0.01]) == pytest.approx(0.5)

    def test_empty_is_none(self):
        assert efficiency_ratio([]) is None
        assert efficiency_ratio([None, None]) is None

    def test_zero_gross_is_zero(self):
        assert efficiency_ratio([0.0, 0.0]) == 0.0

    def test_skips_none(self):
        assert efficiency_ratio([0.01, None, 0.01]) == pytest.approx(1.0)


class TestLabelMapping:
    def test_thresholds(self):
        assert trend_regime_label(0.0, chop_max=0.3, trend_min=0.55) == CHOP
        assert trend_regime_label(0.3, chop_max=0.3, trend_min=0.55) == CHOP
        assert trend_regime_label(0.4, chop_max=0.3, trend_min=0.55) == TRANSITIONAL
        assert trend_regime_label(0.55, chop_max=0.3, trend_min=0.55) == TRENDING
        assert trend_regime_label(0.9, chop_max=0.3, trend_min=0.55) == TRENDING

    def test_none_passthrough(self):
        assert trend_regime_label(None) is None

    def test_convenience(self):
        assert label_forward_window([0.01, 0.01, 0.01]) == TRENDING
        assert label_forward_window([0.01, -0.01, 0.01, -0.01]) == CHOP

    def test_labels_constant(self):
        assert TREND_REGIME_LABELS == (CHOP, TRANSITIONAL, TRENDING)


def _stage_market_raw(tmp_path: Path, closes: list[float]) -> Path:
    base = datetime.fromisoformat("2025-01-01T00:00:00+00:00")
    root = tmp_path / "market_raw" / "BTCUSDT" / "1h" / "v001"
    root.mkdir(parents=True, exist_ok=True)
    with (root / "data.jsonl").open("w", encoding="utf-8") as fh:
        for i, c in enumerate(closes):
            ts = (base + timedelta(hours=i)).isoformat().replace("+00:00", "Z")
            fh.write(json.dumps({
                "ts": ts, "symbol": "BTCUSDT", "timeframe": "1h",
                "open": float(c), "high": float(c) * 1.001,
                "low": float(c) * 0.999, "close": float(c),
                "volume": 100.0, "source": "csv",
            }) + "\n")
    (root / "metadata.json").write_text(json.dumps({
        "family": "market_raw", "version": "v001", "symbol_scope": "BTCUSDT",
        "timeframe": "1h", "source": "csv", "timezone_name": "UTC",
        "generation_commit_sha": "test", "label_version": "n/a",
        "leakage_test_status": "n/a", "builder": "MarketRawBuilder",
        "builder_version": "v1", "row_count": len(closes),
        "schema": {"ts": "str", "symbol": "str", "timeframe": "str",
                   "open": "float", "high": "float", "low": "float",
                   "close": "float", "volume": "float", "source": "str"},
        "notes": "", "generated_at": "2026-05-10T00:00:00+00:00",
        "schema_version": "v1",
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return root


class TestMarketFeaturesIntegration:
    def test_trend_label_emitted_and_valid(self, tmp_path: Path):
        # A smooth uptrend then an alternating-chop block.
        closes = []
        p = 100.0
        for _ in range(60):
            p *= 1.004
            closes.append(p)
        for i in range(60):
            p *= 1 + (0.02 if i % 2 == 0 else -0.02)
            closes.append(p)
        market_raw = _stage_market_raw(tmp_path, closes)
        rows = list(MarketFeaturesBuilder().iter_rows(
            market_raw_path=market_raw, vol_window_n=20, forward_window_m=5,
        ))
        assert rows
        for r in rows:
            assert r["trend_regime_label"] in TREND_REGIME_LABELS
            # Schema parity (the family declares the column).
            assert "trend_regime_label" in MarketFeaturesBuilder.schema

    def test_uptrend_window_labels_trending(self, tmp_path: Path):
        # Pure smooth uptrend → every bar's forward window is trending.
        closes = [100.0 * (1.004 ** i) for i in range(80)]
        market_raw = _stage_market_raw(tmp_path, closes)
        rows = list(MarketFeaturesBuilder().iter_rows(
            market_raw_path=market_raw, vol_window_n=20, forward_window_m=5,
        ))
        assert rows
        assert all(r["trend_regime_label"] == TRENDING for r in rows)

    def test_chop_window_labels_chop(self, tmp_path: Path):
        # Alternating equal up/down → forward windows net ~zero → chop.
        closes = [100.0]
        for i in range(120):
            closes.append(closes[-1] * (1 + (0.02 if i % 2 == 0 else -0.02)))
        market_raw = _stage_market_raw(tmp_path, closes)
        rows = list(MarketFeaturesBuilder().iter_rows(
            market_raw_path=market_raw, vol_window_n=20, forward_window_m=4,
        ))
        assert rows
        # The vast majority of forward windows net near zero → chop.
        chop = sum(1 for r in rows if r["trend_regime_label"] == CHOP)
        assert chop / len(rows) > 0.8
