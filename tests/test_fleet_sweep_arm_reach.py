"""A proposed exit arm must be reported against the ceiling it can fire under.

WHY THIS EXISTS. The sweep proposes a `trail_decay_arm_r` from the p80 of the
WINNER-MFE distribution, and until 2026-08-16 it never compared that arm to the
TP ceiling of the very book it had just measured. It could therefore propose an
arm no trade on the leg could reach and say nothing — which is exactly what
happened: six arms shipped 2026-07-12/13 and `gld_pullback_1d` (5.06R) was later
measured reachable on 0 of 8 live entries
(`BL-20260816-TRAIL-DECAY-ARM-R-SITS-ABOVE-THE-VENUE-TP-CAP`).

The data to catch it was already there. `leg_v["live_tp_reach_r"]` records
`tp_r_effective_*` — the per-trade cap_R measured on the leg's own base book.
Nothing compared the two. So the primary assertion here is the COMPARISON, and
the derived ATR/close ceiling is its interpretable companion.

THE IDENTITY, which is what makes the derived form legitimate rather than a
second definition: live units and both harnesses compute
`sl = entry -/+ atr_stop_mult*atr` with byte-identical `_atr` helpers, so
`risk/entry == atr_stop_mult * (ATR/close)` and
`cap_R == cap / (atr_stop_mult * ATR/close)`. Inverting gives the ceiling.
Memo: `docs/research/m20-arm-reachability-is-a-vol-threshold-2026-08-16.md`.

WHAT IS DELIBERATELY NOT ASSERTED: that any particular arm is reachable. That is
a measurement about a leg, not a property of this function. These tests pin the
arithmetic, and — more importantly — that the three states stay DISTINCT, since
"the book had no cap" and "we could not compute it" are opposite statements that
a shared `None` would collapse.
"""
from __future__ import annotations

import importlib.util
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]


def _mod():
    spec = importlib.util.spec_from_file_location(
        "m20_fleet_exit_sweep",
        REPO / "scripts" / "research" / "m20_fleet_exit_sweep.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_flag_value_reads_argv_pairs():
    fv = _mod().flag_value
    assert fv(["--atr-stop-mult", "2.5", "--trail-mult", "5.0"],
              "--atr-stop-mult") == 2.5
    assert fv(["--trail-mult", "5.0"], "--atr-stop-mult") is None
    # A non-numeric value is NOT silently coerced to a default.
    assert fv(["--atr-stop-mult", "abc"], "--atr-stop-mult") is None


def test_capped_ceiling_reproduces_the_measured_legs():
    """The two ceilings published in the merged memo, recomputed here.

    gld_pullback_1d: stop-mult 2.0, arm 5.06 -> 0.099/(2.0*5.06)  = 0.9783%
    trend_donchian_sol_4h: 2.5, arm 5.57     -> 0.099/(2.5*5.57)  = 0.7110%

    These are the numbers the memo asserts, so a drift in the arithmetic breaks
    a claim that is already published rather than only a test.
    """
    f = _mod().arm_atr_close_ceiling
    state, pct = f(["--tp-cap-pct", "0.099", "--atr-stop-mult", "2.0"], 5.06)
    assert state == "capped"
    assert abs(pct * 100.0 - 0.9783) < 1e-3

    state, pct = f(["--tp-cap-pct", "0.099", "--atr-stop-mult", "2.5"], 5.57)
    assert state == "capped"
    assert abs(pct * 100.0 - 0.7110) < 1e-3


def test_same_family_same_stopmult_can_differ_by_an_order_of_magnitude():
    """The finding in one assertion.

    `trend_donchian` (BTC 1h, ATR/close ~0.333%) and `trend_donchian_sol_4h`
    (SOL 4h, ~2.415%) share a family and an `atr_stop_mult`, and were shipped
    arms 1.16x apart — while their ceilings differ ~7x. A ceiling that did not
    move with the instrument would make the sweep's similar arms look fine.
    """
    f = _mod().arm_atr_close_ceiling
    _, btc = f(["--tp-cap-pct", "0.099", "--atr-stop-mult", "2.5"], 6.49)
    _, sol = f(["--tp-cap-pct", "0.099", "--atr-stop-mult", "2.5"], 5.57)
    # Same stop-mult, arms 1.16x apart -> ceilings only 1.16x apart. The 7.3x
    # gap comes from the INSTRUMENT's ATR/close, which is why the ceiling has to
    # be read against a measured vol, never taken as a per-leg constant.
    assert 1.1 < (sol / btc) < 1.2


def test_uncapped_and_unknown_never_collapse():
    """`uncapped` and `unknown` are opposite statements; both return None.

    Only the STATE distinguishes them, which is the whole point: a run over an
    uncapped book has no ceiling to report (the arm is unbounded), while a
    missing `atr_stop_mult` means we could not look. Reading either as the other
    inverts the conclusion.
    """
    f = _mod().arm_atr_close_ceiling

    # No cap in the base args -> the measured book genuinely has no ceiling.
    # NOTE base_args applies --tp-cap-pct only to LIVE_TP_CAPPED_FAMILIES, so
    # this is read from what ACTUALLY ran, not from the command-line flag.
    assert f(["--atr-stop-mult", "2.5"], 5.57) == ("uncapped", None)
    assert f(["--tp-cap-pct", "0.0", "--atr-stop-mult", "2.5"], 5.57) == (
        "uncapped", None)

    # Capped, but the stop-mult or the arm is unreadable -> we could not look.
    assert f(["--tp-cap-pct", "0.099"], 5.57) == ("unknown", None)
    assert f(["--tp-cap-pct", "0.099", "--atr-stop-mult", "2.5"], None) == (
        "unknown", None)
    assert f(["--tp-cap-pct", "0.099", "--atr-stop-mult", "0"], 5.57) == (
        "unknown", None)

    states = {f(["--atr-stop-mult", "2.5"], 5.57)[0],
              f(["--tp-cap-pct", "0.099"], 5.57)[0],
              f(["--tp-cap-pct", "0.099", "--atr-stop-mult", "2.5"], 5.57)[0]}
    assert states == {"uncapped", "unknown", "capped"}


def test_no_fabricated_number_on_any_non_capped_path():
    """A ceiling is emitted ONLY when one genuinely exists.

    The failure this guards against is a 0.0 or an inf standing in for "not
    applicable" — which would sort as the tightest possible ceiling and make
    every arm look unreachable, or the loosest and make every arm look fine.
    """
    f = _mod().arm_atr_close_ceiling
    for args, arm in (
        (["--atr-stop-mult", "2.5"], 5.57),
        (["--tp-cap-pct", "0.099"], 5.57),
        (["--tp-cap-pct", "0.099", "--atr-stop-mult", "2.5"], 0.0),
        ([], None),
    ):
        state, pct = f(args, arm)
        assert state in ("uncapped", "unknown")
        assert pct is None, f"{args}/{arm} fabricated a ceiling: {pct}"
