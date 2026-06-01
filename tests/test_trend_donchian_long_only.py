"""trend_donchian long-only gate (2026-06-01, Tier-3, operator-approved).

The regime×direction matrix showed trend_donchian's short side is a net −37 R
drag (earns only in chop), so the live strategy is set ``long_only: true`` in
config/strategies.yaml and the builder suppresses shorts. These tests verify the
opt-in gate: a downtrend yields side=none (never a sell) when long_only is set,
and the default (no flag) still emits a short. Fully offline (synthetic OHLCV +
monkeypatch; no exchange / network / secrets) — mirrors test_mes_trend_long_1d.
"""
from __future__ import annotations

import pandas as pd

import src.runtime.strategy_signal_builders as ssb


def _trend_frame(direction: str, n: int = 60) -> pd.DataFrame:
    """n 1h bars trending up or down so the Donchian unit fires a long/short."""
    step = 1.0 if direction == "up" else -1.0
    rows = []
    for k in range(n):
        base = 30000.0 + step * k * 50.0
        rows.append([base, base + 25.0, base - 25.0, base + 20.0 * step])
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    df["ts"] = pd.date_range("2024-01-01", periods=len(df), freq="1h", tz="UTC")
    return df


def _wire(monkeypatch, frame, *, long_only: bool):
    import src.units.strategies as units
    cfg = {
        "enabled": True, "timeframe": "1h", "donchian": 20, "atr_period": 14,
        "atr_stop_mult": 2.5, "trail_mult": 5.0, "tp_r": 50.0,
        "min_confidence": 0.0,
    }
    if long_only:
        cfg["long_only"] = True
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


def test_short_suppressed_when_long_only(monkeypatch):
    sig = _wire(monkeypatch, _trend_frame("down"), long_only=True)
    assert sig["side"] == "none"
    assert sig["meta"].get("reason") == "short_suppressed_long_only"


def test_long_passes_through_when_long_only(monkeypatch):
    sig = _wire(monkeypatch, _trend_frame("up"), long_only=True)
    # Uptrend fires a long (buy) or is non-actionable (none) — never a sell.
    assert sig["side"] in ("buy", "none")
    assert sig["side"] != "sell"


def test_default_two_sided_still_shorts(monkeypatch):
    # Without the flag the builder keeps the original two-sided behaviour:
    # a downtrend must still be able to emit a sell (the gate is opt-in).
    sig = _wire(monkeypatch, _trend_frame("down"), long_only=False)
    assert sig["side"] in ("sell", "none")
    assert sig["meta"].get("reason") != "short_suppressed_long_only"
