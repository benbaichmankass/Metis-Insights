"""M28 Phase A3 — test the combined construction grader (S2 signal + S3 PnL, one call).

Fixture-driven (no network): synthetic snapshots + candle panels → grade() → assert the
combined scorecard shape + a valid verdict, and that S2 and S3 both ran on the same
entries. Reuses the same minimal fixture pattern as the horizon-IC scan test.
"""

from __future__ import annotations

import os
import sys

_MACRO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "macro")
sys.path.insert(0, _MACRO)

import grade_construction as gc  # noqa: E402


def _snap(symbol, cheap_score, observed_at):
    return {
        "symbol": symbol, "metric": "erp", "value": 1.0,
        "cheap_score": cheap_score, "label": "cheap" if cheap_score >= 0.5 else "rich",
        "higher_is_cheaper": True, "n_history": 60, "percentile": cheap_score,
        "z_score": 0.0, "observed_at": observed_at, "as_of": observed_at,
        "source": "test", "asset_class": "equity", "inputs": {}, "note": "",
    }


def test_grade_runs_s2_and_s3_and_returns_valid_verdict():
    dates = ["2026-01-05", "2026-02-02", "2026-03-02", "2026-04-06", "2026-05-04", "2026-06-01"]
    # SPY reads cheap (→ long), CASH reads rich (→ short). SPY rises, CASH flat.
    records = ([_snap("SPY", 0.9, d) for d in dates] + [_snap("CASH", 0.1, d) for d in dates])
    spy = [(d, 100.0 + 4.0 * i) for i, d in enumerate(dates + ["2026-09-01"])]
    cash = [(d, 100.0) for d in dates + ["2026-09-01"]]
    panels = {"SPY": spy, "CASH": cash}
    price_at = gc.make_price_at(panels)
    cfg = {"min_conviction": 0.4, "universe": ["SPY", "CASH"],
           "express_as": "debit_vertical", "account": "alpaca_options_paper"}

    card = gc.grade(records, price_at, cfg=cfg, rebalance_every=30, horizons=[30, 60, 90],
                    pnl_horizon=30, oos_frac=0.5)

    # combined scorecard shape
    assert set(card) >= {"verdict", "worth_building", "s2_signal", "s3_pnl", "meta"}
    assert card["verdict"] in {"worth_building", "signal_but_no_pnl", "pnl_but_no_signal", "no_edge"}
    assert isinstance(card["worth_building"], bool)
    # S2 ran across the horizons
    assert [r["horizon_days"] for r in card["s2_signal"]["rows"]] == [30, 60, 90]
    assert "any_honest_monetizable_horizon" in card["s2_signal"]["summary"]
    # S3 produced the three books + the pays_oos gate
    s3 = card["s3_pnl"]
    assert {"conviction_weighted", "long_short_neutral", "baseline_all_long", "summary"} <= set(s3)
    assert "pays_oos" in s3["summary"]
    # worth_building is exactly the AND of the two gates
    assert card["worth_building"] == (
        bool(card["s2_signal"]["summary"].get("any_honest_monetizable_horizon"))
        and bool(s3["summary"].get("pays_oos"))
    )


def test_grade_empty_records_is_safe():
    card = gc.grade([], gc.make_price_at({}), cfg={"universe": [], "min_conviction": 0.4,
                    "express_as": "debit_vertical", "account": "alpaca_options_paper"},
                    rebalance_every=30, horizons=[30], pnl_horizon=30)
    assert card["verdict"] == "no_edge" and card["worth_building"] is False
