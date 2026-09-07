"""Tests for ML-2's predictive-bracket model, corpus builder and grader.

These pin the **distinctions**, not today's numbers — the discipline MI-148's
`tests/test_bracket_calibration.py` set for the sibling instrument. A test that
asserts a measured value has to be rewritten every time the corpus grows, and
rewriting a test to match new output is how a suite stops being evidence.

The distinctions that matter here, and why each has a test:

  * **calibration and sharpness are separate verdicts** — the unconditional
    quantile is calibrated BY CONSTRUCTION, so a suite that only checked
    calibration would pass on a model that ignores every feature;
  * **the shuffled-label null GATES sharpness** — an improvement inside the
    null's upper tail is a refusal, and this was a real defect in the first
    cut, not a hypothetical;
  * **"we did not look" is never "we looked and found nothing"** — the
    insufficient-n / degenerate / unknown states;
  * **the bank lever makes an exit price unrecoverable** and the corpus must
    refuse rather than invert it;
  * **outcomes never leak into features.**
"""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "scripts" / "research") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts" / "research"))

from src.research.bracket_quantile import (  # noqa: E402
    CAL_DEGENERATE, CAL_GRADED, CAL_INSUFFICIENT_N, MIN_EVAL_N,
    SHARP_BEATS_BASELINE, SHARP_NO_BETTER, SHARP_NOT_MEASURED,
    SHARP_WITHIN_NULL, QuantileRegressor, calibration_curve, empirical_coverage,
    empirical_quantile, grade_model, mean_absolute_calibration_error,
    mean_pinball, pinball_loss, shuffled_label_control,
)
from ml2_bracket_corpus import (  # noqa: E402
    EXIT_BLENDED, EXIT_EXACT, EXIT_UNREADABLE, FEATURE_NAMES, OUTCOME_NAMES,
    build_row, feature_matrix, per_leg_mfe_quantiles, summarise,
)


# ---------------------------------------------------------------------------
# pinball loss
# ---------------------------------------------------------------------------

def test_pinball_is_asymmetric_and_that_is_the_point():
    # At q=0.9 under-predicting (actual above prediction) must cost 9x more than
    # over-predicting. That asymmetry is what makes the minimiser the quantile
    # rather than the mean.
    under = pinball_loss(actual=10.0, predicted=9.0, q=0.9)
    over = pinball_loss(actual=8.0, predicted=9.0, q=0.9)
    assert under == pytest.approx(0.9)
    assert over == pytest.approx(0.1)
    assert under > over


def test_pinball_is_zero_only_on_an_exact_hit():
    assert pinball_loss(5.0, 5.0, 0.7) == 0.0
    assert pinball_loss(5.0, 5.1, 0.7) > 0.0


def test_mean_pinball_skips_unreadable_rather_than_treating_them_as_zero():
    # A None prediction counted as 0.0 would look like a perfect score on a row
    # nobody predicted.
    got = mean_pinball([1.0, 2.0], [1.0, None], 0.5)
    assert got == pytest.approx(0.0)  # only the readable pair, which is exact


def test_mean_pinball_is_none_when_nothing_is_gradeable():
    assert mean_pinball([], [], 0.5) is None
    assert mean_pinball([1.0], [None], 0.5) is None


# ---------------------------------------------------------------------------
# the baseline
# ---------------------------------------------------------------------------

def test_empirical_quantile_interpolates_and_handles_edges():
    vals = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert empirical_quantile(vals, 0.0) == pytest.approx(1.0)
    assert empirical_quantile(vals, 0.5) == pytest.approx(3.0)
    assert empirical_quantile(vals, 1.0) == pytest.approx(5.0)
    assert empirical_quantile([7.0], 0.9) == pytest.approx(7.0)


def test_empirical_quantile_is_none_not_zero_on_an_empty_sample():
    # 0.0 would be a real level. None is "there is no sample".
    assert empirical_quantile([], 0.5) is None
    assert empirical_quantile([None, "x"], 0.5) is None


