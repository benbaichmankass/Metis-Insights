"""Wiring tests for trend_donchian_1h (overnight research 2026-06-01).

The strategy *logic* is the live trend_donchian unit (covered by
test_trend_donchian.py) parametrised by its own config block. These tests pin
the SHADOW WIRING that makes it a distinct instance: the config block carries
the validated 1h/wide-trail params at execution: shadow, and it is the roster
priority floor. Fully offline — no exchange / network / secrets.
"""
from __future__ import annotations


def _load_strategies_cfg():
    from src.units.strategies import load_strategy_config
    return load_strategy_config() or {}


def test_config_block_is_shadow_with_validated_params():
    cfg = _load_strategies_cfg().get("trend_donchian_1h")
    assert cfg is not None, "trend_donchian_1h missing from config/strategies.yaml"
    assert cfg["execution"] == "shadow"      # never sends a live order
    assert cfg["enabled"] is True
    assert cfg["timeframe"] == "1h"          # the faster-TF variant
    assert int(cfg["donchian"]) == 20
    assert float(cfg["trail_mult"]) == 5.0   # the validated wide trail
    assert cfg.get("symbols") == ["BTCUSDT"]


def test_distinct_from_live_trend_donchian():
    # It is a SEPARATE instance — the live 2h strategy must be unchanged.
    cfg = _load_strategies_cfg()
    live = cfg["trend_donchian"]
    assert live["timeframe"] == "2h" and float(live["trail_mult"]) == 3.5, \
        "live trend_donchian must remain 2h / trail 3.5 (the A/B must not touch it)"
    assert cfg["trend_donchian_1h"]["timeframe"] == "1h"


def test_intent_priority_registered_below_established_roster():
    from src.runtime.intents import DEFAULT_PRIORITIES
    assert DEFAULT_PRIORITIES.get("trend_donchian_1h") == 1
    # ...below every established (non-shadow) strategy so a wiring slip can't let
    # the shadow A/B override the live roster. (Not necessarily the global min —
    # a later shadow strategy, e.g. the MES sleeve, sits even lower.)
    assert DEFAULT_PRIORITIES["trend_donchian_1h"] < DEFAULT_PRIORITIES["fade_breakout_4h"]


def test_routed_to_demo_only():
    import yaml
    accounts = yaml.safe_load(open("config/accounts.yaml"))["accounts"]
    bybit_1 = accounts["bybit_1"]["strategies"]
    bybit_2 = accounts["bybit_2"]["strategies"]
    assert "trend_donchian_1h" in bybit_1, "must run on bybit_1 (demo)"
    assert "trend_donchian_1h" not in bybit_2, \
        "must NOT be on bybit_2 (real money) until promoted (Tier-3)"
