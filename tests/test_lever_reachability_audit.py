"""Tests for the reach-gate reachability audit.

What is worth pinning is what would let this report a confident wrong answer:
grading a lever "reachable" on no evidence, defaulting a missing stop to zero
risk (which makes every cap_R infinite and every lever look fine), collapsing
"we did not look" into "we looked and it is fine", and the live cap constant
drifting away from the one production clamps with.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "ops"))

from lever_reachability_audit import (  # noqa: E402
    LIVE_TP_CAP_PCT, audit, cap_applies, cap_r, grade_leg,
    observed_risk_ratios, required_risk_pct)


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
        assert [o["ratio"] for o in got] == [0.02, 0.02]


class TestRiskBasis:
    """`entry - stop_loss` is NOT reliably the entry risk — a stop is trailed.

    Measured on the live fleet 2026-08-16 (diag #9587): gld/qqq packages agreed
    with `risk_per_unit` to 1.00, xrp's ratio was 0.71, one trend_donchian
    package read 5.7x tighter than its own recorded risk. The error runs the
    dangerous way — understated risk inflates cap_R and makes an inert lever
    look reachable — so the sized value must win and the basis must be visible.
    """

    def _rpu_row(self, entry=1.0806, rpu=0.02726786, sl=1.04193571):
        return {"strategy": "xrp_pullback_2h", "entry": entry, "sl": sl,
                "signalLogic": {"risk_per_unit": rpu}}

    def test_risk_per_unit_wins_over_a_trailed_stop(self):
        got = observed_risk_ratios([self._rpu_row()], "xrp_pullback_2h")
        assert got[0]["basis"] == "risk_per_unit"
        # the trailed stop would have given 3.578%, the sized risk gives 2.523%
        assert abs(got[0]["ratio"] - 0.025233) < 1e-5

    def test_the_fallback_error_has_no_fixed_SIGN(self):
        """Why the sized value must win rather than be bias-corrected.

        A stop trailed INTO profit sits closer to the exit than to entry, so
        `|entry - stop|` OVERSTATES risk and DEFLATES cap_R (the live xrp short:
        3.578% vs a sized 2.523%, cap_R 2.77R vs 3.92R). A stop amended TIGHTER
        than the sizer's understates risk and INFLATES cap_R (the trend_donchian
        package, 5.7x). Same field, opposite errors — so there is no correction
        factor, only the right field.
        """
        sized = observed_risk_ratios([self._rpu_row()], "xrp_pullback_2h")[0]
        assert abs(cap_r(sized["ratio"]) - 3.923) < 0.01

        trailed_into_profit = self._rpu_row()
        trailed_into_profit.pop("signalLogic")
        deflated = observed_risk_ratios(
            [trailed_into_profit], "xrp_pullback_2h")[0]
        assert cap_r(deflated["ratio"]) < cap_r(sized["ratio"])

        amended_tighter = self._rpu_row(sl=1.0806 - 0.0048)
        amended_tighter.pop("signalLogic")
        inflated = observed_risk_ratios(
            [amended_tighter], "xrp_pullback_2h")[0]
        assert cap_r(inflated["ratio"]) > cap_r(sized["ratio"])

        # BOTH verdicts would be wrong, in opposite directions, against arm 4.49
        assert cap_r(inflated["ratio"]) > 4.49 > cap_r(sized["ratio"])

    def test_fallback_is_used_and_labelled_when_risk_per_unit_is_absent(self):
        got = observed_risk_ratios([_row()], "xrp_pullback_2h")
        assert got[0]["basis"] == "entry_minus_stop"

    def test_a_non_positive_risk_per_unit_falls_back_rather_than_dropping(self):
        row = self._rpu_row()
        row["signalLogic"] = {"risk_per_unit": 0.0}
        got = observed_risk_ratios([row], "xrp_pullback_2h")
        assert len(got) == 1 and got[0]["basis"] == "entry_minus_stop"

    def test_the_basis_mix_is_reported_per_leg(self):
        rows = [self._rpu_row(), self._rpu_row(), _row()]
        rec = grade_leg("xrp_pullback_2h", _cfg(), rows=rows)[0]
        assert rec["risk_basis_risk_per_unit"] == 2
        assert rec["risk_basis_entry_minus_stop"] == 1
        assert rec["observations"] == 3


class TestVerdictsAreNeverCollapsed:
    def test_no_observations_is_unmeasured_not_reachable(self):
        recs = grade_leg("xrp_pullback_2h", _cfg(), rows=[])
        assert len(recs) == 1
        assert recs[0]["reachability"] == "unmeasured"
        assert recs[0]["reach_share_pct"] is None

    def test_an_unresolvable_leg_is_cap_unknown_not_reachable(self):
        """No family match AND no signal builder => we cannot establish a cap.
        That is NOT 'uncapped', and must never grade as reachable."""
        recs = grade_leg("mystery_leg_9000", _cfg(), rows=[])
        assert recs[0]["reachability"] == "cap_unknown"
        assert recs[0]["cap_applies"] is None
        assert recs[0]["cap_basis"] == "no_builder_found"

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
            grade_leg("mystery_leg_9000", _cfg(), rows=[])[0]["reachability"],
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


class TestCapResolution:
    """The family string is not the only evidence a leg is capped."""

    def test_family_match_resolves_capped(self):
        capped, basis = cap_applies("xrp_pullback_2h")
        assert capped is True and basis == "family"

    def test_an_equity_leg_resolves_through_its_builders_unit(self):
        """`qqq_trend_long_1d` matches no family tag, but its signal builder
        imports `order_package` from `trend_donchian`, which clamps. Verified
        2026-08-16 — a family-only test under-claimed on every equity leg."""
        capped, basis = cap_applies("qqq_trend_long_1d")
        assert capped is True
        assert basis == "builder_unit:trend_donchian"

    def test_an_unknown_leg_resolves_to_none_not_false(self):
        capped, basis = cap_applies("mystery_leg_9000")
        assert capped is None, "unproven absence must not read as 'uncapped'"
        assert basis == "no_builder_found"


class TestConstantsDoNotDrift:
    def test_the_audit_cap_IS_the_owner_not_a_copy(self):
        """Was a source regex against a literal; now an identity check.

        The audit no longer carries its own number, so "does the audit agree
        with production" is answered by object identity rather than by parsing
        the strategy file and comparing floats.
        """
        from src.runtime.tp_venue_cap import TP_VENUE_CAP_PCT
        assert LIVE_TP_CAP_PCT is TP_VENUE_CAP_PCT

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
