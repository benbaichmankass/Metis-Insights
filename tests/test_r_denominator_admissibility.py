"""MI-278 U46 — can the rows in an R aggregate register an R at all?

The module ships an executable `--self-test`; this file is the pytest surface so
those controls run in CI on every PR rather than only when a session remembers
to invoke them. Every test names the defect it would catch, and each was
verified by PLANTING that defect and watching this file fail.
"""
import importlib.util
import os

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location(
    "_u46", os.path.join(_HERE, "scripts", "research",
                         "r_denominator_admissibility.py"))
U46 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(U46)


def leg(name, trades, pnl, r, er, declared=None, stored=0):
    return {"name": name, "trades": trades, "totalPnl": pnl, "totalR": r,
            "expectancyR": er,
            "rBasis": {"declaredInitial": trades if declared is None else declared,
                       "storedStop": stored, "refusedWrongSide": 0, "noBasis": 0}}


def block(legs, **kw):
    b = {"totalTrades": sum(x["trades"] for x in legs),
         "totalPnl": round(sum(x["totalPnl"] for x in legs), 6),
         "totalR": round(sum(x["totalR"] for x in legs), 6),
         "rCoverage": 1.0, "perStrategy": legs,
         "rBasis": {"declaredInitial": sum(x["rBasis"]["declaredInitial"] for x in legs),
                    "storedStop": sum(x["rBasis"]["storedStop"] for x in legs),
                    "refusedWrongSide": 0, "noBasis": 0}}
    b.update(kw)
    return b


def pk(strategy, entry, sl, n=1):
    return [{"strategy_name": strategy, "entry": entry, "sl": sl} for _ in range(n)]


# --------------------------------------------------------------------------
# the scale axis — the question rCoverage cannot ask
# --------------------------------------------------------------------------
def test_a_hundred_percent_stop_is_a_notional_sentinel_not_a_risk_level():
    """The pairs sleeve's stop is a sentinel at 50-100% of entry. Its R is a
    return on notional; calling it a risk multiple is the defect."""
    assert U46.scale_state(100.0, 200.0) == U46.SCALE_SENTINEL
    assert U46.scale_state(100.0, 50.0) == U46.SCALE_SENTINEL


def test_an_ordinary_stop_is_a_risk_level():
    """Measured on the live fleet, non-pairs |entry-sl|/entry runs
    0.0001-0.1440 with a median of 0.0107."""
    assert U46.scale_state(100.0, 101.07) == U46.SCALE_RISK
    assert U46.scale_state(100.0, 114.4) == U46.SCALE_RISK


def test_unusable_geometry_is_unknown_and_never_risk_level():
    """Catches the dangerous default. Treating a row we could not measure as a
    risk level is how an inadmissible population passes as a clean one."""
    assert U46.scale_state(None, 1.0) == U46.SCALE_UNKNOWN
    assert U46.scale_state(0.0, 1.0) == U46.SCALE_UNKNOWN


def test_the_sentinel_bar_is_a_flag_with_a_measured_basis_not_a_constant():
    assert U46.DEFAULT_SENTINEL_FRAC == 0.25
    assert U46.scale_state(100.0, 110.0, sentinel_frac=0.05) == U46.SCALE_SENTINEL
    assert U46.scale_state(100.0, 110.0, sentinel_frac=0.5) == U46.SCALE_RISK


def test_a_mixed_leg_grades_unknown_and_reports_its_sentinel_count():
    """Catches: rounding a half-sentinel leg to whichever side is bigger.
    'Half this leg's denominators are sentinels' is a finding, not a rounding
    decision."""
    pkgs = pk("m", 100.0, 200.0, 3) + pk("m", 100.0, 100.5, 2)
    state, graded, n_sent = U46.leg_scale(pkgs, "m")
    assert state == U46.SCALE_UNKNOWN
    assert (graded, n_sent) == (5, 3)


def test_a_leg_with_no_packages_is_unknown_not_admissible():
    assert U46.leg_scale(pk("other", 100.0, 101.0), "absent")[0] == U46.SCALE_UNKNOWN


# --------------------------------------------------------------------------
# the basis axis — read from the endpoint, never re-derived
# --------------------------------------------------------------------------
def test_basis_census_reads_the_endpoints_own_keys():
    assert U46.basis_census({"declaredInitial": 41, "storedStop": 0,
                             "refusedWrongSide": 0, "noBasis": 0}) == {
        U46.BASIS_DECLARED: 41, U46.BASIS_STORED: 0,
        U46.BASIS_REFUSED: 0, U46.BASIS_NONE: 0}


def test_an_absent_rbasis_is_unknown_never_no_basis():
    """'The endpoint says this row has no risk basis' and 'we failed to read
    the field' are opposite statements."""
    assert U46.basis_census(None) == {U46.BASIS_UNKNOWN: None}
    assert U46.basis_census({"nope": 1}) == {U46.BASIS_UNKNOWN: None}


# --------------------------------------------------------------------------
# THE refusal — no packages means no admissibility claim
# --------------------------------------------------------------------------
def test_without_packages_no_admissible_share_is_published():
    """THE load-bearing control. Deriving admissibility from rBasis alone would
    repeat the exact collapse this module exists to expose — the endpoint
    already reports rCoverage 1.0 on a population that is half sentinels."""
    b = block([leg("scalp", 2, 10.0, 2.0, 1.0),
               leg("pairs_a", 2, 0.0, 0.0, 0.0, declared=0, stored=2)])
    v = U46.grade_block(b, None)
    assert v["scale_graded"] is False
    assert v["admissible"] is None
    assert v["restated"] is None
    txt = U46.render(v)
    assert "SCALE NOT GRADED" in txt
    assert "admissibleShare" not in txt