def test_the_baseline_is_calibrated_by_construction():
    """The load-bearing fact behind the whole two-bar design.

    If this ever fails, the module's central claim — that calibration alone is
    vacuous — is wrong and the grading scheme needs rethinking.
    """
    rng = random.Random(3)
    y = [rng.expovariate(1 / 0.02) for _ in range(4000)]
    for q in (0.5, 0.7, 0.9):
        base = empirical_quantile(y, q)
        cov = empirical_coverage(y, [base] * len(y))
        assert abs(cov - q) < 0.02, f"q={q} cov={cov}"


# ---------------------------------------------------------------------------
# the model
# ---------------------------------------------------------------------------

def test_unfitted_model_predicts_none_never_zero():
    m = QuantileRegressor(0.8)
    assert m.fitted is False
    assert m.predict([[1.0, 2.0]]) == [None]


def test_fit_refuses_an_empty_or_ragged_sample_without_raising():
    assert QuantileRegressor(0.5).fit([], []).fitted is False
    ragged = QuantileRegressor(0.5).fit([[1.0, 2.0], [1.0]], [1.0, 2.0])
    assert ragged.fitted is False


def test_fit_survives_non_finite_input():
    # A NaN reaching the loss silently poisons every aggregate; it must be
    # dropped at the boundary instead.
    m = QuantileRegressor(0.5).fit([[1.0], [float("nan")], [3.0]], [1.0, 2.0, 3.0])
    assert m.fitted is True
    assert m.n_train == 2


def test_degenerate_target_predicts_the_constant_and_does_not_descend():
    m = QuantileRegressor(0.9).fit([[1.0], [2.0], [3.0]], [0.05, 0.05, 0.05])
    assert m.fitted is True
    preds = m.predict([[1.0], [9.0]])
    assert all(p == pytest.approx(0.05) for p in preds)


def test_model_recovers_a_known_conditional_quantile():
    """y | x is uniform on [0, x], so the true q-quantile is exactly q*x."""
    rng = random.Random(5)
    X = [[rng.uniform(1.0, 5.0)] for _ in range(1500)]
    y = [rng.uniform(0.0, x[0]) for x in X]
    m = QuantileRegressor(0.5, seed=1).fit(X, y)
    assert m.fitted
    # At x=2 the true median is 1.0; at x=4 it is 2.0. A model that ignored x
    # would return the same number for both.
    p2, p4 = m.predict([[2.0], [4.0]])
    assert p4 > p2, "model is not conditioning on the feature at all"
    assert p2 == pytest.approx(1.0, abs=0.35)
    assert p4 == pytest.approx(2.0, abs=0.45)


def test_predictions_are_returned_in_the_callers_units():
    # The fit standardises the target internally; a caller must never receive a
    # z-score. Targets ~0.02 must come back ~0.02, not ~0.
    rng = random.Random(9)
    X = [[rng.uniform(0.0, 1.0)] for _ in range(400)]
    y = [0.02 + rng.expovariate(1 / 0.005) for _ in X]
    m = QuantileRegressor(0.5, seed=2).fit(X, y)
    preds = [p for p in m.predict(X) if p is not None]
    assert 0.005 < sum(preds) / len(preds) < 0.15


def test_quantile_predictions_are_ordered_in_q():
    rng = random.Random(13)
    X = [[rng.uniform(0.0, 1.0)] for _ in range(800)]
    y = [rng.expovariate(1 / 0.02) for _ in X]
    got = []
    for q in (0.3, 0.6, 0.9):
        m = QuantileRegressor(q, seed=4).fit(X, y)
        preds = [p for p in m.predict(X) if p is not None]
        got.append(sum(preds) / len(preds))
    assert got[0] < got[1] < got[2], f"quantiles not monotone in q: {got}"


# ---------------------------------------------------------------------------
# calibration / coverage
# ---------------------------------------------------------------------------

def test_empirical_coverage_counts_at_or_below():
    assert empirical_coverage([1.0, 2.0, 3.0], [2.0, 2.0, 2.0]) == pytest.approx(2 / 3)


def test_empirical_coverage_is_none_not_zero_when_nothing_is_readable():
    assert empirical_coverage([], []) is None
    assert empirical_coverage([1.0], [None]) is None


def test_calibration_curve_and_mace():
    curve = calibration_curve([1.0, 2.0, 3.0, 4.0], {0.5: [2.0] * 4, 0.75: [3.0] * 4})
    assert [r["q"] for r in curve] == [0.5, 0.75]
    assert curve[0]["coverage"] == pytest.approx(0.5)
    assert curve[0]["coverage_error"] == pytest.approx(0.0)
    assert mean_absolute_calibration_error(curve) is not None


