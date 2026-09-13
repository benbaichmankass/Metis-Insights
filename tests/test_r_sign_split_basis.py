"""MI-278 U36 — expectancyR disagreeing with totalPnl: defect, or R working?

The module ships an executable `--self-test`; this file is the pytest surface so
those controls run in CI. Every test names the defect it would catch, and each
was verified by PLANTING that defect and watching this file fail.
"""
import importlib.util
import os

import pytest

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location(
    "_u36", os.path.join(_HERE, "scripts", "research", "r_sign_split_basis.py"))
U36 = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(U36)

CLEAN = {"declaredInitial": 41, "storedStop": 0, "refusedWrongSide": 0, "noBasis": 0}
DIRTY = {"declaredInitial": 30, "storedStop": 11, "refusedWrongSide": 0, "noBasis": 0}


def leg(name, pnl, r, n=5):
    return {"name": name, "trades": n, "totalPnl": pnl, "totalR": r,
            "expectancyR": (r / n if n else None), "rBasis": dict(CLEAN)}


def payload(**kw):
    base = {"expectancyR": 0.11, "totalPnl": -14.6, "profitFactor": 0.83,
            "rBasis": dict(CLEAN),
            "perStrategy": [leg("win_small", 16.9, 11.4), leg("win_mid", 8.0, 2.7),
                            leg("lose_a", -26.3, -6.9), leg("lose_b", -8.0, -1.9)]}
    base.update(kw)
    return base


def test_module_self_test_passes():
    assert U36._self_test() == 0


# --------------------------------------------------------------------------
# The sign test
# --------------------------------------------------------------------------

@pytest.mark.parametrize("er,pnl,expected", [
    (0.11, -14.6, "disagree"), (-0.2, -14.6, "agree"), (0.2, 14.6, "agree"),
    (0.0, -1.0, "not_computable"), (None, -1.0, "not_computable"),
    (0.1, None, "not_computable"), (0.1, 0.0, "not_computable"),
])
def test_sign_states(er, pnl, expected):
    assert U36.sign_state({"expectancyR": er, "totalPnl": pnl})[0] == expected


def test_an_absent_profit_factor_is_none_not_false():
    """A missing corroborator is not a disagreeing one.
    Plant: `bool(pf is not None and ...)`."""
    _, d = U36.sign_state({"expectancyR": 0.11, "totalPnl": -14.6})
    assert d["profit_factor_corroborates"] is None
    _, d = U36.sign_state({"expectancyR": 0.11, "totalPnl": -14.6, "profitFactor": 0.83})
    assert d["profit_factor_corroborates"] is True


# --------------------------------------------------------------------------
# The mechanism test — and why it must not read `contaminated`
# --------------------------------------------------------------------------

def test_mechanism_is_graded_from_rbasis_never_from_contaminated():
    """`rProvenance.contaminated` grades the STORED STOP; `rBasis` says what
    each R was DIVIDED BY. A declared-basis R never consults the stored stop, so
    a high contaminated count cannot make the mechanism live.

    Plant: grade off `contaminated`.
    """
    st, det = U36.mechanism_state({"rBasis": dict(CLEAN),
                                   "rProvenance": {"contaminated": 11}})
    assert st == "cannot_operate"
    assert det["rProvenance_contaminated"] == 11


def test_stored_stop_in_use_makes_the_mechanism_a_candidate():
    assert U36.mechanism_state({"rBasis": dict(DIRTY)})[0] == "can_operate"


def test_an_absent_rbasis_is_we_could_not_look():
    """Plant: return `cannot_operate` when rBasis is missing — that would assert
    a clean bill over a field nobody read."""
    assert U36.mechanism_state({})[0] == "not_gradeable"
    assert U36.mechanism_state({"rBasis": {}})[0] == "not_gradeable"


# --------------------------------------------------------------------------
# dollars-per-R, and the leg that has none
# --------------------------------------------------------------------------

