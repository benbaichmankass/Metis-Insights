"""M15 Phase 3 — xauusd_trend_1h wiring tests.

Builder gates (disabled / weekend), multiplexer + intents registration,
and the config trio (strategy entry pinned to the sweep-validated
params, instrument profile routing, account routing).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import yaml

from src.runtime.strategy_signal_builders import xauusd_trend_1h_signal_builder


def test_registered_in_multiplexer_and_intents():
    import src.runtime.intent_multiplexer as imx
    import src.runtime.intents as intents

    src_text = open(imx.__file__).read()
    assert '"xauusd_trend_1h": xauusd_trend_1h_signal_builder' in src_text
    assert "xauusd_trend_1h" in open(intents.__file__).read()


def test_builder_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(
        "src.units.strategies.load_strategy_config",
        lambda: {"xauusd_trend_1h": {"enabled": False}},
    )
    out = xauusd_trend_1h_signal_builder({})
    assert out["side"] == "none"
    assert out["meta"]["reason"] == "disabled_in_yaml"


def test_builder_weekend_gate(monkeypatch):
    monkeypatch.setattr(
        "src.units.strategies.load_strategy_config",
        lambda: {"xauusd_trend_1h": {"enabled": True}},
    )
    monkeypatch.setattr(
        "src.runtime.market_hours.is_market_open", lambda cls: False
    )
    out = xauusd_trend_1h_signal_builder({})
    assert out["side"] == "none"
    assert out["meta"]["reason"] == "fx_market_closed"


def test_market_hours_fx_actually_closed_on_saturday():
    from src.runtime.market_hours import is_market_open

    assert not is_market_open("fx", datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc))


def test_strategy_yaml_pins_sweep_params():
    cfg = yaml.safe_load(open("config/strategies.yaml"))["strategies"]["xauusd_trend_1h"]
    # DISABLED 2026-07-04 (operator, /health-review follow-up): the only routed
    # account (oanda_practice) has been shelved since 2026-06-12, so the
    # strategy was silent-enabled debris (loaded every boot, zero audit
    # events). The sweep-validated params below stay PINNED so a re-enable
    # resumes exactly the validated cell.
    assert cfg["enabled"] is False
    # PROMOTED shadow -> live-on-practice 2026-06-11 (operator: "go live
    # on practice"); oanda_practice is paper money. execution stays live so
    # the enabled flip is the single gate to reverse.
    assert cfg["execution"] == "live"
    assert cfg["symbols"] == ["XAUUSD"]
    assert cfg["timeframe"] == "1h"
    # exactly the harness defaults the winning sweep cell ran; trail_mult
    # 3 -> 4: M20 fleet sweep 2026-07-12 walk-forward 6/6 (GC=F proxy) —
    # moves with the mgc_trend_1h sibling (parity contract). Leg disabled;
    # a re-enable resumes the better-validated cell.
    assert (cfg["donchian"], cfg["atr_period"]) == (20, 14)
    assert (cfg["atr_stop_mult"], cfg["trail_mult"]) == (2.5, 4.0)
    assert cfg["min_confidence"] == 0.0
    assert cfg["shadow_model_ids"] == []


def test_instrument_profile_routes_to_oanda():
    inst = yaml.safe_load(open("config/instruments.yaml"))["instruments"]["XAUUSD"]
    assert inst["exchange"] == "oanda"
    assert inst["min_qty"] == 1


def test_account_routing_and_description_present():
    acct = yaml.safe_load(open("config/accounts.yaml"))["accounts"]["oanda_practice"]
    # PAUSED 2026-06-16 (PB-20260616-001): removed from routing while OANDA US
    # can't trade XAU_USD; the strategy still exists in strategies.yaml + builder.
    assert acct["strategies"] == []
    assert acct["mode"] == "dry_run"  # SHELVED 2026-06-12 (set-account-mode #3446):
    # gold covered live on IBKR MGC + Alpaca GLD; OANDA US can't trade XAU_USD.
    desc = json.load(open("config/strategy_descriptions.json"))
    assert "xauusd_trend_1h" in desc and desc["xauusd_trend_1h"]["short"]
