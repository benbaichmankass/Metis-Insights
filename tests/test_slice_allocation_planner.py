"""NETTED-SLICES: slice accounts ALLOCATE instead of electing one (arbitration_fanout)."""
from __future__ import annotations

from typing import Any, Dict

import pytest

from src.runtime.arbitration_fanout import (
    accounts_in_more_than_one_round,
    plan_per_account_election,
)
from src.runtime.intents import StrategyIntent, elect_from_gated


def _intent(strategy, side="long", confidence=0.5, entry=100.0, sl=95.0, tp=115.0):
    kw: Dict[str, Any] = dict(
        strategy=strategy, symbol="ETHUSDT", side=side, target_qty=0.0,
        regime="trending", adx_14=30.0, vol_regime=None,
        entry=entry, sl=sl if side == "long" else 105.0,
        tp=tp if side == "long" else 85.0, confidence=confidence,
    )
    return StrategyIntent(**kw)


ROSTER = {
    "bybit_1": {"position_model": "slices",
                "strategies": ["trend_donchian_eth_4h", "trend_donchian_eth_prop", "eth_scalp"]},
    "bybit_2": {"strategies": ["trend_donchian_eth_4h", "trend_donchian_eth_prop"]},
}


def _score(c):
    return {"score": float(c.confidence)}


def _elect(cands, **kw):
    return elect_from_gated(cands, annotate=False, **kw)


def _plan(cands):
    return plan_per_account_election(
        cands, accounts=ROSTER, elect_fn=_elect, intents_before_gate=len(cands),
        keep_desired=True, slice_score_fn=_score,
    )


def test_slice_account_allocates_by_score_and_other_accounts_still_elect_one():
    plan = _plan((_intent("trend_donchian_eth_4h", confidence=0.6),
                  _intent("trend_donchian_eth_prop", confidence=0.2)))
    b1 = plan["per_account"]["bybit_1"]
    assert b1["state"] == "allocated"
    assert b1["allocation"] == {"trend_donchian_eth_4h": pytest.approx(0.75),
                                "trend_donchian_eth_prop": pytest.approx(0.25)}
    assert plan["per_account"]["bybit_2"]["state"] == "elected"
    rounds = {r["strategy"]: r for r in plan["rounds"]}
    assert "bybit_1" in rounds["trend_donchian_eth_4h"]["accounts"]
    assert "bybit_1" in rounds["trend_donchian_eth_prop"]["accounts"]
    assert rounds["trend_donchian_eth_prop"]["allocation"] == {"bybit_1": pytest.approx(0.25)}
    # bybit_2 sits in exactly one round, with no allocation
    assert sum("bybit_2" in r["accounts"] for r in plan["rounds"]) == 1
    assert accounts_in_more_than_one_round(plan["rounds"]) == []
    assert set(plan["_desired_by_strategy"]) == set(rounds)


def test_the_name_never_decides_a_share():
    a = _plan((_intent("trend_donchian_eth_4h", confidence=0.4),
               _intent("trend_donchian_eth_prop", confidence=0.4)))
    assert a["per_account"]["bybit_1"]["allocation"] == {
        "trend_donchian_eth_4h": pytest.approx(0.5),
        "trend_donchian_eth_prop": pytest.approx(0.5)}


def test_opposite_side_candidates_are_named_not_dispatched():
    plan = _plan((_intent("trend_donchian_eth_4h", confidence=0.9),
                  _intent("eth_scalp", side="short", confidence=0.1)))
    b1 = plan["per_account"]["bybit_1"]
    assert b1["opposite_side"] == ["eth_scalp"]
    assert list(b1["allocation"]) == ["trend_donchian_eth_4h"]
    assert all(r["strategy"] != "eth_scalp" for r in plan["rounds"])


def test_single_contender_gets_the_whole_budget():
    plan = _plan((_intent("trend_donchian_eth_4h"),))
    assert plan["per_account"]["bybit_1"]["allocation"] == {"trend_donchian_eth_4h": 1.0}


def test_double_round_check_still_bites_on_unallocated_accounts():
    rounds = [{"strategy": "a", "accounts": ["x"]}, {"strategy": "b", "accounts": ["x"]}]
    assert accounts_in_more_than_one_round(rounds) == ["x"]
    ok = [{"strategy": "a", "accounts": ["x"], "allocation": {"x": 0.5}},
          {"strategy": "b", "accounts": ["x"], "allocation": {"x": 0.5}}]
    assert accounts_in_more_than_one_round(ok) == []
    same_strategy = [{"strategy": "a", "accounts": ["x"], "allocation": {"x": 0.5}},
                     {"strategy": "a", "accounts": ["x"], "allocation": {"x": 0.5}}]
    assert accounts_in_more_than_one_round(same_strategy) == ["x"]


def test_multiplexer_puts_each_share_on_its_own_round_package(monkeypatch):
    import src.runtime.intent_multiplexer as im
    from src.runtime import allocator_ev as ae
    from src.runtime.positions import ALLOCATION_META_KEY

    monkeypatch.setattr(im, "_load_accounts_dict", lambda: ROSTER)
    monkeypatch.setattr(ae, "intent_allocation_score", lambda c: {"score": float(c.confidence)})
    cands = (_intent("trend_donchian_eth_4h", confidence=0.6),
             _intent("trend_donchian_eth_prop", confidence=0.2))
    signal = {"side": "buy", "meta": {}}
    im._attach_account_election(signal, cands, symbol="ETHUSDT", intents_before_gate=2)
    rounds = {r["strategy"]: r for r in signal[im.ACCOUNT_ROUNDS_KEY]}
    prop = rounds["trend_donchian_eth_prop"]["signal"]["meta"][ALLOCATION_META_KEY]
    assert prop["bybit_1"]["weight"] == pytest.approx(0.25)
    assert prop["bybit_1"]["contenders"] == ["trend_donchian_eth_4h", "trend_donchian_eth_prop"]
    # the round package carries only ITS share, and bybit_2 (non-slice) none
    assert set(prop) == {"bybit_1"}
