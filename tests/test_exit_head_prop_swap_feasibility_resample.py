"""Regression test for the resample-skip bug fixed in
``scripts/research/exit_head_prop_swap_feasibility.py`` (RQ-20260927-003).

THE BUG: the driver called ``backtest_data_source.resolve_or_refuse(...)``,
read the ``resample`` flag it returns onto ``res.resample``, and then never
consulted it -- ``harness._load_candles(res.path)`` ran straight into
``run_backtest`` with whatever grain the resolver actually found on disk.
SOLUSDT/ETHUSDT have no native 1h file (only 5m/15m), so a ``--timeframe 1h``
request silently backtested this leg's 1h-geometry params (donchian window,
atr_stop_mult, trail_mult, timeout_bars=200) against raw 5-minute bars --
~12x the intended bar count, and a different instrument's semantics entirely.
This is the same defect class as
``BL-20260813-HARNESS-SYMBOL-IS-A-LABEL-DATA-DEFAULTS-TO-BTC`` (a declared
input silently not being what actually ran), and it is the confirmed
root cause of both prior RQ-20260927-003 dispatches exceeding the
trainer-vm-diag 60-minute job cap.

This test does not touch the trainer or any real candle/artifact data (both
absent in this checkout, per the unit's own
``routing.needs_trainer_resident_data: true``). It pins the ONE behaviour
the fix adds: when the resolver flags ``resample``, the dataframe handed to
``run_backtest`` is the RESAMPLED one, not the raw one.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys

import pytest

pd = pytest.importorskip("pandas")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "scripts", "ml"))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_REPO, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


driver = _load("_rq003_prop_swap_driver",
                "scripts/research/exit_head_prop_swap_feasibility.py")

# driver.main() does `from exit_head_replay import ...` and
# `from scripts.ops import backtest_data_source` INSIDE its own body, so these
# must be the REAL, canonically-named modules (found via sys.modules on each
# call) rather than aliased _load() copies -- otherwise a monkeypatch here
# would land on a different module object than the one driver.py resolves.
import exit_head_replay as replay  # noqa: E402  (scripts/ml on sys.path above)
backtest_data_source = importlib.import_module("scripts.ops.backtest_data_source")


def _synthetic_5m_frame(n_bars: int = 288) -> "pd.DataFrame":
    """24h of 5m bars (288 of them) -> should resample down to ~24 1h bars.

    A real (non-flat) price path: `candle_io.load_candles` refuses a
    degenerate (zero-variance) series (MI-157), so this is a small
    deterministic sine-driven walk rather than a constant close.
    """
    import math
    idx = pd.date_range("2026-01-01", periods=n_bars, freq="5min", tz="UTC")
    close = [100.0 + math.sin(i / 7.0) * 2.0 + (i % 5) * 0.01 for i in range(n_bars)]
    high = [c + 0.5 for c in close]
    low = [c - 0.5 for c in close]
    open_ = [100.0] + close[:-1]
    return pd.DataFrame({
        "timestamp": idx, "open": open_, "high": high, "low": low,
        "close": close, "volume": 1.0,
    })


class _FakeResolution:
    def __init__(self, path, resample):
        self.ok = True
        self.path = path
        self.resample = resample


def test_resample_flag_is_applied_before_run_backtest(monkeypatch, tmp_path):
    raw = _synthetic_5m_frame()
    raw_path = tmp_path / "ETHUSDT_5m.csv"
    raw.to_csv(raw_path, index=False)

    captured = {}

    def _fake_run_backtest(df, **kwargs):
        captured["n_bars"] = len(df)
        captured["cols"] = set(df.columns)
        return {"net_total_r": 0.0}

    # Real harness module (backtest_trend.py) so `_load_candles` / `_resample`
    # are the genuine functions -- only `run_backtest` is stubbed, and only to
    # avoid needing a full config-exact strategy run for this unit test.
    harness = _load("_bt_trend_for_resample_test", "scripts/backtest_trend.py")
    monkeypatch.setattr(harness, "run_backtest", _fake_run_backtest)

    monkeypatch.setattr(replay, "_load_harness", lambda: harness)
    monkeypatch.setattr(replay, "_apply_venue_cost_policy", lambda h, sym: {"fake": True})
    monkeypatch.setattr(
        replay, "load_heads",
        lambda artifact_dir, tf, sym: [({"model_id": "fake-test-head", "stage": "test"}, None)],
    )
    monkeypatch.setattr(
        backtest_data_source, "resolve_or_refuse",
        lambda symbol, timeframe, legacy_default: _FakeResolution(str(raw_path), "1h"),
    )

    rc = driver.main([
        "exit_head_prop_swap_feasibility.py",
        "--strategy", "trend_donchian_eth_prop",
        "--symbol", "ETHUSDT",
        "--timeframe", "1h",
    ])

    assert rc == 0
    # 288 raw 5m bars must have been collapsed to ~24-25 1h bars (exact count
    # depends on the right-closed/right-labeled bucket edges) BEFORE reaching
    # run_backtest -- the exact bug: without the fix this would read 288.
    assert 20 <= captured["n_bars"] <= 26, captured["n_bars"]
    assert captured["n_bars"] < len(raw)
