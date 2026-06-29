"""ETF-breadth daily sweep (2026-06-20) — iwm/tlt/ief wiring tests.

Mirrors tests/test_m15_etf_wiring.py for the three new daily-ETF cells routed
to alpaca_paper (paper money): iwm_trend_long_1d (small-cap trend, spy/qqq
sibling) + tlt_pullback_1d / ief_pullback_1d (Treasury-bond pullback, gld
sibling). Validated by the ETF-breadth daily sweep
(docs/research/expansion-backtesting-research-2026-06-20.md).
"""
from __future__ import annotations

import json

import yaml

from src.runtime.intent_multiplexer import _resolve_builders
from src.runtime.intents import DEFAULT_PRIORITIES
from src.runtime.strategy_signal_builders import (
    ief_pullback_1d_signal_builder,
    iwm_trend_long_1d_signal_builder,
    tlt_pullback_1d_signal_builder,
)

_BUILDERS = {
    "iwm_trend_long_1d": iwm_trend_long_1d_signal_builder,
    "tlt_pullback_1d": tlt_pullback_1d_signal_builder,
    "ief_pullback_1d": ief_pullback_1d_signal_builder,
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


def test_intent_roster_and_priorities_registered():
    roster = _resolve_builders()
    for name, builder in _BUILDERS.items():
        assert roster.get(name) is builder
        # untested new cells sit at the floor priority (0), like spy/gld
        assert DEFAULT_PRIORITIES[name] == 0


def test_monitor_unit_resolves_to_base_unit():
    # aliased builders reuse a base unit's monitor() via the monitor_unit tag
    assert iwm_trend_long_1d_signal_builder.monitor_unit == "trend_donchian"
    assert tlt_pullback_1d_signal_builder.monitor_unit == "htf_pullback_trend_2h"
    assert ief_pullback_1d_signal_builder.monitor_unit == "htf_pullback_trend_2h"


def test_yaml_entries_pin_validated_params():
    cfg = yaml.safe_load(open("config/strategies.yaml"))["strategies"]
    # IWM — long-only Donchian trend (spy/qqq clone)
    iwm = cfg["iwm_trend_long_1d"]
    assert iwm["execution"] == "live" and iwm["enabled"] is True
    assert iwm["long_only"] is True
    assert iwm["symbols"] == ["IWM"]
    assert iwm["signal_prefixes"] == ["iwm_trend_long", "iwm_trend"]
    assert (iwm["donchian"], iwm["atr_period"], iwm["atr_stop_mult"]) == (30, 14, 2.5)
    assert (iwm["trail_mult"], iwm["tp_r"]) == (4.0, 50.0)
    assert iwm["timeframe"] == "1d"
    assert iwm["min_confidence"] == 0.0 and iwm["shadow_model_ids"] == []
    assert iwm["model"] is None
    # TLT — bidirectional bond pullback (gld clone)
    tlt = cfg["tlt_pullback_1d"]
    assert tlt["execution"] == "live" and tlt["enabled"] is True
    assert tlt["symbols"] == ["TLT"]
    assert tlt["signal_prefixes"] == ["tlt_pullback"]
    assert (tlt["trend_lookback"], tlt["pullback_lookback"], tlt["pullback_frac"]) == (40, 10, 0.618)
    assert (tlt["atr_period"], tlt["atr_stop_mult"], tlt["trail_mult"]) == (14, 2.5, 5.0)
    assert tlt["timeframe"] == "1d"
    assert tlt["shadow_model_ids"] == []
    # IEF — bidirectional bond pullback (gld clone)
    ief = cfg["ief_pullback_1d"]
    assert ief["execution"] == "live" and ief["enabled"] is True
    assert ief["symbols"] == ["IEF"]
    assert ief["signal_prefixes"] == ["ief_pullback"]
    assert (ief["trend_lookback"], ief["pullback_lookback"], ief["pullback_frac"]) == (30, 10, 0.5)
    assert (ief["atr_period"], ief["atr_stop_mult"], ief["trail_mult"]) == (14, 2.5, 5.0)
    assert ief["timeframe"] == "1d"
    assert ief["shadow_model_ids"] == []


def test_instrument_profiles_route_to_alpaca():
    inst = yaml.safe_load(open("config/instruments.yaml"))["instruments"]
    for sym in ("IWM", "TLT", "IEF"):
        assert inst[sym]["exchange"] == "alpaca"
        assert inst[sym]["min_qty"] == 1  # whole shares (bracket constraint)
        assert inst[sym]["tick_size"] == 0.01


def test_account_routing_and_descriptions():
    acct = yaml.safe_load(open("config/accounts.yaml"))["accounts"]["alpaca_paper"]
    for name in _BUILDERS:
        assert name in acct["strategies"]
    for sym in ("IWM", "TLT", "IEF"):
        assert sym in acct["symbols"]
    desc = json.load(open("config/strategy_descriptions.json"))
    for name in _BUILDERS:
        assert name in desc and desc[name]["short"] and desc[name]["how_it_works"]
