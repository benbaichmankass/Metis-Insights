"""Tests for :mod:`src.runtime.r_provenance` — the R-denominator detector.

⚠️ EVERY DISCRIMINATION IS ASSERTED IN **BOTH** DIRECTIONS. A detector shown
only to fire has been shown to fire, not to discriminate: a classifier that
returned ``contaminated`` unconditionally would pass a one-sided suite. So each
positive control is paired with the nearest-possible negative — same shape, one
field changed — and the pairs are asserted together where that is what makes the
claim.

The realistic fixtures carry REAL rows measured off the live journal copy
(`/home/ubuntu/ict-trading-bot/data/trade_journal.db` on the trainer VM, mtime
2026-09-02T04:28:35Z; trader serving sha 2c7ae605) so the suite pins the actual
shapes, not invented ones.
"""
from __future__ import annotations

import pytest

from src.runtime.r_provenance import (
    CONFIRM_REL_TOL,
    DISAGREEMENT_RATIO_BAR,
    R_CONFIRMED_INITIAL,
    R_CONTAMINATED,
    R_NO_BASIS,
    R_STATES,
    R_UNVERIFIED,
    classify_r,
    declared_initial_risk,
    disagreement_ratio,
    empty_counts,
    stop_is_wrong_side,
    summarize,
)


# ───────────────────────── stop_is_wrong_side ──────────────────────────────
class TestStopIsWrongSide:
    """The side test, three-valued. ``None`` is *we could not look* and must
    never be readable as ``False``."""

    @pytest.mark.parametrize("direction", ["long", "buy", "LONG", " Buy "])
    def test_long_stop_above_entry_is_wrong_and_below_is_not(self, direction):
        # BOTH directions in one assertion pair — the discrimination IS the pair.
        assert stop_is_wrong_side(direction, 100.0, 101.0) is True
        assert stop_is_wrong_side(direction, 100.0, 99.0) is False

    @pytest.mark.parametrize("direction", ["short", "sell", "SHORT", " Sell "])
    def test_short_stop_below_entry_is_wrong_and_above_is_not(self, direction):
        assert stop_is_wrong_side(direction, 100.0, 99.0) is True
        assert stop_is_wrong_side(direction, 100.0, 101.0) is False

    def test_unrecognised_direction_returns_none_not_false(self):
        # The whole point of the three-valued return. `is False` would mean
        # "we looked and the side is fine"; this is "we could not look".
        assert stop_is_wrong_side("flat", 100.0, 101.0) is None
        assert stop_is_wrong_side(None, 100.0, 101.0) is None
        assert stop_is_wrong_side("", 100.0, 101.0) is None

    def test_missing_or_unparseable_price_returns_none(self):
        assert stop_is_wrong_side("long", None, 101.0) is None
        assert stop_is_wrong_side("long", 100.0, None) is None
        assert stop_is_wrong_side("long", "abc", 101.0) is None
        assert stop_is_wrong_side("long", float("nan"), 101.0) is None

    def test_stop_exactly_at_entry_is_not_wrong_side(self):
        # It is a ZERO-RISK row, graded NO_BASIS by the classifier. The side
        # test must not claim it as a side violation.
        assert stop_is_wrong_side("long", 100.0, 100.0) is False
        assert stop_is_wrong_side("short", 100.0, 100.0) is False


# ─────────────────────── declared_initial_risk ─────────────────────────────
class TestDeclaredInitialRisk:
    def test_reads_risk_per_unit_from_json_string_and_from_mapping(self):
        assert declared_initial_risk('{"risk_per_unit": 18.947142857143263}') == pytest.approx(18.947142857143263)
        assert declared_initial_risk({"risk_per_unit": 2.5}) == 2.5

    def test_real_live_meta_blob_is_parsed(self):
        # Verbatim shape from order_packages.meta for trade 4773
        # (ict_scalp_mgc_15m), the worked example in the module docstring.
        blob = ('{"donchian_hi": 4400.0, "atr": 6.3, "tp_r": 2.0, '
                '"risk_per_unit": 18.947142857143263, "entry_time": '
                '"2026-08-30T10:00:00+00:00", "timeframe": "15m"}')
        assert declared_initial_risk(blob) == pytest.approx(18.947142857143263)

    @pytest.mark.parametrize("blob", [
        None, "", "not json", "[]", "{}", '{"risk_per_unit": null}',
        '{"risk_per_unit": 0}', '{"risk_per_unit": -1}', '{"risk_per_unit": "abc"}',
    ])
    def test_absent_or_non_positive_risk_is_none_never_zero(self, blob):
        # A zero risk is not a reading; it is the absence of one. Returning 0.0
        # would make `disagreement_ratio` divide into a fabricated basis.
        got = declared_initial_risk(blob)
        assert got is None, f"{blob!r} -> {got!r}"

    def test_positive_control_the_probe_can_find_one(self):
        # A negative result is only evidence if the probe can find a positive.
        assert declared_initial_risk('{"risk_per_unit": 1.0}') == 1.0


