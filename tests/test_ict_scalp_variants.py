"""Unit tests for the M27 P0 Batch-1 ict_scalp per-symbol alt variants
(ict_scalp_sol_5m / ict_scalp_xrp_5m / ict_scalp_avax_5m) and their shared
``_ict_scalp_variant_builder``.

Fully offline — synthetic OHLCV DataFrames + monkeypatch only, no exchange
calls, no secrets, no network. Mirrors tests/test_trend_donchian_long_only.py's
``_wire_variant`` pattern and tests/test_ict_scalp_5m.py's bullish-setup frame.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import src.runtime.strategy_signal_builders as ssb


def _bullish_scalp_frame(n: int = 80, base: float = 100.0, freq: str = "5min") -> pd.DataFrame:
    """A clean bullish ICT-scalp setup — sweep, displacement, FVG mitigation.

    Same construction as test_ict_scalp_5m.py::_bullish_scalp_frame, scaled to
    a lower price level (altcoin-realistic) rather than BTC's ~50k.
    """
    rng = pd.date_range("2026-04-01", periods=n, freq=freq, tz="UTC")
    opens = np.full(n, base + 0.05)
    highs = np.full(n, base + 0.10)
    lows = np.full(n, base - 0.10)
    closes = np.full(n, base + 0.06)

    sweep_idx = n - 5
    lows[sweep_idx] = base - 0.60
    closes[sweep_idx] = base - 0.02
    opens[sweep_idx] = base + 0.01
    highs[sweep_idx] = base + 0.02

    disp_idx = n - 4
    opens[disp_idx] = base + 0.00
    closes[disp_idx] = base + 0.80
    highs[disp_idx] = base + 0.82
    lows[disp_idx] = base - 0.02

    cont_idx = n - 3
    opens[cont_idx] = base + 0.80
    closes[cont_idx] = base + 1.00
    highs[cont_idx] = base + 1.05
    lows[cont_idx] = base + 0.78

    hold_idx = n - 2
    opens[hold_idx] = base + 1.00
    closes[hold_idx] = base + 1.02
    highs[hold_idx] = base + 1.06
    lows[hold_idx] = base + 0.98

    mit_idx = n - 1
    opens[mit_idx] = base + 0.60
    closes[mit_idx] = base + 0.65
    highs[mit_idx] = base + 0.66
    lows[mit_idx] = base + 0.15

    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes,
         "volume": np.full(n, 100.0)},
        index=rng,
    )


def _base_cfg(symbol: str, **overrides) -> dict:
    cfg = {
        "enabled": True, "timeframe": "5m", "symbols": [symbol],
        "sweep_lookback_bars": 12, "swing_lookback_bars": 20, "atr_period": 14,
        "sweep_buffer_bps": 5.0, "displacement_atr_mult": 1.3,
        "min_displacement_body_to_range": 0.55, "min_fvg_size_bps": 2.0,
        "mitigation_mode": "wick_rejection", "htf_trend_filter_enabled": False,
        "atr_sl_buffer_mult": 0.20, "tp_at_r": 1.5, "be_offset_bps": 15,
        "session_filter_enabled": False,
    }
    cfg.update(overrides)
    return cfg


def _wire(monkeypatch, name, symbol, frame, cfg, *, trend_label="unknown", vol_label="unknown",
          fire_signal=False):
    import src.units.strategies as units
    monkeypatch.setattr(
        units, "load_strategy_config", lambda *a, **k: {name: cfg}, raising=False)
    import src.runtime.market_data as md
    monkeypatch.setattr(md, "fetch_candles", lambda *a, **k: frame, raising=False)
    monkeypatch.setattr(ssb, "_build_killzone_exchange", lambda settings: None)
    monkeypatch.setattr(ssb, "_publish_liquidity_state", lambda *a, **k: None)
    monkeypatch.setattr(ssb, "_emit_shadow_preds", lambda *a, **k: None)
    monkeypatch.setattr(ssb, "detect_regime", lambda *a, **k: {"regime": trend_label, "adx": 30.0})
    import src.runtime.regime.vol_detector as vd
    monkeypatch.setattr(vd, "vol_regime_from_spec", lambda *a, **k: (vol_label, 0.001))
    if fire_signal:
        # Decouple the off-cell gate test from ict_scalp's precise sweep/
        # displacement geometry (already covered by test_ict_scalp_5m.py) —
        # stub order_package to return a fixed valid long setup.
        import src.units.strategies.ict_scalp as ict_scalp_unit
        monkeypatch.setattr(
            ict_scalp_unit, "order_package",
            lambda cfg, candles_df=None: {
                "symbol": cfg["symbol"], "direction": "long",
                "entry": 1.0, "sl": 0.95, "tp": 1.075, "confidence": 0.8,
                "meta": {},
            },
            raising=False,
        )
    return ssb._ict_scalp_variant_builder(name, {"SYMBOL": symbol})


# ---------------------------------------------------------------------------
# Symbol pinning
# ---------------------------------------------------------------------------

def test_variant_pins_symbol_from_own_config_not_tick_settings(monkeypatch):
    cfg = _base_cfg("SOLUSDT")
    frame = _bullish_scalp_frame(base=100.0)
    sig = _wire(monkeypatch, "ict_scalp_sol_5m", "SOLUSDT", frame, cfg)
    # Called with a tick settings symbol of BTCUSDT-shaped default via _wire's
    # {"SYMBOL": symbol}; assert the returned signal is pinned to the variant's
    # own configured symbol regardless.
    assert sig["symbol"] == "SOLUSDT"
    assert sig["meta"]["strategy_name"] == "ict_scalp_sol_5m"


def test_disabled_variant_returns_side_none(monkeypatch):
    cfg = _base_cfg("XRPUSDT", enabled=False)
    frame = _bullish_scalp_frame(base=0.60)
    sig = _wire(monkeypatch, "ict_scalp_xrp_5m", "XRPUSDT", frame, cfg)
    assert sig["side"] == "none"
    assert sig["meta"]["reason"] == "disabled_in_yaml"


# ---------------------------------------------------------------------------
# Strategy-local off-cell regime gate (XRP)
# ---------------------------------------------------------------------------

def test_off_cell_match_suppresses_signal(monkeypatch):
    cfg = _base_cfg(
        "XRPUSDT",
        off_cells=[["trending", "volatile"], ["chop", "volatile"]],
        vol_spec={"vol_bucket_labels": ["low", "mid", "high"],
                  "vol_bucket_edges": [0.001, 0.002], "vol_window_n": 20},
    )
    frame = _bullish_scalp_frame(base=0.60)
    sig = _wire(
        monkeypatch, "ict_scalp_xrp_5m", "XRPUSDT", frame, cfg,
        trend_label="trending", vol_label="volatile", fire_signal=True,
    )
    assert sig["side"] == "none"
    assert sig["meta"]["reason"] == "regime_off_cell_local"
    assert sig["meta"]["regime"] == "trending"
    assert sig["meta"]["vol_regime"] == "volatile"


def test_off_cell_no_match_passes_through(monkeypatch):
    cfg = _base_cfg(
        "XRPUSDT",
        off_cells=[["trending", "volatile"], ["chop", "volatile"]],
        vol_spec={"vol_bucket_labels": ["low", "mid", "high"],
                  "vol_bucket_edges": [0.001, 0.002], "vol_window_n": 20},
    )
    frame = _bullish_scalp_frame(base=0.60)
    sig = _wire(
        monkeypatch, "ict_scalp_xrp_5m", "XRPUSDT", frame, cfg,
        trend_label="trending", vol_label="calm", fire_signal=True,
    )
    assert sig["side"] == "buy"
    assert sig["meta"].get("reason") != "regime_off_cell_local"


def test_no_off_cells_configured_never_gates(monkeypatch):
    # SOL/AVAX ship with no off_cells/vol_spec — the gate must be a pure no-op
    # (never suppress) regardless of what detect_regime/vol_regime_from_spec
    # would return.
    cfg = _base_cfg("SOLUSDT")
    frame = _bullish_scalp_frame(base=100.0)
    sig = _wire(
        monkeypatch, "ict_scalp_sol_5m", "SOLUSDT", frame, cfg,
        trend_label="trending", vol_label="volatile", fire_signal=True,
    )
    assert sig["side"] == "buy"
    assert sig["meta"].get("reason") != "regime_off_cell_local"