def test_mace_is_none_not_zero_when_no_point_is_gradeable():
    # 0.0 would read as perfect calibration.
    curve = calibration_curve([1.0], {0.5: [None]})
    assert mean_absolute_calibration_error(curve) is None


# ---------------------------------------------------------------------------
# grade_model — the three-way states, and the null gate
# ---------------------------------------------------------------------------

def test_insufficient_n_is_not_calibrated_and_not_miscalibrated():
    y = [float(i) for i in range(5)]
    g = grade_model(y, [2.0] * 5, [2.0] * 5, 0.5, min_n=MIN_EVAL_N)
    assert g["calibration_state"] == CAL_INSUFFICIENT_N
    assert g["coverage"] is None
    assert g["calibration_state"] not in (CAL_GRADED,)


def test_degenerate_target_is_its_own_state_not_perfect_calibration():
    y = [0.03] * 60
    g = grade_model(y, [0.03] * 60, [0.03] * 60, 0.5)
    assert g["calibration_state"] == CAL_DEGENERATE
    assert g["coverage"] is None


def test_unknown_when_nothing_is_readable():
    g = grade_model([], [], [], 0.5)
    assert g["calibration_state"] == "unknown"
    assert g["sharpness_state"] == SHARP_NOT_MEASURED


def test_no_better_than_baseline_when_model_loses():
    rng = random.Random(21)
    y = [rng.expovariate(1 / 0.02) for _ in range(200)]
    base = empirical_quantile(y, 0.8)
    bad = [base * 5.0] * len(y)  # wildly too far
    g = grade_model(y, bad, [base] * len(y), 0.8, null_p95=0.0)
    assert g["sharpness_state"] == SHARP_NO_BETTER


def test_an_improvement_inside_the_null_is_a_refusal_not_a_pass():
    """The defect this state exists for. A tiny win over the baseline is
    estimator efficiency, not information."""
    rng = random.Random(23)
    y = [rng.expovariate(1 / 0.02) for _ in range(300)]
    base = empirical_quantile(y, 0.7)
    # A hair better than the baseline.
    slightly_better = [base * 1.001] * len(y)
    g_gated = grade_model(y, slightly_better, [base] * len(y), 0.7, null_p95=0.5)
    g_ungated = grade_model(y, slightly_better, [base] * len(y), 0.7, null_p95=0.0)
    if g_gated["pinball_improvement"] is not None and g_gated["pinball_improvement"] > 0:
        assert g_gated["sharpness_state"] == SHARP_WITHIN_NULL
        assert g_ungated["sharpness_state"] == SHARP_BEATS_BASELINE


def test_missing_null_never_counts_as_the_gate_being_passed():
    """A gate that was not run must not read as a gate that was cleared."""
    rng = random.Random(29)
    y = [rng.expovariate(1 / 0.02) for _ in range(300)]
    base = empirical_quantile(y, 0.7)
    better = [base * 1.001] * len(y)
    g = grade_model(y, better, [base] * len(y), 0.7, null_p95=None)
    assert g["sharpness_state"] != SHARP_BEATS_BASELINE


# ---------------------------------------------------------------------------
# the shuffled-label control
# ---------------------------------------------------------------------------

def test_control_reports_not_measured_rather_than_a_pass_when_unusable():
    c = shuffled_label_control([[1.0]], [1.0], [[1.0]], [1.0], 0.7, trials=2)
    assert c["control_state"] == "not_measured"


def test_control_returns_a_distribution_not_a_single_draw():
    """One Bernoulli draw cannot tell 5% bad luck from a dead null — the
    lesson `e2_null_calibration.py` was written for, and the one that made this
    module's own selftest wrong on its first run."""
    rng = random.Random(31)
    X = [[rng.uniform(0.0, 1.0)] for _ in range(220)]
    y = [rng.expovariate(1 / 0.02) for _ in X]
    c = shuffled_label_control(X[:150], y[:150], X[150:], y[150:], 0.7, trials=6)
    assert c["trials_requested"] == 6
    assert c["trials_usable"] >= 1
    for k in ("null_mean_improvement", "null_p95_improvement", "null_max_improvement"):
        assert k in c


