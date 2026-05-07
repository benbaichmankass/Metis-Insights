"""Tests for scripts/s006_ict_synthetic_validate.py."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.s006_ict_synthetic_validate import (  # noqa: E402
    make_synthetic_ohlcv,
    run_synthetic_validation,
    _verdict,
    render_report,
    SYMBOLS,
)


# ---------------------------------------------------------------------------
# make_synthetic_ohlcv — structural checks
# ---------------------------------------------------------------------------

def test_shape_and_columns():
    df = make_synthetic_ohlcv(n=500, regime="bullish", base_price=100.0, seed=1)
    assert len(df) == 500
    for col in ("timestamp", "open", "high", "low", "close", "volume"):
        assert col in df.columns


def test_ohlcv_invariants():
    df = make_synthetic_ohlcv(n=500, regime="bullish", base_price=100.0, seed=2)
    assert (df["high"] >= df["low"]).all(), "high < low found"
    assert (df["high"] >= df["open"]).all(), "high < open found"
    assert (df["high"] >= df["close"]).all(), "high < close found"
    assert (df["low"] <= df["open"]).all(), "low > open found"
    assert (df["low"] <= df["close"]).all(), "low > close found"
    assert (df["volume"] > 0).all(), "zero/negative volume found"


def test_timestamps_monotone():
    df = make_synthetic_ohlcv(n=300, regime="bullish", seed=3)
    assert df["timestamp"].is_monotonic_increasing


@pytest.mark.parametrize("regime", ["bullish", "bearish", "mixed", "ranging"])
def test_all_regimes_produce_correct_length(regime):
    df = make_synthetic_ohlcv(n=400, regime=regime, seed=7)
    assert len(df) == 400


def test_deterministic_with_same_seed():
    df1 = make_synthetic_ohlcv(n=200, seed=42)
    df2 = make_synthetic_ohlcv(n=200, seed=42)
    assert (df1["close"] == df2["close"]).all()


def test_different_seeds_differ():
    df1 = make_synthetic_ohlcv(n=200, seed=1)
    df2 = make_synthetic_ohlcv(n=200, seed=2)
    assert not (df1["close"] == df2["close"]).all()


# ---------------------------------------------------------------------------
# FVG presence — bullish regime must produce FVG-triggerable patterns
# ---------------------------------------------------------------------------

def test_bullish_regime_has_fvgs():
    """At least some bars should have low[i] > high[i-2] (bullish FVG)."""
    df = make_synthetic_ohlcv(n=2000, regime="bullish", seed=10)
    fvg_count = sum(
        df["low"].iloc[i] > df["high"].iloc[i - 2]
        for i in range(2, len(df))
    )
    assert fvg_count >= 10, f"Expected FVGs in bullish regime, got {fvg_count}"


def test_bearish_regime_has_fvgs():
    df = make_synthetic_ohlcv(n=2000, regime="bearish", seed=11)
    fvg_count = sum(
        df["high"].iloc[i] < df["low"].iloc[i - 2]
        for i in range(2, len(df))
    )
    assert fvg_count >= 10, f"Expected bearish FVGs, got {fvg_count}"


# ---------------------------------------------------------------------------
# run_synthetic_validation — end-to-end
# ---------------------------------------------------------------------------

def test_validation_produces_50_plus_trades():
    results = run_synthetic_validation()
    total = sum(r["summary"].get("total_trades", 0) for r in results
                if "error" not in r["summary"])
    assert total >= 50, f"Expected ≥50 trades, got {total}"


def test_validation_go_verdict():
    results = run_synthetic_validation()
    go, agg = _verdict(results)
    assert go is True, (
        f"Expected GO (PF>1.2), got PF={agg['avg_profit_factor']}, "
        f"trades={agg['total_trades']}"
    )


def test_validation_all_symbols_present():
    results = run_synthetic_validation()
    returned = {r["symbol"] for r in results}
    expected = {s[0] for s in SYMBOLS}
    assert returned == expected


def test_validation_pf_above_threshold():
    results = run_synthetic_validation()
    _, agg = _verdict(results)
    assert agg["avg_profit_factor"] > 1.2


# ---------------------------------------------------------------------------
# render_report
# ---------------------------------------------------------------------------

def test_render_report_contains_go():
    results = run_synthetic_validation()
    md = render_report(results)
    assert "GO" in md
    for sym, *_ in SYMBOLS:
        assert sym in md


def test_render_report_is_string():
    results = run_synthetic_validation()
    md = render_report(results)
    assert isinstance(md, str)
    assert len(md) > 500


# ---------------------------------------------------------------------------
# main() CLI
# ---------------------------------------------------------------------------

def test_main_writes_report(tmp_path: Path):
    from scripts.s006_ict_synthetic_validate import main
    out = tmp_path / "report.md"
    rc = main(["--output", str(out), "--quiet"])
    assert rc == 0
    assert out.exists()
    content = out.read_text()
    assert "GO" in content
    assert "BTCUSDT" in content
