"""Offline tests for scripts/research/maker_economics.py — the M22 P1 maker-fee
re-scorer. Fully offline; a tiny synthetic emit JSONL, no network."""
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
    "maker_economics", str(REPO_ROOT / "scripts" / "research" / "maker_economics.py"))
me = importlib.util.module_from_spec(_SPEC)
sys.modules["maker_economics"] = me
_SPEC.loader.exec_module(me)  # type: ignore[union-attr]


def _emit(tmp_path, name, rows):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in rows))
    return str(p)


def test_tf_parsed_from_name():
    assert me._tf_from_name("/x/fade_5m.jsonl") == "5m"
    assert me._tf_from_name("/x/chop_scalp_1m.jsonl") == "1m"
    assert me._tf_from_name("/x/squeeze_15m.jsonl") == "15m"
    assert me._tf_from_name("/x/nope.jsonl") == ""


def test_taker_reproduces_net_exactly(tmp_path):
    # gross - net = fee_r at 7.5bps taker; re-pricing at taker must return net.
    rows = [{"gross_r": 2.0, "net_r": 1.7, "hold_bars": 10},
            {"gross_r": -1.0, "net_r": -1.25, "hold_bars": 20}]
    path = _emit(tmp_path, "cell_5m.jsonl", rows)
    r = me._score_cell(path, taker_bps=7.5, maker_both_bps=0.0, fill_rate=1.0, adverse_r=0.0)
    # taker net total = 1.7 + (-1.25) = 0.45
    assert r["taker"]["net_total_r"] == pytest.approx(0.45, abs=1e-6)
    # gross total = 2.0 - 1.0 = 1.0
    assert r["gross_total_r"] == pytest.approx(1.0, abs=1e-6)


def test_maker_both_equals_gross_at_zero_bps(tmp_path):
    rows = [{"gross_r": 2.0, "net_r": 1.7, "hold_bars": 10},
            {"gross_r": -1.0, "net_r": -1.25, "hold_bars": 20}]
    path = _emit(tmp_path, "cell_5m.jsonl", rows)
    r = me._score_cell(path, taker_bps=7.5, maker_both_bps=0.0, fill_rate=1.0, adverse_r=0.0)
    # at 0 bps, net == gross
    assert r["maker_optimistic"]["net_total_r"] == pytest.approx(1.0, abs=1e-6)


def test_flip_detection(tmp_path):
    # A cell whose gross is +ve but taker-net is -ve = the maker-flip candidate.
    # gross 1.0, taker-net 0.45 (already +ve here) — make taker-net negative:
    rows = [{"gross_r": 0.3, "net_r": -0.2, "hold_bars": 5},
            {"gross_r": 0.4, "net_r": -0.1, "hold_bars": 5}]
    path = _emit(tmp_path, "fade_5m.jsonl", rows)
    r = me._score_cell(path, taker_bps=7.5, maker_both_bps=0.0, fill_rate=1.0, adverse_r=0.0)
    assert r["taker"]["net_total_r"] < 0          # taker kills it
    assert r["gross_total_r"] == pytest.approx(0.7, abs=1e-6)
    assert r["maker_optimistic"]["net_total_r"] == pytest.approx(0.7, abs=1e-6)  # gross is +ve
    # realistic = maker entry / taker exit (3.75bps) × fill 1.0, no adverse:
    # fee_r_taker per trade = gross-net = 0.5 each; at 3.75 = half = 0.25; net = 0.3-0.25=0.05, 0.4-0.25=0.15
    r2 = me._score_cell(path, 7.5, 0.0, fill_rate=1.0, adverse_r=0.0)
    assert r2["maker_realistic"]["net_total_r"] == pytest.approx(0.2, abs=1e-6)


def test_empty_cell(tmp_path):
    path = _emit(tmp_path, "empty_5m.jsonl", [])
    r = me._score_cell(path, 7.5, 0.0, 0.6, 0.02)
    assert r["taker"]["trades"] == 0
    assert r["maker_realistic"]["net_r_per_pos_day"] is None