# ──────────────────────── disagreement_ratio ───────────────────────────────
class TestDisagreementRatio:
    def test_ratio_above_one_means_stored_stop_is_tighter_than_declared(self):
        # declared 2.0, stored distance 1.0 -> the stop has been pulled in 2x,
        # so R is INFLATED 2x. Sign convention is load-bearing; assert it.
        assert disagreement_ratio(100.0, 99.0, '{"risk_per_unit": 2.0}') == pytest.approx(2.0)

    def test_ratio_below_one_means_stored_is_wider_and_is_returned_honestly(self):
        # Trailing cannot produce this. It is NOT clamped to 1.0 — 24.3% of
        # correct-side live rows read below 0.99 and hiding that would hide the
        # evidence that the ~1.0 mass is two-sided noise, not a trail.
        assert disagreement_ratio(100.0, 98.0, '{"risk_per_unit": 1.0}') == pytest.approx(0.5)

    def test_none_when_either_side_is_unavailable(self):
        assert disagreement_ratio(100.0, 99.0, None) is None
        assert disagreement_ratio(None, 99.0, '{"risk_per_unit": 1.0}') is None
        assert disagreement_ratio(100.0, 100.0, '{"risk_per_unit": 1.0}') is None  # zero distance


# ──────────────────────────── classify_r ───────────────────────────────────
class TestClassifyContaminated:
    """CONTAMINATED is a PROOF, so every positive is paired with the nearest
    negative: same row, stop moved to the risk side."""

    def test_long_trailed_past_entry_fires_and_the_clean_twin_does_not(self):
        contaminated = {"direction": "long", "entry_price": 100.0,
                        "stop_loss": 101.0, "qty": 1.0, "take_profit_1": 110.0}
        clean = dict(contaminated, stop_loss=99.0)
        assert classify_r(contaminated)[0] == R_CONTAMINATED
        assert classify_r(clean)[0] != R_CONTAMINATED

    def test_short_trailed_past_entry_fires_and_the_clean_twin_does_not(self):
        contaminated = {"direction": "short", "entry_price": 100.0,
                        "stop_loss": 99.0, "qty": 1.0, "take_profit_1": 90.0}
        clean = dict(contaminated, stop_loss=101.0)
        assert classify_r(contaminated)[0] == R_CONTAMINATED
        assert classify_r(clean)[0] != R_CONTAMINATED

    def test_the_live_worst_case_row_is_caught(self):
        # Trade 5027, ict_scalp_sol_15m paper, SHORT, entry 100.44, stop
        # 100.439115 -> a 0.000885 stop distance on a ~$100 instrument, giving
        # the +3672.32 R that is the headline of this whole finding.
        row = {"direction": "short", "entry_price": 100.44,
               "stop_loss": 100.439115, "qty": 992.2, "pnl": 3224.65}
        state, reason = classify_r(row)
        assert (state, reason) == (R_CONTAMINATED, "wrong_side_of_entry")

    def test_a_trailed_stop_short_of_entry_is_NOT_caught_and_that_is_stated(self):
        # THE DETECTOR'S KNOWN BLIND SPOT, asserted so it cannot be forgotten:
        # a stop trailed to just INSIDE entry is side-plausible and just as
        # wrong. With no declared record it is UNVERIFIED — "we could not
        # look" — and must never read as clean.
        row = {"direction": "long", "entry_price": 100.0, "stop_loss": 99.999,
               "qty": 1.0}
        state, reason = classify_r(row)
        assert state == R_UNVERIFIED
        assert reason == "no_declared_initial_risk_record"

    def test_that_same_blind_spot_IS_caught_once_a_declared_record_exists(self):
        # ...which is exactly why the second axis is published.
        row = {"direction": "long", "entry_price": 100.0, "stop_loss": 99.999,
               "qty": 1.0, "package_meta": '{"risk_per_unit": 2.0}'}
        assert classify_r(row)[0] == R_UNVERIFIED
        assert disagreement_ratio(100.0, 99.999, '{"risk_per_unit": 2.0}') > DISAGREEMENT_RATIO_BAR


