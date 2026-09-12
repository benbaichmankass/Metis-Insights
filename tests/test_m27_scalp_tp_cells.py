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


class TestTimeoutShareFidelity:
    """Every cell verdict carries the share of its trades the HARNESS killed.

    Production has no time exit on any ict_scalp leg, so a `timeout` trade is
    one the live leg would still have held. The share is not constant across
    the grid — measured on SOLUSDT 5m it rises monotonically 21.4% (0.75R) to
    58.5% (4R) — so a verdict printed without it cannot be told apart from one
    measured at parity.
    """

    def test_share_is_computed_from_the_outcome_map(self):
        w = {"trades": 40, "by_outcome": {"tp_hit": 10, "sl_hit": 10, "timeout": 20}}
        assert sw.timeout_share(w) == 0.5

    def test_absent_timeout_key_is_a_real_zero(self):
        """No timeouts IS a measurement — distinct from not having looked."""
        w = {"trades": 8, "by_outcome": {"tp_hit": 8}}
        assert sw.timeout_share(w) == 0.0

    def test_we_did_not_look_is_None_and_never_0(self):
        """0.0 would read as PERFECT fidelity — the most flattering wrong answer."""
        for w in ({"trades": 10},                      # no outcome map
                  {"trades": 0, "by_outcome": {}},     # no trades
                  {},                                  # nothing at all
                  {"trades": 5, "by_outcome": None}):  # unusable map
            assert sw.timeout_share(w) is None, w

    def test_suffix_renders_both_windows(self):
        cell = {"IS":  {"trades": 10, "by_outcome": {"timeout": 2}},
                "OOS": {"trades": 10, "by_outcome": {"timeout": 5}}}
        s = sw._fidelity_suffix(cell)
        assert "IS 20%" in s and "OOS 50%" in s

    def test_suffix_says_unknown_rather_than_omitting_it(self):
        """A silently absent fidelity reads as a verdict with nothing to declare."""
        cell = {"IS": {"trades": 10, "by_outcome": {"timeout": 1}}, "OOS": {}}
        s = sw._fidelity_suffix(cell)
        assert "OOS ?" in s, s
        assert "OOS 0%" not in s

    def test_the_measured_monotone_shape_is_pinned(self):
        """Guards the claim the memo makes, not just the arithmetic."""
        measured = [(0.75, 9, 42), (1.0, 14, 42), (1.25, 14, 41), (1.5, 15, 41),
                    (2.0, 17, 41), (2.5, 21, 41), (3.0, 23, 41), (4.0, 24, 41)]
        shares = [sw.timeout_share({"trades": n, "by_outcome": {"timeout": t}})
                  for _, t, n in measured]
        assert shares == sorted(shares), f"not monotone: {shares}"
        assert shares[0] < 0.25 and shares[-1] > 0.55, shares


class TestFidelityIsWiredToTheVerdictLine:
    """The suffix must reach the PRINTED verdict, not merely exist as a helper.

    A mutant that deleted `+ _fidelity_suffix(cell)` from main()'s print left
    every helper test green — the same test-a-copy defect that made two other
    tests in this session non-load-bearing. This drives the real `main()` and
    reads its stdout.
    """

    def _tiny_csv(self, tmp_path):
        import pandas as pd
        n = 40
        ts = pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC")
        p = tmp_path / "d.csv"
        pd.DataFrame({"timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "open": 1.0, "high": 1.1, "low": 0.9,
                      "close": 1.0, "volume": 1.0}).to_csv(p, index=False)
        return p

    def test_printed_verdict_carries_the_timeout_share(self, tmp_path, monkeypatch, capsys):
        data = self._tiny_csv(tmp_path)

        def fake_run_cell(data_csv, extra, out_json):
            # a wide cell times out far more than the baseline — the whole point
            wide = any(a not in ("--symbol", "SOLUSDT", "--timeframe", "5m")
                       and a.replace(".", "").isdigit() and float(a) >= 2.0
                       for a in extra)
            return {"total_trades": 100, "total_r": 10.0, "max_drawdown_r": 5.0,
                    "expectancy_r": 0.1, "win_rate_pct": 50.0,
                    "by_outcome": {"timeout": 60 if wide else 20,
                                   "tp_hit": 20, "sl_hit": 20}}

        monkeypatch.setattr(sw, "run_cell", fake_run_cell)
        # argv[0] is the PROGRAM NAME — main() parses argv[1:], like sys.argv.
        rc = sw.main(["ict_scalp_exit_sweep.py",
                      "--data", str(data), "--symbol", "SOLUSDT",
                      "--timeframe", "5m", "--split", "2025-01-01T20:00:00Z",
                      "--cells", "bracket_geometry", "--out", str(tmp_path / "o")])
        out = capsys.readouterr().out
        assert rc == 0, out
        assert "[timeout" in out, (
            "the verdict line does not carry its fidelity — a reader cannot "
            f"tell this from a parity run:\n{out}")
        # and it must show the DIFFERENCE, not one constant for every cell
        assert "IS 60%" in out and "IS 20%" in out, out


