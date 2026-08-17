"""A cell row says which of its OOS years the walk-forward panel never examined.

`BL-20260817-WF-FOLD-PANEL-IS-A-FIXED-CALENDAR-NOT-AN-OOS-WALKFORWARD`.

`m20_fleet_exit_sweep.FOLDS` is a module literal of six calendar years and
`walkforward()` iterates it WITHOUT reading the cell's own `split`, so
`wf_summary` is a fixed-calendar robustness panel rather than an out-of-sample
walk-forward — and a delta originating in a year no fold examined reads as
walk-forward-confirmed. 5 of the 133 fold-carrying corpus rows have uncovered
OOS years and **all five PASSED**.

WHAT EACH TEST IS FOR, since three of them pin failure modes rather than the
happy path:

1. `test_absent_split_does_not_claim_coverage` — `[]` and `None` are different
   answers. `[]` = the panel covers the whole OOS span; `None` = we could not
   look. A `[]` standing in for the second asserts coverage never checked.
2. `test_the_producer_reads_the_sweeps_own_fold_key` — the sweep's key is
   `walkforward_folds`; the CORPUS name `wf_folds` is assigned in the same dict
   literal. Reading the corpus name at the producer returns None for every row
   and the coverage fields come back silently all-None — a write-and-never-read
   defect of exactly the class this field exists to expose. This was a real
   mistake made while writing it, caught by reading the adjacent line.
3. `test_years_above_the_last_fold_are_not_reported_uncovered` — the sweep's
   final fold carries `end=None` (open-ended), so a label alone cannot show a
   later year is uncovered. Guessing would fabricate a gap.

⚠️ WHAT THIS FIELD DOES NOT MEASURE, pinned in
`test_docstring_disclaims_the_per_year_decomposition`: it reports the years no
fold EXAMINED, and does NOT decompose `d_net_r_OOS` per year. On `splg` every
covered fold is exactly 0.0, so the supported reading is that the whole OOS gain
sits in 2019-2020 — but that is a READING, not a measurement, and the field must
never be quoted as settling it.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / "docs" / "research" / "m20-sweep-corpus.jsonl"
PRODUCER = REPO / "scripts" / "research" / "m20_corpus_extract.py"

PANEL = [{"fold": str(y)} for y in (2021, 2022, 2023, 2024, 2025, 2026)]

# Measured 2026-08-17 over the committed corpus.
EXPECTED_UNCOVERED = {
    ("ief_pullback_1d", "vt_hot80_t2.5"): [2017, 2018, 2019, 2020],
    ("ief_pullback_1d", "decay_stall10_t2.5"): [2017, 2018, 2019, 2020],
    ("tlt_pullback_1d", "decay_stall6_t2.5"): [2019, 2020],
    ("tlt_pullback_1d", "decay_arm1.5R_stall6_t2.5"): [2019, 2020],
    ("splg_trend_long_1d", "vt_hot80_t2"): [2019, 2020],
}


def _mod():
    spec = importlib.util.spec_from_file_location("_fc_probe", PRODUCER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_fc_probe"] = mod
    spec.loader.exec_module(mod)
    return mod


def _cells_with_folds():
    return [r for r in (json.loads(x) for x in CORPUS.read_text().splitlines() if x.strip())
            if r.get("kind") == "cell" and r.get("wf_folds")]


def test_uncovered_oos_years_reproduce_on_the_committed_corpus():
    fc = _mod().fold_coverage
    got = {}
    for r in _cells_with_folds():
        c = fc(r.get("split"), r.get("wf_folds"))
        if c["uncovered_oos_years"]:
            got[(r["leg"], r.get("cell"))] = c["uncovered_oos_years"]
    assert got == EXPECTED_UNCOVERED


def test_absent_split_does_not_claim_coverage():
    """`None` is not `[]`. Grading an unparseable boundary as 'fully covered'
    would assert a check nobody ran."""
    fc = _mod().fold_coverage
    for bad in (None, "garbage", "", 12345):
        assert fc(bad, PANEL)["uncovered_oos_years"] is None, bad
    # And the same for unusable folds, with a perfectly good split.
    for bad in (None, [], [{"fold": "h1"}], "notalist"):
        assert fc("2019-01-30", bad)["uncovered_oos_years"] is None, bad


def test_a_split_inside_the_panel_is_covered_not_unknown():
    """The positive case must be distinguishable from the unknown one."""
    fc = _mod().fold_coverage
    c = fc("2025-07-01", PANEL)
    assert c["uncovered_oos_years"] == []
    assert c["pre_split_fold_years"] == [2021, 2022, 2023, 2024]


def test_years_above_the_last_fold_are_not_reported_uncovered():
    """The sweep's final fold is open-ended (`end=None`), so a label cannot show
    a later year is unexamined. Reporting one would fabricate a gap."""
    fc = _mod().fold_coverage
    # A split AFTER the whole panel: nothing below the earliest fold, so nothing
    # is claimed uncovered even though 2027 has no label.
    assert fc("2027-03-01", PANEL)["uncovered_oos_years"] == []


def test_the_producer_reads_the_sweeps_own_fold_key():
    """`walkforward_folds`, not the corpus-side `wf_folds`.

    Reading the corpus name at the producer yields None for every row and the
    coverage fields degrade silently to all-None — the write-and-never-read shape
    this field exists to expose.
    """
    src = PRODUCER.read_text()
    assert "fold_coverage(leg_common.get(\"split\")" in src, (
        "the producer no longer calls fold_coverage from the cell row")
    call = src[src.index("**fold_coverage("):]
    call = call[:call.index("\n\n")] if "\n\n" in call[:600] else call[:400]
    assert "walkforward_folds" in call, (
        "fold_coverage is being fed the CORPUS field name; it must read the "
        "sweep's own `walkforward_folds`")


def test_docstring_disclaims_the_per_year_decomposition():
    """The limit is load-bearing: without it the field reads as though it proved
    where the OOS gain came from, which nobody has measured."""
    doc = _mod().fold_coverage.__doc__ or ""
    assert "does NOT decompose" in doc
    assert "READING, not a measurement" in doc
