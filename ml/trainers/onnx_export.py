"""ONNX export + CUDA/CPU numeric-parity gate (M19 T1.1).

A LightGBM booster is CPU-native, so "trained" and "served" are byte-identical
and no export step exists. A torch model breaks that: it trains on CUDA (or
pod-CPU) and must serve via ``onnxruntime`` on the CPU-only money-box. Float
kernels differ across CUDA ↔ CPU ↔ ONNX, so before a torch model may influence
anything we must **prove the served ONNX graph reproduces the trained module's
outputs within tolerance**. This module is that gate.

It is the one genuinely-new piece of machinery T1.1 needs. It **fails closed**:
on a parity miss it raises :class:`ParityError`, and the caller (the trainer)
returns NO model — a model that can't be reproduced on the serve target must
never reach the registry, let alone ``candidate``.

``torch`` / ``onnx`` / ``onnxruntime`` are imported lazily inside the function so
this module imports on a torch-free money-box (the live path never calls it).
"""
from __future__ import annotations

import io
from typing import Any


class ParityError(RuntimeError):
    """The exported ONNX graph did not reproduce the torch module within tol."""


# The ONNX graph's single input / output tensor names. The predictor's
# ``onnxruntime`` session feeds ``INPUT_NAME`` and reads the first output.
INPUT_NAME = "seq_window"
OUTPUT_NAME = "proba"


def export_and_verify(
    module: Any,
    sample_input: Any,
    *,
    tol: float = 1e-4,
    opset: int = 17,
) -> tuple[bytes, dict[str, float]]:
    """Export ``module`` to ONNX bytes and verify CPU-ONNX parity vs torch.

    ``module`` must be a ``torch.nn.Module`` in eval mode whose forward maps a
    ``(batch, seq_len, n_features)`` float tensor to ``(batch, n_classes)``
    **probabilities** (softmax applied inside the module, so the served output is
    the proba directly). ``sample_input`` is a reference batch of real windows.

    Returns ``(onnx_bytes, parity_report)``. Raises :class:`ParityError` if the
    max absolute probability difference exceeds ``tol`` OR any argmax disagrees.
    """
    import numpy as np  # noqa: PLC0415
    import torch  # noqa: PLC0415

    module.eval()
    with torch.no_grad():
        y_torch = module(sample_input).cpu().numpy()

    buffer = io.BytesIO()
    export_kwargs = dict(
        input_names=[INPUT_NAME],
        output_names=[OUTPUT_NAME],
        opset_version=opset,
        dynamic_axes={INPUT_NAME: {0: "batch"}, OUTPUT_NAME: {0: "batch"}},
        do_constant_folding=True,
    )
    try:
        # Force the stable TorchScript exporter — the torch>=2.9 default routed to
        # the dynamo exporter, which pulls an extra `onnxscript` dep and is newer /
        # edge-case-prone for small conv nets. `dynamo=False` keeps the battle-
        # tested path and needs no extra dep. Older torch lacks the kwarg (legacy
        # is already the default) → retry without it.
        torch.onnx.export(module, sample_input, buffer, dynamo=False, **export_kwargs)
    except TypeError:
        buffer = io.BytesIO()
        torch.onnx.export(module, sample_input, buffer, **export_kwargs)
    onnx_bytes = buffer.getvalue()

    import onnxruntime as ort  # noqa: PLC0415

    sess = ort.InferenceSession(onnx_bytes, providers=["CPUExecutionProvider"])
    y_onnx = sess.run(None, {INPUT_NAME: sample_input.cpu().numpy().astype(np.float32)})[0]

    y_torch = np.asarray(y_torch, dtype=np.float64)
    y_onnx = np.asarray(y_onnx, dtype=np.float64)
    if y_torch.shape != y_onnx.shape:
        raise ParityError(
            f"shape mismatch: torch {y_torch.shape} vs onnx {y_onnx.shape}"
        )

    max_abs_diff = float(np.max(np.abs(y_torch - y_onnx))) if y_torch.size else 0.0
    argmax_agreement = (
        float(np.mean(y_torch.argmax(axis=1) == y_onnx.argmax(axis=1)))
        if y_torch.size
        else 1.0
    )

    report = {
        "max_abs_diff": max_abs_diff,
        "argmax_agreement": argmax_agreement,
        "n_ref": float(y_torch.shape[0]) if y_torch.ndim else 0.0,
        "tol": float(tol),
    }

    if max_abs_diff > tol or argmax_agreement < 1.0:
        raise ParityError(
            "ONNX/torch parity gate FAILED (fail-closed): "
            f"max_abs_diff={max_abs_diff:.3e} (tol={tol:.1e}), "
            f"argmax_agreement={argmax_agreement:.4f}. "
            "Refusing to emit a model that does not reproduce on the CPU serve target."
        )
    return onnx_bytes, report
