"""End-to-end SSL corpus-encoder fit + ONNX export + parity + predictor tests (M19 T1.2 P1).

Guarded by importorskip — they run only where torch/onnx/onnxruntime are present
(the GPU pod, the backtest venv, a dev box), NOT on the money-box/CI, which is the
whole point: the encoder trains off-box and serves as ONNX. Kept fast (few series,
short windows, d=4, L=8).
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("numpy")
pytest.importorskip("torch")
pytest.importorskip("onnx")
pytest.importorskip("onnxruntime")

import numpy as np  # noqa: E402

from ml.trainers.ssl_corpus_encoder import SSLCorpusEncoderTrainer  # noqa: E402


SEQ_LEN = 8
EMB_DIM = 4
SERIES = ["s0", "s1", "s2"]
_CFG = {
    "seq_len": SEQ_LEN,
    "embedding_dim": EMB_DIM,
    "mask_ratio": 0.5,
    "hidden": 16,
    "epochs": 15,
    "batch_size": 16,
    "lr": 0.01,
    "seed": 7,
    "parity_tol": 1e-4,
    "parity_ref_n": 32,
}


def _synthetic_panel(n_days=80, missing_early=True):
    """A daily panel with cross-series structure + genuine early missingness."""
    rng = np.random.default_rng(0)
    rows = []
    level = np.zeros(len(SERIES))
    for i in range(n_days):
        level = 0.9 * level + rng.normal(0, 1.0, size=len(SERIES))
        values = {}
        for f, s in enumerate(SERIES):
            # s2 doesn't start until day 20 → real `None` cells (missingness).
            if missing_early and s == "s2" and i < 20:
                values[s] = None
            else:
                values[s] = float(level[f])
        rows.append({"date": f"2020-{1 + i // 28:02d}-{1 + i % 28:02d}", "values": values})
    return rows


def test_fit_returns_json_serializable_state_with_onnx_and_parity():
    rows = _synthetic_panel()
    state = SSLCorpusEncoderTrainer().fit(rows, _CFG)
    # JSON round-trip — the registry/mirror/bundle plumbing json.dumps()es state.
    reloaded = json.loads(json.dumps(state))
    assert reloaded["trainer"].endswith("SSLCorpusEncoderTrainer")
    assert reloaded["series"] == SERIES
    assert reloaded["embedding_dim"] == EMB_DIM
    assert reloaded["seq_len"] == SEQ_LEN
    assert isinstance(reloaded["onnx_b64"], str) and reloaded["onnx_b64"]
    assert reloaded["n_series"] == len(SERIES)
    assert reloaded["n_windows"] > 0
    # Metrics present.
    assert reloaded["reconstruction_loss"] >= 0.0
    assert "val_loss" in reloaded
    # Fail-closed parity gate ran and passed within tolerance.
    assert reloaded["parity"]["max_abs_diff"] <= _CFG["parity_tol"]
    assert reloaded["parity_max_abs_diff"] <= _CFG["parity_tol"]
    # Standardizer stored per-series (fold-frozen from train windows).
    assert len(reloaded["standardizer"]["mean"]) == len(SERIES)
    assert len(reloaded["standardizer"]["std"]) == len(SERIES)


def test_predictor_reproduces_the_encoder_trunk_within_tol():
    """The B predictor's serve-input construction reproduces the ONNX trunk output.

    Parity (torch==onnx) is proven inside fit(); here we prove the predictor feeds
    the graph the SAME raw value+mask window the trainer exported against — i.e. a
    fresh onnxruntime run on the stored graph equals predictor.embed() for the same
    panel window.
    """
    import onnxruntime as ort

    rows = _synthetic_panel()
    state = SSLCorpusEncoderTrainer().fit(rows, _CFG)
    pred = SSLCorpusEncoderTrainer.PREDICTOR_CLASS(state)

    # The most recent SEQ_LEN panel rows are a serve window.
    window = sorted(rows, key=lambda r: r["date"])[-SEQ_LEN:]
    emb = pred.embed(window)
    assert len(emb) == EMB_DIM
    assert all(np.isfinite(emb))
    # Deterministic across calls.
    assert pred.embed(window) == emb

    # Independently rebuild the raw value+mask serve tensor and run the graph — it
    # must match the predictor's embedding (proves _build_input is correct).
    import base64

    sess = ort.InferenceSession(
        base64.b64decode(state["onnx_b64"]), providers=["CPUExecutionProvider"]
    )
    L, F = SEQ_LEN, len(SERIES)
    value = np.zeros((L, F), dtype=np.float32)
    mask = np.ones((L, F), dtype=np.float32)
    for r_idx, row in enumerate(window):
        for f, s in enumerate(SERIES):
            v = row["values"].get(s)
            if v is not None:
                value[r_idx, f] = float(v)
                mask[r_idx, f] = 0.0
    x = np.concatenate([value, mask], axis=-1).reshape(1, L, 2 * F).astype(np.float32)
    ref = sess.run(None, {sess.get_inputs()[0].name: x})[0].reshape(-1)
    assert np.allclose(ref, np.asarray(emb), atol=1e-4)


def test_short_window_left_pads_with_missing():
    """A serve window shorter than L is left-padded with missing cells, not zeros."""
    rows = _synthetic_panel()
    state = SSLCorpusEncoderTrainer().fit(rows, _CFG)
    pred = SSLCorpusEncoderTrainer.PREDICTOR_CLASS(state)
    short = sorted(rows, key=lambda r: r["date"])[-3:]  # only 3 of SEQ_LEN rows
    emb = pred.embed(short)
    assert len(emb) == EMB_DIM
    assert all(np.isfinite(emb))


def test_offline_block_runs_with_the_real_encoder():
    """The offline producer + predictor_embed_fn produce per-day emb rows end-to-end."""
    from ml.datasets.corpus_embedding_features import (
        compute_corpus_embedding_rows,
        corpus_embedding_sidestream,
        predictor_embed_fn,
    )

    rows = _synthetic_panel()
    state = SSLCorpusEncoderTrainer().fit(rows, _CFG)
    embed_fn = predictor_embed_fn(state)
    emb_rows = compute_corpus_embedding_rows(
        rows, embed_fn=embed_fn, seq_len=SEQ_LEN, out_dim=EMB_DIM
    )
    assert emb_rows
    assert all("date" in r and "corpus_emb_0" in r for r in emb_rows)
    # The side-stream re-keys to a one-day-lagged ts grid.
    side = corpus_embedding_sidestream(emb_rows[:3])
    assert all("ts" in r for r in side)
