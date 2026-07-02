"""End-to-end torch-sequence trainer + predictor + parity-gate tests (M19 T1.1).

Guarded by importorskip — they run only where torch/onnx/onnxruntime are present
(the GPU pod, the backtest venv, a dev box), NOT on the money-box/CI, which is the
whole point: the deep stack trains off-box and serves as ONNX.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("numpy")
pytest.importorskip("torch")
pytest.importorskip("onnx")
pytest.importorskip("onnxruntime")

import numpy as np  # noqa: E402

from ml.datasets.sequence_window import build_causal_windows  # noqa: E402
from ml.predictors.torch_sequence import TorchSequencePredictor  # noqa: E402
from ml.trainers.torch_sequence import TorchSequenceTrainer  # noqa: E402


SEQ_LEN = 8
FEATURES = ["a", "b"]
_CFG = {
    "target_column": "regime_label",
    "feature_columns": FEATURES,
    "seq_len": SEQ_LEN,
    "channels": 8,
    "kernel_size": 2,
    "dilations": [1, 2],
    "epochs": 5,
    "batch_size": 64,
    "lr": 0.01,
    "class_weight": {"range": 1.0, "volatile": 5.0},
    "seed": 7,
    "parity_tol": 1e-4,
}


def _synthetic_rows(n=400):
    """A learnable signal: 'volatile' when the window's channel-a energy is high."""
    rng = np.random.default_rng(0)
    raw = []
    for i in range(n):
        hi = i % 4 == 0
        a = float(rng.normal(0, 1.0 if hi else 0.1))
        b = float(rng.normal(0, 0.5))
        raw.append({
            "ts": f"2026-01-01T{i//60:02d}:{i%60:02d}:00Z",
            "symbol": "BTCUSDT", "timeframe": "15m",
            "a": a, "b": b,
            "regime_label": "volatile" if hi else "range",
        })
    return build_causal_windows(raw, feature_columns=FEATURES, seq_len=SEQ_LEN)


def test_fit_returns_json_serializable_state_with_onnx_and_parity():
    rows = _synthetic_rows()
    state = TorchSequenceTrainer().fit(rows, _CFG)
    # JSON round-trip — the registry/mirror/bundle plumbing json.dumps()es state.
    reloaded = json.loads(json.dumps(state))
    assert reloaded["trainer"].endswith("TorchSequenceTrainer")
    assert reloaded["class_labels"] == ["range", "volatile"]
    assert isinstance(reloaded["onnx_b64"], str) and reloaded["onnx_b64"]
    assert reloaded["n_train"] == len(rows)
    # Parity gate ran and passed within tolerance.
    assert reloaded["parity"]["max_abs_diff"] <= _CFG["parity_tol"]
    assert reloaded["parity"]["argmax_agreement"] == 1.0


def test_predictor_round_trips_and_scores():
    rows = _synthetic_rows()
    state = TorchSequenceTrainer().fit(rows, _CFG)
    pred = TorchSequencePredictor(state)
    proba = pred.predict_proba(rows[0])
    assert set(proba) == {"range", "volatile"}
    assert abs(sum(proba.values()) - 1.0) < 1e-4
    assert pred.predict_label(rows[0]) in {"range", "volatile"}


def test_predictor_discriminates_the_signal():
    rows = _synthetic_rows(600)
    state = TorchSequenceTrainer().fit(rows, _CFG)
    pred = TorchSequenceTrainer.PREDICTOR_CLASS(state)
    # A class-weighted model trades raw accuracy for minority recall, so the
    # meaningful check is DISCRIMINATION: mean P(volatile) must be higher on
    # truly-volatile rows than on truly-range rows.
    vol_p = [pred.predict_proba(r)["volatile"] for r in rows if r["regime_label"] == "volatile"]
    rng_p = [pred.predict_proba(r)["volatile"] for r in rows if r["regime_label"] == "range"]
    assert vol_p and rng_p
    assert sum(vol_p) / len(vol_p) > sum(rng_p) / len(rng_p)


def test_parity_gate_fails_closed():
    import torch
    from torch import nn

    from ml.trainers.onnx_export import ParityError, export_and_verify

    module = nn.Sequential(nn.Flatten(), nn.Linear(SEQ_LEN * len(FEATURES), 2), nn.Softmax(dim=1))
    sample = torch.randn(4, SEQ_LEN, len(FEATURES))
    # An impossible tolerance must raise — proving the gate blocks a non-parity model.
    with pytest.raises(ParityError):
        export_and_verify(module, sample, tol=-1.0)
