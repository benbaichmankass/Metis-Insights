"""M28 P4 — tests for the thesis backtest scoring core (calibration + net-of-cost)."""

from __future__ import annotations

from src.units.strategies.macro_thesis.thesis_backtest import (
    calibration_bins,
    calibration_rank,
    net_return,
    run_thesis_backtest,
    score_backtest,
    thesis_outcome,
)


# --------------------------------------------------------------------------
# net_return — direction-aware, net of cost, honest-null
# --------------------------------------------------------------------------

def test_net_return_long_short():
    assert net_return("long", 100.0, 110.0) == 0.10
    assert net_return("short", 100.0, 110.0) == -0.10
    assert net_return("short", 100.0, 90.0) == 0.10


def test_net_return_subtracts_fees_and_carry():
    # long +10%, minus 1% round-trip fee, minus 2% carry → 7%
    r = net_return("long", 100.0, 110.0, fee_frac=0.01, carry_frac=0.02)
    assert abs(r - 0.07) < 1e-9


def test_net_return_honest_null():
    assert net_return("long", 0.0, 110.0) is None       # non-positive entry
    assert net_return("long", "x", 110.0) is None        # non-numeric
    assert net_return("sideways", 100.0, 110.0) is None  # unknown direction


def test_thesis_outcome_win_flag_and_drops():
    o = thesis_outcome(0.8, "long", 100.0, 110.0, thesis_id="mth-1")
    assert o == {"thesis_id": "mth-1", "conviction": 0.8, "net_return": 0.10, "win": True}
    assert thesis_outcome(None, "long", 100.0, 110.0) is None       # no conviction → dropped
    assert thesis_outcome(0.8, "long", 0.0, 110.0) is None          # bad price → dropped
    loss = thesis_outcome(0.8, "long", 100.0, 95.0)
    assert loss["win"] is False


# --------------------------------------------------------------------------
# calibration
# --------------------------------------------------------------------------

def _out(conv, net):
    return {"conviction": conv, "net_return": net, "win": net > 0}


def test_calibration_bins_partition_and_stats():
    outs = [_out(0.1, -0.02), _out(0.2, -0.01),   # bin 0 [0,.25): 0 wins
            _out(0.6, 0.03),                        # bin 2 [.5,.75)
            _out(0.9, 0.05), _out(0.95, 0.04)]      # bin 3 [.75,1]: 2 wins
    bins = calibration_bins(outs, n_bins=4)
    assert len(bins) == 4
    assert bins[0]["n"] == 2 and bins[0]["hit_rate"] == 0.0
    assert bins[1]["n"] == 0 and bins[1]["hit_rate"] is None   # empty bin kept
    assert bins[3]["n"] == 2 and bins[3]["hit_rate"] == 1.0
    assert abs(bins[3]["mean_net_return"] - 0.045) < 1e-9


def test_calibration_bins_score_1p0_lands_in_last_bin():
    bins = calibration_bins([_out(1.0, 0.05)], n_bins=4)
    assert bins[3]["n"] == 1


def test_calibration_rank_positive_when_conviction_predicts():
    # conviction monotonically tracks return → Spearman ~ +1
    outs = [_out(0.1, -0.05), _out(0.4, -0.01), _out(0.7, 0.02), _out(0.95, 0.06)]
    r = calibration_rank(outs)
    assert r is not None and r > 0.99


def test_calibration_rank_negative_when_anticorrelated():
    outs = [_out(0.1, 0.06), _out(0.4, 0.02), _out(0.7, -0.01), _out(0.95, -0.05)]
    assert calibration_rank(outs) < -0.99


def test_calibration_rank_none_on_degenerate():
    assert calibration_rank([_out(0.5, 0.01)]) is None        # < 2 points
    assert calibration_rank([_out(0.5, 0.01), _out(0.5, 0.01)]) is None  # no variance


# --------------------------------------------------------------------------
# score_backtest
# --------------------------------------------------------------------------

def test_score_backtest_aggregates():
    outs = [_out(0.9, 0.04), _out(0.8, 0.02), _out(0.3, -0.03)]
    card = score_backtest(outs)
    assert card["n"] == 3
    assert abs(card["win_rate"] - 2 / 3) < 1e-9
    assert abs(card["mean_net_return"] - (0.04 + 0.02 - 0.03) / 3) < 1e-9
    assert card["calibration_rank"] is not None
    assert len(card["calibration_bins"]) == 4


def test_score_backtest_edge_vs_baseline():
    outs = [_out(0.9, 0.05), _out(0.8, 0.03)]
    base = [_out(0.9, 0.01), _out(0.8, 0.01)]
    card = score_backtest(outs, baseline_outcomes=base)
    assert abs(card["baseline_mean_net_return"] - 0.01) < 1e-9
    assert abs(card["edge_vs_baseline"] - (0.04 - 0.01)) < 1e-9


def test_score_backtest_empty_is_null_not_zero():
    card = score_backtest([])
    assert card["n"] == 0
    assert card["win_rate"] is None
    assert card["mean_net_return"] is None


# --------------------------------------------------------------------------
# run_thesis_backtest — the point-in-time replay harness
# --------------------------------------------------------------------------

def test_run_thesis_backtest_scores_entries_with_baseline():
    entries = [
        {"thesis_id": "a", "conviction": 0.9, "direction": "short",
         "entry_price": 100.0, "exit_price": 90.0, "hold_days": 10},   # short win +10% - carry
        {"thesis_id": "b", "conviction": 0.6, "direction": "long",
         "entry_price": 100.0, "exit_price": 104.0, "hold_days": 5},
    ]
    card = run_thesis_backtest(entries, fee_frac=0.005, carry_frac_per_day=0.001)
    assert card["n"] == 2
    # thesis 'a' is short (profits from the drop); the all-long baseline loses on it,
    # so the sleeve should show a positive edge over naive all-long
    assert card["edge_vs_baseline"] is not None and card["edge_vs_baseline"] > 0


def test_run_thesis_backtest_drops_bad_entries():
    entries = [{"conviction": 0.9, "direction": "long", "entry_price": 0.0, "exit_price": 90.0},
               {"conviction": None, "direction": "long", "entry_price": 100.0, "exit_price": 110.0}]
    card = run_thesis_backtest(entries)
    assert card["n"] == 0