def test_without_packages_the_basis_census_and_reconciliation_still_run():
    """The refusal is scoped to the claim it cannot support, not to everything."""
    b = block([leg("scalp", 2, 10.0, 2.0, 1.0),
               leg("pairs_a", 2, 0.0, 0.0, 0.0, declared=0, stored=2)])
    v = U46.grade_block(b, None)
    assert v["basis"][U46.BASIS_STORED] == 2
    assert v["reconciles"]["ok"] is True


# --------------------------------------------------------------------------
# the restated aggregate, and the reconciliation that makes it quotable
# --------------------------------------------------------------------------
def test_the_restated_aggregate_is_printed_beside_the_reported_one_never_instead():
    b = block([leg("scalp", 2, 10.0, 2.0, 1.0),
               leg("pairs_a", 2, 0.0, 0.0, 0.0, declared=0, stored=2)])
    pkgs = pk("scalp", 100.0, 100.5, 2) + pk("pairs_a", 100.0, 200.0, 2)
    v = U46.grade_block(b, pkgs)
    assert v["restated"]["expectancy_r_as_reported"] == 0.5
    assert v["restated"]["expectancy_r_admissible_only"] == 1.0
    assert v["restated"]["ratio"] == 2.0
    txt = U46.render(v)
    assert "as reported" in txt and "admissible rows" in txt


def test_a_per_leg_table_that_does_not_sum_to_its_block_is_caught():
    """Catches the class U45 hit on its own first draft: a table and a census
    counting different populations, with nothing in the output disagreeing."""
    b = block([leg("scalp", 2, 10.0, 2.0, 1.0)])
    b["totalTrades"] = 9
    assert U46.grade_block(b, pk("scalp", 100.0, 100.5, 2))["reconciles"]["ok"] is False


def test_a_total_r_that_does_not_sum_is_caught_too():
    b = block([leg("scalp", 2, 10.0, 2.0, 1.0)])
    b["totalR"] = 99.0
    assert U46.grade_block(b, pk("scalp", 100.0, 100.5, 2))["reconciles"]["ok"] is False


def test_the_inadmissible_share_of_pnl_is_reported_beside_the_share_of_rows():
    """The two are the whole point when they diverge: on the live paper block
    the sentinel legs are 49.4% of the R population and 0.4% of its |PnL|."""
    b = block([leg("scalp", 2, 1000.0, 2.0, 1.0),
               leg("pairs_a", 2, 4.0, 0.0, 0.0, declared=0, stored=2)])
    pkgs = pk("scalp", 100.0, 100.5, 2) + pk("pairs_a", 100.0, 200.0, 2)
    a = U46.grade_block(b, pkgs)["admissible"]
    assert a["inadmissible_share_of_block"] == 0.5
    assert a["inadmissible_share_of_abs_pnl"] == pytest.approx(4.0 / 1004.0, abs=1e-4)


def test_an_ungraded_leg_is_neither_admissible_nor_inadmissible():
    """Catches: folding 'we could not look' into either bucket, which would
    make admissibleShare a statement about a population nobody measured."""
    b = block([leg("scalp", 2, 10.0, 2.0, 1.0), leg("blind", 3, 1.0, 0.1, 0.03)])
    v = U46.grade_block(b, pk("scalp", 100.0, 100.5, 2))
    assert v["admissible"]["ungraded_trades"] == 3
    assert v["admissible"]["admissible_trades"] == 2
    assert v["admissible"]["inadmissible_trades"] == 0
    assert v["admissible"]["admissible_share"] == 1.0


# --------------------------------------------------------------------------
# the stated bound — PB-20260821's clause 1 requires one
# --------------------------------------------------------------------------
def test_the_largest_absolute_expectancy_r_is_reported_with_its_leg():
    b = block([leg("a", 1, 1.0, 0.5, 0.5), leg("b", 1, -1.0, -3.0, -3.0)])
    v = U46.grade_block(b, pk("a", 100.0, 100.5))
    assert v["max_abs_expectancy_r"] == -3.0
    assert v["max_abs_expectancy_r_leg"] == "b"


def test_the_bound_is_stated_by_the_caller_and_the_verdict_names_it():
    """Catches: an instrument inventing a plausibility bound. The row's clause
    1 says 'a STATED plausible bound' — stating it is the caller's job."""
    b = block([leg("b", 1, -1.0, -3.0, -3.0)])
    v = U46.grade_block(b, pk("b", 100.0, 100.5))
    assert "EXCEEDS the stated bound 2.00" in U46.render(v, bound=2.0)
    assert "WITHIN the stated bound 5.00" in U46.render(v, bound=5.0)
    assert "bound" not in U46.render(v, bound=None)


def test_a_non_mapping_block_is_refused_not_graded():
    v = U46.grade_block(None, [])
    assert v["reported"].get("error")
    assert v["admissible"] is None


def test_render_never_raises_on_any_reachable_report():
    b = block([leg("scalp", 2, 10.0, 2.0, 1.0)])
    for v in (U46.grade_block(b, None),
              U46.grade_block(b, pk("scalp", 100.0, 100.5, 2)),
              U46.grade_block(None, [])):
        assert isinstance(U46.render(v, bound=3.0), str)


def test_the_module_self_test_passes():
    assert U46._self_test() == 0
