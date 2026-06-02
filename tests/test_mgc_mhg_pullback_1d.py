"""Wiring + fidelity tests for the WS-A metals sleeve (mgc_pullback_1d / mhg_pullback_1d).

Both strategies reuse the EXISTING unit ``src.units.strategies.htf_pullback_trend_2h``
(no new strategy logic). These tests pin what makes the metals sleeve distinct from
the BTC htf_pullback instance and from the MES sleeve:

  1. FIDELITY — the unit accepts the validated metals configs (frac 0.618 for MGC,
     0.5 for MHG) on a synthetic daily frame and either returns a well-formed package
     or raises ValueError deterministically; the symbol field flows through.
  2. SUPPORTED_SYMBOLS — MGC / MHG are whitelisted and a StrategyIntent constructs.
  3. INSTRUMENTS PROFILE — MGC / MHG load with exchange interactive_brokers + the
     COMEX micro-metal tick sizes.
  4. BUILDER SMOKE — the signal builders return a pipeline-shape dict with the right
     symbol / pattern / meta.strategy_name, trading BOTH directions (no long-only gate,
     unlike the MES builder).

Fully offline (synthetic OHLCV + monkeypatch; no exchange / network / secrets).
"""
from __future__ import annotations

import pandas as pd
import pytest

import src.runtime.strategy_signal_builders as ssb


# Validated WS-A configs (mirror config/strategies.yaml; scripts/backtest_pullback.py
# mirrors htf_pullback_trend_2h.order_package exactly, so the unit must accept these).
_MGC_CFG = {
    "symbol": "MGC", "timeframe": "1d",
    "trend_lookback": 40, "pullback_lookback": 15, "pullback_frac": 0.618,
    "atr_period": 14, "atr_stop_mult": 2.0, "trail_mult": 4.0,
    "timeout_bars": 200, "tp_r": 50.0, "min_confidence": 0.0,
}
_MHG_CFG = {
    "symbol": "MHG", "timeframe": "1d",
    "trend_lookback": 40, "pullback_lookback": 15, "pullback_frac": 0.5,
    "atr_period": 14, "atr_stop_mult": 2.0, "trail_mult": 4.0,
    "timeout_bars": 200, "tp_r": 50.0, "min_confidence": 0.0,
}


def _daily_frame(direction: str, n: int = 80) -> pd.DataFrame:
    """n daily bars: a gentle trend, then a deep pullback + confirmation bar.

    The setup the htf_pullback unit looks for: an HTF trend (close above/below
    the Donchian-40 midline) AND a short-term pullback into the lower (long) /
    upper (short) part of the recent 15-bar range, closed on a reversal-
    confirmation bar (close back in the trend direction). Engineered so the unit
    deterministically fires a long (up) / short (down).
    """
    step = 1.0 if direction == "up" else -1.0
    rows = []
    base = 1900.0
    # Gentle trend so price sits above (up) / below (down) the Donchian midline.
    for k in range(n - 2):
        c = base + step * k * 0.5
        rows.append([c, c + 1.0, c - 1.0, c + 0.2 * step])
    prev = rows[-1][3]
    if direction == "up":
        # Penultimate bar: a deep dip — sets a low recent-range floor AND a low
        # prev_close so the final bar can close *above* it (confirmation).
        dip = prev - 8.0
        rows.append([prev, prev + 0.5, dip - 1.0, dip])
        # Final bar: close back up but still low in the recent range.
        rows.append([dip, dip + 2.0, dip - 0.5, dip + 1.5])
    else:
        spike = prev + 8.0
        rows.append([prev, spike + 1.0, prev - 0.5, spike])
        rows.append([spike, spike + 0.5, spike - 2.0, spike - 1.5])
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    df["timestamp"] = pd.date_range("2024-01-01", periods=len(df), freq="1D", tz="UTC")
    return df


