"""Self-tests for the `tp_geometry` half of `m20_coverage_rollup.validate`.

WHY THIS FILE EXISTS
--------------------
This guard ships GREEN — measured 2026-08-16, 210 live cells carry no
`tp_geometry` and the recorded ceiling is 210, so the ratchet passes and every
present value is a legend value. **A guard that has never been observed to fail
is not evidence that the property holds**; it is equally consistent with a probe
that cannot fire. Criterion (3) of
`BL-20260814-TP-GEOMETRY-RECORDED-ON-2-PERCENT-OF-CELLS-SO-ABSENCE-CANNOT-MEAN-ANYTHING`
asks for the guard *and* a planted-omission self-test for exactly that reason,
so each check below is exercised against a planted positive and the real,
unmutated matrix is asserted clean by the same instrument.

The bar is set by `new-table-wiring-guard`'s lesson, recorded in `CLAUDE.md`:
a guard that is cheaper to lie to than to satisfy is worse than no guard. Here
the cheap lie would be raising `_unstamped_ceiling` — which is why the ceiling
lives in the matrix beside the legend rather than in this script, so raising it
lands in a diff a reviewer reads.
"""
from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "m20_coverage_rollup", REPO / "scripts" / "research" / "m20_coverage_rollup.py")
m20 = importlib.util.module_from_spec(_spec)
sys.modules["m20_coverage_rollup"] = m20
_spec.loader.exec_module(m20)


@pytest.fixture
def matrix():
    return m20.load(m20.MATRIX)


def _geom_problems(problems):
    """Only this file's concern — not the join or cell-hygiene checks."""
    return [p for p in problems
            if "tp_geometry" in p or "_unstamped_ceiling" in p]


def _first_stamped_cell(matrix):
    for row, col, _status in m20.cells(matrix, live_only=True):
        cell = row.get(col)
        if isinstance(cell, dict) and cell.get("tp_geometry") is not None:
            return row, col
    raise AssertionError(
        "no live cell carries tp_geometry — the planted-omission tests below "
        "would be vacuous, so this is a failure, not a skip")


def test_the_real_matrix_is_clean(matrix):
    """The reading that matters, taken by the same instrument as the planted ones."""
    assert _geom_problems(m20.validate(matrix)) == []


def test_the_legend_defines_exactly_the_two_geometries(matrix):
    """Read from the matrix, never hardcoded — a guard with its own copy drifts."""
    assert m20.tp_geometry_legend_values(matrix) == {"live_parity", "no_take_profit"}


def test_an_invented_geometry_value_is_flagged(matrix):
    """A value outside the legend cannot be graded, but wears a graded value's shape."""
    m = copy.deepcopy(matrix)
    row, col = _first_stamped_cell(m)
    row[col]["tp_geometry"] = "live_parity_probably"
    problems = _geom_problems(m20.validate(m))
    assert any("is not a defined value" in p for p in problems), problems


def test_un_stamping_a_cell_breaches_the_ratchet(matrix):
    """THE PLANTED OMISSION. Removing one stamp must fail the guard.

    This is the exact regression the ratchet exists for: a cell that loses (or
    never had) its geometry while the file still looks well-formed.
    """
    m = copy.deepcopy(matrix)
    row, col = _first_stamped_cell(m)
    del row[col]["tp_geometry"]
    problems = _geom_problems(m20.validate(m))
    assert any("above the recorded ceiling" in p for p in problems), problems


def test_a_null_stamp_is_treated_as_absent_not_as_a_value(matrix):
    """`None` is absence wearing a value's shape — it must not pass as stamped."""
    m = copy.deepcopy(matrix)
    row, col = _first_stamped_cell(m)
    row[col]["tp_geometry"] = None
    problems = _geom_problems(m20.validate(m))
    assert any("above the recorded ceiling" in p for p in problems), problems


def _stamp_one_unstamped_live_cell(m) -> None:
    """Stamp exactly one unstamped live cell, or fail loudly.

    The `else` is the point: a mutation helper that silently mutates nothing
    turns every test built on it into a vacuous pass.
    """
    for row, col, _status in m20.cells(m, live_only=True):
        cell = row.get(col)
        if isinstance(cell, dict) and cell.get("tp_geometry") is None:
            cell["tp_geometry"] = "live_parity"
            return
    raise AssertionError("no unstamped live cell to stamp — test is vacuous")


