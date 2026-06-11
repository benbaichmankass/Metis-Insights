"""M15 Phase 4 buildout — spy/qqq/gld wiring tests."""
from __future__ import annotations

import json

import yaml

from src.runtime.strategy_signal_builders import (
    gld_pullback_1d_signal_builder,
    qqq_trend_long_1d_signal_builder,
    spy_trend_long_1d_signal_builder,
)

_BUILDERS = {
    "spy_trend_long_1d": spy_trend_long_1d_signal_builder,
    "qqq_trend_long_1d": qqq_trend_long_1d_signal_builder,
    "gld_pullback_1d": gld_pullback_1d_signal_builder,
}


def test_builders_disabled_gate(monkeypatch):
    monkeypatch.setattr(
        "src.units.strategies.load_strategy_config",
        lambda: {name: {"enabled": False} for name in _BUILDERS},
    )
    for name, builder in _BUILDERS.items():
        out = builder({})
        assert out["side"] == "none"
        assert out["meta"]["reason"] == "disabled_in_yaml"


def test_builders_us_session_gate(monkeypatch):
    monkeypatch.setattr(
        "src.units.strategies.load_strategy_config",
        lambda: {name: {"enabled": True} for name in _BUILDERS},
    )
    monkeypatch.setattr("src.runtime.market_hours.is_market_open", lambda cls: False)
    for name, builder in _BUILDERS.items():
        out = builder({})
        assert out["side"] == "none"
        assert out["meta"]["reason"] == "us_market_closed"


def test_yaml_entries_pin_validated_params():
    cfg = yaml.safe_load(open("config/strategies.yaml"))["strategies"]
    for name in ("spy_trend_long_1d", "qqq_trend_long_1d"):
        s = cfg[name]
        assert s["execution"] == "live" and s["enabled"] is True
        assert s["long_only"] is True
        assert (s["donchian"], s["atr_stop_mult"], s["trail_mult"]) == (30, 2.5, 4.0)
        assert s["timeframe"] == "1d"
    g = cfg["gld_pullback_1d"]
    assert g["execution"] == "live"
    assert (g["trend_lookback"], g["pullback_lookback"], g["pullback_frac"]) == (40, 15, 0.618)
    assert (g["atr_stop_mult"], g["trail_mult"]) == (2.0, 4.0)
    # gold leg promoted to live-on-practice in the same PR
    assert cfg["xauusd_trend_1h"]["execution"] == "live"


def test_instrument_profiles_route_to_alpaca():
    inst = yaml.safe_load(open("config/instruments.yaml"))["instruments"]
    for sym in ("SPY", "QQQ", "GLD"):
        assert inst[sym]["exchange"] == "alpaca"
        assert inst[sym]["min_qty"] == 1  # whole shares (bracket constraint)


def test_account_routing_and_descriptions():
    acct = yaml.safe_load(open("config/accounts.yaml"))["accounts"]["alpaca_paper"]
    assert acct["strategies"] == [
        "spy_trend_long_1d", "qqq_trend_long_1d", "gld_pullback_1d"
    ]
    desc = json.load(open("config/strategy_descriptions.json"))
    for name in _BUILDERS:
        assert name in desc and desc[name]["short"]