# ---------------------------------------------------------------------------
# 1. FIDELITY — the unit accepts the validated metals configs deterministically.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("cfg,symbol", [(_MGC_CFG, "MGC"), (_MHG_CFG, "MHG")])
@pytest.mark.parametrize("direction", ["up", "down"])
def test_order_package_accepts_metals_config(cfg, symbol, direction):
    from src.units.strategies.htf_pullback_trend_2h import order_package
    frame = _daily_frame(direction)
    try:
        pkg = order_package(cfg, candles_df=frame)
    except ValueError:
        # A non-actionable tick is a valid deterministic outcome (side none).
        return
    # Well-formed package — the symbol flows through, prices are coherent.
    assert pkg["symbol"] == symbol
    assert pkg["direction"] in ("long", "short")
    assert pkg["entry"] > 0 and pkg["sl"] > 0 and pkg["tp"] > 0
    if pkg["direction"] == "long":
        assert pkg["sl"] < pkg["entry"] < pkg["tp"]
    else:
        assert pkg["tp"] < pkg["entry"] < pkg["sl"]


@pytest.mark.parametrize("cfg,symbol", [(_MGC_CFG, "MGC"), (_MHG_CFG, "MHG")])
def test_order_package_fires_long_on_engineered_pullback(cfg, symbol):
    """Prove the unit ACTUALLY produces a well-formed package (not just
    ValueError) on the engineered uptrend-pullback frame — so the fidelity
    test above is exercising the actionable path for both fracs."""
    from src.units.strategies.htf_pullback_trend_2h import order_package
    pkg = order_package(cfg, candles_df=_daily_frame("up"))
    assert pkg["symbol"] == symbol
    assert pkg["direction"] == "long"
    assert pkg["sl"] < pkg["entry"] < pkg["tp"]


def test_order_package_is_deterministic():
    from src.units.strategies.htf_pullback_trend_2h import order_package
    frame = _daily_frame("up")
    out = []
    for _ in range(2):
        try:
            out.append(order_package(dict(_MGC_CFG), candles_df=frame.copy())["direction"])
        except ValueError:
            out.append("none")
    assert out[0] == out[1]


# ---------------------------------------------------------------------------
# 2. SUPPORTED_SYMBOLS — MGC / MHG whitelisted; StrategyIntent constructs.
# ---------------------------------------------------------------------------
def test_metals_in_supported_symbols():
    from src.runtime.intents import SUPPORTED_SYMBOLS
    assert "MGC" in SUPPORTED_SYMBOLS
    assert "MHG" in SUPPORTED_SYMBOLS


@pytest.mark.parametrize("symbol", ["MGC", "MHG"])
def test_strategy_intent_constructs_for_metals(symbol):
    from src.runtime.intents import StrategyIntent
    intent = StrategyIntent(
        strategy=f"{symbol.lower()}_pullback_1d",
        symbol=symbol,
        side="long",
        target_qty=0.0,
    )
    assert intent.symbol == symbol


def test_default_priorities_registered():
    from src.runtime.intents import DEFAULT_PRIORITIES
    assert DEFAULT_PRIORITIES.get("mgc_pullback_1d") == 0
    assert DEFAULT_PRIORITIES.get("mhg_pullback_1d") == 0


# ---------------------------------------------------------------------------
# 3. INSTRUMENTS PROFILE — MGC / MHG load with the COMEX-micro spec.
# ---------------------------------------------------------------------------
def test_instrument_profiles_for_metals():
    from src.core.profile_loader import load_instrument_profiles
    profiles = load_instrument_profiles()
    mgc = profiles.get("MGC")
    mhg = profiles.get("MHG")
    assert mgc is not None, "MGC missing from config/instruments.yaml"
    assert mhg is not None, "MHG missing from config/instruments.yaml"
    assert mgc.exchange == "interactive_brokers"
    assert mhg.exchange == "interactive_brokers"
    assert mgc.tick_size == pytest.approx(0.10)
    assert mhg.tick_size == pytest.approx(0.0005)
    assert mgc.category == "futures"
    assert mhg.category == "futures"


