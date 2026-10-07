"""Regression tests for CA-B04 pairs_universe_scan defects (2026-10-07).

1. The Engle-Granger gate used the single-series DF critical value (-2.86); on
   independent random walks that passes ~20% of pairs at a nominal 5%.
2. The OOS-persistence cointegrating vector was fit on the FULL sample,
   including the OOS window it was then tested against.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "research"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pairs_universe_scan as pus  # noqa: E402


def test_eg_critical_value_is_two_variable_mackinnon():
    assert abs(pus.eg_critical_value(1500) - (-3.34)) < 0.02
    assert pus.eg_critical_value(1500) < -2.86 - 0.4
    assert pus.eg_critical_value(500, 0.01) < pus.eg_critical_value(500, 0.05) < pus.eg_critical_value(500, 0.10)


def test_gate_false_positive_rate_near_nominal_on_independent_walks():
    rng = np.random.default_rng(11)
    n, trials, hits_old, hits_new = 1500, 300, 0, 0
    crit = pus.eg_critical_value(n)
    for _ in range(trials):
        la = np.cumsum(rng.normal(0, 0.02, n))
        lb = np.cumsum(rng.normal(0, 0.02, n))
        X = np.column_stack([np.ones(n), lb])
        coef, *_ = np.linalg.lstsq(X, la, rcond=None)
        t = pus._adf_tstat(la - X @ coef)
        hits_old += t <= -2.86
        hits_new += t <= crit
    assert hits_old / trials > 0.12          # the defect: ~4x nominal
    assert hits_new / trials < 0.10          # fixed: near the nominal 5%


def _csv(tmp, name, close, start="2022-01-01"):
    ts = pd.date_range(start, periods=len(close), freq="1h", tz="UTC")
    p = os.path.join(tmp, f"{name}.csv")
    pd.DataFrame({"timestamp": ts, "open": close, "high": close * 1.001,
                  "low": close * 0.999, "close": close}).to_csv(p, index=False)
    return p


def test_cointegrating_vector_fit_excludes_oos_window(tmp_path):
    rng = np.random.default_rng(3)
    n = 4000
    la = np.cumsum(rng.normal(0, 0.02, n)) + 4.6
    lb = np.cumsum(rng.normal(0, 0.02, n)) + 3.9
    pa = _csv(str(tmp_path), "AAA", np.exp(la))
    pb = _csv(str(tmp_path), "BBB", np.exp(lb))
    args = pus._parse([])
    args.min_bars = 500
    args.min_trades = 1
    args.oos_start = "2022-04-01"
    rec = pus._score_pair("AAA", "BBB", pa, pb, args)
    assert "error" not in rec, rec
    oos_ts = pd.Timestamp(args.oos_start, tz="UTC")
    n_pre = int((pd.date_range("2022-01-01", periods=n, freq="1h", tz="UTC") < oos_ts).sum())
    assert rec["n_bars"] == n
    assert rec["n_fit_bars"] == n_pre < n
    # and the recorded gate is the EG value, not -2.86
    assert abs(rec["adf_crit"] - pus.eg_critical_value(n_pre)) < 1e-3
