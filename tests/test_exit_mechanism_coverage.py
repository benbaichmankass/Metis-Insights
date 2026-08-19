"""Tests for the exit-mechanism coverage probe.

What is worth pinning is what would let this report a confident WRONG clean
result — because "no orphaned declares" is the kind of answer a reader acts on
by not looking further:

* a probe that cannot detect a known positive (so everything reads clean),
* a leg silently dropped from the denominator instead of marked `unresolved`,
* two disagreeing unit resolutions quietly resolved by picking one,
* `not_implemented` collapsed with `undeclared` — opposite statements about
  whose choice it was.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_SCRIPT = REPO / "scripts" / "ops" / "exit_mechanism_coverage.py"

spec = importlib.util.spec_from_file_location("exit_mechanism_coverage", _SCRIPT)
emc = importlib.util.module_from_spec(spec)
sys.modules["exit_mechanism_coverage"] = emc
spec.loader.exec_module(emc)


class TestTheProbeCanSeeAPositive:
    """A coverage probe that cannot find a known positive proves nothing.

    These read the REAL modules deliberately. A fixture would pass forever
    while the thing being measured drifted underneath it.
    """

    def test_trend_donchian_implements_all_four(self):
        src = (REPO / "src/units/strategies/trend_donchian.py").read_text()
        for mech in emc.MECHANISMS:
            assert emc.module_implements(src, mech), f"{mech} not detected"

    def test_htf_pullback_coverage_is_three_of_four(self):
        """The measured asymmetry — UPDATED 2026-08-18, and this test is why.

        It previously asserted `only_trail_decay` and carried the message "the
        coverage finding is stale and the docs quoting it must be re-measured".
        The shared-lever extraction (src/runtime/exit_levers.py) made that true
        and this tripwire caught it in CI, which is exactly what it was planted
        for. The docs it pointed at were re-measured in the same change:
        exit_mechanism_coverage's own docstring corrected, and the two
        2026-08-16 records annotated forward rather than rewritten.

        Kept as a REAL tripwire, not softened into "implements at least one":
        exit_head is still genuinely absent (it needs an advisory-stage trained
        head this family does not have), so the asymmetry is now 3-of-4 and a
        future change to EITHER side must come back through here.
        """
        src = (REPO / "src/units/strategies/htf_pullback_trend_2h.py").read_text()
        for mech in ("trail_decay", "stale_stop", "giveback_stop"):
            assert emc.module_implements(src, mech), (
                f"htf_pullback_trend_2h LOST {mech} — the shared-lever wiring "
                f"in src/runtime/exit_levers.py is broken, or the detector "
                f"stopped following the import")
        assert not emc.module_implements(src, "exit_head"), (
            "htf_pullback_trend_2h now implements exit_head — the 3-of-4 "
            "coverage finding is stale and the docs quoting it must be "
            "re-measured")

    def test_a_module_with_none_of_them_reads_as_none(self):
        assert not any(emc.module_implements("def monitor(): pass", m)
                       for m in emc.MECHANISMS)


class TestUnitResolution:
    def test_both_witnesses_agree_on_a_known_leg(self):
        src = (REPO / "src/runtime/strategy_signal_builders.py").read_text()
        unit, basis = emc.unit_of(src, "xrp_pullback_2h")
        assert unit == "htf_pullback_trend_2h"
        assert basis in ("registration_table", "builder_import")

    def test_a_conflict_resolves_to_none_not_to_a_guess(self):
        """Two witnesses disagreeing is 'we could not establish it'.

        Picking one would report a confident wrong module, and every mechanism
        verdict downstream inherits that error silently.
        """
        src = ('def foo_signal_builder(settings):\n'
               '    from src.units.strategies.trend_donchian import order_package\n'
               'REG = [(foo_signal_builder, "htf_pullback_trend_2h")]\n')
        unit, basis = emc.unit_of(src, "foo")
        assert unit is None
        assert basis.startswith("conflict:")

    def test_an_unknown_leg_is_not_silently_resolved(self):
        unit, basis = emc.unit_of("", "nonexistent_leg")
        assert unit is None and basis == "no_builder_found"


class TestStatesAreNotCollapsed:
    def test_not_implemented_and_undeclared_are_distinct(self):
        """Opposite statements: the module can't, versus the leg chose not to.

        Collapsing them turns a family coverage gap into a config preference.
        """
        assert emc.NOT_IMPLEMENTED != emc.UNDECLARED

    def test_all_five_states_are_distinct(self):
        states = {emc.NOT_IMPLEMENTED, emc.UNDECLARED, emc.DECLARED,
                  emc.ORPHANED, emc.UNRESOLVED}
        assert len(states) == 5


class TestTheLiveAudit:
    """Runs against the real config — the point is to catch drift, not to
    freeze today's numbers into an assertion that has to be edited."""

    def test_audit_runs_and_grades_every_live_leg(self):
        res = emc.audit()
        assert res["live_leg_count"] > 0
        for row in res["legs"]:
            assert set(row["mechanisms"]) == set(emc.MECHANISMS)

    def test_no_orphaned_declares(self):
        """The finding, as a standing assertion.

        If this fails, a live leg declares an exit lever its own unit module
        never reads — silently inert, and INVISIBLE to
        `check_lever_reachability.py`, which only compares arm_r to cap_R.
        """
        res = emc.audit()
        assert not res["orphans"], (
            "orphaned exit-lever declares: "
            + ", ".join(f"{s}:{m}" for s, m in res["orphans"]))

    def test_unresolved_legs_are_reported_not_dropped(self):
        """An ungradeable leg must stay countable.

        Dropping it would make the orphan count read as full coverage — the
        unasserted-denominator failure, where a clean answer is clean only
        over the rows that happened to resolve.
        """
        res = emc.audit()
        graded = {r["strategy"] for r in res["legs"]}
        assert set(res["unresolved"]) <= graded

    def test_self_test_passes(self):
        assert emc._self_test() == 0


class TestExitCodes:
    def test_orphans_only_exits_zero_when_clean(self):
        if emc.audit()["orphans"]:
            pytest.skip("orphans exist; the clean-exit contract is untestable here")
        assert emc.main(["--orphans-only"]) == 0
