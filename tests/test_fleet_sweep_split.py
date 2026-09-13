"""The IS/OOS boundary must not let TRADE FREQUENCY decide gradeability.

WHY THIS EXISTS. The fleet sweep split IS from OOS at a FIXED calendar date
(2025-07-01). A leg trading ~20x/year gets a handful of trades after that date,
so it fails the 25-trade OOS floor for a reason that has nothing to do with the
lever under test. Measured 2026-08-13: the 1d equity legs came back **OOS n=3-6**
against lifetimes of 33-79 trades — 6 of the 7 can support a 25-trade OOS window;
the date simply was not placed to give them one.

This is the same defect as the E1 per-calendar-year fold cut
(`train_exit_head.fold_blocks`), in a different code path.

WHAT IS DELIBERATELY NOT ASSERTED: that the derived date PRODUCES exactly the
target number of OOS trades. The harness windows CANDLES, not trades, so an OOS
run needs warmup and may differ near the boundary. `resolve_split` places the
boundary; the measured `_base_oos_n` remains the authority. A test that asserted
the target was achieved would be asserting something the function does not claim.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]


def _mod():
    spec = importlib.util.spec_from_file_location(
        "m20_fleet_exit_sweep",
        REPO / "scripts" / "research" / "m20_fleet_exit_sweep.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["m20_fleet_exit_sweep"] = m
    spec.loader.exec_module(m)
    return m


def _emit(monkeypatch, m, stamps, rc=0, stderr=""):
    """Stand in for the harness run, so the split logic is tested without a
    16-minute backtest.

    ⚠️ THE EMIT PATH IS READ OFF THE COMMAND, NOT HARDCODED. This fake used to
    write to the literal "/tmp/m20_split_emit.jsonl" because `resolve_split`
    used that literal too — which coupled the test to an implementation-internal
    temp path AND meant the test could only pass while that path was
    process-shared. When the four shared literals became `tempfile.mkstemp` (the
    concurrency fix, BL-20260820-RUN-CELL-SHARES-A-FIXED-TEMP-PATH), all three
    tests here failed with `split_fallback: emit_unreadable` — the fake was
    writing somewhere nobody read. Taking the path from `--emit-trades` in the
    argv the fake is handed is both the fix and the more honest test: it asserts
    the caller and callee agree on a path rather than that both agree with the
    test's guess."""
    import subprocess

    def fake_run(cmd, **kw):
        import json
        argv = list(cmd)
        assert "--emit-trades" in argv, (
            "resolve_split no longer passes --emit-trades; this fake can no "
            "longer stand in for it")
        out = pathlib.Path(argv[argv.index("--emit-trades") + 1])
        out.write_text("\n".join(
            json.dumps({"entry_time": s}) for s in stamps), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, rc, "", stderr)

    monkeypatch.setattr(m.subprocess, "run", fake_run)


def _daily(n, start="2020-01-02"):
    """n trades, one every ~18 days — the measured 1d cadence (~20/year)."""
    from datetime import datetime, timedelta
    d0 = datetime.fromisoformat(start)
    return [(d0 + timedelta(days=18 * i)).isoformat() for i in range(n)]


def test_date_mode_returns_the_fixed_split_untouched(monkeypatch):
    """The legacy path must be byte-identical — it is the fallback and the
    reproduction route for every pre-2026-08-13 verdict."""
    m = _mod()
    split, meta = m.resolve_split("scripts/backtest_trend.py", [], "date",
                                  "2025-07-01", 25)
    assert split == "2025-07-01"
    assert meta["split_mode"] == "date"
    assert "split_fallback" not in meta


def test_oos_trades_moves_the_boundary_to_reach_the_target(monkeypatch):
    """A 60-trade leg (the measured spy_trend_long_1d lifetime) must get a
    boundary that leaves the target in OOS — not the fixed date that gave it 3."""
    m = _mod()
    stamps = _daily(60)
    _emit(monkeypatch, m, stamps)
    split, meta = m.resolve_split("scripts/backtest_trend.py", [],
                                  "oos-trades", "2025-07-01", 25)
    assert meta.get("split_fallback") is None, meta
    assert meta["split_lifetime_trades"] == 60
    # the boundary is the 25th-from-last trade's date
    assert split == stamps[-25][:10]
    # and it is EARLIER than the fixed date would have placed it, which is the
    # whole point — the fixed date sat too late for this leg's cadence
    assert split < "2025-07-01" or stamps[-1][:10] < "2025-07-01"


def test_a_leg_too_thin_falls_back_AND_SAYS_SO(monkeypatch):
    """mes_trend_long_1d has 33 lifetime trades: giving 25 to OOS leaves 8 for
    IS, which fits nothing. It must fall back to the fixed date with a STATED
    reason — a silent fallback would look like a derived boundary."""
    m = _mod()
    _emit(monkeypatch, m, _daily(33))
    split, meta = m.resolve_split("scripts/backtest_trend.py", [],
                                  "oos-trades", "2025-07-01", 25)
    assert split == "2025-07-01"
    assert meta["split_fallback"] == "leg_too_thin"
    assert meta["split_lifetime_trades"] == 33


def test_harness_failure_falls_back_AND_SAYS_SO(monkeypatch):
    """'We could not look' must never be reported as a derived boundary."""
    m = _mod()
    _emit(monkeypatch, m, _daily(60), rc=1)
    split, meta = m.resolve_split("scripts/backtest_trend.py", [],
                                  "oos-trades", "2025-07-01", 25)
    assert split == "2025-07-01"
    assert meta["split_fallback"] == "harness_rc"


