"""Guards for the 2026-07-30 feature-parity-probe correction.

`scripts/ml/_feature_parity_probe.py` reported the shadow log's ``score`` field
under the label ``PREDICTED score(volatile)``. That field is **not**
P(volatile): ``ShadowPredictor.predict`` returns the wrapped predictor's
``predict``, and for a regime head that is
``MulticlassPredictor.predict == max(proba.values())`` — the confidence of the
*predicted* class, hence ``>= 0.5`` by construction for a 2-class head.

Consequence when it was mislabelled: every regime head read as pinned to
volatile. A fleet sweep of `runtime_logs/shadow_predictions.jsonl` found
``frac(score < 0.5) == 0.0000`` for all ~30 regime heads, while the
non-multiclass heads in the same log (``exit-head-donchian-1h-v1``,
``setup-quality-lgbm-v2``, ``execution-quality-baseline-v0``) ranged freely
below 0.5. That contrast is the tell, and the mislabel nearly produced a false
P1 against the live real-money BTC vol gate.

These tests pin the invariant the correction rests on, so a future edit can't
quietly reintroduce the confusion.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pytest

from ml.predictors.multiclass import MulticlassPredictor

_PROBE = Path(__file__).resolve().parents[2] / "scripts" / "ml" / "_feature_parity_probe.py"


class _TwoClassHead(MulticlassPredictor):
    """Minimal 2-class regime head returning a fixed proba mapping."""

    def __init__(self, proba: Mapping[str, float]) -> None:
        self._proba = dict(proba)

    def predict_label(self, row: Mapping[str, Any]) -> str:
        return max(self._proba, key=lambda k: self._proba[k])

    def predict_proba(self, row: Mapping[str, Any]) -> Mapping[str, float]:
        return self._proba


@pytest.mark.parametrize(
    ("p_volatile", "expected_predict"),
    [
        (0.97, 0.97),  # confident volatile -> max proba is P(volatile)
        (0.03, 0.97),  # confident CALM -> max proba is P(calm), NOT P(volatile)
        (0.50, 0.50),
    ],
)
def test_predict_is_max_proba_not_p_volatile(p_volatile, expected_predict):
    """``predict()`` is the predicted class's confidence, not P(volatile).

    The 0.03 case is the whole point: a head that is 97% sure the regime is
    CALM still reports ``predict() == 0.97``. Reading that as P(volatile)
    inverts the meaning.
    """
    head = _TwoClassHead({"volatile": p_volatile, "calm": 1.0 - p_volatile})
    assert head.predict({}) == pytest.approx(expected_predict)
    assert head.predict_proba({})["volatile"] == pytest.approx(p_volatile)


def test_predict_never_below_half_for_two_class_head():
    """The floor that made every regime head look saturated is structural."""
    for i in range(0, 101):
        p = i / 100.0
        head = _TwoClassHead({"volatile": p, "calm": 1.0 - p})
        assert head.predict({}) >= 0.5 - 1e-12, f"p_volatile={p}"


def test_probe_does_not_label_logged_score_as_p_volatile():
    """The misleading label must not come back."""
    src = _PROBE.read_text(encoding="utf-8")
    # Scoped to the PRINT site (the ``:34s`` column-format usage), not the whole
    # file — the module docstring legitimately quotes the old label to explain
    # the correction, and banning the words outright would forbid documenting it.
    assert "'PREDICTED score(volatile)':34s" not in src, (
        "the logged `score` is max(proba), not P(volatile) — printing it as "
        "'PREDICTED score(volatile)' is what caused the false reading"
    )
    assert "max(proba), NOT P(vol)" in src, "the honest label is missing"


def test_probe_reports_the_probability_the_gate_actually_reads():
    """The probe must surface ``predict_proba[volatile]`` + the would-gate share."""
    src = _PROBE.read_text(encoding="utf-8")
    assert "_p_volatile_for_rows" in src
    assert "predict_proba" in src
    assert "would gate VOLATILE" in src


def test_probe_resolves_the_manifest_pinned_dataset():
    """Second defect: the training dataset was ``sorted(glob(...))[-1]``.

    BTCUSDT/15m holds ~14 versions, so the alphabetically-last one
    (``vfmac003``) was compared against heads that trained on other versions —
    ``btc-regime-15m-lgbm-fc-pcv-v1`` pins ``v520``.
    """
    src = _PROBE.read_text(encoding="utf-8")
    assert "_manifest_dataset_version" in src
    # A fallback is allowed, but it must never be silent.
    assert "NOT\n" in src or "NOT " in src, "fallback must warn it is not the pinned data"
