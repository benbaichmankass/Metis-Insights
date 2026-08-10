"""Tests for the ONE exit-capture definition (scripts/exit_capture.py).

The axis that measures the operator's actual complaint -- "got within cents of
TP and then it turned into a loss" -- and which no gate in this repo has ever
read (BL-20260810-EXIT-GATE-BLIND-TO-CAPTURE-AND-CAPITAL).

These pin the properties that make the census trustworthy, each one a case
where the convenient answer is a number that reads as good news:

  * an undefined capture is None, never 0.0;
  * near-miss on a leg with no fixed target is None, never "0% -- all clear";
  * a give-it-all-back loser is NOT clamped to zero capture, because that
    erases the exact population being counted.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import exit_capture as ec  # noqa: E402


def _t(net_r, mfe_r):
    return {"net_r": net_r, "mfe_r": mfe_r}


# --------------------------------------------------------------- capture_ratio
def test_capture_is_none_when_the_trade_never_went_favourable():
    """Undefined, not zero. A fabricated 0.0 would drag a fleet mean toward a
    value no trade ever printed."""
    assert ec.capture_ratio(-1.0, 0.0) is None
    assert ec.capture_ratio(-1.0, None) is None
    assert ec.capture_ratio(-1.0, -0.2) is None


def test_capture_of_a_giveback_loser_is_negative_not_clamped():
    """Peaked at +1.4R, closed at -1R. Capture is negative and must stay so --
    clamping to 0 erases the give-it-all-back case this module exists to count."""
    assert ec.capture_ratio(-1.0, 1.4) == round(-1.0 / 1.4, 4)


def test_capture_of_a_clean_winner():
    assert ec.capture_ratio(1.2, 1.5) == 0.8


def test_non_numeric_and_nan_degrade_to_none():
    assert ec.capture_ratio("n/a", 1.5) is None
    assert ec.capture_ratio(1.0, float("nan")) is None


# ------------------------------------------------------------------- near-miss
def test_near_miss_is_none_for_a_trail_leg_not_zero():
    """A pullback/trend/squeeze leg has no fixed target, so it CANNOT have a
    near-miss. Reporting 0% would read as a clean bill of health for exactly
    the population under suspicion."""
    s = ec.summarize([_t(-1.0, 1.4), _t(2.0, 2.2)], target_r=None)
    for k in ("near_miss_80_pct", "near_miss_90_pct", "near_miss_95_pct",
              "near_miss_measured_n", "near_miss_r_left_on_table"):
        assert s[k] is None, f"{k} must be None for a trail leg, got {s[k]!r}"
    # ...but capture still works, because capture generalises.
    assert s["capture_mean"] is not None


def test_near_miss_bands_count_only_losers():
    """A winner that reached 99% of target is not the complaint."""
    trades = [
        _t(-1.0, 1.45),   # loser, 96.7% of a 1.5R target -> in all three bands
        _t(-1.0, 1.35),   # loser, 90.0%                  -> 80 and 90
        _t(-1.0, 1.25),   # loser, 83.3%                  -> 80 only
        _t(-1.0, 0.10),   # loser, nowhere near           -> none
        _t(1.5, 1.49),    # WINNER at 99%                 -> excluded entirely
    ]
    s = ec.summarize(trades, target_r=1.5)
    assert s["near_miss_measured_n"] == 4      # 4 losers, winner excluded
    assert s["near_miss_95_pct"] == 25.0       # 1/4
    assert s["near_miss_90_pct"] == 50.0       # 2/4
    assert s["near_miss_80_pct"] == 75.0       # 3/4


def test_r_left_on_table_sums_the_90pct_band_only():
    """The prize, in R, on the axis the gate already speaks."""
    s = ec.summarize([_t(-1.0, 1.45), _t(-1.0, 1.35), _t(-1.0, 0.1)],
                     target_r=1.5)
    # (1.45 - -1.0) + (1.35 - -1.0) = 2.45 + 2.35 ; the 0.1 loser is not a near-miss
    assert s["near_miss_r_left_on_table"] == 4.8


def test_loser_with_unmeasured_mfe_is_excluded_from_both_sides():
    """Not counted as a near-miss, and not counted as a clean non-near-miss --
    an unmeasured row must not silently improve the rate."""
    s = ec.summarize([_t(-1.0, 1.45), _t(-1.0, None)], target_r=1.5)
    assert s["near_miss_measured_n"] == 1
    assert s["near_miss_90_pct"] == 100.0   # 1/1, NOT 50% from a phantom denominator


# -------------------------------------------------------------------- envelope
def test_every_key_is_always_present():
    """A consumer never has to distinguish 'absent' from 'not computed'."""
    for s in (ec.summarize([], target_r=1.5), ec.summarize([], target_r=None),
              ec.empty(), ec.summarize([_t(1.0, 2.0)], target_r=2.0)):
        assert set(ec.KEYS) <= set(s), sorted(set(ec.KEYS) - set(s))


def test_zero_trade_leg_reports_counts_not_fake_rates():
    s = ec.summarize([], target_r=1.5)
    assert s["n_trades"] == 0 and s["capture_measured_n"] == 0
    assert s["capture_mean"] is None and s["capture_lt_30_pct"] is None


def test_module_encodes_no_capture_threshold():
    """The <30% / >75% bands are REPORTING buckets drawn from the external
    literature, not a gate. Nothing here may grade a leg pass/fail -- that
    threshold is the operator's to set from this distribution, the same
    discipline capital_efficiency follows."""
    import ast
    src = (Path(__file__).resolve().parents[1] / "scripts" / "exit_capture.py").read_text()
    tree = ast.parse(src)
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        body = fn.body[1:] if (fn.body and isinstance(fn.body[0], ast.Expr)
                               and isinstance(fn.body[0].value, ast.Constant)
                               and isinstance(fn.body[0].value.value, str)) else fn.body
        code = "\n".join(ast.unparse(n) for n in body)
        for banned in ("PASS", "verdict", "is_pass", "gate"):
            assert banned.lower() not in code.lower(), \
                f"{fn.name} must report, not grade: found {banned!r}"


# ------------------------------------------------- denominator-noise regression
def test_capture_mean_is_denominator_noise_and_the_robust_mean_is_not():
    """The 2026-08-10 census shipped `capture_mean` as its only stdout figure and
    reported -14.13 for fvg_range_15m and -5.05 for htf_pullback_trend_2h. Those
    are not captures; they are a ratio exploding on a near-zero MFE.

    A book whose real capture is 0.8 reproduces the artifact exactly when three
    of ten trades peaked at 0.05R: each contributes -20 to the mean. The median
    and the robust mean both read the truth, and the excluded count is stated.
    """
    trades = [_t(-1.0, 0.05)] * 3 + [_t(1.2, 1.5)] * 7
    s = ec.summarize(trades, target_r=1.5)

    assert s["capture_mean"] < -5            # the artifact, reproduced
    assert s["capture_median"] == 0.8        # robust to it
    assert s["capture_mean_robust"] == 0.8   # robust to it
    assert s["capture_lowmfe_n"] == 3        # and the exclusion is STATED
    assert s["capture_measured_n"] == 10     # over an unchanged denominator


def test_robust_mean_is_none_not_zero_when_every_trade_is_below_the_floor():
    """No trade cleared the MFE floor: the robust mean is undefined, and 0.0
    would read as 'we captured nothing' rather than 'we could not measure'."""
    s = ec.summarize([_t(-1.0, 0.01), _t(-1.0, 0.02)], target_r=1.5)
    assert s["capture_mean_robust"] is None
    assert s["capture_lowmfe_n"] == 2
    assert s["capture_measured_n"] == 2      # they ARE measured, just not robustly


def test_mfe_floor_is_reported_so_the_robust_mean_is_interpretable():
    """A statistic computed against a floor must say which floor."""
    assert ec.summarize([], target_r=1.0)["mfe_floor_r"] == ec.DEFAULT_MFE_FLOOR_R
    assert ec.summarize([], target_r=1.0, mfe_floor_r=0.25)["mfe_floor_r"] == 0.25


# ------------------------------------- a declared target is not an operative one
def test_disabled_tp_sentinel_does_not_print_a_reassuring_zero():
    """Eight pullback legs declare `tp_r: 50.0` -- a 50R take-profit on a
    trailing strategy, i.e. a DISABLED-TP sentinel. The first census treated it
    as a real target and printed near_miss_90_pct = 0.0 for eth_pullback_2h,
    the very leg the operator cited. Of course nothing reached 45R.

    The test is derived from the population, not a magic cut-off: if no trade
    ever reached the declared target, the target is not operative.
    """
    s = ec.summarize([_t(-1.0, 0.4), _t(2.1, 3.0)], target_r=50.0)
    assert s["near_miss_90_pct"] is None
    assert s["near_miss_80_pct"] is None and s["near_miss_95_pct"] is None
    assert s["near_miss_not_applicable"] == "target_never_approached_by_any_trade"
    assert s["target_r_reached_n"] == 0
    # capture is still measured -- only the near-miss half is inapplicable
    assert s["capture_median"] is not None


# ------------------------------------------- two harness shapes, one accessor
def test_mfe_is_read_from_the_nested_scalp_shape():
    """`backtest_ict_scalp` nests mfe_r under `meta`; trend/pullback/squeeze put
    it at the top level. Measured 2026-08-10: the census read only the top level
    and reported `meas=0/1102` for ict_scalp_avax_5m — 3,823 scalp trades, zero
    capture readings — while `winner_mfe_p80` returned its "fewer than 30
    winners" answer for the same legs."""
    assert ec.mfe_r_of({"net_r": -1.0, "meta": {"mfe_r": 1.4}}) == 1.4
    assert ec.mfe_r_of({"net_r": -1.0, "mfe_r": 1.4}) == 1.4
    # top level wins when both are present, and a non-dict meta never raises
    assert ec.mfe_r_of({"mfe_r": 2.0, "meta": {"mfe_r": 9.9}}) == 2.0
    assert ec.mfe_r_of({"net_r": 1.0}) is None
    assert ec.mfe_r_of({"net_r": 1.0, "meta": None}) is None
    assert ec.mfe_r_of({"net_r": 1.0, "meta": "not-a-dict"}) is None


