"""Tests for the causal Gaussian-HMM regime family (S-MLOPT-S14 / Phase 3.2).

Covers the trainer (``ml.trainers.causal_hmm_regime.CausalHMMRegimeTrainer``)
and the filtered predictor
(``ml.predictors.causal_hmm_regime.CausalHMMRegimePredictor``). The headline
discipline test is **causal invariance**: the filtered posterior at bar t must
not change when future bars are appended — proving the forward-only recursion
never leaks the future (no Viterbi / forward-backward smoothing).
"""
from __future__ import annotations

import pytest

from ml.predictors.causal_hmm_regime import CausalHMMRegimePredictor
from ml.trainers.causal_hmm_regime import CausalHMMRegimeTrainer


def _toy_state():
    """A hand-built 2-state HMM: state 0 = low vol → 'range', state 1 = high
    vol → 'volatile'. Sticky transitions; one feature."""
    return {
        "trainer": "ml.trainers.causal_hmm_regime.CausalHMMRegimeTrainer",
        "feature_columns": ["yang_zhang_vol"],
        "means": [[0.001], [0.02]],
        "variances": [[1e-7], [1e-5]],
        "transition": [[0.9, 0.1], [0.1, 0.9]],
        "start_prob": [0.5, 0.5],
        "state_label_proba": [
            {"range": 0.95, "volatile": 0.05},
            {"range": 0.05, "volatile": 0.95},
        ],
        "class_labels": ["range", "volatile"],
        "time_column": "ts",
    }


def _row(ts, vol, label=None):
    r = {"ts": ts, "yang_zhang_vol": vol}
    if label is not None:
        r["regime_label"] = label
    return r


class TestPredictorBasics:
    def test_proba_sums_to_one(self):
        p = CausalHMMRegimePredictor(_toy_state())
        proba = p.predict_proba(_row(0, 0.001))
        assert set(proba) == {"range", "volatile"}
        assert sum(proba.values()) == pytest.approx(1.0)

    def test_low_vol_predicts_range_high_vol_predicts_volatile(self):
        p = CausalHMMRegimePredictor(_toy_state())
        # A run of low-vol bars settles on 'range'.
        for t in range(5):
            lbl = p.predict_label(_row(t, 0.001))
        assert lbl == "range"
        # A fresh run of high-vol bars settles on 'volatile'.
        p.reset()
        for t in range(5):
            lbl = p.predict_label(_row(t, 0.02))
        assert lbl == "volatile"

    def test_missing_feature_does_not_crash(self):
        p = CausalHMMRegimePredictor(_toy_state())
        # None feature → skipped in the product, filter still advances.
        proba = p.predict_proba({"ts": 0, "yang_zhang_vol": None})
        assert sum(proba.values()) == pytest.approx(1.0)

    def test_missing_required_state_keys_raise(self):
        bad = _toy_state()
        del bad["means"]
        with pytest.raises(ValueError):
            CausalHMMRegimePredictor(bad)


class TestCausality:
    """The forward-only filter must not let future bars change past outputs."""

    def test_filtered_posterior_invariant_to_future(self):
        seq = [_row(t, v) for t, v in enumerate([0.001, 0.001, 0.02, 0.02, 0.001])]
        future = [_row(t, v) for t, v in enumerate([0.02, 0.001, 0.02], start=5)]

        # Run 1: just the first 5 bars, capture each step's proba.
        p1 = CausalHMMRegimePredictor(_toy_state())
        first = [dict(p1.predict_proba(r)) for r in seq]

        # Run 2: the same 5 bars PLUS 3 future bars.
        p2 = CausalHMMRegimePredictor(_toy_state())
        with_future = [dict(p2.predict_proba(r)) for r in (seq + future)]

        # The first 5 steps must be byte-identical — the future cannot leak back.
        for a, b in zip(first, with_future[:5]):
            assert a == b

    def test_auto_reset_on_nonmonotonic_ts(self):
        p = CausalHMMRegimePredictor(_toy_state())
        # Settle into 'volatile'.
        for t in range(6):
            p.predict_label(_row(t, 0.02))
        hot = dict(p.predict_proba(_row(6, 0.02)))
        # A ts that goes backwards is a new sequence → filter resets to pi, so
        # a single low-vol bar is scored from the prior, not from the hot state.
        after_reset = dict(p.predict_proba(_row(0, 0.001)))
        fresh = CausalHMMRegimePredictor(_toy_state())
        fresh_first = dict(fresh.predict_proba(_row(0, 0.001)))
        assert after_reset == fresh_first
        assert after_reset != hot


