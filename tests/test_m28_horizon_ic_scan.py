"""M28/M29 — tests for the information-coefficient-by-horizon scan.

Unit-tests the new decision logic (`ic_t_stat`, `summarize`) precisely, plus one
end-to-end wiring test (fixture snapshots → scan_horizons → per-horizon IC rows)
reusing the same minimal valuation-snapshot fixture the P4 runner test uses.
"""

from __future__ import annotations

import importlib.util
import math
import os

_SCAN_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts", "macro", "horizon_ic_scan.py",
)
_spec = importlib.util.spec_from_file_location("horizon_ic_scan", _SCAN_PATH)
scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan)


# ---- ic_t_stat -----------------------------------------------------------

def test_ic_t_stat_basic():
    assert scan.ic_t_stat(0.0, 100) == 0.0
    # ic*sqrt(n-2)/sqrt(1-ic^2)
    assert abs(scan.ic_t_stat(0.2, 102) - (0.2 * math.sqrt(100) / math.sqrt(0.96))) < 1e-9


def test_ic_t_stat_edge_cases():
    assert scan.ic_t_stat(None, 100) is None
    assert scan.ic_t_stat(0.3, 2) is None       # n < 3
    assert scan.ic_t_stat(1.0, 50) is None       # denom 0 → undefined, not a div0 crash


# ---- summarize -----------------------------------------------------------

def _row(h, n, ic, edge, spread=None, overlapping=False):
    return {
        "horizon_days": h, "n": n, "n_windows": n, "overlapping": overlapping, "ic": ic,
        "ic_t": scan.ic_t_stat(ic, n), "win_rate": 0.5,
        "mean_net_return": 0.0, "edge_vs_baseline": edge, "conv_spread": spread,
    }


# ---- conviction_spread ---------------------------------------------------

def test_conviction_spread_high_minus_low():
    card = {"calibration_bins": [
        {"lo": 0.0, "hi": 0.25, "n": 0, "mean_net_return": None},   # empty → skipped
        {"lo": 0.25, "hi": 0.5, "n": 40, "mean_net_return": -0.002},  # lowest populated
        {"lo": 0.5, "hi": 0.75, "n": 60, "mean_net_return": 0.001},
        {"lo": 0.75, "hi": 1.0, "n": 90, "mean_net_return": 0.004},   # highest populated
    ]}
    assert abs(scan.conviction_spread(card) - (0.004 - (-0.002))) < 1e-12
    assert scan.conviction_spread({"calibration_bins": [{"n": 5, "mean_net_return": 0.01}]}) is None


def test_summarize_flags_honest_monetizable_horizon():
    rows = [
        _row(7, 400, 0.01, -0.001, spread=-0.001),          # no positive IC/spread
        _row(30, 400, 0.15, 0.004, spread=0.003),           # +IC (|t|~3), +spread, non-overlapping → honest
        _row(90, 200, 0.02, 0.0005, spread=0.0001),         # weak
    ]
    s = scan.summarize(rows, t_flag=2.0)
    assert s["any_monetizable_horizon"] is True
    assert s["any_honest_monetizable_horizon"] is True
    assert s["best_horizon_days"] == 30 and s["best_conv_spread"] == 0.003
    assert s["verdict"] == "monetizable_horizon_found"


def test_summarize_overlap_only_is_a_lead_not_confirmed():
    # +IC + +spread but the window OVERLAPS → the t is optimistic; a lead to re-test, not confirmed.
    rows = [_row(90, 2000, 0.05, -0.004, spread=0.003, overlapping=True)]
    s = scan.summarize(rows, t_flag=2.0)
    assert s["any_monetizable_horizon"] is True
    assert s["any_honest_monetizable_horizon"] is False
    assert s["verdict"] == "monetizable_horizon_overlap_only"


def test_summarize_no_monetizable_horizon():
    rows = [
        _row(7, 400, 0.01, 0.002, spread=0.001),   # +spread but |t|<2
        _row(30, 400, 0.15, 0.004, spread=-0.002),  # flagged +IC but spread<0 (not monetizable)
    ]
    s = scan.summarize(rows, t_flag=2.0)
    assert s["any_monetizable_horizon"] is False
    assert s["verdict"] == "no_monetizable_horizon"
    assert s["strongest_ic_horizon_days"] == 30 and s["strongest_ic"] == 0.15


