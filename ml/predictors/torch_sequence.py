"""Torch sequence-model predictor — ONNX / onnxruntime CPU serve (M19 T1.1).

Paired with :class:`ml.trainers.torch_sequence.TorchSequenceTrainer`. Reloads the
trained model's **ONNX graph** (``model_state['onnx_b64']``) and scores a row via
``onnxruntime`` on the **CPU** — never torch. Standardization is baked into the
graph, so this predictor feeds the RAW ``seq_window`` and reads probabilities out.

``onnxruntime`` / ``numpy`` are imported lazily (session built on first predict) so
this module — and the trainer that imports it, and the evaluator that resolves it —
import cleanly on an environment without onnxruntime (the torch-free money-box
until the serve dependency is deliberately added at the T1.1 P3 promotion).
"""
from __future__ import annotations

import base64
from typing import Any, Mapping

from .multiclass import MulticlassPredictor


class TorchSequencePredictor(MulticlassPredictor):
    def __init__(self, state: Mapping[str, Any]) -> None:
        self._state = state
        self.class_labels: list[str] = [str(c) for c in state.get("class_labels", [])]
        if not self.class_labels:
            raise ValueError("model_state['class_labels'] is empty")
        self.feature_columns: list[str] = [str(c) for c in state.get("feature_columns", [])]
        self.seq_len = int(state.get("seq_len", 0))
        self.window_column = str(state.get("window_column", "seq_window"))
        onnx_b64 = state.get("onnx_b64")
        if not onnx_b64:
            raise ValueError("model_state['onnx_b64'] missing; not a torch-sequence model")
        self._onnx_bytes = base64.b64decode(onnx_b64)
        self._session: Any = None
        self._input_name: str | None = None

    def _ensure_session(self) -> None:
        if self._session is not None:
            return
        import onnxruntime as ort  # noqa: PLC0415

        self._session = ort.InferenceSession(
            self._onnx_bytes, providers=["CPUExecutionProvider"]
        )
        self._input_name = self._session.get_inputs()[0].name

    def predict_proba(self, row: Mapping[str, Any]) -> Mapping[str, float]:
        import numpy as np  # noqa: PLC0415

        win = row.get(self.window_column)
        if not isinstance(win, list) or (self.seq_len and len(win) != self.seq_len):
            # Materialization guarantees a valid window on every eval/serve row;
            # a missing/ragged one is a data-contract violation. Degrade to a
            # uniform distribution (a deterministic, non-crashing miss) rather
            # than take down a whole eval pass on one bad row.
            u = 1.0 / len(self.class_labels)
            return {c: u for c in self.class_labels}
        self._ensure_session()
        arr = np.asarray([win], dtype=np.float32)  # (1, L, F)
        out = self._session.run(None, {self._input_name: arr})[0]
        proba = np.asarray(out, dtype=np.float64).reshape(-1)
        return {c: float(proba[i]) for i, c in enumerate(self.class_labels)}

    def predict_label(self, row: Mapping[str, Any]) -> str:
        proba = self.predict_proba(row)
        if not proba:
            return self.class_labels[0]
        return max(proba.items(), key=lambda kv: kv[1])[0]
