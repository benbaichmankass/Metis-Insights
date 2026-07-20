"""Tests for the registry-change fingerprint + the cache invalidation it
gates (src/runtime/registry_fingerprint.py; MB follow-up to the M25
promotion needing a restart-bot-service purely to rotate the per-process
predictor caches, 2026-07-20)."""
from __future__ import annotations

import json
import os
import time

from src.runtime.registry_fingerprint import registry_fingerprint


def _bump(path, seconds: float = 5.0) -> None:
    t = time.time() + seconds
    os.utime(path, (t, t))


class TestRegistryFingerprint:
    def test_changes_when_a_registry_json_mtime_changes(self, tmp_path):
        f = tmp_path / "model-a.json"
        f.write_text(json.dumps({"model_id": "a"}))
        fp1 = registry_fingerprint(tmp_path)
        _bump(f)
        fp2 = registry_fingerprint(tmp_path)
        assert fp2 > fp1

    def test_changes_when_a_file_is_added(self, tmp_path):
        (tmp_path / "model-a.json").write_text("{}")
        fp1 = registry_fingerprint(tmp_path)
        g = tmp_path / "model-b.json"
        g.write_text("{}")
        _bump(g)
        assert registry_fingerprint(tmp_path) > fp1

    def test_stable_when_nothing_changes(self, tmp_path):
        (tmp_path / "model-a.json").write_text("{}")
        assert registry_fingerprint(tmp_path) == registry_fingerprint(tmp_path)

    def test_missing_root_returns_stable_sentinel(self, tmp_path):
        missing = tmp_path / "nope"
        assert registry_fingerprint(missing) == -1.0
        assert registry_fingerprint(None) == -1.0


class TestPredictorCacheInvalidation:
    def _registry_with_one_entry(self, tmp_path):
        # Minimal real registry the resolver can list.
        from ml.registry.model_registry import ModelRegistry

        state = tmp_path / "model_state.json"
        state.write_text(json.dumps({
            "trainer": "ml.trainers.constant_baseline.ConstantPredictionTrainer",
            "constant": 0.5,
        }))
        root = tmp_path / "registry-store"
        reg = ModelRegistry(root)
        reg.register(
            model_id="fp-test-head-v1",
            manifest={
                "manifest_version": "v1", "model_id": "fp-test-head-v1",
                "model_family": "regime",
                "trainer": "ml.trainers.constant_baseline.ConstantPredictionTrainer",
                "trainer_config": {"target_column": "y"},
                "dataset": {"family": "market_features",
                            "symbol_scope": "BTCUSDT", "timeframe": "15m",
                            "version": "v1"},
                "evaluator": "ml.evaluators.regression.RegressionEvaluator",
                "evaluator_config": {"target_column": "y", "time_column": "ts"},
                "target_deployment_stage": "shadow",
            },
            model_state_path=str(state),
            metrics={"macro_f1": 0.6}, code_revision="x",
        )
        return root

    def test_registry_mtime_change_rotates_the_predictor_cache(
        self, tmp_path, monkeypatch,
    ):
        import src.runtime.regime_bar_scoring as rbs

        root = self._registry_with_one_entry(tmp_path)
        log = tmp_path / "shadow.jsonl"
        monkeypatch.setattr(rbs, "_PREDICTOR_CACHE", {})

        first = rbs._resolve_regime_predictors(root, log)
        assert len(first) == 1
        # Same fingerprint → the cached OBJECTS come back (no re-resolve).
        again = rbs._resolve_regime_predictors(root, log)
        assert again is first

        # A registry rewrite (what the mirror publish does on a promotion)
        # bumps the fingerprint → fresh resolution, old entry pruned.
        for f in root.glob("*.json"):
            _bump(f)
        fresh = rbs._resolve_regime_predictors(root, log)
        assert fresh is not first
        assert len(rbs._PREDICTOR_CACHE) == 1  # superseded entry released


class TestAdvisorySpecCacheInvalidation:
    def test_fingerprint_change_re_resolves_advisory_specs(
        self, tmp_path, monkeypatch,
    ):
        import src.runtime.regime.ml_vol_verdict as mvv

        root = tmp_path / "registry-store"
        root.mkdir()
        (root / "m.json").write_text("{}")
        monkeypatch.setattr("ml.shadow.factory.DEFAULT_REGISTRY_ROOT", root)

        calls = {"n": 0}

        class _FakeRegistry:
            def __init__(self, *a, **k):
                calls["n"] += 1

            def list(self):
                return []

        monkeypatch.setattr(mvv, "_ADVISORY_SPEC_CACHE", None)
        monkeypatch.setattr(
            "ml.registry.model_registry.ModelRegistry", _FakeRegistry,
        )
        mvv.discover_advisory_stage_regime_specs()
        mvv.discover_advisory_stage_regime_specs()
        assert calls["n"] == 1  # same fingerprint → cached

        t = time.time() + 9.0
        os.utime(root / "m.json", (t, t))
        mvv.discover_advisory_stage_regime_specs()
        assert calls["n"] == 2  # fingerprint moved → re-resolved
