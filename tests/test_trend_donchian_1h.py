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


def test_config_block_retired_after_adoption():
    # 2026-06-01: the A/B's 1h / trail 5.0 config was ADOPTED into the live
    # trend_donchian, so this instance is RETIRED (enabled: false) to avoid
    # double-firing identical 1h breakouts on bybit_1 (the demo runs both).
    cfg = _load_strategies_cfg().get("trend_donchian_1h")
    assert cfg is not None, "trend_donchian_1h block retained as the A/B record"
    assert cfg["enabled"] is False           # RETIRED — config adopted into live
    assert cfg["timeframe"] == "1h"
    assert int(cfg["donchian"]) == 20
    assert float(cfg["trail_mult"]) == 5.0
    assert cfg.get("symbols") == ["BTCUSDT"]


def test_research_optimal_config_adopted_into_live():
    # The A/B existed to validate 1h / trail 5.0 vs the live 2h flagship; that
    # validation is now adopted — the live trend_donchian IS the research-optimal
    # 1h / donchian 20 / trail 5.0 corner (Tier-3 re-tune, operator-approved,
    # supersedes the 2026-05-24 S9 2h migration).
    live = _load_strategies_cfg()["trend_donchian"]
    assert live["timeframe"] == "1h", "live trend_donchian re-tuned 2h -> 1h"
    assert float(live["trail_mult"]) == 5.0, "live trail re-tuned 3.5 -> 5.0"
    assert int(live["donchian"]) == 20
    assert live["execution"] == "live"


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
