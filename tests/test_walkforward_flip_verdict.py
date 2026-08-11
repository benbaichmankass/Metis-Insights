"""The walk-forward driver's verdict must distinguish FAILED from NOT TESTED.

BL-20260811 follow-up. `_evaluate_pass_criteria` looped over the module-level
`FOLDS` and both rosters regardless of what the run actually covered, so any
absent cell became `missing_cell` and `overall_pass` was False by construction.

That is not hypothetical. The flip-override walk-forward
(.github/workflows/flip-override-walkforward.yml, run 31523739722) shards ONE
FOLD PER JOB for wall-clock, so each job saw the other fold's cells as missing
and printed **`Overall: FAIL`** — directly beneath a result about the
`hold_confgap` override, which these criteria do not test at all (they compare
`hold` vs `reverse`, the May 2026 question). A reader has to already know the
harness internals to discount that, and a reader who does not takes the red
verdict as the finding.

It is the repo's own collapsed-state class (docs/CLAUDE-RULES-CANONICAL.md
§ "Collapsed states") landing in a research tool: "we did not look" and "we
looked and it failed" are opposite statements, and one was wearing the other's
label.

There is no pre-existing test module for this driver — its absence is why the
defect shipped — so this file is new.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

wf = pytest.importorskip("scripts.walkforward_flip_policy")


def _cell(fold, half, roster, policy, net, dd):
    # Full enough for BOTH consumers: `_evaluate_pass_criteria` reads only
    # net/maxDD, but `_markdown_summary` renders the whole row, and a fixture
    # shaped to only the first would pass the verdict tests while the rendering
    # tests died on a KeyError — testing the fix's JSON half and not its
    # human-facing half is exactly the gap that let this ship.
    return wf.Cell(fold=fold, half=half, roster=roster, policy=policy,
                   start="2020-06-01", end="2023-12-31",
                   summary={"net_pnl": net, "max_drawdown_pct": dd,
                            "return_dd_ratio": None, "total_trades": 0,
                            "by_exit_reason": {}, "evidence": {}})


def _four_mem_grid(folds=("A", "B"), *, hold_net=100.0, reverse_net=10.0,
                   hold_dd=5.0, reverse_dd=9.0):
    cells = []
    for f in folds:
        for h in ("train", "oos"):
            cells.append(_cell(f, h, "4mem", "hold", hold_net, hold_dd))
            cells.append(_cell(f, h, "4mem", "reverse", reverse_net, reverse_dd))
    return cells


# ---------------------------------------------------------------------------
# The defect: a run that could not evaluate a criterion must not fail it
# ---------------------------------------------------------------------------
def test_a_confgap_only_run_does_not_fail_the_hold_vs_reverse_criteria():
    """The exact shape that produced the misleading output: an override-arm run
    with no `reverse` cells at all. Both criteria are hold-vs-reverse, so
    neither is answerable, and neither may report FAIL."""
    cells = [_cell("A", "train", "4mem", "hold", 672.0, 8.20),
             _cell("A", "train", "4mem", "hold_confgap", 113.0, 11.04)]
    v = wf._evaluate_pass_criteria(cells)

    c1 = v["criterion_1_4member_hold_dominates_reverse"]
    c2 = v["criterion_2_6member_hold_not_worse_than_reverse_oos"]
    assert c1["applicable"] is False
    assert c1["pass"] is None, "an untested criterion has no verdict"
    assert c2["applicable"] is False
    assert c2["pass"] is None
    assert v["overall_pass"] is None, (
        "nothing was applicable, so the run has no overall verdict — "
        "not False (the bug) and not a vacuous True")


def test_a_single_fold_shard_does_not_fail_on_the_absent_fold():
    """The fold matrix's actual failure mode: fold A alone, complete and
    passing on its own cells, must not be dragged to FAIL by fold B's absence."""
    v = wf._evaluate_pass_criteria(_four_mem_grid(folds=("A",)))
    c1 = v["criterion_1_4member_hold_dominates_reverse"]
    assert c1["applicable"] is True
    assert c1["pass"] is True, "fold A's own cells all pass"
    assert not any(cell.get("reason") == "missing_cell" for cell in c1["cells"]), \
        "fold B must not appear as a missing cell in a fold-A-only run"
    assert v["scope"]["folds_in_run"] == ["A"]
    assert v["scope"]["is_full_fold_grid"] is False, (
        "a shard must DECLARE it is a shard, or a reader cannot tell a partial "
        "grid from a full one")


def test_a_genuine_failure_is_still_reported_as_a_failure():
    """The guard against over-correcting: making everything NOT TESTED would be
    the mirror-image bug. A run that DID evaluate the criterion and lost must
    still read FAIL."""
    cells = _four_mem_grid(folds=("A",), hold_net=10.0, reverse_net=999.0)
    v = wf._evaluate_pass_criteria(cells)
    c1 = v["criterion_1_4member_hold_dominates_reverse"]
    assert c1["applicable"] is True
    assert c1["pass"] is False
    assert v["overall_pass"] is False


def test_full_grid_still_passes_end_to_end():
    """The May 2026 shape, unchanged: both folds, both rosters, hold beats
    reverse everywhere."""
    cells = _four_mem_grid()
    for f in ("A", "B"):
        cells.append(_cell(f, "oos", "6mem", "hold", 50.0, 12.0))
        cells.append(_cell(f, "oos", "6mem", "reverse", 20.0, 15.0))
    v = wf._evaluate_pass_criteria(cells)
    assert v["criterion_1_4member_hold_dominates_reverse"]["pass"] is True
    assert v["criterion_2_6member_hold_not_worse_than_reverse_oos"]["pass"] is True
    assert v["overall_pass"] is True
    assert v["scope"]["is_full_fold_grid"] is True


# ---------------------------------------------------------------------------
# The rendered output and the exit code carry the same three states
# ---------------------------------------------------------------------------
def test_markdown_says_NOT_TESTED_rather_than_FAIL():
    """A JSON field nobody reads is not the fix — the human-facing line is what
    misled, so it is what has to change."""
    cells = [_cell("A", "train", "4mem", "hold", 672.0, 8.20),
             _cell("A", "train", "4mem", "hold_confgap", 113.0, 11.04)]
    md = wf._markdown_summary(cells, wf._evaluate_pass_criteria(cells),
                              ["hold", "hold_confgap"])
    assert "NOT TESTED" in md
    assert "Overall: **FAIL**" not in md
    # And it warns the reader off applying these criteria to the override arms.
    assert "hold_confgap" in md


def test_markdown_flags_a_partial_grid():
    md = wf._markdown_summary(_four_mem_grid(folds=("A",)),
                              wf._evaluate_pass_criteria(_four_mem_grid(folds=("A",))),
                              ["hold", "reverse"])
    assert "folds ['A'] only" in md or "['A']" in md


def test_exit_code_does_not_report_untested_as_failed():
    """Exiting 2 on 'nothing to judge' would reproduce the collapsed state one
    level up, where a caller (a CI step, a workflow) sees only the code."""
    assert wf._exit_code_for({"overall_pass": None}) == 0
    assert wf._exit_code_for({"overall_pass": True}) == 0
    assert wf._exit_code_for({"overall_pass": False}) == 2