def test_summarize_measures_a_nested_shape_book_end_to_end():
    """The regression proper: an all-nested book must not summarise to zero
    coverage. This is the assertion whose absence let the defect survive two
    census runs."""
    nested = [{"net_r": -1.0, "meta": {"mfe_r": 1.4}},
              {"net_r": 1.2, "meta": {"mfe_r": 1.5}}]
    s = ec.summarize(nested, target_r=1.5)
    assert s["capture_measured_n"] == 2, \
        "a scalp-shaped book measured zero trades — the 2026-08-10 defect"
    assert s["capture_median"] is not None
    assert s["near_miss_90_pct"] == 100.0     # the one loser peaked at 1.4/1.5


def test_an_operative_target_still_reports_near_miss():
    """The guard must not suppress a real 1.5R bracket the book actually reaches."""
    s = ec.summarize([_t(-1.0, 1.45), _t(1.5, 1.6)], target_r=1.5)
    assert s["target_r_reached_n"] == 1
    assert s["near_miss_not_applicable"] is None
    assert s["near_miss_90_pct"] == 100.0     # the one loser, at 96.7% of target


def test_trail_leg_reason_is_distinct_from_disabled_target_reason():
    """Two different 'not applicable' causes must stay distinguishable -- one is
    'this family has no target', the other is 'the declared target is dead'."""
    assert ec.summarize([_t(1.0, 2.0)], target_r=None)["near_miss_not_applicable"] \
        == "no_declared_target"
    assert ec.summarize([_t(1.0, 2.0)], target_r=99.0)["near_miss_not_applicable"] \
        == "target_never_approached_by_any_trade"


