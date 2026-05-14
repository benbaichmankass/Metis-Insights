"""Runtime wiring tests for the ict_scalp_5m signal builder.

The unit tests at tests/test_ict_scalp_5m.py cover the pure strategy
logic. These tests cover the runtime path — specifically v2's HTF
candle fetch and the cfg threading into ``order_package``. Without
this layer, flipping ``enabled: true`` in YAML would ship the
v2-no-HTF variant (weaker performance) instead of the v2-default
variant the backtest validated.
"""
from __future__ import annotations

import sys
import types
from unittest import mock

# Stub matplotlib so the pipeline import chain works without it.
if "matplotlib" not in sys.modules:
    _mpl_stub = types.ModuleType("matplotlib")
    _mpl_stub.pyplot = mock.MagicMock()
    sys.modules["matplotlib"] = _mpl_stub
    sys.modules["matplotlib.pyplot"] = mock.MagicMock()

import pandas as pd


def _ohlcv(prices, freq="5min", start="2026-04-01"):
    rng = pd.date_range(start, periods=len(prices), freq=freq, tz="UTC")
    return pd.DataFrame(
        {
            "timestamp": rng,
            "open": prices,
            "high": [p + 50 for p in prices],
            "low": [p - 50 for p in prices],
            "close": [p + 25 for p in prices],
            "volume": [1.0] * len(prices),
        }
    )


