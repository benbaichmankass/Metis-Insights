"""Offline tests for scripts/research/session_gating.py — the M22 wave-2
session/killzone frequency-reduction re-scorer. Fully offline; synthetic emits."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_SPEC = importlib.util.spec_from_file_location(
    "session_gating", str(REPO_ROOT / "scripts" / "research" / "session_gating.py"))
sg = importlib.util.module_from_spec(_SPEC)
sys.modules["session_gating"] = sg
_SPEC.loader.exec_module(sg)  # type: ignore[union-attr]


def _emit(tmp_path, name, rows):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in rows))
    return str(p)


def test_hour_of_parses_formats():
    assert sg._hour_of("2023-01-01 08:30:00+00:00") == 8
    assert sg._hour_of("2023-01-01T14:00:00Z") == 14
    assert sg._hour_of("2023-01-01T14:00:00") == 14  # naive treated UTC
    assert sg._hour_of(1_672_549_200) == 5  # 2023-01-01 05:00 UTC epoch-s
    assert sg._hour_of(None) is None
    assert sg._hour_of("garbage") is None


def test_kz_of_hour_boundaries():
    assert sg._kz_of_hour(0) == "asian"
    assert sg._kz_of_hour(5) == "asian"
    assert sg._kz_of_hour(6) == "off"      # 06 is the asian upper bound (exclusive)
    assert sg._kz_of_hour(8) == "london"
    assert sg._kz_of_hour(13) == "ny_am"
    assert sg._kz_of_hour(23) == "off"
    assert sg._kz_of_hour(None) == "unknown"


def test_net_at_reproduces_taker():
    rows = [{"gross_r": 2.0, "net_r": 1.7}, {"gross_r": -1.0, "net_r": -1.25}]
    r = sg._net_at(rows, 7.5, 7.5)
    assert r["net_total_r"] == pytest.approx(0.45, abs=1e-6)   # 1.7 - 1.25
    assert r["gross_total_r"] == pytest.approx(1.0, abs=1e-6)


def test_net_at_maker_both_equals_gross():
    rows = [{"gross_r": 2.0, "net_r": 1.7}, {"gross_r": -1.0, "net_r": -1.25}]
    r = sg._net_at(rows, 0.0, 7.5)
    assert r["net_total_r"] == pytest.approx(1.0, abs=1e-6)    # == gross at 0 bps


def test_session_gate_flips_a_cell(tmp_path):
    # Two london trades (net-positive) + two asian trades (net-negative). All-hours
    # net is negative; restricting to london (the positive KZ) flips it positive.
    rows = [
        {"gross_r": 0.5, "net_r": 0.3, "entry_time": "2023-01-01 08:00:00+00:00"},
        {"gross_r": 0.5, "net_r": 0.3, "entry_time": "2023-01-02 09:00:00+00:00"},
        {"gross_r": -0.4, "net_r": -0.6, "entry_time": "2023-01-01 02:00:00+00:00"},
        {"gross_r": -0.4, "net_r": -0.6, "entry_time": "2023-01-02 03:00:00+00:00"},
    ]
    path = _emit(tmp_path, "fade_5m.jsonl", rows)
    r = sg._score_cell(path, taker_bps=7.5, maker_both_bps=0.0)
    assert r["all_hours"]["net_total_r"] == pytest.approx(-0.6, abs=1e-6)  # 0.3+0.3-0.6-0.6
    assert "london" in r["best_subset"]["killzones"]
    assert "asian" not in r["best_subset"]["killzones"]
    assert r["best_subset"]["taker"]["net_total_r"] == pytest.approx(0.6, abs=1e-6)
    assert r["entry_time_coverage_pct"] == 100.0


def test_no_positive_kz(tmp_path):
    rows = [{"gross_r": -0.4, "net_r": -0.6, "entry_time": "2023-01-01 08:00:00+00:00"},
            {"gross_r": -0.4, "net_r": -0.6, "entry_time": "2023-01-01 13:00:00+00:00"}]
    path = _emit(tmp_path, "cell_5m.jsonl", rows)
    r = sg._score_cell(path, 7.5, 0.0)
    assert r["best_subset"]["killzones"] == []
    assert r["best_subset"]["taker"]["net_total_r"] == 0.0  # empty subset