# ----------------------------- the giveback ladder: the target-free complaint
def test_giveback_ladder_separates_structural_pokes_from_real_giveback():
    """The discrimination the all-trade capture median CANNOT make, and the
    reason every capture figure so far has needed a verbal caveat.

    A breakout book is structurally full of small pokes that fail. Measured
    2026-08-10, squeeze_breakout_4h read capture median -0.39 / 72.45% under
    30% capture -- which looks alarming and may be nothing but that structure.
    The operator's actual complaint is narrower: trades that went MEANINGFULLY
    favourable and still closed red.

    Both books below have ~70/30 and ~60/40 win splits and negative-ish means.
    Only the second one is the complaint, and only the ladder says so.
    """
    pokes = [_t(-1.0, 0.3)] * 70 + [_t(2.0, 2.4)] * 30      # fails small
    giveback = [_t(-1.0, 2.2)] * 40 + [_t(2.0, 2.4)] * 60   # fails BIG

    sp = ec.summarize(pokes, target_r=None)
    sg = ec.summarize(giveback, target_r=None)

    # The headline bucket is WORSE for the innocent book -- the trap.
    assert sp["capture_lt_30_pct"] > sg["capture_lt_30_pct"]

    def at(s, rung):
        return next(r for r in s["giveback_ladder"] if r["mfe_ge_r"] == rung)

    # ...and the ladder inverts that, correctly.
    assert at(sp, 2.0)["lost_n"] == 0, "poke book flagged as giving winners back"
    assert at(sg, 2.0)["lost_n"] == 40
    assert at(sg, 2.0)["lost_pct"] == 40.0
    # The prize, in R, on the axis the gate already speaks: 40 * (2.2 - -1.0)
    assert at(sg, 2.0)["r_left"] == 128.0
    assert at(sp, 2.0)["r_left"] == 0.0


