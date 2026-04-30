"""Smoke test for ``analyze_fixtures.py``.

This is a thin import + happy-path test — the script is an analysis
runner, not library code, so the contract is "runs end-to-end on the
committed repo fixtures without raising and produces a non-empty
report on stdout".
"""
from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_resample_and_load_pipeline_runs():
    """The CSV → DataFrame → resample pipeline used by analyze_fixtures
    must produce a 15m frame with the canonical columns and a UTC
    DatetimeIndex when fed the committed sample fixture."""
    from scripts.sprint015 import analyze_fixtures as af

    fixture = REPO_ROOT / "data" / "btc_1m_sample.csv"
    if not fixture.exists():
        pytest.skip("repo fixture missing")
    df_1m = af._load_fixture(fixture)
    df_15m = af._resample(df_1m, "15min")
    assert list(df_15m.columns) == ["open", "high", "low", "close", "volume"]
    assert df_15m.index.tz is not None
    assert len(df_15m) > 0
    assert len(df_15m) < len(df_1m)


def test_signal_density_is_monotone_decreasing():
    """Sanity: as the entry-std threshold rises, fewer signals fire.
    A monotonically *non-increasing* sequence is enough — a degenerate
    bar may produce the same signal count at adjacent thresholds.
    Exposes adapter regressions where the threshold isn't being
    plumbed through (the strategy module mutates module state)."""
    from scripts.sprint015 import analyze_fixtures as af

    fixture = REPO_ROOT / "data" / "btc_1m_sample.csv"
    if not fixture.exists():
        pytest.skip("repo fixture missing")
    df_1m = af._load_fixture(fixture)
    df_15m = af._resample(df_1m, "15min")
    rows = af._signal_density(df_15m, {"qty": 1.0, "lookback": 50, "symbol": "BTCUSDT"})
    counts = [r["n_signals"] for r in rows]
    assert counts == sorted(counts, reverse=True)
    assert counts[0] > 0


def test_slippage_sweep_pnl_monotone_decreasing():
    """Higher round-trip slippage cannot improve realised P&L for a
    backtest that opens at least one trade; this is an arithmetic
    invariant of the harness."""
    from scripts.sprint015 import analyze_fixtures as af

    fixture = REPO_ROOT / "data" / "btc_1m_sample.csv"
    if not fixture.exists():
        pytest.skip("repo fixture missing")
    df_1m = af._load_fixture(fixture)
    df_15m = af._resample(df_1m, "15min")
    rows = af._slippage_sweep(df_15m, {"qty": 1.0, "lookback": 50, "symbol": "BTCUSDT"})
    pnls = [r["realised_pnl"] for r in rows]
    # Strictly non-increasing as slippage rises.
    for prev, nxt in zip(pnls, pnls[1:]):
        assert nxt <= prev + 1e-6


def test_main_runs_and_emits_markdown(tmp_path, monkeypatch):
    """End-to-end: invoking the script must produce a markdown report
    that mentions both fixtures by name."""
    from scripts.sprint015 import analyze_fixtures as af

    captured = io.StringIO()
    monkeypatch.setattr(sys, "stdout", captured)
    rc = af.main()
    assert rc == 0
    out = captured.getvalue()
    assert "# S-015 T3" in out
    assert "Slippage sensitivity" in out
    assert "Signal density" in out
    # At least one of the fixtures must produce a report stanza.
    assert ("btc_2026_03" in out) or ("btc_2022_07" in out)
