"""Tests for the `market_features` family (S-AI-WS5-B-PART-2 PR 2B)."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from ml.datasets import get_builder, list_families, validate_dataset
from ml.datasets.families.market_features import (
    MarketFeaturesBuilder,
    REGIME_LABELS,
    _label_regime,
)


def _stage_market_raw(
    tmp_path: Path,
    *,
    closes: list[float],
    base_ts_iso: str = "2025-01-01T00:00:00Z",
    bar_seconds: int = 3600,
    symbol: str = "BTCUSDT",
    timeframe: str = "1h",
    source: str = "csv",
) -> Path:
    """Write a synthetic market_raw data.jsonl + metadata.json."""
    from datetime import datetime, timedelta, timezone

    base = datetime.fromisoformat(base_ts_iso.replace("Z", "+00:00"))
    root = tmp_path / "market_raw" / symbol / timeframe / "v001"
    root.mkdir(parents=True, exist_ok=True)
    data = root / "data.jsonl"
    with data.open("w", encoding="utf-8") as fh:
        for i, close in enumerate(closes):
            ts = (base + timedelta(seconds=bar_seconds * i)).isoformat().replace(
                "+00:00", "Z"
            )
            row = {
                "ts": ts,
                "symbol": symbol,
                "timeframe": timeframe,
                "open": float(close),
                "high": float(close) * 1.001,
                "low": float(close) * 0.999,
                "close": float(close),
                "volume": 100.0,
                "source": source,
            }
            fh.write(json.dumps(row) + "\n")
    metadata = {
        "family": "market_raw",
        "version": "v001",
        "symbol_scope": symbol,
        "timeframe": timeframe,
        "source": source,
        "timezone_name": "UTC",
        "generation_commit_sha": "test",
        "label_version": "n/a",
        "leakage_test_status": "n/a",
        "builder": "MarketRawBuilder",
        "builder_version": "v1",
        "row_count": len(closes),
        "schema": {
            "ts": "str",
            "symbol": "str",
            "timeframe": "str",
            "open": "float",
            "high": "float",
            "low": "float",
            "close": "float",
            "volume": "float",
            "source": "str",
        },
        "notes": "",
        "generated_at": "2026-05-10T00:00:00+00:00",
        "schema_version": "v1",
    }
    (root / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return root


def _trending_then_choppy(n_per_phase: int = 80) -> list[float]:
    """Three concatenated price phases with distinct vol/trend signature."""
    closes: list[float] = []
    # Phase A: smooth uptrend (low past vol, large positive forward return).
    price = 100.0
    for i in range(n_per_phase):
        price *= 1.005
        closes.append(price)
    # Phase B: high-vol chop (large per-bar swings, near-zero net move).
    price = closes[-1]
    for i in range(n_per_phase):
        sign = 1 if i % 2 == 0 else -1
        price *= 1 + sign * 0.03
        closes.append(price)
    # Phase C: tight range (low vol, near-zero net move).
    price = closes[-1]
    for i in range(n_per_phase):
        sign = 1 if i % 2 == 0 else -1
        price *= 1 + sign * 0.0005
        closes.append(price)
    return closes


class TestRegimeLabelingRule:
    def test_volatile_overrides_trend(self):
        # High vol with strong directional move → "volatile" wins over
        # "trend" because vol is checked first.
        assert (
            _label_regime(
                forward_log_return=0.1,
                forward_vol=0.02,
                trend_threshold=0.005,
                vol_threshold=0.005,
            )
            == "volatile"
        )

    def test_trend_when_low_vol_and_strong_move(self):
        assert (
            _label_regime(
                forward_log_return=0.02,
                forward_vol=0.001,
                trend_threshold=0.005,
                vol_threshold=0.005,
            )
            == "trend"
        )

    def test_range_when_quiet(self):
        assert (
            _label_regime(
                forward_log_return=0.001,
                forward_vol=0.0005,
                trend_threshold=0.005,
                vol_threshold=0.005,
            )
            == "range"
        )

    def test_negative_trend(self):
        assert (
            _label_regime(
                forward_log_return=-0.02,
                forward_vol=0.001,
                trend_threshold=0.005,
                vol_threshold=0.005,
            )
            == "trend"
        )


class TestMarketFeaturesBuilder:
    def test_emits_canonical_schema(self, tmp_path: Path):
        market_raw = _stage_market_raw(
            tmp_path, closes=_trending_then_choppy(n_per_phase=80)
        )
        builder = MarketFeaturesBuilder()
        rows = list(
            builder.iter_rows(
                market_raw_path=market_raw,
                vol_window_n=20,
                forward_window_m=5,
                vol_threshold=0.005,
                trend_threshold=0.005,
                n_vol_buckets=3,
            )
        )
        assert rows, "builder should emit rows for the synthetic dataset"
        for row in rows:
            assert set(row.keys()) == set(MarketFeaturesBuilder.schema.keys())
            assert row["regime_label"] in REGIME_LABELS
            assert row["vol_bucket"] in {"vol_b0", "vol_b1", "vol_b2"}
            assert isinstance(row["log_return"], float)
            assert isinstance(row["rolling_log_return_vol"], float)
            assert row["rolling_log_return_vol"] >= 0.0
            assert isinstance(row["forward_log_return"], float)
            assert isinstance(row["forward_log_return_vol"], float)
            assert row["forward_log_return_vol"] >= 0.0
            assert row["symbol"] == "BTCUSDT"
            assert row["timeframe"] == "1h"
            assert row["source"] == "csv"

    def test_skips_edge_rows(self, tmp_path: Path):
        # n bars; first vol_window_n-2 rows have no past vol (None);
        # last forward_window_m rows have no forward window. Builder
        # should yield only complete rows.
        closes = _trending_then_choppy(n_per_phase=50)
        market_raw = _stage_market_raw(tmp_path, closes=closes)
        builder = MarketFeaturesBuilder()
        rows = list(
            builder.iter_rows(
                market_raw_path=market_raw,
                vol_window_n=10,
                forward_window_m=5,
            )
        )
        # We expect rows = n - (vol_window_n - 1) - forward_window_m,
        # roughly. Specifically, first complete row is at i = vol_window_n-1
        # (so the past window has all bars including i and excluding the
        # one missing log_return at i=0). Last complete row is at
        # i = n - 1 - forward_window_m.
        n = len(closes)
        # The minimum-i with full past stats is bounded by needing
        # at least vol_window_n - 1 non-None log_returns up to index i;
        # since log_returns[0] is None, that becomes i >= vol_window_n - 1.
        expected_min = 10 - 1
        expected_max_inclusive = n - 1 - 5
        expected_count = expected_max_inclusive - expected_min + 1
        assert len(rows) == expected_count

    def test_phase_distribution(self, tmp_path: Path):
        # The synthetic dataset has three distinct phases. With low
        # thresholds tuned to the synthetic, all three regime classes
        # should appear in the output.
        market_raw = _stage_market_raw(
            tmp_path, closes=_trending_then_choppy(n_per_phase=80)
        )
        builder = MarketFeaturesBuilder()
        rows = list(
            builder.iter_rows(
                market_raw_path=market_raw,
                vol_window_n=10,
                forward_window_m=5,
                vol_threshold=0.005,
                trend_threshold=0.005,
            )
        )
        labels = {r["regime_label"] for r in rows}
        # Trend (Phase A) + volatile (Phase B) + range (Phase C) must all
        # appear if thresholds are chosen sanely.
        assert labels == set(REGIME_LABELS), (
            f"expected all three regimes; got {labels}"
        )

    def test_log_return_matches_close_diff(self, tmp_path: Path):
        # Sanity-check the per-bar log_return against the source closes.
        closes = [100.0, 101.0, 102.5, 101.0, 99.5] + [
            100.0 + i * 0.1 for i in range(40)
        ]
        market_raw = _stage_market_raw(tmp_path, closes=closes)
        builder = MarketFeaturesBuilder()
        rows = list(
            builder.iter_rows(
                market_raw_path=market_raw,
                vol_window_n=3,
                forward_window_m=3,
            )
        )
        for row in rows[:5]:
            ts = row["ts"]
            # Find the source close at this ts.
            from datetime import datetime, timezone

            ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            base = datetime.fromisoformat(
                "2025-01-01T00:00:00+00:00"
            ).astimezone(timezone.utc)
            idx = round((ts_dt - base).total_seconds() / 3600)
            expected = math.log(closes[idx] / closes[idx - 1])
            assert math.isclose(
                row["log_return"], expected, rel_tol=1e-9, abs_tol=1e-12
            )

    def test_invalid_window_raises(self, tmp_path: Path):
        market_raw = _stage_market_raw(tmp_path, closes=[100.0] * 100)
        builder = MarketFeaturesBuilder()
        with pytest.raises(ValueError, match="vol_window_n"):
            list(builder.iter_rows(market_raw_path=market_raw, vol_window_n=1))
        with pytest.raises(ValueError, match="forward_window_m"):
            list(
                builder.iter_rows(
                    market_raw_path=market_raw, forward_window_m=1
                )
            )
        with pytest.raises(ValueError, match="vol_threshold"):
            list(
                builder.iter_rows(
                    market_raw_path=market_raw, vol_threshold=-0.1
                )
            )

    def test_missing_path_raises(self, tmp_path: Path):
        builder = MarketFeaturesBuilder()
        with pytest.raises(FileNotFoundError):
            list(
                builder.iter_rows(
                    market_raw_path=tmp_path / "does-not-exist"
                )
            )

    def test_short_input_yields_nothing(self, tmp_path: Path):
        # Fewer bars than needed for a single complete past + forward
        # window → no rows.
        market_raw = _stage_market_raw(tmp_path, closes=[100.0] * 5)
        builder = MarketFeaturesBuilder()
        rows = list(
            builder.iter_rows(
                market_raw_path=market_raw,
                vol_window_n=10,
                forward_window_m=5,
            )
        )
        assert rows == []

    def test_full_build_round_trip(self, tmp_path: Path):
        # End-to-end: market_features builder writes a valid dataset
        # that passes validate_dataset.
        market_raw = _stage_market_raw(
            tmp_path, closes=_trending_then_choppy(n_per_phase=60)
        )
        out = tmp_path / "datasets"
        builder = MarketFeaturesBuilder()
        paths = builder.build(
            output_dir=out,
            version="v001",
            source=str(market_raw),
            symbol_scope="BTCUSDT",
            timeframe="1h",
            commit_sha="deadbeef",
            market_raw_path=market_raw,
            vol_window_n=10,
            forward_window_m=5,
        )
        assert paths.root == out / "market_features" / "BTCUSDT" / "1h" / "v001"
        report = validate_dataset(paths.root)
        assert report.ok, report.errors
        meta = json.loads(paths.metadata.read_text())
        assert meta["family"] == "market_features"
        assert meta["leakage_test_status"] == "passed"
        assert meta["label_version"] == "regime-3class-v1"


def test_registry_includes_market_features():
    assert "market_features" in list_families()
    assert isinstance(get_builder("market_features"), MarketFeaturesBuilder)