class TestICTScalpHTFRuntimeWiring:
    """v2: the runtime builder must fetch HTF candles + thread the
    EMA values through cfg so the unit's bias filter fires live the
    way it did in the backtest."""

    def test_htf_fetch_is_called_with_yaml_params(self, monkeypatch):
        """Runtime fetches HTF candles at the timeframe + period the
        YAML specifies (default 1h / 20)."""
        from src.runtime import strategy_signal_builders as ssb
        from src.units import strategies as strategies_mod

        # Stub YAML so we know exactly what params are in play.
        yaml_cfg = {
            "ict_scalp_5m": {
                "enabled": True,
                "timeframe": "5m",
                "htf_trend_filter_enabled": True,
                "htf_filter_timeframe": "1h",
                "htf_filter_ema_period": 20,
            }
        }
        monkeypatch.setattr(strategies_mod, "load_strategy_config",
                            lambda *a, **kw: yaml_cfg)
        monkeypatch.setattr(ssb, "load_strategy_config",
                            lambda *a, **kw: yaml_cfg, raising=False)

        # Capture every fetch_candles call. Return a small DF so the
        # unit raises a "need more candles" ValueError, which the
        # builder catches and converts to side="none".
        calls = []
        small_df = _ohlcv([50_000] * 5)

        def fake_fetch(symbol, tf, exchange_client=None, limit=None):
            calls.append({"symbol": symbol, "tf": tf, "limit": limit})
            return small_df

        monkeypatch.setattr(
            "src.runtime.market_data.fetch_candles", fake_fetch
        )
        monkeypatch.setattr(ssb, "_build_killzone_exchange", lambda s: object())
        monkeypatch.setattr("src.runtime.liquidity_state.write_state",
                            lambda *a, **kw: None, raising=False)

        sig = ssb.ict_scalp_signal_builder({"SYMBOL": "BTCUSDT"})
        assert sig["side"] == "none"  # short-frame fallback
        timeframes = [c["tf"] for c in calls]
        assert "5m" in timeframes, f"5m fetch missing: {calls}"
        assert "1h" in timeframes, f"1h HTF fetch missing: {calls}"

    def test_htf_fetch_skipped_when_disabled_in_yaml(self, monkeypatch):
        """When ``htf_trend_filter_enabled: false`` the builder must
        NOT fetch HTF candles — saves an exchange round-trip and
        matches the v2-no-HTF variant the operator might use for an
        A/B."""
        from src.runtime import strategy_signal_builders as ssb
        from src.units import strategies as strategies_mod

        yaml_cfg = {
            "ict_scalp_5m": {
                "enabled": True,
                "timeframe": "5m",
                "htf_trend_filter_enabled": False,
            }
        }
        monkeypatch.setattr(strategies_mod, "load_strategy_config",
                            lambda *a, **kw: yaml_cfg)
        monkeypatch.setattr(ssb, "load_strategy_config",
                            lambda *a, **kw: yaml_cfg, raising=False)

        calls = []
        small_df = _ohlcv([50_000] * 5)

        def fake_fetch(symbol, tf, exchange_client=None, limit=None):
            calls.append({"symbol": symbol, "tf": tf, "limit": limit})
            return small_df

        monkeypatch.setattr(
            "src.runtime.market_data.fetch_candles", fake_fetch
        )
        monkeypatch.setattr(ssb, "_build_killzone_exchange", lambda s: object())
        monkeypatch.setattr("src.runtime.liquidity_state.write_state",
                            lambda *a, **kw: None, raising=False)

        ssb.ict_scalp_signal_builder({"SYMBOL": "BTCUSDT"})
        timeframes = [c["tf"] for c in calls]
        assert timeframes == ["5m"], (
            f"HTF fetch should be skipped when disabled, got {timeframes}"
        )

    def test_htf_fetch_failure_degrades_gracefully(self, monkeypatch):
        """If the HTF fetch raises, the 5m fetch must still run and
        the builder must not crash — the filter just stays off this
        tick (operationally equivalent to the v2-no-HTF variant)."""
        from src.runtime import strategy_signal_builders as ssb
        from src.units import strategies as strategies_mod

        yaml_cfg = {
            "ict_scalp_5m": {
                "enabled": True,
                "timeframe": "5m",
                "htf_trend_filter_enabled": True,
                "htf_filter_timeframe": "1h",
            }
        }
        monkeypatch.setattr(strategies_mod, "load_strategy_config",
                            lambda *a, **kw: yaml_cfg)
        monkeypatch.setattr(ssb, "load_strategy_config",
                            lambda *a, **kw: yaml_cfg, raising=False)

        small_df = _ohlcv([50_000] * 5)

        def fake_fetch(symbol, tf, exchange_client=None, limit=None):
            if tf == "1h":
                raise RuntimeError("simulated HTF outage")
            return small_df

        monkeypatch.setattr(
            "src.runtime.market_data.fetch_candles", fake_fetch
        )
        monkeypatch.setattr(ssb, "_build_killzone_exchange", lambda s: object())
        monkeypatch.setattr("src.runtime.liquidity_state.write_state",
                            lambda *a, **kw: None, raising=False)

        # Should not raise — short-frame fallback returns side=none.
        sig = ssb.ict_scalp_signal_builder({"SYMBOL": "BTCUSDT"})
        assert sig["side"] == "none"

    def test_builder_short_circuits_when_disabled_in_yaml(self, monkeypatch):
        """When ``enabled: false`` the builder must skip both the 5m
        fetch and the HTF fetch — this is the "wired but inactive"
        state that ships v2 to main without changing live behaviour."""
        from src.runtime import strategy_signal_builders as ssb
        from src.units import strategies as strategies_mod

        yaml_cfg = {"ict_scalp_5m": {"enabled": False}}
        monkeypatch.setattr(strategies_mod, "load_strategy_config",
                            lambda *a, **kw: yaml_cfg)
        monkeypatch.setattr(ssb, "load_strategy_config",
                            lambda *a, **kw: yaml_cfg, raising=False)

        calls = []

        def fake_fetch(symbol, tf, exchange_client=None, limit=None):
            calls.append(tf)
            return None

        monkeypatch.setattr(
            "src.runtime.market_data.fetch_candles", fake_fetch
        )
        monkeypatch.setattr(ssb, "_build_killzone_exchange", lambda s: object())

        sig = ssb.ict_scalp_signal_builder({"SYMBOL": "BTCUSDT"})
        assert sig["side"] == "none"
        assert sig["meta"]["reason"] == "disabled_in_yaml"
        assert calls == [], (
            f"disabled strategy should not fetch anything; got {calls}"
        )