def test_stamping_a_cell_WITHOUT_lowering_the_ceiling_is_a_named_slack_failure(matrix):
    """⚠️ THIS TEST WAS INVERTED ON 2026-08-25, DELIBERATELY.

    It used to assert `stamping a cell does NOT fail the ratchet`, on the
    reasoning that the ratchet 'must not punish progress'. That reasoning is
    right and the assertion was wrong, because the ratchet was ONE-SIDED
    (`unstamped > ceiling`): stamping dropped the count below the ceiling and
    left SLACK, and inside that slack a live cell can lose its stamp with the
    guard still green. The file's own 2026-08-17 note records exactly this —
    two cells stamped without lowering the ceiling, 'the probe going quiet,
    exactly the failure mode that file exists to prevent'.

    So progress is still not punished — see the next test, where stamping AND
    lowering passes. What is now refused is progress that is not RECORDED.
    """
    m = copy.deepcopy(matrix)
    _stamp_one_unstamped_live_cell(m)
    problems = _geom_problems(m20.validate(m))
    assert any("SLACK" in p for p in problems), problems


def test_stamping_a_cell_AND_lowering_the_ceiling_passes(matrix):
    """The original intent, preserved: the ratchet must not punish progress.

    Without this the guard would be satisfiable only by never touching the
    file. The cost of stamping is one number in the same diff, which is the
    mechanism — a reviewer sees the ratchet tighten.
    """
    m = copy.deepcopy(matrix)
    _stamp_one_unstamped_live_cell(m)
    m["tp_geometry_legend"]["_unstamped_ceiling"] -= 1
    assert _geom_problems(m20.validate(m)) == []


def test_demoting_a_leg_shrinks_the_population_and_is_a_named_slack_failure(matrix):
    """THE POPULATION-SHRINK PATH, which no test covered until now.

    Criterion (2) of BL-20260824-DEMOTING-A-LEG-SILENTLY-LOOSENS-THE-M20-RATCHET
    is explicit that the existing tests caught the stamp-removal path only
    'because the ceiling happened to be exactly tight' — a coincidence, not a
    property. This plants the shrink itself: flip a live row to `shadow`, which
    is exactly what a Tier-3 demote does, so its cells leave the `live_only`
    denominator.

    Measured instances this reproduces: eth_pullback_prop_2h (2026-08-23, live
    423 -> 414, 2 cells of slack) and slv_trend_1h (2026-08-24, 5 cells of
    slack). Both were found by hand; neither was caught by this file.
    """
    m = copy.deepcopy(matrix)
    for row in m["rows"]:
        if row.get("execution") != "live":
            continue
        if any(isinstance(row.get(c), dict) and row[c].get("tp_geometry") is None
               for c in m["lever_columns"]):
            row["execution"] = "shadow"        # the demote
            break
    else:
        raise AssertionError(
            "no live row carries an unstamped cell — the plant would not move "
            "the count, so this test would pass vacuously")
    problems = _geom_problems(m20.validate(m))
    assert any("SLACK" in p for p in problems), problems
    # and the message must say WHY the reviewer is seeing it, not just that a
    # number moved — a demote and a stamping run need different remedies.
    assert any("population shrank" in p for p in problems), problems


def test_a_missing_ceiling_reads_as_UNCHECKED_not_as_clean(matrix):
    """'We could not evaluate the ratchet' is not 'the ratchet passed'."""
    m = copy.deepcopy(matrix)
    m["tp_geometry_legend"].pop("_unstamped_ceiling", None)
    problems = _geom_problems(m20.validate(m))
    assert any("cannot be evaluated" in p for p in problems), problems


def test_an_unreadable_legend_reads_as_UNCHECKED_not_as_clean(matrix):
    """The same collapsed-state rule one level up: no legend, no verdict.

    Silently skipping would report a clean matrix on a file where nothing was
    validated — the unasserted-denominator shape this repo guards for.
    """
    m = copy.deepcopy(matrix)
    m.pop("tp_geometry_legend", None)
    problems = _geom_problems(m20.validate(m))
    assert any("NOT validated" in p for p in problems), problems
