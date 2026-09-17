"""MI-278 U40 — is the arbitration fallback still BELOW the legs it must lose to?

The guard ships an executable `--self-test`; this file is the pytest surface so
those controls run in CI on every PR rather than only when the guard is invoked.
Every test names the defect it would catch, and each was verified by PLANTING
that defect and watching this file fail.
"""
import importlib.util
import os

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location(
    "_u40", os.path.join(_HERE, "scripts", "ci",
                         "check_priority_fallback_distribution.py"))
U40 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(U40)


def test_self_test_passes_in_full():
    """Catches: any control in the guard's own suite regressing."""
    assert U40.self_test() == 0


# ------------------------------------------------------------------------ R1


def test_fallback_below_every_mapped_value_is_the_clean_state():
    assert U40.distribution_state({"a": 20, "b": 30}, 10)[0] == "fallback_below_all"


def test_equal_to_the_minimum_is_not_below_it():
    """Catches: relaxing `<` to `<=`. The row's assertion is STRICT, and a
    fallback tying the minimum still beats nothing while losing to nothing —
    an ambiguity the constant exists to avoid."""
    assert U40.distribution_state({"a": 10, "b": 30}, 10)[0] == "fallback_beats_some"


def test_the_beaten_count_is_reported_not_just_the_verdict():
    """Catches: dropping the count. '45 of 50' is the whole force of the finding;
    a bare verdict cannot say whether the inversion is marginal or total."""
    assert U40.distribution_state({"a": 0, "b": 0, "c": 30}, 10)[1]["beaten"] == 2


@pytest.mark.parametrize("mapping,fallback", [({}, 10), ({"a": 0}, None), (None, 10)])
def test_missing_inputs_are_ungradeable_never_clean(mapping, fallback):
    """Catches: an unreadable or empty map grading `fallback_below_all` — a
    vacuous pass that reads exactly like a healthy distribution."""
    assert U40.distribution_state(mapping, fallback)[0] == "ungradeable"


def test_ungradeable_reports_min_as_none_never_zero():
    assert U40.distribution_state({}, 10)[1]["min_mapped"] is None


# -------------------------------------------------------------------- parser


def test_both_annotated_and_plain_assignments_are_parsed():
    """Catches: walking only `ast.Assign`. DEFAULT_PRIORITIES is declared
    `Dict[str, int] = {...}` — an AnnAssign — so an Assign-only walk returns an
    empty map, which reads as 'no legs are mapped'. My own first probe did this."""
    priorities, fallback = U40._read_intents_symbols()
    assert isinstance(priorities, dict) and priorities
    assert isinstance(fallback, int)


def test_unreadable_source_yields_none_not_an_empty_map():
    import pathlib
    assert U40._read_intents_symbols(pathlib.Path("/nonexistent/x.py")) == (None, None)


# ------------------------------------------------------------------------ R2


def test_absent_legs_are_named_not_only_counted():
    """Catches: returning the count without the names, so nobody can act on it."""
    state, absent = U40.coverage_state({"a": 0}, ["a", "b", "c"])
    assert state == "absent_legs"
    assert absent == ["b", "c"]


def test_a_dated_exemption_covers_a_leg():
    assert U40.coverage_state({"a": 0}, ["a", "b"], exempt={"b"})[0] == "all_covered"


@pytest.mark.parametrize("mapping,legs", [(None, ["a"]), ({"a": 0}, None)])
def test_unreadable_inputs_are_ungradeable_never_all_covered(mapping, legs):
    """Catches: an unreadable roster reading as full coverage."""
    assert U40.coverage_state(mapping, legs)[0] == "ungradeable"


# ------------------------------------------------------------------------ R3

BASE = {"absent_legs": ["x", "y"], "absent_legs_on_real_money": ["x"]}


def test_the_seeded_stock_alone_is_within_baseline():
    """Seeding is a FLOOR, not a disposition: the seeded legs are still
    uncovered. Catches: treating the seed as a clean bill."""
    assert U40.regression_state(["x", "y"], ["x"], BASE)[0] == "within_baseline"


def test_a_new_absent_leg_worsens_and_is_named():
    state, terms = U40.regression_state(["x", "y", "z"], ["x"], BASE)
    assert state == "worsened"
    assert terms["new_absent"] == ["z"]


def test_a_seeded_leg_moving_onto_real_money_worsens():
    """Catches: keying the regression on new legs only. A leg already uncovered
    that gets routed onto a real-money account is a worsening with no new leg —
    exactly what Option A did on 2026-09-10."""
    assert U40.regression_state(["x", "y"], ["x", "y"], BASE)[0] == "worsened"


def test_a_cleared_leg_is_improved_and_does_not_fail():
    assert U40.regression_state(["x"], ["x"], BASE)[0] == "improved"


def test_no_baseline_is_not_a_pass():
    """Catches: a missing seed grading within_baseline, which turns 'we could
    not look' into 'nothing got worse'."""
    state, terms = U40.regression_state(["x"], [], None)
    assert state == "no_baseline"
    assert terms["seeded_absent"] is None


# ------------------------------------------------------------------- exposure


def test_no_absent_leg_is_its_own_state():
    assert U40.exposure_state([])[0] == "no_absent_leg_routed"


def test_vocabularies_are_declared():
    assert "fallback_beats_some" in U40.DISTRIBUTION_STATES
    assert "absent_leg_on_real_money" in U40.EXPOSURE_STATES
    assert "no_baseline" in U40.REGRESSION_STATES
    assert "ungradeable" in U40.COVERAGE_STATES


# --------------------------------------------------------------------- render


def test_unknown_and_empty_render_differently():
    """Catches: collapsing 'we could not look' into 'there were none'."""
    assert U40._n(None) == "unknown"
    assert U40._n([]) == "none"


def test_report_explains_why_r1_r2_do_not_fail():
    res = U40.assess()
    text = "\n".join(U40.report(res))
    if res["distribution_state"] != "fallback_below_all" or res["coverage_state"] != "all_covered":
        assert "R3 is the enforcing rule" in text
        assert "Tier-3" in text


def test_report_warns_explicitly_on_a_real_money_exposure():
    """Catches: deleting the warning. The per-account line already contains
    'REAL MONEY, LIVE', so a control keyed on that string passes with the
    warning gone — a plant proved it."""
    res = U40.assess()
    text = "\n".join(U40.report(res))
    if res["exposure_state"] == "absent_leg_on_real_money":
        assert "AN UNCOVERED LEG IS ROUTED ON A LIVE REAL-MONEY ACCOUNT" in text
        assert "it WINS the arbitration" in text


def test_live_assessment_grades_every_rule():
    res = U40.assess()
    assert res["distribution_state"] in U40.DISTRIBUTION_STATES
    assert res["coverage_state"] in U40.COVERAGE_STATES
    assert res["exposure_state"] in U40.EXPOSURE_STATES
    assert res["regression_state"] in U40.REGRESSION_STATES
