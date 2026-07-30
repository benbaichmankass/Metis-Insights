"""M3 — the expectation-model vs survey-consensus validator.

This is the operator-approved M1 gate's own satisfiability condition for option (b): the PIT
expectation model must be shown to track survey consensus where both exist. Its kill
condition is real, so the validator must be hard to misread.

The load-bearing property is `TestProvenanceGuard`: the FIRST run of this tool against the
then-committed backfill produced Spearman -0.5982 and an OLS slope of -49205 — which reads as
"the model anti-correlates with the survey", i.e. the kill condition tripping. It was nothing
of the kind: that file predated the units + release-date fixes. A plausible, decision-shaped,
completely wrong number. Hence the tool checks its own inputs' provenance.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts", "macro"))

v = pytest.importorskip("econ_expectation_validate")


def survey_row(kind, date, consensus, actual):
    return {"kind": kind, "scheduled_for": date, "status": "resolved",
            "expected": {"consensus": consensus},
            "realized_outcome": {"consensus": consensus, "actual": actual}}


def model_row(kind, date, expectation, actual, *, stamped=True):
    r = {"kind": kind, "scheduled_for": date, "backfilled": True,
         "expectation_source": "model:seasonal_ar_ols_v1",
         "expected": {"consensus": expectation},
         "realized_outcome": {"consensus": expectation, "actual": actual}}
    if stamped:
        r["release_date_basis"] = "modeled_lag"
        r["units_transform"] = "identity"
    return r


class TestStatistics:
    def test_spearman_is_1_on_a_monotone_pair(self):
        assert v.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)

    def test_spearman_is_minus_1_when_inverted(self):
        assert v.spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)

    def test_ties_do_not_break_the_rank(self):
        assert v.spearman([1, 1, 2, 3], [1, 1, 2, 3]) == pytest.approx(1.0)

    def test_slope_exposes_a_scale_error_that_correlation_hides(self):
        """The whole reason the slope is reported: perfect correlation, wrong units."""
        xs, ys = [1.0, 2.0, 3.0], [1000.0, 2000.0, 3000.0]
        assert v.spearman(xs, ys) == pytest.approx(1.0)
        assert v.ols_slope(xs, ys) == pytest.approx(1000.0)

    def test_sign_agreement_excludes_zero_surprises(self):
        """A zero surprise has no direction; counting it would manufacture agreement."""
        assert v.sign_agreement([1, -1, 0], [1, -1, 5]) == pytest.approx(1.0)

    def test_sign_agreement_is_none_when_every_pair_has_a_zero(self):
        assert v.sign_agreement([0, 0], [1, 2]) is None

    def test_degenerate_inputs_return_none_not_zero(self):
        assert v.pearson([1, 1, 1], [1, 2, 3]) is None
        assert v.spearman([1], [1]) is None


class TestJoin:
    def test_exact_date_match(self):
        pairs = v.join_overlap([survey_row("k", "2026-07-16", 10.0, 12.0)],
                              [model_row("k", "2026-07-16", 11.0, 12.0)])
        assert len(pairs) == 1
        assert pairs[0]["survey_surprise"] == pytest.approx(2.0)
        assert pairs[0]["model_surprise"] == pytest.approx(1.0)

    def test_within_tolerance_matches_and_records_the_offset(self):
        """The BLS CPI release drifts, so exact equality silently drops months."""
        pairs = v.join_overlap([survey_row("k", "2026-07-14", 10.0, 12.0)],
                              [model_row("k", "2026-07-16", 11.0, 12.0)],
                              tolerance_days=5)
        assert len(pairs) == 1 and pairs[0]["offset_days"] == 2

    def test_outside_tolerance_does_not_match(self):
        assert v.join_overlap([survey_row("k", "2026-07-14", 10.0, 12.0)],
                             [model_row("k", "2026-08-30", 11.0, 12.0)],
                             tolerance_days=5) == []

    def test_nearest_wins(self):
        pairs = v.join_overlap([survey_row("k", "2026-07-14", 10.0, 12.0)],
                              [model_row("k", "2026-07-18", 9.0, 12.0),
                               model_row("k", "2026-07-15", 11.0, 12.0)],
                              tolerance_days=5)
        assert pairs[0]["model_date"] == "2026-07-15"

    def test_a_model_row_is_consumed_at_most_once(self):
        """One expectation must not be double-counted against two survey rows."""
        pairs = v.join_overlap([survey_row("k", "2026-07-14", 10.0, 12.0),
                               survey_row("k", "2026-07-15", 10.0, 12.0)],
                              [model_row("k", "2026-07-15", 11.0, 12.0)],
                              tolerance_days=5)
        assert len(pairs) == 1

    def test_kinds_are_never_crossed(self):
        assert v.join_overlap([survey_row("a", "2026-07-16", 10.0, 12.0)],
                             [model_row("b", "2026-07-16", 11.0, 12.0)]) == []

    def test_a_model_row_is_never_validated_against_itself(self):
        """Backfilled rows on the survey side must be ignored, or the model would be
        compared to its own output and trivially 'pass'."""
        m = model_row("k", "2026-07-16", 11.0, 12.0)
        assert v.join_overlap([m], [m]) == []


class TestModelSurveyDiscriminator:
    """`backfilled` must NOT imply "model".

    An earlier `is_model_row` treated any backfilled row as a model row — fine while the model
    side was the only thing ever backfilled, and broken the moment a SURVEY backfill exists
    (FXStreet's calendar API takes an arbitrary date range, so retro-fetched REAL survey
    consensus is legitimately `backfilled: true`). Misclassifying those would silently drop
    them from the survey side, leaving M3 comparing the model against itself or against
    nothing — a wrong answer with no error.
    """

    @staticmethod
    def _survey_backfill_row(kind, date, consensus, actual):
        return {"kind": kind, "scheduled_for": date, "backfilled": True,
                "expectation_source": "survey:fxstreet",
                "expected": {"consensus": consensus},
                "realized_outcome": {"consensus": consensus, "actual": actual}}

    def test_a_backfilled_SURVEY_row_is_not_a_model_row(self):
        r = self._survey_backfill_row("k", "2026-07-16", 10.0, 12.0)
        assert r["backfilled"] is True
        assert not v.is_model_row(r), "backfilled != model"

    def test_a_model_row_still_reads_as_model(self):
        assert v.is_model_row(model_row("k", "2026-07-16", 11.0, 12.0))

    def test_a_row_with_no_expectation_source_is_survey_side(self):
        """The forward feed's own rows carry no expectation_source at all."""
        assert not v.is_model_row(survey_row("k", "2026-07-16", 10.0, 12.0))

    def test_a_backfilled_survey_row_JOINS_against_a_model_row(self):
        """The end-to-end property the fix exists for: a survey backfill must widen the
        overlap, not be silently discarded from it."""
        pairs = v.join_overlap(
            [self._survey_backfill_row("k", "2026-07-16", 10.0, 12.0)],
            [model_row("k", "2026-07-16", 11.0, 12.0)])
        assert len(pairs) == 1
        assert pairs[0]["survey_consensus"] == 10.0
        assert pairs[0]["model_expectation"] == 11.0


