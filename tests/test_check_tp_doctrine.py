"""`scripts/ci/check_tp_doctrine.py` — the TP-doctrine ratchet.

Two things the guard's own `--self-test` cannot pin without importing the
runtime, pinned here where pytest already pays that import:

1. the guard's SOURCE-READ leg→unit resolution agrees with the runtime's
   `pipeline.monitor_unit_for` on every rostered leg (a source read that
   drifted from the resolver would grade the wrong module's `monitor()`);
2. the live baseline matches the live scan exactly — a stale baseline is a
   finding, in both directions.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("yaml")  # the guard reads the two configs; without yaml it can only say "could not look"

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.ci import check_tp_doctrine as guard  # noqa: E402


def test_source_read_unit_resolution_matches_runtime_resolver() -> None:
    pytest.importorskip("pandas")
    from src.runtime.pipeline import monitor_unit_for

    aliases = guard.read_monitor_aliases(guard.Paths().builders)
    routings = guard.scan()
    assert routings, "the scan found no routings — accounts.yaml has rosters, so the probe is broken"
    mismatches = []
    for r in routings:
        runtime_unit = monitor_unit_for(r.leg)
        if guard.unit_for(r.leg, aliases, guard.Paths().units_dir) != runtime_unit:
            mismatches.append((r.leg, r.unit, runtime_unit))
    assert not mismatches, mismatches


def test_live_baseline_matches_live_scan() -> None:
    findings, summary = guard.ratchet(guard.scan(), guard.BASELINE_2026_10_06)
    assert findings == [], findings
    # Population stated, so a silent shrink of the roster is visible here too.
    assert summary["routings"] >= 1
    assert summary["baseline_entries"] == summary["baselined_debt_routings"]


def test_strict_fails_while_debt_exists() -> None:
    findings, summary = guard.ratchet(guard.scan(), guard.BASELINE_2026_10_06, strict=True)
    if summary["baselined_debt_routings"]:
        assert any(x.startswith("STRICT") for x in findings)
    else:  # the day every routing complies, strict must be clean
        assert findings == []


def test_self_test_passes() -> None:
    proc = subprocess.run([sys.executable, "scripts/ci/check_tp_doctrine.py", "--self-test"],
                          cwd=REPO, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_json_output_carries_summary_by_class() -> None:
    import json
    proc = subprocess.run([sys.executable, "scripts/ci/check_tp_doctrine.py", "--json"],
                          cwd=REPO, capture_output=True, text=True, check=False)
    assert proc.returncode in (0, 1), proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert set(data["summary"]["by_class"]) <= set(guard.CLASS_RANK)
    assert len(data["routings"]) == data["summary"]["routings"]


# ── TP-REACH-FLOOR (operator-approved 2026-10-14, checklist row TP-REACH-FLOOR) ──
def test_reach_floor_positive_and_negative_controls_on_the_curve() -> None:
    ev = {"n": 400, "reach": {2.0: 0.30, 3.0: 0.10, 6.0: 0.009}}
    # positive control: a TP reached by >= 10% still passes (the floor itself included)
    assert guard.grade_reach(2.0, ev) == (guard.REACH_OK, 0.30)
    assert guard.grade_reach(3.0, ev)[0] == guard.REACH_OK
    # negative control: a near-sentinel TP reached by ~1% is below the floor
    assert guard.grade_reach(6.0, ev) == (guard.REACH_BELOW, 0.009)
    # absent evidence is never read as reachable
    assert guard.grade_reach(2.0, None) == (guard.REACH_UNMEASURED, None)


def test_reach_floor_grades_the_live_near_sentinel_prop_target_unreachable() -> None:
    """The finding the row was filed on (PI-20261007-SXUZGFME-0001): trend_donchian_eth_prop's
    declared 6 R is reached by < 10% of uncapped harness trades, so its routings fail entry_tp.
    Re-measured by the scan, not quoted: the assertion is the floor, not a number."""
    rows = [r for r in guard.scan() if r.leg == "trend_donchian_eth_prop"]
    assert rows, "trend_donchian_eth_prop is no longer rostered -- re-point this control at another below-floor leg"
    for r in rows:
        assert r.reach_state == guard.REACH_BELOW and r.reach_rate < guard.REACH_FLOOR
        assert r.entry_state == guard.ENTRY_UNREACHABLE and guard.DIM_ENTRY in r.fails


def test_reach_census_states_its_population() -> None:
    _, summary = guard.ratchet(guard.scan(), guard.BASELINE_2026_10_06)
    census = summary["reach_census_over_declared_targets"]
    declared = [r for r in guard.scan() if r.reach_state != guard.REACH_NA]
    assert sum(census.values()) == len(declared)
