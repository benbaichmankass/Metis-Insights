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
    # atr_stop_mult diverges DELIBERATELY since the 2026-08-29 e35 re-check
    # (Tier-3, operator-approved) — the SECOND param to leave the equality set,
    # for the same reason trail_mult did, so pin each leg to its OWN evidence
    # rather than loosening the check to make the two agree:
    #   * BTC  htf_pullback_trend_2h -> 3.0, cell `sm3` (path_b_wf_pass, wf 4/6,
    #     d_net_r +20.2306) on sweep run 33277532648.
    #   * ETH  eth_pullback_2h stays 2.5 — its own e35 verdict is
    #     `blocked:no_live_bar_count_exit`: every cell that cleared the gate on
    #     that leg carries a `to<N>` timeout component, and no live unit
    #     implements a bar-count exit, so it has ZERO shippable cells.
    # The two legs are not expected to converge again unless ETH later produces
    # a shippable winner of its own.
    assert btc["atr_stop_mult"] == 3.0, "htf_pullback_trend_2h atr_stop_mult drifted from the e35 sm3 cell"
    assert s["atr_stop_mult"] == 2.5, "eth_pullback_2h atr_stop_mult drifted from its WS-C-validated 2.5"
    for k in ("trend_lookback", "pullback_lookback", "pullback_frac", "atr_period"):
        assert s[k] == btc[k], f"eth_pullback_2h {k} drifted from the BTC leg"


def test_instrument_profile_routes_to_bybit():
    inst = yaml.safe_load(open("config/instruments.yaml"))["instruments"]
    eth = inst["ETHUSDT"]
    assert eth["exchange"] == "bybit"
    assert eth["category"] == "linear"
    assert eth["quote_currency"] == "USDT"


def test_routed_to_bybit_1_only_since_the_r2_cut():
    """The leg soaks on bybit_1 and is OFF real money.

    RENAMED from ``test_routed_to_bybit_1_and_2`` on 2026-09-22, deliberately:
    a test named ``_and_2`` whose body asserts the leg is NOT on bybit_2 is the
    stored-label-read-as-the-measurement defect this repo keeps paying for, so
    the name moves with the fact.

    HISTORY, kept because it is what a re-promotion would have to overturn:
    2026-06-18 (Tier-3, operator-directed) eth_pullback_2h was PROMOTED to
    real-money bybit_2 as a deliberate live test ("bybit_2 is a test account; I
    want to see how ETH performs there"), running the same ADX>=25-gated config
    as the bybit_1 demo.

    2026-09-22 (Tier-3, operator instruction "Cut the legs that don't clear the
    bar", checklist row R2): REMOVED from bybit_2 and from the bybit_portfolio
    mirror. It fails clause C4 of B1's four-clause evidence bar — the registered
    rule RULE-D1-STAGE0-NET-OF-FULL-COST returns verdict `fail` on its record.
    The leg keeps ``execution: live`` in config/strategies.yaml and keeps
    trading on bybit_1, the full-roster soak book, so evidence still accrues.

    The bybit_2 assertion is INVERTED rather than deleted: an accidental re-add
    to a real-money roster should fail this test, and re-promotion is a Tier-3
    act that updates it deliberately.
    """
    accounts = yaml.safe_load(open("config/accounts.yaml"))["accounts"]
    assert "eth_pullback_2h" in accounts["bybit_1"]["strategies"], "runs on bybit_1 (demo)"
    assert "eth_pullback_2h" not in accounts["bybit_2"]["strategies"], (
        "eth_pullback_2h is back on the real-money bybit_2 roster. That is a "
        "Tier-3 promotion and needs a record clearing the four-clause bar: "
        "python3 scripts/ci/check_roster_promotion_evidence.py --population")
    assert "eth_pullback_2h" not in accounts["bybit_portfolio"]["strategies"], (
        "the Gate-2 mirror must not carry a leg bybit_2 does not trade")
    assert "ETHUSDT" in accounts["bybit_1"]["symbols"]
    assert "ETHUSDT" in accounts["bybit_2"]["symbols"], (
        "ETHUSDT stays in bybit_2.symbols — trend_donchian_eth_4h still trades it")


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