def test_the_verdict_records_how_its_boundary_was_placed(monkeypatch):
    """Trade-derived and fixed-date splits are not comparable evidence, so a
    verdict that does not state which produced it invites the wrong read."""
    m = _mod()
    _emit(monkeypatch, m, _daily(60))
    _, meta = m.resolve_split("scripts/backtest_trend.py", [], "oos-trades",
                              "2025-07-01", 25)
    for key in ("split_mode", "split_target_oos", "split"):
        assert key in meta, (key, meta)


def test_the_check_can_fail(monkeypatch):
    """A leg with exactly 2*target must NOT fall back, and one trade fewer must.
    Without this the thin-leg assertion could pass for the wrong reason."""
    m = _mod()
    _emit(monkeypatch, m, _daily(50))
    _, ok = m.resolve_split("scripts/backtest_trend.py", [], "oos-trades",
                            "2025-07-01", 25)
    assert ok.get("split_fallback") is None, ok

    _emit(monkeypatch, m, _daily(49))
    _, thin = m.resolve_split("scripts/backtest_trend.py", [], "oos-trades",
                              "2025-07-01", 25)
    assert thin["split_fallback"] == "leg_too_thin"


# ---------------------------------------------------------------------------
# BL-20260818-HARNESS-RC-COLLAPSES-THE-ACTUAL-ERROR
# BL-20260818-UNGRADED-CELLS-DO-NOT-SURFACE-AT-THE-FLEET-LEVEL
# BL-20260818-SWEEP-REPORTS-DONE-AFTER-MEASURING-ZERO-CELLS
#
# One defect in three presentations: the run's OUTPUT does not state what it
# actually measured. Each test below asserts the OUTPUT, not the internals —
# the internals were already honest in every one of these cases, which is
# exactly why nobody noticed.
# ---------------------------------------------------------------------------

def test_harness_failure_NAMES_ITS_CAUSE_not_just_the_stage(monkeypatch):
    """`harness_rc` says WHERE it failed and nothing about WHY.

    The cause was captured all along — `resolve_split` stores the stderr tail
    in `split_detail` — and every reader dropped it, so a plain ImportError
    cost two relay round-trips to see.
    """
    m = _mod()
    _emit(monkeypatch, m, _daily(60), rc=1,
          stderr="ModuleNotFoundError: No module named 'pandas'")
    _, meta = m.resolve_split("scripts/backtest_trend.py", [],
                              "oos-trades", "2025-07-01", 25)
    assert meta["split_fallback"] == "harness_rc"
    line = m.summary_split_line("xrp_pullback_2h", meta)
    assert "harness_rc" in line
    assert "ModuleNotFoundError" in line, line
    assert "pandas" in line, line


def test_a_clean_split_line_carries_no_cause(monkeypatch):
    """Control: without this the cause assertion could pass for the wrong
    reason (e.g. if the word were printed unconditionally)."""
    m = _mod()
    _emit(monkeypatch, m, _daily(60))
    _, meta = m.resolve_split("scripts/backtest_trend.py", [],
                              "oos-trades", "2025-07-01", 25)
    assert meta.get("split_fallback") is None, meta
    assert "cause:" not in m.summary_split_line("xrp_pullback_2h", meta)


def _leg(cells):
    """cells: list of verdict strings -> a leg_v shaped like the real one."""
    return {"proxy": "p", "family": "f",
            "levers": {"lev": [{"cell": f"c{i}", "verdict": v}
                               for i, v in enumerate(cells)]}}


def test_a_run_with_no_cells_is_not_a_result():
    m = _mod()
    c = m.fleet_grading_census({})
    assert c["run_state"] == "no_cells", c
    assert c["cells_total"] == 0 and c["cells_graded"] == 0


def test_all_ungraded_is_NOT_the_same_state_as_graded():
    """`insufficient_base` is honest and must stay; what was missing is that
    nothing said so above the per-cell level."""
    m = _mod()
    c = m.fleet_grading_census(
        {"xrp_pullback_2h": _leg(["insufficient_base"] * 14)})
    assert c["run_state"] == "all_ungraded", c
    assert c["cells_total"] == 14 and c["cells_ungraded"] == 14
    assert c["per_leg"]["xrp_pullback_2h"]["ungraded"] == 14


def test_a_partly_ungraded_leg_still_reports_its_ungraded_count():
    """The measured instance: 14 cells, 4 ungraded. The run IS graded, and the
    4 must not vanish into that."""
    m = _mod()
    c = m.fleet_grading_census(
        {"xrp_pullback_2h": _leg(["insufficient_base"] * 4 + ["is_oos_pass"] * 10)})
    assert c["run_state"] == "graded", c
    assert (c["cells_total"], c["cells_ungraded"], c["cells_graded"]) == (14, 4, 10)


def test_a_harness_error_leg_is_not_counted_as_graded_cells():
    m = _mod()
    c = m.fleet_grading_census({"a": {"status": "harness_error"},
                                "b": _leg(["is_oos_pass"])})
    assert c["legs_harness_error"] == 1, c
    assert c["per_leg"]["a"]["harness_error"] is True
    assert c["cells_total"] == 1, c


def test_the_census_can_fail():
    """Mutation control: a census that called everything `graded` would pass
    three of the tests above. This pins that the states are really distinct."""
    m = _mod()
    states = {
        m.fleet_grading_census({})["run_state"],
        m.fleet_grading_census({"x": _leg(["insufficient_base"])})["run_state"],
        m.fleet_grading_census({"x": _leg(["is_oos_pass"])})["run_state"],
    }
    assert states == {"no_cells", "all_ungraded", "graded"}, states
