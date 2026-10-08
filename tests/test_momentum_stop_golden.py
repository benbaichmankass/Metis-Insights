"""MOMENTUM-TRAIL-LEVER golden: the existing arms are bit-for-bit unchanged.

`momentum_trail` (--trail-adx-*) and `momentum_stale` (--momentum-stale-*) are
new exit levers on `backtest_trend.py` / `backtest_pullback.py`. Every prior
verdict was produced without them, so with the flags unset each harness must
reproduce the summary dict AND the emitted trade rows it produced BEFORE the
levers existed (`tests/fixtures/momentum_stop_default_golden.json`, captured from
the unmodified harnesses; regenerate only on purpose:
``python3 tests/test_momentum_stop_golden.py --capture``). The arms cover the
base trail and every existing exit lever (vol_trail, trail_decay, stale_stop,
giveback, capped TP) on both harnesses. A golden that traded nothing proves
nothing, so each case asserts it traded.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")

_REPO = Path(__file__).resolve().parents[1]
GOLDEN = _REPO / "tests" / "fixtures" / "momentum_stop_default_golden.json"


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, str(_REPO / rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


TREND = _load("_mom_trend", "scripts/backtest_trend.py")
PULLBACK = _load("_mom_pullback", "scripts/backtest_pullback.py")


def _candles(rule="5min"):
    df = pd.read_csv(_REPO / "data" / "backtest_candles.csv")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return (df.set_index("timestamp").resample(rule, label="right", closed="right")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna().reset_index())


TREND_KW = dict(donchian=20, atr_period=14, atr_stop_mult=2.5, trail_mult=3.0,
                timeout_bars=200, cooldown_bars=1, timeframe="5min", symbol="BTCUSDT")
PULLBACK_KW = dict(trend_lookback=40, pullback_lookback=10, pullback_frac=0.5,
                   atr_period=14, atr_stop_mult=2.5, trail_mult=5.0,
                   timeout_bars=200, cooldown_bars=1, timeframe="5min",
                   symbol="BTCUSDT")

_VOL = dict(trail_vol_above_pctl=0.8, vol_pctl_window=100, trail_vol_tight_mult=1.5)
_DECAY = dict(trail_decay_stall_bars=6, trail_decay_tight_mult=1.5)
_STALE = dict(stale_exit_bars=8, stale_exit_below_r=0.3)
_GIVE = dict(giveback_min_mfe_r=1.0, giveback_r=0.5)
_CAP = dict(tp_cap_pct=0.099, tp_r=1.5)

CASES = {
    "trend_trail_only": ("trend", {}),
    "trend_capped_tp": ("trend", _CAP),
    "trend_vol_trail": ("trend", _VOL),
    "trend_trail_decay": ("trend", _DECAY),
    "trend_stale": ("trend", _STALE),
    "trend_giveback": ("trend", _GIVE),
    "trend_adx_gate": ("trend", dict(adx_min=20.0)),
    "pullback_trail_only": ("pullback", {}),
    "pullback_capped_tp": ("pullback", _CAP),
    "pullback_vol_trail": ("pullback", _VOL),
    "pullback_trail_decay": ("pullback", _DECAY),
    "pullback_stale": ("pullback", _STALE),
    "pullback_giveback": ("pullback", _GIVE),
    "pullback_adx_gate": ("pullback", dict(adx_min=20.0)),
}


def run_case(case_id, tmp_path, extra=None):
    kind, over = CASES[case_id]
    emit = Path(tmp_path) / f"{case_id}.jsonl"
    mod, base = (TREND, TREND_KW) if kind == "trend" else (PULLBACK, PULLBACK_KW)
    out = mod.run_backtest(_candles().copy(), emit_path=str(emit),
                           **{**base, **over, **(extra or {})})
    rows = [json.loads(ln) for ln in emit.read_text().splitlines()] if emit.exists() else []
    summary = json.loads(json.dumps(out, default=str))
    summary.pop("run_date", None)   # the only wall-clock field in the summary
    return summary, rows


@pytest.mark.skipif(not GOLDEN.exists(), reason="golden not captured")
@pytest.mark.parametrize("cid", list(CASES))
def test_defaults_reproduce_pre_lever_golden(cid, tmp_path):
    golden = json.loads(GOLDEN.read_text())[cid]
    summary, rows = run_case(cid, tmp_path)
    assert golden["rows"], f"{cid}: golden holds no trades -- the equality would prove nothing"
    assert summary == golden["summary"]
    assert rows == golden["rows"]


if __name__ == "__main__" and "--capture" in sys.argv:
    with tempfile.TemporaryDirectory() as d:
        g = {cid: dict(zip(("summary", "rows"), run_case(cid, d))) for cid in CASES}
    GOLDEN.write_text(json.dumps(g, indent=1, sort_keys=True) + "\n")
    print({k: len(v["rows"]) for k, v in g.items()})