def test_a_mixed_sign_leg_has_no_dollars_per_r():
    """A leg holding both winning and losing outcomes has no single
    dollars-per-R, and a negative ratio there would read as a risk.

    Plant: drop the `p * r > 0` test.
    """
    t = U36.leg_risk_table({"perStrategy": [leg("mixed", -0.33, 0.28, 4)]})[0]
    assert t["dollars_per_r"] is None
    assert t["dollars_per_r_state"] == "ungradeable_mixed_sign"


def test_a_same_sign_leg_yields_a_positive_ratio():
    t = U36.leg_risk_table({"perStrategy": [leg("w", 16.9, 11.4)]})[0]
    assert t["dollars_per_r"] == pytest.approx(16.9 / 11.4)
    assert t["dollars_per_r_state"] == "measured"


def test_an_ungradeable_leg_is_counted_not_dropped():
    v = U36.report(payload(perStrategy=payload()["perStrategy"]
                           + [leg("mixed", -0.33, 0.28, 4)]))
    assert v["explanation_detail"]["ungradeable_legs"] == 1
    assert v["explanation_detail"]["gradeable_legs"] == 4


# --------------------------------------------------------------------------
# The explanation — threshold-free separation, and no manufactured cause
# --------------------------------------------------------------------------

def test_clean_separation_grades_risk_size_heterogeneity():
    v = U36.report(payload())
    assert v["explanation"] == "risk_size_heterogeneity"
    assert v["explanation_detail"]["separated"] is True
    assert v["explanation_detail"]["gap"] > 0
    assert "risk normalisation working, not contamination" in v["verdict"]


def test_overlapping_ranges_are_not_a_clean_separation():
    """Plant: `if True:` on the separation branch."""
    v = U36.report(payload(perStrategy=[
        leg("win_a", 10.0, 2.0), leg("win_b", 2.0, 10.0),
        leg("lose_a", -10.0, -3.0), leg("lose_b", -2.0, -8.0)]))
    assert v["explanation_detail"]["separated"] is False
    assert v["explanation"] in ("partial", "unexplained")


def test_winners_sized_larger_leaves_it_unexplained():
    """The module does not manufacture a cause.
    Plant: always return `partial`."""
    v = U36.report(payload(perStrategy=[leg("win", 40.0, 2.0), leg("lose", -2.0, -8.0)]))
    assert v["explanation"] == "unexplained"
    assert "UNESTABLISHED" in v["verdict"]


def test_a_live_mechanism_is_not_displaced_by_a_benign_explanation():
    """An available real cause outranks a benign one.
    Plant: `if False:` on the can_operate branch of `_verdict`."""
    v = U36.report(payload(rBasis=dict(DIRTY)))
    assert v["mechanism_state"] == "can_operate"
    assert "mechanism is live" in v["verdict"]


def test_agreeing_signs_short_circuit():
    v = U36.report({"expectancyR": -0.2, "totalPnl": -14.6,
                    "rBasis": dict(CLEAN), "perStrategy": []})
    assert v["sign_state"] == "agree"
    assert v["explanation"] == "not_applicable"
    assert "criterion is met" in v["verdict"]


def test_too_few_legs_is_not_computable():
    v = U36.report(payload(perStrategy=[leg("win", 10.0, 2.0)]))
    assert v["explanation"] == "not_computable"


def test_every_state_is_declared():
    for p in (payload(), payload(rBasis=dict(DIRTY)),
              payload(perStrategy=[leg("w", 40.0, 2.0), leg("l", -2.0, -8.0)]),
              {"expectancyR": -0.2, "totalPnl": -1.0, "perStrategy": []}):
        v = U36.report(p)
        assert v["sign_state"] in U36.SIGN_STATES
        assert v["mechanism_state"] in U36.MECHANISM_STATES
        assert v["explanation"] in U36.EXPLANATION_STATES


def test_render_states_the_route_is_tier_2_and_untouched():
    assert "Tier-2" in U36.render(U36.report(payload()))
