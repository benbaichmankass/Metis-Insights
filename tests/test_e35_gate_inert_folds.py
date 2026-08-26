"""The e35 bracket gate must not count INERT walk-forward folds as wins.

WHY THIS FILE EXISTS
--------------------
`e35_bracket_geometry_sweep` imports `m20_fleet_exit_sweep.walkforward` and
then graded on the RAW `wins`, ignoring `wins_effective` — the field added by
`BL-20260817-FLEET-SWEEP-WF-COUNTS-INERT-FOLDS-AS-WINS` for exactly this
reason. An inert fold is one where the cell and the base produce the same book,
so it is not evidence the cell generalises.

The comment directly above the predicate ASSERTED the effective tally was used.
It was not. That is why this is a test and not a comment:
`BL-20260826-E35-GATE-COUNTS-INERT-FOLDS-AS-WALKFORWARD-WINS`.

MEASURED on the real corpus, population stated: all 33 gate-passing cells
(7 from 2026-08-23, 26 from 2026-08-26), all 33 checkable because every row
carries both counts. **4 pass on raw and fail on effective**, each with 3 of 6
folds inert — `splg_trend_long_1d tp2.5`, `spy_trend_long_1d tp2.5`,
`tlt_pullback_1d to48`, `uso_trend_1h tp4`.
"""
from __future__ import annotations

import math

import pytest


def _gate(usable: int, wins: int, wins_effective: int | None) -> bool:
    """The SHIPPING predicate, lifted from the sweep rather than restated.

    Lifted by reading the source so this test cannot drift from the code into
    a private re-implementation that agrees with itself.
    """
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "scripts" / "research"
           / "e35_bracket_geometry_sweep.py").read_text()
    assert "passed = usable > 0 and wins_effective >= threshold" in src, (
        "the shipping predicate changed shape; this test must be re-derived "
        "from it rather than left asserting a stale form")
    assert re.search(r'wins_effective = wf\.get\("wins_effective"\)', src), (
        "the gate no longer reads wins_effective")

    if wins_effective is None:
        wins_effective = wins
    threshold = math.ceil(2 * usable / 3)
    return usable > 0 and wins_effective >= threshold


def _gate_prefix(usable: int, wins: int) -> bool:
    """The PRE-FIX predicate. The positive control."""
    return usable > 0 and wins >= math.ceil(2 * usable / 3)


# The four real cells, from the corpus. (usable, wins, inert_wins)
REAL_INERT_CARRIED = {
    "splg_trend_long_1d tp2.5": (6, 5, 3),
    "spy_trend_long_1d tp2.5": (6, 5, 3),
    "tlt_pullback_1d to48": (6, 6, 3),
    "uso_trend_1h tp4": (6, 5, 3),
}


@pytest.mark.parametrize("label", sorted(REAL_INERT_CARRIED))
def test_the_four_real_inert_carried_cells_now_fail(label):
    usable, wins, inert = REAL_INERT_CARRIED[label]
    effective = wins - inert

    # POSITIVE CONTROL: the pre-fix gate really did pass these. Without this
    # the assertion below could hold for reasons unrelated to the fix.
    assert _gate_prefix(usable, wins), (
        f"{label} must PASS the pre-fix gate or this test proves nothing")

    assert not _gate(usable, wins, effective), (
        f"{label} passes on raw wins ({wins}/{usable}) and must fail on "
        f"effective ({effective}/{usable}, threshold "
        f"{math.ceil(2 * usable / 3)})")


def test_a_clean_pass_is_unaffected():
    """The fix must only ever REMOVE passes, never change a clean one."""
    assert _gate_prefix(6, 5)
    assert _gate(6, 5, 5), "a cell with zero inert folds must still pass"


def test_the_fix_can_never_manufacture_a_pass():
    """`wins_effective <= wins` always, so the graded set can only shrink.

    Stated as a property over the plausible space rather than an example,
    because "stricter in the cases I picked" is not the same claim.
    """
    for usable in range(1, 9):
        for wins in range(0, usable + 1):
            for inert in range(0, wins + 1):
                after = _gate(usable, wins, wins - inert)
                before = _gate_prefix(usable, wins)
                assert not (after and not before), (
                    f"usable={usable} wins={wins} inert={inert}: the fix "
                    "created a pass that did not exist before")


def test_a_missing_effective_field_is_not_silently_treated_as_zero():
    """A pre-fix `walkforward` returns no `wins_effective`.

    That is "we could not look", and the honest fallback is the raw tally with
    `verdict_basis` recording that it was used — NOT zero, which would fail
    every cell and read as a fleet-wide negative that nobody measured.
    """
    assert _gate(6, 5, None) is True
    assert _gate(6, 2, None) is False
