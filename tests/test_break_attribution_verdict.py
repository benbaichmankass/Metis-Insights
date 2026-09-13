"""MI-278 U41 — was the 2026-08-30 break the e35 geometry or the market?

The module ships an executable `--self-test`; this file is the pytest surface so
those controls run in CI on every PR. Every test names the defect it would
catch, and each was verified by PLANTING that defect and watching this file fail.
"""
import datetime as dt
import importlib.util
import os

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location(
    "_u41", os.path.join(_HERE, "scripts", "research",
                         "m20_u41_break_attribution_verdict.py"))
U41 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(U41)

EV = U41._parse(U41.EVENT_ISO)
M = {"treated_state": "measured", "control_state": "measured"}


def test_self_test_passes_in_full():
    assert U41.self_test() == 0


# ------------------------------------------------------------------- era split


@pytest.mark.parametrize("when,want", [
    ("2026-08-29T00:00:00Z", "pre"),
    ("2026-08-31T00:00:00Z", "post"),
    (U41.EVENT_ISO, "post"),
])
def test_era_placement(when, want):
    assert U41.era_of(U41._parse(when), EV) == want


@pytest.mark.parametrize("bad", [None, "not-a-date", ""])
def test_an_unreadable_timestamp_is_never_defaulted_into_an_era(bad):
    """Catches: defaulting unplaceable packages into one era, which loads that
    cell with every parse failure and moves the difference under study."""
    assert U41.era_of(U41._parse(bad), EV) is None


# --------------------------------------------------------------- excursion


def _row(arm, era, ratio, w=24):
    return {"arm": arm, "era": era, "window_h": w, "fa_ratio": ratio}


def test_an_empty_cell_has_no_mean_rather_than_a_zero_one():
    """Catches: an empty cell reporting 0.0, which reads as 'the market offered
    no favourable travel' — the strongest possible finding, from no data."""
    t = U41.excursion_terms([_row("e35", "pre", 2.0)], 24)
    assert t[("control_other", "post")]["mean_fa"] is None
    assert t[("control_other", "post")]["n"] == 0


def test_a_none_ratio_is_skipped_not_counted_as_zero_travel():
    """Catches: folding mae==0 (no adverse travel — undefined ratio) into 0.0,
    which is its arithmetic opposite."""
    t = U41.excursion_terms([_row("e35", "pre", 2.0), _row("e35", "pre", None)], 24)
    assert t[("e35", "pre")]["n"] == 1


def test_only_the_requested_window_is_averaged():
    rows = [_row("e35", "pre", 1.0, 24), _row("e35", "pre", 9.9, 48)]
    assert U41.excursion_terms(rows, 24)[("e35", "pre")]["mean_fa"] == 1.0


def test_every_arm_and_era_appears_present_or_not():
    t = U41.excursion_terms([], 24)
    assert set(t) == {(a, e) for a in U41.ARMS for e in U41.ERAS}


# ------------------------------------------------------------ term gradeability


def test_the_three_term_states_are_distinct():
    """Catches: collapsing 'the cell is too small' into 'the cell is empty', or
    either into 'measured'. They imply different next steps — one needs more
    time, the other needs a working fetch."""
    big, small, empty = {"n": 20, "mean_fa": 1.0}, {"n": 3, "mean_fa": 1.0}, {"n": 0, "mean_fa": None}
    assert U41.term_state(big, big) == "measured"
    assert U41.term_state(big, small) == "insufficient_n"
    assert U41.term_state(big, empty) == "not_measured"


def test_an_ungradeable_delta_is_none_never_zero():
    terms = {("e35", "pre"): {"n": 20, "mean_fa": 1.0},
             ("e35", "post"): {"n": 0, "mean_fa": None}}
    assert U41.arm_delta(terms, "e35")[1] is None


# ---------------------------------------------------------------- the verdict


def test_both_arms_falling_without_a_stop_rise_is_the_market():
    assert U41.verdict(-2.0, -0.5, -0.4, **M)[0] == "market_regime"


