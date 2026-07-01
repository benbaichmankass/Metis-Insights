"""Causal Gaussian-HMM regime trainer (S-MLOPT-S14 / Phase 3.2).

An alternative regime model to the LightGBM heads: a small **Gaussian HMM**
over range-based volatility features (the S9 estimators), fit with diagonal-
covariance EM. The point of the HMM over the per-bucket / tree classifiers is
**temporal persistence** — the transition matrix smooths regime flicker — and
**interpretability** (each hidden state is a Gaussian vol regime with a
posterior probability).

Discipline (mandatory, enforced by the predictor): live/eval scoring uses the
**filtered** (forward-only) posterior, never Viterbi / forward-backward
smoothing, which would leak the future. See
``ml.predictors.causal_hmm_regime.CausalHMMRegimePredictor``.

Fit (NumPy, trainer-VM only):

1. order the labeled training rows by ``time_column``;
2. diagonal-Gaussian **GMM EM** over ``feature_columns`` → per-state means +
   variances + soft responsibilities (deterministic quantile init, seeded);
3. **transition matrix** from soft consecutive-bar responsibilities
   (Laplace-smoothed, row-stochastic);
4. **start distribution** = mean responsibility (a stable stationary-ish prior);
5. **per-state label distribution** = responsibility-weighted regime-label
   frequencies — this is what turns a hidden-state posterior into a
   class posterior.

The model is a **Tier-3 candidate proposal** (pre-shadow); promotion past ``shadow`` is
operator-gated, and it must add OOS edge over the LightGBM regime head under
the Phase-0 purged WF-CV before it earns anything (the "illusion of regimes"
dissent — validate, don't assume).
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..predictors.causal_hmm_regime import CausalHMMRegimePredictor
from .base import Trainer


class CausalHMMRegimeTrainer(Trainer):
    PREDICTOR_CLASS = CausalHMMRegimePredictor

    def fit(
        self,
        rows: Iterable[Mapping[str, Any]],
        config: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        import numpy as np

        target = str(config.get("target_column", "regime_label"))
        features = [
            str(f) for f in (config.get("feature_columns") or ["yang_zhang_vol"])
        ]
        n_states = int(config.get("n_states", 3))
        n_em_iter = int(config.get("n_em_iter", 50))
        seed = int(config.get("seed", 42))
        time_column = str(config.get("time_column", "ts"))
        var_floor = float(config.get("var_floor", 1e-9))
        trans_smoothing = float(config.get("transition_smoothing", 1.0))
        label_smoothing = float(config.get("label_smoothing", 1.0))

        # --- assemble the labeled, fully-featured, time-ordered sequence ----
        usable: list[tuple[Any, list[float], str]] = []
        for row in rows:
            tv = row.get(target)
            if tv is None or not str(tv).strip():
                continue
            vals: list[float] = []
            ok = True
            for f in features:
                v = row.get(f)
                if v is None:
                    ok = False
                    break
                try:
                    vals.append(float(v))
                except (TypeError, ValueError):
                    ok = False
                    break
            if not ok:
                continue
            usable.append((row.get(time_column), vals, str(tv).strip()))

        # Sort by time so the transition matrix reflects real bar order.
        usable.sort(key=lambda t: (t[0] is None, t[0]))

        configured_labels = config.get("class_labels")
        if configured_labels:
            class_labels = [str(c) for c in configured_labels]
        else:
            class_labels = sorted({lbl for _, _, lbl in usable})

        n = len(usable)
        d = len(features)
        # Degenerate guards: too few rows / states collapse to a 1-state model
        # so the run still produces a (trivial) scorable model_state.
        eff_states = max(1, min(n_states, n)) if n > 0 else 1

        base_state: dict[str, Any] = {
            "trainer": "ml.trainers.causal_hmm_regime.CausalHMMRegimeTrainer",
            "target_column": target,
            "feature_columns": features,
            "class_labels": class_labels,
            "time_column": time_column,
            "n_states": eff_states,
            "n_train": n,
            "symbol": str(config.get("symbol", "")),
            "timeframe": str(config.get("timeframe", "")),
        }

        if n == 0:
            # Nothing to fit — emit a uniform 1-state model.
            uni = {c: 1.0 / len(class_labels) for c in class_labels} if class_labels else {}
            base_state.update(
                means=[[0.0] * d], variances=[[1.0] * d],
                transition=[[1.0]], start_prob=[1.0],
                state_label_proba=[uni], n_states=1,
            )
            return base_state

        X = np.asarray([v for _, v, _ in usable], dtype=float)  # (n, d)
        labels = [lbl for _, _, lbl in usable]

        rng = np.random.default_rng(seed)
        means, variances, resp = _gmm_em(
            X, eff_states, n_em_iter, var_floor, rng,
        )

        # Transition matrix from soft consecutive-bar responsibilities.
        k = means.shape[0]
        trans = np.full((k, k), trans_smoothing, dtype=float)
        if n >= 2:
            # Σ_t resp[t] outer resp[t+1]  (vectorised).
            trans += resp[:-1].T @ resp[1:]
        trans /= trans.sum(axis=1, keepdims=True)

        # Start distribution = mean responsibility (stable, full-data prior).
        start = resp.mean(axis=0)
        start /= start.sum()

        # Per-state label distribution (responsibility-weighted, smoothed).
        label_idx = {c: i for i, c in enumerate(class_labels)}
        counts = np.full((k, len(class_labels)), label_smoothing, dtype=float)
        for t, lbl in enumerate(labels):
            j = label_idx.get(lbl)
            if j is not None:
                counts[:, j] += resp[t]
        # Optional class weighting of the state->label projection (M19 T0.2
        # salvage). The unweighted argmax washes out a rare class: at a 4.6%
        # volatile base rate EVERY state's majority label is `range`, so the HMM
        # collapses to an all-range classifier. Scaling each label column by a
        # weight — the direct analogue of the LightGBM head's `class_weight` —
        # lets a state with *elevated* volatile responsibility map to volatile
        # even when volatile is the minority. `None` (default) → unchanged;
        # "balanced" → sklearn inverse-frequency; a dict → explicit per-label
        # weights. Applied before the row-normalisation so the per-state
        # distribution still sums to 1.
        cw_cfg = config.get("label_projection_class_weight")
        if cw_cfg:
            if isinstance(cw_cfg, str) and cw_cfg.lower() == "balanced":
                freq = np.array(
                    [max(1, labels.count(c)) for c in class_labels], dtype=float
                )
                cw = n / (len(class_labels) * freq)
            elif isinstance(cw_cfg, Mapping):
                cw = np.array(
                    [float(cw_cfg.get(c, 1.0)) for c in class_labels], dtype=float
                )
            else:
                cw = np.ones(len(class_labels), dtype=float)
            counts = counts * cw[None, :]
            base_state["label_projection_class_weight"] = (
                cw_cfg if isinstance(cw_cfg, str) else dict(cw_cfg)
            )
        counts /= counts.sum(axis=1, keepdims=True)
        state_label_proba = [
            {c: float(counts[s, label_idx[c]]) for c in class_labels}
            for s in range(k)
        ]

        base_state.update(
            means=means.tolist(),
            variances=variances.tolist(),
            transition=trans.tolist(),
            start_prob=start.tolist(),
            state_label_proba=state_label_proba,
            n_states=k,
        )
        return base_state


def _gmm_em(X, k, n_iter, var_floor, rng):
    """Diagonal-covariance Gaussian-mixture EM. Deterministic quantile init.

    Returns ``(means[k,d], variances[k,d], responsibilities[n,k])``.
    """
    import numpy as np

    n, d = X.shape
    k = max(1, min(k, n))

    # Deterministic init: order by the first feature, split into k contiguous
    # quantile groups, seed each component from its group.
    order = np.argsort(X[:, 0], kind="stable")
    groups = np.array_split(order, k)
    means = np.empty((k, d))
    variances = np.empty((k, d))
    weights = np.empty(k)
    for s, g in enumerate(groups):
        g = g if len(g) > 0 else order[: max(1, n // k)]
        means[s] = X[g].mean(axis=0)
        variances[s] = np.maximum(X[g].var(axis=0), var_floor)
        weights[s] = len(g) / n
    weights /= weights.sum()

    log_2pi = np.log(2.0 * np.pi)
    resp = np.full((n, k), 1.0 / k)
    for _ in range(max(1, n_iter)):
        # E-step in log space.
        log_resp = np.empty((n, k))
        for s in range(k):
            diff2 = (X - means[s]) ** 2
            log_norm = -0.5 * (
                diff2 / variances[s] + np.log(variances[s]) + log_2pi
            ).sum(axis=1)
            log_resp[:, s] = np.log(weights[s] + 1e-300) + log_norm
        log_resp -= log_resp.max(axis=1, keepdims=True)
        r = np.exp(log_resp)
        r /= r.sum(axis=1, keepdims=True)
        # M-step.
        nk = r.sum(axis=0) + 1e-300
        weights = nk / n
        new_means = (r.T @ X) / nk[:, None]
        new_vars = np.empty((k, d))
        for s in range(k):
            diff2 = (X - new_means[s]) ** 2
            new_vars[s] = np.maximum(
                (r[:, s][:, None] * diff2).sum(axis=0) / nk[s], var_floor
            )
        means, variances, resp = new_means, new_vars, r

    return means, variances, resp
