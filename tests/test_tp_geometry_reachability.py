"""Controls for MI-278 U48 — `tp_geometry` reachability inside one family.

The module's central claim is a NEGATIVE — *no family can carry both contrast
levels at any cap* — and a negative is exactly the kind of claim that rots
silently. These controls therefore pin the two things that make it a proof
rather than a sample:

  * the SIGN TEST (the cap enters the label only through ``cap > 0``), without
    which every ``unreachable_by_construction`` must degrade to ``unknown``; and
  * that the module reads the REAL producers rather than a copy of their rule.

They also pin the three *we did not look* paths apart from their negatives,
because collapsing any of them is what would turn this instrument into the
class of defect it was written to find.
"""
from __future__ import annotations

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts", "research")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tp_geometry_reachability as U48  # noqa: E402


@pytest.fixture(scope="module")
def prod():
    try:
        return U48.load_producers()
    except U48.ProducersUnavailable as exc:  # pragma: no cover - environment
        pytest.skip(f"real producers unavailable: {exc}")


def _row(leg, family, geometry, verdict, cap=0.099, block_unit="family_pooled"):
    return {"leg": leg, "family": family, "tp_geometry": geometry,
            "verdict": verdict, "tp_cap_pct": cap, "block_unit": block_unit}


# --------------------------------------------------------------- producers

def test_the_module_reads_the_real_label_owner_not_a_copy(prod):
    """If this ever imports a local re-derivation, the whole claim is hearsay."""
    import m20_fleet_exit_sweep as fs
    assert prod["tp_geometry_for"] is fs.tp_geometry_for
    assert prod["base_args"] is fs.base_args


def test_clamping_families_comes_from_the_live_single_owner(prod):
    from src.runtime.tp_venue_cap import CLAMPING_FAMILIES
    assert prod["clamping_families"] == frozenset(CLAMPING_FAMILIES)


def test_producers_unavailable_refuses_rather_than_falling_back(monkeypatch):
    """A missing producer must REFUSE, never quietly re-derive the predicate."""
    monkeypatch.setitem(sys.modules, "m20_fleet_exit_sweep", None)
    with pytest.raises(U48.ProducersUnavailable):
        U48.load_producers()


# ------------------------------------------------------------- the sign test

def test_sign_test_holds_on_the_real_producer(prod):
    st = U48.cap_enters_only_as_a_sign_test(prod, sorted(prod["clamping_families"]))
    assert st["holds"] is True
    assert st["counterexamples"] == []
    assert st["families_graded"] == len(prod["clamping_families"])


def test_sign_test_fails_loudly_if_the_label_ever_depends_on_the_caps_SIZE(prod):
    """This is the assertion that keeps 'unreachable' from becoming a lie."""
    real = prod["tp_geometry_for"]
    fake = dict(prod)
    fake["tp_geometry_for"] = (
        lambda fams, cap: "live_parity_uncapped" if cap > 0.5 else real(fams, cap))
    st = U48.cap_enters_only_as_a_sign_test(fake, ["pullback"])
    assert st["holds"] is False
    assert st["counterexamples"][0]["family"] == "pullback"


def test_sign_test_on_no_families_is_unknown_not_true(prod):
    assert U48.cap_enters_only_as_a_sign_test(prod, [])["holds"] is None


# ------------------------------------------------------- per-family verdicts

def test_a_clamping_family_can_never_be_uncapped(prod):
    f = U48.family_reachability(prod, "pullback")
    assert f["pair_state"] == U48.PAIR_CAPPED_ONLY
    assert "live_parity_uncapped" not in f["labels_attainable"]


def test_no_take_profit_is_not_counted_as_the_uncapped_level(prod):
    """A capped family at cap 0 is a NO-TARGET book, not the parity arm."""
    f = U48.family_reachability(prod, "pullback")
    assert "NO_TAKE_PROFIT" in f["labels_attainable"]
    assert f["pair_state"] == U48.PAIR_CAPPED_ONLY
    assert "NO_TAKE_PROFIT" not in U48.CONTRAST_LEVELS


def test_a_non_clamping_familys_label_does_not_move_at_all(prod):
    f = U48.family_reachability(prod, "scalp")
    assert f["pair_state"] == U48.PAIR_UNCAPPED_ONLY
    assert f["label_varies"] is False


def test_the_cap_flag_never_reaches_a_non_clamping_harness(prod):
    """The row's unestablished caveat, established: scalp's recorded cap is inert."""
    assert U48.flag_delivered(prod, "scalp", 0.099) is False
    assert U48.flag_delivered(prod, "pullback", 0.099) is True
    assert U48.family_reachability(prod, "scalp")["dose_state"] == U48.DOSE_INERT
    assert U48.family_reachability(prod, "pullback")["dose_state"] == U48.DOSE_REACHABLE


def test_an_unreadable_argv_build_is_unknown_not_withheld(prod):
    broken = dict(prod)

    def _boom(*_a, **_k):
        raise RuntimeError("no argv")

    broken["base_args"] = _boom
    assert U48.flag_delivered(broken, "pullback", 0.099) is None
    assert U48.family_reachability(broken, "pullback")["dose_state"] == U48.DOSE_UNKNOWN


# ------------------------------------------------------------ clause grading

def test_clause_a_is_unsatisfiable_by_construction_on_the_real_producers(prod):
    fams = sorted(prod["clamping_families"]) + ["scalp"]
    st = U48.cap_enters_only_as_a_sign_test(prod, fams)
    per = [U48.family_reachability(prod, f) for f in fams]
    ca = U48.grade_clause_a(per, st)
    assert ca["state"] == U48.CLAUSE_UNSATISFIABLE
    assert ca["families_with_both"] == []