class TestPathBIsRecordedNeverGraded:
    """The exit-refinement gate has TWO qualifying paths; this sweep grades one.

    Path B's thresholds (how much net_r_per_capital_day must improve, how much
    net_R may fall) are deliberately UNSET repo-wide — m20_fleet_exit_sweep's
    `capital_delta` says the operator sets them "from a measured distribution,
    not from a number a session invented", and its own `path_b_wf_pass` verdict
    "IS NOT A PROMOTION". So the obligation here is to RECORD the evidence and
    to SAY that only Path A was graded. A pass/fail computed here would invent
    an operator-reserved threshold.
    """

    def test_metrics_carry_the_path_b_inputs(self):
        m = sw.metrics({"total_r": 1.0, "max_drawdown_r": 2.0,
                        "net_total_r": 0.9, "net_r_per_capital_day": 0.5,
                        "net_r_per_position_day": 0.5, "capital_days": 1.8,
                        "mean_bars_held": 12.0, "trades": 3}) \
            if hasattr(sw, "metrics") else None
        if m is None:
            import inspect
            src = inspect.getsource(sw)
            for k in ("net_r_per_capital_day", "capital_days", "net_total_r"):
                assert f'"{k}": summary.get("{k}")' in src, (
                    f"{k} is not recorded — Path B cannot be evaluated later, "
                    "and it cannot be recovered retroactively either")
            return
        for k in ("net_r_per_capital_day", "capital_days", "net_total_r"):
            assert k in m, k

    def test_the_capital_comparison_is_IMPORTED_not_reimplemented(self):
        """A second copy makes a cross-harness comparison meaningless."""
        import inspect
        src = inspect.getsource(sw._capital_delta)
        assert "m20_fleet_exit_sweep" in src, (
            "the capital comparison must delegate to its single owner")
        assert "capital_delta" in src
        # and it must not compute the ratio itself
        assert "/" not in src.split("return")[-1], (
            "looks like a local re-derivation rather than a delegation")

    def test_delegation_actually_reaches_the_owner(self, tmp_path):
        cell = {"net_total_r": 2.0, "net_r_per_capital_day": 0.4,
                "net_r_per_position_day": 0.4, "capital_days": 5.0,
                "mean_bars_held": 10.0}
        base = {"net_total_r": 1.0, "net_r_per_capital_day": 0.2,
                "net_r_per_position_day": 0.2, "capital_days": 5.0,
                "mean_bars_held": 10.0}
        out = sw._capital_delta(cell, base)
        assert out["d_net_r_per_capital_day"] == 0.2
        assert out["d_net_total_r"] == 1.0
        assert out["net_r_retained_frac"] == 2.0

    def test_unmeasurable_rate_is_None_not_zero(self):
        """Collapsing 'could not measure' into 0.0 is what the owner refuses."""
        out = sw._capital_delta({"net_total_r": 1.0}, {"net_total_r": 1.0})
        assert out["d_net_r_per_capital_day"] is None
        assert out["cell_net_r_per_capital_day"] is None

    def test_the_sweep_DECLARES_that_it_graded_only_path_A(self, tmp_path, monkeypatch, capsys):
        """Silence about which paths were graded reads as 'the gate was applied'."""
        import pandas as pd
        n = 40
        ts = pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC")
        data = tmp_path / "d.csv"
        pd.DataFrame({"timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "open": 1.0, "high": 1.1, "low": 0.9,
                      "close": 1.0, "volume": 1.0}).to_csv(data, index=False)

        def fake(data_csv, extra, out_json):
            return {"total_trades": 50, "total_r": 5.0, "max_drawdown_r": 2.0,
                    "expectancy_r": 0.1, "win_rate_pct": 50.0,
                    "net_total_r": 4.0, "net_r_per_capital_day": 0.3,
                    "net_r_per_position_day": 0.3, "capital_days": 13.0,
                    "mean_bars_held": 9.0,
                    "by_outcome": {"tp_hit": 25, "sl_hit": 25}}

        monkeypatch.setattr(sw, "run_cell", fake)
        import json as _json
        out = tmp_path / "o"
        rc = sw.main(["prog", "--data", str(data), "--symbol", "SOLUSDT",
                      "--timeframe", "5m", "--split", "2025-01-01T20:00:00Z",
                      "--cells", "bracket_geometry", "--out", str(out)])
        assert rc == 0, capsys.readouterr().out
        v = _json.loads((out / "verdicts.json").read_text())
        for tag, c in v["cells"].items():
            assert c["gate_paths_graded"] == ["A"], (
                f"{tag} does not declare which gate paths it graded — a reader "
                "cannot tell a Path-A verdict from a full-gate one")
            assert "capital_delta" in c, tag
            assert set(c["capital_delta"]) == {"IS", "OOS"}, tag


