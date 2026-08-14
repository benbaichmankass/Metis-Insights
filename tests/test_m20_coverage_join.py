"""Self-tests for the config<->matrix JOIN half of `m20_coverage_rollup.validate`.

WHY THIS FILE EXISTS
--------------------
The join check ships GREEN: measured 2026-08-13, config declares 45 live
harness-classified legs and the matrix carries 45 live rows, with the
set-difference empty in both directions. A guard that has never been observed
to fail is not evidence that the property holds — it is equally consistent with
a probe that cannot fire. So each check below is exercised against a PLANTED
positive, and the real, unmutated matrix is asserted clean at the end so the
two readings are taken by the same instrument.

The direction under test is the one that was missing until 2026-08-13
(`BL-20260810-COVERAGE-MATRIX-LEG-IDS-DO-NOT-JOIN-TO-CONFIG`): matrix -> config
was enforced, config -> matrix was not. Only the second can catch a live leg
that is absent from the M20 denominator entirely — the error that makes a
coverage headline read 100% over an under-counted population.
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


def _join_problems(problems):
    """Only the problems this file is about — the join half, not cell hygiene."""
    markers = (
        "NO matrix row", "matrix rows for ONE leg", "denominator and the runtime disagree",
        "was NOT checked",
    )
    return [p for p in problems if any(mk in p for mk in markers)]


def test_real_matrix_join_is_clean(matrix):
    """The convergence actually holds today — the baseline the plants move off."""
    assert _join_problems(m20.validate(matrix)) == []


def test_live_leg_with_no_matrix_row_is_flagged(matrix, monkeypatch):
    """The error the missing direction could not see: an under-counted denominator."""
    monkeypatch.setattr(m20, "_declared_legs", lambda: {
        **{r["strategy"]: {"execution": r.get("execution", "live")} for r in matrix["rows"]},
        "planted_ghost_leg_1h": {"execution": "live"},
    })
    monkeypatch.setattr(m20, "_family_of", lambda name: "donchian")
    problems = _join_problems(m20.validate(matrix))
    assert any("planted_ghost_leg_1h" in p and "NO matrix row" in p for p in problems), problems


def test_shadow_leg_with_no_matrix_row_is_NOT_flagged(matrix, monkeypatch):
    """A non-live leg is correctly outside the denominator — no false positive.

    This is the companion to the test above: a check that fires on everything is
    as useless as one that fires on nothing.
    """
    monkeypatch.setattr(m20, "_declared_legs", lambda: {
        **{r["strategy"]: {"execution": r.get("execution", "live")} for r in matrix["rows"]},
        "planted_shadow_leg_1h": {"execution": "shadow"},
    })
    monkeypatch.setattr(m20, "_family_of", lambda name: "donchian")
    assert not any("planted_shadow_leg_1h" in p for p in _join_problems(m20.validate(matrix)))


def test_omitted_execution_counts_as_live(matrix, monkeypatch):
    """Default-permissive: a leg that never says `execution` IS live.

    Reading an omitted `execution` as anything else would exempt exactly the
    legs the guard exists to police (the two-gates rule).
    """
    monkeypatch.setattr(m20, "_declared_legs", lambda: {
        **{r["strategy"]: {"execution": r.get("execution", "live")} for r in matrix["rows"]},
        "planted_implicit_live_leg": {},  # no `execution` key at all
    })
    monkeypatch.setattr(m20, "_family_of", lambda name: "donchian")
    problems = _join_problems(m20.validate(matrix))
    assert any("planted_implicit_live_leg" in p and "NO matrix row" in p for p in problems), problems


def test_duplicate_rows_for_one_leg_are_flagged(matrix):
    """Two rows for one leg = two statuses for one leg, reader gets whichever."""
    mutated = copy.deepcopy(matrix)
    live = next(r for r in mutated["rows"] if r.get("execution") == "live")
    mutated["rows"].append(copy.deepcopy(live))
    problems = _join_problems(m20.validate(mutated))
    assert any("matrix rows for ONE leg" in p and live["strategy"] in p for p in problems), problems


def test_execution_disagreement_is_flagged(matrix, monkeypatch):
    """A row marked shadow for a live leg drops it out of the denominator too."""
    live = next(r for r in matrix["rows"] if r.get("execution") == "live")
    monkeypatch.setattr(m20, "_declared_legs", lambda: {
        r["strategy"]: {"execution": ("shadow" if r is live else r.get("execution", "live"))}
        for r in matrix["rows"]
    })
    monkeypatch.setattr(m20, "_family_of", lambda name: "donchian")
    problems = _join_problems(m20.validate(matrix))
    assert any("disagree about this leg" in p and live["strategy"] in p for p in problems), problems


def test_unreadable_config_reports_unchecked_not_clean(matrix, monkeypatch):
    """An unreadable config must never read as a clean pass."""
    monkeypatch.setattr(m20, "_declared_legs", lambda: None)
    problems = _join_problems(m20.validate(matrix))
    assert any("was NOT checked" in p for p in problems), problems


def test_absent_classifier_reports_unchecked_not_clean(matrix, monkeypatch):
    """Same third state for the family classifier: absent != nothing to find."""
    monkeypatch.setattr(m20, "_declared_legs", lambda: {
        r["strategy"]: {"execution": r.get("execution", "live")} for r in matrix["rows"]})
    monkeypatch.setattr(m20, "_family_of", lambda name: None)
    problems = _join_problems(m20.validate(matrix))
    assert any("NO leg was family-classified" in p.replace("\n", " ") or "was NOT checked" in p
               for p in problems), problems


# ── bare `blocked` is a collapsed state (2026-08-13) ──────────────────────────
def _bare_blocked_problems(problems):
    return [p for p in problems if "bare 'blocked'" in p]


def test_no_live_cell_carries_a_bare_blocked(matrix):
    """The real matrix states a reason on every blocked cell.

    This is the baseline the plant below moves off. It went green only after
    `mes_trend_long_1d` `vol_trail` was given its `:insufficient_base` suffix —
    it was the one cell whose status was silent about a cause its own `ref`
    described in full.
    """
    assert _bare_blocked_problems(m20.validate(matrix)) == []


def test_a_planted_bare_blocked_IS_flagged(matrix):
    """A guard that cannot fail proves nothing.

    Without this, `test_no_live_cell_carries_a_bare_blocked` would pass just as
    happily if the check were deleted — a clean result over an inert probe,
    which is the sub-class C shape this repo keeps re-learning.
    """
    m = copy.deepcopy(matrix)
    row = next(r for r in m["rows"] if r.get("execution") == "live")
    row["vol_trail"] = {"status": "blocked", "ref": "planted — cause deliberately unstated"}

    problems = _bare_blocked_problems(m20.validate(m))
    assert any(row["strategy"] in p for p in problems), problems


def test_a_reasoned_blocked_is_NOT_flagged(matrix):
    """The negative control: the check must object to SILENCE, not to blocking.

    Without this pair, a check that flagged every `blocked` cell — reasoned or
    not — would still pass the plant above while making the status field
    useless.
    """
    m = copy.deepcopy(matrix)
    row = next(r for r in m["rows"] if r.get("execution") == "live")
    row["vol_trail"] = {"status": "blocked:planted_novel_reason",
                        "ref": "planted — cause stated"}

    assert _bare_blocked_problems(m20.validate(m)) == []


def test_a_novel_reason_is_accepted(matrix):
    """Reasons are open-vocabulary on purpose.

    Three of the six reasons in use were coined this week. A closed list would
    push the next genuine discovery toward whichever existing label fits worst,
    which is how a taxonomy starts lying. `planted_novel_reason` above is not in
    any legend and must still pass — asserted separately so the intent survives
    someone later "tightening" the check to a fixed set.
    """
    m = copy.deepcopy(matrix)
    row = next(r for r in m["rows"] if r.get("execution") == "live")
    row["vol_trail"] = {"status": "blocked:no_such_reason_exists_anywhere",
                        "ref": "planted"}

    assert _bare_blocked_problems(m20.validate(m)) == []


def test_geometry_coverage_ships_its_denominator_and_counts_unrecorded():
    """`tp_geometry` coverage must be a FRACTION, not a list of marked cells.

    The field exists for one job: BL-20260810-BACKTEST-DOES-NOT-MODEL-THE-LIVE-CAPPED-TP
    established that every pre-2026-08-10 verdict was measured on a book with no
    take-profit, and `tp_geometry` says which geometry a verdict rests on.
    Measured 2026-08-14 it is set on 10 of 416 cells, so ABSENCE covers three
    conditions at once -- live-parity-but-unstamped, pre-cutover, and nobody
    looked -- and the reassuring reading is wrong for nearly the whole
    population.

    So `unrecorded` is COUNTED, never omitted: a reader must not be able to
    infer completeness from the marked cells, which is exactly what a bare list
    invites. Same discipline as `rCoverage`/`pnlCoverage` on `/performance`.
    """
    matrix = {
        "lever_columns": ["stale_stop", "trail_decay"],
        "rows": [
            {"strategy": "a", "symbol": "A", "tf": "1h", "execution": "live",
             "stale_stop": {"status": "shipped", "tp_geometry": "live_parity"},
             "trail_decay": {"status": "honest_negative"}},
            {"strategy": "b", "symbol": "B", "tf": "1h", "execution": "live",
             "stale_stop": {"status": "honest_negative",
                            "tp_geometry": "no_take_profit"},
             "trail_decay": {"status": None}},
        ],
    }
    g = m20.geometry_coverage(matrix)

    # Three cells carry a status; the None-status cell is not in the population.
    assert g["total_cells"] == 3
    assert g["recorded"] == 2
    assert g["unrecorded"] == 1
    assert g["recorded_pct"] == round(100 * 2 / 3, 1)
    assert g["by_value"] == {"live_parity": 1, "no_take_profit": 1,
                             "unrecorded": 1}


def test_geometry_coverage_uses_the_headline_population_not_all_rows():
    """A figure over a different denominator than the headline is how the
    304/311/319 divergence started. Shadow rows are excluded, same as `cells()`.
    """
    matrix = {
        "lever_columns": ["stale_stop"],
        "rows": [
            {"strategy": "live_leg", "symbol": "A", "tf": "1h",
             "execution": "live",
             "stale_stop": {"status": "shipped", "tp_geometry": "live_parity"}},
            {"strategy": "shadow_leg", "symbol": "B", "tf": "1h",
             "execution": "shadow",
             "stale_stop": {"status": "honest_negative"}},
        ],
    }
    g = m20.geometry_coverage(matrix)
    assert g["total_cells"] == 1, "the shadow row must not enter the denominator"
    assert g["recorded_pct"] == 100.0
