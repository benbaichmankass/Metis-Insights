"""Tests for `ShadowPredictor` (S-AI-WS7-PART-1)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from ml.predictors import Predictor, ShadowPredictor


class _FakePredictor(Predictor):
    """Returns a fixed score, records every call."""

    def __init__(self, score: float) -> None:
        self._score = score
        self.calls: list[Mapping[str, Any]] = []

    def predict(self, row: Mapping[str, Any]) -> float:
        self.calls.append(dict(row))
        return self._score


class TestShadowPredictor:
    def test_returns_wrapped_score(self):
        inner = _FakePredictor(score=2.713)
        shadow = ShadowPredictor(
            inner, model_id="m-1", stage="shadow",
        )
        assert shadow.predict({"x": 1, "y": 2}) == pytest.approx(2.713)
        assert inner.calls == [{"x": 1, "y": 2}]

    def test_appends_one_jsonl_per_call(self, tmp_path: Path):
        log = tmp_path / "audit.jsonl"
        inner = _FakePredictor(score=1.5)
        shadow = ShadowPredictor(
            inner, model_id="m-1", stage="shadow", log_path=log,
        )
        shadow.predict({"strategy_name": "vwap", "setup_type": "FVG"})
        shadow.predict({"strategy_name": "turtle", "setup_type": "OB"})
        lines = [
            json.loads(line)
            for line in log.read_text().splitlines()
            if line
        ]
        assert len(lines) == 2
        for entry in lines:
            assert entry["model_id"] == "m-1"
            assert entry["stage"] == "shadow"
            assert entry["score"] == pytest.approx(1.5)
            assert "predicted_at_utc" in entry
        assert lines[0]["row_keys"] == ["setup_type", "strategy_name"]
        assert lines[1]["row_keys"] == ["setup_type", "strategy_name"]

    def test_does_not_capture_row_values(self, tmp_path: Path):
        # The audit log records row KEYS only, not values. Operators
        # shouldn't be able to accidentally leak account state or
        # PII into the audit by virtue of the model seeing it.
        log = tmp_path / "audit.jsonl"
        inner = _FakePredictor(score=0.5)
        shadow = ShadowPredictor(
            inner, model_id="m-1", stage="shadow", log_path=log,
        )
        secret = "VELOTRADE_API_KEY_SECRET_DO_NOT_LOG"
        shadow.predict({"public_feature": 1, "api_key": secret})
        contents = log.read_text()
        assert secret not in contents

    def test_creates_parent_dir(self, tmp_path: Path):
        log = tmp_path / "nested" / "deep" / "audit.jsonl"
        inner = _FakePredictor(score=1.0)
        shadow = ShadowPredictor(
            inner, model_id="m-1", stage="shadow", log_path=log,
        )
        shadow.predict({"x": 1})
        assert log.is_file()

    def test_no_log_path_runs_silently(self, tmp_path: Path):
        # log_path=None — no IO, no errors, just return the score.
        inner = _FakePredictor(score=3.14)
        shadow = ShadowPredictor(
            inner, model_id="m-1", stage="shadow", log_path=None,
        )
        result = shadow.predict({"x": 1})
        assert result == pytest.approx(3.14)
        # tmp_path stays empty.
        assert sorted(tmp_path.iterdir()) == []

    def test_invalid_stage_rejected(self):
        with pytest.raises(ValueError, match="stage"):
            ShadowPredictor(
                _FakePredictor(score=1.0),
                model_id="m-1", stage="made-up",
            )

    def test_blank_model_id_rejected(self):
        with pytest.raises(ValueError, match="model_id"):
            ShadowPredictor(
                _FakePredictor(score=1.0),
                model_id="   ", stage="shadow",
            )

    def test_non_predictor_wrapped_rejected(self):
        class _Bogus:
            def predict(self, row):
                return 1.0

        with pytest.raises(TypeError, match="Predictor"):
            ShadowPredictor(
                _Bogus(),  # type: ignore[arg-type]
                model_id="m-1", stage="shadow",
            )

    def test_score_coerced_to_float(self):
        class _IntPredictor(Predictor):
            def predict(self, row):
                return 7  # int, not float

        shadow = ShadowPredictor(
            _IntPredictor(), model_id="m-1", stage="shadow",
        )
        result = shadow.predict({"x": 1})
        assert isinstance(result, float)
        assert result == 7.0

    def test_accepts_every_valid_stage(self, tmp_path: Path):
        from ml.manifest import VALID_DEPLOYMENT_STAGES

        for stage in VALID_DEPLOYMENT_STAGES:
            shadow = ShadowPredictor(
                _FakePredictor(score=1.0),
                model_id=f"m-{stage}",
                stage=stage,
                log_path=tmp_path / f"{stage}.jsonl",
            )
            assert shadow.predict({"x": 1}) == pytest.approx(1.0)
            assert shadow.stage == stage

    def test_exposes_model_id_and_stage(self):
        shadow = ShadowPredictor(
            _FakePredictor(score=1.0),
            model_id="m-1", stage="shadow",
        )
        assert shadow.model_id == "m-1"
        assert shadow.stage == "shadow"

    def test_appends_to_existing_log(self, tmp_path: Path):
        log = tmp_path / "audit.jsonl"
        log.write_text(json.dumps({"prior": "entry"}) + "\n")
        inner = _FakePredictor(score=0.5)
        shadow = ShadowPredictor(
            inner, model_id="m-1", stage="shadow", log_path=log,
        )
        shadow.predict({"x": 1})
        lines = [
            json.loads(line)
            for line in log.read_text().splitlines()
            if line
        ]
        assert len(lines) == 2
        assert lines[0] == {"prior": "entry"}