class TestSlBufferCells:
    """The stop-buffer grid — the family's only stop-side knob."""

    def test_the_leg_s_own_value_is_excluded(self):
        """A cell equal to the baseline measures a guaranteed zero and reports
        as a tested negative rather than as the no-op it is."""
        tags = [t for t, _, _ in sw.sl_buffer_cells(0.20)]
        assert "slbuf0.2" not in tags
        # and if a leg ever declared one of the grid values, that one drops too
        assert "slbuf0.3" not in [t for t, _, _ in sw.sl_buffer_cells(0.30)]
        assert "slbuf0.3" in [t for t, _, _ in sw.sl_buffer_cells(0.20)]

    def test_the_grid_brackets_the_live_value_on_BOTH_sides(self):
        """A one-sided grid can only answer half of 'is the stop mistimed'."""
        vals = [float(e[1]) for _, _, e in sw.sl_buffer_cells(0.20)]
        assert any(v < 0.20 for v in vals), "no TIGHTER cell"
        assert any(v > 0.20 for v in vals), "no WIDER cell"

    def test_zero_is_NOT_in_the_grid(self):
        """The harness REFUSES 0.0, so such a cell reports as an error and
        reads as a failed sweep rather than as a value that cannot be run."""
        assert 0.0 not in sw._SL_BUFFER_GRID
        vals = [float(e[1]) for _, _, e in sw.sl_buffer_cells(0.20)]
        assert all(v > 0 for v in vals)

    def test_every_cell_names_the_stop_geometry_lever(self):
        assert {lev for _, lev, _ in sw.sl_buffer_cells(0.20)} == {"stop_geometry"}

    def test_the_family_reaches_the_PRODUCTION_assembly(self):
        levers = {lev for _, lev, _ in sw.all_cells(1.5)}
        assert "stop_geometry" in levers, (
            "the family exists but the sweep never runs it")
        assert {"bracket_geometry", "breakeven_ratchet", "exit_ladder"} <= levers

    def test_cells_pass_the_flag_the_harness_actually_exposes(self):
        """A cell naming a flag the harness does not have exits non-zero and
        reads as a failed cell rather than as a broken sweep."""
        import subprocess
        import sys
        help_txt = subprocess.run(
            [sys.executable, "scripts/backtest_ict_scalp.py", "--help"],
            capture_output=True, text=True).stdout
        for _, _, extra in sw.sl_buffer_cells(0.20):
            assert extra[0].lstrip("-").replace("-", "-") and extra[0] in help_txt, (
                f"{extra[0]} is not a flag the harness exposes")
