"""Tests for ict_scalp.order_package shadow-mode integration (WS7).

Mirrors tests/test_turtle_soup_shadow.py — same surface, different
strategy. Verifies:

- Package keys are unchanged when no shadow predictor is wired.
- Singular ``_shadow_predictor`` (PART-3 backwards-compat) still works.
- Plural ``_shadow_predictors`` injection runs every predictor.
- Config-driven ``shadow_model_ids`` resolves through the registry-
  backed factory and runs concurrent predictors with per-tick
  audit lines.
- A failing predictor never breaks the package or other predictors.
- Stage gate refuses unpromoted models without crashing the tick.
- ``_build_shadow_feature_row`` emits the shared WS5 surface plus
  the three ict_scalp-specific features (sweep_depth_atr,
  fvg_size_norm, displacement_idx_from_end).
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any, Mapping
from unittest import mock

import pytest

if "matplotlib" not in sys.modules:
    _mpl_stub = types.ModuleType("matplotlib")
    _mpl_stub.pyplot = mock.MagicMock()
    sys.modules["matplotlib"] = _mpl_stub
    sys.modules["matplotlib.pyplot"] = mock.MagicMock()

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")

from ml.predictors import ConstantPredictor, Predictor, ShadowPredictor  # noqa: E402
from src.units.strategies.ict_scalp import (  # noqa: E402
    _build_shadow_feature_row,
    order_package,
)


# ---------------------------------------------------------------------------
# Fixtures — identical to test_ict_scalp_5m.py so both suites stay aligned.
# ---------------------------------------------------------------------------


def _flat_frame(n: int = 80, base: float = 50_000.0, freq: str = "5min") -> pd.DataFrame:
    rng = pd.date_range("2026-04-01", periods=n, freq=freq, tz="UTC")
    return pd.DataFrame(
        {
            "open":   np.full(n, base + 25.0),
            "high":   np.full(n, base + 50.0),
            "low":    np.full(n, base - 50.0),
            "close":  np.full(n, base + 30.0),
            "volume": np.full(n, 1.0),
        },
        index=rng,
    )


def _bullish_scalp_frame(freq: str = "5min") -> pd.DataFrame:
    """Clean bullish ICT-scalp setup that passes all four gates.

    Sweep at bar n-5, displacement at n-4, FVG between n-5 and n-3,
    wick-rejection mitigation at the last bar (n-1).
    """
    n = 80
    base = 50_000.0
    df = _flat_frame(n, base, freq).copy()

    sweep_idx = n - 5
    df.iloc[sweep_idx, df.columns.get_loc("open")]  = base - 30.0
    df.iloc[sweep_idx, df.columns.get_loc("high")]  = base + 10.0
    df.iloc[sweep_idx, df.columns.get_loc("low")]   = base - 300.0
    df.iloc[sweep_idx, df.columns.get_loc("close")] = base + 5.0

    disp_idx = n - 4
    df.iloc[disp_idx, df.columns.get_loc("open")]  = base + 10.0
    df.iloc[disp_idx, df.columns.get_loc("high")]  = base + 320.0
    df.iloc[disp_idx, df.columns.get_loc("low")]   = base + 5.0
    df.iloc[disp_idx, df.columns.get_loc("close")] = base + 300.0

    fvg_after_idx = n - 3
    df.iloc[fvg_after_idx, df.columns.get_loc("open")]  = base + 305.0
    df.iloc[fvg_after_idx, df.columns.get_loc("high")]  = base + 500.0
    df.iloc[fvg_after_idx, df.columns.get_loc("low")]   = base + 340.0
    df.iloc[fvg_after_idx, df.columns.get_loc("close")] = base + 480.0

    cont_idx = n - 2
    df.iloc[cont_idx, df.columns.get_loc("open")]  = base + 470.0
    df.iloc[cont_idx, df.columns.get_loc("high")]  = base + 520.0
    df.iloc[cont_idx, df.columns.get_loc("low")]   = base + 350.0
    df.iloc[cont_idx, df.columns.get_loc("close")] = base + 500.0

    last_idx = n - 1
    df.iloc[last_idx, df.columns.get_loc("open")]  = base + 410.0
    df.iloc[last_idx, df.columns.get_loc("high")]  = base + 460.0
    df.iloc[last_idx, df.columns.get_loc("low")]   = base + 330.0
    df.iloc[last_idx, df.columns.get_loc("close")] = base + 450.0
    return df


# ---------------------------------------------------------------------------
# Helper predictors
# ---------------------------------------------------------------------------


class _CountingPredictor(Predictor):
    def __init__(self, score: float = 0.5) -> None:
        self._score = score
        self.calls: list[Mapping[str, Any]] = []

    def predict(self, row: Mapping[str, Any]) -> float:
        self.calls.append(dict(row))
        return self._score


class _BrokenPredictor(Predictor):
    def predict(self, row: Mapping[str, Any]) -> float:
        raise RuntimeError("model state corrupted")


_PACKAGE_KEYS = sorted(
    ["symbol", "direction", "entry", "sl", "tp", "confidence", "meta"]
)


# ---------------------------------------------------------------------------
# Integration: shadow wiring in order_package
# ---------------------------------------------------------------------------


class TestIctScalpShadowIntegration:
    def test_no_predictor_keys_unchanged(self):
        package = order_package({"symbol": "BTCUSDT"}, _bullish_scalp_frame())
        assert sorted(package.keys()) == _PACKAGE_KEYS
        assert "shadow_score" not in package
        assert "model_id" not in package

    def test_singular_predictor_called(self, tmp_path: Path):
        inner = _CountingPredictor(score=0.42)
        predictor = ShadowPredictor(
            inner, model_id="ict-shadow-v0", stage="shadow",
            log_path=tmp_path / "audit.jsonl",
        )
        package = order_package(
            {"symbol": "BTCUSDT", "_shadow_predictor": predictor},
            _bullish_scalp_frame(),
        )
        assert sorted(package.keys()) == _PACKAGE_KEYS
        assert len(inner.calls) == 1
        row = inner.calls[0]
        assert row["strategy_name"] == "ict_scalp_5m"
        assert row["direction"] == package["direction"]
        assert row["confidence"] == package["confidence"]
        # ict_scalp-specific features must be present.
        assert "sweep_depth_atr" in row
        assert "fvg_size_norm" in row
        assert "displacement_idx_from_end" in row
        # Outcome columns must NOT leak into the feature row.
        assert "pnl" not in row
        assert "r_multiple" not in row

    def test_audit_log_emitted(self, tmp_path: Path):
        log = tmp_path / "audit.jsonl"
        predictor = ShadowPredictor(
            ConstantPredictor(state={"constant": 0.5}),
            model_id="ict-shadow-v0", stage="shadow", log_path=log,
        )
        order_package(
            {"symbol": "BTCUSDT", "_shadow_predictor": predictor},
            _bullish_scalp_frame(),
        )
        lines = log.read_text().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["model_id"] == "ict-shadow-v0"
        assert record["stage"] == "shadow"
        assert "score" in record

    def test_broken_predictor_does_not_crash(self, tmp_path: Path):
        broken = ShadowPredictor(
            _BrokenPredictor(), model_id="ict-broken", stage="shadow",
            log_path=tmp_path / "broken.jsonl",
        )
        package = order_package(
            {"symbol": "BTCUSDT", "_shadow_predictor": broken},
            _bullish_scalp_frame(),
        )
        assert sorted(package.keys()) == _PACKAGE_KEYS

    def test_score_does_not_influence_package(self, tmp_path: Path):
        """WS7 non-negotiable: shadow score is audit-only.
        A predictor returning 0.0 and one returning 1.0 must yield
        byte-identical packages."""
        baseline = order_package({"symbol": "BTCUSDT"}, _bullish_scalp_frame())

        for score in (0.0, 1.0):
            pred = ShadowPredictor(
                ConstantPredictor(state={"constant": score}),
                model_id=f"ict-const-{score}", stage="shadow",
                log_path=tmp_path / f"log_{score}.jsonl",
            )
            pkg = order_package(
                {"symbol": "BTCUSDT", "_shadow_predictor": pred},
                _bullish_scalp_frame(),
            )
            assert pkg == baseline


# ---------------------------------------------------------------------------
# Config-driven multi-predictor (registry path)
# ---------------------------------------------------------------------------


class TestIctScalpConfigDrivenShadow:
    def _register_model(
        self,
        root: Path,
        model_id: str,
        stage: str = "shadow",
    ) -> Path:
        from ml.registry.model_registry import ModelRegistry

        state_path = root / f"{model_id}_state.json"
        state_path.write_text(
            json.dumps(
                {
                    "trainer": "ml.trainers.constant_baseline."
                               "ConstantPredictionTrainer",
                    "constant": 0.5,
                }
            )
        )
        registry_root = root / "registry-store"
        registry = ModelRegistry(registry_root)
        registry.register(
            model_id=model_id,
            manifest={"manifest_version": "v1"},
            model_state_path=str(state_path),
            metrics={"mae": 0.1},
            code_revision="x",
        )
        ladder = [
            "candidate", "backtest_approved", "shadow",
            "advisory", "limited_live", "live_approved",
        ]
        for step in ladder:
            registry.promote_stage(
                model_id, step, by="op", reason=f"to-{step}",
            )
            if step == stage:
                break
        return registry_root

    def test_shadow_model_ids_resolves_three_concurrently(
        self, tmp_path: Path
    ):
        registry_root = self._register_model(tmp_path, "wr-baseline")
        self._register_model(tmp_path, "rmult-baseline")
        self._register_model(tmp_path, "slip-baseline")
        log_path = tmp_path / "shadow_audit.jsonl"
        cfg = {
            "symbol": "BTCUSDT",
            "shadow_model_ids": [
                "wr-baseline", "rmult-baseline", "slip-baseline",
            ],
            "_shadow_registry_root": str(registry_root),
            "_shadow_log_path": str(log_path),
        }
        package = order_package(cfg, _bullish_scalp_frame())
        assert sorted(package.keys()) == _PACKAGE_KEYS
        lines = [
            json.loads(line)
            for line in log_path.read_text().splitlines()
            if line
        ]
        assert len(lines) == 3
        assert {e["model_id"] for e in lines} == {
            "wr-baseline", "rmult-baseline", "slip-baseline",
        }

    def test_unpromoted_model_skipped_not_crashed(self, tmp_path: Path):
        registry_root = self._register_model(tmp_path, "ok-shadow")
        self._register_model(tmp_path, "stuck-research", stage="research_only")
        log_path = tmp_path / "shadow_audit.jsonl"
        cfg = {
            "symbol": "BTCUSDT",
            "shadow_model_ids": ["ok-shadow", "stuck-research"],
            "_shadow_registry_root": str(registry_root),
            "_shadow_log_path": str(log_path),
        }
        package = order_package(cfg, _bullish_scalp_frame())
        assert sorted(package.keys()) == _PACKAGE_KEYS
        lines = [
            json.loads(line)
            for line in log_path.read_text().splitlines()
            if line
        ]
        assert len(lines) == 1
        assert lines[0]["model_id"] == "ok-shadow"

    def test_empty_shadow_model_ids_no_op(self):
        cfg = {"symbol": "BTCUSDT", "shadow_model_ids": []}
        package = order_package(cfg, _bullish_scalp_frame())
        assert sorted(package.keys()) == _PACKAGE_KEYS

    def test_plural_injection_path(self, tmp_path: Path):
        log_a = tmp_path / "a.jsonl"
        log_b = tmp_path / "b.jsonl"
        a = ShadowPredictor(
            ConstantPredictor(state={"constant": 0.1}),
            model_id="m-a", stage="shadow", log_path=log_a,
        )
        b = ShadowPredictor(
            ConstantPredictor(state={"constant": 0.9}),
            model_id="m-b", stage="shadow", log_path=log_b,
        )
        cfg = {"symbol": "BTCUSDT", "_shadow_predictors": [a, b]}
        order_package(cfg, _bullish_scalp_frame())
        assert len(log_a.read_text().splitlines()) == 1
        assert len(log_b.read_text().splitlines()) == 1


# ---------------------------------------------------------------------------
# _build_shadow_feature_row unit tests
# ---------------------------------------------------------------------------


class TestBuildShadowFeatureRow:
    def test_includes_shared_and_ict_scalp_specific_fields(self):
        package = {
            "symbol": "BTCUSDT",
            "direction": "long",
            "confidence": 0.72,
            "meta": {
                "atr": 200.0,
                "displacement_body_to_range": 0.78,
                "setup_tf": "5m",
                "timeframe": "5m",
                "sweep_extreme": 49_700.0,
                "sweep_level": 50_000.0,
                "fvg_size": 100.0,
                "displacement_idx_from_end": 3,
            },
        }
        row = _build_shadow_feature_row(package)
        # Shared WS5 surface.
        assert row["strategy_name"] == "ict_scalp_5m"
        assert row["direction"] == "long"
        assert row["confidence"] == pytest.approx(0.72)
        assert row["atr"] == pytest.approx(200.0)
        assert row["body_to_range"] == pytest.approx(0.78)
        assert row["setup_type"] == "5m"
        assert row["timeframe"] == "5m"
        # ict_scalp-specific features.
        # sweep_depth_atr = abs(49700 - 50000) / 200 = 1.5
        assert row["sweep_depth_atr"] == pytest.approx(1.5)
        # fvg_size_norm = min(100 / 200, 1.0) = 0.5
        assert row["fvg_size_norm"] == pytest.approx(0.5)
        assert row["displacement_idx_from_end"] == 3
        # Outcome columns must be absent.
        assert "pnl" not in row
        assert "r_multiple" not in row

    def test_fvg_size_norm_capped_at_one(self):
        package = {
            "direction": "long",
            "confidence": 0.5,
            "meta": {
                "atr": 100.0,
                "fvg_size": 500.0,  # 5x ATR → would be 5.0 uncapped
                "sweep_extreme": 0.0,
                "sweep_level": 0.0,
            },
        }
        row = _build_shadow_feature_row(package)
        assert row["fvg_size_norm"] == pytest.approx(1.0)

    def test_missing_meta_handled(self):
        row = _build_shadow_feature_row(
            {"direction": "long", "confidence": 0.5}
        )
        assert row["atr"] == 0.0
        assert row["body_to_range"] == 0.0
        assert row["sweep_depth_atr"] == 0.0
        assert row["fvg_size_norm"] == 0.0
        assert row["displacement_idx_from_end"] == 0
        assert row["setup_type"] == ""
        assert row["timeframe"] == ""

    def test_zero_atr_does_not_divide_by_zero(self):
        package = {
            "direction": "long",
            "confidence": 0.5,
            "meta": {
                "atr": 0.0,
                "sweep_extreme": 49_500.0,
                "sweep_level": 50_000.0,
                "fvg_size": 50.0,
            },
        }
        row = _build_shadow_feature_row(package)
        assert row["sweep_depth_atr"] == 0.0
        assert row["fvg_size_norm"] == 0.0
