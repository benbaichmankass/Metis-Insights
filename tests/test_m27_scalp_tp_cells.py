"""The `bracket_geometry` target grid and the live-parity timeout axis (MI-278 U3).

These lock the two properties that make the new cells trustworthy:

  1. **THE GRID BRACKETS THE LIVE VALUE ON BOTH SIDES.** MI-277's question is
     "are winners being cut short", which biases an author toward testing only
     WIDER targets — and a one-sided grid cannot distinguish "further is better"
     from "we only looked further".
  2. **THE LIVE VALUE IS NEVER EMITTED AS A CELL.** It is the baseline arm, so a
     cell equal to it compares the baseline against itself and reports a
     confident zero — the same provable-no-op shape `_RUNG_FRACS_OF_TP` already
     avoids one lever up, and the shape `bank_rung_state` stamps in the harness.

Plus the timeout axis, whose DEFAULT must stay "pass nothing". Changing it to
parity would silently re-grade every M27 scalp cell already in the coverage
matrix against a different book — the population-mixing the module's own
dataset header warns about.
"""
from __future__ import annotations

import pytest

import scripts.research.m27.ict_scalp_exit_sweep as sw


class TestTargetGrid:
    def test_grid_brackets_the_live_value_on_both_sides(self):
        live = 1.5                       # every live ict_scalp leg, 2026-09-12
        assert any(t < live for t in sw._TP_GRID), "no arm BELOW the live target"
        assert any(t > live for t in sw._TP_GRID), "no arm ABOVE the live target"

    def test_the_live_value_is_not_emitted_as_a_cell(self):
        tags = [t for t, _, _ in sw.tp_cells(1.5)]
        assert "tp1.5R" not in tags, (
            "a cell equal to the baseline measures the baseline against itself "
            "and reports a confident zero")
        assert len(tags) == len(sw._TP_GRID) - 1

    @pytest.mark.parametrize("live", [0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0])
    def test_the_exclusion_follows_THIS_leg_s_value_not_a_hardcoded_1_5(self, live):
        """A leg whose tp_at_r is not 1.5 must exclude ITS OWN value."""
        tags = [t for t, _, _ in sw.tp_cells(live)]
        assert f"tp{live:g}R" not in tags
        assert len(tags) == len(sw._TP_GRID) - 1

    def test_a_leg_off_the_grid_gets_every_arm(self):
        tags = [t for t, _, _ in sw.tp_cells(1.7)]
        assert len(tags) == len(sw._TP_GRID)

    def test_every_cell_targets_the_bracket_geometry_column(self):
        assert {lever for _, lever, _ in sw.tp_cells(1.5)} == {"bracket_geometry"}

    def test_every_cell_passes_the_flag_the_harness_actually_exposes(self):
        for _, _, extra in sw.tp_cells(1.5):
            assert extra[0] == "--tp-at-r" and len(extra) == 2
            float(extra[1])              # parses as a number

    def test_cells_are_included_in_the_swept_set(self):
        combined = list(sw.CELLS) + sw.ladder_cells(1.5) + sw.tp_cells(1.5)
        assert "bracket_geometry" in {lever for _, lever, _ in combined}, (
            "a cell family that is never added to the swept set is a dead feature")


class TestTimeoutParityAxis:
    def test_default_passes_NOTHING_so_existing_cells_are_unchanged(self):
        flags = sw.base_flags("SOLUSDT", "5m")
        assert "--timeout-bars" not in flags, (
            "defaulting to parity would silently re-grade every M27 scalp cell "
            "in the coverage matrix against a different book")

    def test_explicit_value_is_passed_through(self):
        flags = sw.base_flags("SOLUSDT", "5m", None, 100000)
        assert flags[flags.index("--timeout-bars") + 1] == "100000"

    def test_zero_is_passed_rather_than_treated_as_unset(self):
        """0 is a real value; conflating it with None is a collapsed state."""
        flags = sw.base_flags("SOLUSDT", "5m", None, 0)
        assert "--timeout-bars" in flags
        assert flags[flags.index("--timeout-bars") + 1] == "0"

    def test_declared_levers_survive_the_new_argument(self):
        flags = sw.base_flags("ETHUSDT", "15m", ["--stale-exit-bars", "12"], 100000)
        assert "--stale-exit-bars" in flags and "--timeout-bars" in flags
        assert "--sim-breakeven" in flags
