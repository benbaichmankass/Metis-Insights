"""The ONE definition of the backtest risk basis (src/research/risk_basis.py).

Context: the operator's own example of the modularity failure —
"the back test risk and the live config don't match ... it needs to, in any
case, check various different risk percentages."

Measured 2026-08-20: live `risk_pct: 0.015` is a FRACTION (1.5%);
`backtest_system.py::_risk_qty` divides by 100, so its `--risk-pct 0.3`
default is 0.3% — one FIFTH of live. These tests pin the contract that
makes that undetectable-by-construction gap detectable.
"""
from __future__ import annotations

import pytest

from src.research.risk_basis import (
    DEFAULT_GRID_MULTIPLIERS,
    DEFAULT_REFERENCE_ACCOUNT,
    STATE_ACCOUNT_ABSENT,
    STATE_RESOLVED,
    STATE_UNREADABLE,
    UNIT_FRACTION,
    UNIT_PERCENT,
    compare_to_live,
    live_risk,
    risk_grid_percent,
    to_fraction,
    to_percent,
)


class TestUnits:
    """The conversion that must never be re-spelled as a bare `/ 100.0`."""

    def test_round_trips(self):
        assert to_fraction(to_percent(0.015)) == pytest.approx(0.015)
        assert to_percent(to_fraction(1.5)) == pytest.approx(1.5)

    def test_the_live_value_converts_to_the_harness_unit(self):
        # 0.015 fraction IS 1.5 percent. Reading one as the other is the
        # 100x error the guard's FILE_UNITS map exists to prevent.
        assert to_percent(0.015) == pytest.approx(1.5)

    def test_the_two_unit_names_are_distinct(self):
        assert UNIT_FRACTION != UNIT_PERCENT


class TestLiveRisk:
    def test_resolves_against_the_real_config(self):
        lr = live_risk(DEFAULT_REFERENCE_ACCOUNT)
        assert lr.state == STATE_RESOLVED, lr.detail
        assert lr.ok
        assert lr.fraction and 0 < lr.fraction < 1, "live risk_pct is a FRACTION"
        assert lr.percent == pytest.approx(to_percent(lr.fraction))
        assert lr.source and "accounts.yaml" in lr.source

    def test_describe_names_the_source_not_just_the_number(self):
        """A value with no provenance is how a stale default survives."""
        d = live_risk(DEFAULT_REFERENCE_ACCOUNT).describe()
        assert "accounts.yaml" in d and DEFAULT_REFERENCE_ACCOUNT in d

    def test_an_unknown_account_is_ABSENT_not_a_default(self):
        lr = live_risk("no_such_account_xyz")
        assert lr.state == STATE_ACCOUNT_ABSENT
        assert lr.fraction is None and lr.percent is None
        assert not lr.ok

    def test_an_unreadable_config_is_UNREADABLE_not_absent(self, tmp_path):
        """'we could not look' and 'we looked and it is not there' differ."""
        lr = live_risk(DEFAULT_REFERENCE_ACCOUNT,
                       accounts_path=tmp_path / "nope.yaml")
        assert lr.state == STATE_UNREADABLE
        assert lr.state != STATE_ACCOUNT_ABSENT
        assert lr.fraction is None

    def test_a_garbled_config_is_UNREADABLE(self, tmp_path):
        bad = tmp_path / "accounts.yaml"
        bad.write_text("accounts: [this, is, a, list, not, a, mapping]\n")
        lr = live_risk(DEFAULT_REFERENCE_ACCOUNT, accounts_path=bad)
        assert lr.state == STATE_UNREADABLE

    def test_there_is_NO_fallback_constant(self, tmp_path):
        """The whole point. A silent default is how 0.3 sat 5x below live."""
        lr = live_risk(DEFAULT_REFERENCE_ACCOUNT, accounts_path=tmp_path / "gone.yaml")
        assert lr.fraction is None and lr.percent is None


class TestGrid:
    def test_the_grid_brackets_live(self, ):
        grid, live = risk_grid_percent(DEFAULT_REFERENCE_ACCOUNT)
        assert live.ok and grid
        assert min(grid) < live.percent < max(grid), (
            "a sweep that does not straddle live cannot show whether a "
            "conclusion survives a change in risk")
        assert any(g == pytest.approx(live.percent) for g in grid), (
            "the sweep must INCLUDE live, or it never answers what production does")

    def test_the_grid_is_in_the_harness_unit(self):
        grid, live = risk_grid_percent(DEFAULT_REFERENCE_ACCOUNT)
        assert grid and live.percent
        # percent, not fraction: values are of order 1, not 0.01.
        assert all(g > live.fraction for g in grid)

    def test_no_grid_when_live_is_unknown(self, tmp_path):
        """Never sweep around a basis that was never read."""
        grid, live = risk_grid_percent(DEFAULT_REFERENCE_ACCOUNT,
                                       accounts_path=tmp_path / "gone.yaml")
        assert grid is None
        assert live.state == STATE_UNREADABLE

    def test_multipliers_are_sane(self):
        assert 1.0 in DEFAULT_GRID_MULTIPLIERS
        assert all(m > 0 for m in DEFAULT_GRID_MULTIPLIERS)


class TestCompareToLive:
    def test_the_fleet_default_is_reported_as_one_fifth_of_live(self):
        """The measured finding, pinned so a silent re-drift shows up here."""
        out = compare_to_live(0.3)
        assert out["verdict"] == "differs_from_live"
        assert out["ratio"] == pytest.approx(0.2, rel=1e-3), (
            "backtest_system.py's --risk-pct 0.3 (percent) against live 1.5%")

    def test_live_matches_itself(self):
        live = live_risk(DEFAULT_REFERENCE_ACCOUNT)
        out = compare_to_live(live.percent)
        assert out["verdict"] == "matches_live"
        assert out["ratio"] == pytest.approx(1.0)

    def test_unknown_live_is_NOT_reported_as_a_match(self, tmp_path):
        out = compare_to_live(1.5, accounts_path=tmp_path / "gone.yaml")
        assert out["verdict"] == "live_unknown"
        assert out["verdict"] != "matches_live"
        assert out["ratio"] is None

    def test_the_output_carries_its_own_provenance(self):
        out = compare_to_live(0.3)
        assert "accounts.yaml" in out["describe"]
