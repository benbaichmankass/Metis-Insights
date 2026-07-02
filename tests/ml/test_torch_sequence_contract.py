"""Money-box import-discipline + manifest sanity for the torch-sequence stack (M19 T1.1).

These tests deliberately do NOT importorskip torch/onnxruntime — they must run on
CI (which has neither) to PROVE the trainer/predictor modules import without them.
If a torch/onnxruntime import ever leaks to module top-level, collection here fails.
"""
from __future__ import annotations

import base64
from pathlib import Path

import yaml


def test_modules_import_without_torch_or_onnxruntime():
    # Plain imports — if any of these pulled torch/onnxruntime at top level, this
    # would ImportError on the money-box / CI.
    import ml.datasets.families.market_sequences  # noqa: F401
    import ml.datasets.sequence_window  # noqa: F401
    import ml.predictors.torch_sequence as pred  # noqa: F401
    import ml.trainers.onnx_export  # noqa: F401
    import ml.trainers.torch_sequence as trn

    assert trn.TorchSequenceTrainer.PREDICTOR_CLASS is pred.TorchSequencePredictor


def test_predictor_constructs_and_degrades_without_onnxruntime():
    from ml.predictors.torch_sequence import TorchSequencePredictor

    state = {
        "class_labels": ["range", "volatile"],
        "feature_columns": ["log_return", "rolling_log_return_vol"],
        "seq_len": 4,
        "window_column": "seq_window",
        "onnx_b64": base64.b64encode(b"not-a-real-graph").decode("ascii"),
    }
    # Construction must not build an onnxruntime session (lazy) — so this works
    # even where onnxruntime is absent.
    p = TorchSequencePredictor(state)
    # A missing/ragged window degrades to a uniform distribution WITHOUT ever
    # touching onnxruntime — proving the serve dependency is truly lazy.
    proba = p.predict_proba({"seq_window": None})
    assert set(proba) == {"range", "volatile"}
    assert abs(sum(proba.values()) - 1.0) < 1e-9
    assert p.predict_label({"seq_window": None}) in {"range", "volatile"}


def test_manifest_matches_family_and_trainer_contract():
    manifest = yaml.safe_load(Path("ml/configs/btc-regime-15m-tcn-v1.yaml").read_text())
    assert manifest["trainer"] == "ml.trainers.torch_sequence.TorchSequenceTrainer"
    assert manifest["dataset"]["family"] == "market_sequences"
    # feature_columns MUST match the family's windowed default (order-sensitive
    # coupling contract).
    from ml.datasets.families.market_sequences import DEFAULT_FEATURE_COLUMNS

    assert manifest["trainer_config"]["feature_columns"] == DEFAULT_FEATURE_COLUMNS
    # A deep model must default to candidate (inert) — never auto-shadow.
    assert manifest["target_deployment_stage"] == "candidate"
    # The split must key on `ts` (market_features rows have no created_at).
    assert manifest["evaluator_config"]["time_column"] == "ts"
