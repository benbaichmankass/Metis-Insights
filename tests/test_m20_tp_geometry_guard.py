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


def test_stamping_a_cell_does_NOT_fail_the_ratchet(matrix):
    """The ratchet must not punish progress — only regression.

    Without this, the guard would be satisfiable only by never touching the
    file, and the first person to stamp a cell would be told they broke it.
    """
    m = copy.deepcopy(matrix)
    for row, col, _status in m20.cells(m, live_only=True):
        cell = row.get(col)
        if isinstance(cell, dict) and cell.get("tp_geometry") is None:
            cell["tp_geometry"] = "live_parity"
            break
    else:
        raise AssertionError("no unstamped live cell to stamp — test is vacuous")
    assert _geom_problems(m20.validate(m)) == []


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