class TestTrainer:
    def _make_sequence(self, n_each=120):
        """Two clearly-separated vol regimes in alternating blocks."""
        rows = []
        ts = 0
        for block in range(4):
            low = block % 2 == 0
            for _ in range(n_each):
                vol = 0.001 if low else 0.02
                rows.append(
                    {
                        "ts": ts,
                        "yang_zhang_vol": vol,
                        "rolling_log_return_vol": vol * 1.1,
                        "regime_label": "range" if low else "volatile",
                    }
                )
                ts += 1
        return rows

    def test_fit_produces_valid_state(self):
        rows = self._make_sequence()
        state = CausalHMMRegimeTrainer().fit(
            rows,
            {
                "target_column": "regime_label",
                "feature_columns": ["yang_zhang_vol", "rolling_log_return_vol"],
                "n_states": 3,
                "seed": 42,
            },
        )
        assert state["trainer"] == (
            "ml.trainers.causal_hmm_regime.CausalHMMRegimeTrainer"
        )
        k = state["n_states"]
        assert len(state["means"]) == k
        assert len(state["transition"]) == k
        assert len(state["start_prob"]) == k
        assert len(state["state_label_proba"]) == k
        # Transition rows are stochastic.
        for row in state["transition"]:
            assert sum(row) == pytest.approx(1.0)
        assert sum(state["start_prob"]) == pytest.approx(1.0)

    def test_fit_is_deterministic(self):
        rows = self._make_sequence()
        cfg = {"feature_columns": ["yang_zhang_vol"], "n_states": 2, "seed": 7}
        a = CausalHMMRegimeTrainer().fit(rows, cfg)
        b = CausalHMMRegimeTrainer().fit(rows, cfg)
        assert a["means"] == b["means"]
        assert a["transition"] == b["transition"]

    def test_label_projection_class_weight_lifts_rare_class(self):
        """M19 T0.2 salvage: `balanced` class-weighting the state->label
        projection up-weights the rare class in every state, and leaves the
        default (no knob) behaviour byte-identical."""
        # Rare volatile: 5% of rows, otherwise a clean two-vol structure.
        rows = []
        for i in range(1000):
            volatile = i % 20 == 0
            vol = 0.02 if volatile else 0.001
            rows.append(
                {
                    "ts": i,
                    "yang_zhang_vol": vol,
                    "rolling_log_return_vol": vol * 1.1,
                    "regime_label": "volatile" if volatile else "range",
                }
            )
        cfg = {
            "feature_columns": ["yang_zhang_vol", "rolling_log_return_vol"],
            "n_states": 3,
            "seed": 42,
        }
        plain = CausalHMMRegimeTrainer().fit(rows, cfg)
        weighted = CausalHMMRegimeTrainer().fit(
            rows, {**cfg, "label_projection_class_weight": "balanced"}
        )
        # Default carries no knob and no marker.
        assert "label_projection_class_weight" not in plain
        assert weighted["label_projection_class_weight"] == "balanced"
        # Balanced up-weights the minority `volatile` column, so its posterior
        # mass rises in every state (>=) and strictly in at least one.
        pv = [s["volatile"] for s in plain["state_label_proba"]]
        wv = [s["volatile"] for s in weighted["state_label_proba"]]
        assert all(w >= p - 1e-12 for w, p in zip(wv, pv))
        assert any(w > p + 1e-9 for w, p in zip(wv, pv))
        # Each per-state distribution still normalises.
        for s in weighted["state_label_proba"]:
            assert sum(s.values()) == pytest.approx(1.0)

    def test_fit_then_predict_separates_regimes(self):
        rows = self._make_sequence()
        state = CausalHMMRegimeTrainer().fit(
            rows, {"feature_columns": ["yang_zhang_vol"], "n_states": 2, "seed": 42},
        )
        p = CausalHMMRegimePredictor(state)
        # Feed a long low-vol run → 'range'; then reset + high-vol run → 'volatile'.
        for t in range(30):
            lo = p.predict_label({"ts": t, "yang_zhang_vol": 0.001})
        p.reset()
        for t in range(30):
            hi = p.predict_label({"ts": t, "yang_zhang_vol": 0.02})
        assert lo == "range"
        assert hi == "volatile"

    def test_empty_rows_yield_scorable_uniform_model(self):
        state = CausalHMMRegimeTrainer().fit(
            [], {"feature_columns": ["yang_zhang_vol"], "class_labels": ["range", "volatile"]},
        )
        p = CausalHMMRegimePredictor(state)
        proba = p.predict_proba({"ts": 0, "yang_zhang_vol": 0.01})
        assert sum(proba.values()) == pytest.approx(1.0)


class TestEvaluatorIntegration:
    def test_scores_through_multiclass_evaluator(self):
        from ml.evaluators.multiclass_classification import (
            MulticlassClassificationEvaluator,
        )

        trainer = CausalHMMRegimeTrainer()
        rows = []
        ts = 0
        for block in range(4):
            low = block % 2 == 0
            for _ in range(80):
                vol = 0.001 if low else 0.02
                rows.append(
                    {
                        "ts": ts,
                        "yang_zhang_vol": vol,
                        "regime_label": "range" if low else "volatile",
                    }
                )
                ts += 1
        cfg = {"feature_columns": ["yang_zhang_vol"], "n_states": 2, "seed": 42}
        state = trainer.fit(rows, cfg)
        metrics = MulticlassClassificationEvaluator().score(
            state, rows, {"target_column": "regime_label"},
        )
        # Cleanly-separated synthetic regimes → high accuracy through the
        # standard evaluator path (predictor auto-resolved via PREDICTOR_CLASS).
        assert metrics["n_eval"] == float(len(rows))
        assert metrics["accuracy"] > 0.9
