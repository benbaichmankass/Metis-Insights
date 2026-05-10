"""Tests for `ml.predictors.external` (S-AI-WS6-PART-1).

The module ships an ABC and an exception type. Tests verify:

- `ExternalPredictor` is abstract and refuses direct instantiation.
- A concrete subclass that implements `predict` + `describe`
  works as a `Predictor` (drop-in for the shadow harness).
- `ProviderError` carries provider + model_identifier metadata.
- The shadow harness wraps an `ExternalPredictor` correctly
  (defense-in-depth — the WS6 framework must compose with WS7).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from ml.predictors.base import Predictor
from ml.predictors.external import ExternalPredictor, ProviderError
from ml.predictors.shadow import ShadowPredictor


class _ConcretePredictor(ExternalPredictor):
    provider = "test-fixture"
    model_identifier = "test-fixture/dummy@v0"

    def __init__(self, score: float = 0.42) -> None:
        self._score = score
        self.calls: list[Mapping[str, Any]] = []

    def predict(self, row: Mapping[str, Any]) -> float:
        self.calls.append(dict(row))
        return self._score

    def describe(self) -> str:
        return f"{self.provider}:{self.model_identifier}"


class _BrokenPredictor(ExternalPredictor):
    provider = "broken"
    model_identifier = "broken/x@v0"

    def predict(self, row: Mapping[str, Any]) -> float:
        raise ProviderError(
            "backing model unreachable",
            provider=self.provider,
            model_identifier=self.model_identifier,
        )

    def describe(self) -> str:
        return "broken"


class TestAbstract:
    def test_cannot_instantiate_abc_directly(self):
        with pytest.raises(TypeError):
            ExternalPredictor()  # type: ignore[abstract]

    def test_subclass_missing_predict_cannot_instantiate(self):
        class _NoPredict(ExternalPredictor):
            provider = "x"
            model_identifier = "y"

            def describe(self) -> str:
                return "x"

        with pytest.raises(TypeError):
            _NoPredict()  # type: ignore[abstract]

    def test_subclass_missing_describe_cannot_instantiate(self):
        class _NoDescribe(ExternalPredictor):
            provider = "x"
            model_identifier = "y"

            def predict(self, row):
                return 0.0

        with pytest.raises(TypeError):
            _NoDescribe()  # type: ignore[abstract]


class TestConcreteSubclass:
    def test_predict_returns_value(self):
        p = _ConcretePredictor(score=0.7)
        assert p.predict({"k": "v"}) == 0.7
        assert p.calls == [{"k": "v"}]

    def test_is_a_predictor(self):
        p = _ConcretePredictor()
        assert isinstance(p, Predictor)
        assert isinstance(p, ExternalPredictor)

    def test_describe_human_readable(self):
        p = _ConcretePredictor()
        assert "test-fixture" in p.describe()
        assert "dummy" in p.describe()

    def test_repr_pins_provider_and_model(self):
        p = _ConcretePredictor()
        r = repr(p)
        assert "test-fixture" in r
        assert "test-fixture/dummy@v0" in r


class TestProviderError:
    def test_carries_metadata(self):
        exc = ProviderError(
            "bad", provider="hf", model_identifier="distilbert@v1",
        )
        assert str(exc) == "bad"
        assert exc.provider == "hf"
        assert exc.model_identifier == "distilbert@v1"

    def test_is_runtime_error(self):
        # ProviderError must be catchable as RuntimeError so the
        # shadow harness' bare `except Exception` continues to work.
        try:
            raise ProviderError("x")
        except RuntimeError:
            assert True
        else:
            pytest.fail("ProviderError was not catchable as RuntimeError")


class TestShadowHarnessComposition:
    """Defense-in-depth: an ExternalPredictor must drop in
    cleanly to the WS7 shadow harness."""

    def test_wrap_in_shadow_predictor(self, tmp_path: Path):
        inner = _ConcretePredictor(score=0.9)
        wrapped = ShadowPredictor(
            inner,
            model_id="hf-test-v0",
            stage="shadow",
            log_path=tmp_path / "audit.jsonl",
        )
        assert wrapped.predict({"k": 1}) == 0.9
        line = (tmp_path / "audit.jsonl").read_text().strip()
        record = json.loads(line)
        assert record["model_id"] == "hf-test-v0"
        assert record["score"] == 0.9

    def test_broken_external_doesnt_crash_shadow_caller(self, tmp_path: Path):
        from src.runtime.shadow_adapter import with_shadow_preds

        inner = _BrokenPredictor()
        wrapped = ShadowPredictor(
            inner,
            model_id="broken-test-v0",
            stage="shadow",
            log_path=tmp_path / "audit.jsonl",
        )
        # The ProviderError raised inside predict() is caught by
        # with_shadow_preds; the caller's decision survives.
        sentinel = {"x": 1}
        result = with_shadow_preds(
            sentinel, predictors=[wrapped], feature_row={"k": 1},
        )
        assert result is sentinel
