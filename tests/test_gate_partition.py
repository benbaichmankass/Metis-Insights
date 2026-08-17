"""The done-condition must be partitioned by GATE, and the partition must be
impossible to read as complete while it is not.

`BL-20260817-M20-ACTIONABLE-COUNT-OVERSTATES-WHAT-A-SESSION-CAN-DO`.

The roll-up used to print `read the done-condition as {N} actionable +
{M} arithmetic`, computed `cells_to_done - len(unreachable)`. That subtracts
exactly ONE gate — the `exit_head_ml` fold arithmetic — and calls every other
gate "actionable". Measured 2026-08-17 it said **12 actionable** where about
**4** were workable by a session: the rest wait on trade accrual, on candles
that do not exist, on a lever the harness cannot express, or on a decision.

The reason this needs a test rather than a careful reading is that **the script
emitted the number**. It was not one author's prose slip — it reached a roadmap
entry and three operator pings, because every consumer of the roll-up was told
"12 actionable" by the tool itself.

Three properties are pinned, and the second is the load-bearing one:

  1. the partition RECONCILES to `cells_to_done` — a partition that drops or
     double-counts a cell is worse than no partition, since it looks total;
  2. an UNKNOWN `blocked:<reason>` surfaces as `unclassified` and is never
     absorbed into a neighbouring bucket. A silent default would make the
     partition read complete while mis-stating it — the collapsed-state shape
     the guard family elsewhere in this repo exists to catch;
  3. the `arithmetic` subset is taken from `fold_reachability`, not re-derived.
     Two derivations of one bound drifting apart is the defect this very file
     already carries a test for (`test_fold_reachability_is_derived.py`), and
     the reach rows are passed IN for exactly that reason.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROLLUP = REPO / "scripts" / "research" / "m20_coverage_rollup.py"
MATRIX = REPO / "docs" / "research" / "exit-refinement-coverage.json"


def _rollup():
    spec = importlib.util.spec_from_file_location("_m20_rollup_gp", ROLLUP)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_m20_rollup_gp"] = mod
    spec.loader.exec_module(mod)
    return mod


def _matrix(rows: list[dict]) -> dict:
    return {"lever_columns": ["exit_head_ml", "vol_trail"],
            "rows": rows, "updated_at": "2026-08-17"}


def _leg(strategy: str, **cells) -> dict:
    return {"strategy": strategy, "symbol": "X", "tf": "1d",
            "execution": "live", **cells}


# --------------------------------------------------------------------------
# 1. RECONCILIATION, against the real committed matrix.


def test_partition_reconciles_to_cells_to_done_on_the_live_matrix():
    m = _rollup()
    r = m.rollup(json.loads(MATRIX.read_text()))
    total = sum(len(v) for v in r["gate_partition"].values())
    assert total == r["cells_to_done"], (
        f"partition sums to {total} but cells_to_done is {r['cells_to_done']} — "
        f"a partition that does not reconcile is worse than none, because it "
        f"reads as exhaustive: {[(k, len(v)) for k, v in r['gate_partition'].items()]}")


def test_no_cell_appears_in_two_buckets():
    m = _rollup()
    r = m.rollup(json.loads(MATRIX.read_text()))
    seen: dict[tuple, str] = {}
    for kind, rows in r["gate_partition"].items():
        for ident in rows:
            key = tuple(ident[:4])
            assert key not in seen, (
                f"{key} is in both '{seen.get(key)}' and '{kind}' — double-counted, "
                f"so the totals can reconcile while the split is wrong")
            seen[key] = kind


# --------------------------------------------------------------------------
# 2. THE LOAD-BEARING ONE: an unknown reason must not be silently absorbed.


def test_unknown_blocked_reason_surfaces_as_unclassified():
    m = _rollup()
    gp = m.gate_partition(
        _matrix([_leg("newleg", vol_trail={"status": "blocked:some_future_reason"})]),
        reach=[])
    assert "unclassified" in gp, (
        "a blocked reason absent from GATE_KINDS was absorbed into another "
        "bucket — the partition then reads complete while mis-stating what "
        "gates the cell, which is the exact defect this partition replaced")
    assert len(gp["unclassified"]) == 1
    # and it must not ALSO have landed somewhere else
    assert sum(len(v) for v in gp.values()) == 1


def test_known_reasons_map_to_their_gate():
    m = _rollup()
    gp = m.gate_partition(_matrix([
        _leg("a", vol_trail={"status": "blocked:insufficient_lifetime_trades"}),
        _leg("b", vol_trail={"status": "blocked:native-history-thin"}),
        _leg("c", vol_trail={"status": "blocked:no_harness_levers"}),
        _leg("d", vol_trail={"status": "pending"}),
    ]), reach=[])
    kinds = {k: len(v) for k, v in gp.items()}
    assert kinds == {"accrual": 1, "data": 1, "harness_gap": 1,
                     "never_attempted": 1}, kinds
    assert "unclassified" not in gp


# --------------------------------------------------------------------------
# 3. THE ARITHMETIC SUBSET COMES FROM `fold_reachability`, NOT A SECOND DERIVATION.


def test_arithmetic_subset_is_driven_by_the_reach_rows():
    m = _rollup()
    rows = [_leg("thin", exit_head_ml={
        "status": "blocked:insufficient_lifetime_trades", "lifetime_trades": 30})]
    # With the leg named unreachable by reach, the cell is `arithmetic`...
    gp = m.gate_partition(_matrix(rows),
                          reach=[{"strategy": "thin", "usable_folds": 0}])
    assert [k for k, v in gp.items() if v] == ["arithmetic"], gp
    # ...and with the SAME matrix but the leg reachable, it is plain accrual.
    # This is what proves the classification follows `reach` rather than being
    # recomputed from the status string alone.
    gp = m.gate_partition(_matrix(rows),
                          reach=[{"strategy": "thin", "usable_folds": 2}])
    assert [k for k, v in gp.items() if v] == ["accrual"], gp


def test_ungraded_reach_stays_accrual_never_arithmetic():
    """`usable_folds: None` means we could not compute the bound.

    Absence of a graded bound is NOT evidence of an unreachable one, and calling
    it `arithmetic` would assert "no re-run can ever move this" on a cell nobody
    measured — the fabricated-certainty half of the same family.
    """
    m = _rollup()
    gp = m.gate_partition(
        _matrix([_leg("nofield", exit_head_ml={
            "status": "blocked:insufficient_lifetime_trades"})]),
        reach=[{"strategy": "nofield", "usable_folds": None,
                "ungraded_why": "cell carries no `lifetime_trades` field"}])
    assert [k for k, v in gp.items() if v] == ["accrual"], gp


def test_arithmetic_only_applies_to_exit_head_ml():
    """A non-exit_head_ml cell must not inherit the leg's fold arithmetic.

    `fold_reachability` is scoped to `exit_head_ml` (that lever's E1 fold
    protocol is what the bound is about). A `vol_trail` cell on the same thin
    leg is gated by its own floor, so labelling it `arithmetic` would import a
    bound from a different lever's protocol.
    """
    m = _rollup()
    gp = m.gate_partition(
        _matrix([_leg("thin", vol_trail={
            "status": "blocked:insufficient_lifetime_trades"})]),
        reach=[{"strategy": "thin", "usable_folds": 0}])
    assert [k for k, v in gp.items() if v] == ["accrual"], gp


# --------------------------------------------------------------------------
# 4. The withdrawn phrasing must not come back into the OUTPUT.


def test_output_no_longer_claims_a_single_actionable_number():
    m = _rollup()
    text = m.render(m.rollup(json.loads(MATRIX.read_text())))
    assert "actionable +" not in text, (
        "the roll-up is again printing a single 'N actionable + M arithmetic' "
        "figure. That number subtracts one gate and calls the rest work; it "
        "reported 12 where ~4 were workable and reached a roadmap entry and "
        "three operator pings. Print the gate partition instead.")
    assert "DONE-CONDITION BY GATE" in text
    # The caveat is part of the contract: the tool must SAY it cannot recover
    # the ref-prose categories, or a reader will take the status partition for
    # the whole truth.
    assert "ref PROSE" in text or "ref prose" in text
