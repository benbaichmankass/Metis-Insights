"""Intraday ETF rollout 2b (2026-06-20 § 0e) — spy/qqq/tlt pullback + uso trend wiring tests.

Mirrors tests/test_etf_intraday_pilot_wiring.py for the four cells that complete
the intraday (1h) ETF sleeve on alpaca_paper (paper money):
  - spy_pullback_1h — SPY 1h BIDIRECTIONAL HTF pullback (gld_pullback_1h sibling;
    htf_pullback_trend_2h unit, frac 0.618 / trail 5.0).
  - qqq_pullback_1h — QQQ 1h BIDIRECTIONAL HTF pullback (same params as spy).
  - tlt_pullback_1h — TLT 1h BIDIRECTIONAL HTF pullback (frac 0.5 / trail 4.0).
  - uso_trend_1h    — USO 1h LONG-ONLY Donchian trend (spy_trend_long_1d clone;
    trend_donchian unit, KEEPS the short suppression — the both-sides variant was
    REJECTED in the sweep).
Validated by the intraday 1h ETF sweep
(docs/research/expansion-backtesting-research-2026-06-20.md § 0e).
"""
from __future__ import annotations

import json

import yaml

from src.runtime.intent_multiplexer import _resolve_builders
from src.runtime.intents import DEFAULT_PRIORITIES
from src.runtime.strategy_signal_builders import (
    spy_pullback_1h_signal_builder,
    qqq_pullback_1h_signal_builder,
    tlt_pullback_1h_signal_builder,
    uso_trend_1h_signal_builder,
)

