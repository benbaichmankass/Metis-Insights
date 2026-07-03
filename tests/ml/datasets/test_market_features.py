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
    from datetime import datetime, timedelta

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
    def test_high_vol_returns_volatile(self):
        # High forward vol → "volatile" regardless of direction.
        assert (
            _label_regime(
                forward_log_return=0.1,
                forward_vol=0.02,
                trend_threshold=0.005,
                vol_threshold=0.005,
            )
            == "volatile"
        )

    def test_low_vol_strong_move_returns_range(self):
        # 2-class collapse: strong directional move in a calm period
        # used to return "trend"; now returns "range".
        assert (
            _label_regime(
                forward_log_return=0.02,
                forward_vol=0.001,
                trend_threshold=0.005,
                vol_threshold=0.005,
            )
            == "range"
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

    def test_negative_strong_move_returns_range(self):
        # 2-class collapse: negative directional move in calm period → "range".
        assert (
            _label_regime(
                forward_log_return=-0.02,
                forward_vol=0.001,
                trend_threshold=0.005,
                vol_threshold=0.005,
            )
            == "range"
        )

    def test_label_regime_never_returns_trend(self):
        # Regression guard: "trend" class was eliminated in
        # S-ML-REGIME-CLASSIFIER-FIX. No inputs should ever produce it.
        for flr, fvol in [(0.5, 0.0001), (-0.5, 0.0), (0.0, 0.0), (0.1, 0.004)]:
            result = _label_regime(
                forward_log_return=flr,
                forward_vol=fvol,
                trend_threshold=0.005,
                vol_threshold=0.005,
            )
            assert result != "trend", f"unexpected 'trend' for flr={flr}, fvol={fvol}"


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
        # The synthetic dataset has three distinct phases. After the
        # 2-class collapse (S-ML-REGIME-CLASSIFIER-FIX), exactly the
        # two REGIME_LABELS (range / volatile) must appear.
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
        assert "trend" not in labels, "trend class was eliminated; should not appear"
        assert labels <= set(REGIME_LABELS), (
            f"unexpected regime labels: {labels - set(REGIME_LABELS)}"
        )
        assert "volatile" in labels and "range" in labels, (
            f"expected both range and volatile; got {labels}"
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


class TestV2FeatureExpansion:
    """Phase-2 feature expansion: hour_of_day, dayofweek, log_return_lag_{1,2}.

    All new fields are non-leaking (parsed from the bar's own ``ts`` or
    derived from prior `log_return`s). Tests pin the schema, the
    correctness of the parsed time features, and that lag values match
    the underlying log_return series.
    """

    def test_v2_fields_present_in_every_row(self, tmp_path: Path):
        market_raw = _stage_market_raw(
            tmp_path, closes=_trending_then_choppy(n_per_phase=80)
        )
        builder = MarketFeaturesBuilder()
        rows = list(
            builder.iter_rows(
                market_raw_path=market_raw, vol_window_n=10, forward_window_m=5
            )
        )
        assert rows, "builder should emit at least one row"
        for r in rows:
            assert "hour_of_day" in r and isinstance(r["hour_of_day"], int)
            assert 0 <= r["hour_of_day"] <= 23
            assert "dayofweek" in r and isinstance(r["dayofweek"], int)
            assert 0 <= r["dayofweek"] <= 6
            assert "log_return_lag_1" in r and isinstance(r["log_return_lag_1"], float)
            assert "log_return_lag_2" in r and isinstance(r["log_return_lag_2"], float)

    def test_hour_dow_match_ts(self, tmp_path: Path):
        # Hourly bars starting Wed 2025-01-01 00:00 UTC → first emitted row
        # should land at a known (hour, dow). Wed = 2 (Mon=0).
        market_raw = _stage_market_raw(
            tmp_path,
            closes=_trending_then_choppy(n_per_phase=40),
            base_ts_iso="2025-01-01T00:00:00Z",
            bar_seconds=3600,
        )
        builder = MarketFeaturesBuilder()
        rows = list(
            builder.iter_rows(
                market_raw_path=market_raw, vol_window_n=10, forward_window_m=5
            )
        )
        # The first emitted row is bar index 9 (vol_window_n - 1 = 9), so
        # ts = 2025-01-01T09:00:00Z → hour=9, dow=2 (Wednesday).
        first = rows[0]
        assert first["hour_of_day"] == 9
        assert first["dayofweek"] == 2

    def test_log_return_lag_matches_series(self, tmp_path: Path):
        # The lag features should equal log_return[i-1] / log_return[i-2]
        # for each emitted bar. Easiest check: log_return_lag_1 of bar
        # i+1 == log_return of bar i.
        market_raw = _stage_market_raw(
            tmp_path, closes=_trending_then_choppy(n_per_phase=80)
        )
        builder = MarketFeaturesBuilder()
        rows = list(
            builder.iter_rows(
                market_raw_path=market_raw, vol_window_n=10, forward_window_m=5
            )
        )
        # rows are contiguous bar indices once the first complete row is
        # emitted (the builder skips earlier incomplete rows). Within the
        # emitted span, lag_1[i+1] = log_return[i].
        for i in range(len(rows) - 1):
            assert math.isclose(
                rows[i + 1]["log_return_lag_1"],
                rows[i]["log_return"],
                rel_tol=1e-9,
                abs_tol=1e-12,
            )

    def test_builder_version_is_v12(self):
        # v6 -> v7: S-MLOPT-S15 added the trend_regime_label column.
        # v7 -> v8: S-CROSS-ASSET-PROBE added the xa_peer{1,2}_* + breadth columns.
        # v8 -> v9: S-CROSS-ASSET-PROBE step 3 added the direction_label column.
        # v9 -> v10: M19 T0.1 added the tsfm_emb_* pretrained-TSFM embedding columns.
        # v10 -> v11: M19 T0.4 added the fc_* pretrained-TSFM quantile-forecast columns.
        # v11 -> v12: M19 T1.2 added the corpus_emb_* SSL corpus-encoder columns.
        assert MarketFeaturesBuilder.builder_version == "v12"


class TestRangeVolEstimators:
    """S-MLOPT-S9: range-based vol estimator columns on every emitted row."""

    _COLS = ("parkinson_vol", "garman_klass_vol", "rogers_satchell_vol", "yang_zhang_vol")

    def test_columns_present_and_non_negative(self, tmp_path: Path):
        market_raw = _stage_market_raw(
            tmp_path, closes=_trending_then_choppy(n_per_phase=80)
        )
        rows = list(MarketFeaturesBuilder().iter_rows(
            market_raw_path=market_raw, vol_window_n=10, forward_window_m=5,
        ))
        assert rows
        for r in rows:
            for c in self._COLS:
                assert c in r and isinstance(r[c], float)
                assert r[c] >= 0.0
        # The synthetic feed has real overnight moves, so YZ is strictly positive.
        assert any(r["yang_zhang_vol"] > 0 for r in rows)
        assert any(r["parkinson_vol"] > 0 for r in rows)

    def test_range_vols_in_schema_and_validate(self, tmp_path: Path):
        market_raw = _stage_market_raw(
            tmp_path, closes=_trending_then_choppy(n_per_phase=80)
        )
        builder = MarketFeaturesBuilder()
        for c in self._COLS:
            assert c in builder.schema
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001", source="csv",
            symbol_scope="BTCUSDT", timeframe="1h",
            market_raw_path=market_raw, vol_window_n=10, forward_window_m=5,
        )
        report = validate_dataset(paths.root)
        assert report.ok, report


def _stage_funding_oi(
    tmp_path: Path,
    *,
    base_ts_iso: str = "2025-01-01T00:00:00Z",
    n_bars: int = 200,
    bar_seconds: int = 3600,
    funding_every: int = 8,
    symbol: str = "BTCUSDT",
) -> Path:
    """Write a synthetic funding/OI side-stream dir (data.jsonl)."""
    from datetime import datetime, timedelta

    base = datetime.fromisoformat(base_ts_iso.replace("Z", "+00:00"))
    root = tmp_path / "funding_oi" / symbol / "v001"
    root.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for i in range(0, n_bars, funding_every):
        ts = (base + timedelta(seconds=bar_seconds * i)).isoformat().replace("+00:00", "Z")
        rows.append({"ts": ts, "symbol": symbol,
                     "funding_rate": 0.0001 * (1 + (i % 7)), "open_interest": None})
    for i in range(n_bars):
        ts = (base + timedelta(seconds=bar_seconds * i)).isoformat().replace("+00:00", "Z")
        rows.append({"ts": ts, "symbol": symbol,
                     "funding_rate": None, "open_interest": 1000.0 + i * 3.0})
    rows.sort(key=lambda r: r["ts"])
    (root / "data.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return root


class TestFundingOiFeatures:
    """S-MLOPT-S11: funding-rate + open-interest feature columns."""

    _COLS = (
        "funding_rate",
        "funding_rate_zscore",
        "funding_rate_abs_z",
        "open_interest_change",
        "open_interest_change_zscore",
    )

    def test_columns_zero_without_funding_path(self, tmp_path: Path):
        market_raw = _stage_market_raw(tmp_path, closes=_trending_then_choppy(80))
        rows = list(MarketFeaturesBuilder().iter_rows(
            market_raw_path=market_raw, vol_window_n=10, forward_window_m=5,
        ))
        assert rows
        for r in rows:
            for c in self._COLS:
                assert c in r and r[c] == 0.0
        # range-vol features unaffected (still computed).
        assert any(r["yang_zhang_vol"] > 0 for r in rows)

    def test_columns_populated_with_funding_path(self, tmp_path: Path):
        market_raw = _stage_market_raw(tmp_path, closes=[100.0 * (1.001 ** i) for i in range(200)])
        funding_oi = _stage_funding_oi(tmp_path, n_bars=200)
        rows = list(MarketFeaturesBuilder().iter_rows(
            market_raw_path=market_raw, vol_window_n=10, forward_window_m=5,
            funding_oi_path=funding_oi, funding_window_n=48,
        ))
        assert rows
        # funding rate carried forward as-of (non-zero somewhere).
        assert any(r["funding_rate"] > 0 for r in rows)
        # rising OI => positive log change somewhere.
        assert any(r["open_interest_change"] > 0 for r in rows)
        # |z| equals abs(z) on every row.
        for r in rows:
            assert math.isclose(r["funding_rate_abs_z"], abs(r["funding_rate_zscore"]), abs_tol=1e-12)

    def test_asof_alignment_is_past_only(self, tmp_path: Path):
        # All funding observations timestamped AFTER the bar window => no bar may
        # see a funding rate (carry-forward stays 0.0). Guards leakage.
        market_raw = _stage_market_raw(tmp_path, closes=[100.0] * 120)
        funding_oi = _stage_funding_oi(
            tmp_path, base_ts_iso="2030-01-01T00:00:00Z", n_bars=120,
        )
        rows = list(MarketFeaturesBuilder().iter_rows(
            market_raw_path=market_raw, vol_window_n=10, forward_window_m=5,
            funding_oi_path=funding_oi, funding_window_n=48,
        ))
        assert rows
        assert all(r["funding_rate"] == 0.0 for r in rows)

    def test_funding_in_schema_and_validate(self, tmp_path: Path):
        market_raw = _stage_market_raw(tmp_path, closes=[100.0 * (1.001 ** i) for i in range(160)])
        funding_oi = _stage_funding_oi(tmp_path, n_bars=160)
        builder = MarketFeaturesBuilder()
        for c in self._COLS:
            assert c in builder.schema
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001", source="csv",
            symbol_scope="BTCUSDT", timeframe="1h",
            market_raw_path=market_raw, vol_window_n=10, forward_window_m=5,
            funding_oi_path=str(funding_oi), funding_window_n=48,
        )
        report = validate_dataset(paths.root)
        assert report.ok, report

    def test_invalid_funding_window_raises(self, tmp_path: Path):
        market_raw = _stage_market_raw(tmp_path, closes=[100.0] * 100)
        with pytest.raises(ValueError):
            list(MarketFeaturesBuilder().iter_rows(
                market_raw_path=market_raw, funding_window_n=1,
            ))


def _stage_microstructure(
    tmp_path: Path,
    *,
    base_ts_iso: str = "2025-01-01T00:00:00Z",
    n_bars: int = 200,
    bar_seconds: int = 300,
    symbol: str = "BTCUSDT",
) -> Path:
    """Write a synthetic market_microstructure side-stream dir (data.jsonl)."""
    from datetime import datetime, timedelta

    base = datetime.fromisoformat(base_ts_iso.replace("Z", "+00:00"))
    root = tmp_path / "market_microstructure" / symbol / "5m" / "v001"
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(n_bars):
        ts = (base + timedelta(seconds=bar_seconds * i)).isoformat().replace("+00:00", "Z")
        rows.append({
            "ts": ts, "symbol": symbol,
            "ofi": float((i % 7) - 3),
            "buy_vol": 5.0 + (i % 3), "sell_vol": 4.0,
            "rel_spread_mean": 0.0005, "microprice_dev": 0.0001 * ((i % 5) - 2),
            "n_snapshots": 30,
        })
    (root / "data.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return root


class TestMicrostructureFeatures:
    """S-MLOPT-S10: order-flow / microstructure feature columns."""

    _COLS = ("ofi", "ofi_zscore", "vpin", "order_imbalance", "rel_spread_mean", "microprice_dev")

    def test_columns_zero_without_path(self, tmp_path: Path):
        market_raw = _stage_market_raw(tmp_path, closes=_trending_then_choppy(80))
        rows = list(MarketFeaturesBuilder().iter_rows(
            market_raw_path=market_raw, vol_window_n=10, forward_window_m=5,
        ))
        assert rows
        for r in rows:
            for c in self._COLS:
                assert c in r and r[c] == 0.0

    def test_columns_populated_with_path(self, tmp_path: Path):
        market_raw = _stage_market_raw(
            tmp_path, closes=[100.0 * (1.001 ** i) for i in range(200)], bar_seconds=300,
            timeframe="5m",
        )
        micro = _stage_microstructure(tmp_path, n_bars=200)
        rows = list(MarketFeaturesBuilder().iter_rows(
            market_raw_path=market_raw, vol_window_n=10, forward_window_m=5,
            microstructure_path=micro, microstructure_window_n=20,
        ))
        assert rows
        assert any(r["ofi"] != 0.0 for r in rows)
        assert all(0.0 <= r["vpin"] <= 1.0 for r in rows)
        # buy_vol >= sell_vol in the fixture → non-negative order imbalance.
        assert all(r["order_imbalance"] >= 0.0 for r in rows)

    def test_asof_alignment_past_only(self, tmp_path: Path):
        # Side-stream entirely AFTER the bars → no bar may see it (carry-forward 0).
        market_raw = _stage_market_raw(tmp_path, closes=[100.0] * 120, bar_seconds=300, timeframe="5m")
        micro = _stage_microstructure(tmp_path, base_ts_iso="2030-01-01T00:00:00Z", n_bars=120)
        rows = list(MarketFeaturesBuilder().iter_rows(
            market_raw_path=market_raw, vol_window_n=10, forward_window_m=5,
            microstructure_path=micro, microstructure_window_n=20,
        ))
        assert rows
        assert all(r["ofi"] == 0.0 and r["vpin"] == 0.0 for r in rows)

    def test_in_schema_and_validate(self, tmp_path: Path):
        market_raw = _stage_market_raw(
            tmp_path, closes=[100.0 * (1.001 ** i) for i in range(160)], bar_seconds=300,
            timeframe="5m",
        )
        micro = _stage_microstructure(tmp_path, n_bars=160)
        builder = MarketFeaturesBuilder()
        for c in self._COLS:
            assert c in builder.schema
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001", source="csv",
            symbol_scope="BTCUSDT", timeframe="5m",
            market_raw_path=market_raw, vol_window_n=10, forward_window_m=5,
            microstructure_path=str(micro), microstructure_window_n=20,
        )
        report = validate_dataset(paths.root)
        assert report.ok, report

    def test_invalid_window_raises(self, tmp_path: Path):
        market_raw = _stage_market_raw(tmp_path, closes=[100.0] * 100)
        with pytest.raises(ValueError):
            list(MarketFeaturesBuilder().iter_rows(
                market_raw_path=market_raw, microstructure_window_n=1,
            ))


def _stage_macro(
    tmp_path: Path,
    *,
    base_ts_iso: str = "2025-01-01T00:00:00Z",
    n_bars: int = 200,
    bar_seconds: int = 300,
) -> Path:
    """Write a synthetic daily macro side-stream dir (data.jsonl).

    Rows are the pre-computed, one-day-lagged feature columns the producer emits.
    Stamps daily rows across the bar span so the as-of carry-forward populates.
    """
    from datetime import datetime, timedelta

    base = datetime.fromisoformat(base_ts_iso.replace("Z", "+00:00"))
    root = tmp_path / "macro" / "MES" / "v001"
    root.mkdir(parents=True, exist_ok=True)
    span_seconds = bar_seconds * n_bars
    rows = []
    day = 0
    t = 0
    while t <= span_seconds:
        ts = (base + timedelta(seconds=t)).isoformat().replace("+00:00", "Z")
        rows.append({
            "ts": ts,
            "vix_level": 15.0 + (day % 5),
            "vix_zscore": float((day % 7) - 3),
            "vix_term_slope": 0.01 * ((day % 3) - 1),
            "dxy_zscore": float((day % 5) - 2),
            "dxy_return": 0.001 * ((day % 4) - 1),
            "ust10y_level": 40.0 + (day % 6) * 0.1,
            "ust_slope_3m10y": 1.0 + 0.1 * (day % 3),
        })
        day += 1
        t += 86400
    (root / "data.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return root


def _stage_cross_asset(
    tmp_path: Path,
    *,
    base_ts_iso: str = "2025-01-01T00:00:00Z",
    n_bars: int = 80,
    bar_seconds: int = 3600,
) -> Path:
    """Write a synthetic cross_asset side-stream keyed at the bar grid.

    Uses the producer's own pure function so the test exercises the real
    column set + emit shape (peer1/peer2 rising peers + a target).
    """
    from datetime import datetime, timedelta

    from ml.datasets.cross_asset_features import compute_cross_asset_feature_rows

    base = datetime.fromisoformat(base_ts_iso.replace("Z", "+00:00"))

    def _bars(start: float, step: float):
        out = []
        for i in range(n_bars):
            ts = (base + timedelta(seconds=bar_seconds * i)).isoformat().replace(
                "+00:00", "Z")
            out.append({"ts": ts, "close": start + step * i})
        return out

    target = _bars(100.0, 1.0)
    peer1 = _bars(200.0, 1.3)
    peer2 = _bars(50.0, 0.7)
    rows = compute_cross_asset_feature_rows(
        target, [peer1, peer2], vol_window_n=5, beta_window_n=10)
    root = tmp_path / "cross_asset" / "ETHUSDT" / "1h" / "v001"
    root.mkdir(parents=True, exist_ok=True)
    (root / "data.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return root


class TestDirectionLabel:
    """S-CROSS-ASSET-PROBE step 3: directional forward label."""

    def test_binary_up_down_sign_default(self, tmp_path: Path):
        # Monotonic-up closes → forward return > 0 → every label "up".
        market_raw = _stage_market_raw(
            tmp_path, closes=[100.0 + i for i in range(60)])
        rows = list(MarketFeaturesBuilder().iter_rows(
            market_raw_path=market_raw, vol_window_n=10, forward_window_m=5))
        assert rows
        assert all(r["direction_label"] == "up" for r in rows)
        assert all("direction_label" in r for r in rows)

    def test_down_when_falling(self, tmp_path: Path):
        market_raw = _stage_market_raw(
            tmp_path, closes=[200.0 - i for i in range(60)])
        rows = list(MarketFeaturesBuilder().iter_rows(
            market_raw_path=market_raw, vol_window_n=10, forward_window_m=5))
        assert rows
        assert all(r["direction_label"] == "down" for r in rows)

    def test_deadband_emits_flat(self, tmp_path: Path):
        # A big dead-band swallows small moves into "flat".
        market_raw = _stage_market_raw(
            tmp_path, closes=[100.0 + (i % 2) * 0.01 for i in range(80)])
        rows = list(MarketFeaturesBuilder().iter_rows(
            market_raw_path=market_raw, vol_window_n=10, forward_window_m=5,
            direction_threshold=0.5))
        assert rows
        assert any(r["direction_label"] == "flat" for r in rows)

    def test_in_schema(self):
        assert MarketFeaturesBuilder.schema["direction_label"] is str


class TestCrossAssetFeatures:
    """S-CROSS-ASSET-PROBE: peer-asset conditioning columns (ETH ← BTC/SOL)."""

    from ml.datasets.cross_asset_features import CROSS_ASSET_FEATURE_COLUMNS as _COLS

    def test_columns_zero_without_cross_asset_path(self, tmp_path: Path):
        market_raw = _stage_market_raw(tmp_path, closes=_trending_then_choppy(60))
        rows = list(MarketFeaturesBuilder().iter_rows(
            market_raw_path=market_raw, vol_window_n=10, forward_window_m=5,
        ))
        assert rows
        for r in rows:
            for c in self._COLS:
                assert c in r and r[c] == 0.0

    def test_columns_populated_with_cross_asset_path(self, tmp_path: Path):
        market_raw = _stage_market_raw(
            tmp_path, symbol="ETHUSDT", closes=[100.0 + i for i in range(80)])
        xa = _stage_cross_asset(tmp_path, n_bars=80)
        rows = list(MarketFeaturesBuilder().iter_rows(
            market_raw_path=market_raw, vol_window_n=10, forward_window_m=5,
            cross_asset_path=xa,
        ))
        assert rows
        assert any(r["xa_peer1_beta"] != 0.0 for r in rows)
        assert any(r["xa_peer2_ret"] != 0.0 for r in rows)
        assert any(r["xa_breadth_up"] > 0 for r in rows)

    def test_asof_alignment_past_only(self, tmp_path: Path):
        # Side-stream entirely AFTER the bars → no bar may see it (carry 0).
        market_raw = _stage_market_raw(
            tmp_path, symbol="ETHUSDT", closes=[100.0] * 80)
        xa = _stage_cross_asset(
            tmp_path, base_ts_iso="2030-01-01T00:00:00Z", n_bars=80)
        rows = list(MarketFeaturesBuilder().iter_rows(
            market_raw_path=market_raw, vol_window_n=10, forward_window_m=5,
            cross_asset_path=xa,
        ))
        assert rows
        assert all(r["xa_peer1_beta"] == 0.0 and r["xa_peer2_ret"] == 0.0
                   for r in rows)

    def test_in_schema(self, tmp_path: Path):
        builder = MarketFeaturesBuilder()
        for c in self._COLS:
            assert c in builder.schema


class TestMacroFeatures:
    """S-MLOPT-S12: cross-asset/macro conditioning columns (MES focus)."""

    _COLS = (
        "vix_level", "vix_zscore", "vix_term_slope",
        "dxy_zscore", "dxy_return", "ust10y_level", "ust_slope_3m10y",
    )

    def test_columns_zero_without_macro_path(self, tmp_path: Path):
        market_raw = _stage_market_raw(tmp_path, closes=_trending_then_choppy(80))
        rows = list(MarketFeaturesBuilder().iter_rows(
            market_raw_path=market_raw, vol_window_n=10, forward_window_m=5,
        ))
        assert rows
        for r in rows:
            for c in self._COLS:
                assert c in r and r[c] == 0.0

    def test_columns_populated_with_macro_path(self, tmp_path: Path):
        market_raw = _stage_market_raw(
            tmp_path, closes=[100.0 * (1.001 ** i) for i in range(200)],
            bar_seconds=300, timeframe="5m",
        )
        macro = _stage_macro(tmp_path, n_bars=200)
        rows = list(MarketFeaturesBuilder().iter_rows(
            market_raw_path=market_raw, vol_window_n=10, forward_window_m=5,
            macro_path=macro,
        ))
        assert rows
        # vix_level carried forward as-of (non-zero somewhere).
        assert any(r["vix_level"] > 0 for r in rows)
        assert any(r["ust10y_level"] > 0 for r in rows)

    def test_asof_alignment_past_only(self, tmp_path: Path):
        # Macro entirely AFTER the bars → no bar may see it (carry-forward 0).
        market_raw = _stage_market_raw(
            tmp_path, closes=[100.0] * 120, bar_seconds=300, timeframe="5m",
        )
        macro = _stage_macro(tmp_path, base_ts_iso="2030-01-01T00:00:00Z", n_bars=120)
        rows = list(MarketFeaturesBuilder().iter_rows(
            market_raw_path=market_raw, vol_window_n=10, forward_window_m=5,
            macro_path=macro,
        ))
        assert rows
        assert all(r["vix_level"] == 0.0 and r["ust10y_level"] == 0.0 for r in rows)

    def test_in_schema_and_validate(self, tmp_path: Path):
        market_raw = _stage_market_raw(
            tmp_path, closes=[100.0 * (1.001 ** i) for i in range(160)],
            bar_seconds=300, timeframe="5m",
        )
        macro = _stage_macro(tmp_path, n_bars=160)
        builder = MarketFeaturesBuilder()
        for c in self._COLS:
            assert c in builder.schema
        paths = builder.build(
            output_dir=tmp_path / "out", version="v001", source="csv",
            symbol_scope="MES", timeframe="5m",
            market_raw_path=market_raw, vol_window_n=10, forward_window_m=5,
            macro_path=str(macro),
        )
        report = validate_dataset(paths.root)
        assert report.ok, report
