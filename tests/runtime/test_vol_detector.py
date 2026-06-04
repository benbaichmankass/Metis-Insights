"""Volatility-axis regime detector (S-MLOPT-S15b, Phase 3.3 track B).

Covers:
  * ``vol_regime_from_spec`` — calm/volatile collapse against frozen edges,
    and the ``unknown`` degeneracy guards (no labels, no edges, short series).
  * ``detect_vol_regime`` — the ``(symbol, timeframe)``-routed wrapper with an
    injected spec table; permissive ``unknown`` when no spec / no candles.
  * Never-raises contract on malformed candles.
  * Default-preserving: missing symbol/timeframe → ``unknown``.
"""
from __future__ import annotations

import pandas as pd

from src.runtime.regime.vol_detector import (
    VOL_CALM,
    VOL_UNKNOWN,
    VOL_VOLATILE,
    detect_vol_regime,
    vol_regime_from_spec,
)


def _spec(edges, labels=("vol_b0", "vol_b1", "vol_b2"), window_n=5, **extra):
    return {
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "vol_bucket_labels": list(labels),
        "vol_bucket_edges": list(edges),
        "vol_window_n": window_n,
        **extra,
    }


def _candles(closes):
    return pd.DataFrame({"close": closes})


# Constant closes → zero log-return vol → lowest bucket → calm.
_FLAT = [100.0, 100.0, 100.0, 100.0, 100.0, 100.0]
# Big alternating closes → high log-return vol → upper bucket → volatile.
_CHOPPY = [100.0, 110.0, 100.0, 110.0, 100.0, 110.0]


# --- vol_regime_from_spec --------------------------------------------------

def test_vol_from_spec_calm_when_vol_below_lowest_edge():
    out, vol = vol_regime_from_spec(_spec(edges=[0.01, 0.05]), _FLAT)
    assert out == VOL_CALM
    assert vol == 0.0


def test_vol_from_spec_volatile_when_vol_above_lowest_edge():
    out, vol = vol_regime_from_spec(_spec(edges=[0.01, 0.05]), _CHOPPY)
    assert out == VOL_VOLATILE
    assert vol is not None and vol > 0.01


def test_vol_from_spec_two_bucket_spec_collapses():
    """A 2-bucket spec (labels [vol_b0, vol_b1], one edge) works uniformly:
    lowest bucket → calm, the other → volatile."""
    calm, _ = vol_regime_from_spec(_spec(edges=[0.01], labels=("vol_b0", "vol_b1")), _FLAT)
    vol, _ = vol_regime_from_spec(_spec(edges=[0.01], labels=("vol_b0", "vol_b1")), _CHOPPY)
    assert calm == VOL_CALM
    assert vol == VOL_VOLATILE


def test_vol_from_spec_unknown_when_fewer_than_two_labels():
    out, vol = vol_regime_from_spec(_spec(edges=[], labels=("vol_b0",)), _FLAT)
    assert out == VOL_UNKNOWN
    assert vol is None


def test_vol_from_spec_unknown_when_no_edges():
    out, vol = vol_regime_from_spec(_spec(edges=[]), _FLAT)
    assert out == VOL_UNKNOWN


def test_vol_from_spec_unknown_when_too_few_closes():
    out, vol = vol_regime_from_spec(_spec(edges=[0.01], labels=("vol_b0", "vol_b1")), [100.0])
    assert out == VOL_UNKNOWN
    assert vol is None


def test_vol_from_spec_none_spec_is_unknown():
    out, vol = vol_regime_from_spec(None, _FLAT)
    assert out == VOL_UNKNOWN
    assert vol is None


# --- detect_vol_regime (routed wrapper) ------------------------------------

def test_detect_vol_regime_calm_with_injected_specs():
    specs = {("BTCUSDT", "1H"): _spec(edges=[0.01, 0.05])}
    out = detect_vol_regime(_candles(_FLAT), symbol="BTCUSDT", timeframe="1h", specs=specs)
    assert out["vol_regime"] == VOL_CALM
    assert out["rolling_log_return_vol"] == 0.0
    assert out["source"].startswith("vol-bucket-edges")


def test_detect_vol_regime_volatile_with_injected_specs():
    specs = {("BTCUSDT", "1H"): _spec(edges=[0.01, 0.05], model_id="btc-regime-1h-lgbm-v2")}
    out = detect_vol_regime(_candles(_CHOPPY), symbol="BTCUSDT", timeframe="1h", specs=specs)
    assert out["vol_regime"] == VOL_VOLATILE
    assert out["source"] == "vol-bucket-edges:btc-regime-1h-lgbm-v2"


def test_detect_vol_regime_unknown_when_no_spec_for_pair():
    """No deployed head for this (symbol, timeframe) → permissive unknown."""
    specs = {("BTCUSDT", "1H"): _spec(edges=[0.01, 0.05])}
    out = detect_vol_regime(_candles(_FLAT), symbol="MES", timeframe="1d", specs=specs)
    assert out["vol_regime"] == VOL_UNKNOWN
    assert out["rolling_log_return_vol"] is None


def test_detect_vol_regime_unknown_without_symbol_or_timeframe():
    specs = {("BTCUSDT", "1H"): _spec(edges=[0.01, 0.05])}
    assert detect_vol_regime(_candles(_FLAT), symbol=None, timeframe="1h", specs=specs)["vol_regime"] == VOL_UNKNOWN
    assert detect_vol_regime(_candles(_FLAT), symbol="BTCUSDT", timeframe=None, specs=specs)["vol_regime"] == VOL_UNKNOWN


def test_detect_vol_regime_never_raises_on_garbage_candles():
    specs = {("BTCUSDT", "1H"): _spec(edges=[0.01, 0.05])}
    for bad in (None, object(), 12345, "not-a-frame"):
        out = detect_vol_regime(bad, symbol="BTCUSDT", timeframe="1h", specs=specs)
        assert out["vol_regime"] == VOL_UNKNOWN


def test_detect_vol_regime_empty_specs_table_is_unknown():
    out = detect_vol_regime(_candles(_FLAT), symbol="BTCUSDT", timeframe="1h", specs={})
    assert out["vol_regime"] == VOL_UNKNOWN


# --- resolve_vol_specs never-raises / degrades -----------------------------

def test_resolve_vol_specs_returns_dict_and_never_raises():
    """Without a reachable registry (no model artefacts here) it must degrade
    to {} rather than raise — the live tick can never break on this path."""
    from src.runtime.regime.vol_detector import resolve_vol_specs

    out = resolve_vol_specs(force=True)
    assert isinstance(out, dict)
