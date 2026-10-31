"""ML-LINE-S1-CORPUS producer: money-leg discovery from config, family keys, station metrics."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "scripts" / "research"), str(REPO / "scripts" / "ops"), str(REPO / "scripts")]

import leg_corpus_s1 as s1  # noqa: E402
import strategy_status as ss  # noqa: E402


def test_self_test_passes():
    assert s1._self_test() == 0


def test_money_legs_are_exactly_production_or_prop_and_every_one_has_a_family():
    legs = s1.money_legs()
    assert legs, "no money legs parsed from config"
    t = ss.table()
    for leg in legs:
        assert t[leg]["status"] == ss.PRODUCTION or leg in legs  # prop legs are money legs without 'production'
    assert all(t[l]["status"] != ss.INACTIVE for l in legs)
    fams = s1.money_families()
    assert sorted(l for v in fams.values() for l in v) == sorted(legs)      # every leg in exactly one family
    assert all(f.rsplit("_", 1)[-1] == legs[v[0]]["timeframe"] for f, v in fams.items())  # key carries config tf


def test_timeframe_label_regression_and_cost_gap_are_not_covered():
    from datetime import datetime, timezone
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    cfg = {"timeframe": "4h", "symbols": ["ETHUSDT"], "donchian": 20}
    m = s1.leg_meta("a", cfg, "trend_4h", [{"cost_fee_r": 0, "cost_slippage_r": 0, "cost_funding_r": 0}],
                    {"timeframe": "1h", "params": {"donchian": 20}}, now)       # the eth_4h/xrp_4h defect shape
    sm = s1.station_metrics(["a"], {"a": m}, now)
    assert sm["coverage"]["covered"] == 0 and sm["coverage"]["not_covered"]["a"] == "timeframe mismatch"
