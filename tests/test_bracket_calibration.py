"""Tests for the E3.6 calibration instrument.

The bar these pin is not "the numbers today are X" — the fleet is retuned and
that would fail every time. It is the set of DISTINCTIONS the module exists to
keep apart, each of which this repo has previously lost somewhere else:

  * a missing INPUT vs a missing DECLARATION (unreadable vs no_target_declared)
  * "we did not look" vs "it did not happen" (target_provenance unknown)
  * an empty population vs a rate of zero (None vs 0.0)
  * a venue artefact vs a chosen level (clamp_bound)
"""

import pytest

from src.runtime.bracket_calibration import (
    GRADE_DEGENERATE, GRADE_NO_TARGET, GRADE_OK, GRADE_UNREADABLE,
    TARGET_PROVENANCE_MAY_HAVE_MOVED, TARGET_PROVENANCE_STATIC,
    TARGET_PROVENANCE_UNKNOWN, TP_VENUE_CAP_PCT,
    grade_trade, quantile, summarise,
)

LONG = {"entry_price": 100.0, "exit_price": 105.0,
        "take_profit_1": 110.0, "direction": "long", "strategy_name": "leg"}


def _g(**over):
    row = dict(LONG)
    row.update(over)
    return grade_trade(row, acting_tp_producer_strategies=[])


class TestBasis:
    def test_percent_of_entry_long(self):
        g = _g()
        assert g["grade"] == GRADE_OK
        assert g["target_pct"] == pytest.approx(0.10)
        assert g["exit_pct"] == pytest.approx(0.05)
        assert g["attainment"] == pytest.approx(0.5)
        assert g["reached_target"] is False

    def test_short_is_graded_on_the_same_basis_sign_flipped(self):
        g = _g(direction="short", exit_price=95.0, take_profit_1=90.0)
        assert g["target_pct"] == pytest.approx(0.10)
        assert g["exit_pct"] == pytest.approx(0.05)

    def test_reaching_the_target_is_inclusive(self):
        assert _g(exit_price=110.0)["reached_target"] is True

    def test_the_stop_is_never_read(self):
        """The R denominator is contaminated; this module must not touch it.

        A row whose `stop_loss` is absurd (the trailed-stop contamination
        MI-144 measured) must grade identically to one without it.
        """
        assert _g(stop_loss=109.9999) == _g(stop_loss=None) == _g()


class TestStatesAreNeverCollapsed:
    def test_missing_input_is_unreadable_not_a_missed_target(self):
        g = _g(exit_price=None)
        assert g["grade"] == GRADE_UNREADABLE
        assert g["reached_target"] is None

    def test_missing_declaration_is_its_own_grade(self):
        g = _g(take_profit_1=None)
        assert g["grade"] == GRADE_NO_TARGET
        # It still knows where the trade ENDED — only the claim is missing.
        assert g["exit_pct"] == pytest.approx(0.05)

    def test_unreadable_and_no_target_are_different_grades(self):
        assert _g(exit_price=None)["grade"] != _g(take_profit_1=None)["grade"]

    def test_target_on_the_wrong_side_is_degenerate_not_a_target(self):
        assert _g(take_profit_1=90.0)["grade"] == GRADE_DEGENERATE

    def test_nonpositive_entry_is_degenerate(self):
        assert _g(entry_price=0.0)["grade"] == GRADE_DEGENERATE


class TestTargetProvenance:
    def test_none_means_we_did_not_look_and_is_not_static(self):
        g = grade_trade(LONG)  # caller established nothing
        assert g["target_provenance"] == TARGET_PROVENANCE_UNKNOWN

    def test_empty_set_is_a_real_answer_not_an_absent_one(self):
        assert _g()["target_provenance"] == TARGET_PROVENANCE_STATIC

    def test_a_strategy_with_an_acting_producer_is_flagged(self):
        g = grade_trade(LONG, acting_tp_producer_strategies=["leg"])
        assert g["target_provenance"] == TARGET_PROVENANCE_MAY_HAVE_MOVED


class TestClampRecogniser:
    def test_a_target_at_the_cap_is_recognised_as_the_artefact(self):
        assert _g(take_profit_1=100.0 * (1 + TP_VENUE_CAP_PCT))["clamp_bound"] is True

    def test_a_chosen_level_well_inside_the_cap_is_not(self):
        assert _g(take_profit_1=101.5)["clamp_bound"] is False

    def test_the_recogniser_fires_on_shorts_too(self):
        g = _g(direction="short", exit_price=99.0,
               take_profit_1=100.0 * (1 - TP_VENUE_CAP_PCT))
        assert g["clamp_bound"] is True

    def test_a_genuine_target_just_outside_tolerance_is_not_swept_up(self):
        # 10.5% is a real (if unusual) choice, not the 9.9% artefact.
        assert _g(take_profit_1=110.5)["clamp_bound"] is False


class TestSummariseDenominators:
    def test_empty_population_yields_none_never_zero(self):
        s = summarise([])
        assert s["n_graded"] == 0
        assert s["reach_rate"] is None
        assert s["clamp_bound_rate"] is None

    def test_zero_is_a_real_rate_and_is_reported_as_zero(self):
        s = summarise([_g(), _g()])
        assert s["n_graded"] == 2
        assert s["reach_rate"] == 0.0

    def test_ungradeable_rows_are_excluded_from_rates_but_kept_in_counts(self):
        s = summarise([_g(), _g(exit_price=None), _g(take_profit_1=None)])
        assert s["n_input"] == 3
        assert s["n_graded"] == 1
        assert s["grade_counts"][GRADE_UNREADABLE] == 1
        assert s["grade_counts"][GRADE_NO_TARGET] == 1

    def test_target_quantile_locates_the_claim_in_the_outcomes(self):
        # Nine trades end at +1%, one at +50%; the target is +10%. Nine of ten
        # realised exits sit at or below it.
        rows = [_g(exit_price=101.0) for _ in range(9)] + [_g(exit_price=150.0)]
        assert summarise(rows)["target_quantile_in_exits"] == pytest.approx(0.9)


class TestQuantile:
    def test_empty_is_none_not_zero(self):
        assert quantile([], 0.5) is None

    def test_single_value(self):
        assert quantile([3.0], 0.9) == 3.0

    def test_interpolates(self):
        assert quantile([0.0, 1.0], 0.5) == pytest.approx(0.5)

    def test_clamps_out_of_range_q(self):
        assert quantile([1.0, 2.0, 3.0], 5.0) == 3.0
        assert quantile([1.0, 2.0, 3.0], -1.0) == 1.0


class TestNeverRaises:
    @pytest.mark.parametrize("row", [
        {}, {"entry_price": "abc"}, {"entry_price": float("nan")},
        {"entry_price": 1, "exit_price": float("inf")},
        {"entry_price": 1, "exit_price": 1, "take_profit_1": "x"},
    ])
    def test_garbage_in_grade_out(self, row):
        g = grade_trade(row)
        assert g["grade"] in (GRADE_OK, GRADE_NO_TARGET,
                              GRADE_UNREADABLE, GRADE_DEGENERATE)

    def test_object_rows_work_too(self):
        class R:
            entry_price, exit_price, take_profit_1 = 100.0, 105.0, 110.0
            direction, strategy_name = "long", "leg"
        assert grade_trade(R())["grade"] == GRADE_OK
