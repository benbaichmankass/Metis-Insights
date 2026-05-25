"""Tests for the advisory influence operator (reductive-only, default-off)."""
from __future__ import annotations

import pytest

from src.core.order_contract import OrderPackage
from src.runtime.advisory_influence import (
    AdvisoryPolicy,
    apply_advisory_influence,
    parse_policy,
)


def _pkg(qty: float = 1.0) -> OrderPackage:
    return OrderPackage(
        strategy_id="vwap",
        symbol="BTCUSDT",
        account_id="bybit_2",
        side="buy",
        qty=qty,
        entry_price=80000.0,
        stop_loss=79500.0,
        take_profit=81000.0,
        order_type="limit",
        timestamp_utc="2026-05-25T00:00:00+00:00",
    )


def test_flag_off_is_identity():
    p = _pkg()
    res = apply_advisory_influence(
        p, {"m": 0.0}, AdvisoryPolicy(mode="veto"), flag_enabled=False,
    )
    assert res.action == "none"
    assert res.package is p


def test_mode_off_is_identity():
    p = _pkg()
    res = apply_advisory_influence(
        p, {"m": 0.0}, AdvisoryPolicy(mode="off"), flag_enabled=True,
    )
    assert res.action == "none"
    assert res.package is p


def test_no_scores_is_identity():
    p = _pkg()
    res = apply_advisory_influence(
        p, {}, AdvisoryPolicy(mode="veto"), flag_enabled=True,
    )
    assert res.action == "none"
    assert res.package is p


def test_veto_fires_when_quorum_met():
    p = _pkg(qty=2.0)
    res = apply_advisory_influence(
        p, {"m1": 0.1}, AdvisoryPolicy(mode="veto", veto_threshold=0.35, quorum=1),
        flag_enabled=True,
    )
    assert res.action == "veto"
    assert res.package.qty == 0.0
    assert res.package.is_flat
    assert res.record["final_qty"] == 0.0
    assert "advisory_veto" in res.package.attribution


def test_veto_does_not_fire_below_quorum():
    p = _pkg()
    res = apply_advisory_influence(
        p, {"m1": 0.1, "m2": 0.9},
        AdvisoryPolicy(mode="veto", veto_threshold=0.35, quorum=2),
        flag_enabled=True,
    )
    assert res.action == "none"
    assert res.package.qty == 1.0


def test_veto_quorum_two_fires_when_both_bearish():
    p = _pkg()
    res = apply_advisory_influence(
        p, {"m1": 0.1, "m2": 0.2},
        AdvisoryPolicy(mode="veto", veto_threshold=0.35, quorum=2),
        flag_enabled=True,
    )
    assert res.action == "veto"
    assert res.package.qty == 0.0


def test_annotate_attaches_scores_without_changing_qty():
    p = _pkg(qty=1.5)
    res = apply_advisory_influence(
        p, {"m1": 0.8}, AdvisoryPolicy(mode="annotate"), flag_enabled=True,
    )
    assert res.action == "annotate"
    assert res.package.qty == 1.5
    assert res.package.attribution["advisory_scores"] == {"m1": 0.8}


def test_reductive_invariant_fields_preserved_on_veto():
    p = _pkg(qty=3.0)
    res = apply_advisory_influence(
        p, {"m1": 0.0}, AdvisoryPolicy(mode="veto"), flag_enabled=True,
    )
    # only qty changed; every risk-bearing field identical
    assert res.package.side == p.side
    assert res.package.entry_price == p.entry_price
    assert res.package.stop_loss == p.stop_loss
    assert res.package.take_profit == p.take_profit
    assert abs(res.package.qty) <= abs(p.qty)


def test_non_order_package_raises():
    with pytest.raises(TypeError):
        apply_advisory_influence(
            {"qty": 1.0}, {"m": 0.0}, AdvisoryPolicy(mode="veto"), flag_enabled=True,
        )


def test_parse_policy_defaults_to_off():
    assert parse_policy(None).mode == "off"
    assert parse_policy({}).mode == "off"
    assert parse_policy({"advisory_policy": None}).mode == "off"
    assert parse_policy({"other": 1}).mode == "off"


def test_parse_policy_reads_fields():
    pol = parse_policy({"advisory_policy": {
        "mode": "veto", "veto_threshold": 0.4, "quorum": 2,
    }})
    assert pol.mode == "veto"
    assert pol.veto_threshold == 0.4
    assert pol.quorum == 2


def test_policy_validation():
    with pytest.raises(ValueError):
        AdvisoryPolicy(mode="amplify")
    with pytest.raises(ValueError):
        AdvisoryPolicy(mode="veto", quorum=0)
