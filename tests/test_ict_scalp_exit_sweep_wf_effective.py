"""CA-B04: ict_scalp_exit_sweep.walk_forward counted a fold where the lever changed
nothing (0 >= 0, 0 <= 0) as a full win. It must report an EFFECTIVE grade."""
import importlib.util
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts" / "research"))
spec = importlib.util.spec_from_file_location(
    "ict_scalp_exit_sweep_t", str(REPO / "scripts/research/m27/ict_scalp_exit_sweep.py"))
mod = importlib.util.module_from_spec(spec)
sys.modules["ict_scalp_exit_sweep_t"] = mod
spec.loader.exec_module(mod)


def _wf(monkeypatch, tmp_path, cell_total_r):
    """Baseline: 20 trades, +5R, dd 2R on every fold. Cell results per fold given."""
    calls = {"i": 0}

    def fake_run_cell(csv, extra, outp):
        if not extra:                                   # baseline
            return {"total_trades": 20, "total_r": 5.0, "max_drawdown_r": 2.0}
        r = cell_total_r[calls["i"] % len(cell_total_r)]
        calls["i"] += 1
        return {"total_trades": 20, "total_r": r[0], "max_drawdown_r": r[1]}

    monkeypatch.setattr(mod, "run_cell", fake_run_cell)
    ts = pd.date_range("2021-01-01", "2026-06-30", freq="1D", tz="UTC")
    df = pd.DataFrame({"timestamp": ts, "close": 1.0})
    return mod.walk_forward(df, pd.Series(ts), tmp_path, {"lever": ["--x"]})["lever"]


def test_all_inert_folds_pass_raw_but_not_effective(monkeypatch, tmp_path):
    # lever never fires: identical to baseline on all 6 folds
    r = _wf(monkeypatch, tmp_path, [(5.0, 2.0)] * 6)
    assert r["verdict"] == "PASS"                      # historical figure, unchanged
    assert r["inert_folds"] == 6
    assert r["effective_usable_folds"] == 0
    assert r["verdict_effective"] == "honest_negative"


def test_genuine_wins_survive_effective_grade(monkeypatch, tmp_path):
    r = _wf(monkeypatch, tmp_path, [(6.0, 1.5)] * 6)
    assert r["inert_folds"] == 0
    assert r["verdict_effective"] == "PASS"
