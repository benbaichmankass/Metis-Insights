"""Offline tests for scripts/research/pair_funding_drag.py — synthetic funding
CSVs, no network."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_SPEC = importlib.util.spec_from_file_location(
    "pair_funding_drag", str(REPO_ROOT / "scripts" / "research" / "pair_funding_drag.py"))
fd = importlib.util.module_from_spec(_SPEC)
sys.modules["pair_funding_drag"] = fd
_SPEC.loader.exec_module(fd)  # type: ignore[union-attr]


def _write(tmp_path, name, ts, rates):
    p = tmp_path / name
    pd.DataFrame({"timestamp": ts, "funding_rate": rates}).to_csv(p, index=False)
    return str(p)


def test_identical_legs_zero_net(tmp_path):
    ts = pd.date_range("2025-01-01", periods=90, freq="8h", tz="UTC")
    a = _write(tmp_path, "a.csv", ts, [0.0001] * 90)
    b = _write(tmp_path, "b.csv", ts, [0.0001] * 90)  # identical -> net 0
    out = fd.analyze(a, b, mean_hold_hours=5.7)
    assert out["intervals"] == 90
    assert out["net_diff_mean_abs_bps_8h"] == pytest.approx(0.0, abs=1e-9)
    assert out["worst_case_drag_bps_per_trade"] == pytest.approx(0.0, abs=1e-9)


def test_constant_differential(tmp_path):
    ts = pd.date_range("2025-01-01", periods=30, freq="8h", tz="UTC")
    # A funding 0.0002, B 0.0001 -> net 0.0001 = 1.0 bps per 8h
    a = _write(tmp_path, "a.csv", ts, [0.0002] * 30)
    b = _write(tmp_path, "b.csv", ts, [0.0001] * 30)
    out = fd.analyze(a, b, mean_hold_hours=8.0)  # exactly 1 interval
    assert out["net_diff_mean_abs_bps_8h"] == pytest.approx(1.0, abs=1e-6)
    assert out["net_diff_mean_abs_bps_day"] == pytest.approx(3.0, abs=1e-6)  # 3x per day
    assert out["worst_case_drag_bps_per_trade"] == pytest.approx(1.0, abs=1e-6)  # 8h hold = 1 interval


def test_missing_rate_col_raises(tmp_path):
    p = tmp_path / "bad.csv"
    pd.DataFrame({"timestamp": ["2025-01-01"], "nope": [1]}).to_csv(p, index=False)
    with pytest.raises(ValueError):
        fd._load_funding(str(p))
