"""mgc_trend_1h wiring tests — the IBKR/MGC sibling of xauusd_trend_1h.

Builder disabled-gate, multiplexer + intents registration, and the config
trio (strategy entry pinned to the sweep-validated params, instrument
profile routing to IBKR, account routing onto ib_paper). Unlike the OANDA
xauusd sibling there is NO FX weekend gate — MGC is a CME future and the
IBKR futures sleeves don't gate on market hours.
"""
from __future__ import annotations

import json

import yaml

from src.runtime.strategy_signal_builders import mgc_trend_1h_signal_builder


def test_registered_in_multiplexer_and_intents():
    import src.runtime.intent_multiplexer as imx
    import src.runtime.intents as intents

    src_text = open(imx.__file__).read()
    assert '"mgc_trend_1h": mgc_trend_1h_signal_builder' in src_text
    assert "mgc_trend_1h" in open(intents.__file__).read()


def test_builder_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(
        "src.units.strategies.load_strategy_config",
        lambda: {"mgc_trend_1h": {"enabled": False}},
    )
    out = mgc_trend_1h_signal_builder({})
    assert out["side"] == "none"
    assert out["meta"]["reason"] == "disabled_in_yaml"


def test_strategy_yaml_pins_sweep_params():
    cfg = yaml.safe_load(open("config/strategies.yaml"))["strategies"]["mgc_trend_1h"]
    assert cfg["enabled"] is True
    # DEMOTED live -> shadow 2026-06-18 (Tier-3, operator-approved): net-negative
    # on both the XAUUSD spot proxy (-50.7R) AND real GC=F 1h (-15.5R) — no
    # validated edge to soak, so it logs order packages but sends no paper order
    # (docs/research/recombination-sweep-2026-06-18.md follow-up).
    assert cfg["execution"] == "shadow"
    assert cfg["symbols"] == ["MGC"]
    assert cfg["timeframe"] == "1h"
    # exactly the sweep-validated xauusd_trend_1h defaults (same gold underlying);
    # trail_mult 3 -> 4: M20 fleet sweep 2026-07-12 walk-forward 6/6 on the
    # GC=F proxy (runtime_logs/m20_fleet; Tier-3 exit-lever package) — the
    # xauusd sibling passed the same cell 6/6 and moves with it (parity kept).
    assert (cfg["donchian"], cfg["atr_period"]) == (20, 14)
    assert (cfg["atr_stop_mult"], cfg["trail_mult"]) == (2.5, 4.0)
    # M21 E-2 batch 2: confirm_1 replaced the depth cell (parity with xauusd).
    assert cfg["min_confidence"] == 0.0
    assert cfg["confirm_bars"] == 1
    assert cfg["shadow_model_ids"] == []


def test_params_match_xauusd_sibling():
    strat = yaml.safe_load(open("config/strategies.yaml"))["strategies"]
    mgc, xau = strat["mgc_trend_1h"], strat["xauusd_trend_1h"]
    # Same validated edge, different venue/symbol — every tuned param matches.
    for k in ("donchian", "atr_period", "atr_stop_mult", "trail_mult",
              "tp_r", "min_confidence", "confirm_bars", "timeframe"):
        assert mgc[k] == xau[k], k


def test_instrument_profile_routes_to_ibkr():
    inst = yaml.safe_load(open("config/instruments.yaml"))["instruments"]["MGC"]
    assert inst["exchange"] == "interactive_brokers"
    assert inst["category"] == "futures"
    assert inst["min_qty"] == 1.0  # whole-contract sizing


def test_account_routing_and_description_present():
    acct = yaml.safe_load(open("config/accounts.yaml"))["accounts"]["ib_paper"]
    assert "mgc_trend_1h" in acct["strategies"]
    assert "MGC" in acct["symbols"]
    assert acct["mode"] == "live"  # ib_paper paper money — executes the soak
    desc = json.load(open("config/strategy_descriptions.json"))
    assert "mgc_trend_1h" in desc and desc["mgc_trend_1h"]["short"]
