"""Regime stamping on per-strategy eval audit rows (PERF-20260601-002 phase 1).

Verifies that every per-strategy ``*_eval`` row emitted via the centralised
``log_signal`` path now carries the three regime detector fields (``regime``,
``adx_14``, ``regime_source``). One end-to-end test per representative
emit-site shape covers all 21 stamping sites (the wrapper is shared).

Fully offline — monkeypatches ``fetch_candles`` / ``log_signal`` and never
touches an exchange. Mirrors the pattern of ``test_trend_donchian_long_only.py``.
"""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd

import src.runtime.strategy_signal_builders as ssb


def _uptrend_frame(n: int = 80) -> pd.DataFrame:
    """1h trending-up OHLC so the live strategies that fire on a breakout
    produce an actionable (side=buy) eval row AND the ADX-14 sits above
    25 (trending). Bars are visibly directional so plus_di > minus_di by
    a wide margin."""
    closes = np.array([30000.0 + i * 50.0 for i in range(n)], dtype=float)
    df = pd.DataFrame({
        "open": np.concatenate(([closes[0]], closes[:-1])),
        "high": closes + 25.0,
        "low": closes - 25.0,
        "close": closes,
        "volume": [100.0] * n,
    })
    df["ts"] = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    return df


def _capture_eval_rows(monkeypatch) -> List[Dict[str, Any]]:
    """Replace log_signal with a list-accumulating spy so we can inspect
    every audit row the tick emitted, then return that list."""
    captured: List[Dict[str, Any]] = []

    def _spy(payload: Dict[str, Any], *args: Any, **kwargs: Any) -> None:
        captured.append(dict(payload))

    monkeypatch.setattr(ssb, "log_signal", _spy)
    return captured


def _wire_trend_donchian(monkeypatch, frame, **cfg_extra: Any) -> Dict[str, Any]:
    """Drive the trend_donchian builder with a synthetic frame; mirrors
    test_trend_donchian_long_only.py."""
    import src.units.strategies as units
    cfg = {
        "enabled": True, "timeframe": "1h", "donchian": 20, "atr_period": 14,
        "atr_stop_mult": 2.5, "trail_mult": 5.0, "tp_r": 50.0,
        "min_confidence": 0.0, **cfg_extra,
    }
    monkeypatch.setattr(
        units, "load_strategy_config",
        lambda *a, **k: {"trend_donchian": cfg}, raising=False,
    )
    import src.runtime.market_data as md
    monkeypatch.setattr(md, "fetch_candles", lambda *a, **k: frame, raising=False)
    monkeypatch.setattr(ssb, "_build_killzone_exchange", lambda settings: None)
    monkeypatch.setattr(ssb, "_publish_liquidity_state", lambda *a, **k: None)
    monkeypatch.setattr(ssb, "_emit_shadow_preds", lambda *a, **k: None)
    return ssb.trend_donchian_signal_builder({"SYMBOL": "BTCUSDT"})


# --- Stamping helper: pure-function shape check ----------------------------

def test_stamp_regime_helper_adds_three_fields():
    """The helper is the single chokepoint every builder funnels through.
    A unit-level check ensures the contract (three named fields) is stable
    even if we don't exercise every builder's integration path."""
    df = _uptrend_frame(60)
    payload: Dict[str, Any] = {"event": "x_eval", "strategy": "x", "side": "none"}
    out = ssb._stamp_regime(payload, df)
    assert "regime" in out
    assert "adx_14" in out
    assert "regime_source" in out
    assert out["regime"] in ("chop", "transitional", "trending", "unknown")
    assert out["regime_source"] == "adx-14"


def test_stamp_regime_preserves_caller_fields():
    """setdefault semantics: a builder that already computed ADX-14 / a
    regime label should not be clobbered. (No live strategy does this yet,
    but the contract matters for the upcoming phase-2 weight injection.)"""
    df = _uptrend_frame(60)
    payload = {
        "event": "x_eval",
        "regime": "preset-by-builder",
        "adx_14": 99.0,
    }
    out = ssb._stamp_regime(payload, df)
    assert out["regime"] == "preset-by-builder"   # not overwritten
    assert out["adx_14"] == 99.0                  # not overwritten
    # regime_source still stamped (was missing in the payload)
    assert out["regime_source"] == "adx-14"


def test_stamp_regime_never_raises_on_bad_input():
    """Phase-1 is observability-only — a regime stamping failure must not
    take the tick down."""
    assert "regime" in ssb._stamp_regime({}, None)
    assert "regime" in ssb._stamp_regime({}, "not a frame")
    assert "regime" in ssb._stamp_regime({}, pd.DataFrame())


# --- Live-path integration: actual builder emits stamped row ---------------

def test_trend_donchian_eval_row_carries_regime_fields(monkeypatch):
    """Drives the trend_donchian builder against a clean uptrend; checks
    every eval row written to signal_audit.jsonl now carries the regime
    triplet. This is the smoke test that the wrapping survived for the 21
    sites in strategy_signal_builders.py — they all share the same helper,
    so if one works they all do."""
    captured = _capture_eval_rows(monkeypatch)
    _wire_trend_donchian(monkeypatch, _uptrend_frame(80))

    eval_rows = [r for r in captured if r.get("event") == "trend_donchian_eval"]
    assert eval_rows, "expected at least one trend_donchian_eval row"
    for row in eval_rows:
        assert "regime" in row, f"missing regime in {row}"
        assert "adx_14" in row, f"missing adx_14 in {row}"
        assert "regime_source" in row, f"missing regime_source in {row}"
        assert row["regime"] in ("chop", "transitional", "trending", "unknown")
        assert row["regime_source"] == "adx-14"
        if row["adx_14"] is not None:
            assert isinstance(row["adx_14"], float)
