"""Tests for the regime-alignment calibrator (piece 4 of the A+B conviction
program — ``ml.calibration.regime_alignment`` + the fit CLI's pure functions).

Hermetic: synthetic in-memory corpora only, no DB / network / sklearn.
"""

from __future__ import annotations

import pytest

from ml.calibration.calibrators import Calibrator, ConstantCalibrator, PlattCalibrator
from ml.calibration.regime_alignment import (
    POOLED_DIRECTION,
    REGIME_ALIGNMENT_KEY,
    canon_direction,
    corpus_for_model,
    fit_model_section,
    fit_regime_alignment_calibrator,
    load_regime_alignment,
    predict_alignment,
    regime_score_from_model_scores,
)

MODEL = "btc-regime-1h-lgbm-yz-v1"


# --------------------------------------------------------------------------- #
# corpus transform
# --------------------------------------------------------------------------- #
def test_canon_direction():
    assert canon_direction("long") == "long"
    assert canon_direction("buy") == "long"
    assert canon_direction("SHORT") == "short"
    assert canon_direction("sell") == "short"
    assert canon_direction("sideways") is None
    assert canon_direction(None) is None


def test_regime_score_extraction():
    ms = {MODEL: {"stage": "advisory", "score": 0.73}}
    assert regime_score_from_model_scores(ms, MODEL) == pytest.approx(0.73)
    assert regime_score_from_model_scores(ms, "other") is None
    assert regime_score_from_model_scores({MODEL: {"score": "x"}}, MODEL) is None
    assert regime_score_from_model_scores(None, MODEL) is None


def test_corpus_for_model_splits_by_direction_and_pools():
    rows = [
        {"direction": "long", "pnl": 5.0, "model_scores": {MODEL: {"score": 0.9}}},
        {"direction": "short", "pnl": -3.0, "model_scores": {MODEL: {"score": 0.1}}},
        # row with no score for MODEL — skipped
        {"direction": "long", "pnl": 2.0, "model_scores": {"other": {"score": 0.5}}},
        # won via explicit flag
        {"direction": "long", "won": True, "model_scores": {MODEL: {"score": 0.8}}},
    ]
    by_dir = corpus_for_model(rows, MODEL)
    assert by_dir[POOLED_DIRECTION] == [(0.9, 1), (0.1, 0), (0.8, 1)]
    assert by_dir["long"] == [(0.9, 1), (0.8, 1)]
    assert by_dir["short"] == [(0.1, 0)]


def test_corpus_never_raises_on_garbage():
    rows = ["not-a-dict", {"model_scores": "x"}, {}, {"model_scores": {MODEL: "y"}}]
    by_dir = corpus_for_model(rows, MODEL)
    assert by_dir[POOLED_DIRECTION] == []


# --------------------------------------------------------------------------- #
# fit — monotone in score, degenerate fallbacks
# --------------------------------------------------------------------------- #
def _monotone_corpus(n: int = 40) -> list[tuple[float, int]]:
    """High score → win, low score → loss (a clean monotone relationship)."""
    pairs: list[tuple[float, int]] = []
    for i in range(n):
        score = i / (n - 1)  # 0..1
        won = 1 if score >= 0.5 else 0
        pairs.append((score, won))
    return pairs


def test_fit_is_monotone_in_score():
    cal = fit_regime_alignment_calibrator(_monotone_corpus(), method="logistic")
    assert isinstance(cal, PlattCalibrator)
    lo = cal.predict(0.05)
    mid = cal.predict(0.5)
    hi = cal.predict(0.95)
    assert lo < mid < hi  # higher regime score → higher P(favorable)
    assert 0.0 <= lo <= 1.0 and 0.0 <= hi <= 1.0


def test_fit_degenerate_single_class_is_constant():
    pairs = [(0.5, 1)] * 20  # all wins → nothing to discriminate
    cal = fit_regime_alignment_calibrator(pairs, method="logistic")
    assert isinstance(cal, ConstantCalibrator)
    assert cal.predict(0.0) == pytest.approx(1.0)


def test_fit_below_min_rows_is_constant():
    pairs = [(0.9, 1), (0.1, 0)]  # 2 rows < min_rows
    cal = fit_regime_alignment_calibrator(pairs, method="logistic", min_rows=10)
    assert isinstance(cal, ConstantCalibrator)
    assert cal.predict(0.9) == pytest.approx(0.5)  # base rate of [1,0]


def test_fit_empty_is_constant_half():
    cal = fit_regime_alignment_calibrator([], method="logistic")
    assert isinstance(cal, ConstantCalibrator)
    assert cal.predict(0.0) == pytest.approx(0.5)


