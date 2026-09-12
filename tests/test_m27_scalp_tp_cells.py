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


class TestBreakevenCells:
    """DISARMING the break-even ratchet — the opposite polarity to every other cell.

    The ratchet rides in BASE_FLAGS as `--sim-breakeven` because it is part of
    the shipped config, so the only way to make it a VARIABLE is to remove it.
    That means `run_cell` has to support subtraction, and the cell has to
    refuse to exist where it cannot bind.
    """

    def test_no_op_below_the_arming_threshold_is_not_emitted(self):
        """A cell that measures exactly zero would read as a tested negative."""
        for tp in (0.5, 0.75, 1.0):
            assert sw.breakeven_cells(tp) == [], (
                f"tp_at_r={tp} exits before 1R so the ratchet can never arm; "
                f"emitting the cell would report a provable no-op as a result")

    def test_emitted_where_it_can_bind(self):
        for tp in (1.25, 1.5, 2.0, 3.0):
            cells = sw.breakeven_cells(tp)
            assert len(cells) == 1
            tag, lever, extra = cells[0]
            assert lever == "breakeven_ratchet"
            assert extra == ["--no-sim-breakeven"]

    def test_the_threshold_is_the_ARMING_point_not_a_hardcoded_1_5(self):
        """1.0 is the arm point; 1.5 is merely today's live value."""
        assert sw._BE_ARM_AT_R == 1.0
        assert sw.breakeven_cells(1.0) == []
        assert len(sw.breakeven_cells(1.01)) == 1

    def test_cells_reach_the_swept_set(self):
        """Reads the PRODUCTION assembly, never a copy of it.

        An earlier version of this test rebuilt the list itself, so a mutant
        dropping `breakeven_cells` from `main`'s assembly stayed green — the
        exact copy-the-logic defect that made two of this session's other
        tests non-load-bearing.
        """
        levers = {lev for _, lev, _ in sw.all_cells(1.5)}
        assert "breakeven_ratchet" in levers, (
            "the family exists but the sweep never runs it")
        # and the sibling families are still there — a mutant that swapped
        # the assembly for `breakeven_cells(...)` alone must not pass.
        assert {"bracket_geometry", "exit_ladder", "stale_stop",
                "giveback_stop"} <= levers


class TestRunCellSubtraction:
    """`--no-<flag>` REMOVES a base flag. An unknown one must fail loudly."""

    def test_removal_strips_the_base_flag(self, tmp_path, monkeypatch):
        seen = {}

        def _spy(cmd, capture_output, text):
            seen["cmd"] = cmd
            class R:
                returncode = 0
                stderr = ""
            (tmp_path / "o.json").write_text("{}")
            return R()

        monkeypatch.setattr(sw, "BASE_FLAGS",
                            ["--symbol", "SOLUSDT", "--timeframe", "5m", "--sim-breakeven"])
        monkeypatch.setattr(sw.subprocess, "run", _spy)
        sw.run_cell(tmp_path / "d.csv", ["--no-sim-breakeven"], tmp_path / "o.json")
        assert "--sim-breakeven" not in seen["cmd"], "the base flag was not removed"
        assert "--no-sim-breakeven" not in seen["cmd"], (
            "the marker leaked to the harness, which would exit non-zero and "
            "read as a failed cell rather than a broken sweep")

    def test_removing_a_flag_that_is_not_in_the_base_is_an_ERROR(self, tmp_path, monkeypatch):
        """The refusal must come from the GUARD, not from a harness that failed anyway.

        This test used to let `subprocess.run` reach the real harness with a
        non-existent CSV, so a mutant that silently ignored the unremovable
        flag still produced an `error` key — from the missing file — and the
        test passed for the wrong cause. It survived the mutation check.
        The spy below returns SUCCESS, so the only route to an error is the
        guard, and the message must name the flag it refused.
        """
        ran = {"called": False}

        def _spy(cmd, capture_output, text):
            ran["called"] = True
            class R:
                returncode = 0
                stderr = ""
            (tmp_path / "nope.json").write_text("{}")
            return R()

        monkeypatch.setattr(sw, "BASE_FLAGS", ["--symbol", "SOLUSDT"])
        monkeypatch.setattr(sw.subprocess, "run", _spy)
        out = sw.run_cell(tmp_path / "d.csv", ["--no-sim-breakeven"], tmp_path / "nope.json")
        assert "error" in out, (
            "silently ignoring an unremovable flag would run the BASE arm and "
            "report it as the disarmed one — the two would be indistinguishable")
        assert "--sim-breakeven" in out["error"], (
            f"the refusal must name the flag it could not remove: {out['error']!r}")
        assert not ran["called"], (
            "the harness was invoked despite the refusal — the BASE arm ran")

    def test_ordinary_additive_cells_are_untouched(self, tmp_path, monkeypatch):
        seen = {}

        def _spy(cmd, capture_output, text):
            seen["cmd"] = cmd
            class R:
                returncode = 0
                stderr = ""
            (tmp_path / "o2.json").write_text("{}")
            return R()

        monkeypatch.setattr(sw, "BASE_FLAGS",
                            ["--symbol", "SOLUSDT", "--sim-breakeven"])
        monkeypatch.setattr(sw.subprocess, "run", _spy)
        sw.run_cell(tmp_path / "d.csv", ["--tp-at-r", "2.0"], tmp_path / "o2.json")
        assert "--sim-breakeven" in seen["cmd"]
        assert "--tp-at-r" in seen["cmd"] and "2.0" in seen["cmd"]
