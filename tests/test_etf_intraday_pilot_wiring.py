"""Intraday ETF pilot (2026-06-20 § 0e) — gld_pullback_1h / slv_trend_1h wiring tests.

Mirrors tests/test_etf_breadth_alpaca_wiring.py for the two new INTRADAY (1h)
daily-ETF-family cells routed to alpaca_paper (paper money):
  - gld_pullback_1h — GLD 1h bidirectional HTF pullback (gld_pullback_1d
    sibling on the 1-hour timeframe; htf_pullback_trend_2h unit).
  - slv_trend_1h    — SLV 1h BIDIRECTIONAL Donchian trend (spy_trend_long_1d
    clone but both-sides — silver trends down too; trend_donchian unit, NO
    long-only suppression).
Validated by the intraday 1h ETF sweep
(docs/research/expansion-backtesting-research-2026-06-20.md § 0e).
"""
from __future__ import annotations

import json

import yaml

from src.runtime.intent_multiplexer import _resolve_builders
from src.runtime.intents import DEFAULT_PRIORITIES
from src.runtime.strategy_signal_builders import (
    gld_pullback_1h_signal_builder,
    slv_trend_1h_signal_builder,
)

_BUILDERS = {
    "gld_pullback_1h": gld_pullback_1h_signal_builder,
    "slv_trend_1h": slv_trend_1h_signal_builder,
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
    assert gld_pullback_1h_signal_builder.monitor_unit == "htf_pullback_trend_2h"
    assert slv_trend_1h_signal_builder.monitor_unit == "trend_donchian"


def test_slv_builder_is_bidirectional_no_long_only_suppression():
    """slv_trend_1h must trade BOTH directions — the spy/iwm long-only short
    suppression block must be ABSENT (silver trends down too).
    """
    import inspect

    src = inspect.getsource(slv_trend_1h_signal_builder)
    # The long-only builders suppress shorts with this reason token; slv must not.
    assert "short_suppressed_long_only" not in src
    # And it must not gate on direction != long.
    assert 'pkg["direction"] != "long"' not in src


def test_yaml_entries_pin_validated_params():
    cfg = yaml.safe_load(open("config/strategies.yaml"))["strategies"]
    # GLD 1h — bidirectional pullback (gld_pullback_1d clone, 1h)
    gld = cfg["gld_pullback_1h"]
    assert gld["execution"] == "live" and gld["enabled"] is True
    assert "long_only" not in gld  # bidirectional
    assert gld["symbols"] == ["GLD"]
    assert gld["signal_prefixes"] == ["gld_pullback_1h"]
    assert (gld["trend_lookback"], gld["pullback_lookback"], gld["pullback_frac"]) == (60, 12, 0.5)
    assert (gld["atr_period"], gld["atr_stop_mult"], gld["trail_mult"]) == (14, 2.5, 4.0)
    assert gld["timeframe"] == "1h"
    assert gld["min_confidence"] == 0.0 and gld["shadow_model_ids"] == []
    assert gld["model"] is None
    # SLV 1h — BIDIRECTIONAL Donchian trend (spy clone but both-sides)
    slv = cfg["slv_trend_1h"]
    assert slv["execution"] == "live" and slv["enabled"] is True
    # CRITICAL: no long_only key — silver trades both directions
    assert "long_only" not in slv
    assert slv["symbols"] == ["SLV"]
    assert slv["signal_prefixes"] == ["slv_trend"]
    assert (slv["donchian"], slv["atr_period"], slv["atr_stop_mult"]) == (24, 14, 2.5)
    assert (slv["trail_mult"], slv["tp_r"]) == (4.0, 50.0)
    assert slv["timeframe"] == "1h"
    assert slv["min_confidence"] == 0.0 and slv["shadow_model_ids"] == []
    assert slv["model"] is None


def test_instrument_profiles_route_to_alpaca():
    inst = yaml.safe_load(open("config/instruments.yaml"))["instruments"]
    # SLV is new; GLD (used by gld_pullback_1h) already present.
    for sym in ("GLD", "SLV"):
        assert inst[sym]["exchange"] == "alpaca"
        assert inst[sym]["min_qty"] == 1  # whole shares (bracket constraint)
        assert inst[sym]["qty_step"] == 1
        assert inst[sym]["tick_size"] == 0.01
    slv = inst["SLV"]
    assert slv["asset_class"] == "commodity"  # silver ETF (corrected from equity 2026-06-20)
    assert slv["contract_value_usd"] == 1.0
    assert slv["max_leverage"] == 4
    assert slv["display_name"] == "iShares Silver Trust ETF (Alpaca paper)"


def test_account_routing_and_descriptions():
    acct = yaml.safe_load(open("config/accounts.yaml"))["accounts"]["alpaca_paper"]
    for name in _BUILDERS:
        assert name in acct["strategies"]
    assert "SLV" in acct["symbols"]
    assert "GLD" in acct["symbols"]  # already routed; gld_pullback_1h reuses it
    desc = json.load(open("config/strategy_descriptions.json"))
    for name in _BUILDERS:
        assert name in desc and desc[name]["short"] and desc[name]["how_it_works"]