class TestProvenanceGuard:
    """THE load-bearing property — see the module docstring."""

    @staticmethod
    def _unstamped_pairs():
        return v.join_overlap(
            [survey_row("k", f"2026-07-{d:02d}", 10.0, 12.0) for d in (1, 8, 15)],
            [model_row("k", f"2026-07-{d:02d}", 11.0, 12.0, stamped=False)
             for d in (1, 8, 15)])

    def test_missing_stamps_are_detected(self):
        problems = v.provenance_problems(self._unstamped_pairs())
        assert len(problems) == 2
        assert any("units_transform" in p for p in problems)
        assert any("release_date_basis" in p for p in problems)

    def test_stale_inputs_PREEMPT_both_pass_and_fail(self):
        """A correlation across mismatched units is not weak evidence — it is NO evidence,
        so it must not be allowed to read as the gate's kill condition."""
        rep = v.score(self._unstamped_pairs(), min_honest_n=1)
        assert rep["verdict"] == "stale_model_inputs"
        assert rep["provenance_problems"]

    def test_stamped_inputs_are_scored_normally(self):
        pairs = v.join_overlap(
            [survey_row("k", f"2026-07-{d:02d}", 10.0, 12.0 + d) for d in (1, 8, 15)],
            [model_row("k", f"2026-07-{d:02d}", 10.5, 12.0 + d) for d in (1, 8, 15)])
        rep = v.score(pairs, min_honest_n=1)
        assert rep["verdict"] != "stale_model_inputs"
        assert not rep["provenance_problems"]

    def test_the_render_shouts_about_stale_inputs(self):
        pairs = self._unstamped_pairs()
        text = v.render(v.score(pairs, min_honest_n=1), pairs)
        assert "NOT interpretable" in text


class TestVerdictGating:
    @staticmethod
    def _pairs(n, model_offset=0.5):
        s = [survey_row("k", f"2026-0{1 + i // 28}-{1 + i % 28:02d}", 10.0, 12.0 + i)
             for i in range(n)]
        m = [model_row("k", r["scheduled_for"], 10.0 + model_offset, r["realized_outcome"]["actual"])
             for r in s]
        return v.join_overlap(s, m)

    def test_below_min_honest_n_is_neither_pass_nor_fail(self):
        rep = v.score(self._pairs(5), min_honest_n=12)
        assert rep["verdict"] == "insufficient_overlap"
        assert "NOT a pass and NOT a fail" in rep["note"]

    def test_a_tracking_model_passes_the_preregistered_bar(self):
        rep = v.score(self._pairs(20), min_honest_n=12)
        assert rep["verdict"] == "model_tracks_survey"

    def test_the_bar_is_declared_in_the_report(self):
        rep = v.score(self._pairs(20), min_honest_n=12)
        assert rep["bar_spearman"] == v.BAR_SPEARMAN
        assert rep["bar_sign_agreement"] == v.BAR_SIGN_AGREEMENT


class TestMainRefusesToMeasureNothing:
    def test_empty_inputs_exit_nonzero(self, tmp_path):
        """A scorecard computed from nothing is vacuous, and vacuous is indistinguishable
        from thin once published."""
        s = tmp_path / "s.jsonl"
        s.write_text("", encoding="utf-8")
        m = tmp_path / "m.jsonl"
        m.write_text("", encoding="utf-8")
        assert v.main(["--survey", str(s), "--model", str(m), "--dry-run"]) == 2

    def test_a_zero_row_join_exits_nonzero(self, tmp_path):
        s = tmp_path / "s.jsonl"
        s.write_text(json.dumps(survey_row("a", "2026-01-01", 1.0, 2.0)) + "\n", encoding="utf-8")
        m = tmp_path / "m.jsonl"
        m.write_text(json.dumps(model_row("b", "2026-09-09", 1.0, 2.0)) + "\n", encoding="utf-8")
        assert v.main(["--survey", str(s), "--model", str(m), "--dry-run"]) == 2