def test_ladder_rung_always_ships_its_denominator():
    """A rung no trade ever reached must report 0 reached, not a rate over
    nothing -- an unasserted denominator is how `0.0% lost` reads as good news
    for a rung the book never tested."""
    s = ec.summarize([_t(1.0, 0.4), _t(-1.0, 0.2)], target_r=None)
    high = next(r for r in s["giveback_ladder"] if r["mfe_ge_r"] == 2.0)
    assert high["mfe_ge_n"] == 0
    assert high["lost_pct"] is None, "a rate over an empty denominator must be None"
    low = next(r for r in s["giveback_ladder"] if r["mfe_ge_r"] == 0.5)
    assert low["mfe_ge_n"] == 0      # neither trade reached even 0.5R


def test_r_weighted_capture_is_portfolio_level_not_a_trade_mean():
    """A per-trade mean weights a 0.2R scratch like a 6R runner. R-weighted
    does not, so it is the figure that tracks money."""
    book = [_t(0.1, 0.2)] * 9 + [_t(1.0, 6.0)]     # nine scratches, one runner
    s = ec.summarize(book, target_r=None)
    assert s["capture_median"] == 0.5               # the scratches dominate
    # (9*0.1 + 1.0) / (9*0.2 + 6.0) = 1.9 / 7.8
    assert s["capture_r_weighted"] == round(1.9 / 7.8, 4)
    assert s["capture_r_weighted"] < s["capture_median"]


def test_r_weighted_is_none_not_zero_when_no_excursion_was_offered():
    s = ec.summarize([_t(-1.0, 0.0), _t(-1.0, None)], target_r=None)
    assert s["capture_r_weighted"] is None