class TestClassifyMirroredBracket:
    """The direction-mirrored row: the side test's INPUT is unreliable, so the
    row is UNVERIFIED, never a false CONTAMINATED proof."""

    def test_real_live_mirrored_row_grades_unverified_not_contaminated(self):
        # Trade 3319, sol_pullback_2h paper, setup_type='intent_reduce'.
        # trades.direction='long' while order_packages.direction='short'; the
        # WHOLE bracket is inverted (tp 70.52 < entry 78.27 < sl 80.53).
        row = {"direction": "long", "entry_price": 78.27, "stop_loss": 80.5325,
               "take_profit_1": 70.52127, "qty": 1.0}
        state, reason = classify_r(row)
        assert state == R_UNVERIFIED
        assert reason == "bracket_mirrored_vs_direction"

    def test_the_discriminator_is_the_TP_only(self):
        # Same row, one field changed: put the take-profit back on the profit
        # side and it becomes a genuine trailed-stop proof. A trail moves the
        # STOP; a mirror moves BOTH. That is the whole discrimination.
        mirrored = {"direction": "long", "entry_price": 78.27, "stop_loss": 80.5325,
                    "take_profit_1": 70.52127, "qty": 1.0}
        trailed = dict(mirrored, take_profit_1=86.0)
        assert classify_r(mirrored)[0] == R_UNVERIFIED
        assert classify_r(trailed)[0] == R_CONTAMINATED

    def test_a_missing_tp_cannot_manufacture_a_mirror_verdict(self):
        # No take-profit -> the mirror test could not run -> fall through to
        # the side proof. Absence must not be read as "the tp is fine".
        row = {"direction": "long", "entry_price": 78.27, "stop_loss": 80.5325,
               "qty": 1.0}
        assert classify_r(row) == (R_CONTAMINATED, "wrong_side_of_entry")


class TestClassifyConfirmedInitial:
    def test_distance_matching_the_declared_record_confirms(self):
        row = {"direction": "long", "entry_price": 100.0, "stop_loss": 98.0,
               "qty": 1.0, "take_profit_1": 110.0,
               "package_meta": '{"risk_per_unit": 2.0}'}
        assert classify_r(row) == (R_CONFIRMED_INITIAL, "matches_declared_initial_risk")

    def test_a_disagreeing_distance_is_UNVERIFIED_and_never_CONTAMINATED(self):
        # A disagreement is not a proof — the near-1.0 mass on live data is
        # two-sided noise whose cause is not established.
        row = {"direction": "long", "entry_price": 100.0, "stop_loss": 98.0,
               "qty": 1.0, "take_profit_1": 110.0,
               "package_meta": '{"risk_per_unit": 5.0}'}
        state, reason = classify_r(row)
        assert state == R_UNVERIFIED
        assert reason == "disagrees_with_declared_initial_risk"

    def test_wrong_side_beats_a_matching_distance(self):
        # Precedence. A row that matches on distance AND sits on the wrong side
        # is a CONTRADICTION, and a contradiction is not a confirmation.
        # (Without a tp to reveal a mirror, the side proof stands.)
        row = {"direction": "long", "entry_price": 100.0, "stop_loss": 102.0,
               "qty": 1.0, "package_meta": '{"risk_per_unit": 2.0}'}
        assert classify_r(row)[0] == R_CONTAMINATED

    def test_confirmation_is_impossible_when_the_direction_is_unreadable(self):
        # "We could not look" can never become a confirmation, however well the
        # distance matches.
        row = {"direction": "sideways", "entry_price": 100.0, "stop_loss": 98.0,
               "qty": 1.0, "package_meta": '{"risk_per_unit": 2.0}'}
        assert classify_r(row) == (R_UNVERIFIED, "direction_unreadable")

    def test_tolerance_boundary_holds_in_both_directions(self):
        inside = 2.0 * (1 + CONFIRM_REL_TOL / 2)
        outside = 2.0 * (1 + CONFIRM_REL_TOL * 10)
        base = {"direction": "long", "entry_price": 100.0, "qty": 1.0,
                "package_meta": '{"risk_per_unit": 2.0}'}
        assert classify_r(dict(base, stop_loss=100.0 - inside))[0] == R_CONFIRMED_INITIAL
        assert classify_r(dict(base, stop_loss=100.0 - outside))[0] == R_UNVERIFIED