def test_a_flat_control_with_a_stop_rise_is_the_geometry():
    assert U41.verdict(+5.0, -0.5, +0.1, **M)[0] == "e35_geometry"


def test_both_arms_falling_with_a_stop_rise_is_both():
    assert U41.verdict(+5.0, -0.5, -0.4, **M)[0] == "both_contribute"


def test_a_falling_control_with_a_rising_treated_arm_is_never_the_geometry():
    """Catches: dropping the `not control_fell` guard. The control falling is a
    market move e35 cannot cause, so blaming the geometry there is backwards —
    and a plant that did exactly this passed every other control in the suite."""
    assert U41.verdict(+5.0, +0.2, -0.4, **M)[0] != "e35_geometry"
    assert U41.verdict(+5.0, +0.2, -0.4, **M)[0] == "cannot_discriminate"


@pytest.mark.parametrize("tstate,cstate", [
    ("measured", "insufficient_n"),
    ("measured", "not_measured"),
    ("insufficient_n", "measured"),
])
def test_an_ungradeable_arm_cannot_be_discriminated_whatever_the_stop_term(tstate, cstate):
    """Catches: letting a large stop-rate DiD carry a verdict on its own. The row
    forecloses exactly that — the stop term alone cannot separate geometry from
    regime."""
    got = U41.verdict(+20.0, -0.9, -0.9, treated_state=tstate, control_state=cstate)[0]
    assert got == "cannot_discriminate"


def test_a_missing_stop_term_is_not_read_as_a_rise():
    assert U41.verdict(None, -0.5, +0.1, **M)[0] == "neither_explains"


def test_every_verdict_carries_a_reason():
    for args in ((-2.0, -0.5, -0.4), (5.0, -0.5, 0.1), (5.0, -0.5, -0.4), (-1.0, 0.2, 0.1)):
        v, why = U41.verdict(*args, **M)
        assert v in U41.VERDICTS
        assert why.strip()


# --------------------------------------------------------------------- report


def _res(verdict="cannot_discriminate"):
    t = U41.excursion_terms([_row("e35", "pre", 2.0), _row("e35", "post", 1.0)], 24)
    return {"population": "fixture", "event": U41.EVENT_ISO, "interval": "15",
            "windows": [24], "unplaceable": 3, "cells": {("e35", "pre"): 2},
            "excursion": {24: t},
            "deltas": {24: {a: U41.arm_delta(t, a) for a in U41.ARMS}},
            "stop_did_pp": None, "verdict_window": 24,
            "verdict": verdict, "verdict_why": "fixture"}


def test_report_states_population_interval_and_the_unplaceable_count():
    text = "\n".join(U41.report(_res()))
    assert "POPULATION: fixture" in text
    assert "candle interval 15" in text
    assert "could not be placed in time" in text


def test_report_says_the_excursion_term_is_geometry_free():
    """Catches: dropping the sentence that carries the whole discriminating
    argument, leaving two numbers with no reason to believe they separate."""
    assert "geometry-FREE" in "\n".join(U41.report(_res()))


def test_cannot_discriminate_says_it_does_not_clear_the_row():
    """Catches: rendering a non-verdict as a result. The row demands an
    attribution; reporting 'we could not tell' as a pass is the substitution it
    forecloses by name."""
    text = "\n".join(U41.report(_res("cannot_discriminate")))
    assert "THIS DOES NOT CLEAR THE ROW" in text
    assert "substitution the row forecloses" in text


def test_a_real_verdict_does_not_carry_the_does_not_clear_warning():
    assert "THIS DOES NOT CLEAR THE ROW" not in "\n".join(U41.report(_res("market_regime")))


def test_vocabularies_are_declared():
    assert set(U41.VERDICTS) >= {"e35_geometry", "market_regime", "cannot_discriminate"}
    assert set(U41.TERM_STATES) == {"measured", "insufficient_n", "not_measured"}
