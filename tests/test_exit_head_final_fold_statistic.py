"""analyze_exit_head must emit the FINAL time-block fold's own n_oos and recovered R.

WHY THIS EXISTS. RQ-20260928-005 registered its statistic on the final walk-forward
fold (n_oos floor 64), but the analyzer only emitted a pooled all-fold mean, so the
registered statistic could not be graded and the unit read `indeterminate`.
Pooled figures must not stand in for it after the data is seen.
"""
from __future__ import annotations

import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from scripts.research import analyze_exit_head as aeh  # noqa: E402


def test_fold_entry_sums_and_flags_last_block():
    e = aeh._fold_policy_entry(4, 5, [0.5, -0.25, 0.25])
    assert e == {"fold": 4, "is_last_block": True, "n_oos": 3,
                 "recovered_r": 0.5, "mean_net_r_improvement": 0.1667}
    assert aeh._fold_policy_entry(0, 5, [])["mean_net_r_improvement"] is None
    assert aeh._fold_policy_entry(3, 5, [1.0])["is_last_block"] is False


def test_final_fold_never_borrows_an_earlier_fold():
    by_fold = [aeh._fold_policy_entry(i, 5, [0.1]) for i in range(4)]  # last skipped
    out = aeh._final_fold_entry(by_fold)
    assert out["computed"] is False


def test_final_fold_picked_when_present():
    by_fold = [aeh._fold_policy_entry(i, 3, [0.1] * (i + 1)) for i in range(3)]
    out = aeh._final_fold_entry(by_fold)
    assert out["computed"] and out["n_oos"] == 3 and out["fold"] == 2


@pytest.mark.skipif(not aeh._NUMPY_OK, reason="numpy unavailable")
def test_analyze_emits_by_fold_reconciling_to_pooled():
    import random
    rnd = random.Random(7)
    rows = []
    t = 0
    for tid in range(120):
        r_real = rnd.uniform(-1, 2)
        for b in range(6):
            t += 1
            rows.append({
                "trade_id": tid, "decision_time": f"2025-01-01T{tid // 60:02d}:{tid % 60:02d}:{b:02d}",
                "label_hold": rnd.randint(0, 1), "label_t0": tid * 100 + b, "label_t1": tid * 100 + b + 12,
                "feat_a": rnd.random(), "feat_b": rnd.random(), "feat_upnl_r": rnd.uniform(-1, 1),
                "trade_realized_r": r_real, "advantage_r": rnd.uniform(-1, 1), "r_per_bp": 0.001,
            })
    rep = aeh.analyze_exit_head(rows, None, n_folds=5, embargo_bars=12)
    pol = rep["exit_policy"]
    assert pol["computed"]
    by = pol["by_fold"]
    assert sum(f["n_oos"] for f in by) == pol["n_policy_trades"]
    assert pol["final_fold"]["computed"] and pol["final_fold"]["is_last_block"]
    assert pol["final_fold"]["n_oos"] == by[-1]["n_oos"]
    pooled = pol["mean_net_r_improvement"] * pol["n_policy_trades"]
    assert abs(sum(f["recovered_r"] for f in by) - pooled) < 0.01 * len(by) + 0.01
