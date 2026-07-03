"""SSL corpus-encoder predictor — ONNX / onnxruntime CPU serve (M19 T1.2 P1).

Paired with :class:`ml.trainers.ssl_corpus_encoder.SSLCorpusEncoderTrainer`. Reloads
the trained encoder's **ONNX graph** (``model_state['onnx_b64']``) and, given a
window of the last ``L`` daily ``corpus_panel`` rows, returns the ``d``-dim
market-state **embedding** via ``onnxruntime`` on the **CPU** — never torch.

The masked-reconstruction *decoder* is discarded at train time; only the encoder
trunk (value + mask → embedding) is exported. Per-cell standardization is **baked
into the exported graph** (the frozen train-fold mean/std are the module's first
op, exactly like :class:`ml.predictors.torch_sequence.TorchSequencePredictor`), so
this predictor feeds the **raw** value + missingness-mask serve input and does no
arithmetic of its own — parity therefore covers normalization too.

Unlike the classifier predictors this one produces a **vector embedding**, not a
class label. Its real surface is :meth:`embed` (``window -> list[float]``), used by
the offline :mod:`ml.datasets.corpus_embedding_features` block and the eventual
live producer. :meth:`predict` is the minimal :class:`~ml.predictors.base.Predictor`
ABC implementation (returns the first embedding component of a window carried on the
row under ``panel_window``) so the encoder still resolves through the standard
``PREDICTOR_CLASS`` machinery.

``onnxruntime`` / ``numpy`` are imported lazily (the session is built on first
embed) so this module — and the trainer that imports it — import cleanly on a
torch-/onnxruntime-free environment (CI, the money-box).
"""
from __future__ import annotations

import base64
import math
from typing import Any, Mapping, Sequence

from .base import Predictor

# The panel-row window key `predict()` looks for (the offline block calls `embed`
# directly, so this only matters for the minimal ABC surface).
PANEL_WINDOW_KEY = "panel_window"


def _finite(value: Any) -> float | None:
    """Coerce to a finite float, else ``None`` (a genuinely-missing cell)."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


class SSLCorpusEncoderPredictor(Predictor):
    """Serve the SSL corpus encoder's embedding via onnxruntime on CPU."""

    def __init__(self, state: Mapping[str, Any]) -> None:
        self._state = state
        self.series: list[str] = [str(s) for s in state.get("series", [])]
        if not self.series:
            raise ValueError("model_state['series'] is empty; not an SSL-encoder model")
        self.seq_len = int(state.get("seq_len", 0))
        if self.seq_len < 1:
            raise ValueError("model_state['seq_len'] must be >= 1")
        self.embedding_dim = int(state.get("embedding_dim", 0))
        onnx_b64 = state.get("onnx_b64")
        if not onnx_b64:
            raise ValueError("model_state['onnx_b64'] missing; not an SSL-encoder model")
        self._onnx_bytes = base64.b64decode(onnx_b64)
        self._session: Any = None
        self._input_name: str | None = None

    # -- lazy onnxruntime session -------------------------------------------
    def _ensure_session(self) -> None:
        if self._session is not None:
            return
        import onnxruntime as ort  # noqa: PLC0415

        self._session = ort.InferenceSession(
            self._onnx_bytes, providers=["CPUExecutionProvider"]
        )
        self._input_name = self._session.get_inputs()[0].name

    # -- serve-input construction (mirrors the trainer's window builder) -----
    def _build_input(self, window_rows: Sequence[Mapping[str, Any]]) -> "Any":
        """Build the ``(1, L, 2F)`` raw value+mask serve tensor for a panel window.

        The last ``L`` rows (ascending by ``date``) are used; a window with fewer
        than ``L`` rows is **left-padded with missing** cells (value 0, mask 1) so
        the earliest positions read as absent rather than as a fabricated zero. A
        cell whose ``values[series]`` is ``None`` / non-finite is likewise marked
        missing (mask 1, value 0) — never fed as signal. The mask is the second
        ``F`` columns; ``mask=1`` ⟺ the cell is absent (matches the trainer's
        ``native None → mask=1`` convention).
        """
        import numpy as np  # noqa: PLC0415

        L = self.seq_len
        F = len(self.series)
        value = np.zeros((L, F), dtype=np.float32)
        mask = np.ones((L, F), dtype=np.float32)  # default: everything missing

        ordered = sorted(window_rows, key=lambda r: str(r.get("date", "")))[-L:]
        offset = L - len(ordered)  # left-pad shorter windows with missing rows
        for r_idx, row in enumerate(ordered):
            vals = row.get("values") or {}
            for f, series_id in enumerate(self.series):
                v = _finite(vals.get(series_id))
                if v is not None:
                    value[offset + r_idx, f] = v
                    mask[offset + r_idx, f] = 0.0
        x = np.concatenate([value, mask], axis=-1)  # (L, 2F)
        return x.reshape(1, L, 2 * F)

    def embed(self, window_rows: Sequence[Mapping[str, Any]]) -> list[float]:
        """Return the ``d``-dim embedding for a window of the last ``L`` panel rows.

        ``window_rows`` are ``corpus_panel`` rows (``{date, values:{series_id:
        float|None}}``). The embedding is the encoder trunk's output on the
        standardized value + missingness-mask window ending at the latest date.
        """
        import numpy as np  # noqa: PLC0415

        self._ensure_session()
        arr = self._build_input(window_rows)
        out = self._session.run(None, {self._input_name: arr})[0]
        return [float(v) for v in np.asarray(out, dtype=np.float64).reshape(-1)]

    # -- minimal Predictor ABC surface --------------------------------------
    def predict(self, row: Mapping[str, Any]) -> float:
        """Minimal ABC hook — first embedding component of ``row[panel_window]``.

        The encoder's meaningful output is the whole vector (:meth:`embed`); this
        single-float surface exists only so the encoder resolves through the
        standard predictor machinery. ``0.0`` when no window is carried.
        """
        window = row.get(PANEL_WINDOW_KEY) if isinstance(row, Mapping) else row
        if not window:
            return 0.0
        emb = self.embed(window)
        return emb[0] if emb else 0.0
