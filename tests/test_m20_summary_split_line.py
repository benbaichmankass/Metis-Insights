"""The sweep must report the boundary it CUT AT, not the one it was asked for.

Under the default `--split-mode oos-trades` the IS/OOS boundary is resolved
PER LEG by `resolve_split()`, and `--split` is only the fallback date. The PR
comment banner nonetheless printed ``IS/OOS split `<--split>` `` unqualified,
so every leg's comment asserted one shared calendar cut.

Measured on the 2026-08-15 pullback re-sweep (17 legs, corpus rows in
`docs/research/m20-sweep-corpus.jsonl`): every comment said
``IS/OOS split `2025-07-01` `` and SIXTEEN legs had actually run at a
different derived boundary --

    sol_pullback_2h  2025-08-23      slv_pullback_1d  2022-11-29
    ief_pullback_1d  2017-01-20      gdx_pullback_1d  2022-12-06   ...

-- while the single leg that genuinely used 2025-07-01, `iaum_pullback_1d`,
did so only because its derivation could not be satisfied. The one true
reading was true by FAILURE. A reader comparing two legs' cells on the stated
assumption of a common split is comparing different partitions.

That is `diagnostic-provenance-guard` sub-class A (the label names a quantity
the code did not emit), on the same banner whose `geometry` line was added to
kill the identical defect five days earlier.

What is pinned here is not the wording. It is that the line CAN DISCRIMINATE
the three states a boundary can be in -- and, in particular, the two that a
naive renderer collapses:

  1. **A fallback is never dressed as a derivation.** The fixed date was USED
     but not CHOSEN, and a reader who cannot tell will blame the leg for the
     fallback's window.
  2. **An unresolved boundary is never filled in with the request.** A leg that
     never reached `resolve_split` has no boundary; printing the requested date
     manufactures exactly the claim this function exists to stop.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "m20_fleet_exit_sweep_split_line",
        REPO / "scripts/research/m20_fleet_exit_sweep.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["m20_fleet_exit_sweep_split_line"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


SWEEP = _load()
LINE = SWEEP.summary_split_line

# Verbatim shapes from the 2026-08-15 run.
DERIVED = {"split": "2025-08-23", "split_mode": "oos-trades",
           "split_target_oos": 35, "split_lifetime_trades": 224}
# iaum_pullback_1d -- the leg whose derivation could not be satisfied and so
# landed on the requested date by falling back to it.
FELL_BACK = {"split": "2025-07-01", "split_mode": "oos-trades",
             "split_target_oos": 35, "split_lifetime_trades": 35,
             "split_fallback": "lifetime below target"}
FIXED = {"split": "2025-07-01", "split_mode": "date"}
NEVER_RAN: dict = {"status": "harness_error"}


# ------------------------------------------------- the three states are apart

def test_a_derived_boundary_is_reported_as_the_one_actually_cut() -> None:
    msg = LINE("sol_pullback_2h", DERIVED)
    assert "2025-08-23" in msg, msg
    assert "DERIVED" in msg


def test_a_fallback_is_never_dressed_as_a_derivation() -> None:
    """The defect's sharpest edge: same DATE, opposite provenance.

    `iaum_pullback_1d` really did run at 2025-07-01, so a renderer that only
    printed the date would look CORRECT on this leg while saying nothing about
    why -- and the banner's shared-split claim would appear vindicated by the
    one leg that proves the derivation broke.
    """
    fell, derived = LINE("iaum_pullback_1d", FELL_BACK), LINE("x", DERIVED)
    assert "FELL BACK" in fell, fell
    assert "NOT chosen" in fell
    assert "DERIVED" not in fell, "a fallback rendered as a derivation"
    assert fell != derived


def test_an_unresolved_boundary_is_not_filled_in_with_the_request() -> None:
    msg = LINE("mes_trend_long_1d", NEVER_RAN)
    assert "unknown" in msg
    assert "2025-07-01" not in msg, f"fabricated a boundary from the request: {msg}"


def test_a_fixed_split_says_every_leg_shares_it() -> None:
    """`--split-mode date` is the ONE case where a shared cut is a true claim."""
    msg = LINE("spy_pullback_1h", FIXED)
    assert "2025-07-01" in msg and "every leg" in msg
    assert "DERIVED" not in msg


# --------------------------------------------- the claim the banner got wrong

def test_a_derived_line_warns_AGAINST_cross_leg_comparison() -> None:
    """The banner's implicit promise was that legs share a boundary.

    Since they do not, the line has to say so -- otherwise the reader keeps the
    old assumption and the new date just looks like a detail.
    """
    msg = LINE("sol_pullback_2h", DERIVED)
    assert "do NOT share a boundary" in msg, msg


def test_the_target_is_not_presented_as_the_achieved_count() -> None:
    """35 was TARGETED; the achieved OOS n is a different number.

    Sibling of `insufficient_base_reason`'s own rule -- a target printed bare
    reads as a measurement.
    """
    msg = LINE("sol_pullback_2h", DERIVED)
    assert "targeting 35" in msg
    assert "ACHIEVED" in msg and "base n OOS" in msg


def test_two_legs_of_one_run_render_distinguishable_boundaries() -> None:
    """The whole point, end to end: the 16-of-17 case must be visible."""
    a = LINE("sol_pullback_2h", DERIVED)
    b = LINE("slv_pullback_1d", {**DERIVED, "split": "2022-11-29"})
    assert a != b
    assert "2025-08-23" in a and "2022-11-29" in b


# ---------------------------------------------------------- the wiring itself

def test_the_summary_block_calls_this_function() -> None:
    """A pure function nothing calls would be provenance theatre.

    The SUMMARY block lives inside `main()` and is not otherwise reachable, so
    this is the join that keeps the tested string and the emitted string the
    same one. Mirrors `test_the_wired_verdict_block_calls_this_function`.
    """
    src = (REPO / "scripts/research/m20_fleet_exit_sweep.py").read_text()
    assert "lines.append(summary_split_line(_leg, _v))" in src


def test_the_workflow_banner_no_longer_asserts_a_bare_boundary() -> None:
    """The other half of the fix, pinned so it cannot silently regress.

    The banner may state the REQUEST; it may not state a boundary it does not
    know. Asserted on the emitted body expression, not on prose about it.

    Note what is NOT asserted: that the bare ``IS/OOS split `<SPLIT>` `` form
    is absent from the file. It survives, correctly, inside the `split_mode
    date` branch -- that is the one mode where the requested value IS the
    boundary for every leg. A test banning the substring outright would be
    demanding the code state less than it knows, and the first draft of this
    test did exactly that and failed against correct code.
    """
    wf = (REPO / ".github/workflows/m20-exit-lever-sweep.yml").read_text()
    body = next(ln for ln in wf.splitlines() if "body: `### Exit-lever sweep" in ln)
    assert "${splitTxt}" in body, body
    assert "process.env.SPLIT" not in body, (
        "the body is asserting the requested date as the boundary again")
    # ...and the qualified branch is the DEFAULT one, not a dead path.
    assert "FALLBACK date" in wf
    assert "SPLIT_MODE: ${{ inputs.split_mode || 'oos-trades' }}" in wf