class TestClassifyNoBasis:
    @pytest.mark.parametrize("row", [
        {"direction": "long", "entry_price": None, "stop_loss": 99.0, "qty": 1.0},
        {"direction": "long", "entry_price": 100.0, "stop_loss": None, "qty": 1.0},
        {"direction": "long", "entry_price": 100.0, "stop_loss": 99.0, "qty": None},
        {"direction": "long", "entry_price": 100.0, "stop_loss": 99.0, "qty": 0.0},
        {},
    ])
    def test_missing_inputs_are_no_basis(self, row):
        assert classify_r(row)[0] == R_NO_BASIS

    def test_stop_equal_to_entry_is_no_basis_not_contaminated(self):
        # Zero risk. `r_multiple` already returns None here; the grade must
        # agree, or the partition stops lining up with rCoverage.
        assert classify_r({"direction": "long", "entry_price": 100.0,
                           "stop_loss": 100.0, "qty": 1.0}) == (R_NO_BASIS, "risk_not_positive")

    def test_position_size_is_accepted_as_an_alias_for_qty(self):
        row = {"direction": "long", "entry_price": 100.0, "stop_loss": 99.0,
               "position_size": 5.0}
        assert classify_r(row)[0] != R_NO_BASIS


class TestMetaAliases:
    @pytest.mark.parametrize("key", ["package_meta", "pkg_meta", "meta"])
    def test_all_three_meta_key_spellings_are_read(self, key):
        row = {"direction": "long", "entry_price": 100.0, "stop_loss": 98.0,
               "qty": 1.0, key: '{"risk_per_unit": 2.0}'}
        assert classify_r(row)[0] == R_CONFIRMED_INITIAL


# ──────────────────────────── summarize ────────────────────────────────────
class TestSummarize:
    def test_counts_partition_the_population_by_construction(self):
        rows = [
            {"direction": "long", "entry_price": 100.0, "stop_loss": 101.0, "qty": 1.0},
            {"direction": "long", "entry_price": 100.0, "stop_loss": 98.0, "qty": 1.0,
             "package_meta": '{"risk_per_unit": 2.0}'},
            {"direction": "long", "entry_price": 100.0, "stop_loss": 99.0, "qty": 1.0},
            {},
        ]
        s = summarize(rows)
        assert s["graded"] == 4
        assert sum(s["counts"].values()) == s["graded"], "partition must be checkable"
        assert s["counts"] == {R_CONTAMINATED: 1, R_CONFIRMED_INITIAL: 1,
                               R_UNVERIFIED: 1, R_NO_BASIS: 1}

    def test_every_state_key_is_present_with_an_explicit_zero(self):
        # A key that vanishes makes a consumer branch on absence, and absence is
        # not one of the states.
        s = summarize([])
        assert set(s["counts"]) == set(R_STATES)
        assert all(v == 0 for v in s["counts"].values())
        assert set(empty_counts()) == set(R_STATES)

    def test_tightened_count_ships_with_its_own_denominator(self):
        # A bar-crossing count over an unstated denominator is not a claim.
        rows = [
            {"direction": "long", "entry_price": 100.0, "stop_loss": 99.9, "qty": 1.0,
             "package_meta": '{"risk_per_unit": 5.0}'},      # ratio 50 -> crosses
            {"direction": "long", "entry_price": 100.0, "stop_loss": 98.0, "qty": 1.0,
             "package_meta": '{"risk_per_unit": 2.0}'},      # ratio 1.0 -> does not
            {"direction": "long", "entry_price": 100.0, "stop_loss": 98.0, "qty": 1.0},
        ]
        s = summarize(rows)
        assert s["declared_risk_records"] == 2, "the denominator counts only rows WITH a record"
        assert s["tightened_vs_declared"] == 1
        assert s["disagreement_ratio_bar"] == DISAGREEMENT_RATIO_BAR

    def test_nothing_is_excluded_from_the_population(self):
        # The rule this module is built around: publish the count, never drop
        # the row. `graded` must equal the input length whatever the grades.
        rows = [{"direction": "long", "entry_price": 100.0, "stop_loss": 101.0,
                 "qty": 1.0}] * 7
        assert summarize(rows)["graded"] == 7
