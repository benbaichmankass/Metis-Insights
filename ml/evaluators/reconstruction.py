"""Reconstruction evaluator — surfaces a self-supervised encoder's training diagnostics (M19 T1.2 P1b).

The SSL corpus encoder
(:class:`ml.trainers.ssl_corpus_encoder.SSLCorpusEncoderTrainer`) is **label-free**:
it produces a market-state *embedding*, not a per-row prediction, and its
masked-reconstruction **decoder is discarded** at train time (only the encoder
trunk is ONNX-exported). So — unlike the classification/regression evaluators —
there is no per-row target to score and no decoder to recompute reconstruction on
the held-out eval rows at serve time.

What this evaluator does, therefore, is **surface the training-diagnostic metrics
the trainer already recorded** in ``model_state`` (the reconstruction / held-out
val loss computed inside ``fit`` on its own trailing val split, plus the
fail-closed ONNX-parity max-abs-diff and the shape counters) so the experiment
runner can persist them to ``metrics.json`` and register the encoder at
``candidate``. It also reports ``n_eval`` (the count of held-out rows the runner
handed it) so the split is not invisible.

**This is a training diagnostic, NOT the gate.** A masked-reconstruction loss is
not comparable across encoders and does not by itself justify promotion — the
real T1.2 gate is the **P2 downstream A/B**: does the ``corpus_emb_*`` block
(built from this encoder via
:mod:`ml.datasets.corpus_embedding_features`) lift ≥1 boosting head's purged-CV
metric beyond BOTH the no-embedding baseline AND the frozen-Chronos T0.1
embedding. That A/B runs a *classification* evaluator on the downstream head, not
this one. Keeping this evaluator metric-only (no predictor resolution, no
fabricated per-row score) is the honest shape for a self-supervised pretraining
run.

Stdlib-only; no torch/onnxruntime (it reads floats already in ``model_state``).
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from .base import Evaluator

# The training-diagnostic metric keys the SSL encoder's `fit()` records inline in
# `model_state`. Any that are missing (a trainer that didn't record them) are
# simply omitted from the score dict rather than fabricated.
_DIAGNOSTIC_KEYS: tuple[str, ...] = (
    "reconstruction_loss",
    "val_loss",
    "parity_max_abs_diff",
    "embedding_dim",
    "n_windows",
    "n_train_windows",
    "n_series",
)


class ReconstructionEvaluator(Evaluator):
    """Surface a self-supervised encoder's recorded reconstruction diagnostics.

    Reads the metric keys the trainer wrote into ``model_state`` and returns them
    as the score mapping (all coerced to ``float``; non-finite/absent dropped),
    plus ``n_eval`` = the number of held-out rows. Does NOT resolve a predictor —
    a reconstruction encoder has no per-row label to score.
    """

    def score(
        self,
        model_state: Mapping[str, Any],
        rows: Iterable[Mapping[str, Any]],
        config: Mapping[str, Any],
    ) -> Mapping[str, float]:
        n_eval = sum(1 for _ in rows)
        out: dict[str, float] = {"n_eval": float(n_eval)}
        for key in _DIAGNOSTIC_KEYS:
            if key not in model_state:
                continue
            try:
                value = float(model_state[key])
            except (TypeError, ValueError):
                continue
            if math.isfinite(value):
                out[key] = value
        return out
