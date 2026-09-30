"""Synthetic-only tests for scripts/research/m16_within_leg_confidence.py.

The real committed ledgers are deliberately NOT read here: the unit's rule is registered
before its first run, and a smoke on the real data would be a look at the answer.
"""
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from scripts.research import m16_within_leg_confidence as m  # noqa: E402


def _write(tmp: Path, legs: dict, coverage="measured") -> Path:
    ev = tmp / "comms" / "strategy_evidence"
    (ev / "runs" / "d").mkdir(parents=True)
    for leg, rows in legs.items():
        led = ev / "runs" / "d" / f"{leg}__trades.jsonl"
        led.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        (ev / f"{leg}.json").write_text(json.dumps(
            {"coverage_state": coverage, "source_run": f"comms/strategy_evidence/runs/d/{leg}__trades.jsonl"}))
    return ev


def _legs(n_legs, n, signal, seed=1):
    rng = np.random.default_rng(seed)
    out = {}
    for k in range(n_legs):
        rows = []
        for i in range(n):
            c = float(rng.random())
            r = signal * (c - 0.5) * 4 + float(rng.normal())
            rows.append({"confidence": c, "net_r": r,
                         "entry_time": f"2025-{1 + i * 12 // n:02d}-{1 + i % 27:02d} 00:00:00+00:00"})
        out[f"ict_scalp_x{k}_5m" if k % 2 else f"trend_donchian_x{k}"] = rows
    return out


def _run(tmp_path, legs, monkeypatch, **bars):
    ev = _write(tmp_path, legs)
    monkeypatch.setattr(m, "REPO", tmp_path)
    monkeypatch.setattr(m, "PERM_N", 300)
    for k, v in bars.items():
        monkeypatch.setattr(m, k, v)
    loaded, census = m.load_legs(ev)
    return m.grade(loaded, census)


def test_planted_within_leg_signal_passes(tmp_path, monkeypatch):
    v = _run(tmp_path, _legs(8, 300, 0.5), monkeypatch)
    assert v["verdict"] == "pass", v["measurement"]


def test_no_signal_fails(tmp_path, monkeypatch):
    v = _run(tmp_path, _legs(8, 300, 0.0), monkeypatch)
    assert v["verdict"] == "fail" and v["read_state"] == "measured"


def test_thin_population_is_indeterminate_not_fail(tmp_path, monkeypatch):
    v = _run(tmp_path, _legs(3, 60, 0.5), monkeypatch)
    assert v["verdict"] == "indeterminate"


def test_no_usable_leg_is_not_applicable(tmp_path, monkeypatch):
    ev = _write(tmp_path, _legs(2, 100, 0.5), coverage="harness_failed")
    monkeypatch.setattr(m, "REPO", tmp_path)
    legs, census = m.load_legs(ev)
    v = m.grade(legs, census)
    assert v["verdict"] == "not_applicable" and v["read_state"] == "no_data"
    assert census["not_measured"] == 2


def test_percentile_uses_only_earlier_trades():
    c = np.array([0.1] * 20 + [0.5, 0.05])
    p = m.past_percentile(c)
    assert np.isnan(p[:20]).all() and p[20] == 1.0 and p[21] == 0.0


def test_scale_differences_between_legs_do_not_matter(tmp_path, monkeypatch):
    legs = _legs(8, 300, 0.5)
    scaled = {k: [dict(r, confidence=r["confidence"] * (10 ** i)) for r in v]
              for i, (k, v) in enumerate(legs.items())}
    a = _run(tmp_path, legs, monkeypatch)["measurement"]["rho"]
    b = _run(tmp_path / "s", scaled, monkeypatch)["measurement"]["rho"]
    assert abs(a - b) < 1e-9
