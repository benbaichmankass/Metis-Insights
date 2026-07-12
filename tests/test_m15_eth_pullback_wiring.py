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
    # shadow_model_ids OMITTED 2026-06-18 (soak-everything, symbol-aware auto-wire):
    # the opt-out was removed so ETH signals auto-wire the symbol-agnostic
    # decision/meta models; the symbol filter keeps BTC/MES regime heads off.
    assert "shadow_model_ids" not in s
    btc = cfg["htf_pullback_trend_2h"]
    # trail_mult diverges DELIBERATELY since M20 (2026-07-12, Tier-3
    # operator-approved): the per-year walk-forward passed trail 4.0 on the
    # BTC leg only (4/6 folds incl. 2025+2026); the ETH folds did not pass,
    # so ETH stays at the WS-C-validated 5.0. Pinned per leg, not by equality.
    assert btc["trail_mult"] == 4.0, "htf_pullback_trend_2h trail_mult drifted from the M20-approved 4.0"
    for k in ("trend_lookback", "pullback_lookback", "pullback_frac",
              "atr_period", "atr_stop_mult"):
        assert s[k] == btc[k], f"eth_pullback_2h {k} drifted from the BTC leg"


def test_instrument_profile_routes_to_bybit():
    inst = yaml.safe_load(open("config/instruments.yaml"))["instruments"]
    eth = inst["ETHUSDT"]
    assert eth["exchange"] == "bybit"
    assert eth["category"] == "linear"
    assert eth["quote_currency"] == "USDT"


def test_routed_to_bybit_1_and_2():
    # 2026-06-18 (Tier-3, operator-directed): eth_pullback_2h PROMOTED to real-money
    # bybit_2 as a deliberate live test ("bybit_2 is a test account; I want to see how
    # ETH performs there"), running the same ADX>=25-gated config as the bybit_1 demo.
    accounts = yaml.safe_load(open("config/accounts.yaml"))["accounts"]
    assert "eth_pullback_2h" in accounts["bybit_1"]["strategies"], "runs on bybit_1 (demo)"
    assert "eth_pullback_2h" in accounts["bybit_2"]["strategies"], "runs on bybit_2 (real-money test)"
    assert "ETHUSDT" in accounts["bybit_1"]["symbols"]
    assert "ETHUSDT" in accounts["bybit_2"]["symbols"]


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