# ---------------------------------------------------------------------------
# the corpus builder
# ---------------------------------------------------------------------------

_EMIT = {
    "strategy": "trend_donchian_BTCUSDT_15m", "symbol": "BTCUSDT",
    "entry_time": "2026-01-02 13:45:00", "exit_time": "2026-01-02 18:00:00",
    "entry": 100.0, "sl": 98.0, "exit_reason": "trail", "direction": "long",
    "gross_r": 2.0, "net_r": 1.9, "mfe_r": 3.0, "confidence": 0.42,
}


def test_percent_of_entry_conversion_is_exact():
    r = build_row(dict(_EMIT))
    # risk = 2.0, entry = 100 -> risk_frac 0.02; mfe_r 3.0 -> 6% of entry.
    assert r["risk_frac"] == pytest.approx(0.02)
    assert r["mfe_frac"] == pytest.approx(0.06)
    assert r["exit_frac"] == pytest.approx(0.04)
    assert r["exit_recoverable"] == EXIT_EXACT
    assert r["mfe_state"] == EXIT_EXACT


def test_short_side_converts_on_the_same_basis():
    raw = dict(_EMIT, direction="short", entry=100.0, sl=102.0)
    r = build_row(raw)
    assert r["risk_frac"] == pytest.approx(0.02)
    assert r["mfe_frac"] == pytest.approx(0.06)
    assert r["is_long"] == 0.0


def test_bank_lever_refuses_the_exit_but_keeps_the_mfe():
    """The one row shape that cannot be inverted — and MFE is unaffected
    because it is a path statistic computed before the blend."""
    r = build_row(dict(_EMIT), bank_frac_asserted=0.5)
    assert r["exit_recoverable"] == EXIT_BLENDED
    assert r["exit_frac"] is None
    assert r["mfe_frac"] == pytest.approx(0.06)
    assert r["mfe_state"] == EXIT_EXACT


def test_zero_risk_is_unreadable_not_a_zero_outcome():
    r = build_row(dict(_EMIT, sl=100.0))
    assert r["exit_recoverable"] == EXIT_UNREADABLE
    assert r["mfe_frac"] is None
    assert r["exit_frac"] is None


def test_missing_entry_is_unreadable():
    r = build_row(dict(_EMIT, entry=None))
    assert r["exit_recoverable"] == EXIT_UNREADABLE
    assert r["risk_frac"] is None


def test_unparseable_timestamp_leaves_time_features_none_not_imputed():
    r = build_row(dict(_EMIT, entry_time="not a date"))
    assert r["hour_sin"] is None and r["dow"] is None
    # and such a row is then DROPPED by the matrix rather than imputed
    X, y, dropped = feature_matrix([r], outcome="mfe_frac")
    assert dropped["missing_feature"] == 1 and dropped["kept"] == 0


def test_hour_is_cyclically_encoded_so_23_and_00_are_adjacent():
    a = build_row(dict(_EMIT, entry_time="2026-01-02 23:00:00"))
    b = build_row(dict(_EMIT, entry_time="2026-01-03 00:00:00"))
    d_adj = math.hypot(a["hour_sin"] - b["hour_sin"], a["hour_cos"] - b["hour_cos"])
    c = build_row(dict(_EMIT, entry_time="2026-01-02 11:00:00"))
    d_far = math.hypot(a["hour_sin"] - c["hour_sin"], a["hour_cos"] - c["hour_cos"])
    assert d_adj < d_far


def test_no_outcome_column_is_a_feature():
    """§ 0.2's failure mode, asserted rather than trusted to review: every one
    of the 11 features that produced every negative exit result was ENDOGENOUS.
    """
    assert not set(FEATURE_NAMES) & set(OUTCOME_NAMES)
    for banned in ("mfe_r", "mfe_frac", "gross_r", "net_r", "exit_frac",
                   "exit_time", "exit_reason", "exit_price"):
        assert banned not in FEATURE_NAMES


