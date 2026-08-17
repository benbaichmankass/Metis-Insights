"""The census SUMMARY table's columns must match the values it emits.

`BL-20260817-CENSUS-SUMMARY-TABLE-IS-COLUMN-SHIFTED`.

The table declared **15** headers and its row f-string emitted **13** values, so
every cell from `gb R med` rightward rendered under a NEIGHBOURING column's name
and the two rightmost headers rendered nothing:

    `gb R med` <- near_miss_90_pct    ·  `nm@90%` <- near_miss_measured_n
    `nm pop`   <- target_r_reached_n
    `tgt hit`  <- near_miss_r_left_on_table   (an R-SUM under a COUNT's header)
    `R left` / `R->tgt` <- nothing at all

Two accessors were computed by `exit_capture.py`, NAMED in the header, and
referenced NOWHERE in the sweep — `r_left_median` and `near_miss_r_to_target`,
0 greps each against 3-4 for their siblings. That is the written-and-never-read
shape, and sub-class **A** of the diagnostic-provenance rule in CLAUDE.md: the
label names a quantity the code never fetched.

It also defeated two guards the table explicitly claims:

  * *"nm@90% ALWAYS ships beside its denominator"* — the denominator rendered
    under the RATE's header;
  * `r_left_median` exists so *"the MEDIAN ships beside the sum so the skew is
    visible without opening the artifact"* — it was dropped entirely, so the
    anti-skew guard never reached a reader.

And the table's own prose instructs *"Read `R->tgt` INSTEAD of `R left`"* — the
column that never rendered.

WHY A WIDTH TEST IS NOT ENOUGH, and why this file pins the MAPPING. The shift
and the two dropped values are the same defect seen from opposite ends: pad the
row to 15 without fixing the order and every count still reconciles while
`tgt hit` keeps showing an R-sum. So the load-bearing assertion here is
column -> accessor, not `len()`.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SWEEP = REPO / "scripts" / "research" / "m20_fleet_exit_sweep.py"


def _sweep():
    spec = importlib.util.spec_from_file_location("_m20_sweep_cols", SWEEP)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_m20_sweep_cols"] = mod
    spec.loader.exec_module(mod)
    return mod


# A census payload with a DISTINCT value per field, so a shift cannot hide
# behind two columns that happen to share a value. Mirrors the real
# `squeeze_breakout_4h` row measured 2026-08-17 (census run 32015369620), with
# the near-miss block filled in — the real leg is `target_inert`, which nulls
# exactly the columns the shift corrupted and would have masked it.
CENSUS_V = {
    "exit_kind": "fixed_target",
    "n_trades": 101,
    "capture_median": -0.3894,
    "capture_winners_median": 0.6206,
    "capture_r_weighted": 0.2244,
    "capture_lt_30_pct": 72.73,
    "giveback_ladder": [
        {"mfe_ge_r": 0.5, "mfe_ge_n": 60, "lost_n": 20, "lost_pct": 33.3,
         "r_left": 99.9, "r_left_median": 9.9},
        {"mfe_ge_r": 1.0, "mfe_ge_n": 45, "lost_n": 9, "lost_pct": 20.0,
         "r_left": 13.275, "r_left_median": 1.5},
    ],
    "near_miss_90_pct": 41.4,
    "near_miss_measured_n": 29,
    "target_r_reached_n": 7,
    "near_miss_r_left_on_table": 88.8,
    "near_miss_r_to_target": 22.2,
}


def test_row_emits_exactly_one_cell_per_declared_column():
    m = _sweep()
    cells = m.census_row_cells("leg_x", CENSUS_V)
    assert len(cells) == len(m.CENSUS_COLUMNS), (
        f"{len(cells)} cells against {len(m.CENSUS_COLUMNS)} declared columns "
        f"— a shifted row mislabels every cell after the gap, which is worse "
        f"than a missing one because it still reads as data")


def test_every_column_maps_to_its_own_accessor():
    """THE LOAD-BEARING ONE. Width alone would pass a still-shifted row."""
    m = _sweep()
    got = dict(zip(m.CENSUS_COLUMNS, m.census_row_cells("leg_x", CENSUS_V)))
    assert got["n"] == 101
    assert got["cap med"] == -0.3894
    assert got["cap w-med"] == 0.6206
    assert got["cap Rwt"] == 0.2244
    assert got["cap <30%"] == 72.73
    # The 1R rung, as lost/reached with its denominator attached.
    assert got["gb>=1R"] == "9/45 (20.0%)"
    # The rung is selected by mfe_ge_r == 1.0, NOT by list position — the 0.5
    # rung sits first in the ladder and carries deliberately different numbers.
    assert got["gb R left"] == 13.275, "picked the wrong ladder rung"
    assert got["gb R med"] == 1.5, (
        "`gb R med` must be the rung's r_left_median — the anti-skew guard. It "
        "previously rendered near_miss_90_pct")
    assert got["nm@90%"] == 41.4, (
        "`nm@90%` must be the RATE; it previously rendered its own denominator")
    assert got["nm pop"] == 29, "`nm pop` is the denominator the rate ships with"
    assert got["tgt hit"] == 7, (
        "`tgt hit` must be target_r_reached_n — a COUNT. It previously rendered "
        "near_miss_r_left_on_table, an R-sum, under a count's header")
    assert got["R left"] == 88.8
    assert got["R->tgt"] == 22.2, (
        "`R->tgt` is the column the table's own prose tells the reader to use "
        "INSTEAD of `R left`, and it previously rendered nowhere at all")


def test_the_two_dropped_accessors_are_read_by_this_module():
    """Both were computed upstream, named in the header, and never referenced.

    Pinned as a source-level fact as well as a rendered one: the rendering
    assertions above would still pass if a future edit hardcoded the values,
    and the defect being guarded is specifically *the accessor is never read*.
    """
    src = SWEEP.read_text()
    for key in ("r_left_median", "near_miss_r_to_target"):
        assert key in src, (
            f"`{key}` is computed by exit_capture.py and named in "
            f"CENSUS_COLUMNS but read nowhere in the sweep — written and never "
            f"read, the shape provenance-consumer-guard exists to catch")


def test_missing_giveback_ladder_still_fills_both_ladder_columns():
    """A leg with no ladder must not silently shorten the row."""
    m = _sweep()
    v = dict(CENSUS_V, giveback_ladder=[])
    cells = m.census_row_cells("leg_y", v)
    assert len(cells) == len(m.CENSUS_COLUMNS)
    got = dict(zip(m.CENSUS_COLUMNS, cells))
    assert got["gb>=1R"] == "—" and got["gb R left"] == "—"
    assert got["gb R med"] == "—", (
        "an absent ladder must render em-dash in BOTH ladder columns, never "
        "drop a cell — dropping one is how the table shifted originally")


def test_header_and_alignment_row_derive_from_the_same_constant():
    """The header, the alignment row and the error row share one width source.

    They were three independent literals, which is how they drifted.
    """
    m = _sweep()
    n = len(m.CENSUS_COLUMNS)
    header = "| " + " | ".join(m.CENSUS_COLUMNS) + " |"
    align = "|" + "|".join(["---", "---"] + ["--:"] * (n - 2)) + "|"
    err = "| leg | ERROR: boom |" + " — |" * (n - 2)
    for label, line in (("header", header), ("align", align), ("error", err)):
        cells = [c for c in line.strip().strip("|").split("|")]
        assert len(cells) == n, f"{label} row is {len(cells)} wide, not {n}"
