"""The exit_head_ml block must be DERIVED from a field, not asserted in prose.

`BL-20260815-EXIT-HEAD-MATRIX-REFS-USE-THE-LEVER-SWEEPS-ARITHMETIC`.

Eleven `exit_head_ml` cells are blocked on trade volume, and every one of their
lifetime-trade counts lived only inside ref PROSE. Nothing could recompute the
bound, so two different arithmetics coexisted in one lever column: the seven
equity 1d cells reasoned correctly from lifetime trades and the ">=100 for one
fold" bound, while three futures cells reasoned from the LEVER sweep's
`MIN_OOS_TRADES=25` and its date split — and then named an "earlier split" route
that `train_exit_head.fold_blocks` forecloses, because that lever has no date
split at all.

Two properties are pinned here, and the second is the one that matters:

  1. the roll-up's `usable_folds` matches `train_exit_head.fold_blocks`'s ACTUAL
     construction, not a closed form someone believed was equivalent;
  2. the roll-up READS `lifetime_trades`. A field written and never read is
     worse than a missing one — reviewers see it and assume something acts on
     it, which is exactly the `provenance-consumer-guard` class.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROLLUP = REPO / "scripts" / "research" / "m20_coverage_rollup.py"
TRAINER = REPO / "scripts" / "ml" / "train_exit_head.py"
MATRIX = REPO / "docs" / "research" / "exit-refinement-coverage.json"


def _rollup():
    spec = importlib.util.spec_from_file_location("_m20_rollup", ROLLUP)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_m20_rollup"] = mod
    spec.loader.exec_module(mod)
    return mod


def _blocked_cells() -> list[dict]:
    d = json.loads(MATRIX.read_text())
    return [r for r in d["rows"]
            if isinstance(r.get("exit_head_ml"), dict)
            and str(r["exit_head_ml"].get("status") or "").startswith(
                "blocked:insufficient_lifetime")]


# --------------------------------------------------------------------------
# Positive control — a negative needs a denominator.
# --------------------------------------------------------------------------

def test_there_are_blocked_cells_to_reason_about() -> None:
    cells = _blocked_cells()
    assert len(cells) >= 5, (
        f"only {len(cells)} blocked exit_head_ml cell(s); every assertion below "
        "would be over a population too small to mean anything")


def test_every_blocked_cell_CARRIES_the_count():
    """The count must be a field. In prose it is unrecomputable — which is how
    two incompatible arithmetics survived side by side in one column."""
    missing = [r["strategy"] for r in _blocked_cells()
               if not isinstance(r["exit_head_ml"].get("lifetime_trades"), int)]
    assert not missing, (
        f"blocked exit_head_ml cells with no `lifetime_trades` field: {missing}. "
        "The bound cannot be re-derived from prose, so a reader has to trust it")


# --------------------------------------------------------------------------
# The arithmetic must be the TRAINER's, not a lookalike.
# --------------------------------------------------------------------------

def test_usable_folds_matches_the_trainers_actual_block_construction() -> None:
    """Compared against the real expression lifted from `fold_blocks`.

    A closed form (`max(0, N//b - 1)`) happens to agree today. Pinning against
    the real `range(...)` is what keeps them equal if the construction changes —
    the point is that this module cannot silently describe a partition the
    trainer no longer produces.
    """
    mod = _rollup()
    b = mod._E1_BLOCK
    for n in list(range(0, 400, 7)) + [b, 2 * b, 3 * b, 3 * b - 1, 3 * b + 1]:
        expected = len(range(b, n - b + 1, b))          # fold_blocks' own loop
        assert mod.usable_folds(n, b) == expected, (n, expected)


def test_the_trainer_still_blocks_the_way_this_assumes() -> None:
    """If `fold_blocks` stops using this loop, the derivation is stale.

    Structural, because running it needs a dataset and hours of compute — but a
    stale derivation printed as a measured bound is the failure this whole row
    is about, so a cheap tripwire beats none.
    """
    src = TRAINER.read_text()
    assert "for start in range(block_n, len(ordered) - block_n + 1, block_n):" in src, (
        "train_exit_head.fold_blocks no longer cuts blocks with "
        "`range(block_n, len(ordered)-block_n+1, block_n)`, so the roll-up's "
        "usable_folds() may now describe a partition the trainer does not "
        "produce. Re-derive both together.")


# --------------------------------------------------------------------------
# The field must be READ.
# --------------------------------------------------------------------------

def test_the_rollup_actually_reads_the_field() -> None:
    mod = _rollup()
    matrix = json.loads(MATRIX.read_text())
    rows = mod.fold_reachability(matrix)
    assert rows, "fold_reachability returned nothing over a matrix that has blocked cells"
    assert len(rows) == len(_blocked_cells())
    assert all(r["usable_folds"] is not None for r in rows), (
        f"ungraded rows present: {[r for r in rows if r['usable_folds'] is None]}")
    # And it must reach the rendered output — a derivation nobody prints is a
    # derivation nobody reads.
    text = mod.render(mod.rollup(matrix)) if hasattr(mod, "render") else ""
    assert "lifetime_trades" in ROLLUP.read_text()
    assert "u=" in text and "arithmetic" in text.lower(), (
        "the reachability block is computed but not rendered")


def test_the_done_condition_SPLITS_actionable_from_arithmetic() -> None:
    """A done-condition that pools both invites "keep sweeping and it converges".

    It does not. A leg with 31 lifetime trades cannot reach u>=2 (N>=150) by
    being re-run — only by TRADING more, which is a strategy question, not an
    M20 one. The two kinds of remaining work must be legible as two kinds.
    """
    mod = _rollup()
    matrix = json.loads(MATRIX.read_text())
    r = mod.rollup(matrix)
    text = mod.render(r)

    unreachable = [x for x in r["fold_reachability"] if x["usable_folds"] == 0]
    assert unreachable, "no u=0 cells — the split below would be vacuous"
    assert "ARITHMETICALLY unreachable" in text, (
        "the done-condition no longer separates cells that cannot be closed by "
        "more work from ones that can")
    # The arithmetic must RECONCILE, not just be printed.
    actionable = r["cells_to_done"] - len(unreachable)
    assert f"{actionable} actionable + {len(unreachable)} arithmetic" in text, (
        f"the split does not reconcile with cells_to_done={r['cells_to_done']} "
        f"and {len(unreachable)} unreachable")


def test_a_cell_missing_the_count_is_REPORTED_not_skipped() -> None:
    """Silently dropping it would report a denominator nothing measured."""
    mod = _rollup()
    matrix = {"rows": [{"strategy": "fake_leg",
                        "exit_head_ml": {"status": "blocked:insufficient_lifetime_trades"}}]}
    rows = mod.fold_reachability(matrix)
    assert len(rows) == 1 and rows[0]["usable_folds"] is None
    assert rows[0].get("ungraded_why"), "an ungraded row must say why"
