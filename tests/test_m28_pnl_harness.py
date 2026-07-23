"""M28 Phase A2 — tests for the S3 PnL harness (conviction-weighted portfolio backtest).

Covers the pure book-construction + metric helpers and one end-to-end run where the
conviction-weighted book beats the all-long baseline and the market-neutral book pays
out-of-sample — the S3 gate.
"""

from __future__ import annotations

import os
import sys

_MACRO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "macro")
sys.path.insert(0, _MACRO)

import pnl_harness as ph  # noqa: E402


# ---- book construction ---------------------------------------------------

def test_signed_conviction():
    assert ph._signed_conviction("long", 0.8) == 0.8
    assert ph._signed_conviction("short", 0.8) == -0.8
    assert ph._signed_conviction("sideways", 0.8) is None
    assert ph._signed_conviction("long", None) is None


def test_gross_normalize_sums_to_one():
    b = ph._gross_normalize({"A": 0.6, "B": -0.6})
    assert abs(b["A"] - 0.5) < 1e-12 and abs(b["B"] + 0.5) < 1e-12
    assert abs(sum(abs(v) for v in b.values()) - 1.0) < 1e-12
    assert ph._gross_normalize({"A": 0.0}) == {}          # no gross exposure


def test_neutral_book_dollar_neutral_and_single_side_degrades():
    b = ph._neutral_book({"A": 0.6, "B": -0.3})
    assert abs(sum(v for v in b.values() if v > 0) - 0.5) < 1e-12   # longs +0.5
    assert abs(sum(v for v in b.values() if v < 0) + 0.5) < 1e-12   # shorts −0.5
    # only one side → can't be neutral → degrades to gross-normalized (Σ|w|=1)
    one = ph._neutral_book({"A": 0.6, "B": 0.2})
    assert abs(sum(abs(v) for v in one.values()) - 1.0) < 1e-12


def test_all_long_book_equal_weight():
    assert ph._all_long_book(["A", "B", "C"]) == {"A": 1 / 3, "B": 1 / 3, "C": 1 / 3}


# ---- cohort net + period return ------------------------------------------

def test_cohort_gross_cost_is_direction_agnostic():
    # gross is the LONG move per symbol; direction is applied by the signed weight, not here.
    cohort = [
        {"symbol": "SPY", "direction": "long", "entry_price": 100.0, "exit_price": 110.0, "hold_days": 30},
        {"symbol": "TLT", "direction": "short", "entry_price": 100.0, "exit_price": 90.0, "hold_days": 30},
    ]
    gross, cost = ph._cohort_gross_cost(cohort, fee_frac=0.0, carry_frac_per_day=0.0)
    assert abs(gross["SPY"] - 0.10) < 1e-9     # +10% long move
    assert abs(gross["TLT"] + 0.10) < 1e-9     # −10% long move (the short earns +10% via its −weight)
    assert cost["SPY"] == 0.0


def test_cohort_gross_cost_computes_cost_drag():
    cohort = [{"symbol": "X", "direction": "long", "entry_price": 100.0, "exit_price": 110.0, "hold_days": 10}]
    gross, cost = ph._cohort_gross_cost(cohort, fee_frac=0.01, carry_frac_per_day=0.001)
    assert abs(gross["X"] - 0.10) < 1e-9
    assert abs(cost["X"] - (0.01 + 0.001 * 10)) < 1e-9      # fee + carry_per_day×hold_days


def test_book_period_return_signed_weight_and_cost_drag():
    # long A (+10% gross), short B (−4% gross → short earns +2% at half weight); cost drag on |w|.
    gross, cost = {"A": 0.1, "B": -0.04}, {"A": 0.0, "B": 0.0}
    assert abs(ph._book_period_return({"A": 0.5, "B": -0.5}, gross, cost) - (0.05 + 0.02)) < 1e-12
    # a cost of 0.01 on each leg drags |0.5|+|0.5| = 1.0 × 0.01
    assert abs(ph._book_period_return({"A": 0.5, "B": -0.5}, gross, {"A": 0.01, "B": 0.01}) - (0.07 - 0.01)) < 1e-12


# ---- metrics -------------------------------------------------------------

def test_equity_metrics_basic():
    m = ph._equity_metrics([0.10, -0.05, 0.10], ann_periods=12.0)
    # compounded: 1.1 * 0.95 * 1.1 = 1.1495
    assert abs(m["total_return"] - 0.1495) < 1e-4
    assert m["n_periods"] == 3 and m["hit_rate"] == round(2 / 3, 4)
    assert m["max_drawdown"] >= 0 and m["sharpe"] is not None


def test_equity_metrics_empty():
    m = ph._equity_metrics([], ann_periods=12.0)
    assert m["n_periods"] == 0 and m["total_return"] is None and m["equity"] == []


def test_turnover_and_ann_periods():
    to = ph._turnover([{"A": 0.5, "B": 0.5}, {"A": 1.0}])
    assert to is not None and to > 0
    assert ph._turnover([{"A": 1.0}]) is None
    # monthly dates → ~12/yr; weekly → ~52/yr
    monthly = [f"2024-{m:02d}-01" for m in range(1, 6)]
    assert 11 < ph._ann_periods(monthly) < 13


# ---- end-to-end: conviction book beats baseline + pays OOS ----------------

def _cohort(as_of):
    # SPY cheap → long, wins +10%; TLT rich → short, wins +10% (price falls).
    # all-long baseline: SPY +10%, TLT long −10% → nets flat, so conviction must beat it.
    return [
        {"symbol": "SPY", "direction": "long", "conviction": 0.9,
         "entry_price": 100.0, "exit_price": 110.0, "as_of": as_of, "hold_days": 30},
        {"symbol": "TLT", "direction": "short", "conviction": 0.9,
         "entry_price": 100.0, "exit_price": 90.0, "as_of": as_of, "hold_days": 30},
    ]


def test_run_pnl_backtest_conviction_beats_baseline_and_pays_oos():
    entries = _cohort("2026-01-01") + _cohort("2026-02-01") + _cohort("2026-03-01") + _cohort("2026-04-01")
    res = ph.run_pnl_backtest(entries, fee_frac=0.0, carry_frac_per_day=0.0, oos_frac=0.5)

    cw = res["conviction_weighted"]["full"]
    base = res["baseline_all_long"]["full"]
    ls = res["long_short_neutral"]["full"]
    assert cw["total_return"] > base["total_return"]        # directional book beats all-long
    assert abs(base["total_return"]) < 1e-9                 # all-long nets ~flat here
    assert ls["total_return"] > 0                           # market-neutral book pays
    assert cw["n_periods"] == 4
    assert res["summary"]["pays_oos"] is True               # the S3 gate passes OOS
    assert res["summary"]["edge_conviction_vs_baseline"] > 0
    # every book carries the full metric set
    for k in ("total_return", "sharpe", "max_drawdown", "hit_rate", "n_periods", "equity"):
        assert k in cw


def test_run_pnl_backtest_empty_is_safe():
    res = ph.run_pnl_backtest([], fee_frac=0.0, carry_frac_per_day=0.0)
    assert res["summary"]["n_rebalances"] == 0
    assert res["conviction_weighted"]["full"]["total_return"] is None
    assert res["summary"]["pays_oos"] is False