def test_ib_tick_size_lookup():
    from src.units.accounts.ib_client import tick_size_for
    assert tick_size_for("MGC") == pytest.approx(0.10)
    assert tick_size_for("MHG") == pytest.approx(0.0005)
    assert tick_size_for("MES") == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# 4. BUILDER SMOKE — pipeline-shape dict, right symbol/pattern, BOTH directions.
# ---------------------------------------------------------------------------
def _wire_builder(monkeypatch, builder, strategy_name, cfg, symbol, frame):
    import src.units.strategies as units
    monkeypatch.setattr(
        units, "load_strategy_config",
        lambda *a, **k: {strategy_name: {**cfg, "enabled": True}},
        raising=False,
    )
    import src.runtime.market_data as md
    monkeypatch.setattr(md, "fetch_candles", lambda *a, **k: frame, raising=False)
    monkeypatch.setattr(ssb, "_build_killzone_exchange", lambda settings: None)
    monkeypatch.setattr(ssb, "_publish_liquidity_state", lambda *a, **k: None)
    monkeypatch.setattr(ssb, "_emit_shadow_preds", lambda *a, **k: None)
    return builder({"SYMBOL": symbol})


@pytest.mark.parametrize(
    "builder_name,strategy_name,cfg,symbol",
    [
        ("mgc_pullback_1d_signal_builder", "mgc_pullback_1d", _MGC_CFG, "MGC"),
        ("mhg_pullback_1d_signal_builder", "mhg_pullback_1d", _MHG_CFG, "MHG"),
    ],
)
def test_builder_returns_pipeline_shape(monkeypatch, builder_name, strategy_name, cfg, symbol):
    builder = getattr(ssb, builder_name)
    sig = _wire_builder(monkeypatch, builder, strategy_name, cfg, symbol, _daily_frame("up"))
    assert sig["symbol"] == symbol
    assert sig["side"] in ("buy", "sell", "none")
    if sig["side"] != "none":
        assert sig["pattern"] == strategy_name
        assert sig["meta"]["strategy_name"] == strategy_name
        assert sig["entry_price"] > 0


@pytest.mark.parametrize(
    "builder_name,strategy_name,cfg,symbol",
    [
        ("mgc_pullback_1d_signal_builder", "mgc_pullback_1d", _MGC_CFG, "MGC"),
        ("mhg_pullback_1d_signal_builder", "mhg_pullback_1d", _MHG_CFG, "MHG"),
    ],
)
def test_builder_disabled_returns_none(monkeypatch, builder_name, strategy_name, cfg, symbol):
    import src.units.strategies as units
    monkeypatch.setattr(
        units, "load_strategy_config",
        lambda *a, **k: {strategy_name: {**cfg, "enabled": False}},
        raising=False,
    )
    monkeypatch.setattr(ssb, "_build_killzone_exchange", lambda settings: None)
    sig = getattr(ssb, builder_name)({"SYMBOL": symbol})
    assert sig["side"] == "none"
    assert sig["meta"]["reason"] == "disabled_in_yaml"


def test_builder_emits_short_no_long_only_gate(monkeypatch):
    """Unlike the MES builder, the pullback builders trade BOTH directions.

    On a downtrend frame the unit fires a short (sell) — the builder must NOT
    suppress it. If the synthetic frame is non-actionable we accept side=none,
    but it must NEVER coerce a short into none via a long-only gate that does
    not exist here.
    """
    frame = _daily_frame("down")
    sig = _wire_builder(
        monkeypatch, ssb.mgc_pullback_1d_signal_builder,
        "mgc_pullback_1d", _MGC_CFG, "MGC", frame,
    )
    assert sig["side"] in ("sell", "none")
    # Never coerced to none by a (non-existent) long-only gate.
    assert sig["meta"].get("reason") != "short_suppressed_long_only"
    if sig["side"] == "sell":
        # short package: tp < entry < sl
        assert sig["take_profit"] < sig["entry_price"] < sig["stop_loss"]