def test_feature_matrix_states_why_each_row_was_dropped():
    good = build_row(dict(_EMIT))
    no_outcome = build_row(dict(_EMIT, mfe_r=None))
    bad_feature = build_row(dict(_EMIT, entry_time=""))
    X, y, dropped = feature_matrix([good, no_outcome, bad_feature], outcome="mfe_frac")
    assert dropped["kept"] == 1
    assert dropped["missing_outcome"] == 1
    assert dropped["missing_feature"] == 1
    assert len(X) == len(y) == 1
    assert dropped["kept"] + dropped["missing_outcome"] + dropped["missing_feature"] == 3


def test_summarise_partitions_the_population_exactly():
    rows = [build_row(dict(_EMIT)),
            build_row(dict(_EMIT), bank_frac_asserted=0.3),
            build_row(dict(_EMIT, sl=100.0))]
    s = summarise(rows)
    assert s["n_rows"] == 3
    # the three exit states must sum to the population, with no row uncounted
    assert (s["exit_exact"] + s["exit_blended_unrecoverable"]
            + s["exit_unreadable"]) == s["n_rows"]
    assert s["mfe_exact"] + s["mfe_unreadable"] == s["n_rows"]


def test_per_leg_quantiles_are_monotone_and_carry_the_cap_reach():
    rows = []
    for i in range(50):
        rows.append(build_row(dict(_EMIT, mfe_r=float(i) / 10.0)))
    t = per_leg_mfe_quantiles(rows)
    assert len(t) == 1
    row = t[0]
    assert row["n"] == 50
    assert row["p50"] <= row["p70"] <= row["p80"] <= row["p90"] <= row["p95"]
    assert 0.0 <= row["reach_venue_cap"] <= 1.0


def test_per_leg_quantiles_report_none_not_zero_for_an_empty_leg():
    t = per_leg_mfe_quantiles([build_row(dict(_EMIT, mfe_r=None))])
    # a leg with no readable MFE must not appear with a 0.0 quantile
    assert all(r["n"] > 0 for r in t)


# ---------------------------------------------------------------------------
# the E4 dispersion arm must be the SAME computation as the headline
# ---------------------------------------------------------------------------

def test_dispersion_arm_at_the_headline_split_reproduces_the_headline():
    """A stability claim about a DIFFERENT measurement reads as corroboration
    and is worse than no claim at all.

    This regressed once for real: the arms ran at `control_trials=3` while the
    headline ran at 10, so every arm reported `calibrated_and_sharper` and
    `split_sensitive: False` beneath a headline of
    `calibrated_but_no_sharper_than_baseline`.
    """
    import ml2_bracket_train_eval as ev

    rng = random.Random(41)
    rows = []
    for i in range(500):
        rf = rng.uniform(0.005, 0.05)
        rows.append({
            "leg": "L", "entry_time": f"2026-01-{1 + i % 28:02d} {i % 24:02d}:00:00",
            "risk_frac": rf, "is_long": float(i % 2), "confidence": rng.random(),
            "hour_sin": math.sin(i), "hour_cos": math.cos(i), "dow": float(i % 7),
            "mfe_frac": max(0.0, 3.0 * rf + rng.expovariate(1 / 0.01)),
        })
    head = ev.evaluate(rows, quantiles=(0.5, 0.8), control_trials=4, seed=0)
    disp = ev.dispersion(rows, headline_verdict=head["verdict"],
                         quantiles=(0.5, 0.8), control_trials=4, seed=0)
    assert disp["arms_consistent_with_headline"] is True, (
        f"arm at 0.65 gave {[a for a in disp['arms'] if a['split'] == 0.65]} "
        f"but the headline gave {head['verdict']} — not the same computation")
    assert disp["control_trials"] == 4


def test_dispersion_consistency_is_none_not_true_when_not_checked():
    """`None` is 'we did not compare', which must not read as agreement."""
    import ml2_bracket_train_eval as ev
    rows = [{"leg": "L", "entry_time": "2026-01-01 00:00:00", "risk_frac": 0.02,
             "is_long": 1.0, "confidence": 0.1, "hour_sin": 0.0, "hour_cos": 1.0,
             "dow": 1.0, "mfe_frac": 0.03} for _ in range(10)]
    disp = ev.dispersion(rows, quantiles=(0.5,), control_trials=1)
    assert disp["arms_consistent_with_headline"] is None
