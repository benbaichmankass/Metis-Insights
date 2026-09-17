"""Controls for MI-301's stop-term robustness instrument.

These run the module's own `--self-test` controls under pytest (a required CI
context) AND pin the properties that would silently license a Tier-3 revert if
they regressed. The self-test is the readable form; these are the enforced one.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "research" / "mi301_stop_term_robustness.py"
sys.path.insert(0, str(REPO / "scripts" / "research"))
sys.path.insert(0, str(REPO))

M = pytest.importorskip("mi301_stop_term_robustness")


def test_self_test_passes() -> None:
    """The module's own controls must pass as a whole."""
    r = subprocess.run([sys.executable, str(SCRIPT), "--self-test"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "0 control(s) failed" in r.stdout


# --- the interval ---------------------------------------------------------

def test_empty_cell_has_no_interval() -> None:
    """None, never (0.0, 0.0): 'no unit reached this cell' is not 'pinned at 0'."""
    assert M.wilson(0, 0) is None


def test_two_of_eight_is_not_an_estimate() -> None:
    """The treated pre cell's shape. Its interval must visibly span everything."""
    lo, hi = M.wilson(2, 8)
    assert hi - lo > 40.0
    # and it must CONTAIN the control's pre rate, i.e. the arms are not
    # distinguishable in the pre era
    assert lo <= 100.0 * 15 / 47 <= hi


def test_interval_tightens_with_n() -> None:
    a = M.wilson(2, 8)
    b = M.wilson(20, 80)
    assert (b[1] - b[0]) < (a[1] - a[0])


# --- the floors -----------------------------------------------------------

def test_numerator_floor_binds_independently_of_the_cell_floor() -> None:
    """4 stop-outs in 44 clears the CELL floor and must still be ungradeable.

    A cell floor alone would pass it: 2/8 and 4/44 are equally undecided about
    the rate, and the binding quantity is the numerator.
    """
    units = ([{"adjudicated": "reached_stop"}] * 4
             + [{"adjudicated": "neither"}] * 40)
    got = M.rate(units, M.NUMERATORS["all_stop_outs"])
    assert got["n"] >= M.MIN_CELL
    assert got["state"] == "insufficient_n"


def test_empty_cell_is_a_distinct_state_from_underpowered() -> None:
    assert M.rate([], M.NUMERATORS["all_stop_outs"])["state"] == "empty"
    assert M.rate([], M.NUMERATORS["all_stop_outs"])["rate_pp"] is None


def test_underpowered_cell_still_reports_its_number() -> None:
    """The state refuses the estimate; it must not hide the observation."""
    units = ([{"adjudicated": "reached_stop"}] * 2
             + [{"adjudicated": "neither"}] * 6)
    assert M.rate(units, M.NUMERATORS["all_stop_outs"])["rate_pp"] == 25.0


# --- the numerators -------------------------------------------------------

def test_amended_stop_out_is_not_declared_geometry() -> None:
    """BL-20260911-A-TRAILED-STOP-OUT-IS-ATTRIBUTED-TO-THE-DECLARED-GEOMETRY."""
    u = {"adjudicated": "reached_stop", "stop_integrity": "stop_amended_tighter"}
    assert not M.NUMERATORS["declared_only"](u)
    assert M.NUMERATORS["all_stop_outs"](u)


# --- the verdict term -----------------------------------------------------

@pytest.mark.parametrize("flips_at", [0, 1])
def test_a_one_row_margin_is_never_reported_as_a_risen_stop_rate(flips_at: int) -> None:
    """THE LOAD-BEARING CONTROL.

    A positive DiD whose sign flips within one observation must NOT return
    `stop_rose`. `stop_rose` is what `m20_u41_break_attribution_verdict.verdict()`
    reads to reach `both_contribute`, i.e. to put an e35 revert on the table.
    """
    assert M.verdict_term(5.31, "measured", flips_at) == "sign_fragile"


def test_a_robust_positive_term_is_still_reported_positive() -> None:
    """The guard above must not swallow a real effect."""
    assert M.verdict_term(19.5, "measured", 6) == "stop_rose"


def test_an_ungradeable_cell_cannot_produce_a_term() -> None:
    assert M.verdict_term(19.5, "ungradeable_cell", 6) == "cannot_discriminate"
    assert M.verdict_term(None, "measured", 6) == "cannot_discriminate"


def test_negative_robust_term_is_its_own_state() -> None:
    assert M.verdict_term(-8.0, "measured", None) == "stop_did_not_rise"


# --- the DiD and the flip ladder ------------------------------------------

def _live_shape() -> dict:
    n = M.NUMERATORS["all_stop_outs"]
    mk = lambda s, t: ([{"adjudicated": "reached_stop"}] * s
                       + [{"adjudicated": "neither"}] * (t - s))
    return {
        "e35": {"pre": M.rate(mk(2, 8), n), "post": M.rate(mk(11, 20), n)},
        "untouched_control": {"pre": M.rate(mk(22, 47), n),
                              "post": M.rate(mk(59, 103), n)},
    }


def test_did_arithmetic_on_the_live_cell_shape() -> None:
    d = M.did(_live_shape())
    assert abs(d["did_pp"] - 19.5269) < 0.01
    # positive, and STILL not gradeable — both facts must survive together
    assert d["did_pp"] > 0
    assert d["state"] == "ungradeable_cell"


def test_flip_ladder_starts_at_the_observed_value() -> None:
    f = M.flip_threshold(_live_shape())
    assert f["ladder"][0]["did_pp"] == 19.53
    assert f["ladder"][0]["stop_rose"] is True


def test_sign_flips_within_two_observations_on_the_live_shape() -> None:
    f = M.flip_threshold(_live_shape())
    assert f["flips_at"] == 2
    assert f["ladder"][f["flips_at"]]["stop_rose"] is False


def test_did_is_none_when_a_cell_is_empty() -> None:
    n = M.NUMERATORS["all_stop_outs"]
    cells = _live_shape()
    cells["e35"]["pre"] = M.rate([], n)
    d = M.did(cells)
    assert d["did_pp"] is None
    assert d["state"] == "ungradeable_cell"


# --- provenance -----------------------------------------------------------

def test_provenance_uses_the_canonical_module() -> None:
    """Never a bespoke predicate — CLAUDE.md names that as the defect class."""
    src = SCRIPT.read_text()
    assert "from src.runtime import provenance" in src
    assert "P.split_counts" in src
    assert "P.classify_row" in src


def test_notes_parse_failure_is_detected_not_silently_unverified() -> None:
    """`_decode_notes` returns {} on bad JSON, which reads as 'no provenance'."""
    assert M._notes_ok('{"a": 1}') is True
    assert M._notes_ok("{truncated") is False
    assert M._notes_ok(None) is False
