"""Causal (filtered) Gaussian-HMM regime predictor (S-MLOPT-S14 / Phase 3.2).

Pairs with ``ml.trainers.causal_hmm_regime.CausalHMMRegimeTrainer``.

Runs the **filtered** HMM recursion — the forward pass only:

    alpha_t(k) ∝ emission_k(x_t) · Σ_i alpha_{t-1}(i) · A[i, k]
    alpha_0(k) ∝ emission_k(x_0) · pi(k)

so the state posterior at bar *t* depends on observations ``x_1 .. x_t``
**only** — never the future. This is the mandatory discipline for a regime
model that will be scored live: Viterbi / forward-backward *smoothing* uses
the whole sequence (including future bars) and inflates backtests; we never
use it. The class-label posterior is the state posterior pushed through the
per-state label distribution the trainer recorded.

**Sequential contract.** ``predict_label`` / ``predict_proba`` are *stateful*:
each call advances the filter by one bar, assuming rows arrive in
chronological order (which is how both ``time_aware_holdout`` and each
``purged_walk_forward`` test fold feed the evaluator). The filter
**auto-resets** when a row's ``ts`` is not strictly greater than the previous
one (a fold boundary, a re-instantiation, or any out-of-order replay), so a
fresh evaluation pass always starts from the prior ``pi`` — there is no
cross-fold state leak. ``reset()`` forces it manually.

Pure-stdlib (``math`` only) so it stays light on the live trader if the model
is ever promoted to ``shadow`` — the heavy EM fit lives in the trainer
(NumPy), the trainer VM only.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .multiclass import MulticlassPredictor

_LOG_2PI = math.log(2.0 * math.pi)


class CausalHMMRegimePredictor(MulticlassPredictor):
    """Filtered Gaussian-HMM regime classifier.

    Required ``model_state`` keys (set by the trainer):

    - ``feature_columns`` — ordered list of numeric feature names (the HMM
      observation vector per bar).
    - ``means`` / ``variances`` — ``[n_states][n_features]`` diagonal-Gaussian
      emission params.
    - ``transition`` — ``[n_states][n_states]`` row-stochastic transition matrix.
    - ``start_prob`` — ``[n_states]`` initial state distribution (pi).
    - ``state_label_proba`` — ``[n_states]`` of ``{class_label: prob}`` — the
      per-state distribution over regime labels.
    - ``class_labels`` — ordered canonical label list (output ordering /
      tie-break).
    - ``time_column`` — the ts field used for the monotonicity auto-reset
      (default ``"ts"``).
    """

    def __init__(self, state: Mapping[str, Any]) -> None:
        feats = state.get("feature_columns")
        if not feats:
            raise ValueError(
                "CausalHMMRegimePredictor requires state['feature_columns']"
            )
        self._features = [str(f) for f in feats]

        means = state.get("means")
        variances = state.get("variances")
        if not means or not variances:
            raise ValueError(
                "CausalHMMRegimePredictor requires state['means'] + ['variances']"
            )
        self._means = [[float(v) for v in row] for row in means]
        # Floor variances so the Gaussian never degenerates to a spike.
        self._vars = [[max(float(v), 1e-12) for v in row] for row in variances]
        self._n_states = len(self._means)

        trans = state.get("transition")
        start = state.get("start_prob")
        if not trans or not start:
            raise ValueError(
                "CausalHMMRegimePredictor requires state['transition'] + ['start_prob']"
            )
        self._A = [[float(v) for v in row] for row in trans]
        self._pi = [float(v) for v in start]

        slp = state.get("state_label_proba")
        if not slp:
            raise ValueError(
                "CausalHMMRegimePredictor requires state['state_label_proba']"
            )
        self._state_label_proba = [
            {str(c): float(p) for c, p in dist.items()} for dist in slp
        ]

        class_labels = state.get("class_labels")
        if not class_labels:
            raise ValueError(
                "CausalHMMRegimePredictor requires state['class_labels']"
            )
        self._class_labels = tuple(str(c) for c in class_labels)
        self._time_column = str(state.get("time_column", "ts"))

        # Filter state (stateful across a chronological pass).
        self._alpha: list[float] | None = None
        self._last_ts: Any = None

    # -- filter lifecycle ---------------------------------------------------

    def reset(self) -> None:
        """Clear the filter so the next row restarts from ``start_prob``."""
        self._alpha = None
        self._last_ts = None

    def _features_of(self, row: Mapping[str, Any]) -> list[float | None]:
        out: list[float | None] = []
        for f in self._features:
            v = row.get(f)
            try:
                out.append(None if v is None else float(v))
            except (TypeError, ValueError):
                out.append(None)
        return out

    def _emission_likelihoods(self, x: Sequence[float | None]) -> list[float]:
        """Per-state diagonal-Gaussian likelihood, max-normalised for stability.

        A missing feature (``None``) is skipped in the product (contributes
        equally to every state), so a row with a gap still advances the
        filter on whatever features it has.
        """
        log_lik: list[float] = []
        for k in range(self._n_states):
            ll = 0.0
            for d, xd in enumerate(x):
                if xd is None:
                    continue
                mu = self._means[k][d]
                var = self._vars[k][d]
                ll += -0.5 * ((xd - mu) ** 2 / var + math.log(var) + _LOG_2PI)
            log_lik.append(ll)
        m = max(log_lik)
        return [math.exp(ll - m) for ll in log_lik]

    def _advance(self, row: Mapping[str, Any]) -> list[float]:
        """Advance the filter by one bar; return the filtered state posterior."""
        ts = row.get(self._time_column)
        # Auto-reset on a non-monotonic timestamp (new fold / replay).
        if ts is not None and self._last_ts is not None:
            try:
                if ts <= self._last_ts:
                    self.reset()
            except TypeError:
                self.reset()  # incomparable ts types → treat as a fresh sequence

        e = self._emission_likelihoods(self._features_of(row))
        if self._alpha is None:
            a = [self._pi[k] * e[k] for k in range(self._n_states)]
        else:
            a = []
            for k in range(self._n_states):
                pred = sum(
                    self._alpha[i] * self._A[i][k] for i in range(self._n_states)
                )
                a.append(pred * e[k])
        total = math.fsum(a)
        if total <= 0.0 or not math.isfinite(total):
            # Degenerate step (all-zero likelihood) → fall back to the prior.
            a = list(self._pi)
            total = math.fsum(a)
        self._alpha = [v / total for v in a]
        self._last_ts = ts if ts is not None else self._last_ts
        return self._alpha

    # -- MulticlassPredictor surface ---------------------------------------

    def predict_proba(self, row: Mapping[str, Any]) -> Mapping[str, float]:
        alpha = self._advance(row)
        proba = {c: 0.0 for c in self._class_labels}
        for k in range(self._n_states):
            for label, p in self._state_label_proba[k].items():
                proba[label] = proba.get(label, 0.0) + alpha[k] * p
        return proba

    def predict_label(self, row: Mapping[str, Any]) -> str:
        proba = self.predict_proba(row)
        best_label = self._class_labels[0]
        best_prob = -1.0
        for label in self._class_labels:
            p = proba.get(label, 0.0)
            if p > best_prob:
                best_prob = p
                best_label = label
        return best_label