# --------------------------------------------------------------------------- #
# fit_model_section — per-direction sections + thin-direction suppression
# --------------------------------------------------------------------------- #
def test_fit_model_section_emits_pooled_and_directions():
    rows = []
    # long: monotone, short: inverted (direction sensitivity)
    for i in range(40):
        s = i / 39.0
        rows.append({"direction": "long", "pnl": 1.0 if s >= 0.5 else -1.0,
                     "model_scores": {MODEL: {"score": s}}})
    for i in range(40):
        s = i / 39.0
        rows.append({"direction": "short", "pnl": 1.0 if s < 0.5 else -1.0,
                     "model_scores": {MODEL: {"score": s}}})
    section = fit_model_section(rows, MODEL, method="logistic", min_rows=10)
    assert "all" in section and "long" in section and "short" in section

    long_cal = Calibrator.from_dict(section["long"])
    short_cal = Calibrator.from_dict(section["short"])
    # long: high score favorable; short: low score favorable — opposite slopes
    assert long_cal.predict(0.9) > long_cal.predict(0.1)
    assert short_cal.predict(0.9) < short_cal.predict(0.1)


def test_fit_model_section_suppresses_thin_direction():
    rows = []
    for i in range(40):  # plenty of long
        s = i / 39.0
        rows.append({"direction": "long", "pnl": 1.0 if s >= 0.5 else -1.0,
                     "model_scores": {MODEL: {"score": s}}})
    # only 3 short rows → below min_rows → suppressed (pooled still covers)
    for s, w in [(0.2, 1), (0.3, 0), (0.4, 1)]:
        rows.append({"direction": "short", "pnl": 1.0 if w else -1.0,
                     "model_scores": {MODEL: {"score": s}}})
    section = fit_model_section(rows, MODEL, method="logistic", min_rows=10)
    assert "all" in section and "long" in section
    assert "short" not in section


# --------------------------------------------------------------------------- #
# load + predict
# --------------------------------------------------------------------------- #
def test_load_and_predict_direction_preference_and_fallback():
    raw = {
        REGIME_ALIGNMENT_KEY: {
            MODEL: {
                "all": PlattCalibrator(a=0.0, b=0.0).to_dict(),      # sigmoid(0)=0.5
                "long": PlattCalibrator(a=0.0, b=10.0).to_dict(),    # ~1.0
            }
        }
    }
    loaded = load_regime_alignment(raw)
    cals = loaded[MODEL]
    # long → direction-specific calibrator
    assert predict_alignment(cals, 0.5, "long") == pytest.approx(1.0, abs=1e-3)
    # short → no short calibrator → pooled "all" fallback (0.5)
    assert predict_alignment(cals, 0.5, "short") == pytest.approx(0.5)
    # unknown direction → pooled fallback
    assert predict_alignment(cals, 0.5, None) == pytest.approx(0.5)


def test_predict_alignment_missing_and_bad_score():
    cals = {POOLED_DIRECTION: PlattCalibrator(a=1.0, b=0.0)}
    assert predict_alignment(cals, "not-a-number", "long") is None
    assert predict_alignment({}, 0.5, "long") is None  # no calibrators at all


def test_load_regime_alignment_fail_permissive():
    # missing section
    assert load_regime_alignment({}) == {}
    assert load_regime_alignment(None) == {}
    # malformed section / model / direction entries are skipped, not raised
    raw = {
        REGIME_ALIGNMENT_KEY: {
            MODEL: {"all": {"method": "nonsense"}, "long": "not-a-dict"},
            "bad_model": "not-a-mapping",
        }
    }
    assert load_regime_alignment(raw) == {}  # nothing deserializable


def test_load_regime_alignment_section_not_mapping():
    assert load_regime_alignment({REGIME_ALIGNMENT_KEY: "x"}) == {}


# --------------------------------------------------------------------------- #
# fit CLI pure functions (corpus transform + fit) — no DB / network
# --------------------------------------------------------------------------- #
def test_fit_cli_regime_model_ids_and_artifact():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "fit_regime_alignment_calibrators",
        Path(__file__).resolve().parents[2]
        / "scripts" / "ml" / "fit_regime_alignment_calibrators.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    rows = []
    for i in range(40):
        s = i / 39.0
        rows.append({"direction": "long", "pnl": 1.0 if s >= 0.5 else -1.0,
                     "model_scores": {MODEL: {"score": s},
                                      "setup-quality-baseline-v0": {"score": 1.2}}})
    # only the regime head is a c_reg model_id (setup-quality is c_setup)
    assert mod.regime_model_ids(rows) == [MODEL]

    section, report = mod.fit_artifact(rows, method="logistic", min_rows=10)
    assert MODEL in section
    assert "all" in section[MODEL] and "long" in section[MODEL]
    # the fitted "all" calibrator round-trips + is monotone
    cal = Calibrator.from_dict(section[MODEL]["all"])
    assert cal.predict(0.9) > cal.predict(0.1)
    assert report[MODEL]["directions_fit"]  # report carries what was fit


def test_fit_cli_empty_corpus_yields_empty_section():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "fit_regime_alignment_calibrators_2",
        Path(__file__).resolve().parents[2]
        / "scripts" / "ml" / "fit_regime_alignment_calibrators.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    section, report = mod.fit_artifact([], method="logistic", min_rows=10)
    assert section == {}
    assert report == {}
