"""GEOM-B1-HARNESS, sweep half: the `tp_extend` / `tp_retarget` lever columns.

What this pins, and why each is load-bearing:

* The cells are OPT-IN. `cells_for` without the flag returns exactly what it did
  before this lever existed, so no existing sweep grows and an empty `levers`
  input still means what it meant.
* The grids are the ones the four units REGISTERED before any run
  (RQ-20261007-001..004); a grid that drifts from its rule grades a different
  question under the same id.
* Both columns are in the coverage matrix on every row, and the lever-wiring
  guard sees them (gradeable, runnable-exempt: backtest-only until BUILD B1).
* The scalp sweep (`m27`) and the fleet sweep share ONE cell definition.
* The mapper that lands the scalp sweep's result never calls a P&L-only cell a
  pass, and never lets an unread calibration side pass.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
for p in (REPO, REPO / "scripts", REPO / "scripts" / "research"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, str(REPO / rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sw = _load("_tpg_sweep_fleet", "scripts/research/m20_fleet_exit_sweep.py")
m27 = _load("_tpg_sweep_m27", "scripts/research/m27/ict_scalp_exit_sweep.py")
ssr = _load("_tpg_sweep_result", "scripts/research/m27/scalp_sweep_result.py")

TREND_CFG = {"timeframe": "4h", "symbols": ["XRPUSDT"], "trail_mult": 3.0, "tp_r": 3.0}


def _tp(cells):
    return [c for c in cells if c[1] in sw.TP_GEOMETRY_LEVERS]


def test_tp_cells_are_opt_in():
    off = sw.cells_for(TREND_CFG, "donchian", skipped=[], tp_cap_pct=0.099)
    on = sw.cells_for(TREND_CFG, "donchian", skipped=[], tp_cap_pct=0.099,
                      tp_geometry_cells=True)
    assert _tp(off) == []
    assert [c for c in on if c not in _tp(on)] == off      # nothing else moved
    assert len(_tp(on)) > 0


def test_trail_family_grid_is_the_registered_one():
    cells = _tp(sw.cells_for(TREND_CFG, "donchian", skipped=[], tp_geometry_cells=True))
    ext = [c for c in cells if c[1] == "tp_extend"]
    ret = [c for c in cells if c[1] == "tp_retarget"]
    # targets {2.0, 2.5, declared 3.0} x approach (0.75, 0.85) x extend_r (0.5, 1.0) x max (1, 3)
    assert len(ext) == 3 * 2 * 2 * 2
    # targets x {atr_rescale, stall_pull_in x stall bars (6, 10)}
    assert len(ret) == 3 * 3
    assert {a for _, _, a in ret if "--tp-retarget-stall-bars" in a
            for a in [a[a.index("--tp-retarget-stall-bars") + 1]]} == {"6", "10"}
    # the leg's own DECLARED finite target rides along; a sentinel never does
    sentinel = _tp(sw.cells_for({**TREND_CFG, "tp_r": 50.0}, "donchian", skipped=[],
                                tp_geometry_cells=True))
    assert all(a[a.index("--tp-target-r") + 1] in ("2", "2.5")
               for _, _, a in sentinel)


def test_scalp_grid_is_the_registered_one_and_acts_on_the_live_bracket():
    cells = _tp(sw.cells_for({"timeframe": "5m"}, "scalp", skipped=[],
                             tp_geometry_cells=True))
    assert len(cells) == 5                                  # 2 extend + atr + stall(3, 6)
    assert all("--tp-target-r" not in a for _, _, a in cells)   # the live tp_at_r, not a new one
    stalls = {a[a.index("--tp-retarget-stall-bars") + 1] for _, _, a in cells
              if "--tp-retarget-stall-bars" in a}
    assert stalls == {"3", "6"}


def test_families_without_a_harness_lever_are_skipped_and_said_so():
    sk: list = []
    for fam in ("squeeze", "fvg"):
        assert _tp(sw.cells_for({"timeframe": "4h"}, fam, skipped=sk,
                                tp_geometry_cells=True)) == []
    assert [s["reason"] for s in sk if s["lever"] == "tp_extend,tp_retarget"] == [
        "tp_geometry_not_in_harness:squeeze", "tp_geometry_not_in_harness:fvg"]


def test_every_cell_argv_is_accepted_by_its_harness_parser():
    """A cell whose flags the harness rejects would grade nothing and look like an error row."""
    trend = _load("_tpg_sweep_trend", "scripts/backtest_trend.py")
    pullback = _load("_tpg_sweep_pb", "scripts/backtest_pullback.py")
    scalp = _load("_tpg_sweep_scalp", "scripts/backtest_ict_scalp.py")
    probes = {"donchian": (trend, None), "pullback": (pullback, None), "scalp": (scalp, None)}
    for fam in probes:
        cfg = TREND_CFG if fam != "scalp" else {"timeframe": "5m"}
        for tag, _lever, extra in _tp(sw.cells_for(cfg, fam, skipped=[],
                                                   tp_geometry_cells=True)):
            if fam == "scalp":
                ns = scalp.build_parser().parse_args(["--data", "x.csv", *extra])
                spec = scalp._tpg.spec_from_args(ns)
            else:
                # the trend/pullback parsers are built inside main(); the shared flag
                # registrar is the one definition both use
                import argparse
                ap = argparse.ArgumentParser()
                trend._tpg.add_cli_flags(ap)
                spec = trend._tpg.spec_from_args(ap.parse_args(extra))
            assert spec.armed, tag


def test_lever_wiring_guard_sees_both_columns():
    out = subprocess.run([sys.executable, str(REPO / "scripts/ci/check_lever_wiring.py")],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "tp_extend" in out.stdout and "tp_retarget" in out.stdout
    st = subprocess.run([sys.executable, str(REPO / "scripts/ci/check_lever_wiring.py"),
                         "--self-test"], capture_output=True, text=True)
    assert st.returncode == 0, st.stdout


def test_coverage_matrix_carries_both_columns_on_every_row():
    mx = json.loads((REPO / "docs/research/exit-refinement-coverage.json").read_text())
    assert "tp_extend" in mx["lever_columns"] and "tp_retarget" in mx["lever_columns"]
    for row in mx["rows"]:
        for col in ("tp_extend", "tp_retarget"):
            cell = row.get(col)
            assert isinstance(cell, dict) and cell.get("status"), (row["strategy"], col)
            fam = sw.classify(row["strategy"])
            if fam in sw.TP_GEOMETRY_FAMILIES:
                assert cell["status"] == "pending", (row["strategy"], col)
            else:
                assert cell["status"] == "blocked:no_harness_levers", (row["strategy"], col)
    rc = subprocess.run([sys.executable, str(REPO / "scripts/research/m20_coverage_rollup.py"),
                         "--check"], capture_output=True, text=True)
    assert rc.returncode == 0, rc.stdout + rc.stderr


def test_per_cell_record_reads_calibration_beside_the_base_and_never_invents_a_zero():
    g = {"calibration_share": 0.5, "total_extends": 3, "total_retargets": 0}
    b = {"calibration_share": 0.25}
    e = sw.tp_geometry_entry({"tp_geometry": g}, {"tp_geometry": g},
                             {"IS": {"tp_geometry": b}, "OOS": {"tp_geometry": b}})
    assert e["IS"]["calibration_ge_base"] is True
    assert e["OOS"]["calibration_share_cell"] == 0.5 and e["OOS"]["calibration_share_base"] == 0.25
    unread = sw.tp_geometry_entry({}, {}, None)
    assert unread["IS"]["calibration_ge_base"] is None          # "we did not look", not False
    assert unread["IS"]["calibration_share_cell"] is None


def test_corpus_rows_carry_the_tp_calibration_fields():
    ce = _load("_tpg_sweep_extract", "scripts/research/m20_corpus_extract.py")
    entry = {"cell": "tpx_t2_a0.85_e1_m3", "verdict": "is_oos_fail", "is_oos_pass": False,
             "tp_geometry": {w: {"calibration_share_cell": 0.4, "calibration_share_base": 0.3,
                                 "calibration_ge_base": True,
                                 "cell": {"total_extends": 7, "total_retargets": 0}}
                             for w in ("IS", "OOS")}}
    doc = {"run_id": "r", "split": "2025-07-01", "verdicts": {"leg_x": {
        "family": "donchian", "proxy": False, "levers": {"tp_extend": [entry]},
        "base_book": {}, "declared_levers_present": [], "declared_levers_dropped": []}}}
    rows = [r for r in ce.rows_from_verdicts(doc, "r") if r.get("kind") == "cell"]
    assert len(rows) == 1
    r = rows[0]
    assert (r["tp_cal_cell_IS"], r["tp_cal_base_OOS"], r["tp_extends_OOS"]) == (0.4, 0.3, 7)
    # and a non-tp row carries none of them
    entry2 = {"cell": "stale8_lt0R", "verdict": "is_oos_fail", "is_oos_pass": False}
    doc["verdicts"]["leg_x"]["levers"] = {"stale_stop": [entry2]}
    r2 = [r for r in ce.rows_from_verdicts(doc, "r") if r.get("kind") == "cell"][0]
    assert not any(k.startswith("tp_cal_") or k.startswith("tp_extends_") for k in r2)


def test_scalp_sweep_shares_the_fleet_cell_definition_and_expands_the_alias():
    assert m27.tp_revision_cells() == sw._tp_geometry_cells({}, "scalp")
    assert m27.want_levers("tp_revision") == {"tp_extend", "tp_retarget"}
    assert m27.want_levers("tp_extend,exit_ladder") == {"tp_extend", "exit_ladder"}
    assert m27.want_levers(None) is None and m27.want_levers("") is None


def test_scalp_sweep_default_cell_set_is_unchanged():
    """`--cells exit_ladder` (the workflow default) must not pick up a tp cell."""
    want = m27.want_levers("exit_ladder")
    cells = [c for c in list(m27.CELLS) + m27.ladder_cells(1.5) if c[1] in want]
    assert cells and all(c[1] == "exit_ladder" for c in cells)


def test_scalp_result_mapper_self_test():
    r = subprocess.run([sys.executable, str(REPO / "scripts/research/m27/scalp_sweep_result.py"),
                        "--self-test"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_workflows_accept_the_lever_and_the_dispatcher_inputs():
    m20 = yaml.safe_load((REPO / ".github/workflows/m20-exit-lever-sweep.yml").read_text())
    scalp = yaml.safe_load((REPO / ".github/workflows/ict-scalp-exit-sweep.yml").read_text())
    i20 = m20[True]["workflow_dispatch"]["inputs"]
    isc = scalp[True]["workflow_dispatch"]["inputs"]
    assert "tp_extend" in i20["levers"]["description"] and "tp_retarget" in i20["levers"]["description"]
    assert "tp_revision" in isc["cells"]["description"]
    # the dispatcher refuses an `accruing` unit unless these are declared, and `gh workflow
    # run -f <undeclared>` errors
    for k in ("research_unit", "power_state", "decision_rule_id", "decision_rule_registered_at"):
        assert k in isc, k
    assert "research_result" in scalp["jobs"]
    step_uses = [s.get("uses", "") for s in scalp["jobs"]["research_result"]["steps"]]
    assert "./.github/actions/research-result" in step_uses
    # the landing is skipped for a run that is not queue-dispatched
    assert "inputs.research_unit != ''" in scalp["jobs"]["research_result"]["if"]
