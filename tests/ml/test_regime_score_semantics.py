"""Pin the ONE module that owns what the shadow log's ``score`` means.

Companion to ``test_feature_parity_probe_score_semantics.py``. That file pins
the correction in ONE probe; this one pins the shared accessor both probes now
call, so the next diagnostic to need P(volatile) imports the answer instead of
re-deriving it — which is exactly how two probes ended up printing the same
substituted quantity under two different confident labels on the same day
(2026-07-30, BL-20260730-DIAGNOSTIC-PROVENANCE-CLASS).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Mapping

from ml.predictors.multiclass import MulticlassPredictor

_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sem = _load("_regime_score_semantics", "scripts/ml/_regime_score_semantics.py")


class _CalmHead(MulticlassPredictor):
    """A 2-class regime head that is 97% sure the regime is CALM."""

    def predict_label(self, row: Mapping[str, Any]) -> str:
        return "calm"

    def predict_proba(self, row: Mapping[str, Any]):
        return {"calm": 0.97, "volatile": 0.03}


def test_the_logged_score_is_high_for_a_confidently_calm_head():
    """The whole defect in one assertion.

    ``predict()`` returns 0.97 for a head that is 97% sure the regime is CALM.
    Any diagnostic that prints that under a "P(volatile)" label has INVERTED
    the meaning — 0.97 vs the true 0.03.
    """
    head = _CalmHead()
    assert head.predict({}) == 0.97           # what the shadow log records
    assert head.predict_proba({})["volatile"] == 0.03   # what the gate reads
    assert head.predict({}) != head.predict_proba({})["volatile"]


def test_p_volatile_returns_the_gate_quantity(monkeypatch):
    head = _CalmHead()
    monkeypatch.setattr(sem, "resolve_proba_fn", lambda mid: head.predict_proba)
    assert sem.p_volatile("any-head", {}) == 0.03


def test_p_volatile_is_none_when_the_predictor_cannot_be_resolved(monkeypatch):
    """``None`` is a first-class answer, never a fallback to ``score``.

    A probe running off-trainer has no registry. It must report that the gate
    quantity was unavailable — substituting the logged max-proba is the bug.
    """
    monkeypatch.setattr(sem, "resolve_proba_fn", lambda mid: None)
    assert sem.p_volatile("any-head", {}) is None


def test_rows_helper_reports_an_honest_coverage_denominator(monkeypatch):
    """A silently short list reads as a clean sample — same discipline as
    ``/performance``'s ``rCoverage`` (transparency, never a raw fallback)."""
    calls = {"n": 0}

    def _flaky(row):
        calls["n"] += 1
        return None if calls["n"] % 2 else {"calm": 0.4, "volatile": 0.6}

    monkeypatch.setattr(sem, "resolve_proba_fn", lambda mid: _flaky)
    vals, unresolved = sem.p_volatile_for_rows("h", [{}, {}, {}, {}])
    assert len(vals) + unresolved == 4
    assert unresolved == 2


def test_the_honest_label_never_claims_a_class_probability():
    label = sem.LOGGED_SCORE_LABEL.lower()
    assert "max(proba)" in label
    assert "p(vol" not in label and "volatile" not in label


def test_m20_exit_probe_buckets_p_volatile_not_the_logged_score():
    """Regression for the second instance found in the 2026-07-30 sweep.

    ``m20_ml_exit_probe`` bucketed ``score`` at 0.6/0.4 under the header
    "future_dR by P(volatile) bucket". Because ``score >= 0.5`` by
    construction for a 2-class head, the ``lo`` bucket was EMPTY by
    construction and ``hi`` was a mixture of confidently-volatile and
    confidently-calm bars — so the probe's question was unanswerable from what
    it computed, while its output looked like an answer. This probe's result
    feeds an M20 exit-trigger decision that is Tier-3 downstream.
    """
    src = (_ROOT / "scripts" / "research" / "m20_ml_exit_probe.py").read_text(
        encoding="utf-8")
    assert "from scripts.ml._regime_score_semantics import" in src
    assert "p_volatile(mid, fr)" in src
    # It must NOT silently substitute the logged score when unresolvable.
    assert 'cov["unresolved_models"]' in src
    assert "P(volatile) coverage:" in src