def test_summarize_no_data():
    s = scan.summarize([{"horizon_days": 30, "n": 0, "ic": None, "ic_t": None,
                         "edge_vs_baseline": None, "conv_spread": None}])
    assert s["verdict"] == "no_data"
    assert s["any_monetizable_horizon"] is False


# ---- end-to-end wiring: fixture snapshots → per-horizon IC rows -----------

def _snap(symbol, cheap_score, observed_at):
    return {
        "symbol": symbol, "metric": "erp", "value": 1.0,
        "cheap_score": cheap_score, "label": "cheap" if cheap_score >= 0.5 else "rich",
        "higher_is_cheaper": True, "n_history": 60, "percentile": cheap_score,
        "z_score": 0.0, "observed_at": observed_at, "as_of": observed_at,
        "source": "test", "asset_class": "equity", "inputs": {}, "note": "",
    }


def test_scan_horizons_wires_per_horizon_rows():
    # SPY reads cheap across several monthly rebalances; each becomes a priced thesis.
    # The scan must run the replay at each horizon and return well-formed rows.
    records = [_snap("SPY", 0.9, d) for d in ("2026-01-05", "2026-02-02", "2026-03-02")]
    panels = {"SPY": [
        ("2026-01-05", 100.0), ("2026-02-02", 104.0), ("2026-03-02", 106.0),
        ("2026-04-01", 112.0), ("2026-06-01", 120.0), ("2026-09-01", 130.0),
    ]}
    price_at = scan.make_price_at(panels)   # re-exported from thesis_backtest_run
    cfg = {"min_conviction": 0.4, "universe": ["SPY"],
           "express_as": "debit_vertical", "account": "alpaca_options_paper"}

    horizons = [30, 90, 180]
    rows = scan.scan_horizons(
        records, price_at, cfg=cfg,
        rebalance_dates=["2026-01-05", "2026-02-02", "2026-03-02"], horizons=horizons,
    )
    # one well-formed row per horizon, correct schema, no crash
    assert [r["horizon_days"] for r in rows] == horizons
    keys = {"horizon_days", "n", "n_windows", "overlapping", "ic", "ic_t",
            "win_rate", "mean_net_return", "edge_vs_baseline", "conv_spread"}
    for r in rows:
        assert keys <= set(r)
        assert isinstance(r["n"], int) and r["n"] >= 1
        assert r["overlapping"] is True     # shared rebalance dates (legacy mode)
    # summarize consumes the rows and yields a valid verdict
    assert scan.summarize(rows)["verdict"] in {
        "monetizable_horizon_found", "monetizable_horizon_overlap_only",
        "no_monetizable_horizon", "no_data",
    }


def test_scan_horizons_non_overlapping_uses_per_horizon_dates():
    # A per-horizon rebalance-date callable marks rows non-overlapping and can hand
    # each horizon its own (differently-spaced) dates.
    records = [_snap("SPY", 0.9, d) for d in ("2026-01-05", "2026-02-02", "2026-03-02")]
    panels = {"SPY": [("2026-01-05", 100.0), ("2026-02-02", 104.0), ("2026-03-02", 106.0),
                      ("2026-06-01", 120.0), ("2026-09-01", 130.0)]}
    price_at = scan.make_price_at(panels)
    cfg = {"min_conviction": 0.4, "universe": ["SPY"],
           "express_as": "debit_vertical", "account": "alpaca_options_paper"}
    seen = {}

    def dates_for(h):
        seen[h] = ["2026-01-05"] if h >= 90 else ["2026-01-05", "2026-02-02"]
        return seen[h]

    rows = scan.scan_horizons(records, price_at, cfg=cfg, rebalance_dates=["x"],
                              horizons=[30, 90], rebalance_dates_for=dates_for)
    assert set(seen) == {30, 90}                    # the callable drove each horizon
    assert all(r["overlapping"] is False for r in rows)
    assert dict((r["horizon_days"], r["n_windows"]) for r in rows)[90] == 1
