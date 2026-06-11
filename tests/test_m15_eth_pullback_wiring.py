"""M15 WS-C alt sleeve — eth_pullback_2h wiring tests."""
from __future__ import annotations

import json

import yaml

from src.runtime.strategy_signal_builders import eth_pullback_2h_signal_builder


def test_builder_disabled_gate(monkeypatch):
    monkeypatch.setattr(
        "src.units.strategies.load_strategy_config",
        lambda: {"eth_pullback_2h": {"enabled": False}},
    )
    out = eth_pullback_2h_signal_builder({})
    assert out["side"] == "none"
    assert out["meta"]["reason"] == "disabled_in_yaml"


def test_yaml_entry_pins_validated_params():
    cfg = yaml.safe_load(open("config/strategies.yaml"))["strategies"]
    s = cfg["eth_pullback_2h"]
    # Paper-accounts-execute policy: routed only to bybit_1 (demo), so it
    # ships execution: live — shadow here would strand it.
    assert s["execution"] == "live" and s["enabled"] is True
    assert s["timeframe"] == "2h"
    assert s["symbols"] == ["ETHUSDT"]
    # The WS-C-validated params == the live BTC htf_pullback_trend_2h values.
    assert (s["trend_lookback"], s["pullback_lookback"], s["pullback_frac"]) == (40, 10, 0.5)
    assert (s["atr_stop_mult"], s["trail_mult"]) == (2.5, 5.0)
    assert s["min_confidence"] == 0.0
    assert s["shadow_model_ids"] == []
    btc = cfg["htf_pullback_trend_2h"]
    for k in ("trend_lookback", "pullback_lookback", "pullback_frac",
              "atr_period", "atr_stop_mult", "trail_mult"):
        assert s[k] == btc[k], f"eth_pullback_2h {k} drifted from the BTC leg"


def test_instrument_profile_routes_to_bybit():
    inst = yaml.safe_load(open("config/instruments.yaml"))["instruments"]
    eth = inst["ETHUSDT"]
    assert eth["exchange"] == "bybit"
    assert eth["category"] == "linear"
    assert eth["quote_currency"] == "USDT"


def test_routed_to_demo_only():
    accounts = yaml.safe_load(open("config/accounts.yaml"))["accounts"]
    assert "eth_pullback_2h" in accounts["bybit_1"]["strategies"], \
        "must run on bybit_1 (demo)"
    assert "eth_pullback_2h" not in accounts["bybit_2"]["strategies"], \
        "must NOT be on bybit_2 (real money) until promoted (Tier-3)"
    assert "ETHUSDT" in accounts["bybit_1"]["symbols"]
    assert "ETHUSDT" not in accounts["bybit_2"]["symbols"]


def test_registered_in_multiplexer_and_priorities():
    from src.runtime.intent_multiplexer import _default_intent_builders
    from src.runtime.intents import DEFAULT_PRIORITIES
    builders = _default_intent_builders()
    assert builders["eth_pullback_2h"] is eth_pullback_2h_signal_builder
    # Floor priority — a wiring slip can't override an established member.
    assert DEFAULT_PRIORITIES["eth_pullback_2h"] == 0


def test_description_present():
    desc = json.load(open("config/strategy_descriptions.json"))
    assert desc["eth_pullback_2h"]["short"]
    assert "ETH" in desc["eth_pullback_2h"]["short"]
