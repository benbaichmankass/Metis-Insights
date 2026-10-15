"""`scripts/research/tp_prediction_derive.py` -- the predictive-TP derivation behind RQ-20261015-cab7-01..06.

The script's --self-test plants a reversal leg (a TP must PASS), a fat-tail leg (a fixed TP must FAIL), a thin
leg, a drifting leg, a vol-conditioned leg and a homogeneous null. Pinned here so CI runs it; plus the
units' registered thresholds and the script's constants cannot drift apart silently.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "research"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO))


def test_self_test_passes() -> None:
    proc = subprocess.run([sys.executable, "scripts/research/tp_prediction_derive.py", "--self-test"],
                          cwd=REPO, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr



def test_script_constants_are_the_registered_ones() -> None:
    """RQ-20261015-cab7-0* register these thresholds; tests/test_tp_prediction_units.py pins the units to them."""
    import tp_prediction_derive as t
    assert (t.REACH_FLOOR, t.IS_FRAC, t.MIN_OOS, t.NONINF_MARGIN, t.CAL_TOL) == (0.10, 0.60, 39, 0.05, 0.10)