_PULLBACK_BUILDERS = {
    "spy_pullback_1h": spy_pullback_1h_signal_builder,
    "qqq_pullback_1h": qqq_pullback_1h_signal_builder,
    "tlt_pullback_1h": tlt_pullback_1h_signal_builder,
}
_BUILDERS = {
    **_PULLBACK_BUILDERS,
    "uso_trend_1h": uso_trend_1h_signal_builder,
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
    assert spy_pullback_1h_signal_builder.monitor_unit == "htf_pullback_trend_2h"
    assert qqq_pullback_1h_signal_builder.monitor_unit == "htf_pullback_trend_2h"
    assert tlt_pullback_1h_signal_builder.monitor_unit == "htf_pullback_trend_2h"
    assert uso_trend_1h_signal_builder.monitor_unit == "trend_donchian"


def test_pullback_builders_are_bidirectional_no_long_only_suppression():
    """The three pullback cells must trade BOTH directions — the long-only short
    suppression block must be ABSENT.
    """
    import inspect

    for name, builder in _PULLBACK_BUILDERS.items():
        src = inspect.getsource(builder)
        assert "short_suppressed_long_only" not in src, name
        assert 'pkg["direction"] != "long"' not in src, name


def test_uso_builder_is_long_only():
    """uso_trend_1h must KEEP the long-only short-suppression block — the
    both-sides USO variant was REJECTED in the intraday 1h sweep § 0e.
    """
    import inspect

    src = inspect.getsource(uso_trend_1h_signal_builder)
    assert "short_suppressed_long_only" in src
    assert 'pkg["direction"] != "long"' in src


def test_yaml_entries_pin_validated_params():
    cfg = yaml.safe_load(open("config/strategies.yaml"))["strategies"]

    # SPY 1h — bidirectional pullback (frac 0.618, trail 5.0)
    spy = cfg["spy_pullback_1h"]
    assert spy["execution"] == "live" and spy["enabled"] is True
    assert "long_only" not in spy  # bidirectional
    assert spy["symbols"] == ["SPY"]
    assert spy["signal_prefixes"] == ["spy_pullback"]
    assert (spy["trend_lookback"], spy["pullback_lookback"], spy["pullback_frac"]) == (60, 12, 0.618)
    assert (spy["atr_period"], spy["atr_stop_mult"], spy["trail_mult"]) == (14, 2.5, 5.0)
    assert spy["timeframe"] == "1h"
    assert spy["min_confidence"] == 0.0 and spy["shadow_model_ids"] == []
    assert spy["model"] is None

    # QQQ 1h — bidirectional pullback (same params as spy)
    qqq = cfg["qqq_pullback_1h"]
    assert qqq["execution"] == "live" and qqq["enabled"] is True
    assert "long_only" not in qqq  # bidirectional
    assert qqq["symbols"] == ["QQQ"]
    assert qqq["signal_prefixes"] == ["qqq_pullback"]
    assert (qqq["trend_lookback"], qqq["pullback_lookback"], qqq["pullback_frac"]) == (60, 12, 0.618)
    assert (qqq["atr_period"], qqq["atr_stop_mult"], qqq["trail_mult"]) == (14, 2.5, 5.0)
    assert qqq["timeframe"] == "1h"
    assert qqq["min_confidence"] == 0.0 and qqq["shadow_model_ids"] == []
    assert qqq["model"] is None

    # TLT 1h — bidirectional pullback (frac 0.5, trail 4.0)
    tlt = cfg["tlt_pullback_1h"]
    assert tlt["execution"] == "live" and tlt["enabled"] is True
    assert "long_only" not in tlt  # bidirectional
    assert tlt["symbols"] == ["TLT"]
    assert tlt["signal_prefixes"] == ["tlt_pullback_1h"]
    assert (tlt["trend_lookback"], tlt["pullback_lookback"], tlt["pullback_frac"]) == (60, 12, 0.5)
    assert (tlt["atr_period"], tlt["atr_stop_mult"], tlt["trail_mult"]) == (14, 2.5, 4.0)
    assert tlt["timeframe"] == "1h"
    assert tlt["min_confidence"] == 0.0 and tlt["shadow_model_ids"] == []
    assert tlt["model"] is None

    # USO 1h — LONG-ONLY Donchian trend (donch24, trail 4.0, tp_r 50.0)
    uso = cfg["uso_trend_1h"]
    assert uso["execution"] == "live" and uso["enabled"] is True
    # CRITICAL: uso IS long-only (the both-sides variant was rejected).
    assert uso["long_only"] is True
    assert uso["symbols"] == ["USO"]
    assert uso["signal_prefixes"] == ["uso_trend"]
    assert (uso["donchian"], uso["atr_period"], uso["atr_stop_mult"]) == (24, 14, 2.5)
    assert (uso["trail_mult"], uso["tp_r"]) == (4.0, 50.0)
    assert uso["timeframe"] == "1h"
    assert uso["min_confidence"] == 0.0 and uso["shadow_model_ids"] == []
    assert uso["model"] is None


def test_instrument_profiles_route_to_alpaca():
    inst = yaml.safe_load(open("config/instruments.yaml"))["instruments"]
    # USO is new; SPY/QQQ/TLT (used by the 1h pullback cells) already present.
    for sym in ("SPY", "QQQ", "TLT", "USO"):
        assert inst[sym]["exchange"] == "alpaca"
        assert inst[sym]["min_qty"] == 1  # whole shares (bracket constraint)
        assert inst[sym]["qty_step"] == 1
        assert inst[sym]["tick_size"] == 0.01
    uso = inst["USO"]
    assert uso["asset_class"] == "commodity"  # oil ETF (corrected from equity 2026-06-20)
    assert uso["contract_value_usd"] == 1.0
    assert uso["max_leverage"] == 4
    assert uso["display_name"] == "United States Oil Fund ETF (Alpaca paper)"


def test_account_routing_and_descriptions():
    acct = yaml.safe_load(open("config/accounts.yaml"))["accounts"]["alpaca_paper"]
    for name in _BUILDERS:
        assert name in acct["strategies"]
    assert "USO" in acct["symbols"]
    # SPY/QQQ/TLT already routed; the 1h pullback cells reuse them.
    for sym in ("SPY", "QQQ", "TLT"):
        assert sym in acct["symbols"]
    desc = json.load(open("config/strategy_descriptions.json"))
    for name in _BUILDERS:
        assert name in desc and desc[name]["short"] and desc[name]["how_it_works"]
