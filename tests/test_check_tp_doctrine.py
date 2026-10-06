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

from scripts.ci import check_tp_doctrine as guard


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