def test_clause_a_degrades_to_unknown_without_the_sign_test(prod):
    per = [U48.family_reachability(prod, f) for f in ("pullback", "scalp")]
    for holds in (None, False):
        assert U48.grade_clause_a(per, {"holds": holds})["state"] == U48.CLAUSE_UNKNOWN


def test_clause_a_on_no_families_is_unknown_not_unsatisfiable():
    assert U48.grade_clause_a([], {"holds": True})["state"] == U48.CLAUSE_UNKNOWN


def test_one_ungradeable_family_blocks_the_absence_claim(prod):
    per = [U48.family_reachability(prod, "pullback"),
           {"family": "x", "pair_state": U48.PAIR_UNKNOWN}]
    assert U48.grade_clause_a(per, {"holds": True})["state"] == U48.CLAUSE_UNKNOWN


def test_clause_a_is_satisfiable_when_some_family_really_does_carry_both():
    per = [{"family": "imaginary", "pair_state": U48.PAIR_BOTH}]
    ca = U48.grade_clause_a(per, {"holds": True})
    assert ca["state"] == U48.CLAUSE_SATISFIABLE
    assert ca["families_with_both"] == ["imaginary"]


# ------------------------------------------------------------------- census

def test_census_separates_declared_from_delivered(prod):
    rows = [_row("a", "pullback", "live_parity_capped", "candidate"),
            _row("b", "scalp", "live_parity_uncapped", "candidate",
                 block_unit="per_leg")]
    c = U48.arm_census(prod, rows)
    assert c["declared_vs_delivered"] == {"delivered": 1,
                                          "recorded_but_never_delivered": 1}


def test_a_non_numeric_cap_is_ungradeable_and_enters_no_bucket(prod):
    c = U48.arm_census(prod, [_row("a", "scalp", "live_parity_uncapped",
                                   "candidate", cap="oops")])
    assert c["ungradeable"]["non_numeric_tp_cap_pct"] == 1
    assert c["declared_vs_delivered"] == {}


def test_an_unreadable_delivery_is_ungradeable_not_delivered(prod):
    broken = dict(prod)

    def _boom(*_a, **_k):
        raise RuntimeError("no argv")

    broken["base_args"] = _boom
    c = U48.arm_census(broken, [_row("a", "pullback", "live_parity_capped", "x")])
    assert c["ungradeable"]["flag_delivery_unreadable"] == 1
    assert c["declared_vs_delivered"] == {}


def test_census_names_families_carrying_one_geometry_only(prod):
    rows = [_row("a", "pullback", "live_parity_capped", "candidate"),
            _row("b", "pullback", "live_parity_capped", "honest_negative"),
            _row("c", "scalp", "live_parity_uncapped", "candidate")]
    c = U48.arm_census(prod, rows)
    assert set(c["families_with_one_geometry_only"]) == {"pullback", "scalp"}


# --------------------------------------------------- within-level attribution

def test_within_level_spread_bounds_the_between_level_story():
    rows = [_row("a", "donchian", "live_parity_capped", "candidate"),
            _row("b", "donchian", "live_parity_capped", "honest_negative"),
            _row("c", "pullback", "live_parity_capped", "honest_negative"),
            _row("d", "pullback", "live_parity_capped", "honest_negative"),
            _row("e", "scalp", "live_parity_uncapped", "candidate")]
    w = U48.within_geometry_spread(rows)
    assert w["per_level"]["live_parity_capped"]["spread"] == pytest.approx(0.5)
    assert w["between_level_spread"] == pytest.approx(0.75)
    assert w["within_over_between"] == pytest.approx(0.5 / 0.75)


def test_a_one_family_level_has_no_spread_rather_than_zero():
    w = U48.within_geometry_spread([_row("e", "scalp", "live_parity_uncapped", "x")])
    assert w["per_level"]["live_parity_uncapped"]["spread"] is None
    assert w["between_level_spread"] is None
    assert w["within_over_between"] is None


def test_a_zero_between_level_spread_yields_none_not_a_crash():
    rows = [_row("a", "donchian", "live_parity_capped", "candidate"),
            _row("b", "pullback", "live_parity_capped", "honest_negative"),
            _row("c", "scalp", "live_parity_uncapped", "candidate"),
            _row("d", "scalp", "live_parity_uncapped", "honest_negative")]
    w = U48.within_geometry_spread(rows)
    assert w["between_level_spread"] == 0.0
    assert w["within_over_between"] is None


# ------------------------------------------------------------------- report

def test_an_absent_corpus_publishes_no_census_and_says_so(prod):
    rep = U48.report(arms_path="/nonexistent/arms.jsonl")
    assert rep["census"] is None
    assert rep["within_geometry_spread"] is None
    assert rep["arms_state"].startswith("absent:")
    assert "ARM CORPUS NOT GRADED" in U48.render(rep)


def test_reachability_is_graded_even_with_no_corpus(prod):
    """The negative is about the PRODUCERS, so it does not need the corpus."""
    assert U48.report(arms_path="/nonexistent/arms.jsonl")["clause_a"]["state"] == (
        U48.CLAUSE_UNSATISFIABLE)


def test_render_shouts_when_the_sign_test_did_not_hold(prod):
    rep = U48.report(arms_path="/nonexistent/arms.jsonl")
    rep["sign_test"] = {**rep["sign_test"], "holds": None, "counterexamples": []}
    assert "SAMPLE" in U48.render(rep)


def test_self_test_passes():
    assert U48._self_test() == 0
