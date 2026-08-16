"""Tests for the reach-gate reachability audit.

What is worth pinning is what would let this report a confident wrong answer:
grading a lever "reachable" on no evidence, defaulting a missing stop to zero
risk (which makes every cap_R infinite and every lever look fine), collapsing
"we did not look" into "we looked and it is fine", and the live cap constant
drifting away from the one production clamps with.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "ops"))

from lever_reachability_audit import (  # noqa: E402
    LIVE_TP_CAP_PCT, audit, cap_r, grade_leg, observed_risk_ratios,
    required_risk_pct)


def _cfg(arm_r=4.49, **kw):
    base = {"enabled": True, "execution": "live", "atr_stop_mult": 2.5,
            "tp_r": 50.0, "trail_decay_arm_r": arm_r}
    base.update(kw)
    return base


def _row(strategy="xrp_pullback_2h", entry=1.0806, sl=1.10786786):
    return {"strategy_name": strategy, "entry_price": entry, "stop_loss": sl}


class TestCapArithmetic:
    def test_cap_r_is_the_cap_over_the_risk_ratio(self):
        # entry 1.0806 / stop 1.10786786 => risk/entry 2.5233%
        assert abs(cap_r(0.025233) - 3.923) < 0.01

    def test_required_risk_pct_is_the_break_even_ratio(self):
        need = required_risk_pct(4.49)
        assert abs(cap_r(need) - 4.49) < 1e-9

    def test_zero_or_negative_risk_yields_no_cap_not_infinity(self):
        assert cap_r(0.0) is None
        assert cap_r(-0.01) is None
        assert required_risk_pct(0.0) is None


class TestObservationsAreNeverFabricated:
    def test_row_missing_a_stop_is_dropped_not_zeroed(self):
        rows = [_row(), {"strategy_name": "xrp_pullback_2h", "entry_price": 1.0},
                {"strategy_name": "xrp_pullback_2h", "stop_loss": 1.1}]
        got = observed_risk_ratios(rows, "xrp_pullback_2h")
        assert len(got) == 1, "a missing price must drop the row, never default it"

    def test_rows_of_another_leg_do_not_leak_in(self):
        rows = [_row(), _row(strategy="trend_donchian", entry=100.0, sl=90.0)]
        assert len(observed_risk_ratios(rows, "xrp_pullback_2h")) == 1

    def test_direction_does_not_matter_risk_is_a_distance(self):
        long_row = _row(entry=100.0, sl=98.0)
        short_row = _row(entry=100.0, sl=102.0)
        got = observed_risk_ratios([long_row, short_row], "xrp_pullback_2h")
        assert got == [0.02, 0.02]


class TestVerdictsAreNeverCollapsed:
    def test_no_observations_is_unmeasured_not_reachable(self):
        recs = grade_leg("xrp_pullback_2h", _cfg(), rows=[])
        assert len(recs) == 1
        assert recs[0]["reachability"] == "unmeasured"
        assert recs[0]["reach_share_pct"] is None

    def test_unknown_family_is_cap_unknown_not_reachable(self):
        recs = grade_leg("qqq_trend_long_1d", _cfg(), rows=[])
        assert recs[0]["reachability"] == "cap_unknown"

    def test_inert_when_every_observed_trade_caps_below_the_arm(self):
        rows = [_row() for _ in range(20)]  # cap_R 3.92 < arm 4.49
        recs = grade_leg("xrp_pullback_2h", _cfg(arm_r=4.49), rows=rows)
        assert recs[0]["reachability"] == "inert"
        assert recs[0]["reach_share_pct"] == 0.0
        assert recs[0]["observations"] == 20

    def test_reachable_when_a_tighter_stop_clears_the_arm(self):
        # risk/entry 1.0% => cap_R 9.9 > arm 4.49
        rows = [_row(entry=100.0, sl=99.0) for _ in range(20)]
        recs = grade_leg("xrp_pullback_2h", _cfg(arm_r=4.49), rows=rows)
        assert recs[0]["reachability"] == "reachable"
        assert recs[0]["reach_share_pct"] == 100.0

    def test_the_four_states_are_distinguishable(self):
        got = {
            grade_leg("xrp_pullback_2h", _cfg(), rows=[])[0]["reachability"],
            grade_leg("qqq_trend_long_1d", _cfg(), rows=[])[0]["reachability"],
            grade_leg("xrp_pullback_2h", _cfg(),
                      rows=[_row()] * 5)[0]["reachability"],
            grade_leg("xrp_pullback_2h", _cfg(),
                      rows=[_row(entry=100.0, sl=99.0)] * 5)[0]["reachability"],
        }
        assert got == {"unmeasured", "cap_unknown", "inert", "reachable"}


class TestAuditScope:
    def test_shadow_and_disabled_legs_are_excluded(self):
        strategies = {
            "a_pullback_2h": _cfg(),
            "b_pullback_2h": _cfg(execution="shadow"),
            "c_pullback_2h": _cfg(enabled=False),
        }
        recs = audit(strategies)
        assert [r["strategy"] for r in recs] == ["a_pullback_2h"]

    def test_a_leg_with_no_reach_gate_emits_nothing(self):
        cfg = {"enabled": True, "execution": "live", "atr_stop_mult": 2.5}
        assert grade_leg("plain_pullback_2h", cfg, rows=[]) == []

    def test_a_below_r_gate_is_not_treated_as_a_reach_gate(self):
        """stale_exit_below_r fires when R falls BELOW a level — the opposite
        direction. Grading it against the cap would be a category error."""
        cfg = {"enabled": True, "execution": "live", "stale_exit_below_r": 0.5}
        assert grade_leg("x_pullback_2h", cfg, rows=[]) == []


class TestConstantsDoNotDrift:
    def test_live_cap_matches_the_strategy_source(self):
        for mod in ("trend_donchian.py", "htf_pullback_trend_2h.py"):
            src = (REPO / "src" / "units" / "strategies" / mod).read_text()
            m = re.search(r"_TP_SENTINEL_CAP_PCT\s*=\s*([0-9.]+)", src)
            assert m, f"could not find _TP_SENTINEL_CAP_PCT in {mod}"
            assert abs(float(m.group(1)) - LIVE_TP_CAP_PCT) < 1e-9, (
                f"audit uses {LIVE_TP_CAP_PCT}, {mod} uses {m.group(1)}")

    def test_the_live_config_still_parses_and_grades(self):
        """Smoke: the real YAML must produce records, else the audit is silently
        auditing nothing (an unasserted denominator)."""
        import yaml
        doc = yaml.safe_load((REPO / "config" / "strategies.yaml").read_text())
        recs = audit(doc.get("strategies", doc))
        assert len(recs) >= 1, "live config declares no reach gates — verify"
        assert all(r["reachability"] in
                   ("unmeasured", "cap_unknown", "inert", "reachable")
                   for r in recs)
