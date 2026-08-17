"""The Path-B floor gate does not count a no-op fold as a walk-forward win.

`BL-20260817-FLEET-SWEEP-WF-COUNTS-INERT-FOLDS-AS-WINS`.

`m20_path_b_floor.wf_pass` graded on the sweep's recorded `wf_wins`, which
counts a fold as won whenever the gate returned `ok` — and the gate returns
`ok` for a fold where the lever changed NOTHING (`d_net_r == 0.0` and
`d_max_dd == 0.0`). That is not a label problem here: `wf_pass` is the
pass/fail signal the floor CALIBRATION is computed over, so an inflated count
feeds the floor the script recommends.

WHAT IS PINNED, and why each part is separate:

1. **The inequality is the same, only the numerator changes.** A second inline
   copy of the promotion rule is how the recorded and effective verdicts could
   drift for a reason unrelated to inert folds.
2. **`is_inert` is IMPORTED, not re-derived.** Two definitions of "did the
   lever fire?" could disagree about a fold, making the gate and the audit
   tool (`m20_wf_effective.py`) report different verdicts on the same row.
3. **`raw_only` is not zero.** A row with no `wf_folds` cannot have its inert
   split measured; grading it 0-effective would manufacture a failure out of a
   missing record. It falls back to the recorded count and SAYS so.
4. **Both rates ship.** Replacing the recorded rate silently would leave no way
   to see that the two differ, and the size of the gap is itself the finding.

⚠️ POPULATIONS — three different denominators, all correct, none
interchangeable (measured on the committed corpus 2026-08-17):

* **17 of 96** — deduped newest-run-per-cell rows carrying `wf_folds`.
* **19 of 133** — ALL cell rows carrying `wf_folds` (superseded re-runs included).
* **13 of 112** — the rows `analyse` actually grades (fold-carrying AND
  carrying the `base_rate_IS` axis). This is the one the floor calibration
  depends on.

The gap on the analysed population is **11.6pp** (0.6875 recorded vs 0.5714
effective), and the verdict is `no_separation` BOTH ways — so nothing was
mis-selected on this corpus. That is recorded here so the finding is not
re-inflated: the defect is real and biases a selection, it did not make one.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _floor():
    return _load("scripts/research/m20_path_b_floor.py", "_pbf_probe")


def _folds(n: int, *, inert: bool):
    d = {"d_net_r": 0.0, "d_max_dd": 0.0} if inert else {"d_net_r": 1.0,
                                                         "d_max_dd": -0.5}
    return [{"ok": True, "usable": True, **d} for _ in range(n)]


# ---------------------------------------------------------------------------
# 1. AN ALL-INERT WALK-FORWARD MUST NOT PASS.

def test_all_inert_folds_do_not_pass_while_the_recorded_count_does():
    """The load-bearing case: identical recorded 6/6, opposite verdicts.

    If this ever reads True the gate is back to counting no-ops as evidence of
    generalisation.
    """
    fl = _floor()
    row = {"wf_ran": True, "wf_wins": 6, "wf_usable": 6,
           "wf_folds": _folds(6, inert=True)}
    assert fl.wf_pass_as_recorded(row) is True, "the recorded count should pass"
    assert fl.wf_pass(row) is False, (
        "six folds in which the lever changed nothing passed the gate")


def test_all_real_folds_still_pass():
    """The non-regression half — the fix must not fail a genuine 6/6."""
    fl = _floor()
    row = {"wf_ran": True, "wf_wins": 6, "wf_usable": 6,
           "wf_folds": _folds(6, inert=False)}
    assert fl.wf_pass(row) is True
    assert fl.wf_pass_as_recorded(row) is True


# ---------------------------------------------------------------------------
# 2. THE THREE BASES STAY DISTINGUISHABLE.

def test_wf_basis_separates_effective_raw_only_and_ungraded():
    fl = _floor()
    assert fl.wf_basis({"wf_ran": False}) == "ungraded"
    assert fl.wf_basis({"wf_ran": True, "wf_wins": 5,
                        "wf_usable": 6}) == "raw_only"
    assert fl.wf_basis({"wf_ran": True, "wf_wins": 5, "wf_usable": 6,
                        "wf_folds": _folds(6, inert=False)}) == "effective"
    # An EMPTY fold list is not fold detail — it is the absence of it.
    assert fl.wf_basis({"wf_ran": True, "wf_wins": 5, "wf_usable": 6,
                        "wf_folds": []}) == "raw_only"


def test_absent_fold_detail_falls_back_rather_than_grading_zero():
    """`raw_only` must NOT be scored as zero-effective.

    Absent fold detail is "we could not look". Grading it 0 would manufacture a
    failure out of a missing record — the same error as reading an unmeasured
    exposure as a flat book.
    """
    fl = _floor()
    row = {"wf_ran": True, "wf_wins": 6, "wf_usable": 6}
    assert fl.wf_pass(row) is True, (
        "a row with no fold detail was failed as if every fold were inert")
    assert fl.wf_pass(row) == fl.wf_pass_as_recorded(row)


def test_ungraded_is_none_and_never_false():
    """None is not False — a cell that never reached a walk-forward carries no
    evidence, and folding it into the failures would invent a negative."""
    fl = _floor()
    assert fl.wf_pass({"wf_ran": False}) is None
    assert fl.wf_pass_as_recorded({"wf_ran": False}) is None
    # wf_ran true but the counts absent is also unknowable, not a failure.
    assert fl.wf_pass({"wf_ran": True}) is None


# ---------------------------------------------------------------------------
# 3. ONE DEFINITION OF "DID THE LEVER FIRE?".

def test_inert_predicate_is_imported_not_redefined():
    """The gate must reuse `m20_wf_effective`'s grading.

    Two derivations could disagree about a fold, and then the live gate and the
    offline audit would report different verdicts on the same row — which is
    the class this whole item is about.
    """
    src = (REPO / "scripts" / "research" / "m20_path_b_floor.py").read_text()
    assert "m20_wf_effective" in src, "the gate no longer imports the reader"
    assert "d_net_r\") == 0.0" not in src and "d_net_r'] == 0.0" not in src, (
        "the inert test was re-derived inline instead of imported")

    fl = _floor()
    wfe = _load("scripts/research/m20_wf_effective.py", "_wfe_probe")
    # Same fold list, same split — the gate's numerator IS grade_folds().
    folds = _folds(4, inert=True) + _folds(2, inert=False)
    g = wfe.grade_folds(folds)
    assert g["effective"] == 2 and g["inert"] == 4
    row = {"wf_ran": True, "wf_wins": 6, "wf_usable": 6, "wf_folds": folds}
    # 2 effective of 6 usable fails 2/3; 6 recorded of 6 passes.
    assert fl.wf_pass(row) is False
    assert fl.wf_pass_as_recorded(row) is True


def test_the_promotion_rule_is_stated_once():
    """Both verdicts must run the SAME inequality, differing only in numerator."""
    fl = _floor()
    # Below the usable floor, neither can pass however many wins are claimed.
    assert fl._wf_inequality(3, 3) is None or fl._wf_inequality(3, 3) is False
    assert fl._wf_inequality(4, 4) is True
    assert fl._wf_inequality(2, 4) is False       # 2/4 < 2/3
    assert fl._wf_inequality(4, 6) is True        # exactly 2/3 passes
    assert fl._wf_inequality(3, 6) is False
    assert fl._wf_inequality(None, 6) is None     # absent != zero
    assert fl._wf_inequality(4, None) is None


# ---------------------------------------------------------------------------
# 4. THE REPORT CARRIES BOTH RATES AND THE DIVERGENCE.

def test_analyse_reports_both_rates_and_the_inflated_count():
    """Publishing only the corrected rate would hide that the two differ."""
    fl = _floor()
    rows = []
    # 6 legs, each one cell, all with the axis present. Half all-inert.
    for i in range(6):
        rows.append({
            "kind": "cell", "leg": f"leg_{i}", "lever": "vol_trail",
            "cell": f"c{i}", "wf_ran": True, "wf_wins": 6, "wf_usable": 6,
            "wf_folds": _folds(6, inert=(i % 2 == 0)),
            "base_rate_IS": 1.0 + i * 0.5,
        })
    out = fl.analyse(rows, "base_rate_IS", "floor")
    d = out["axis_distribution"]
    assert d["overall_wf_pass_rate_as_recorded"] == 1.0, (
        "every row records 6/6, so the recorded rate must be 1.0")
    assert d["overall_wf_pass_rate"] == 0.5, (
        "half the rows are all-inert, so the effective rate must be 0.5")
    assert d["cells_inflated_by_inert_folds"] == 3
    assert d["wf_basis_counts"] == {"effective": 6}


def test_raw_only_rows_are_counted_so_partial_correction_is_visible():
    """A large `raw_only` count means the corrected rate is only PARTLY
    corrected. That has to be readable, not inferred."""
    fl = _floor()
    rows = [{
        "kind": "cell", "leg": f"leg_{i}", "lever": "x", "cell": f"c{i}",
        "wf_ran": True, "wf_wins": 6, "wf_usable": 6,
        "base_rate_IS": 1.0 + i * 0.5,
    } for i in range(5)]
    out = fl.analyse(rows, "base_rate_IS", "floor")
    d = out["axis_distribution"]
    assert d["wf_basis_counts"] == {"raw_only": 5}
    # Nothing is measurable as inert here, so no inflation may be claimed.
    assert d["cells_inflated_by_inert_folds"] == 0
    assert d["overall_wf_pass_rate"] == d["overall_wf_pass_rate_as_recorded"]
