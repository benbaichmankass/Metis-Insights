"""ReconstructionEvaluator + SSL-encoder manifest wiring (M19 T1.2 P1b).

No torch/onnxruntime needed — the evaluator reads floats already in the
model_state, and the manifest test only parses + resolves qualnames.
"""
from __future__ import annotations

import importlib
from pathlib import Path

from ml.evaluators.reconstruction import ReconstructionEvaluator
from ml.manifest import TrainingManifest

_MANIFEST = Path("ml/configs/corpus-ssl-encoder-mae-v1.yaml")


# --- the evaluator ---------------------------------------------------------- #
def test_score_surfaces_recorded_diagnostics():
    state = {
        "trainer": "ml.trainers.ssl_corpus_encoder.SSLCorpusEncoderTrainer",
        "reconstruction_loss": 0.42,
        "val_loss": 0.55,
        "parity_max_abs_diff": 1.2e-6,
        "embedding_dim": 16,
        "n_windows": 2400,
        "n_train_windows": 1920,
        "n_series": 18,
    }
    out = ReconstructionEvaluator().score(state, [{"date": "2020-01-02"}] * 3, {})
    assert out["n_eval"] == 3.0
    assert out["reconstruction_loss"] == 0.42
    assert out["val_loss"] == 0.55
    assert out["parity_max_abs_diff"] == 1.2e-6
    assert out["embedding_dim"] == 16.0
    assert out["n_series"] == 18.0
    # every surfaced metric is a float (metrics.json contract)
    assert all(isinstance(v, float) for v in out.values())


def test_score_drops_missing_and_nonfinite_keys():
    state = {"val_loss": float("nan"), "reconstruction_loss": 0.1}
    out = ReconstructionEvaluator().score(state, [], {})
    assert out["n_eval"] == 0.0
    assert out["reconstruction_loss"] == 0.1
    # NaN val_loss dropped, not fabricated; absent keys simply absent
    assert "val_loss" not in out
    assert "parity_max_abs_diff" not in out


def test_score_counts_eval_rows_without_consuming_a_list_twice():
    # rows may be any iterable; the evaluator must count them, not resolve a predictor
    rows = iter([{"date": "2020-01-02"}, {"date": "2020-01-03"}])
    out = ReconstructionEvaluator().score({"reconstruction_loss": 0.3}, rows, {})
    assert out["n_eval"] == 2.0


# --- the manifest ----------------------------------------------------------- #
def test_ssl_encoder_manifest_parses_and_resolves():
    manifest = TrainingManifest.from_yaml(_MANIFEST)
    assert manifest.model_id == "corpus-ssl-encoder-mae-v1"
    assert manifest.target_deployment_stage == "candidate"  # inert
    assert manifest.dataset.family == "corpus_panel"
    # trainer + evaluator qualnames resolve to importable classes
    for qualname, attr in (
        (manifest.trainer, "SSLCorpusEncoderTrainer"),
        (manifest.evaluator, "ReconstructionEvaluator"),
    ):
        module_name, _, cls_name = qualname.rpartition(".")
        cls = getattr(importlib.import_module(module_name), cls_name)
        assert cls.__name__ == attr
    # the trainer resolves its predictor (PREDICTOR_CLASS wired)
    module_name, _, cls_name = manifest.trainer.rpartition(".")
    trainer_cls = getattr(importlib.import_module(module_name), cls_name)
    assert trainer_cls.PREDICTOR_CLASS is not None


def test_ssl_encoder_manifest_uses_holdout_not_labeled_cv():
    # self-supervised: no target_column, holdout split (the recon loss is the metric)
    manifest = TrainingManifest.from_yaml(_MANIFEST)
    assert manifest.evaluator_config.get("split_strategy") == "holdout"
    assert "target_column" not in manifest.evaluator_config
